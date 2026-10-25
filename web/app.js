// LexiconEngine ブラウザ版。Pyodide で src/ の Python (lexicon/ と browser/) を動かし、
// DB はこの端末のブラウザ (IndexedDB) に保存する。Mac の server.py と同じ Wi-Fi にいるときに同期する。
"use strict";

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const APP_DIR = "/home/pyodide/app";
const DB_PATH = "/home/pyodide/data/lexicon.db";
const CONTENT_PATH = "/home/pyodide/data/content.db";
const HIGH_SCORE = 0.5;
const MIDDLE_SCORE = 0.2;

let pyodide = null;
let bridge = null;
let editorBridge = null;
const state = {
  options: null, question: null, answered: false, list: null, sort: { key: null, descending: true },
  // 品詞を自分で選んだか。選ぶまでは問題形式に合わせた初期値 (意味の問題は名詞・動詞・形容詞・副詞) にする
  partsTouched: false,
};

const $ = (id) => document.getElementById(id);
const call = (name, ...args) => JSON.parse(bridge[name](...args));

// --- IndexedDB (DBファイルと設定の保存) ---

function idb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("lexicon-engine", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("files");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function idbGet(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const request = db.transaction("files").objectStore("files").get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function idbPut(key, value) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("files", "readwrite");
    transaction.objectStore("files").put(value, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

async function idbDelete(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("files", "readwrite");
    transaction.objectStore("files").delete(key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

// prefix で始まるキーの [キー, 値] をすべて返す
async function idbEntries(prefix) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const results = [];
    const range = IDBKeyRange.bound(prefix, `${prefix}\uffff`);
    const request = db.transaction("files").objectStore("files").openCursor(range);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) {
        resolve(results);
        return;
      }
      results.push([cursor.key, cursor.value]);
      cursor.continue();
    };
    request.onerror = () => reject(request.error);
  });
}

async function saveDatabase() {
  await idbPut("lexicon.db", pyodide.FS.readFile(DB_PATH));
}

// --- 起動 ---

async function boot() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch((error) => console.warn("service worker", error));
  }
  if (navigator.storage && navigator.storage.persist) {
    navigator.storage.persist().catch(() => {});
  }
  setLoading("Python を読み込み中… (初回は時間がかかります)");
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  await pyodide.loadPackage("sqlite3");
  pyodide.FS.mkdirTree(APP_DIR);
  pyodide.FS.mkdirTree("/home/pyodide/data");
  setLoading("アプリを読み込み中…");
  // まとめて取りに行く (オフラインのとき、1つずつだと Mac を待つ時間がファイルの数だけかかる)
  // ファイルの一覧 (src/ からの相対パス、"lexicon/db/quiz.py" など) はサーバーが作る
  const listResponse = await fetch("py/modules.json");
  if (!listResponse.ok) throw new Error(`Python ファイルの一覧を読み込めません (${listResponse.status})`);
  const modules = await listResponse.json();
  const sources = await Promise.all(modules.map(async (name) => {
    const response = await fetch(`py/${name}`);
    if (!response.ok) throw new Error(`${name} を読み込めません (${response.status})`);
    return [name, await response.text()];
  }));
  for (const [name, source] of sources) {
    const path = `${APP_DIR}/${name}`;
    pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    pyodide.FS.writeFile(path, source);
  }
  pyodide.runPython(`import sys\nsys.path.insert(0, "${APP_DIR}")`);
  bridge = pyodide.pyimport("browser.bridge");
  editorBridge = pyodide.pyimport("browser.editor");
  editorBridge.configure(MATERIALS_ROOT, EDITS_FILE);
  await restoreEditorFiles();

  const saved = await idbGet("lexicon.db");
  if (saved) pyodide.FS.writeFile(DB_PATH, saved);
  const opened = call("open_database", DB_PATH);
  $("sync-button").disabled = false;
  showSyncStatus();
  updateEditStatus();
  if (!opened.words) {
    show("first-sync");
    return;
  }
  await showSetup();
}

function setLoading(message) {
  $("loading-message").textContent = message;
}

function show(screen) {
  for (const section of document.querySelectorAll(".screen")) section.hidden = section.id !== screen;
}

// --- 開始画面 ---

async function showSetup(language) {
  const remembered = (await idbGet("settings")) || {};
  state.options = call("setup_options", language || remembered.language || "");
  const options = state.options;
  fillSelect($("language"), options.languages.map((value) => ({ value, label: value })), options.language);
  fillSelect($("question-type"), options.question_types, remembered.question_type);
  fillSelect($("score-mode"), options.score_modes, remembered.score_mode);
  $("count").max = options.max_count;
  if (remembered.count) $("count").value = remembered.count;
  const sameLanguage = remembered.language === options.language;
  state.partsTouched = sameLanguage && Boolean(remembered.parts_touched);
  for (const group of document.querySelectorAll(".scope-group")) {
    const key = group.dataset.key;
    const selected = new Set(sameLanguage ? (remembered.scope || {})[key] || [] : []);
    const container = group.querySelector(".chips");
    container.replaceChildren();
    for (const value of options.scope[key]) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = value;
      chip.setAttribute("aria-pressed", String(selected.has(value)));
      chip.addEventListener("click", () => {
        chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
        if (key === "parts_of_speech") state.partsTouched = true;
      });
      container.append(chip);
    }
    group.hidden = options.scope[key].length === 0;
  }
  onQuestionTypeChanged();
  show("setup");
}

function fillSelect(select, items, selected) {
  select.replaceChildren(...items.map((item) => new Option(item.label, item.value)));
  if (items.some((item) => item.value === selected)) select.value = selected;
}

function onQuestionTypeChanged() {
  $("hint-row").hidden = $("question-type").value !== "cloze";
  if (state.partsTouched) return;
  const defaults = new Set(state.options.default_parts_of_speech[$("question-type").value] || []);
  for (const chip of document.querySelectorAll('[data-key="parts_of_speech"] .chip')) {
    chip.setAttribute("aria-pressed", String(defaults.has(chip.textContent)));
  }
}

function currentSettings() {
  const scope = {};
  for (const group of document.querySelectorAll(".scope-group")) {
    scope[group.dataset.key] = [...group.querySelectorAll('.chip[aria-pressed="true"]')].map((chip) => chip.textContent);
  }
  return {
    language: $("language").value,
    question_type: $("question-type").value,
    count: Number($("count").value),
    score_mode: $("score-mode").value,
    hint: $("hint").checked,
    parts_touched: state.partsTouched,
    scope,
  };
}

async function startQuiz() {
  const settings = currentSettings();
  if (!Number.isInteger(settings.count) || settings.count < 1 || settings.count > state.options.max_count) {
    alert(`出題数は1〜${state.options.max_count}の整数にしてください`);
    return;
  }
  await idbPut("settings", settings);
  $("summary").textContent = "";
  try {
    showQuestion(call("start_quiz", JSON.stringify(settings)));
  } catch (error) {
    alert(pythonMessage(error));
  }
}

// --- クイズ ---

function showQuestion(question) {
  if (question.done) {
    finishQuiz(question);
    return;
  }
  state.question = question;
  state.answered = false;
  show("quiz");
  $("progress").textContent = `${question.index} / ${question.count}`;
  $("prompt").textContent = question.prompt;
  $("feedback").textContent = "";
  $("feedback").className = "feedback";
  $("skip-button").disabled = false;
  const choices = $("choices");
  choices.replaceChildren();
  question.choices.forEach((choice, index) => {
    const button = document.createElement("button");
    button.className = "choice";
    button.innerHTML = `<span class="key">${index + 1}.</span>`;
    button.append(document.createTextNode(choice));
    button.addEventListener("click", () => submit(choice));
    choices.append(button);
  });
  $("typed").hidden = !question.typed;
  $("answer-button").hidden = !question.typed;
  $("answer-button").textContent = "回答する";
  if (question.typed) {
    const input = $("typed-input");
    input.value = "";
    input.disabled = false;
    const characters = $("special-characters");
    characters.replaceChildren(...[...question.special_characters].map((character) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = character;
      // ボタンを押しても入力欄のフォーカス (iPad のキーボード) を外さない
      button.addEventListener("pointerdown", (event) => event.preventDefault());
      button.addEventListener("click", () => insertCharacter(character));
      return button;
    }));
    input.focus();
  }
}

function insertCharacter(character) {
  const input = $("typed-input");
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  input.value = input.value.slice(0, start) + character + input.value.slice(end);
  input.setSelectionRange(start + 1, start + 1);
  input.focus();
}

// selected が空文字ならスキップ (不正解として記録する)
async function submit(selected) {
  if (state.answered) return;
  state.answered = true;
  const result = call("answer", selected);
  await saveDatabase();
  const feedback = $("feedback");
  feedback.textContent = result.feedback;
  feedback.className = `feedback ${result.correct ? "good" : "bad"}`;
  for (const button of $("choices").querySelectorAll(".choice")) {
    const text = button.lastChild.textContent;
    button.disabled = true;
    if (text === result.answer) button.classList.add("correct");
    else if (text === selected) button.classList.add("wrong");
  }
  $("typed-input").disabled = true;
  $("skip-button").disabled = true;
  $("answer-button").hidden = false;
  $("answer-button").textContent = state.question.is_last ? "結果を見る" : "次の問題へ";
  $("answer-button").focus();
}

function next() {
  showQuestion(call("next_question"));
}

async function finishQuiz(result) {
  state.question = null;
  await showSetup($("language").value);
  $("summary").textContent = [result.error, result.summary].filter(Boolean).join("\n");
}

function onAnswerButton() {
  if (!state.question) return;
  if (state.answered) {
    next();
  } else if (state.question.typed) {
    const typed = $("typed-input").value.trim();
    if (typed) submit(typed);
    else alert("答えを入力してください");
  }
}

// --- 単語一覧 ---

async function showWordList() {
  fillSelect($("list-language"), state.options.languages.map((value) => ({ value, label: value })), $("language").value);
  loadWordList();
  show("word-list");
}

function loadWordList(keepType = false) {
  const type = keepType ? $("list-type").value : "";
  state.list = call("word_list", $("list-language").value, type);
  fillSelect($("list-type"), [{ value: "", label: "意味: すべての形式" }, ...state.list.meaning_types.map((item) => ({ value: item.value, label: `意味: ${item.label}` }))], type);
  state.sort = { key: "meaning:score", descending: true };
  renderWordList();
}

function listColumns() {
  const columns = [
    { key: "word", label: "単語", value: (row) => row.word.toLowerCase(), text: (row) => row.word, className: "word" },
    { key: "parts", label: "品詞", value: (row) => row.parts_of_speech, text: (row) => row.parts_of_speech },
    { key: "meanings", label: "意味", value: (row) => row.meanings, text: (row) => row.meanings },
  ];
  for (const pool of state.list.pools) {
    columns.push({
      key: `${pool.value}:score`, label: `${pool.label} 誤答スコア`, pool: pool.value, numeric: true, descendingFirst: true,
      value: (row) => row.pools[pool.value].score, text: (row) => (row.pools[pool.value].score == null ? "" : row.pools[pool.value].score.toFixed(2)),
    });
    columns.push({
      key: `${pool.value}:answered`, label: `${pool.label} 正解/回答`, pool: pool.value, numeric: true, descendingFirst: true,
      value: (row) => row.pools[pool.value].answered || null,
      text: (row) => (row.pools[pool.value].answered ? `${row.pools[pool.value].correct}/${row.pools[pool.value].answered}` : ""),
    });
  }
  columns.push({ key: "last", label: "最終回答", value: (row) => row.last_answered, text: (row) => localTime(row.last_answered), descendingFirst: true });
  columns.push({ key: "sources", label: "出典", value: (row) => row.sources, text: (row) => row.sources });
  return columns;
}

function localTime(timestamp) {
  if (!timestamp) return "";
  // 回答日時は SQLite の CURRENT_TIMESTAMP (UTC)
  const date = new Date(timestamp.replace(" ", "T") + "Z");
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function renderWordList() {
  const columns = listColumns();
  const column = columns.find((item) => item.key === state.sort.key) || columns[3];
  const colorPool = column.pool || "meaning";
  const query = $("list-search").value.trim().toLowerCase();
  const answeredOnly = $("list-answered").checked;
  let rows = state.list.rows.filter((row) =>
    (!answeredOnly || Object.values(row.pools).some((pool) => pool.answered)) &&
    (!query || row.word.toLowerCase().includes(query) || row.meanings.toLowerCase().includes(query)));
  // 値のない行は末尾。同じ値の中は回答数の多い順、さらに単語順
  rows.sort((a, b) => a.word.localeCompare(b.word));
  rows.sort((a, b) => b.pools[colorPool].answered - a.pools[colorPool].answered);
  const valued = rows.filter((row) => column.value(row) != null && column.value(row) !== "");
  const empty = rows.filter((row) => column.value(row) == null || column.value(row) === "");
  valued.sort((a, b) => {
    const x = column.value(a), y = column.value(b);
    const order = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
    return state.sort.descending ? -order : order;
  });
  rows = [...valued, ...empty];

  const head = document.createElement("tr");
  for (const item of columns) {
    const th = document.createElement("th");
    th.textContent = item.label + (item.key === column.key ? (state.sort.descending ? " ▼" : " ▲") : "");
    if (item.numeric) th.className = "num";
    th.addEventListener("click", () => {
      state.sort = item.key === column.key
        ? { key: item.key, descending: !state.sort.descending }
        : { key: item.key, descending: Boolean(item.descendingFirst) };
      renderWordList();
    });
    head.append(th);
  }
  $("list-table").tHead.replaceChildren(head);
  const body = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    const score = row.pools[colorPool].score;
    tr.className = score == null ? "unanswered" : score >= HIGH_SCORE ? "high" : score >= MIDDLE_SCORE ? "middle" : "";
    for (const item of columns) {
      const td = document.createElement("td");
      td.textContent = item.text(row);
      if (item.numeric) td.className = "num";
      if (item.className) td.className = item.className;
      tr.append(td);
    }
    body.append(tr);
  }
  $("list-table").tBodies[0].replaceChildren(body);
  const poolLabel = state.list.pools.find((pool) => pool.value === colorPool).label;
  $("list-status").textContent =
    `表示 ${rows.length}語 / 全${state.list.rows.length}語 — ${poolLabel}の誤答スコア ${HIGH_SCORE} 以上は赤、${MIDDLE_SCORE} 以上は橙`;
}

// --- 同期 ---

async function showSyncStatus(message) {
  const last = await idbGet("last-sync");
  $("sync-status").textContent = message || (last ? `最終同期 ${localTime(last)}` : "未同期");
}

async function sync() {
  if (state.question && !state.answered) {
    alert("出題中は同期できません。中断してから同期してください");
    return;
  }
  const button = $("sync-button");
  button.disabled = true;
  showSyncStatus("同期中…");
  try {
    // 1. この端末の回答履歴を Mac に送り、Mac にしかない回答履歴を受け取る
    const response = await fetchWithTimeout("api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: `{"rows": ${bridge.history_rows()}}`,
    }, 30000);
    if (!response.ok) throw new Error(`Mac の応答: ${response.status}`);
    const remote = await response.json();
    // 2. この端末の単語編集を Mac の教材JSONに反映する (Mac の DB も作り直される)
    const edited = await syncEdits();
    // 3. 単語データを Mac のもので置き換えてから、受け取った回答履歴を足す
    const content = await fetchWithTimeout("api/content", {}, 30000);
    if (!content.ok) throw new Error(`Mac の応答: ${content.status}`);
    pyodide.FS.writeFile(CONTENT_PATH, new Uint8Array(await content.arrayBuffer()));
    // Mac の単語データが今より極端に少ないときは、Mac 側の事故 (DB が空になったなど) とみなして置き換えない
    const check = JSON.parse(bridge.content_check(CONTENT_PATH));
    if (check.local_words >= 50 && check.content_words < check.local_words * 0.5) {
      pyodide.FS.unlink(CONTENT_PATH);
      throw guardError(`Mac の単語データ (${check.content_words}語) がこの端末 (${check.local_words}語) よりかなり少ないため、置き換えを止めました。Mac の DB を確認してください (「バックアップから復元」で戻せます)`);
    }
    // 置き換える前のこの端末の DB を1つ残しておく
    await idbPut("lexicon.db.before-sync", pyodide.FS.readFile(DB_PATH));
    const result = JSON.parse(bridge.apply_sync(CONTENT_PATH, JSON.stringify(remote.rows)));
    pyodide.FS.unlink(CONTENT_PATH);
    // 4. 教材JSONの写しを Mac のものにする (まだ反映していない編集は重ねて残す)
    await pullMaterials();
    await saveDatabase();
    const now = new Date().toISOString().replace("T", " ").slice(0, 19);
    await idbPut("last-sync", now);
    await showSyncStatus();
    const lines = [`同期しました。`, `Mac に送った回答: ${remote.added}件`, `Mac から受け取った回答: ${result.added}件`];
    if (edited.sent) lines.push(`Mac に反映した編集: ${edited.sent}件`);
    if (edited.conflicts) lines.push(`あとで決める競合: ${edited.conflicts}件 (次の同期でまた確認します)`);
    for (const warning of edited.warnings) lines.push(`\n⚠ ${warning}`);
    alert(lines.join("\n"));
    if (!state.question) await showSetup($("language").value);
  } catch (error) {
    console.error(error);
    showSyncStatus("同期できませんでした");
    if (error.guard) alert(`同期を途中で止めました (この端末のデータはそのままです)。\n\n${error.message}`);
    else alert(`Mac と同期できませんでした。Mac で server.py が動いていて、同じ Wi-Fi につながっているか確認してください。\n\n${pythonMessage(error)}`);
  } finally {
    button.disabled = false;
  }
}

// 同期の安全装置で止めたことを表すエラー (通信の失敗とは分けて知らせる)
function guardError(message) {
  const error = new Error(message);
  error.guard = true;
  return error;
}

function fetchWithTimeout(url, options, timeout) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  return fetch(url, { ...options, signal: controller.signal, cache: "no-store" }).finally(() => clearTimeout(timer));
}

function pythonMessage(error) {
  // Python の例外はトレースバックごと来るので、最後の行 (例外のメッセージ) だけ見せる
  const lines = String(error.message || error).trim().split("\n");
  return lines[lines.length - 1];
}

// --- 確認画面 (競合の選択など) ---

function showDialog(title, body, buttons) {
  const dialog = $("dialog");
  $("dialog-title").textContent = title;
  $("dialog-body").replaceChildren(body);
  $("dialog-buttons").replaceChildren(...buttons.map((button) => {
    const element = document.createElement("button");
    element.value = button.value;
    element.textContent = button.label;
    if (button.className) element.className = button.className;
    return element;
  }));
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue || buttons[0].value), { once: true });
    dialog.showModal();
  });
}

// --- イベント ---

initEditorEvents();

$("language").addEventListener("change", () => showSetup($("language").value));
$("question-type").addEventListener("change", onQuestionTypeChanged);
$("start-button").addEventListener("click", startQuiz);
$("answer-button").addEventListener("click", onAnswerButton);
$("skip-button").addEventListener("click", () => submit(""));
$("stop-button").addEventListener("click", () => finishQuiz(call("finish_quiz")));
$("sync-button").addEventListener("click", sync);
$("word-list-button").addEventListener("click", showWordList);
$("list-back").addEventListener("click", () => show("setup"));
$("list-language").addEventListener("change", () => loadWordList());
$("list-type").addEventListener("change", () => loadWordList(true));
$("list-search").addEventListener("input", renderWordList);
$("list-answered").addEventListener("change", renderWordList);
$("typed-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    event.stopPropagation();
    onAnswerButton();
  }
});
document.addEventListener("keydown", (event) => {
  if ($("quiz").hidden || !state.question || event.isComposing) return;
  if (event.target === $("typed-input")) return;
  if (event.key === "Enter") {
    event.preventDefault();
    onAnswerButton();
  } else if (!state.answered && /^[1-9]$/.test(event.key)) {
    const choice = state.question.choices[Number(event.key) - 1];
    if (choice !== undefined) submit(choice);
  } else if (event.key === "Escape") {
    finishQuiz(call("finish_quiz"));
  }
});

boot().catch((error) => {
  console.error(error);
  setLoading(`起動できませんでした: ${pythonMessage(error)}`);
});

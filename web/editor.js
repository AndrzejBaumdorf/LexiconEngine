// ブラウザ版の単語エディタ。教材JSONの写しを編集し、未同期の編集として記録する (src/web_editor.py)。
// 同期のときに Mac へ送り、Mac の JSON に反映する。app.js の call / $ / idb* / dialog を使う。
"use strict";

const MATERIALS_ROOT = "/home/pyodide/materials";
const EDITS_FILE = "/home/pyodide/data/edits.json";
const editorState = { file: "", entries: [], index: null, spec: null, dirty: false };

// --- 保存 (ブラウザ内のファイル ⇄ IndexedDB) ---

async function restoreEditorFiles() {
  for (const [key, text] of await idbEntries("material:")) {
    const path = `${MATERIALS_ROOT}/${key.slice("material:".length)}`;
    pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    pyodide.FS.writeFile(path, text);
  }
  const edits = await idbGet("edits");
  if (edits) pyodide.FS.writeFile(EDITS_FILE, edits);
}

async function persistMaterial(file) {
  const path = `${MATERIALS_ROOT}/${file}`;
  await idbPut(`material:${file}`, pyodide.FS.readFile(path, { encoding: "utf8" }));
}

async function persistEdits() {
  const exists = pyodide.FS.analyzePath(EDITS_FILE).exists;
  await idbPut("edits", exists ? pyodide.FS.readFile(EDITS_FILE, { encoding: "utf8" }) : "[]");
  updateEditStatus();
}

async function persistAllMaterials() {
  for (const [key] of await idbEntries("material:")) await idbDelete(key);
  for (const file of JSON.parse(editorBridge.files())) await persistMaterial(file);
}

function updateEditStatus() {
  const count = JSON.parse(editorBridge.pending_edits()).length;
  $("edit-status").hidden = count === 0;
  $("edit-status").textContent = `未同期の編集 ${count}件`;
}

// --- 画面 ---

async function showEditor() {
  const files = JSON.parse(editorBridge.files());
  if (!files.length) {
    alert("教材ファイルがまだありません。Mac と同期してください");
    return;
  }
  const remembered = (await idbGet("editor-file")) || editorState.file;
  fillSelect($("editor-file"), files.map((value) => ({ value, label: value })), files.includes(remembered) ? remembered : files[0]);
  openEditorFile($("editor-file").value);
  show("editor");
}

function openEditorFile(file) {
  editorState.file = file;
  idbPut("editor-file", file);
  const info = JSON.parse(editorBridge.open_file(file));
  editorState.entries = info.entries;
  editorState.defaults = info;
  fillDatalist($("ed-sources"), info.sources);
  renderEntryList();
  newEntry();
}

function renderEntryList() {
  const query = $("editor-search").value.trim().toLowerCase();
  const list = $("editor-entries");
  list.replaceChildren(...editorState.entries
    .filter((item) => !query || item.label.toLowerCase().includes(query))
    .map((item) => {
      const li = document.createElement("li");
      li.textContent = item.label;
      if (item.index === editorState.index) li.className = "selected";
      if (item.edited) li.classList.add("edited");
      li.addEventListener("click", () => confirmDiscard() && loadEntry(item.index));
      return li;
    }));
}

function fillDatalist(datalist, values) {
  datalist.replaceChildren(...values.map((value) => new Option(value)));
}

function confirmDiscard() {
  return !editorState.dirty || confirm("保存していない変更は破棄されます。よろしいですか?");
}

function setLanguage(language) {
  editorState.spec = JSON.parse(editorBridge.language_spec(language));
  fillSelect($("ed-language"), editorState.spec.languages.map((value) => ({ value, label: value })), language);
  fillDatalist($("ed-parts"), editorState.spec.parts_of_speech);
}

function newEntry() {
  const defaults = editorState.defaults;
  editorState.index = null;
  setLanguage(defaults.language);
  fillForm({ word: "", language: defaults.language, difficulty: defaults.difficulty, source: defaults.source, derivatives: "", meanings: [], extra: {} });
  addMeaningCard();
  $("ed-delete").hidden = true;
  $("editor-message").textContent = "新しい単語";
  editorState.dirty = false;
  renderEntryList();
  $("ed-word").focus();
}

function loadEntry(index) {
  const form = JSON.parse(editorBridge.load_entry(editorState.file, index));
  editorState.index = index;
  setLanguage(form.language);
  fillForm(form);
  $("ed-delete").hidden = false;
  $("editor-message").textContent = `編集中: ${form.word}`;
  editorState.dirty = false;
  renderEntryList();
}

function fillForm(form) {
  $("ed-word").value = form.word;
  $("ed-language").value = form.language;
  $("ed-difficulty").value = form.difficulty;
  $("ed-source").value = form.source;
  $("ed-derivatives").value = form.derivatives;
  editorState.extra = form.extra || {};
  $("ed-meanings").replaceChildren();
  for (const meaning of form.meanings) addMeaningCard(meaning);
}

function addMeaningCard(meaning = {}) {
  const card = document.createElement("div");
  card.className = "meaning-card";
  card.extra = meaning.extra || {};
  card.forms = { ...(meaning.forms || {}) };
  card.innerHTML = `
    <div class="card-head"><span class="card-title"></span><button type="button" class="ghost remove">意味を削除</button></div>
    <div class="grid2">
      <label>品詞<input class="pos" type="text" list="ed-parts"></label>
      <label>意味<input class="meaning" type="text"></label>
    </div>
    <div class="grid2 forms"></div>
    <div class="grid2">
      <label>類義語<input class="synonyms" type="text" autocapitalize="off" spellcheck="false"></label>
      <label>対義語<input class="antonyms" type="text" autocapitalize="off" spellcheck="false"></label>
    </div>
    <label>例文 (1行1文)<textarea class="examples" autocapitalize="off" spellcheck="false"></textarea></label>
    <label>和訳 (例文と同じ行)<textarea class="translations"></textarea></label>`;
  card.querySelector(".pos").value = meaning.part_of_speech || (editorState.spec.parts_of_speech[0] || "");
  card.querySelector(".meaning").value = meaning.meaning_ja || "";
  card.querySelector(".synonyms").value = meaning.synonyms || "";
  card.querySelector(".antonyms").value = meaning.antonyms || "";
  card.querySelector(".examples").value = meaning.examples || "";
  card.querySelector(".translations").value = meaning.translations || "";
  card.valency = meaning.valency || "";
  card.querySelector(".pos").addEventListener("input", () => renderFormFields(card));
  card.querySelector(".remove").addEventListener("click", () => {
    card.remove();
    editorState.dirty = true;
    numberCards();
  });
  $("ed-meanings").append(card);
  renderFormFields(card);
  numberCards();
  return card;
}

function numberCards() {
  $("ed-meanings").querySelectorAll(".meaning-card").forEach((card, index) => {
    card.querySelector(".card-title").textContent = `意味 ${index + 1}`;
  });
}

function renderFormFields(card) {
  // 言語と品詞に応じた語形の欄 (ドイツ語の名詞なら性・複数形、動詞なら活用) と格支配の欄を出す
  saveFormFields(card);
  const spec = editorState.spec;
  const fields = spec.form_fields[card.querySelector(".pos").value.trim()] || [];
  const container = card.querySelector(".forms");
  container.replaceChildren();
  for (const field of fields) {
    const label = document.createElement("label");
    label.textContent = field.label;
    let input;
    if (field.key === "gender") {
      input = document.createElement("select");
      input.append(new Option("", ""), ...spec.genders.map((gender) => new Option(gender.label, gender.value)));
    } else {
      input = document.createElement("input");
      input.type = "text";
      input.autocapitalize = "off";
      input.spellcheck = false;
    }
    input.dataset.key = field.key;
    input.value = card.forms[field.key] || "";
    label.append(input);
    container.append(label);
  }
  if (spec.cases) {
    const label = document.createElement("label");
    label.textContent = "格支配 (例: Akk, von+Dat, sich+Akk)";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "valency";
    input.autocapitalize = "off";
    input.spellcheck = false;
    input.value = card.valency;
    label.append(input);
    container.append(label);
  }
}

function saveFormFields(card) {
  // 品詞を変えて欄が消えても入力値は残す (保存時は今の品詞の欄だけを使う)
  for (const input of card.querySelectorAll(".forms [data-key]")) card.forms[input.dataset.key] = input.value;
  const valency = card.querySelector(".valency");
  if (valency) card.valency = valency.value;
}

function readForm() {
  const meanings = [...$("ed-meanings").querySelectorAll(".meaning-card")]
    .map((card) => {
      saveFormFields(card);
      const fields = editorState.spec.form_fields[card.querySelector(".pos").value.trim()] || [];
      return {
        part_of_speech: card.querySelector(".pos").value,
        meaning_ja: card.querySelector(".meaning").value,
        forms: Object.fromEntries(fields.map((field) => [field.key, card.forms[field.key] || ""])),
        valency: editorState.spec.cases ? card.valency : "",
        synonyms: card.querySelector(".synonyms").value,
        antonyms: card.querySelector(".antonyms").value,
        examples: card.querySelector(".examples").value,
        translations: card.querySelector(".translations").value,
        extra: card.extra,
      };
    })
    // 何も入力していない意味の欄は無視する
    .filter((meaning) => meaning.meaning_ja.trim() || meaning.examples.trim() || meaning.synonyms.trim());
  return {
    word: $("ed-word").value,
    language: $("ed-language").value,
    difficulty: $("ed-difficulty").value,
    source: $("ed-source").value,
    derivatives: $("ed-derivatives").value,
    meanings,
    extra: editorState.extra,
  };
}

async function saveEntry(event) {
  event.preventDefault();
  const form = JSON.stringify(readForm());
  const index = editorState.index ?? -1;
  let result = JSON.parse(editorBridge.save_entry(editorState.file, index, form, false));
  if (result.status === "confirm") {
    if (!confirm(result.message)) return;
    result = JSON.parse(editorBridge.save_entry(editorState.file, index, form, true));
  }
  if (result.status === "error") {
    alert(result.message);
    return;
  }
  await afterEditorChange();
  editorState.entries = JSON.parse(editorBridge.open_file(editorState.file)).entries;
  editorState.dirty = false;
  loadEntry(result.index);
  $("editor-message").textContent = `${result.message} (同期すると Mac に反映されます)`;
}

async function deleteEntry() {
  if (editorState.index == null) return;
  const word = $("ed-word").value;
  if (!confirm(`${word} を削除しますか?`)) return;
  const result = JSON.parse(editorBridge.delete_entry(editorState.file, editorState.index));
  await afterEditorChange();
  editorState.entries = JSON.parse(editorBridge.open_file(editorState.file)).entries;
  newEntry();
  $("editor-message").textContent = result.message;
}

async function afterEditorChange() {
  await persistMaterial(editorState.file);
  await persistEdits();
  await saveDatabase();
}

async function createFile() {
  const name = prompt("新しいファイルの場所 (例: German/A1A2_単語集/C_A1.json)", editorState.file.replace(/[^/]*$/, ""));
  if (!name) return;
  const file = name.trim().replace(/^\/+/, "");
  if (!file.endsWith(".json")) {
    alert("ファイル名は .json で終わるようにしてください");
    return;
  }
  const result = JSON.parse(editorBridge.create_file(file));
  if (result.status === "error") {
    alert(result.message);
    return;
  }
  await persistMaterial(file);
  const files = JSON.parse(editorBridge.files());
  fillSelect($("editor-file"), files.map((value) => ({ value, label: value })), file);
  openEditorFile(file);
}

// --- 同期 (app.js の sync から呼ぶ) ---

async function syncEdits() {
  // 未同期の編集を Mac に送り、競合したものはどちらを残すかを選んでもらう
  let edits = JSON.parse(editorBridge.pending_edits());
  let sent = 0;
  const conflictsLeft = [];
  const warnings = [];
  for (let round = 0; edits.length && round < 2; round += 1) {
    const response = await fetchWithTimeout("api/edits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits }),
    }, 60000);
    if (!response.ok) throw new Error(`Mac の応答 (編集): ${response.status}`);
    const result = await response.json();
    editorBridge.finish_edits(JSON.stringify(result.applied));
    sent += result.applied.length;
    if (result.warning) warnings.push(result.warning);
    const retry = [];
    for (const conflict of result.conflicts) {
      const choice = await askConflict(conflict);
      if (choice === "later") {
        conflictsLeft.push(conflict);
        continue;
      }
      editorBridge.resolve_conflict(conflict.id, choice, JSON.stringify(conflict.mac_entry));
      if (choice === "ipad") retry.push(conflict.id);
    }
    await persistEdits();
    edits = JSON.parse(editorBridge.pending_edits()).filter((edit) => retry.includes(edit.id));
  }
  return { sent, conflicts: conflictsLeft.length, warnings };
}

function askConflict(conflict) {
  const body = document.createElement("div");
  body.append(`${conflict.file}\n${conflict.reason}。どちらを残しますか?\n\n`);
  const versions = document.createElement("div");
  versions.className = "versions";
  for (const [title, entry] of [["この端末の版", conflict.ipad_entry], ["Mac の版", conflict.mac_entry]]) {
    const box = document.createElement("div");
    box.className = "version";
    box.textContent = `${title}\n${editorBridge.entry_summary(JSON.stringify(entry ?? null))}`;
    versions.append(box);
  }
  body.append(versions);
  return showDialog(`競合: ${conflict.word}`, body, [
    { value: "later", label: "あとで決める", className: "ghost" },
    { value: "mac", label: "Mac の版を残す", className: "secondary" },
    { value: "ipad", label: "この端末の版を残す" },
  ]);
}

async function pullMaterials() {
  const response = await fetchWithTimeout("api/materials", {}, 60000);
  if (!response.ok) throw new Error(`Mac の応答 (教材): ${response.status}`);
  const { files } = await response.json();
  const localFiles = JSON.parse(editorBridge.files()).length;
  if (localFiles && Object.keys(files).length < localFiles * 0.5) {
    throw guardError(`Mac の教材ファイル (${Object.keys(files).length}個) がこの端末 (${localFiles}個) よりかなり少ないため、教材の置き換えを止めました。Mac の materials フォルダを確認してください`);
  }
  editorBridge.replace_materials(JSON.stringify(files));
  await persistAllMaterials();
  await persistEdits();
}

// app.js が読み込まれてから呼ぶ ($ などは app.js にある)
function initEditorEvents() {
  $("editor-button").addEventListener("click", showEditor);
  $("editor-back").addEventListener("click", () => {
    if (!confirmDiscard()) return;
    editorState.dirty = false;
    showSetup($("language").value);
  });
  $("editor-file").addEventListener("change", () => {
    if (confirmDiscard()) openEditorFile($("editor-file").value);
    else $("editor-file").value = editorState.file;
  });
  $("editor-new-file").addEventListener("click", () => confirmDiscard() && createFile());
  $("editor-search").addEventListener("input", renderEntryList);
  $("editor-new").addEventListener("click", () => confirmDiscard() && newEntry());
  $("ed-add-meaning").addEventListener("click", () => {
    addMeaningCard().querySelector(".pos").focus();
    editorState.dirty = true;
  });
  $("ed-language").addEventListener("change", () => {
    const form = readForm();
    setLanguage($("ed-language").value);
    form.language = $("ed-language").value;
    fillForm(form);
    if (!form.meanings.length) addMeaningCard();
    editorState.dirty = true;
  });
  $("editor-form").addEventListener("input", () => { editorState.dirty = true; });
  $("editor-form").addEventListener("submit", saveEntry);
  $("ed-delete").addEventListener("click", deleteEntry);
}

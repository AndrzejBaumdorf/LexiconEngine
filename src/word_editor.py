from __future__ import annotations

import copy
import json
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cloze import find_cloze_spans, mark_spans, parse_marked
from database import normalize_derivatives
from languages import FORM_KEYS, LANGUAGES, Language, find_language, get_language


MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"
LANGUAGE_NAMES = [language.name for language in LANGUAGES]
RELATION_TYPES = ("synonyms", "antonyms")
# フォームで編集するキー。これ以外 (usages など) は編集時もそのまま残す
WORD_KEYS = ("word", "language", "difficulty", "source", "parts_of_speech", "derivatives", "related_words")
MEANING_KEYS = ("meaning_ja", *FORM_KEYS, "synonyms", "antonyms", "examples")
IS_MAC = sys.platform == "darwin"
SHORTCUT_LABEL = "⌘" if IS_MAC else "Ctrl+"
SHIFT_MASK = 0x0001
DERIVATIVE_PATTERN = re.compile(r"^(?P<word>[^=(（]+?)\s*[(（]\s*(?P<pos>[^)）]+?)\s*[)）]\s*(?:=\s*(?P<meaning>.*))?$")


def parse_relations(text: str, part_of_speech: str) -> list[str | dict]:
    # "forsake=見捨てる" は意味付きのオブジェクト、"forsake" だけなら文字列として保存する
    relations: list[str | dict] = []
    for item in re.split(r"[,，]", text):
        word, _, meaning = (part.strip() for part in item.partition("="))
        if not word:
            continue
        relations.append({"word": word, "part_of_speech": part_of_speech, "meaning_ja": meaning} if meaning else word)
    return relations


def format_relations(relations: list[str | dict]) -> str:
    return ", ".join(
        relation if isinstance(relation, str)
        else f"{relation['word']}={relation['meaning_ja']}" if relation.get("meaning_ja") and relation["meaning_ja"] != relation["word"]
        else relation["word"]
        for relation in relations
    )


def parse_derivatives(text: str) -> list[dict]:
    # "placatory(形容詞)=なだめるような, placation(名詞)"
    derivatives = []
    for item in re.split(r"[,，]", text):
        item = item.strip()
        if not item:
            continue
        match = DERIVATIVE_PATTERN.match(item)
        if match is None:
            raise ValueError(f"派生語「{item}」は「placatory(形容詞)=なだめるような」の形で書いてください")
        derivative = {"word": match["word"], "part_of_speech": match["pos"]}
        if match["meaning"] and match["meaning"].strip():
            derivative["meaning_ja"] = match["meaning"].strip()
        derivatives.append(derivative)
    return derivatives


def format_derivatives(derivatives: list[dict]) -> str:
    return ", ".join(
        f"{item['word']}({item['part_of_speech']})" + (f"={item['meaning_ja']}" if item.get("meaning_ja") else "")
        for item in derivatives
    )


def parse_examples(sentences: str, translations: str) -> list[dict]:
    # 例文中の [ ] は穴埋めにする箇所として targets に保存し、本文からは外す
    translation_lines = translations.splitlines()
    examples = []
    for index, sentence in enumerate(sentences.splitlines()):
        if not sentence.strip():
            continue
        plain, targets = parse_marked(sentence.strip())
        example: dict = {"sentence": plain}
        if targets:
            example["targets"] = targets
        translation = translation_lines[index].strip() if index < len(translation_lines) else ""
        if translation:
            example["translation_ja"] = translation
        examples.append(example)
    return examples


def format_examples(examples: list[dict]) -> tuple[str, str]:
    sentences = []
    for example in examples:
        sentence = example["sentence"]
        spans = find_cloze_spans(sentence, "", targets=example["targets"]) if example.get("targets") else None
        sentences.append(mark_spans(sentence, spans) if spans else sentence)
    return "\n".join(sentences), "\n".join(example.get("translation_ja", "") for example in examples)


def _relation_key(relation: str | dict) -> str:
    return relation if isinstance(relation, str) else relation["word"]


def _merge_meaning(existing: dict, meaning: dict) -> None:
    for key in FORM_KEYS:
        if meaning.get(key):
            existing[key] = meaning[key]
    for key in RELATION_TYPES:
        known = {_relation_key(relation) for relation in existing.get(key, [])}
        for relation in meaning.get(key, []):
            if _relation_key(relation) not in known:
                existing.setdefault(key, []).append(relation)
    known_sentences = {example["sentence"] for example in existing.get("examples", [])}
    for example in meaning.get("examples", []):
        if example["sentence"] not in known_sentences:
            existing.setdefault("examples", []).append(example)


def merge_entry(entries: list[dict], entry: dict) -> bool:
    """同じ単語・言語があれば品詞と意味を追記し、なければ末尾に追加する。追記した場合は True。"""
    existing = next((item for item in entries if item.get("word") == entry["word"] and item.get("language", "English") == entry["language"]), None)
    if existing is None:
        entries.append(entry)
        return False
    for key in ("difficulty", "source"):
        if entry.get(key):
            existing[key] = entry[key]
    parts = existing.setdefault("parts_of_speech", [])
    for part in entry["parts_of_speech"]:
        existing_part = next((item for item in parts if item.get("part_of_speech") == part["part_of_speech"]), None)
        if existing_part is None:
            parts.append(part)
            continue
        meanings = existing_part.setdefault("meanings", [])
        for meaning in part["meanings"]:
            existing_meaning = next((item for item in meanings if item.get("meaning_ja") == meaning["meaning_ja"]), None)
            if existing_meaning is None:
                meanings.append(meaning)
            else:
                _merge_meaning(existing_meaning, meaning)
    derivatives = normalize_derivatives(existing)
    known = {item["word"] for item in derivatives}
    derivatives += [item for item in entry.get("derivatives", []) if item["word"] not in known]
    existing.pop("related_words", None)
    if derivatives:
        existing["derivatives"] = derivatives
    return True


def material_files() -> list[str]:
    # materials/English/<教材>/unit01.json のようなサブフォルダも含めて一覧にする
    return sorted(display_path(path) for path in MATERIALS_DIR.rglob("*.json"))


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(MATERIALS_DIR).as_posix()
    except ValueError:
        return str(path)


def resolve_material(text: str) -> Path:
    path = Path(text.strip()).expanduser()
    return (path if path.is_absolute() else MATERIALS_DIR / path).resolve()


def default_language(path: Path) -> str | None:
    relative = display_path(path)
    top = relative.split("/", 1)[0] if "/" in relative else ""
    language = find_language(top)
    return language.name if language else None


def load_entries(path: Path) -> list[dict]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = [payload] if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("JSONのルートは単語オブジェクトの配列にしてください")
    return entries


def save_entries(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


class WordEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LexiconEngine 単語エディタ")
        self.geometry("1100x800")
        self.entries: list[dict] = []
        self.loaded_path: Path | None = None
        self.list_indices: list[int] = []
        self.current_index: int | None = None
        self.current_extra: dict = {}
        self.pending_parts: list[tuple[str, dict]] = []
        self.editing_meaning: int | None = None
        self.dirty = False
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

        file_frame = ttk.LabelFrame(self, text="保存先")
        file_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))
        file_frame.columnconfigure(0, weight=1)
        json_files = material_files()
        self.file_var = tk.StringVar(value=json_files[0] if json_files else "words.json")
        self.file_box = ttk.Combobox(file_frame, textvariable=self.file_var, values=json_files)
        self.file_box.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        self.file_box.bind("<<ComboboxSelected>>", lambda event: self.load_file())
        self.file_box.bind("<Return>", lambda event: self.load_file())
        ttk.Button(file_frame, text="参照…", command=self.choose_file).grid(row=0, column=1, padx=5)

        self._build_word_list()

        word_frame = ttk.LabelFrame(self, text="単語")
        word_frame.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=5)
        word_frame.columnconfigure(1, weight=1)
        word_frame.columnconfigure(3, weight=1)
        self.word_var = tk.StringVar()
        self.language_var = tk.StringVar(value=LANGUAGE_NAMES[0])
        self.difficulty_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.derivatives_var = tk.StringVar()
        self.word_entry = self._entry(word_frame, "単語", self.word_var, 0, 0)
        self._entry(word_frame, "言語", self.language_var, 0, 2, combobox=True).configure(values=LANGUAGE_NAMES)
        self._entry(word_frame, "難易度", self.difficulty_var, 1, 0)
        self.source_box = self._entry(word_frame, "出典", self.source_var, 1, 2, combobox=True)
        self._entry(word_frame, "派生語", self.derivatives_var, 2, 0, columnspan=3)
        ttk.Label(word_frame, text="カンマ区切りで「placatory(形容詞)=なだめるような, placation(名詞)」。意味は省略可", foreground="gray").grid(row=3, column=1, columnspan=3, sticky="w", padx=5)

        meaning_frame = ttk.LabelFrame(self, text="意味")
        meaning_frame.grid(row=2, column=1, sticky="ew", padx=(0, 10), pady=5)
        meaning_frame.columnconfigure(1, weight=1)
        meaning_frame.columnconfigure(3, weight=1)
        self.pos_var = tk.StringVar(value="名詞")
        self.meaning_var = tk.StringVar()
        # 語形 (性・複数形・動詞の活用) は欄を作り直しても入力値が残るよう、変数をキーごとに持つ
        self.form_vars = {key: tk.StringVar() for key in FORM_KEYS}
        self.shown_form_fields: tuple | None = None
        self.synonyms_var = tk.StringVar()
        self.antonyms_var = tk.StringVar()
        self.pos_box = self._entry(meaning_frame, "品詞", self.pos_var, 0, 0, combobox=True)
        self.meaning_entry = self._entry(meaning_frame, "意味", self.meaning_var, 1, 0, columnspan=3)
        self.forms_frame = ttk.Frame(meaning_frame)
        self.forms_frame.grid(row=2, column=0, columnspan=4, sticky="ew")
        self.forms_frame.columnconfigure(1, weight=1)
        self.forms_frame.columnconfigure(3, weight=1)
        self._entry(meaning_frame, "類義語", self.synonyms_var, 3, 0, columnspan=3)
        self._entry(meaning_frame, "対義語", self.antonyms_var, 4, 0, columnspan=3)
        ttk.Label(meaning_frame, text="類義語・対義語はカンマ区切り。英→日で使うには「forsake=見捨てる」", foreground="gray").grid(row=5, column=1, columnspan=3, sticky="w", padx=5)
        ttk.Label(meaning_frame, text="例文 (1行1文)").grid(row=6, column=0, sticky="nw", padx=5, pady=3)
        self.examples_text = tk.Text(meaning_frame, height=3, wrap="word")
        self.examples_text.grid(row=6, column=1, columnspan=3, sticky="ew", padx=5, pady=3)
        ttk.Label(meaning_frame, text="活用形が自動で見つからない語は [overrode] のように囲む。分離動詞は [fängt] … [an]", foreground="gray").grid(row=7, column=1, columnspan=3, sticky="w", padx=5)
        ttk.Label(meaning_frame, text="和訳 (例文と同じ行)").grid(row=8, column=0, sticky="nw", padx=5, pady=3)
        self.translations_text = tk.Text(meaning_frame, height=3, wrap="word")
        self.translations_text.grid(row=8, column=1, columnspan=3, sticky="ew", padx=5, pady=3)
        meaning_buttons = ttk.Frame(meaning_frame)
        meaning_buttons.grid(row=9, column=0, columnspan=4, sticky="e", padx=5, pady=5)
        ttk.Button(meaning_buttons, text="入力欄をクリア", command=self.clear_meaning_form).grid(row=0, column=0, padx=3)
        ttk.Button(meaning_buttons, text="選択中の意味を更新", command=self.update_meaning).grid(row=0, column=1, padx=3)
        ttk.Button(meaning_buttons, text=f"意味を追加 ({SHORTCUT_LABEL}N)", command=self.add_meaning).grid(row=0, column=2, padx=3)

        list_frame = ttk.LabelFrame(self, text="この単語の意味 (選択すると上の入力欄で編集できます)")
        list_frame.grid(row=3, column=1, sticky="nsew", padx=(0, 10), pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("pos", "meaning", "forms", "synonyms", "antonyms", "examples")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=5, selectmode="browse")
        for column, heading, width in zip(columns, ("品詞", "意味", "語形", "類義語", "対義語", "例文"), (60, 200, 140, 120, 120, 50)):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=column not in ("pos", "examples"))
        self.tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.language_var.trace_add("write", lambda *args: self.update_language_form())
        self.pos_var.trace_add("write", lambda *args: self.update_form_fields())
        self.update_language_form()
        self.tree.bind("<<TreeviewSelect>>", self.on_meaning_selected)
        ttk.Button(list_frame, text="選択を削除", command=self.remove_meaning).grid(row=1, column=0, sticky="w", padx=5, pady=(0, 5))

        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 10))
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text=f"JSONに保存 ({SHORTCUT_LABEL}S)", command=self.save_word).grid(row=0, column=1)

        modifier = "Command" if IS_MAC else "Control"
        for key in ("s", "S"):
            self.bind(f"<{modifier}-{key}>", lambda event: self.save_word())
        for key in ("n", "N"):
            self.bind(f"<{modifier}-{key}>", self.on_new_shortcut)
        self.load_file()

    def on_new_shortcut(self, event: tk.Event) -> str:
        # Caps Lock でも keysym が "N" になるため、Shift は state のビットで判定する
        if event.state & SHIFT_MASK:
            self.new_word()
        else:
            self.commit_meaning()
        return "break"

    def _build_word_list(self) -> None:
        frame = ttk.LabelFrame(self, text="登録済みの単語")
        frame.grid(row=1, column=0, rowspan=3, sticky="nsew", padx=10, pady=5)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_word_list())
        ttk.Entry(frame, textvariable=self.search_var).grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        self.word_list = tk.Listbox(frame, width=24, exportselection=False)
        self.word_list.grid(row=1, column=0, sticky="nsew", padx=(5, 0))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.word_list.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 5))
        self.word_list.configure(yscrollcommand=scrollbar.set)
        self.word_list.bind("<<ListboxSelect>>", self.on_word_selected)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        ttk.Button(buttons, text=f"新規 ({SHORTCUT_LABEL}⇧N)", command=self.new_word).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(buttons, text="単語を削除", command=self.delete_word).grid(row=0, column=1)

    def _language(self) -> Language:
        return get_language(self.language_var.get())

    def update_language_form(self) -> None:
        language = self._language()
        self.pos_box.configure(values=language.parts_of_speech)
        if self.form_vars["gender"].get():
            self.form_vars["gender"].set(language.gender_label(self._gender_code()))
        columns = self.tree["columns"]
        self.tree.configure(displaycolumns=columns if language.has_forms else [column for column in columns if column != "forms"])
        self.update_form_fields()

    def update_form_fields(self) -> None:
        # 言語と品詞に応じた語形の欄 (ドイツ語の名詞なら性・複数形、動詞なら活用) だけを出す。
        # 欄が消えても入力値は消さず、保存時に今の品詞の欄の値だけを使う
        language = self._language()
        fields = language.fields_for(self.pos_var.get())
        shown = (language.name, fields)
        if shown == self.shown_form_fields:
            return
        self.shown_form_fields = shown
        for widget in self.forms_frame.winfo_children():
            widget.destroy()
        for index, form_field in enumerate(fields):
            row, column = divmod(index, 2)
            ttk.Label(self.forms_frame, text=form_field.label).grid(row=row, column=column * 2, sticky="w", padx=5, pady=3)
            variable = self.form_vars[form_field.key]
            if form_field.key == "gender":
                widget = ttk.Combobox(self.forms_frame, textvariable=variable, state="readonly",
                                      values=["", *(language.gender_label(code) for code in language.articles)])
            else:
                widget = ttk.Entry(self.forms_frame, textvariable=variable)
            widget.grid(row=row, column=column * 2 + 1, sticky="ew", padx=5, pady=3)
        if fields:
            self.forms_frame.grid()
        else:
            self.forms_frame.grid_remove()

    def _gender_code(self) -> str:
        # 表示は "m (der・男性)" のような形なので先頭のコードだけを取り出す
        return self.form_vars["gender"].get().split(" ", 1)[0]

    def _entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, column: int, combobox: bool = False, columnspan: int = 1) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=5, pady=3)
        widget = ttk.Combobox(parent, textvariable=variable) if combobox else ttk.Entry(parent, textvariable=variable)
        widget.grid(row=row, column=column + 1, columnspan=columnspan, sticky="ew", padx=5, pady=3)
        return widget

    def _has_unsaved_input(self) -> bool:
        # 意味欄の入力途中や、まだ一度も保存していない新しい単語も未保存として扱う
        return (
            self.dirty
            or bool(self.meaning_var.get().strip())
            or (self.current_index is None and bool(self.word_var.get().strip()))
        )

    def _confirm_discard(self) -> bool:
        return not self._has_unsaved_input() or messagebox.askyesno("未保存の変更", "保存していない変更は破棄されます。よろしいですか?")

    def choose_file(self) -> None:
        path = filedialog.asksaveasfilename(initialdir=MATERIALS_DIR, defaultextension=".json", filetypes=[("JSON", "*.json")], confirmoverwrite=False)
        if path:
            self.file_var.set(display_path(Path(path)))
            self.load_file()

    def load_file(self) -> None:
        path = resolve_material(self.file_var.get())
        if path == self.loaded_path or not self._confirm_discard():
            return
        try:
            entries = load_entries(path)
        except (OSError, ValueError) as error:
            messagebox.showerror("読み込みに失敗しました", str(error))
            return
        self.entries = entries
        self.loaded_path = path
        self.file_box.configure(values=material_files())
        self.clear_word()
        # 言語・出典はファイル内の最後の単語に合わせ、空のファイルなら言語をフォルダ名から決める
        last_entry = entries[-1] if entries else {}
        self.language_var.set(last_entry.get("language") or default_language(path) or self.language_var.get())
        self.source_var.set("")
        self.refresh_sources()
        self.status_var.set(f"{display_path(path)}: {len(entries)}語")

    def refresh_sources(self) -> None:
        sources = sorted({entry["source"] for entry in self.entries if isinstance(entry.get("source"), str)})
        self.source_box.configure(values=sources)
        last_source = next((entry["source"] for entry in reversed(self.entries) if isinstance(entry.get("source"), str)), "")
        if last_source and not self.source_var.get():
            self.source_var.set(last_source)

    def refresh_word_list(self) -> None:
        query = self.search_var.get().strip().lower()
        self.list_indices = [index for index, entry in enumerate(self.entries) if query in str(entry.get("word", "")).lower()]
        self.word_list.delete(0, "end")
        for index in self.list_indices:
            entry = self.entries[index]
            language = entry.get("language", "English")
            self.word_list.insert("end", entry.get("word", "") + ("" if language == "English" else f" [{language}]"))
        if self.current_index in self.list_indices:
            row = self.list_indices.index(self.current_index)
            self.word_list.selection_set(row)
            self.word_list.see(row)

    def on_word_selected(self, event: object = None) -> None:
        selection = self.word_list.curselection()
        if not selection or self.list_indices[selection[0]] == self.current_index:
            return
        if not self._confirm_discard():
            self.refresh_word_list()
            return
        self.load_entry(self.list_indices[selection[0]])

    def load_entry(self, index: int) -> None:
        entry = self.entries[index]
        try:
            derivatives = normalize_derivatives(entry)
        except ValueError as error:
            messagebox.showerror("読み込めない単語データです", str(error))
            return
        self.clear_word()
        self.current_index = index
        self.current_extra = {key: value for key, value in entry.items() if key not in WORD_KEYS}
        self.word_var.set(entry.get("word", ""))
        self.language_var.set(entry.get("language", "English"))
        self.difficulty_var.set(entry.get("difficulty") or "")
        self.source_var.set(entry.get("source") or "")
        self.derivatives_var.set(format_derivatives(derivatives))
        for part in entry.get("parts_of_speech", []):
            for meaning in part.get("meanings", []):
                meaning = {"meaning_ja": meaning} if isinstance(meaning, str) else copy.deepcopy(meaning)
                self.pending_parts.append((part.get("part_of_speech") or part.get("name"), meaning))
        self._refresh_tree()
        self.refresh_word_list()
        self.status_var.set(f"編集中: {entry.get('word')}")

    def new_word(self) -> None:
        if self._confirm_discard():
            self.clear_word()
            self.word_entry.focus_set()
            self.status_var.set("新しい単語を入力してください")

    def clear_word(self) -> None:
        # 言語・難易度・出典は続けて登録しやすいように残す
        self.current_index = None
        self.current_extra = {}
        self.word_var.set("")
        self.derivatives_var.set("")
        self.pending_parts.clear()
        self.clear_meaning_form()
        self._refresh_tree()
        self.refresh_word_list()
        self.dirty = False

    def clear_meaning_form(self) -> None:
        self.editing_meaning = None
        for variable in (self.meaning_var, self.synonyms_var, self.antonyms_var, *self.form_vars.values()):
            variable.set("")
        self.examples_text.delete("1.0", "end")
        self.translations_text.delete("1.0", "end")
        self.tree.selection_remove(*self.tree.selection())

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for part_of_speech, meaning in self.pending_parts:
            self.tree.insert("", "end", values=(
                part_of_speech,
                meaning["meaning_ja"],
                " / ".join(meaning[key] for key in FORM_KEYS if meaning.get(key)),
                ", ".join(_relation_key(item) for item in meaning.get("synonyms", [])),
                ", ".join(_relation_key(item) for item in meaning.get("antonyms", [])),
                len(meaning.get("examples", [])),
            ))

    def on_meaning_selected(self, event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.editing_meaning = self.tree.index(selection[0])
        part_of_speech, meaning = self.pending_parts[self.editing_meaning]
        self.pos_var.set(part_of_speech)
        self.meaning_var.set(meaning["meaning_ja"])
        for key, variable in self.form_vars.items():
            value = meaning.get(key) or ""
            variable.set(self._language().gender_label(value) if key == "gender" else value)
        self.synonyms_var.set(format_relations(meaning.get("synonyms", [])))
        self.antonyms_var.set(format_relations(meaning.get("antonyms", [])))
        sentences, translations = format_examples(meaning.get("examples", []))
        self.examples_text.delete("1.0", "end")
        self.examples_text.insert("1.0", sentences)
        self.translations_text.delete("1.0", "end")
        self.translations_text.insert("1.0", translations)

    def _meaning_from_form(self, base: dict) -> tuple[str, dict] | None:
        part_of_speech = self.pos_var.get().strip()
        meaning_ja = self.meaning_var.get().strip()
        if not part_of_speech or not meaning_ja:
            messagebox.showwarning("入力不足", "品詞と意味を入力してください")
            return None
        meaning: dict = {"meaning_ja": meaning_ja}
        for form_field in self._language().fields_for(part_of_speech):
            value = self._gender_code() if form_field.key == "gender" else self.form_vars[form_field.key].get().strip()
            if value:
                meaning[form_field.key] = value
        for key, variable in zip(RELATION_TYPES, (self.synonyms_var, self.antonyms_var)):
            relations = parse_relations(variable.get(), part_of_speech)
            if relations:
                meaning[key] = relations
        meaning.update({key: value for key, value in base.items() if key not in MEANING_KEYS})
        examples = parse_examples(self.examples_text.get("1.0", "end"), self.translations_text.get("1.0", "end"))
        word = self.word_var.get().strip()
        unusable = [
            example["sentence"] for example in examples
            if word and not find_cloze_spans(example["sentence"], word, self.language_var.get(), example.get("targets"), self._language().cloze_forms(meaning))
        ]
        if unusable and not messagebox.askyesno(
            "穴埋めに使えない例文",
            "次の例文は単語の位置を判定できないため穴埋めに出題されません。\n"
            "例文中の該当箇所を [ ] で囲むと出題できます。このまま登録しますか?\n\n" + "\n".join(unusable),
        ):
            return None
        if examples:
            meaning["examples"] = examples
        return part_of_speech, meaning

    def add_meaning(self) -> bool:
        result = self._meaning_from_form({})
        if result is None:
            return False
        self.pending_parts.append(result)
        self._refresh_tree()
        self.clear_meaning_form()
        self.dirty = True
        return True

    def update_meaning(self) -> bool:
        if self.editing_meaning is None:
            messagebox.showwarning("未選択", "一覧から更新する意味を選んでください")
            return False
        result = self._meaning_from_form(self.pending_parts[self.editing_meaning][1])
        if result is None:
            return False
        self.pending_parts[self.editing_meaning] = result
        self._refresh_tree()
        self.clear_meaning_form()
        self.dirty = True
        return True

    def remove_meaning(self) -> None:
        for item in self.tree.selection():
            del self.pending_parts[self.tree.index(item)]
        self._refresh_tree()
        self.clear_meaning_form()
        self.dirty = True

    def _build_entry(self, word: str, derivatives: list[dict]) -> dict:
        entry: dict = {"word": word, "language": self.language_var.get().strip() or "English"}
        if self.difficulty_var.get().strip():
            entry["difficulty"] = self.difficulty_var.get().strip()
        if self.source_var.get().strip():
            entry["source"] = self.source_var.get().strip()
        entry["parts_of_speech"] = []
        for part_of_speech, meaning in self.pending_parts:
            part = next((item for item in entry["parts_of_speech"] if item["part_of_speech"] == part_of_speech), None)
            if part is None:
                part = {"part_of_speech": part_of_speech, "meanings": []}
                entry["parts_of_speech"].append(part)
            part["meanings"].append(meaning)
        if derivatives:
            entry["derivatives"] = derivatives
        entry.update(self.current_extra)
        return entry

    def _write(self, entries: list[dict]) -> bool:
        try:
            save_entries(self.loaded_path, entries)
        except OSError as error:
            messagebox.showerror("保存に失敗しました", str(error))
            return False
        self.entries = entries
        return True

    def commit_meaning(self) -> bool:
        """意味欄の内容を確定する。一覧で選んだ意味を編集中なら更新、そうでなければ追加し、次の意味の入力に移る。"""
        if not self.meaning_var.get().strip():
            self.meaning_entry.focus_set()
            return True
        committed = self.update_meaning() if self.editing_meaning is not None else self.add_meaning()
        if committed:
            self.meaning_entry.focus_set()
        return committed

    def save_word(self) -> None:
        if resolve_material(self.file_var.get()) != self.loaded_path:
            messagebox.showwarning("保存先が未読み込み", "保存先を変更した場合は Enter で読み込んでから保存してください")
            return
        word = self.word_var.get().strip()
        if not word:
            messagebox.showwarning("入力不足", "単語を入力してください")
            return
        # 意味欄に入力したまま保存した場合は、その意味も含める
        if not self.commit_meaning():
            return
        if not self.pending_parts:
            messagebox.showwarning("入力不足", "意味を1つ以上追加してください")
            return
        try:
            derivatives = parse_derivatives(self.derivatives_var.get())
        except ValueError as error:
            messagebox.showwarning("派生語の形式", str(error))
            return

        entry = self._build_entry(word, derivatives)
        entries = copy.deepcopy(self.entries)
        duplicate = next((
            index for index, item in enumerate(entries)
            if index != self.current_index and item.get("word") == word and item.get("language", "English") == entry["language"]
        ), None)
        if self.current_index is not None:
            if duplicate is not None:
                messagebox.showwarning("重複", f"{word} ({entry['language']}) は既に登録されています")
                return
            entries[self.current_index] = entry
            action = "更新"
        elif duplicate is not None:
            if not messagebox.askyesno("登録済みの単語", f"{word} は登録済みです。品詞・意味を追記しますか?"):
                return
            merge_entry(entries, entry)
            action = "既存の単語に追記"
        else:
            entries.append(entry)
            action = "追加"
        if not self._write(entries):
            return
        self.clear_word()
        self.refresh_sources()
        self.status_var.set(f"{word} を{action}しました ({display_path(self.loaded_path)}: {len(entries)}語)")

    def delete_word(self) -> None:
        if self.current_index is None:
            messagebox.showwarning("未選択", "一覧から削除する単語を選んでください")
            return
        word = self.entries[self.current_index].get("word")
        if not messagebox.askyesno("単語を削除", f"{word} をJSONから削除しますか?\n(取り込み済みのDBからは削除されません)"):
            return
        entries = copy.deepcopy(self.entries)
        del entries[self.current_index]
        if self._write(entries):
            self.clear_word()
            self.status_var.set(f"{word} を削除しました ({display_path(self.loaded_path)}: {len(entries)}語)")


if __name__ == "__main__":
    WordEditor().mainloop()

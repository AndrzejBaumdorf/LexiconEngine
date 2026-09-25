from __future__ import annotations

import json
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"
PARTS_OF_SPEECH = ["名詞", "動詞", "形容詞", "副詞", "前置詞", "接続詞", "熟語"]
RELATION_TYPES = ("synonyms", "antonyms")


def parse_relations(text: str, part_of_speech: str) -> list[str | dict]:
    # "forsake=見捨てる" は意味付きのオブジェクト、"forsake" だけなら文字列として保存する
    relations: list[str | dict] = []
    for item in re.split(r"[,，]", text):
        word, _, meaning = (part.strip() for part in item.partition("="))
        if not word:
            continue
        relations.append({"word": word, "part_of_speech": part_of_speech, "meaning_ja": meaning} if meaning else word)
    return relations


def parse_examples(sentences: str, translations: str) -> list[dict]:
    translation_lines = translations.splitlines()
    examples = []
    for index, sentence in enumerate(sentences.splitlines()):
        if not sentence.strip():
            continue
        example = {"sentence": sentence.strip()}
        translation = translation_lines[index].strip() if index < len(translation_lines) else ""
        if translation:
            example["translation_ja"] = translation
        examples.append(example)
    return examples


def _relation_key(relation: str | dict) -> str:
    return relation if isinstance(relation, str) else relation["word"]


def _merge_meaning(existing: dict, meaning: dict) -> None:
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
    return True


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
        self.title("LexiconEngine 単語登録")
        self.geometry("760x720")
        self.pending_parts: list[tuple[str, dict]] = []
        self.columnconfigure(0, weight=1)

        file_frame = ttk.LabelFrame(self, text="保存先")
        file_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        file_frame.columnconfigure(0, weight=1)
        json_files = sorted(str(path) for path in MATERIALS_DIR.glob("*.json"))
        self.file_var = tk.StringVar(value=json_files[0] if json_files else str(MATERIALS_DIR / "words.json"))
        ttk.Combobox(file_frame, textvariable=self.file_var, values=json_files).grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        ttk.Button(file_frame, text="参照…", command=self.choose_file).grid(row=0, column=1, padx=5)

        word_frame = ttk.LabelFrame(self, text="単語")
        word_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        word_frame.columnconfigure(1, weight=1)
        word_frame.columnconfigure(3, weight=1)
        self.word_var = tk.StringVar()
        self.language_var = tk.StringVar(value="English")
        self.difficulty_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self._entry(word_frame, "単語", self.word_var, 0, 0)
        self._entry(word_frame, "言語", self.language_var, 0, 2)
        self._entry(word_frame, "難易度", self.difficulty_var, 1, 0)
        self.source_box = self._entry(word_frame, "出典", self.source_var, 1, 2, combobox=True)
        self.refresh_sources()

        meaning_frame = ttk.LabelFrame(self, text="意味")
        meaning_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        meaning_frame.columnconfigure(1, weight=1)
        self.pos_var = tk.StringVar(value=PARTS_OF_SPEECH[0])
        self.meaning_var = tk.StringVar()
        self.synonyms_var = tk.StringVar()
        self.antonyms_var = tk.StringVar()
        self._entry(meaning_frame, "品詞", self.pos_var, 0, 0, combobox=True).configure(values=PARTS_OF_SPEECH)
        self._entry(meaning_frame, "意味", self.meaning_var, 1, 0)
        self._entry(meaning_frame, "類義語", self.synonyms_var, 2, 0)
        self._entry(meaning_frame, "対義語", self.antonyms_var, 3, 0)
        ttk.Label(meaning_frame, text="類義語・対義語はカンマ区切り。英→日で使うには「forsake=見捨てる」のように意味も書く", foreground="gray").grid(row=4, column=1, sticky="w", padx=5)
        ttk.Label(meaning_frame, text="例文 (1行1文)").grid(row=5, column=0, sticky="nw", padx=5, pady=3)
        self.examples_text = tk.Text(meaning_frame, height=3, wrap="word")
        self.examples_text.grid(row=5, column=1, sticky="ew", padx=5, pady=3)
        ttk.Label(meaning_frame, text="和訳 (例文と同じ行)").grid(row=6, column=0, sticky="nw", padx=5, pady=3)
        self.translations_text = tk.Text(meaning_frame, height=3, wrap="word")
        self.translations_text.grid(row=6, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(meaning_frame, text="意味を追加", command=self.add_meaning).grid(row=7, column=1, sticky="e", padx=5, pady=5)

        list_frame = ttk.LabelFrame(self, text="この単語に追加する意味")
        list_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        columns = ("pos", "meaning", "synonyms", "antonyms", "examples")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=5)
        for column, heading, width in zip(columns, ("品詞", "意味", "類義語", "対義語", "例文"), (60, 220, 150, 150, 50)):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=column != "examples")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        ttk.Button(list_frame, text="選択を削除", command=self.remove_meaning).grid(row=1, column=0, sticky="w", padx=5, pady=(0, 5))

        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="JSONに保存", command=self.save_word).grid(row=0, column=1)

    def _entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, column: int, combobox: bool = False) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=5, pady=3)
        widget = ttk.Combobox(parent, textvariable=variable) if combobox else ttk.Entry(parent, textvariable=variable)
        widget.grid(row=row, column=column + 1, sticky="ew", padx=5, pady=3)
        return widget

    def choose_file(self) -> None:
        path = filedialog.asksaveasfilename(initialdir=MATERIALS_DIR, defaultextension=".json", filetypes=[("JSON", "*.json")], confirmoverwrite=False)
        if path:
            self.file_var.set(path)
            self.refresh_sources()

    def refresh_sources(self) -> None:
        try:
            entries = load_entries(Path(self.file_var.get()))
        except (OSError, ValueError):
            return
        sources = sorted({entry["source"] for entry in entries if isinstance(entry.get("source"), str)})
        self.source_box.configure(values=sources)
        last_source = next((entry["source"] for entry in reversed(entries) if isinstance(entry.get("source"), str)), "")
        if last_source and not self.source_var.get():
            self.source_var.set(last_source)

    def _meaning_form_filled(self) -> bool:
        return bool(self.meaning_var.get().strip())

    def add_meaning(self) -> bool:
        part_of_speech = self.pos_var.get().strip()
        meaning_ja = self.meaning_var.get().strip()
        if not part_of_speech or not meaning_ja:
            messagebox.showwarning("入力不足", "品詞と意味を入力してください")
            return False
        meaning: dict = {"meaning_ja": meaning_ja}
        for key, variable in zip(RELATION_TYPES, (self.synonyms_var, self.antonyms_var)):
            relations = parse_relations(variable.get(), part_of_speech)
            if relations:
                meaning[key] = relations
        examples = parse_examples(self.examples_text.get("1.0", "end"), self.translations_text.get("1.0", "end"))
        if examples:
            meaning["examples"] = examples
        self.pending_parts.append((part_of_speech, meaning))
        self.tree.insert("", "end", values=(
            part_of_speech,
            meaning_ja,
            ", ".join(_relation_key(item) for item in meaning.get("synonyms", [])),
            ", ".join(_relation_key(item) for item in meaning.get("antonyms", [])),
            len(examples),
        ))
        for variable in (self.meaning_var, self.synonyms_var, self.antonyms_var):
            variable.set("")
        self.examples_text.delete("1.0", "end")
        self.translations_text.delete("1.0", "end")
        return True

    def remove_meaning(self) -> None:
        for item in self.tree.selection():
            del self.pending_parts[self.tree.index(item)]
            self.tree.delete(item)

    def save_word(self) -> None:
        word = self.word_var.get().strip()
        if not word:
            messagebox.showwarning("入力不足", "単語を入力してください")
            return
        # 意味欄に入力したまま保存した場合は、その意味も含める
        if self._meaning_form_filled() and not self.add_meaning():
            return
        if not self.pending_parts:
            messagebox.showwarning("入力不足", "意味を1つ以上追加してください")
            return
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

        path = Path(self.file_var.get())
        try:
            entries = load_entries(path)
            merged = merge_entry(entries, entry)
            save_entries(path, entries)
        except (OSError, ValueError) as error:
            messagebox.showerror("保存に失敗しました", str(error))
            return
        self.status_var.set(f"{word} を{'既存の単語に追記' if merged else '追加'}しました ({path.name}: {len(entries)}語)")
        self.word_var.set("")
        self.pending_parts.clear()
        self.tree.delete(*self.tree.get_children())
        self.refresh_sources()


if __name__ == "__main__":
    WordEditor().mainloop()

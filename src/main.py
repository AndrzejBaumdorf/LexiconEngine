from __future__ import annotations

import argparse
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from database import LexiconDatabase, QuizScope
from languages import get_language
from word_editor import WordEditor


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.db"
DEFAULT_SAMPLE_JSON_PATH = Path(__file__).resolve().parent.parent / "samples" / "sample_words.json"
MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"
# 問題形式のIDは回答履歴に保存されるため変えない。表示名の {lang} は選んだ言語の略称 (英・独など) になる
SKIPPED_ANSWER = "(スキップ)"
SCOPE_LIST_HEIGHT = 4
QUESTION_TYPES = [
    ("{lang}→日", "english_to_japanese"),
    ("日→{lang}", "japanese_to_english"),
    ("例文穴埋め", "cloze"),
    ("類義語・対義語", "relation"),
    ("派生語", "derivative"),
    ("ラテン文字転写", "romanization"),
    ("性 (冠詞)", "gender"),
    ("複数形", "plural"),
]


def available_question_types(language: str) -> list[str]:
    # 転写・性・複数形などの問題形式は、対応した言語 (韓国語・ドイツ語など) のときだけ選べる
    language_spec = get_language(language)
    return [value for _, value in QUESTION_TYPES if language_spec.supports(value)]


def question_label(question_type: str, language: str) -> str:
    template = next(label for label, value in QUESTION_TYPES if value == question_type)
    return template.format(lang=get_language(language).label if language else "外")


class QuizApp(tk.Tk):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.title("LexiconEngine クイズ")
        # 問題文や選択肢が長いときは内容に合わせてウィンドウが広がる
        self.minsize(640, 600)
        self.database: LexiconDatabase | None = None
        self.question = None
        self.question_type = ""
        self.quiz_language = ""
        self.quiz_scope = QuizScope()
        self.question_count = 0
        self.question_index = 0
        self.correct_count = 0
        self.answered_count = 0
        self.show_hint = False
        self.answer_var = tk.IntVar(value=-1)
        self.answering = False
        self.editor: WordEditor | None = None

        self.columnconfigure(0, weight=1)
        self._build_db_frame()
        self._build_setup_frame()
        self._build_quiz_frame()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.open_database(db_path)

    def _build_db_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="データベース")
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        frame.columnconfigure(0, weight=1)
        self.db_path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.db_path_var).grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        ttk.Button(frame, text="参照…", command=self.choose_db).grid(row=0, column=1, padx=5)
        ttk.Button(frame, text="開く", command=lambda: self.open_database(Path(self.db_path_var.get()))).grid(row=0, column=2, padx=5)
        ttk.Button(frame, text="JSON取り込み", command=self.import_json).grid(row=0, column=3, padx=5)
        ttk.Button(frame, text="フォルダ取り込み", command=self.import_folder).grid(row=0, column=4, padx=5)
        ttk.Button(frame, text="単語編集", command=self.open_editor).grid(row=0, column=5, padx=5)
        self.db_status_var = tk.StringVar(value="未接続")
        ttk.Label(frame, textvariable=self.db_status_var, foreground="gray").grid(row=1, column=0, columnspan=6, sticky="w", padx=5, pady=(0, 5))

    def _build_setup_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="クイズ設定")
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)
        self.setup_frame = frame

        ttk.Label(frame, text="言語").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.language_var = tk.StringVar()
        self.language_box = ttk.Combobox(frame, textvariable=self.language_var, state="readonly")
        self.language_box.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        self.language_box.bind("<<ComboboxSelected>>", lambda event: self._on_language_selected())

        # 出典・レベルは複数選択できる。何も選ばなければすべてが対象
        ttk.Label(frame, text="出典\n(未選択=すべて)").grid(row=1, column=0, sticky="nw", padx=5, pady=5)
        self.source_list = self._scope_list(frame, 1)
        ttk.Label(frame, text="レベル\n(未選択=すべて)").grid(row=2, column=0, sticky="nw", padx=5, pady=5)
        self.difficulty_list = self._scope_list(frame, 2)

        ttk.Label(frame, text="問題形式").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.type_var = tk.StringVar()
        self.type_box = ttk.Combobox(frame, textvariable=self.type_var, state="readonly")
        self.type_box.grid(row=3, column=1, sticky="ew", padx=5, pady=5)
        self.type_box.bind("<<ComboboxSelected>>", self._on_type_selected)

        ttk.Label(frame, text="出題数 (1〜100)").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.count_var = tk.StringVar(value="10")
        ttk.Spinbox(frame, from_=1, to=100, textvariable=self.count_var, width=5).grid(row=4, column=1, sticky="w", padx=5, pady=5)

        self.hint_var = tk.BooleanVar(value=False)
        self.hint_check = ttk.Checkbutton(frame, text="日本語ヒントを表示する (穴埋めのみ)", variable=self.hint_var)
        self.hint_check.grid(row=5, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))
        self.hint_check.state(["disabled"])

        self.start_button = ttk.Button(frame, text="開始", command=self.start_quiz)
        self.start_button.grid(row=6, column=1, sticky="e", padx=5, pady=(0, 5))
        self._refresh_type_labels()

    def _scope_list(self, parent: ttk.Frame, row: int) -> tk.Listbox:
        container = ttk.Frame(parent)
        container.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        container.columnconfigure(0, weight=1)
        listbox = tk.Listbox(container, selectmode="multiple", exportselection=False, height=SCOPE_LIST_HEIGHT)
        listbox.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)
        return listbox

    @staticmethod
    def _selected_items(listbox: tk.Listbox) -> tuple[str, ...]:
        return tuple(listbox.get(index) for index in listbox.curselection())

    def _refresh_languages(self) -> None:
        # DBに登録されている言語から選ぶ。取り込みで言語が増えたときも選択中の言語は保つ
        languages = self.database.languages() if self.database else []
        self.language_box.configure(values=languages)
        if self.language_var.get() not in languages:
            self.language_var.set(languages[0] if languages else "")
        self._on_language_selected()

    def _on_language_selected(self) -> None:
        # 出典・レベルの候補は選んだ言語に登録されているものだけにする。候補に残った値は選択を保つ
        sources, difficulties = self.database.scope_options(self.language_var.get()) if self.database else ([], [])
        for listbox, values in ((self.source_list, sources), (self.difficulty_list, difficulties)):
            selected = self._selected_items(listbox)
            listbox.delete(0, "end")
            for index, value in enumerate(values):
                listbox.insert("end", value)
                if value in selected:
                    listbox.selection_set(index)
        self._refresh_type_labels()

    def _scope(self) -> QuizScope:
        return QuizScope(self.language_var.get(), self._selected_items(self.source_list), self._selected_items(self.difficulty_list))

    def _refresh_type_labels(self) -> None:
        selected = self._selected_type() if self.type_var.get() else QUESTION_TYPES[0][1]
        self.type_values = available_question_types(self.language_var.get())
        fell_back = selected not in self.type_values
        if fell_back:
            selected = self.type_values[0]
        self.type_box.configure(values=[question_label(value, self.language_var.get()) for value in self.type_values])
        self.type_var.set(question_label(selected, self.language_var.get()))
        if fell_back:
            self._on_type_selected()

    def _on_type_selected(self, event: object = None) -> None:
        is_cloze = self._selected_type() == "cloze"
        self.hint_check.state(["!disabled"] if is_cloze else ["disabled"])
        if not is_cloze:
            self.hint_var.set(False)

    def _selected_type(self) -> str:
        # 表示名は言語で変わるため、選択肢の位置で問題形式を判定する
        return self.type_values[self.type_box.current()]

    def _build_quiz_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="問題")
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        frame.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.quiz_frame = frame

        self.progress_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.progress_var).grid(row=0, column=0, sticky="w", padx=5, pady=(5, 0))

        self.prompt_var = tk.StringVar()
        prompt_label = ttk.Label(frame, textvariable=self.prompt_var, wraplength=480, justify="left", font=("", 11))
        prompt_label.grid(row=1, column=0, sticky="w", padx=5, pady=10)
        frame.bind("<Configure>", lambda event: prompt_label.configure(wraplength=max(200, event.width - 30)))

        self.choices_frame = ttk.Frame(frame)
        self.choices_frame.grid(row=2, column=0, sticky="ew", padx=5)
        self.choices_frame.columnconfigure(0, weight=1)

        self.feedback_var = tk.StringVar()
        self.feedback_label = ttk.Label(frame, textvariable=self.feedback_var, font=("", 10, "bold"))
        self.feedback_label.grid(row=3, column=0, sticky="w", padx=5, pady=5)

        self.score_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.score_var, foreground="gray", wraplength=480, justify="left").grid(row=4, column=0, sticky="w", padx=5)

        # ボタンは問題枠の外に置き、ウィンドウが狭いときは問題枠のほうを縮めて常に押せるようにする
        button_row = ttk.Frame(self)
        button_row.grid(row=3, column=0, sticky="e", padx=15, pady=(0, 10))
        self.answer_button = ttk.Button(button_row, text="回答する (Enter)", command=self.submit_answer)
        self.answer_button.grid(row=0, column=0, padx=5)
        self.skip_button = ttk.Button(button_row, text="スキップ (S)", command=self.skip_question)
        self.skip_button.grid(row=0, column=1, padx=5)
        self.stop_button = ttk.Button(button_row, text="中断 (Esc)", command=self.finish_quiz)
        self.stop_button.grid(row=0, column=2, padx=5)

        self._set_quiz_active(False)

    def _set_quiz_active(self, active: bool) -> None:
        state = "normal" if active else "disabled"
        self.answer_button.configure(state=state)
        self.skip_button.configure(state=state)
        self.stop_button.configure(state=state)
        if not active:
            self.answering = False
            self.progress_var.set("")
            self.prompt_var.set("クイズ設定から開始してください" if self.database else "データベースを開いてください")
            self.feedback_var.set("")
            for widget in self.choices_frame.winfo_children():
                widget.destroy()

    def _bind_shortcuts(self) -> None:
        for number in range(1, 10):
            self.bind(str(number), lambda event, index=number - 1: self._select_choice(index))
        self.bind("<Return>", lambda event: self._invoke(self.answer_button))
        self.bind("<KP_Enter>", lambda event: self._invoke(self.answer_button))
        for key in ("s", "S"):
            self.bind(key, lambda event: self._invoke(self.skip_button))
        self.bind("<Escape>", lambda event: self._invoke(self.stop_button))

    def _shortcut_allowed(self) -> bool:
        # クイズ設定の出題数などに入力中のキーは横取りしない
        return not isinstance(self.focus_get(), (tk.Entry, ttk.Entry))

    def _invoke(self, button: ttk.Button) -> None:
        if self._shortcut_allowed() and button.instate(["!disabled"]):
            button.invoke()

    def _select_choice(self, index: int) -> None:
        if self._shortcut_allowed() and self.answering and self.question and index < len(self.question.choices):
            self.answer_var.set(index)

    def _set_setup_enabled(self, enabled: bool) -> None:
        flag = "!disabled" if enabled else "disabled"
        for child in self.setup_frame.winfo_children():
            child.state([flag])
        for listbox in (self.source_list, self.difficulty_list):
            listbox.configure(state="normal" if enabled else "disabled")
        self._on_type_selected()

    def choose_db(self) -> None:
        current = self.db_path_var.get()
        initial_dir = Path(current).parent if current else DEFAULT_DB_PATH.parent
        path = filedialog.asksaveasfilename(initialdir=initial_dir, defaultextension=".db", filetypes=[("SQLite DB", "*.db")], confirmoverwrite=False)
        if path:
            self.open_database(Path(path))

    def open_database(self, path: Path) -> None:
        try:
            database_exists = path.exists()
            database = LexiconDatabase(path)
            database.initialize()
            if not database_exists:
                count = database.import_json(DEFAULT_SAMPLE_JSON_PATH)
                self.db_status_var.set(f"新規作成: {path.name} (サンプル{count}件取り込み)")
            else:
                self.db_status_var.set(f"接続中: {path.name}")
        except (OSError, ValueError) as error:
            messagebox.showerror("データベースを開けません", str(error))
            return
        if self.database is not None:
            self.database.close()
        self.database = database
        self._refresh_languages()
        self.db_path_var.set(str(path))
        self._set_quiz_active(False)

    def import_json(self) -> None:
        if self.database is None:
            messagebox.showwarning("未接続", "先にデータベースを開いてください")
            return
        path = filedialog.askopenfilename(initialdir=MATERIALS_DIR, filetypes=[("JSON", "*.json")])
        if path:
            self._import_files([Path(path)])

    def import_folder(self) -> None:
        if self.database is None:
            messagebox.showwarning("未接続", "先にデータベースを開いてください")
            return
        folder = filedialog.askdirectory(initialdir=MATERIALS_DIR, mustexist=True)
        if not folder:
            return
        paths = sorted(Path(folder).rglob("*.json"))
        if not paths:
            messagebox.showwarning("JSONがありません", f"{folder} にJSONファイルがありません")
            return
        self._import_files(paths)

    def _import_files(self, paths: list[Path]) -> None:
        # 1ファイル失敗しても他のファイルは取り込み、最後にまとめて報告する
        count = 0
        errors = []
        for path in paths:
            try:
                count += self.database.import_json(path)
            except (OSError, ValueError) as error:
                errors.append(f"{path.name}: {error}")
        self._refresh_languages()
        message = f"{len(paths) - len(errors)}ファイル・{count}件取り込みました。"
        if errors:
            messagebox.showerror("一部の取り込みに失敗しました", message + "\n\n" + "\n".join(errors))
        else:
            messagebox.showinfo("取り込み完了", message)

    def open_editor(self) -> None:
        # エディタは1つだけ開く。既に開いていれば前面に出す
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.deiconify()
            self.editor.lift()
            self.editor.focus_set()
            return
        self.editor = WordEditor(self, on_close=self._on_editor_closed)

    def _on_editor_closed(self, saved_paths: set[Path]) -> None:
        # エディタはJSONだけを書き換えるので、保存したファイルをDBに取り込むか確認する
        self.editor = None
        if not saved_paths or self.database is None:
            return
        names = "\n".join(sorted(path.name for path in saved_paths))
        if messagebox.askyesno("DBに取り込む", f"編集したJSONをDBに取り込みますか?\n\n{names}", parent=self):
            self._import_files(sorted(saved_paths))

    def start_quiz(self) -> None:
        if self.database is None:
            messagebox.showwarning("未接続", "先にデータベースを開いてください")
            return
        count_text = self.count_var.get().strip()
        if not count_text.isdigit() or not 1 <= int(count_text) <= 100:
            messagebox.showwarning("入力エラー", "出題数は1〜100の整数で入力してください")
            return
        if not self.language_var.get():
            messagebox.showwarning("単語がありません", "先に単語データを取り込んでください")
            return
        self.question_type = self._selected_type()
        self.quiz_language = self.language_var.get()
        self.quiz_scope = self._scope()
        self.question_count = int(count_text)
        self.question_index = 0
        self.correct_count = 0
        self.answered_count = 0
        self.show_hint = self.question_type == "cloze" and self.hint_var.get()
        self.score_var.set("")
        self._set_setup_enabled(False)
        self._set_quiz_active(True)
        self.next_question()

    def next_question(self) -> None:
        self.question_index += 1
        if self.question_index > self.question_count:
            self.finish_quiz()
            return
        try:
            self.question = self.database.create_question(self.question_type, self.show_hint, self.quiz_scope)
        except ValueError as error:
            messagebox.showerror("出題エラー", str(error))
            self.finish_quiz()
            return
        self.progress_var.set(f"[{self.question_index}/{self.question_count}]")
        self.prompt_var.set(self.question.prompt)
        self.feedback_var.set("")
        for widget in self.choices_frame.winfo_children():
            widget.destroy()
        self.answer_var.set(-1)
        for index, choice in enumerate(self.question.choices):
            ttk.Radiobutton(self.choices_frame, text=f"{index + 1}. {choice}", value=index, variable=self.answer_var).grid(row=index, column=0, sticky="w", pady=2)
        self.answer_button.configure(text="回答する (Enter)", command=self.submit_answer, state="normal")
        self.skip_button.configure(state="normal")
        self.answering = True
        self.focus_set()

    def submit_answer(self) -> None:
        selected_index = self.answer_var.get()
        if selected_index < 0:
            messagebox.showwarning("未選択", "回答を選択してください")
            return
        self._reveal(self.question.choices[selected_index])

    def skip_question(self) -> None:
        # スキップは不正解として履歴に残し、正解を表示する
        self._reveal(SKIPPED_ANSWER)

    def _reveal(self, selected_answer: str) -> None:
        self.answering = False
        for widget in self.choices_frame.winfo_children():
            widget.configure(state="disabled")
        self.answered_count += 1
        if self.database.record_answer(self.question, selected_answer):
            self.correct_count += 1
            self.feedback_var.set("正解!")
            self.feedback_label.configure(foreground="green")
        else:
            prefix = "スキップ" if selected_answer == SKIPPED_ANSWER else "不正解"
            self.feedback_var.set(f"{prefix}。正解は {self.question.answer_detail or self.question.answer} です。")
            self.feedback_label.configure(foreground="red")
        is_last = self.question_index >= self.question_count
        self.answer_button.configure(text="結果を見る (Enter)" if is_last else "次の問題へ (Enter)", command=self.next_question)
        self.skip_button.configure(state="disabled")

    def finish_quiz(self) -> None:
        self._set_quiz_active(False)
        self._set_setup_enabled(True)
        if self.answered_count and self.question is not None:
            total, correct, accuracy = self.database.recent_accuracy(self.question.question_type, language=self.quiz_language)
            self.score_var.set(
                f"今回: {self.correct_count}/{self.answered_count} ({self.correct_count / self.answered_count * 100:.1f}%)\n"
                f"{self.quiz_language} {question_label(self.question.question_type, self.quiz_language)} 直近{total}問: {accuracy:.1f}% ({correct}/{total})"
            )

    def on_close(self) -> None:
        if self.editor is not None and self.editor.winfo_exists():
            if not self.editor.confirm_discard():
                return
            self.editor.destroy()
        if self.database is not None:
            self.database.close()
        self.destroy()


def reset_database(db_path: Path, assume_yes: bool = False) -> bool:
    """DBを作り直す。元のDBは削除せず lexicon-backup-<日時>.db に名前を変えて残す。中止したら False。"""
    if not db_path.exists():
        print(f"{db_path} はまだありません。新しく作成します。")
        return True
    if not assume_yes:
        answer = input(f"{db_path} をリセットします。回答履歴も含めて空のDBから作り直します。よろしいですか? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("中止しました。")
            return False
    backup_path = db_path.with_name(f"{db_path.stem}-backup-{datetime.now():%Y%m%d-%H%M%S}{db_path.suffix}")
    db_path.rename(backup_path)
    # SQLiteの一時ファイルが残っていれば一緒に退避する
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.rename(backup_path.with_name(backup_path.name + suffix))
    print(f"元のDBを {backup_path} に退避しました。")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLiteベースの単語クイズ (GUI)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--reset", action="store_true", help="DBを空の状態から作り直してから起動する (元のDBはバックアップとして残す)")
    parser.add_argument("-y", "--yes", action="store_true", help="--reset の確認を省略する")
    args = parser.parse_args()
    if args.reset and not reset_database(args.db, args.yes):
        sys.exit(1)
    QuizApp(args.db).mainloop()


if __name__ == "__main__":
    main()

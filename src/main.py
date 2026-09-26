from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from database import LexiconDatabase


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.db"
DEFAULT_SAMPLE_JSON_PATH = Path(__file__).resolve().parent.parent / "materials" / "sample_words.json"
QUESTION_TYPES = [
    ("英→日", "english_to_japanese"),
    ("日→英", "japanese_to_english"),
    ("例文穴埋め", "cloze"),
    ("類義語・対義語", "relation"),
]
QUESTION_LABELS = {value: label for label, value in QUESTION_TYPES}


class QuizApp(tk.Tk):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.title("LexiconEngine クイズ")
        self.geometry("560x560")
        self.resizable(False, False)
        self.database: LexiconDatabase | None = None
        self.question = None
        self.question_type = ""
        self.question_count = 0
        self.question_index = 0
        self.correct_count = 0
        self.answered_count = 0
        self.show_hint = False
        self.answer_var = tk.IntVar(value=-1)

        self.columnconfigure(0, weight=1)
        self._build_db_frame()
        self._build_setup_frame()
        self._build_quiz_frame()
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
        self.db_status_var = tk.StringVar(value="未接続")
        ttk.Label(frame, textvariable=self.db_status_var, foreground="gray").grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(0, 5))

    def _build_setup_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="クイズ設定")
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)
        self.setup_frame = frame

        ttk.Label(frame, text="問題形式").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.type_var = tk.StringVar(value=QUESTION_TYPES[0][0])
        type_box = ttk.Combobox(frame, textvariable=self.type_var, values=[label for label, _ in QUESTION_TYPES], state="readonly")
        type_box.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        type_box.bind("<<ComboboxSelected>>", self._on_type_selected)

        ttk.Label(frame, text="出題数 (1〜100)").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.count_var = tk.StringVar(value="10")
        ttk.Spinbox(frame, from_=1, to=100, textvariable=self.count_var, width=5).grid(row=1, column=1, sticky="w", padx=5, pady=5)

        self.hint_var = tk.BooleanVar(value=False)
        self.hint_check = ttk.Checkbutton(frame, text="日本語ヒントを表示する (穴埋めのみ)", variable=self.hint_var)
        self.hint_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))
        self.hint_check.state(["disabled"])

        self.start_button = ttk.Button(frame, text="開始", command=self.start_quiz)
        self.start_button.grid(row=3, column=1, sticky="e", padx=5, pady=(0, 5))

    def _on_type_selected(self, event: object = None) -> None:
        is_cloze = self._selected_type() == "cloze"
        self.hint_check.state(["!disabled"] if is_cloze else ["disabled"])
        if not is_cloze:
            self.hint_var.set(False)

    def _selected_type(self) -> str:
        label = self.type_var.get()
        return next(value for text, value in QUESTION_TYPES if text == label)

    def _build_quiz_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="問題")
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        frame.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.quiz_frame = frame

        self.progress_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.progress_var).grid(row=0, column=0, sticky="w", padx=5, pady=(5, 0))

        self.prompt_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.prompt_var, wraplength=480, justify="left", font=("", 11)).grid(row=1, column=0, sticky="w", padx=5, pady=10)

        self.choices_frame = ttk.Frame(frame)
        self.choices_frame.grid(row=2, column=0, sticky="ew", padx=5)
        self.choices_frame.columnconfigure(0, weight=1)

        self.feedback_var = tk.StringVar()
        self.feedback_label = ttk.Label(frame, textvariable=self.feedback_var, font=("", 10, "bold"))
        self.feedback_label.grid(row=3, column=0, sticky="w", padx=5, pady=5)

        self.score_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.score_var, foreground="gray", wraplength=480, justify="left").grid(row=4, column=0, sticky="w", padx=5)

        button_row = ttk.Frame(frame)
        button_row.grid(row=5, column=0, sticky="e", padx=5, pady=10)
        self.answer_button = ttk.Button(button_row, text="回答する", command=self.submit_answer)
        self.answer_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(button_row, text="中断", command=self.finish_quiz)
        self.stop_button.grid(row=0, column=1, padx=5)

        self._set_quiz_active(False)

    def _set_quiz_active(self, active: bool) -> None:
        state = "normal" if active else "disabled"
        self.answer_button.configure(state=state)
        self.stop_button.configure(state=state)
        if not active:
            self.progress_var.set("")
            self.prompt_var.set("クイズ設定から開始してください" if self.database else "データベースを開いてください")
            self.feedback_var.set("")
            for widget in self.choices_frame.winfo_children():
                widget.destroy()

    def _set_setup_enabled(self, enabled: bool) -> None:
        flag = "!disabled" if enabled else "disabled"
        for child in self.setup_frame.winfo_children():
            child.state([flag])
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
                self.db_status_var.set(f"接続中: {path}")
        except (OSError, ValueError) as error:
            messagebox.showerror("データベースを開けません", str(error))
            return
        if self.database is not None:
            self.database.close()
        self.database = database
        self.db_path_var.set(str(path))
        self._set_quiz_active(False)

    def import_json(self) -> None:
        if self.database is None:
            messagebox.showwarning("未接続", "先にデータベースを開いてください")
            return
        path = filedialog.askopenfilename(initialdir=DEFAULT_SAMPLE_JSON_PATH.parent, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            count = self.database.import_json(Path(path))
        except (OSError, ValueError) as error:
            messagebox.showerror("取り込みに失敗しました", str(error))
            return
        messagebox.showinfo("取り込み完了", f"{count}件取り込みました。")

    def start_quiz(self) -> None:
        if self.database is None:
            messagebox.showwarning("未接続", "先にデータベースを開いてください")
            return
        count_text = self.count_var.get().strip()
        if not count_text.isdigit() or not 1 <= int(count_text) <= 100:
            messagebox.showwarning("入力エラー", "出題数は1〜100の整数で入力してください")
            return
        self.question_type = self._selected_type()
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
            self.question = self.database.create_question(self.question_type, self.show_hint)
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
            ttk.Radiobutton(self.choices_frame, text=choice, value=index, variable=self.answer_var).grid(row=index, column=0, sticky="w", pady=2)
        self.answer_button.configure(text="回答する", command=self.submit_answer, state="normal")

    def submit_answer(self) -> None:
        selected_index = self.answer_var.get()
        if selected_index < 0:
            messagebox.showwarning("未選択", "回答を選択してください")
            return
        for widget in self.choices_frame.winfo_children():
            widget.configure(state="disabled")
        selected_answer = self.question.choices[selected_index]
        self.answered_count += 1
        if self.database.record_answer(self.question, selected_answer):
            self.correct_count += 1
            self.feedback_var.set("正解!")
            self.feedback_label.configure(foreground="green")
        else:
            self.feedback_var.set(f"不正解。正解は {self.question.answer} です。")
            self.feedback_label.configure(foreground="red")
        is_last = self.question_index >= self.question_count
        self.answer_button.configure(text="結果を見る" if is_last else "次の問題へ", command=self.next_question)

    def finish_quiz(self) -> None:
        self._set_quiz_active(False)
        self._set_setup_enabled(True)
        if self.answered_count and self.question is not None:
            total, correct, accuracy = self.database.recent_accuracy(self.question.question_type)
            self.score_var.set(
                f"今回: {self.correct_count}/{self.answered_count} ({self.correct_count / self.answered_count * 100:.1f}%)\n"
                f"{QUESTION_LABELS[self.question.question_type]} 直近{total}問: {accuracy:.1f}% ({correct}/{total})"
            )

    def on_close(self) -> None:
        if self.database is not None:
            self.database.close()
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLiteベースの英単語クイズ (GUI)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    QuizApp(args.db).mainloop()


if __name__ == "__main__":
    main()

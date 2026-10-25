"""「JSONと照合」の確認画面。消える・増えるものの一覧と、消える単語の回答履歴をどの単語に引き継ぐかの選択を出す。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

NO_TRANSFER = "(引き継がない)"


class TransferRow:
    """回答履歴のある消える単語1つ。default は最初に選んでおく引き継ぎ先 (綴りを直したとみなした単語)"""

    def __init__(self, language: str, word: str, history: int, default: str | None) -> None:
        self.language = language
        self.word = word
        self.history = history
        self.default = default


class SyncDialog(tk.Toplevel):
    """result は {(言語, 消える単語): 引き継ぐ単語}。キャンセルしたら None"""

    def __init__(
        self, master: tk.Misc, summary: str, rows: list[TransferRow], candidates: dict[str, list[tuple[str, str]]],
    ) -> None:
        super().__init__(master)
        self.title("JSONと照合")
        self.geometry("820x640")
        self.transient(master)
        self.result: dict[tuple[str, str], str] | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        text_frame = ttk.Frame(self)
        text_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        text = tk.Text(text_frame, wrap="word", height=16)
        text.insert("1.0", summary)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        # 引き継ぎ先の候補 (言語ごとに、意味が増える単語) の表示名 → 単語
        self.rows = rows
        self.choices: list[tuple[TransferRow, tk.StringVar, dict[str, str]]] = []
        if rows:
            transfer_frame = ttk.LabelFrame(self, text="消える単語の回答履歴の引き継ぎ先 (意味が増える単語から選ぶ)")
            transfer_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
            transfer_frame.columnconfigure(0, weight=1)
            canvas = tk.Canvas(transfer_frame, height=min(36 * len(rows), 220), highlightthickness=0)
            canvas.grid(row=0, column=0, sticky="nsew")
            row_scrollbar = ttk.Scrollbar(transfer_frame, orient="vertical", command=canvas.yview)
            row_scrollbar.grid(row=0, column=1, sticky="ns")
            canvas.configure(yscrollcommand=row_scrollbar.set)
            inner = ttk.Frame(canvas)
            inner.columnconfigure(1, weight=1)
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
            for index, row in enumerate(rows):
                labels = {label: word for label, word in candidates.get(row.language, [])}
                default = next((label for label, word in labels.items() if word == row.default), NO_TRANSFER)
                variable = tk.StringVar(value=default)
                ttk.Label(inner, text=f"{row.word} [{row.language}] — 回答履歴 {row.history}件 →").grid(
                    row=index, column=0, sticky="w", padx=(5, 10), pady=3
                )
                ttk.Combobox(inner, textvariable=variable, values=[NO_TRANSFER, *labels], state="readonly").grid(
                    row=index, column=1, sticky="ew", padx=(0, 5), pady=3
                )
                self.choices.append((row, variable, labels))

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="e", padx=10, pady=(5, 10))
        ttk.Button(buttons, text="反映", command=self._apply).grid(row=0, column=0, padx=5)
        ttk.Button(buttons, text="キャンセル", command=self.destroy).grid(row=0, column=1, padx=5)
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()
        self.focus_set()

    def _apply(self) -> None:
        self.result = {
            (row.language, row.word): labels[variable.get()]
            for row, variable, labels in self.choices
            if variable.get() in labels
        }
        self.destroy()

    @classmethod
    def ask(
        cls, master: tk.Misc, summary: str, rows: list[TransferRow], candidates: dict[str, list[tuple[str, str]]],
    ) -> dict[tuple[str, str], str] | None:
        dialog = cls(master, summary, rows, candidates)
        master.wait_window(dialog)
        return dialog.result

"""単語一覧 (単語帳の表)。単語ごとの正答率を表にし、列見出しのクリックで並べ替える。
正答率は意味・性・複数形・転写を別々に集計し (database.history_pool)、それぞれ列にする。"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from datetime import datetime, timezone
from tkinter import ttk
from typing import NamedTuple

from database import MEANING_POOL, SEPARATE_POOL_TYPES, LexiconDatabase, WordStats
from languages import get_language


ALL_TYPES_LABEL = "すべて"
POOL_LABELS = {MEANING_POOL: "意味", "gender": "性", "plural": "複数形", "valency": "格支配", "conjugation": "活用", "romanization": "転写"}
# 正答率がこれ未満の行を色分けする
LOW_ACCURACY = 50.0
MIDDLE_ACCURACY = 80.0


class Column(NamedTuple):
    id: str
    heading: str
    width: int
    numeric: bool
    # 並べ替えのキー。None (未回答の正答率など) は昇順・降順どちらでも末尾に置く
    key: Callable[[WordStats], object]
    text: Callable[[WordStats], object]
    # 正答率の列が属する集計単位。行の色分けに使う
    pool: str | None = None


def local_time(timestamp: str | None) -> str:
    # 回答日時は SQLite の CURRENT_TIMESTAMP (UTC) で保存されている
    if not timestamp:
        return ""
    return datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def pool_columns(pool: str) -> list[Column]:
    label = POOL_LABELS[pool]

    def accuracy_text(stats: WordStats) -> str:
        accuracy = stats.pool(pool).accuracy
        return "" if accuracy is None else f"{accuracy:.0f}%"

    def count_text(stats: WordStats) -> str:
        pool_stats = stats.pool(pool)
        return f"{pool_stats.correct}/{pool_stats.answered}" if pool_stats.answered else ""

    return [
        Column(f"{pool}_accuracy", f"{label} 正答率", 85, True, lambda stats: stats.pool(pool).accuracy, accuracy_text, pool),
        # 「正解/回答」の列は回答数で並べる
        Column(f"{pool}_answered", f"{label} 正解/回答", 85, True, lambda stats: stats.pool(pool).answered or None, count_text, pool),
    ]


def build_columns(pools: list[str]) -> list[Column]:
    return [
        Column("word", "単語", 140, False, lambda stats: stats.word.casefold(), lambda stats: stats.word),
        Column("parts_of_speech", "品詞", 70, False, lambda stats: stats.parts_of_speech, lambda stats: stats.parts_of_speech),
        Column("meanings", "意味", 280, False, lambda stats: stats.meanings, lambda stats: stats.meanings),
        *(column for pool in pools for column in pool_columns(pool)),
        Column("last_answered", "最終回答", 120, False, lambda stats: stats.last_answered, lambda stats: local_time(stats.last_answered)),
        Column("sources", "出典", 200, False, lambda stats: stats.sources, lambda stats: stats.sources),
    ]


class WordList(tk.Toplevel):
    def __init__(
        self, master: tk.Misc, database: LexiconDatabase, languages: list[str], language: str,
        question_types: list[tuple[str, str]], on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("LexiconEngine 単語一覧")
        self.geometry("1250x650")
        self.database = database
        self.on_close = on_close
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.question_types = question_types
        # 意味の集計に使う問題形式の表示名 → ID。言語で表示名 (英→日 / 独→日) が変わるので、言語を変えたら作り直す
        self.type_values: dict[str, str | None] = {}
        self.pools: list[str] = []
        self.columns: list[Column] = []
        self.rows: list[WordStats] = []
        # 最初は意味の正答率の低い順 (苦手な単語が上)
        self.sort_column = f"{MEANING_POOL}_accuracy"
        self.sort_descending = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        ttk.Label(toolbar, text="言語").grid(row=0, column=0, padx=(0, 5))
        self.language_var = tk.StringVar(value=language)
        language_box = ttk.Combobox(toolbar, textvariable=self.language_var, values=languages, state="readonly", width=12)
        language_box.grid(row=0, column=1, padx=(0, 15))
        language_box.bind("<<ComboboxSelected>>", lambda event: self._on_language_changed())
        ttk.Label(toolbar, text="意味の問題形式").grid(row=0, column=2, padx=(0, 5))
        self.type_var = tk.StringVar(value=ALL_TYPES_LABEL)
        self.type_box = ttk.Combobox(toolbar, textvariable=self.type_var, state="readonly", width=14)
        self.type_box.grid(row=0, column=3, padx=(0, 15))
        self.type_box.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        ttk.Label(toolbar, text="検索").grid(row=0, column=4, padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._fill())
        ttk.Entry(toolbar, textvariable=self.search_var, width=20).grid(row=0, column=5, padx=(0, 15))
        self.answered_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="回答済みのみ", variable=self.answered_only_var, command=self._fill).grid(row=0, column=6, padx=(0, 15))
        ttk.Button(toolbar, text="更新", command=self.refresh).grid(row=0, column=7)
        # 活用クイズがある言語だけ、人称 × 時制の正答率の表を開けるようにする
        self.conjugation_button = ttk.Button(toolbar, text="活用の正答率", command=self.show_conjugation_stats)
        self.conjugation_button.grid(row=0, column=8, padx=(15, 0))

        table = ttk.Frame(self)
        table.grid(row=1, column=0, sticky="nsew", padx=10)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, show="headings", selectmode="browse")
        self.tree.tag_configure("low", foreground="#c0392b")
        self.tree.tag_configure("middle", foreground="#b9770e")
        self.tree.tag_configure("unanswered", foreground="gray")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, foreground="gray").grid(row=2, column=0, sticky="w", padx=10, pady=(5, 10))
        self._on_language_changed()

    def close(self) -> None:
        self.destroy()
        if self.on_close:
            self.on_close()

    def _on_language_changed(self) -> None:
        # 性・複数形・転写の列は、その問題形式に対応した言語のときだけ出す
        language_spec = get_language(self.language_var.get())
        self.pools = [MEANING_POOL, *(pool for pool in SEPARATE_POOL_TYPES if language_spec.supports(pool))]
        if "conjugation" in self.pools:
            self.conjugation_button.grid()
        else:
            self.conjugation_button.grid_remove()
        self.columns = build_columns(self.pools)
        self.tree.configure(columns=[column.id for column in self.columns])
        for column in self.columns:
            self.tree.heading(column.id, command=lambda column_id=column.id: self.sort_by(column_id))
            self.tree.column(column.id, width=column.width, anchor="e" if column.numeric else "w", stretch=column.id in ("meanings", "sources"))
        if self.sort_column not in {column.id for column in self.columns}:
            self.sort_column, self.sort_descending = f"{MEANING_POOL}_accuracy", False

        self.type_values = {ALL_TYPES_LABEL: None}
        for label, value in self.question_types:
            if value not in SEPARATE_POOL_TYPES and language_spec.supports(value):
                self.type_values[label.format(lang=language_spec.label)] = value
        self.type_box.configure(values=list(self.type_values))
        if self.type_var.get() not in self.type_values:
            self.type_var.set(ALL_TYPES_LABEL)
        self.refresh()

    def show_conjugation_stats(self) -> None:
        """人称 × 時制の正答率の表。苦手な人称・時制は動詞を問わず出題が増える (database._conjugation_candidates)"""
        stats = self.database.conjugation_stats(self.language_var.get())
        window = tk.Toplevel(self)
        window.title(f"活用の正答率 ({self.language_var.get()})")
        tenses = ("現在", "過去", "現在完了")
        tree = ttk.Treeview(window, columns=("person", *tenses), show="headings", height=6)
        tree.heading("person", text="人称")
        tree.column("person", width=90)
        for tense in tenses:
            tree.heading(tense, text=tense)
            tree.column(tense, width=120, anchor="e")
        tree.tag_configure("low", foreground="#c0392b")
        persons = get_language(self.language_var.get()).conjugation_pronouns
        for person in persons:
            cells = [stats.get((person, tense)) for tense in tenses]
            texts = [f"{cell.accuracy:.0f}% ({cell.correct}/{cell.answered})" if cell and cell.answered else "" for cell in cells]
            worst = min((cell.accuracy for cell in cells if cell and cell.answered), default=None)
            tree.insert("", "end", values=(person, *texts), tags=("low",) if worst is not None and worst < LOW_ACCURACY else ())
        tree.grid(row=0, column=0, padx=10, pady=10)
        ttk.Label(
            window, foreground="gray",
            text="活用クイズは 動詞の誤り率 × 人称の誤り率 × 時制の誤り率 に比例して出題します (セットの開始時に更新)",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

    def refresh(self) -> None:
        self.rows = self.database.word_stats(self.language_var.get(), self.type_values.get(self.type_var.get()))
        self._fill()

    def sort_by(self, column_id: str) -> None:
        # 同じ列をもう一度押したら逆順にする
        if column_id == self.sort_column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column, self.sort_descending = column_id, False
        self._fill()

    def _sort_column(self) -> Column:
        return next(column for column in self.columns if column.id == self.sort_column)

    def _visible_rows(self) -> list[WordStats]:
        query = self.search_var.get().strip().casefold()
        rows = [
            stats for stats in self.rows
            if (not self.answered_only_var.get() or any(stats.pool(pool).answered for pool in self.pools))
            and (not query or query in stats.word.casefold() or query in stats.meanings.casefold())
        ]
        column = self._sort_column()
        # 値のない行 (未回答の正答率など) は並び順に関係なく末尾。同じ値の中は、並べ替えている集計単位
        # (なければ意味) の回答数の多い順 (0/5 を 0/1 より上に)、さらに単語順。sort は安定なので逆順でも保たれる
        tie_pool = column.pool or MEANING_POOL
        rows.sort(key=lambda stats: stats.word.casefold())
        rows.sort(key=lambda stats: stats.pool(tie_pool).answered, reverse=True)
        valued = [stats for stats in rows if column.key(stats) is not None]
        empty = [stats for stats in rows if column.key(stats) is None]
        valued.sort(key=column.key, reverse=self.sort_descending)
        return valued + empty

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        rows = self._visible_rows()
        # 色分けは並べ替えている集計単位の正答率で行う (性で並べ替えたら性の正答率)
        color_pool = self._sort_column().pool or MEANING_POOL
        for stats in rows:
            accuracy = stats.pool(color_pool).accuracy
            tag = "unanswered" if accuracy is None else "low" if accuracy < LOW_ACCURACY else "middle" if accuracy < MIDDLE_ACCURACY else ""
            self.tree.insert("", "end", values=[column.text(stats) for column in self.columns], tags=(tag,) if tag else ())
        for column in self.columns:
            arrow = (" ▼" if self.sort_descending else " ▲") if column.id == self.sort_column else ""
            self.tree.heading(column.id, text=column.heading + arrow)
        summaries = []
        for pool in self.pools:
            answered = sum(stats.pool(pool).answered for stats in self.rows)
            correct = sum(stats.pool(pool).correct for stats in self.rows)
            if answered:
                summaries.append(f"{POOL_LABELS[pool]} {correct / answered * 100:.1f}% ({correct}/{answered})")
        self.status_var.set(
            f"表示 {len(rows)}語 / 全{len(self.rows)}語  正答率: {'、'.join(summaries) or 'まだ回答がありません'}"
            f" — 列見出しをクリックで並べ替え。{POOL_LABELS[color_pool]}の正答率 {LOW_ACCURACY:.0f}% 未満は赤、{MIDDLE_ACCURACY:.0f}% 未満は橙"
        )

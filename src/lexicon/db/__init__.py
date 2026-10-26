"""学習データの DB (SQLite)。役割ごとのファイルに分けた機能を LexiconDatabase にまとめる。

    schema.py    テーブルの作成と移行
    importer.py  教材JSONの取り込み
    sync.py      JSONとの照合と端末との同期
    quiz.py      出題と採点
    stats.py     回答履歴の集計 (単語一覧など)
    scores.py    誤答スコア・未習得スコア
    scope.py     出題範囲
    known.py     既習にした単語
    text.py      意味・見出し語の文字列の扱い
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lexicon.db.importer import ImportMixin, normalize_derivatives
from lexicon.db.known import KnownMixin
from lexicon.db.quiz import TEST_QUESTION_TYPES, Question, QuizMixin
from lexicon.db.schema import SchemaMixin
from lexicon.db.scope import QuizScope, with_folders
from lexicon.db.scores import MEANING_POOL, SCORE_MODES, SEPARATE_POOL_TYPES, PoolStats, history_pool
from lexicon.db.stats import StatsMixin, WordStats
from lexicon.db.sync import SyncMixin, SyncReport

__all__ = [
    "LexiconDatabase", "MEANING_POOL", "SCORE_MODES", "SEPARATE_POOL_TYPES", "PoolStats", "Question", "QuizScope", "SyncReport", "TEST_QUESTION_TYPES",
    "WordStats", "history_pool", "normalize_derivatives", "with_folders",
]


class LexiconDatabase(SchemaMixin, ImportMixin, SyncMixin, QuizMixin, StatsMixin, KnownMixin):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        # 1セットの間だけ固定する出題の重み。begin_quiz_set() で作り直す。None なら毎回計算する
        self._set_weights: dict | None = None
        # 出題の重みに使うスコア (SCORE_MODES)
        self.score_mode = "unlearned"
        # 直前の record_answer で、間違えた単語の既習の印を外したか
        self.unmarked_known = False

    def close(self) -> None:
        self.connection.close()

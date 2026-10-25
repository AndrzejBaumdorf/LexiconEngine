"""出題の重みと単語一覧に使うスコア (誤答スコア・未習得スコア) と、回答履歴の集計単位。"""
from __future__ import annotations

from typing import NamedTuple


# 出題の重みに使うスコア: 直近 RECENT_HISTORY_LIMIT 回の回答の誤答 (1/0) を、
# 最新を 1、1つ前を RECENT_HISTORY_DECAY、2つ前を RECENT_HISTORY_DECAY² … とした加重平均にする。
# 誤答スコアは答えた回だけで平均し、未習得スコアは RECENT_HISTORY_LIMIT 回に満たない分 (まだ答えていない回) を誤答として数える
RECENT_HISTORY_LIMIT = 25
RECENT_HISTORY_DECAY = 0.841
# 出題モード → 使うスコア。begin_quiz_set() で選ぶ
SCORE_MODES = ("incorrect", "unlearned")


# 意味を問う問題とは別に正答率を集計する問題形式 (語形・綴りの知識を問うもの)。それ以外の形式は「意味」にまとめる
SEPARATE_POOL_TYPES = ("gender", "plural", "valency", "conjugation", "romanization")
MEANING_POOL = "meaning"


def history_pool(question_type: str) -> str:
    return question_type if question_type in SEPARATE_POOL_TYPES else MEANING_POOL


def incorrect_score(results: list[bool]) -> float | None:
    """誤答スコア (0〜1)。results は正誤を新しい順に並べたもの (直近 RECENT_HISTORY_LIMIT 回まで使う)。
    Σ(誤答 × DECAYⁿ) / ΣDECAYⁿ (n は最新が 0、答えた回の分だけ)。回答がなければ None"""
    results = results[:RECENT_HISTORY_LIMIT]
    if not results:
        return None
    weights = [RECENT_HISTORY_DECAY ** n for n in range(len(results))]
    return sum(weight for weight, correct in zip(weights, results) if not correct) / sum(weights)


def unlearned_score(results: list[bool]) -> float:
    """未習得スコア (0〜1)。results は正誤を新しい順に並べたもの (直近 RECENT_HISTORY_LIMIT 回まで使う)。
    Σ(誤答 × DECAYⁿ) / ΣDECAYⁿ (n は最新が 0、n = RECENT_HISTORY_LIMIT - 1 まで)。
    回答が足りない古い側の回は誤答とするので、未回答の単語は 1、正解し続けて RECENT_HISTORY_LIMIT 回で 0 になる"""
    results = results[:RECENT_HISTORY_LIMIT]
    weights = [RECENT_HISTORY_DECAY ** n for n in range(RECENT_HISTORY_LIMIT)]
    learned = sum(weight for weight, correct in zip(weights, results) if correct)
    return 1.0 - learned / sum(weights)


class PoolStats(NamedTuple):
    answered: int = 0
    correct: int = 0
    # 誤答スコア (incorrect_score)。回答がなければ None
    incorrect: float | None = None
    # 未習得スコア (unlearned_score)。回答がなければ 1
    unlearned: float = 1.0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.answered * 100 if self.answered else None

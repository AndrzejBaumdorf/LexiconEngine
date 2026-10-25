"""活用クイズの1問分の形 (言語共通)。行 (ドイツ語は人称、韓国語は文体) × 列 (時制など) の1マス。"""
from __future__ import annotations

from typing import NamedTuple


class ConjugationCell(NamedTuple):
    person: str
    tense: str
    # 正解として認める形。先頭を表示に使う (fährst ab)
    answers: list[str]

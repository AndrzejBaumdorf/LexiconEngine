"""問題形式と出題モードの一覧。デスクトップ版 (main.py) とブラウザ版 (web_bridge.py) で共通に使う。"""
from __future__ import annotations

from database import MEANING_POOL
from languages import get_language

SKIPPED_ANSWER = "(スキップ)"
MAX_QUESTION_COUNT = 1000
# 出題モードの表示名 → database.SCORE_MODES。先頭が初期値
SCORE_MODE_LABELS = {
    "未習得スコア (未回答・回答の少ない単語も優先)": "unlearned",
    "誤答スコア (最近間違えた単語だけを優先。見直し用)": "incorrect",
}
# 問題形式のIDは回答履歴に保存されるため変えない。表示名の {lang} は選んだ言語の略称 (英・独など) になる
QUESTION_TYPES = [
    ("{lang}→日", "english_to_japanese"),
    ("日→{lang}", "japanese_to_english"),
    ("例文穴埋め", "cloze"),
    ("類義語・対義語", "relation"),
    ("派生語", "derivative"),
    ("ラテン文字転写", "romanization"),
    ("性 (冠詞)", "gender"),
    ("複数形", "plural"),
    ("格支配", "valency"),
    ("活用", "conjugation"),
]

# 回答履歴の集計単位 (database.history_pool) の表示名
POOL_LABELS = {MEANING_POOL: "意味", "gender": "性", "plural": "複数形", "valency": "格支配", "conjugation": "活用", "romanization": "転写"}


def available_question_types(language: str) -> list[str]:
    # 転写・性・複数形などの問題形式は、対応した言語 (韓国語・ドイツ語など) のときだけ選べる
    language_spec = get_language(language)
    return [value for _, value in QUESTION_TYPES if language_spec.supports(value)]


def question_label(question_type: str, language: str) -> str:
    template = next(label for label, value in QUESTION_TYPES if value == question_type)
    return template.format(lang=get_language(language).label if language else "外")

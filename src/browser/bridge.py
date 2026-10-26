"""ブラウザ版 (web/) から Pyodide 経由で呼ぶ窓口。出題・採点・単語一覧・同期の材料を JSON で返す。
DB はブラウザ内のファイル (Pyodide の仮想ファイルシステム) に置き、保存は JavaScript 側が IndexedDB に行う。"""
from __future__ import annotations

import json
from pathlib import Path

from lexicon.db import MEANING_POOL, SEPARATE_POOL_TYPES, TEST_QUESTION_TYPES, LexiconDatabase, Question, QuizScope
from lexicon.languages import get_language
from lexicon.quiz_types import (
    MAX_QUESTION_COUNT, MAX_TEST_ROUNDS, POOL_LABELS, QUESTION_TYPES, QUIZ_MODE_LABELS, SKIPPED_ANSWER, TEST_MODE,
    available_question_types, default_parts_of_speech, question_label,
)


class Session:
    def __init__(self) -> None:
        self.database: LexiconDatabase | None = None
        self.question: Question | None = None
        self.question_type = ""
        self.language = ""
        self.scope = QuizScope()
        self.show_hint = False
        self.count = 0
        self.index = 0
        self.answered = 0
        self.correct = 0
        # テストモードの出題順 (単語ID) と1周の問題数。通常のモードでは None
        self.test_order: list[int] | None = None
        self.round_size = 0


session = Session()


def _db() -> LexiconDatabase:
    if session.database is None:
        raise RuntimeError("DBが開かれていません")
    return session.database


def open_database(path: str) -> str:
    if session.database is not None:
        session.database.close()
    session.database = LexiconDatabase(Path(path))
    session.database.initialize()
    return json.dumps({"words": _db().connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]})


def setup_options(language: str = "") -> str:
    """開始画面の選択肢。language が空なら単語の多い言語"""
    languages = _db().languages()
    if language not in languages:
        language = languages[0] if languages else ""
    sources, difficulties, parts_of_speech, files = _db().scope_options(language) if language else ([], [], [], [])
    return json.dumps({
        "languages": languages,
        "language": language,
        "scope": {"sources": sources, "difficulties": difficulties, "parts_of_speech": parts_of_speech, "files": files},
        "question_types": [
            {"value": value, "label": question_label(value, language)} for value in available_question_types(language)
        ] if language else [],
        "score_modes": [{"label": label, "value": value} for label, value in QUIZ_MODE_LABELS.items()],
        "test_mode": TEST_MODE,
        "test_question_types": list(TEST_QUESTION_TYPES),
        "max_count": MAX_QUESTION_COUNT,
        "max_rounds": MAX_TEST_ROUNDS,
        # 品詞を自分で選ぶまでの初期値 (問題形式ごと)
        "default_parts_of_speech": {value: list(default_parts_of_speech(value)) for _, value in QUESTION_TYPES},
    }, ensure_ascii=False)


def _scope(settings: dict) -> QuizScope:
    scope = settings.get("scope", {})
    return QuizScope(
        settings["language"], tuple(scope.get("sources", ())), tuple(scope.get("difficulties", ())),
        tuple(scope.get("parts_of_speech", ())), tuple(scope.get("files", ())),
    )


def mastery(settings_json: str) -> str:
    """選んだ問題形式・出題範囲の習得率 (1 - Σ未習得スコア / 単語数)"""
    settings = json.loads(settings_json)
    rate, words = _db().mastery(settings["question_type"], _scope(settings))
    return json.dumps({"rate": rate, "words": words})


def start_quiz(settings_json: str) -> str:
    """settings の score_mode が "test" ならテストモード (count は周回数)"""
    settings = json.loads(settings_json)
    count = int(settings["count"])
    test_mode = settings.get("score_mode") == TEST_MODE
    limit = MAX_TEST_ROUNDS if test_mode else MAX_QUESTION_COUNT
    if not 1 <= count <= limit:
        raise ValueError(f"{'周回数' if test_mode else '出題数'}は1〜{limit}にしてください")
    session.language = settings["language"]
    session.question_type = settings["question_type"]
    session.scope = _scope(settings)
    session.show_hint = session.question_type == "cloze" and bool(settings.get("hint"))
    session.test_order, session.round_size = None, 0
    if test_mode:
        session.test_order = _db().test_word_ids(session.question_type, session.scope, count)
        session.round_size = len(session.test_order) // count
        count = len(session.test_order)
    session.count, session.index, session.answered, session.correct = count, 0, 0, 0
    _db().begin_quiz_set("unlearned" if test_mode else settings.get("score_mode", "unlearned"))
    return next_question()


def next_question() -> str:
    session.index += 1
    if session.index > session.count:
        return finish_quiz()
    try:
        word_id = session.test_order[session.index - 1] if session.test_order is not None else None
        session.question = _db().create_question(session.question_type, session.show_hint, session.scope, word_id)
    except ValueError as error:
        result = json.loads(finish_quiz())
        result["error"] = str(error)
        return json.dumps(result, ensure_ascii=False)
    question = session.question
    return json.dumps({
        "done": False,
        "index": session.index,
        "count": session.count,
        "prompt": question.prompt,
        "choices": question.choices,
        "typed": question.is_typed,
        "special_characters": get_language(session.language).special_characters if question.is_typed else "",
        "is_last": session.index >= session.count,
        # テストモードなら何周目か
        "round": (session.index - 1) // session.round_size + 1 if session.test_order is not None else None,
    }, ensure_ascii=False)


def answer(selected: str) -> str:
    """回答を記録して採点する。selected は選んだ選択肢の文字列か入力した文字列 (スキップは空文字)"""
    question = session.question
    if question is None:
        raise RuntimeError("出題中の問題がありません")
    skipped = not selected.strip()
    selected = SKIPPED_ANSWER if skipped else selected.strip()
    correct = _db().record_answer(question, selected)
    session.question = None
    session.answered += 1
    shown = question.answer_detail or question.answer
    if correct:
        session.correct += 1
        # 入力式で大文字・小文字や die の有無だけ違うときは、正しい書き方も見せる
        exact = not question.is_typed or selected == question.answer
        feedback = "正解!" if exact else f"正解! ({shown})"
    else:
        prefix = "スキップ" if skipped else "不正解"
        if question.is_typed and not skipped:
            prefix += f" (入力: {selected})"
        feedback = f"{prefix}。正解は {shown} です。" + ("\n(既習の印を外しました)" if _db().unmarked_known else "")
    return json.dumps({"correct": correct, "feedback": feedback, "answer": question.answer}, ensure_ascii=False)


def finish_quiz() -> str:
    session.question = None
    summary = ""
    if session.answered:
        total, correct, accuracy = _db().recent_accuracy(session.question_type, language=session.language)
        summary = (
            f"今回: {session.correct}/{session.answered} ({session.correct / session.answered * 100:.1f}%)\n"
            f"{session.language} {question_label(session.question_type, session.language)} 直近{total}問: {accuracy:.1f}% ({correct}/{total})"
        )
    return json.dumps({"done": True, "summary": summary}, ensure_ascii=False)


def word_list(language: str, meaning_question_type: str = "") -> str:
    """単語一覧。集計単位ごとの誤答スコアと回答数"""
    spec = get_language(language)
    pools = [MEANING_POOL, *(pool for pool in SEPARATE_POOL_TYPES if spec.supports(pool))]
    rows = []
    for stats in _db().word_stats(language, meaning_question_type or None):
        rows.append({
            "word": stats.word,
            "parts_of_speech": stats.parts_of_speech,
            "meanings": stats.meanings,
            "sources": stats.sources,
            "last_answered": stats.last_answered,
            "known": stats.known,
            "pools": {pool: {"score": stats.pool(pool).incorrect, "answered": stats.pool(pool).answered, "correct": stats.pool(pool).correct} for pool in pools},
        })
    meaning_types = [
        {"value": value, "label": label.format(lang=spec.label)}
        for label, value in QUESTION_TYPES if value not in SEPARATE_POOL_TYPES and spec.supports(value)
    ]
    return json.dumps({
        "pools": [{"value": pool, "label": POOL_LABELS[pool]} for pool in pools],
        "meaning_types": meaning_types,
        "rows": rows,
    }, ensure_ascii=False)


def set_known(language: str, word: str, known: bool) -> None:
    _db().set_known(language, word, bool(known))


# --- 同期 (通信は JavaScript 側が行う) ---

def history_rows() -> str:
    return json.dumps(_db().history_rows(), ensure_ascii=False)


def known_rows() -> str:
    return json.dumps(_db().known_rows(), ensure_ascii=False)


def merge_known(rows_json: str) -> int:
    return _db().merge_known(json.loads(rows_json))


def apply_sync(content_path: str, rows_json: str) -> str:
    """Mac から受け取った単語データ (export_content のDB) で置き換えてから、Mac の回答履歴を足す。
    この端末の回答履歴は先に Mac へ送ってある"""
    if content_path:
        _db().replace_content_from(Path(content_path))
    added, skipped = _db().merge_history(json.loads(rows_json))
    return json.dumps({"added": added, "skipped": skipped})


def content_check(content_path: str) -> str:
    """Mac から受け取った単語データの単語数と、この端末の単語数。極端に少なければ置き換えない (Mac 側の事故の巻き込みを防ぐ)"""
    import sqlite3

    received = sqlite3.connect(content_path)
    try:
        content_words = received.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    finally:
        received.close()
    local_words = _db().connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    return json.dumps({"content_words": content_words, "local_words": local_words})

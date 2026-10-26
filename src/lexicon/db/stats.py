"""回答履歴の集計 (単語一覧・活用の集計表・直近の正答率)。"""
from __future__ import annotations

from typing import NamedTuple

from lexicon.languages import mark_separable
from lexicon.db.scores import MEANING_POOL, PoolStats, RECENT_HISTORY_LIMIT, SEPARATE_POOL_TYPES, incorrect_score, unlearned_score
from lexicon.db.text import HAS_REAL_MEANING, hanja_note, valency_note


class WordStats(NamedTuple):
    """単語一覧の1行。回答履歴は単語ごとに記録されるので、意味・品詞はまとめて表示する"""
    word_id: int
    word: str
    parts_of_speech: str
    meanings: str
    sources: str
    # 集計の単位 (意味・性・複数形・転写) → 回答数と正答数
    pools: dict[str, PoolStats]
    last_answered: str | None
    # 既習にした単語か (出題の基礎の重みが下がる)
    known: bool = False

    def pool(self, name: str) -> PoolStats:
        return self.pools.get(name, PoolStats())


class StatsMixin:
    """LexiconDatabase の回答履歴の集計"""

    def _recent_results(self, where: str, params: tuple, pool: str = "''") -> dict[tuple[int, str], list[bool]]:
        """(単語, 集計単位) ごとの直近 RECENT_HISTORY_LIMIT 回の正誤を新しい順に返す"""
        results: dict[tuple[int, str], list[bool]] = {}
        for row in self.connection.execute(
            f"""
            SELECT word_id, pool, is_correct FROM (
                SELECT h.word_id, {pool} AS pool, h.is_correct,
                    ROW_NUMBER() OVER (PARTITION BY h.word_id, {pool} ORDER BY h.id DESC) AS position
                FROM answer_history h JOIN words w ON w.id = h.word_id WHERE {where}
            ) WHERE position <= ? ORDER BY word_id, pool, position
            """,
            (*params, RECENT_HISTORY_LIMIT),
        ):
            results.setdefault((row["word_id"], row["pool"]), []).append(bool(row["is_correct"]))
        return results

    def conjugation_stats(self, language: str) -> dict[tuple[str, str], PoolStats]:
        """活用クイズの (人称, 時制) ごとの回答数・正答数"""
        rows = self.connection.execute(
            """
            SELECT json_extract(h.variant, '$.person') AS person, json_extract(h.variant, '$.tense') AS tense,
                COUNT(*) AS answered, SUM(h.is_correct) AS correct
            FROM answer_history h JOIN words w ON w.id = h.word_id
            WHERE h.question_type = 'conjugation' AND h.variant IS NOT NULL AND w.language = ?
            GROUP BY person, tense
            """,
            (language,),
        ).fetchall()
        return {(row["person"], row["tense"]): PoolStats(row["answered"], row["correct"]) for row in rows}

    def word_stats(self, language: str, meaning_question_type: str | None = None) -> list[WordStats]:
        """その言語の単語ごとの回答数・正答数・誤答スコア・未習得スコアを、意味・性・複数形・転写の集計単位ごとに返す。
        meaning_question_type を指定すると、意味の集計はその問題形式 (穴埋めだけなど) の回答だけを数える。
        類義語などとして名前だけ登録された語 (意味がない語) は含めない"""
        separate = ", ".join(f"'{question_type}'" for question_type in SEPARATE_POOL_TYPES)
        pool = f"CASE WHEN h.question_type IN ({separate}) THEN h.question_type ELSE '{MEANING_POOL}' END"
        where = f"w.language = ? AND (h.question_type IN ({separate}) OR ? IS NULL OR h.question_type = ?)"
        params = (language, meaning_question_type, meaning_question_type)
        recent = self._recent_results(where, params, pool)
        history: dict[int, dict[str, PoolStats]] = {}
        last_answered: dict[int, str] = {}
        for row in self.connection.execute(
            f"""
            SELECT h.word_id, {pool} AS pool,
                COUNT(*) AS answered, SUM(h.is_correct) AS correct, MAX(h.answered_at) AS last_answered
            FROM answer_history h JOIN words w ON w.id = h.word_id
            WHERE {where}
            GROUP BY h.word_id, pool
            """,
            params,
        ):
            results = recent.get((row["word_id"], row["pool"]), [])
            history.setdefault(row["word_id"], {})[row["pool"]] = PoolStats(
                row["answered"], row["correct"], incorrect_score(results), unlearned_score(results)
            )
            last_answered[row["word_id"]] = max(last_answered.get(row["word_id"], ""), row["last_answered"])
        rows = self.connection.execute(
            f"""
            SELECT w.id, w.word, w.separable_prefix,
                (SELECT GROUP_CONCAT(name, '・') FROM (
                    SELECT DISTINCT p.name FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id
                    WHERE p.word_id = w.id AND {HAS_REAL_MEANING})) AS parts_of_speech,
                EXISTS (SELECT 1 FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id
                    WHERE p.word_id = w.id AND {HAS_REAL_MEANING}) AS has_meaning,
                (SELECT GROUP_CONCAT(label, ', ') FROM (
                    SELECT DISTINCT s.name || CASE WHEN e.difficulty != '' THEN ' (' || e.difficulty || ')' ELSE '' END AS label
                    FROM entries e JOIN sources s ON s.id = e.source_id
                    WHERE e.word_id = w.id OR e.id IN (
                        SELECT em.entry_id FROM entry_meanings em JOIN meanings m ON m.id = em.meaning_id
                        JOIN parts_of_speech p ON p.id = m.part_of_speech_id WHERE p.word_id = w.id
                    ))) AS sources
            FROM words w
            WHERE w.language = ? AND has_meaning
            """,
            (language,),
        ).fetchall()
        known = self.known_words(language)
        meanings: dict[int, list[str]] = {}
        for row in self.connection.execute(
            f"""
            SELECT w.id AS word_id, m.meaning_ja, m.valency, m.forms FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE w.language = ? AND {HAS_REAL_MEANING} ORDER BY p.id, m.id
            """,
            (language,),
        ):
            meanings.setdefault(row["word_id"], []).append(
                row["meaning_ja"] + hanja_note(row["forms"]) + valency_note(language, row["valency"])
            )
        return [
            WordStats(
                row["id"], mark_separable(row["word"], row["separable_prefix"]), row["parts_of_speech"] or "", " ／ ".join(meanings[row["id"]]),
                row["sources"] or "", history.get(row["id"], {}), last_answered.get(row["id"]), row["word"] in known,
            )
            for row in rows
        ]

    def recent_accuracy(self, question_type: str | None = None, limit: int = 10, language: str | None = None) -> tuple[int, int, float]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) total, COALESCE(SUM(is_correct), 0) correct
            FROM (
                SELECT h.is_correct
                FROM answer_history h
                JOIN words w ON w.id = h.word_id
                WHERE (? IS NULL OR h.question_type = ?) AND (? IS NULL OR w.language = ?)
                ORDER BY h.id DESC
                LIMIT ?
            )
            """,
            (question_type, question_type, language, language, limit),
        ).fetchone()
        return row["total"], row["correct"], (row["correct"] / row["total"] * 100 if row["total"] else 0.0)

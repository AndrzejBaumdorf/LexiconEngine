"""既習にした単語。出題の基礎の重みを下げる (quiz.py の KNOWN_BASE_WEIGHT)。
単語データを作り直しても消えないよう、単語IDではなく (言語, 単語) で記録する。端末間の同期では、新しく変えたほうを残す。"""
from __future__ import annotations

from lexicon.languages import split_separable


class KnownMixin:
    """LexiconDatabase の既習の単語"""

    def set_known(self, language: str, word: str, known: bool) -> None:
        """word は分離動詞の印 (ab|fahren) が付いていてもよい"""
        word = split_separable(word)[0]
        self.connection.execute(
            """
            INSERT INTO known_words(language, word, known, updated_at) VALUES (?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
            ON CONFLICT(language, word) DO UPDATE SET known = excluded.known, updated_at = excluded.updated_at
            """,
            (language, word, int(known)),
        )
        self.connection.commit()

    def known_words(self, language: str) -> set[str]:
        return {row[0] for row in self.connection.execute("SELECT word FROM known_words WHERE language = ? AND known = 1", (language,))}

    def known_rows(self) -> list[dict]:
        """同期用。既習を外した記録 (known = 0) も、外したことを伝えるために含める"""
        return [dict(row) for row in self.connection.execute("SELECT language, word, known, updated_at FROM known_words")]

    def merge_known(self, rows: list[dict]) -> int:
        """ほかの端末の既習の記録を足す。同じ単語は updated_at の新しいほうを残す。変えた件数を返す"""
        changed = 0
        for row in rows:
            cursor = self.connection.execute(
                """
                INSERT INTO known_words(language, word, known, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(language, word) DO UPDATE SET known = excluded.known, updated_at = excluded.updated_at
                WHERE excluded.updated_at > known_words.updated_at
                """,
                (row["language"], row["word"], int(bool(row["known"])), row["updated_at"]),
            )
            changed += cursor.rowcount
        self.connection.commit()
        return changed

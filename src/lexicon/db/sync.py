"""DB と教材JSONの照合 (「JSONと照合」) と、iPad などの端末との回答履歴・単語データの同期。"""
from __future__ import annotations

import sqlite3
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

from lexicon.languages import split_separable


class SyncReport(NamedTuple):
    """JSONとの照合結果。DBをJSONから作り直したときに消える (removed) もの・増える (added) もの"""
    # 単語: (言語, 単語, 回答履歴の件数)。増える単語の履歴は 0
    removed_words: list[tuple[str, str, int]]
    added_words: list[tuple[str, str, int]]
    # 見出し: (言語, 単語, 出典, レベル)
    removed_entries: list[tuple[str, str, str, str]]
    added_entries: list[tuple[str, str, str, str]]
    # 意味: (言語, 単語, 品詞, 意味)
    removed_meanings: list[tuple[str, str, str, str]]
    added_meanings: list[tuple[str, str, str, str]]
    # 例文: (言語, 単語, 例文)
    removed_examples: list[tuple[str, str, str]]
    added_examples: list[tuple[str, str, str]]
    # 語形・格支配・訳・類義語など、上の一覧に出ない中身の違いがあるか
    other_changes: bool
    # 綴りを直したとみなす単語: (言語, 古い綴り, 新しい綴り, 回答履歴の件数)。回答履歴は新しい綴りに引き継ぐ
    renamed_words: list[tuple[str, str, str, int]]

    @property
    def changed(self) -> bool:
        return self.other_changes or any(self[:8]) or bool(self.renamed_words)


# JSONとの照合で、消える単語と増える単語の綴りがこれ以上似ていれば綴りの修正とみなす (anntotate → annotate)
RENAME_SIMILARITY = 0.85
# JSONとの照合で作り直す表 (回答履歴以外)
CONTENT_TABLES = (
    "sources", "words", "entries", "entry_files", "parts_of_speech", "meanings", "examples",
    "entry_meanings", "entry_examples", "word_relations", "meaning_relations", "derivations",
)


class SyncMixin:
    """LexiconDatabase のJSONとの照合と端末との同期"""

    def sync_with_json(
        self, json_files: list[tuple[Path, str]], apply: bool, transfers: dict[tuple[str, str], str] | None = None,
        extra_transfers: dict[tuple[str, str], str] | None = None,
    ) -> SyncReport:
        """DBの単語データを、JSONファイル (パス, ファイル名) をすべて新しいDBに取り込んだものと照合する。
        apply=True なら単語データをそれに置き換え、回答履歴は同じ単語 (見出し語×言語) に付け替える。
        消える単語の回答履歴は transfers {(言語, 消える単語): 引き継ぐ単語} の単語に引き継ぎ、それ以外は消える。
        transfers を省略すると、綴りを直したとみなした単語 (renamed_words) と extra_transfers (消える単語のものだけ) を引き継ぐ。
        取り込みの順番は json_files の順 (語形などは後のファイルが優先)"""
        with tempfile.TemporaryDirectory() as directory:
            # 照合用の空の DB (同じクラスで作る)
            fresh = type(self)(Path(directory) / "fresh.db")
            try:
                fresh.initialize()
                for path, label in json_files:
                    try:
                        fresh.import_json(path, label)
                    except ValueError as error:
                        raise ValueError(f"{label}: {error}") from None
            finally:
                fresh.close()
            self.connection.execute("ATTACH DATABASE ? AS fresh", (str(Path(directory) / "fresh.db"),))
            try:
                report = self._sync_report()
                if apply and report.changed:
                    if transfers is None:
                        transfers = {(language, old): new for language, old, new, _ in report.renamed_words}
                        removed = {(language, word) for language, word, _ in report.removed_words}
                        transfers.update({key: new for key, new in (extra_transfers or {}).items() if key in removed})
                    self._replace_content(transfers)
            except Exception:
                self.connection.rollback()
                raise
            finally:
                self.connection.commit()
                self.connection.execute("DETACH DATABASE fresh")
        return report

    def _sync_report(self) -> SyncReport:
        def diff(query: str) -> tuple[list, list]:
            current = {tuple(row) for row in self.connection.execute(query.format(db="main"))}
            new = {tuple(row) for row in self.connection.execute(query.format(db="fresh"))}
            return sorted(current - new, key=str), sorted(new - current, key=str)

        removed_words, added_words = diff("SELECT language, word FROM {db}.words")
        history = {
            (row["language"], row["word"]): row["history"]
            for row in self.connection.execute(
                "SELECT w.language, w.word, COUNT(*) AS history FROM answer_history h JOIN main.words w ON w.id = h.word_id GROUP BY w.id"
            )
        }
        entries = diff(
            "SELECT w.language, w.word, s.name, e.difficulty FROM {db}.entries e"
            " JOIN {db}.words w ON w.id = e.word_id JOIN {db}.sources s ON s.id = e.source_id"
        )
        meanings = diff(
            "SELECT w.language, w.word, p.name, m.meaning_ja FROM {db}.meanings m"
            " JOIN {db}.parts_of_speech p ON p.id = m.part_of_speech_id JOIN {db}.words w ON w.id = p.word_id"
        )
        examples = diff("SELECT w.language, w.word, x.sentence FROM {db}.examples x JOIN {db}.words w ON w.id = x.word_id")
        # 一覧に出ない違い: 語形・格支配・訳・ターゲット・類義語・派生語・取り込み元のファイル
        details = [
            diff("SELECT w.language, w.word, p.name, m.meaning_ja, m.forms, m.valency FROM {db}.meanings m"
                 " JOIN {db}.parts_of_speech p ON p.id = m.part_of_speech_id JOIN {db}.words w ON w.id = p.word_id"),
            diff("SELECT w.language, w.word, x.sentence, x.translation_ja, x.targets, x.valency FROM {db}.examples x"
                 " JOIN {db}.words w ON w.id = x.word_id"),
            diff("SELECT w.language, w.word, w.separable_prefix FROM {db}.words w"),
            diff("SELECT a.language, a.word, am.meaning_ja, r.relation_type, b.word, bm.meaning_ja FROM {db}.meaning_relations r"
                 " JOIN {db}.meanings am ON am.id = r.meaning_id JOIN {db}.parts_of_speech ap ON ap.id = am.part_of_speech_id"
                 " JOIN {db}.words a ON a.id = ap.word_id JOIN {db}.meanings bm ON bm.id = r.related_meaning_id"
                 " JOIN {db}.parts_of_speech bp ON bp.id = bm.part_of_speech_id JOIN {db}.words b ON b.id = bp.word_id"),
            diff("SELECT a.language, a.word, b.word, d.part_of_speech FROM {db}.derivations d"
                 " JOIN {db}.words a ON a.id = d.word_id JOIN {db}.words b ON b.id = d.derived_word_id"),
            diff("SELECT w.language, w.word, s.name, e.difficulty, mw.word, m.meaning_ja FROM {db}.entry_meanings em"
                 " JOIN {db}.entries e ON e.id = em.entry_id JOIN {db}.words w ON w.id = e.word_id JOIN {db}.sources s ON s.id = e.source_id"
                 " JOIN {db}.meanings m ON m.id = em.meaning_id JOIN {db}.parts_of_speech mp ON mp.id = m.part_of_speech_id"
                 " JOIN {db}.words mw ON mw.id = mp.word_id"),
            diff("SELECT w.language, w.word, s.name, e.difficulty, f.file FROM {db}.entry_files f JOIN {db}.entries e ON e.id = f.entry_id"
                 " JOIN {db}.words w ON w.id = e.word_id JOIN {db}.sources s ON s.id = e.source_id"),
        ]
        renamed = self._find_renames(removed_words, added_words)
        renamed_old = {(language, old) for language, old, _ in renamed}
        renamed_new = {(language, new) for language, _, new in renamed}
        return SyncReport(
            [(language, word, history.get((language, word), 0)) for language, word in removed_words if (language, word) not in renamed_old],
            [(language, word, 0) for language, word in added_words if (language, word) not in renamed_new],
            *entries, *meanings, *examples,
            any(removed or added for removed, added in details),
            [(language, old, new, history.get((language, old), 0)) for language, old, new in renamed],
        )

    @staticmethod
    def _find_renames(removed: list[tuple[str, str]], added: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """消える単語と増える単語のうち、綴りがよく似た1対1の組 (綴りの修正) を探す"""
        def best(word: tuple[str, str], candidates: list[tuple[str, str]]) -> tuple[str, str] | None:
            scored = [
                (SequenceMatcher(None, word[1].casefold(), other[1].casefold()).ratio(), other)
                for other in candidates if other[0] == word[0]
            ]
            scored = [item for item in scored if item[0] >= RENAME_SIMILARITY]
            if not scored:
                return None
            scored.sort(reverse=True)
            # 同じくらい似た候補が2つ以上あるときは決めない
            return scored[0][1] if len(scored) == 1 or scored[1][0] < scored[0][0] else None

        return [
            (old[0], old[1], new[1])
            for old in removed
            if (new := best(old, added)) is not None and best(new, removed) == old
        ]

    def _replace_content(self, transfers: dict[tuple[str, str], str]) -> None:
        """単語データを attach した fresh のものに置き換え、回答履歴の単語IDを付け替える。
        transfers {(言語, 消える単語): 引き継ぐ単語} の回答履歴は引き継ぐ単語に付け替える"""
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("DROP TABLE IF EXISTS temp.word_map")
            self.connection.execute(
                "CREATE TEMP TABLE word_map AS SELECT o.id AS old_id, n.id AS new_id"
                " FROM main.words o JOIN fresh.words n ON n.word = o.word AND n.language = o.language"
            )
            for (language, old), new in transfers.items():
                self.connection.execute(
                    "INSERT INTO temp.word_map SELECT o.id, n.id FROM main.words o JOIN fresh.words n ON n.language = o.language"
                    " WHERE o.language = ? AND o.word = ? AND n.word = ?",
                    (language, old, new),
                )
            for table in CONTENT_TABLES:
                columns = [row[1] for row in self.connection.execute(f"PRAGMA fresh.table_info({table})")]
                current = {row[1] for row in self.connection.execute(f"PRAGMA main.table_info({table})")}
                columns = ", ".join(column for column in columns if column in current)
                self.connection.execute(f"DELETE FROM main.{table}")
                self.connection.execute(f"INSERT INTO main.{table} ({columns}) SELECT {columns} FROM fresh.{table}")
            self.connection.execute("DELETE FROM answer_history WHERE word_id NOT IN (SELECT old_id FROM temp.word_map)")
            self.connection.execute(
                "UPDATE answer_history SET word_id = (SELECT new_id FROM temp.word_map WHERE old_id = answer_history.word_id)"
            )
            # 綴りを直した単語の既習の印も引き継ぐ
            for (language, old), new in transfers.items():
                self.connection.execute(
                    "UPDATE OR IGNORE known_words SET word = ? WHERE language = ? AND word = ?", (new, language, old)
                )
            self.connection.execute("DROP TABLE temp.word_map")
            self.connection.commit()
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    # --- 端末間の同期 (Mac の server.py と iPad のブラウザ版) ---

    def history_rows(self, exclude_uids: set[str] = frozenset()) -> list[dict]:
        """回答履歴を端末に依存しない形 (単語は見出し語×言語) で返す。exclude_uids の回答は除く"""
        rows = self.connection.execute(
            """
            SELECT h.uid, w.word, w.language, h.question_type, h.selected_answer, h.correct_answer, h.is_correct, h.answered_at, h.variant
            FROM answer_history h JOIN words w ON w.id = h.word_id ORDER BY h.answered_at, h.id
            """
        ).fetchall()
        return [dict(row) for row in rows if row["uid"] not in exclude_uids]

    def history_uids(self) -> set[str]:
        return {row[0] for row in self.connection.execute("SELECT uid FROM answer_history")}

    def merge_history(self, rows: list[dict]) -> tuple[int, int]:
        """ほかの端末の回答履歴を足す。すでにある回答 (uid が同じ) は入れない。
        (足した件数, この端末にない単語なので入れなかった件数) を返す"""
        word_ids = {(row["word"], row["language"]): row["id"] for row in self.connection.execute("SELECT id, word, language FROM words")}
        known = self.history_uids()
        added = skipped = 0
        for row in rows:
            if row["uid"] in known:
                continue
            word_id = word_ids.get((row["word"], row["language"]))
            if word_id is None:
                skipped += 1
                continue
            self.connection.execute(
                "INSERT INTO answer_history(word_id, question_type, selected_answer, correct_answer, is_correct, answered_at, variant, uid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    word_id, row["question_type"], row["selected_answer"], row["correct_answer"], int(row["is_correct"]),
                    row["answered_at"], row["variant"], row["uid"],
                ),
            )
            known.add(row["uid"])
            added += 1
        self.connection.commit()
        return added, skipped

    def export_content(self, path: Path) -> None:
        """単語データだけ (回答履歴なし) のDBを path に書き出す。ほかの端末が replace_content_from で取り込む"""
        path.unlink(missing_ok=True)
        self.connection.commit()
        self.connection.execute("VACUUM INTO ?", (str(path),))
        exported = sqlite3.connect(path)
        try:
            exported.execute("DELETE FROM answer_history")
            exported.execute("DELETE FROM known_words")
            exported.commit()
            exported.execute("VACUUM")
        finally:
            exported.close()

    def replace_content_from(self, path: Path) -> None:
        """単語データを export_content で書き出したDBのものに置き換える。回答履歴は同じ単語に付け替え、
        相手にない単語の回答履歴は消える (先に merge_history で相手に送っておく)"""
        self.connection.commit()
        self.connection.execute("ATTACH DATABASE ? AS fresh", (str(path),))
        try:
            self._replace_content({})
        finally:
            self.connection.execute("DETACH DATABASE fresh")

    def remove_entry(self, word: str, language: str, source: str, difficulty: str) -> None:
        """見出し1つを消し、どの見出しにも属さなくなった意味・例文を消す (単語と回答履歴は残す)"""
        row = self.connection.execute(
            "SELECT e.id, e.word_id FROM entries e JOIN words w ON w.id = e.word_id JOIN sources s ON s.id = e.source_id"
            " WHERE w.word=? AND w.language=? AND s.name=? AND e.difficulty=?",
            (split_separable(word)[0], language, source or "JSON", difficulty or ""),
        ).fetchone()
        if row is None:
            return
        word_ids = {row["word_id"]} | {
            item[0] for item in self.connection.execute(
                "SELECT DISTINCT p.word_id FROM entry_meanings em JOIN meanings m ON m.id = em.meaning_id"
                " JOIN parts_of_speech p ON p.id = m.part_of_speech_id WHERE em.entry_id=?",
                (row["id"],),
            )
        }
        self.connection.execute("DELETE FROM entries WHERE id=?", (row["id"],))
        for word_id in word_ids:
            self.connection.execute(
                """
                DELETE FROM meanings WHERE part_of_speech_id IN (SELECT id FROM parts_of_speech WHERE word_id=?)
                  AND id NOT IN (SELECT meaning_id FROM entry_meanings) AND id NOT IN (SELECT related_meaning_id FROM meaning_relations)
                """,
                (word_id,),
            )
            self.connection.execute(
                "DELETE FROM parts_of_speech WHERE word_id=? AND NOT EXISTS (SELECT 1 FROM meanings WHERE part_of_speech_id = parts_of_speech.id)",
                (word_id,),
            )
            self.connection.execute("DELETE FROM examples WHERE word_id=? AND id NOT IN (SELECT example_id FROM entry_examples)", (word_id,))
        self.connection.commit()

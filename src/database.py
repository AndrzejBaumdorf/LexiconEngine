from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path


RECENT_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class Question:
    word_id: int
    question_type: str
    prompt: str
    choices: list[str]
    answer: str


class LexiconDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self._migrate_legacy_schema()
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY, word TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
                part_of_speech TEXT, difficulty TEXT, source_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language),
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );
            CREATE TABLE IF NOT EXISTS meanings (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL,
                UNIQUE(word_id, meaning_ja), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS examples (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, sentence TEXT NOT NULL,
                translation_ja TEXT, UNIQUE(word_id, sentence), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS word_relations (
                word_id INTEGER NOT NULL, related_word_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL CHECK (relation_type IN ('synonym', 'antonym')),
                PRIMARY KEY (word_id, related_word_id, relation_type),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (related_word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS answer_history (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, question_type TEXT NOT NULL,
                selected_answer TEXT NOT NULL, correct_answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
                answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            """
        )
        self.connection.commit()

    def _migrate_legacy_schema(self) -> None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(words)")}
        if not columns or "language" in columns:
            return
        self.connection.execute("PRAGMA foreign_keys = OFF")
        self.connection.executescript("ALTER TABLE words RENAME TO legacy_words; ALTER TABLE answer_history RENAME TO legacy_answer_history; ALTER TABLE word_relations RENAME TO legacy_word_relations;")
        self.connection.executescript(
            """
            CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE words (id INTEGER PRIMARY KEY, word TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English', part_of_speech TEXT, difficulty TEXT, source_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language), FOREIGN KEY (source_id) REFERENCES sources(id));
            CREATE TABLE meanings (id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL, UNIQUE(word_id, meaning_ja), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
            CREATE TABLE examples (id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, sentence TEXT NOT NULL, translation_ja TEXT, UNIQUE(word_id, sentence), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
            CREATE TABLE word_relations (word_id INTEGER NOT NULL, related_word_id INTEGER NOT NULL, relation_type TEXT NOT NULL, PRIMARY KEY (word_id, related_word_id, relation_type), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE, FOREIGN KEY (related_word_id) REFERENCES words(id) ON DELETE CASCADE);
            CREATE TABLE answer_history (id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, question_type TEXT NOT NULL, selected_answer TEXT NOT NULL, correct_answer TEXT NOT NULL DEFAULT '', is_correct INTEGER NOT NULL, answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
            INSERT INTO words (id, word, language) SELECT id, word, 'English' FROM legacy_words;
            INSERT INTO meanings (word_id, meaning_ja) SELECT id, meaning_ja FROM legacy_words;
            INSERT INTO examples (word_id, sentence) SELECT id, example_sentence FROM legacy_words WHERE example_sentence IS NOT NULL AND example_sentence != '';
            INSERT INTO answer_history (id, word_id, question_type, selected_answer, is_correct) SELECT id, word_id, question_type, selected_answer, is_correct FROM legacy_answer_history;
            DROP TABLE legacy_word_relations; DROP TABLE legacy_answer_history; DROP TABLE legacy_words;
            """
        )
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = ON")

    def import_json(self, json_path: Path) -> int:
        with json_path.open(encoding="utf-8") as file:
            payload = json.load(file)

        entries = [payload] if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("JSONのルートは単語オブジェクトまたは単語オブジェクトの配列にしてください")

        imported = 0
        for entry in entries:
            word = entry.get("word")
            if not isinstance(word, str) or not word.strip():
                raise ValueError("各単語データには word が必要です")
            language = entry.get("language", "English")
            source = entry.get("source", "JSON")
            if not isinstance(language, str) or not isinstance(source, str):
                raise ValueError("language と source は文字列にしてください")

            self.connection.execute("INSERT OR IGNORE INTO sources(name) VALUES (?)", (source,))
            source_id = self.connection.execute("SELECT id FROM sources WHERE name=?", (source,)).fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO words (word, language, part_of_speech, difficulty, source_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(word, language) DO UPDATE SET
                    part_of_speech=excluded.part_of_speech,
                    difficulty=excluded.difficulty,
                    source_id=excluded.source_id
                """,
                (word.strip(), language, entry.get("part_of_speech"), entry.get("difficulty"), source_id),
            )
            word_id = self.connection.execute(
                "SELECT id FROM words WHERE word=? AND language=?", (word.strip(), language)
            ).fetchone()[0]

            meanings = entry.get("meanings", [])
            if not isinstance(meanings, list) or not all(isinstance(meaning, str) for meaning in meanings):
                raise ValueError(f"{word}: meanings は文字列の配列にしてください")
            for meaning in meanings:
                self.connection.execute("INSERT OR IGNORE INTO meanings(word_id, meaning_ja) VALUES (?, ?)", (word_id, meaning.strip()))

            examples = entry.get("examples", [])
            if not isinstance(examples, list):
                raise ValueError(f"{word}: examples は配列にしてください")
            for example in examples:
                if not isinstance(example, dict) or not isinstance(example.get("sentence"), str):
                    raise ValueError(f"{word}: examples の各要素には sentence が必要です")
                self.connection.execute(
                    "INSERT OR IGNORE INTO examples(word_id, sentence, translation_ja) VALUES (?, ?, ?)",
                    (word_id, example["sentence"].strip(), example.get("translation_ja")),
                )

            for relation_type in ("synonyms", "antonyms"):
                related_words = entry.get(relation_type, [])
                if not isinstance(related_words, list) or not all(isinstance(related, str) for related in related_words):
                    raise ValueError(f"{word}: {relation_type} は文字列の配列にしてください")
                db_relation_type = relation_type[:-1]
                for related in related_words:
                    related = related.strip()
                    if not related or related == word.strip():
                        continue
                    self.connection.execute(
                        "INSERT OR IGNORE INTO words (word, language, source_id) VALUES (?, ?, ?)",
                        (related, language, source_id),
                    )
                    related_id = self.connection.execute(
                        "SELECT id FROM words WHERE word=? AND language=?", (related, language)
                    ).fetchone()[0]
                    self.connection.execute(
                        "INSERT OR IGNORE INTO word_relations(word_id, related_word_id, relation_type) VALUES (?, ?, ?)",
                        (word_id, related_id, db_relation_type),
                    )
            imported += 1
        self.connection.commit()
        return imported

    def _related_word_ids(self, word_id: int) -> tuple[int, ...]:
        rows = self.connection.execute(
            "SELECT related_word_id FROM word_relations WHERE word_id=? "
            "UNION SELECT word_id FROM word_relations WHERE related_word_id=?",
            (word_id, word_id),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _choice_exclusion_ids(self, word_id: int) -> tuple[int, ...]:
        return (word_id, *self._related_word_ids(word_id))

    def _weighted_word(self, question_type: str, join: str = "") -> sqlite3.Row:
        rows = self.connection.execute(
            f"""
            SELECT w.id, w.word, m.meaning_ja,
                COALESCE((
                    SELECT AVG(CASE WHEN recent.is_correct = 0 THEN 1.0 ELSE 0.0 END)
                    FROM (
                        SELECT h.is_correct
                        FROM answer_history h
                        WHERE h.word_id = w.id AND h.question_type = ?
                        ORDER BY h.id DESC
                        LIMIT ?
                    ) AS recent
                ), 0.0) AS incorrect_rate
            FROM words w
            JOIN meanings m ON m.word_id = w.id
            {join}
            GROUP BY w.id, m.id
            ORDER BY RANDOM()
            """,
            (question_type, RECENT_HISTORY_LIMIT),
        ).fetchall()
        if not rows:
            raise ValueError("問題を作れる単語データがありません")
        return random.choices(rows, weights=[1.0 + 5.0 * row["incorrect_rate"] for row in rows], k=1)[0]

    def create_question(self, question_type: str, show_hint: bool = False) -> Question:
        if question_type == "english_to_japanese":
            target = self._weighted_word(question_type)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            placeholders = ", ".join("?" for _ in excluded_ids)
            choices = [target["meaning_ja"], *(row[0] for row in self.connection.execute(f"SELECT DISTINCT meaning_ja FROM meanings WHERE word_id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT 3", excluded_ids).fetchall())]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["word"], choices, target["meaning_ja"])
        if question_type == "japanese_to_english":
            target = self._weighted_word(question_type)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            placeholders = ", ".join("?" for _ in excluded_ids)
            choices = [target["word"], *(row[0] for row in self.connection.execute(f"SELECT word FROM words WHERE id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT 3", excluded_ids).fetchall())]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["meaning_ja"], choices, target["word"])
        if question_type == "cloze":
            target = self._weighted_word(question_type, "JOIN examples e ON e.word_id=w.id")
            sentence = self.connection.execute("SELECT sentence FROM examples WHERE word_id=? ORDER BY RANDOM() LIMIT 1", (target["id"],)).fetchone()[0]
            prompt = sentence.replace(target["word"], "_____")
            if show_hint:
                prompt += f"\nヒント: {target['meaning_ja']}"
            excluded_ids = self._choice_exclusion_ids(target["id"])
            placeholders = ", ".join("?" for _ in excluded_ids)
            choices = [target["word"], *(row[0] for row in self.connection.execute(f"SELECT word FROM words WHERE id NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT 3", excluded_ids).fetchall())]
            random.shuffle(choices)
            return Question(target["id"], question_type, prompt, choices, target["word"])
        if question_type == "relation":
            relation = self.connection.execute("SELECT * FROM word_relations ORDER BY RANDOM() LIMIT 1").fetchone()
            if not relation:
                raise ValueError("類義語・対義語データがありません")
            target = self.connection.execute("SELECT id, word FROM words WHERE id=?", (relation["word_id"],)).fetchone()
            related = self.connection.execute("SELECT word FROM words WHERE id=?", (relation["related_word_id"],)).fetchone()[0]
            choices = [related, *(row[0] for row in self.connection.execute("SELECT word FROM words WHERE id NOT IN (?, ?) ORDER BY RANDOM() LIMIT 3", (target["id"], relation["related_word_id"])).fetchall())]
            random.shuffle(choices)
            label = "類義語" if relation["relation_type"] == "synonym" else "対義語"
            return Question(target["id"], question_type, f"{target['word']} の{label}は?", choices, related)
        raise ValueError(f"未対応の問題形式です: {question_type}")

    def record_answer(self, question: Question, selected_answer: str) -> bool:
        correct = selected_answer == question.answer
        self.connection.execute("INSERT INTO answer_history(word_id, question_type, selected_answer, correct_answer, is_correct) VALUES (?, ?, ?, ?, ?)", (question.word_id, question.question_type, selected_answer, question.answer, int(correct)))
        self.connection.commit()
        return correct

    def recent_accuracy(self, question_type: str | None = None, limit: int = 10) -> tuple[int, int, float]:
        condition = "WHERE question_type=?" if question_type else ""
        params = (question_type, limit) if question_type else (limit,)
        row = self.connection.execute(f"SELECT COUNT(*) total, COALESCE(SUM(is_correct),0) correct FROM (SELECT is_correct FROM answer_history {condition} ORDER BY id DESC LIMIT ?)", params).fetchone()
        return row["total"], row["correct"], (row["correct"] / row["total"] * 100 if row["total"] else 0.0)

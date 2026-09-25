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
                difficulty TEXT, source_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language),
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );
            CREATE TABLE IF NOT EXISTS parts_of_speech (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, name TEXT NOT NULL,
                UNIQUE(word_id, name), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS meanings (
                id INTEGER PRIMARY KEY, part_of_speech_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL,
                UNIQUE(part_of_speech_id, meaning_ja), FOREIGN KEY (part_of_speech_id) REFERENCES parts_of_speech(id) ON DELETE CASCADE
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
            CREATE TABLE IF NOT EXISTS meaning_relations (
                meaning_id INTEGER NOT NULL, related_meaning_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL CHECK (relation_type IN ('synonym', 'antonym')),
                PRIMARY KEY (meaning_id, related_meaning_id, relation_type),
                FOREIGN KEY (meaning_id) REFERENCES meanings(id) ON DELETE CASCADE,
                FOREIGN KEY (related_meaning_id) REFERENCES meanings(id) ON DELETE CASCADE
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
        self._migrate_meaning_schema()
        self.connection.commit()

    def _migrate_meaning_schema(self) -> None:
        meaning_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(meanings)")}
        if not meaning_columns or "part_of_speech_id" in meaning_columns:
            return

        self.connection.execute("PRAGMA foreign_keys = OFF")
        self.connection.execute("ALTER TABLE meanings RENAME TO legacy_meanings")
        self.connection.execute("ALTER TABLE words RENAME TO legacy_words")
        self.connection.executescript(
            """
            CREATE TABLE words (id INTEGER PRIMARY KEY, word TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English', difficulty TEXT, source_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language), FOREIGN KEY (source_id) REFERENCES sources(id));
            CREATE TABLE IF NOT EXISTS parts_of_speech (id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, name TEXT NOT NULL, UNIQUE(word_id, name), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
            CREATE TABLE meanings (id INTEGER PRIMARY KEY, part_of_speech_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL, UNIQUE(part_of_speech_id, meaning_ja), FOREIGN KEY (part_of_speech_id) REFERENCES parts_of_speech(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS meaning_relations (meaning_id INTEGER NOT NULL, related_meaning_id INTEGER NOT NULL, relation_type TEXT NOT NULL CHECK (relation_type IN ('synonym', 'antonym')), PRIMARY KEY (meaning_id, related_meaning_id, relation_type), FOREIGN KEY (meaning_id) REFERENCES meanings(id) ON DELETE CASCADE, FOREIGN KEY (related_meaning_id) REFERENCES meanings(id) ON DELETE CASCADE);
            INSERT INTO words (id, word, language, difficulty, source_id, created_at) SELECT id, word, language, difficulty, source_id, created_at FROM legacy_words;
            """
        )
        for word in self.connection.execute("SELECT id, part_of_speech FROM legacy_words"):
            part_of_speech = word[1] or "不明"
            self.connection.execute("INSERT INTO parts_of_speech(word_id, name) VALUES (?, ?)", (word[0], part_of_speech))
        self.connection.execute(
            """
            INSERT INTO meanings (id, part_of_speech_id, meaning_ja)
            SELECT legacy_meanings.id, parts_of_speech.id, legacy_meanings.meaning_ja
            FROM legacy_meanings
            JOIN parts_of_speech ON parts_of_speech.word_id = legacy_meanings.word_id
            """
        )
        legacy_relation_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(word_relations)")}
        if legacy_relation_columns:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO meaning_relations(meaning_id, related_meaning_id, relation_type)
                SELECT source_meaning.id, related_meaning.id, legacy_relations.relation_type
                FROM word_relations legacy_relations
                JOIN parts_of_speech source_pos ON source_pos.word_id = legacy_relations.word_id
                JOIN meanings source_meaning ON source_meaning.part_of_speech_id = source_pos.id
                JOIN parts_of_speech related_pos ON related_pos.word_id = legacy_relations.related_word_id
                JOIN meanings related_meaning ON related_meaning.part_of_speech_id = related_pos.id
                WHERE source_meaning.id = (SELECT MIN(id) FROM meanings WHERE part_of_speech_id = source_pos.id)
                  AND related_meaning.id = (SELECT MIN(id) FROM meanings WHERE part_of_speech_id = related_pos.id)
                """
            )
        self.connection.execute("DROP TABLE legacy_meanings")
        self.connection.execute("DROP TABLE legacy_words")
        self.connection.execute("PRAGMA foreign_keys = ON")

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
                INSERT INTO words (word, language, difficulty, source_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(word, language) DO UPDATE SET
                    difficulty=excluded.difficulty,
                    source_id=excluded.source_id
                """,
                (word.strip(), language, entry.get("difficulty"), source_id),
            )
            word_id = self.connection.execute(
                "SELECT id FROM words WHERE word=? AND language=?", (word.strip(), language)
            ).fetchone()[0]

            parts_of_speech = entry.get("parts_of_speech")
            if parts_of_speech is None:
                legacy_meanings = entry.get("meanings", [])
                if not isinstance(legacy_meanings, list):
                    raise ValueError(f"{word}: meanings は配列にしてください")
                parts_of_speech = [{
                    "part_of_speech": entry.get("part_of_speech", "不明"),
                    "meanings": [
                        {
                            "meaning_ja": meaning,
                            "synonyms": entry.get("synonyms", []),
                            "antonyms": entry.get("antonyms", []),
                        }
                        if isinstance(meaning, str) else meaning
                        for meaning in legacy_meanings
                    ],
                }]
                legacy_format = True
            else:
                legacy_format = False
            if not isinstance(parts_of_speech, list):
                raise ValueError(f"{word}: parts_of_speech は配列にしてください")

            for part in parts_of_speech:
                if not isinstance(part, dict):
                    raise ValueError(f"{word}: parts_of_speech の各要素はオブジェクトにしてください")
                part_name = part.get("part_of_speech") or part.get("name")
                meanings = part.get("meanings", [])
                if not isinstance(part_name, str) or not part_name.strip():
                    raise ValueError(f"{word}: 品詞名が必要です")
                if not isinstance(meanings, list):
                    raise ValueError(f"{word}/{part_name}: meanings は配列にしてください")

                self.connection.execute(
                    "INSERT OR IGNORE INTO parts_of_speech(word_id, name) VALUES (?, ?)",
                    (word_id, part_name.strip()),
                )
                part_id = self.connection.execute(
                    "SELECT id FROM parts_of_speech WHERE word_id=? AND name=?",
                    (word_id, part_name.strip()),
                ).fetchone()[0]

                for meaning in meanings:
                    if isinstance(meaning, str):
                        meaning = {"meaning_ja": meaning}
                    if not isinstance(meaning, dict) or not isinstance(meaning.get("meaning_ja"), str):
                        raise ValueError(f"{word}/{part_name}: meanings の各要素には meaning_ja が必要です")
                    meaning_ja = meaning["meaning_ja"].strip()
                    self.connection.execute(
                        "INSERT OR IGNORE INTO meanings(part_of_speech_id, meaning_ja) VALUES (?, ?)",
                        (part_id, meaning_ja),
                    )
                    meaning_id = self.connection.execute(
                        "SELECT id FROM meanings WHERE part_of_speech_id=? AND meaning_ja=?",
                        (part_id, meaning_ja),
                    ).fetchone()[0]
                    self._import_meaning_relations(meaning_id, meaning, language, source_id, part_name.strip())

                    examples = meaning.get("examples", [])
                    if not isinstance(examples, list):
                        raise ValueError(f"{word}/{part_name}: examples は配列にしてください")
                    for example in examples:
                        self._insert_example(word, word_id, example)

            examples = entry.get("examples", []) if legacy_format else []
            if not isinstance(examples, list):
                raise ValueError(f"{word}: examples は配列にしてください")
            for example in examples:
                self._insert_example(word, word_id, example)
            imported += 1
        self.connection.commit()
        return imported

    def _insert_example(self, word: str, word_id: int, example: object) -> None:
        if not isinstance(example, dict) or not isinstance(example.get("sentence"), str):
            raise ValueError(f"{word}: examples の各要素には sentence が必要です")
        self.connection.execute(
            "INSERT OR IGNORE INTO examples(word_id, sentence, translation_ja) VALUES (?, ?, ?)",
            (word_id, example["sentence"].strip(), example.get("translation_ja")),
        )

    def _import_meaning_relations(
        self,
        meaning_id: int,
        meaning: dict,
        language: str,
        source_id: int,
        default_part_of_speech: str,
    ) -> None:
        for relation_type in ("synonyms", "antonyms"):
            related_entries = meaning.get(relation_type, [])
            if not isinstance(related_entries, list):
                raise ValueError(f"{relation_type} は配列にしてください")
            for related_entry in related_entries:
                if isinstance(related_entry, str):
                    related_entry = {"word": related_entry, "meaning_ja": related_entry}
                if not isinstance(related_entry, dict):
                    raise ValueError(f"{relation_type} の各要素は文字列またはオブジェクトにしてください")
                related_word = related_entry.get("word")
                related_meaning = related_entry.get("meaning_ja")
                related_pos = related_entry.get("part_of_speech", default_part_of_speech)
                if not isinstance(related_word, str) or not related_word.strip():
                    raise ValueError(f"{relation_type} には word が必要です")
                if not isinstance(related_meaning, str) or not related_meaning.strip():
                    raise ValueError(f"{related_word}: 関連する意味には meaning_ja が必要です")
                self.connection.execute(
                    "INSERT OR IGNORE INTO words (word, language, source_id) VALUES (?, ?, ?)",
                    (related_word.strip(), language, source_id),
                )
                related_word_id = self.connection.execute(
                    "SELECT id FROM words WHERE word=? AND language=?", (related_word.strip(), language)
                ).fetchone()[0]
                self.connection.execute(
                    "INSERT OR IGNORE INTO parts_of_speech(word_id, name) VALUES (?, ?)",
                    (related_word_id, related_pos),
                )
                related_pos_id = self.connection.execute(
                    "SELECT id FROM parts_of_speech WHERE word_id=? AND name=?",
                    (related_word_id, related_pos),
                ).fetchone()[0]
                self.connection.execute(
                    "INSERT OR IGNORE INTO meanings(part_of_speech_id, meaning_ja) VALUES (?, ?)",
                    (related_pos_id, related_meaning.strip()),
                )
                related_meaning_id = self.connection.execute(
                    "SELECT id FROM meanings WHERE part_of_speech_id=? AND meaning_ja=?",
                    (related_pos_id, related_meaning.strip()),
                ).fetchone()[0]
                self.connection.execute(
                    "INSERT OR IGNORE INTO meaning_relations(meaning_id, related_meaning_id, relation_type) VALUES (?, ?, ?)",
                    (meaning_id, related_meaning_id, relation_type[:-1]),
                )

    def _related_word_ids(self, word_id: int) -> tuple[int, ...]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT related_word.id
            FROM meaning_relations mr
            JOIN meanings related_meaning ON related_meaning.id = mr.related_meaning_id
            JOIN parts_of_speech related_pos ON related_pos.id = related_meaning.part_of_speech_id
            JOIN words related_word ON related_word.id = related_pos.word_id
            JOIN meanings target_meaning ON target_meaning.id = mr.meaning_id
            JOIN parts_of_speech target_pos ON target_pos.id = target_meaning.part_of_speech_id
            WHERE target_pos.word_id=?
            UNION
            SELECT DISTINCT target_word.id
            FROM meaning_relations mr
            JOIN meanings target_meaning ON target_meaning.id = mr.meaning_id
            JOIN parts_of_speech target_pos ON target_pos.id = target_meaning.part_of_speech_id
            JOIN words target_word ON target_word.id = target_pos.word_id
            JOIN meanings related_meaning ON related_meaning.id = mr.related_meaning_id
            JOIN parts_of_speech related_pos ON related_pos.id = related_meaning.part_of_speech_id
            WHERE related_pos.word_id=?
            """,
            (word_id, word_id),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _choice_exclusion_ids(self, word_id: int) -> tuple[int, ...]:
        return (word_id, *self._related_word_ids(word_id))

    def _choice_candidates(self, column: str, excluded_ids: tuple[int, ...], part_of_speech: str, answer: str) -> list[str]:
        # 同じ品詞の候補を優先し、足りない場合のみ他の品詞で補う
        placeholders = ", ".join("?" for _ in excluded_ids)
        rows = self.connection.execute(
            f"""
            SELECT {column}
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE w.id NOT IN ({placeholders}) AND {column} != ? AND m.meaning_ja != w.word
            GROUP BY {column}
            ORDER BY MAX(p.name = ?) DESC, RANDOM()
            LIMIT 3
            """,
            (*excluded_ids, answer, part_of_speech),
        ).fetchall()
        return [row[0] for row in rows]

    def _weighted_word(self, question_type: str, join: str = "", where: str = "1") -> sqlite3.Row:
        rows = self.connection.execute(
            f"""
            SELECT w.id, w.word, p.name AS part_of_speech, m.id AS meaning_id, m.meaning_ja,
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
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            {join}
            WHERE {where}
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
            # 類義語・対義語を文字列だけで登録すると英単語自体が意味になるため除外する
            target = self._weighted_word(question_type, where="m.meaning_ja != w.word")
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["meaning_ja"], *self._choice_candidates("m.meaning_ja", excluded_ids, target["part_of_speech"], target["meaning_ja"])]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["word"], choices, target["meaning_ja"])
        if question_type == "japanese_to_english":
            target = self._weighted_word(question_type)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"])]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["meaning_ja"], choices, target["word"])
        if question_type == "cloze":
            target = self._weighted_word(question_type, "JOIN examples e ON e.word_id=w.id")
            sentence = self.connection.execute("SELECT sentence FROM examples WHERE word_id=? ORDER BY RANDOM() LIMIT 1", (target["id"],)).fetchone()[0]
            prompt = sentence.replace(target["word"], "_____")
            if show_hint:
                prompt += f"\nヒント: {target['meaning_ja']}"
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"])]
            random.shuffle(choices)
            return Question(target["id"], question_type, prompt, choices, target["word"])
        if question_type == "relation":
            relation = self.connection.execute("SELECT * FROM meaning_relations ORDER BY RANDOM() LIMIT 1").fetchone()
            if not relation:
                raise ValueError("類義語・対義語データがありません")
            target = self.connection.execute(
                """
                SELECT w.id, w.word, m.meaning_ja
                FROM meanings m
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE m.id=?
                """,
                (relation["meaning_id"],),
            ).fetchone()
            related_row = self.connection.execute(
                """
                SELECT w.id, w.word, p.name AS part_of_speech
                FROM meanings m
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE m.id=?
                """,
                (relation["related_meaning_id"],),
            ).fetchone()
            related = related_row["word"]
            choices = [related, *self._choice_candidates("w.word", (target["id"], related_row["id"]), related_row["part_of_speech"], related)]
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

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.db"
RECENT_HISTORY_LIMIT = 10
QUESTION_TYPES = {"1": "english_to_japanese", "2": "japanese_to_english", "3": "cloze", "4": "relation"}
QUESTION_LABELS = {"english_to_japanese": "英→日", "japanese_to_english": "日→英", "cloze": "例文穴埋め", "relation": "類義語・対義語"}


@dataclass(frozen=True)
class Question:
    word_id: int
    question_type: str
    prompt: str
    choices: list[str]
    answer: str


SAMPLE_WORDS = [
    ("abundant", "形容詞", "豊富な", "The region has abundant natural resources.", "B2"),
    ("accurate", "形容詞", "正確な", "Please provide accurate information.", "B1"),
    ("adapt", "動詞", "適応する", "Animals adapt to changes in their environment.", "B1"),
    ("clarify", "動詞", "明確にする", "Could you clarify your main point?", "B2"),
    ("eliminate", "動詞", "取り除く", "The new process will eliminate unnecessary steps.", "B2"),
    ("scarce", "形容詞", "不足した", "Water is scarce in the dry season.", "B2"),
]


class LexiconDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self, seed_samples: bool = True) -> None:
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
        if seed_samples:
            self._seed_samples()
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

    def _seed_samples(self) -> None:
        self.connection.execute("INSERT OR IGNORE INTO sources(name) VALUES ('The Japan Times EX')")
        source_id = self.connection.execute("SELECT id FROM sources WHERE name=?", ("The Japan Times EX",)).fetchone()[0]
        for word, pos, meaning, sentence, difficulty in SAMPLE_WORDS:
            self.connection.execute("INSERT INTO words (word, part_of_speech, difficulty, source_id) VALUES (?, ?, ?, ?) ON CONFLICT(word, language) DO UPDATE SET part_of_speech=excluded.part_of_speech, difficulty=excluded.difficulty, source_id=excluded.source_id", (word, pos, difficulty, source_id))
            word_id = self.connection.execute("SELECT id FROM words WHERE word=?", (word,)).fetchone()[0]
            self.connection.execute("INSERT OR IGNORE INTO meanings(word_id, meaning_ja) VALUES (?, ?)", (word_id, meaning))
            self.connection.execute("INSERT OR IGNORE INTO examples(word_id, sentence) VALUES (?, ?)", (word_id, sentence))
        self._add_relation("abundant", "scarce", "antonym")

    def _add_relation(self, word: str, related: str, relation_type: str) -> None:
        first = self.connection.execute("SELECT id FROM words WHERE word=?", (word,)).fetchone()
        second = self.connection.execute("SELECT id FROM words WHERE word=?", (related,)).fetchone()
        if first and second:
            self.connection.execute("INSERT OR IGNORE INTO word_relations VALUES (?, ?, ?)", (first[0], second[0], relation_type))

    def import_csv(self, csv_path: Path) -> int:
        with csv_path.open(newline="", encoding="utf-8-sig") as file:
            rows = csv.DictReader(file)
            if not rows.fieldnames or not {"word", "meaning_ja"}.issubset(rows.fieldnames):
                raise ValueError("CSVには word, meaning_ja 列が必要です")
            imported = 0
            for row in rows:
                source = (row.get("source") or "CSV").strip()
                source_id = self.connection.execute("INSERT OR IGNORE INTO sources(name) VALUES (?)", (source,)).lastrowid
                if not source_id:
                    source_id = self.connection.execute("SELECT id FROM sources WHERE name=?", (source,)).fetchone()[0]
                word = row["word"].strip()
                self.connection.execute("INSERT INTO words (word, language, part_of_speech, difficulty, source_id) VALUES (?, ?, ?, ?, ?) ON CONFLICT(word, language) DO UPDATE SET part_of_speech=excluded.part_of_speech, difficulty=excluded.difficulty, source_id=excluded.source_id", (word, row.get("language") or "English", row.get("part_of_speech"), row.get("difficulty"), source_id))
                word_id = self.connection.execute("SELECT id FROM words WHERE word=? AND language=?", (word, row.get("language") or "English")).fetchone()[0]
                self.connection.execute("INSERT OR IGNORE INTO meanings(word_id, meaning_ja) VALUES (?, ?)", (word_id, row["meaning_ja"].strip()))
                if row.get("example_sentence"):
                    self.connection.execute("INSERT OR IGNORE INTO examples(word_id, sentence, translation_ja) VALUES (?, ?, ?)", (word_id, row["example_sentence"].strip(), row.get("translation_ja")))
                imported += 1
        self.connection.commit()
        return imported

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
            choices = [target["meaning_ja"], *(row[0] for row in self.connection.execute("SELECT DISTINCT meaning_ja FROM meanings WHERE word_id != ? ORDER BY RANDOM() LIMIT 3", (target["id"],)).fetchall())]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["word"], choices, target["meaning_ja"])
        if question_type == "japanese_to_english":
            target = self._weighted_word(question_type)
            choices = [target["word"], *(row[0] for row in self.connection.execute("SELECT word FROM words WHERE id != ? ORDER BY RANDOM() LIMIT 3", (target["id"],)).fetchall())]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["meaning_ja"], choices, target["word"])
        if question_type == "cloze":
            target = self._weighted_word(question_type, "JOIN examples e ON e.word_id=w.id")
            sentence = self.connection.execute("SELECT sentence FROM examples WHERE word_id=? ORDER BY RANDOM() LIMIT 1", (target["id"],)).fetchone()[0]
            prompt = sentence.replace(target["word"], "_____")
            if show_hint:
                prompt += f"\nヒント: {target['meaning_ja']}"
            choices = [target["word"], *(row[0] for row in self.connection.execute("SELECT word FROM words WHERE id != ? ORDER BY RANDOM() LIMIT 3", (target["id"],)).fetchall())]
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


def run_quiz(database: LexiconDatabase) -> None:
    print("問題形式: 1 英→日 / 2 日→英 / 3 穴埋め / 4 類義語・対義語 / q 終了")
    while True:
        selected = input("形式: ").strip().lower()
        if selected == "q":
            print("終了しました。")
            return
        question_type = QUESTION_TYPES.get(selected)
        if not question_type:
            print("1〜4またはqを入力してください。")
            continue
        while True:
            question_count_input = input("出題数 (1〜100): ").strip().lower()
            if question_count_input == "q":
                print("終了しました。")
                return
            if question_count_input.isdigit() and 1 <= int(question_count_input) <= 100:
                question_count = int(question_count_input)
                break
            print("出題数は1〜100の整数で入力してください。")
        show_hint = selected == "3" and input("日本語ヒントを表示しますか? (y/N): ").strip().lower() == "y"
        correct_count = 0
        answered_count = 0
        for question_number in range(1, question_count + 1):
            try:
                question = database.create_question(question_type, show_hint)
            except ValueError as error:
                print(error)
                break
            print(f"\n[{question_number}/{question_count}] {question.prompt}")
            for index, choice in enumerate(question.choices, 1):
                print(f"  {index}. {choice}")
            answer = input("回答番号 (qで中断): ").strip().lower()
            if answer == "q":
                print("終了しました。")
                return
            if not answer.isdigit() or not 1 <= int(answer) <= len(question.choices):
                print("1〜4の番号を入力してください。")
                continue
            answered_count += 1
            selected_answer = question.choices[int(answer) - 1]
            if database.record_answer(question, selected_answer):
                correct_count += 1
                print("正解!")
            else:
                print(f"不正解。正解は {question.answer} です。")
        if answered_count:
            total, correct, accuracy = database.recent_accuracy(question.question_type)
            print(f"\n今回: {correct_count}/{answered_count} ({correct_count / answered_count * 100:.1f}%)")
            print(f"{QUESTION_LABELS[question.question_type]} 直近{total}問: {accuracy:.1f}% ({correct}/{total})")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLiteベースの英単語クイズ")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--import-csv", type=Path)
    parser.add_argument("--import-json", type=Path)
    args = parser.parse_args()
    database_exists = args.db.exists()
    database = LexiconDatabase(args.db)
    try:
        database.initialize(seed_samples=not args.import_json and (args.init or not database_exists))
        if args.import_csv:
            print(f"{database.import_csv(args.import_csv)}件取り込みました。")
        if args.import_json:
            print(f"{database.import_json(args.import_json)}件取り込みました。")
        run_quiz(database)
    finally:
        database.close()


if __name__ == "__main__":
    main()

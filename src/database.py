from __future__ import annotations

import json
import random
import re
import sqlite3
from dataclasses import dataclass, field
from typing import NamedTuple
from difflib import SequenceMatcher
from pathlib import Path

from cloze import blank_out, find_cloze_spans
from german import NO_PLURAL
from languages import FORM_KEYS, GENDERS, get_language


RECENT_HISTORY_LIMIT = 10
# 類義語・対義語を文字列だけで登録すると英単語自体が意味になるため、出題対象から除外する
HAS_REAL_MEANING = "m.meaning_ja != w.word"
CHOICE_POOL_SIZE = 30
SIMILAR_MEANING_RATIO = 0.6
MEANING_SEPARATOR = "\x1f"
ANNOTATION_PATTERN = re.compile(r"[〈（(［\[《][^〉）)］\]》]*[〉）)］\]》]")
CONTENT_PATTERN = re.compile(r"[\u3400-\u9fff々\u30a1-\u30ff]")
LEGACY_POS_NAMES = {"noun": "名詞", "verb": "動詞", "adjective": "形容詞", "adverb": "副詞"}


def _meaning_key(meaning_ja: str) -> str:
    # 〈批評などが〉のような注記と記号を除き、漢字・カタカナがあればそれだけで比べる (辛らつな / 辛辣な → 辛 / 辛辣)
    text = re.sub(r"[〜～…、，,；;・\s]", "", ANNOTATION_PATTERN.sub("", meaning_ja))
    return "".join(CONTENT_PATTERN.findall(text)) or text


def meanings_similar(first: str, second: str) -> bool:
    first, second = _meaning_key(first), _meaning_key(second)
    return bool(first and second) and SequenceMatcher(None, first, second).ratio() >= SIMILAR_MEANING_RATIO


def normalize_derivatives(entry: dict) -> list[dict]:
    """derivatives と旧形式の related_words ({"noun": "x"} など) を {word, part_of_speech, meaning_ja?} の配列にそろえる"""
    word = entry.get("word")
    derivatives = entry.get("derivatives", [])
    related_words = entry.get("related_words", {})
    if not isinstance(derivatives, list) or not isinstance(related_words, dict):
        raise ValueError(f"{word}: derivatives は配列、related_words はオブジェクトにしてください")
    items = list(derivatives)
    for key, values in related_words.items():
        for value in values if isinstance(values, list) else [values]:
            item = {"word": value} if isinstance(value, str) else dict(value) if isinstance(value, dict) else None
            if item is None:
                raise ValueError(f"{word}: related_words の各要素は文字列またはオブジェクトにしてください")
            item.setdefault("part_of_speech", LEGACY_POS_NAMES.get(key, key))
            items.append(item)

    normalized = []
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("word", "part_of_speech")):
            raise ValueError(f"{word}: 派生語には word と part_of_speech が必要です")
        derivative = {"word": item["word"].strip(), "part_of_speech": item["part_of_speech"].strip()}
        meaning_ja = item.get("meaning_ja")
        if isinstance(meaning_ja, str) and meaning_ja.strip():
            derivative["meaning_ja"] = meaning_ja.strip()
        normalized.append(derivative)
    return normalized


@dataclass(frozen=True)
class QuizScope:
    """出題範囲。None や空の条件は絞り込まない。誤答の選択肢は範囲に関係なく同じ言語の単語から選ぶ"""
    language: str | None = None
    sources: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()

    def condition(self, word: str = "w", meaning: str = "m") -> tuple[str, tuple]:
        # 出典・レベルは意味ごとに判定する。A1 の aber (接続詞) と A2 の aber (心態詞) をレベルで出し分けるため
        clauses, params = [f"(? IS NULL OR {word}.language = ?)"], [self.language, self.language]
        if self.sources or self.difficulties:
            entry_clauses = [f"em.meaning_id = {meaning}.id"]
            if self.sources:
                entry_clauses.append(f"s.name IN ({', '.join('?' for _ in self.sources)})")
                params += self.sources
            if self.difficulties:
                entry_clauses.append(f"e.difficulty IN ({', '.join('?' for _ in self.difficulties)})")
                params += self.difficulties
            clauses.append(
                "EXISTS (SELECT 1 FROM entry_meanings em JOIN entries e ON e.id = em.entry_id"
                f" JOIN sources s ON s.id = e.source_id WHERE {' AND '.join(entry_clauses)})"
            )
        return " AND ".join(clauses), tuple(params)


@dataclass
class ImportedEntry:
    """1回の取り込みで見出し (単語×出典×レベル) に含まれていた品詞・意味・例文"""
    part_ids: set[int] = field(default_factory=set)
    meaning_ids: set[int] = field(default_factory=set)
    sentences: set[str] = field(default_factory=set)


class ClozeSentence(NamedTuple):
    prompt: str
    # 穴の語の形 (英語の past / participle など)。判定できなければ None
    form: str | None
    surface: str
    # 例文が属する意味。旧形式のJSONから取り込んだ例文は None
    meaning_id: int | None


@dataclass(frozen=True)
class Question:
    word_id: int
    question_type: str
    prompt: str
    choices: list[str]
    answer: str
    # 正解の補足表示 (穴埋めで活用形が正解のとき "petrified (petrify)" など)
    answer_detail: str = ""


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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language)
            );
            -- 見出し: 単語がどの出典のどのレベルに載っているか。同じ単語が A1 と A2 に別見出しで載ることがある
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, source_id INTEGER NOT NULL, difficulty TEXT NOT NULL DEFAULT '',
                UNIQUE(word_id, source_id, difficulty),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE, FOREIGN KEY (source_id) REFERENCES sources(id)
            );
            CREATE TABLE IF NOT EXISTS parts_of_speech (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, name TEXT NOT NULL,
                UNIQUE(word_id, name), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS meanings (
                id INTEGER PRIMARY KEY, part_of_speech_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL,
                forms TEXT, UNIQUE(part_of_speech_id, meaning_ja), FOREIGN KEY (part_of_speech_id) REFERENCES parts_of_speech(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS examples (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, sentence TEXT NOT NULL,
                translation_ja TEXT, targets TEXT, meaning_id INTEGER, UNIQUE(word_id, sentence),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (meaning_id) REFERENCES meanings(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS entry_meanings (
                entry_id INTEGER NOT NULL, meaning_id INTEGER NOT NULL, PRIMARY KEY (entry_id, meaning_id),
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
                FOREIGN KEY (meaning_id) REFERENCES meanings(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS entry_examples (
                entry_id INTEGER NOT NULL, example_id INTEGER NOT NULL, PRIMARY KEY (entry_id, example_id),
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
                FOREIGN KEY (example_id) REFERENCES examples(id) ON DELETE CASCADE
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
            CREATE TABLE IF NOT EXISTS derivations (
                word_id INTEGER NOT NULL, derived_word_id INTEGER NOT NULL, part_of_speech TEXT NOT NULL, entry_id INTEGER NOT NULL,
                PRIMARY KEY (entry_id, derived_word_id),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (derived_word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
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
        self._add_missing_columns()
        self._migrate_entries()
        self.connection.commit()

    def _migrate_entries(self) -> None:
        # 旧形式の words.source_id / difficulty を見出し (entries) に移す
        if "source_id" not in {row[1] for row in self.connection.execute("PRAGMA table_info(words)")}:
            return
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        if self.connection.execute("SELECT 1 FROM words WHERE source_id IS NULL").fetchone():
            self.connection.execute("INSERT OR IGNORE INTO sources(name) VALUES ('JSON')")
        # 類義語・派生語として名前だけ作られた単語 (レベルなし・例文なし・他の単語から参照) は見出しにしない
        self.connection.executescript(
            """
            INSERT OR IGNORE INTO entries(word_id, source_id, difficulty)
            SELECT w.id, COALESCE(w.source_id, (SELECT id FROM sources WHERE name = 'JSON')), COALESCE(w.difficulty, '')
            FROM words w
            WHERE w.difficulty IS NOT NULL
               OR EXISTS (SELECT 1 FROM examples x WHERE x.word_id = w.id)
               OR NOT (
                   EXISTS (SELECT 1 FROM derivations d WHERE d.derived_word_id = w.id)
                   OR EXISTS (
                       SELECT 1 FROM meaning_relations mr JOIN meanings m ON m.id = mr.related_meaning_id
                       JOIN parts_of_speech p ON p.id = m.part_of_speech_id WHERE p.word_id = w.id
                   )
               );
            INSERT OR IGNORE INTO entry_meanings(entry_id, meaning_id)
            SELECT e.id, m.id FROM entries e JOIN parts_of_speech p ON p.word_id = e.word_id JOIN meanings m ON m.part_of_speech_id = p.id;
            INSERT OR IGNORE INTO entry_examples(entry_id, example_id)
            SELECT e.id, x.id FROM entries e JOIN examples x ON x.word_id = e.word_id;

            CREATE TABLE new_derivations (
                word_id INTEGER NOT NULL, derived_word_id INTEGER NOT NULL, part_of_speech TEXT NOT NULL, entry_id INTEGER NOT NULL,
                PRIMARY KEY (entry_id, derived_word_id),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (derived_word_id) REFERENCES words(id) ON DELETE CASCADE,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );
            INSERT INTO new_derivations(word_id, derived_word_id, part_of_speech, entry_id)
            SELECT d.word_id, d.derived_word_id, d.part_of_speech, e.id FROM derivations d JOIN entries e ON e.word_id = d.word_id;
            DROP TABLE derivations;
            ALTER TABLE new_derivations RENAME TO derivations;

            CREATE TABLE new_words (
                id INTEGER PRIMARY KEY, word TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(word, language)
            );
            INSERT INTO new_words(id, word, language, created_at) SELECT id, word, language, created_at FROM words;
            DROP TABLE words;
            ALTER TABLE new_words RENAME TO words;
            """
        )
        self.connection.execute("PRAGMA foreign_keys = ON")

    def _add_missing_columns(self) -> None:
        for table, column, declaration in (
            ("meanings", "forms", "TEXT"),
            ("examples", "targets", "TEXT"),
            ("examples", "meaning_id", "INTEGER REFERENCES meanings(id) ON DELETE SET NULL"),
        ):
            columns = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        # 旧形式の gender / plural 列は forms (JSON) に移して削除する
        meaning_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(meanings)")}
        legacy_columns = [column for column in ("gender", "plural") if column in meaning_columns]
        if not legacy_columns:
            return
        for row in self.connection.execute(f"SELECT id, {', '.join(legacy_columns)} FROM meanings WHERE forms IS NULL").fetchall():
            forms = {column: row[column] for column in legacy_columns if row[column]}
            if forms:
                self.connection.execute("UPDATE meanings SET forms=? WHERE id=?", (json.dumps(forms, ensure_ascii=False), row["id"]))
        for column in legacy_columns:
            self.connection.execute(f"ALTER TABLE meanings DROP COLUMN {column}")

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

        # 同じファイルに同じ見出しが複数あってもまとめて扱えるよう、不要になった意味などの削除は最後に行う
        imported: dict[int, ImportedEntry] = {}
        try:
            for entry in entries:
                self._import_entry(entry, imported)
            for entry_id, contents in imported.items():
                self._prune_entry(entry_id, contents)
        except Exception:
            self.connection.rollback()
            raise
        self.connection.commit()
        return len(entries)

    def _import_entry(self, entry: dict, imported: dict[int, ImportedEntry]) -> None:
        # JSONの見出し (単語×出典×レベル) を正とし、JSONから消えた意味・例文・関連はDBからも消す。
        # 同じ単語でも別の出典・レベルの見出しにだけある意味は残す
        word = entry.get("word")
        if not isinstance(word, str) or not word.strip():
            raise ValueError("各単語データには word が必要です")
        word = word.strip()
        language = entry.get("language", "English")
        source = entry.get("source", "JSON")
        difficulty = entry.get("difficulty") or ""
        if not isinstance(language, str) or not isinstance(source, str) or not isinstance(difficulty, str):
            raise ValueError("language・source・difficulty は文字列にしてください")
        difficulty = difficulty.strip()

        self.connection.execute("INSERT OR IGNORE INTO sources(name) VALUES (?)", (source,))
        source_id = self.connection.execute("SELECT id FROM sources WHERE name=?", (source,)).fetchone()[0]
        word_id = self._ensure_word(word, language)
        self.connection.execute(
            "INSERT OR IGNORE INTO entries(word_id, source_id, difficulty) VALUES (?, ?, ?)", (word_id, source_id, difficulty)
        )
        entry_id = self.connection.execute(
            "SELECT id FROM entries WHERE word_id=? AND source_id=? AND difficulty=?", (word_id, source_id, difficulty)
        ).fetchone()[0]
        if entry_id not in imported:
            self._clear_outgoing_links(entry_id)
            imported[entry_id] = ImportedEntry()
        contents = imported[entry_id]

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
            part_name = part_name.strip()
            part_id = self._ensure_part_of_speech(word_id, part_name)
            contents.part_ids.add(part_id)

            for meaning in meanings:
                if isinstance(meaning, str):
                    meaning = {"meaning_ja": meaning}
                if not isinstance(meaning, dict) or not isinstance(meaning.get("meaning_ja"), str):
                    raise ValueError(f"{word}/{part_name}: meanings の各要素には meaning_ja が必要です")
                meaning_id = self._ensure_meaning(part_id, meaning["meaning_ja"].strip())
                contents.meaning_ids.add(meaning_id)
                self.connection.execute("INSERT OR IGNORE INTO entry_meanings(entry_id, meaning_id) VALUES (?, ?)", (entry_id, meaning_id))
                forms = {}
                for key in FORM_KEYS:
                    value = meaning.get(key)
                    if value in (None, ""):
                        continue
                    if not isinstance(value, str):
                        raise ValueError(f"{word}/{part_name}: {key} は文字列にしてください")
                    forms[key] = value.strip()
                if forms.get("gender") not in (None, *GENDERS):
                    raise ValueError(f"{word}/{part_name}: gender は {'/'.join(GENDERS)} のいずれかにしてください")
                self.connection.execute(
                    "UPDATE meanings SET forms=? WHERE id=?",
                    (json.dumps(forms, ensure_ascii=False) if forms else None, meaning_id),
                )
                self._import_meaning_relations(meaning_id, meaning, language, part_name)

                examples = meaning.get("examples", [])
                if not isinstance(examples, list):
                    raise ValueError(f"{word}/{part_name}: examples は配列にしてください")
                for example in examples:
                    contents.sentences.add(self._insert_example(word, word_id, entry_id, example, meaning_id))

        examples = entry.get("examples", []) if legacy_format else []
        if not isinstance(examples, list):
            raise ValueError(f"{word}: examples は配列にしてください")
        for example in examples:
            contents.sentences.add(self._insert_example(word, word_id, entry_id, example))

        for derivative in normalize_derivatives(entry):
            derived_word_id, _ = self._ensure_word_meaning(
                derivative["word"], language, derivative["part_of_speech"], derivative.get("meaning_ja")
            )
            if derived_word_id != word_id:
                self.connection.execute(
                    "INSERT OR IGNORE INTO derivations(word_id, derived_word_id, part_of_speech, entry_id) VALUES (?, ?, ?, ?)",
                    (word_id, derived_word_id, derivative["part_of_speech"], entry_id),
                )

    def _clear_outgoing_links(self, entry_id: int) -> None:
        self.connection.execute(
            "DELETE FROM meaning_relations WHERE meaning_id IN (SELECT meaning_id FROM entry_meanings WHERE entry_id=?)", (entry_id,)
        )
        self.connection.execute("DELETE FROM derivations WHERE entry_id=?", (entry_id,))

    def _prune_entry(self, entry_id: int, contents: ImportedEntry) -> None:
        # 見出しから外れた意味・例文のひも付けを外し、どの見出しにも属さなくなったものを消す。
        # 類義語・派生語として名前だけ作られた意味 (見出しなし) もここで消える
        word_id = self.connection.execute("SELECT word_id FROM entries WHERE id=?", (entry_id,)).fetchone()[0]
        meaning_ids, sentences = tuple(contents.meaning_ids), tuple(contents.sentences)
        self.connection.execute(
            f"DELETE FROM entry_meanings WHERE entry_id=? AND meaning_id NOT IN ({', '.join('?' for _ in meaning_ids)})",
            (entry_id, *meaning_ids),
        )
        self.connection.execute(
            f"""
            DELETE FROM entry_examples WHERE entry_id=?
              AND example_id IN (SELECT id FROM examples WHERE sentence NOT IN ({', '.join('?' for _ in sentences)}))
            """,
            (entry_id, *sentences),
        )
        # 他の単語の類義語・対義語として参照されている意味は残す
        self.connection.execute(
            """
            DELETE FROM meanings
            WHERE part_of_speech_id IN (SELECT id FROM parts_of_speech WHERE word_id=?)
              AND id NOT IN (SELECT meaning_id FROM entry_meanings)
              AND id NOT IN (SELECT related_meaning_id FROM meaning_relations)
            """,
            (word_id,),
        )
        self.connection.execute(
            f"""
            DELETE FROM parts_of_speech
            WHERE word_id=? AND id NOT IN ({", ".join("?" for _ in contents.part_ids)})
              AND NOT EXISTS (SELECT 1 FROM meanings WHERE part_of_speech_id = parts_of_speech.id)
            """,
            (word_id, *contents.part_ids),
        )
        self.connection.execute(
            "DELETE FROM examples WHERE word_id=? AND id NOT IN (SELECT example_id FROM entry_examples)", (word_id,)
        )

    def _insert_example(self, word: str, word_id: int, entry_id: int, example: object, meaning_id: int | None = None) -> str:
        if not isinstance(example, dict) or not isinstance(example.get("sentence"), str):
            raise ValueError(f"{word}: examples の各要素には sentence が必要です")
        targets = example.get("targets")
        if targets is not None and (not isinstance(targets, list) or not all(isinstance(target, str) and target for target in targets)):
            raise ValueError(f"{word}: targets は文字列の配列にしてください")
        sentence = example["sentence"].strip()
        self.connection.execute(
            """
            INSERT INTO examples(word_id, sentence, translation_ja, targets, meaning_id) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(word_id, sentence) DO UPDATE SET
                translation_ja=excluded.translation_ja, targets=excluded.targets, meaning_id=excluded.meaning_id
            """,
            (word_id, sentence, example.get("translation_ja"), json.dumps(targets, ensure_ascii=False) if targets else None, meaning_id),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO entry_examples(entry_id, example_id) SELECT ?, id FROM examples WHERE word_id=? AND sentence=?",
            (entry_id, word_id, sentence),
        )
        return sentence

    def _ensure_word(self, word: str, language: str) -> int:
        self.connection.execute("INSERT OR IGNORE INTO words (word, language) VALUES (?, ?)", (word, language))
        return self.connection.execute("SELECT id FROM words WHERE word=? AND language=?", (word, language)).fetchone()[0]

    def _ensure_part_of_speech(self, word_id: int, name: str) -> int:
        self.connection.execute("INSERT OR IGNORE INTO parts_of_speech(word_id, name) VALUES (?, ?)", (word_id, name))
        return self.connection.execute("SELECT id FROM parts_of_speech WHERE word_id=? AND name=?", (word_id, name)).fetchone()[0]

    def _ensure_meaning(self, part_id: int, meaning_ja: str) -> int:
        self.connection.execute("INSERT OR IGNORE INTO meanings(part_of_speech_id, meaning_ja) VALUES (?, ?)", (part_id, meaning_ja))
        return self.connection.execute(
            "SELECT id FROM meanings WHERE part_of_speech_id=? AND meaning_ja=?", (part_id, meaning_ja)
        ).fetchone()[0]

    def _ensure_word_meaning(self, word: str, language: str, part_of_speech: str, meaning_ja: str | None) -> tuple[int, int | None]:
        # 類義語・派生語として名前だけ登録する。見出しにはしないので、出典・レベルで絞り込んだ出題には出ない
        word_id = self._ensure_word(word, language)
        part_id = self._ensure_part_of_speech(word_id, part_of_speech)
        return word_id, self._ensure_meaning(part_id, meaning_ja) if meaning_ja else None

    def _import_meaning_relations(
        self,
        meaning_id: int,
        meaning: dict,
        language: str,
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
                _, related_meaning_id = self._ensure_word_meaning(
                    related_word.strip(), language, related_pos, related_meaning.strip()
                )
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
            UNION
            SELECT derived_word_id FROM derivations WHERE word_id=?
            UNION
            SELECT word_id FROM derivations WHERE derived_word_id=?
            """,
            (word_id, word_id, word_id, word_id),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _choice_exclusion_ids(self, word_id: int) -> tuple[int, ...]:
        return (word_id, *self._related_word_ids(word_id))

    def _choice_candidates(
        self, column: str, excluded_ids: tuple[int, ...], part_of_speech: str, answer: str, language: str, answer_word_id: int,
        required_part_of_speech: str | None = None,
    ) -> list[str]:
        # 同じ言語・同じ品詞の候補を優先し、足りない場合のみ他の品詞で補う
        placeholders = ", ".join("?" for _ in excluded_ids)
        rows = self.connection.execute(
            f"""
            SELECT {column} AS choice, MIN(w.id) AS word_id, GROUP_CONCAT(m.meaning_ja, char(31)) AS meanings
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE w.id NOT IN ({placeholders}) AND {column} != ? AND {HAS_REAL_MEANING} AND w.language = ?
              AND (? IS NULL OR p.name = ?)
            GROUP BY {column}
            ORDER BY MAX(p.name = ?) DESC, RANDOM()
            LIMIT ?
            """,
            (*excluded_ids, answer, language, required_part_of_speech, required_part_of_speech, part_of_speech, CHOICE_POOL_SIZE),
        ).fetchall()

        # 正解や他の選択肢とほぼ同じ意味の候補 (辛辣な / 辛らつな など) は、正解が複数になったり
        # 消去法で答えが分かったりするので避ける。互いに類義語・派生語の関係にある候補も避ける
        picked: list[sqlite3.Row] = []
        used_meanings = self._word_meanings(answer_word_id)
        related_ids: set[int] = set()
        for row in rows:
            meanings = row["meanings"].split(MEANING_SEPARATOR)
            if row["word_id"] in related_ids or any(meanings_similar(meaning, used) for meaning in meanings for used in used_meanings):
                continue
            picked.append(row)
            used_meanings += meanings
            related_ids.update(self._related_word_ids(row["word_id"]))
            if len(picked) == 3:
                break
        # 候補が少なくて足りないときは、条件を外して残りから補う
        for row in rows:
            if len(picked) == 3:
                break
            if row not in picked:
                picked.append(row)
        return [row["choice"] for row in picked]

    def _word_meanings(self, word_id: int) -> list[str]:
        rows = self.connection.execute(
            "SELECT m.meaning_ja FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id WHERE p.word_id=?",
            (word_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def _derivative_candidates(self, base: sqlite3.Row, answer: str, part_of_speech: str) -> list[str]:
        # 元の単語や別品詞の派生語を優先し、同じ品詞の派生語は正解が複数になるので除外する
        rows = self.connection.execute(
            """
            SELECT w.word
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            LEFT JOIN derivations d ON d.word_id = ? AND d.derived_word_id = w.id
            WHERE w.language = ? AND w.word != ?
              AND (d.part_of_speech IS NULL OR d.part_of_speech != ?)
            GROUP BY w.word
            ORDER BY MAX(w.id = ? OR d.word_id IS NOT NULL) DESC, MAX(p.name = ?) DESC, RANDOM()
            LIMIT 3
            """,
            (base["id"], base["language"], answer, part_of_speech, base["id"], part_of_speech),
        ).fetchall()
        return [row[0] for row in rows]

    def _cloze_sentences(self, language: str | None) -> dict[int, list[ClozeSentence]]:
        """穴埋めに使える例文を単語ごとに返す"""
        rows = self.connection.execute(
            """
            SELECT e.word_id, e.sentence, e.targets, e.meaning_id, w.word, w.language,
                (SELECT GROUP_CONCAT(m.forms, char(31))
                 FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id
                 WHERE p.word_id = w.id AND m.forms IS NOT NULL) AS forms
            FROM examples e
            JOIN words w ON w.id = e.word_id
            WHERE ? IS NULL OR w.language = ?
            """,
            (language, language),
        ).fetchall()
        sentences: dict[int, list[ClozeSentence]] = {}
        for row in rows:
            targets = json.loads(row["targets"]) if row["targets"] else None
            # 登録された複数形・活用形 (gemacht など) も穴にする語として使う
            language_spec = get_language(row["language"])
            extra_forms = [
                form for forms in (row["forms"].split("\x1f") if row["forms"] else ())
                for form in language_spec.cloze_forms(json.loads(forms))
            ]
            spans = find_cloze_spans(row["sentence"], row["word"], row["language"], targets, extra_forms)
            if not spans:
                continue
            start, end = spans[0]
            surface = row["sentence"][start:end]
            form = None
            if len(spans) == 1 and language_spec.classify_form:
                form = language_spec.classify_form(row["word"], surface, row["sentence"][:start])
            sentences.setdefault(row["word_id"], []).append(ClozeSentence(blank_out(row["sentence"], spans), form, surface, row["meaning_id"]))
        return sentences

    def _weighted_word(self, question_type: str, scope: QuizScope, join: str = "", where: str = "1") -> sqlite3.Row:
        scope_condition, scope_params = scope.condition()
        rows = self.connection.execute(
            f"""
            SELECT w.id, w.word, w.language, p.name AS part_of_speech, m.id AS meaning_id, m.meaning_ja, m.forms,
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
            WHERE {where} AND {scope_condition}
            GROUP BY w.id, m.id
            ORDER BY RANDOM()
            """,
            (question_type, RECENT_HISTORY_LIMIT, *scope_params),
        ).fetchall()
        if not rows:
            raise ValueError("出題範囲の条件に合う単語データがありません")
        return random.choices(rows, weights=[1.0 + 5.0 * row["incorrect_rate"] for row in rows], k=1)[0]

    def languages(self) -> list[str]:
        """登録されている言語を単語数の多い順に返す"""
        rows = self.connection.execute("SELECT language FROM words GROUP BY language ORDER BY COUNT(*) DESC, language").fetchall()
        return [row[0] for row in rows]

    def scope_options(self, language: str) -> tuple[list[str], list[str]]:
        """その言語で選べる出典とレベルを返す"""
        sources = self.connection.execute(
            "SELECT DISTINCT s.name FROM entries e JOIN words w ON w.id = e.word_id JOIN sources s ON s.id = e.source_id"
            " WHERE w.language=? ORDER BY s.name",
            (language,),
        ).fetchall()
        difficulties = self.connection.execute(
            "SELECT DISTINCT e.difficulty FROM entries e JOIN words w ON w.id = e.word_id"
            " WHERE w.language=? AND e.difficulty != '' ORDER BY e.difficulty",
            (language,),
        ).fetchall()
        return [row[0] for row in sources], [row[0] for row in difficulties]

    def create_question(self, question_type: str, show_hint: bool = False, scope: QuizScope | None = None) -> Question:
        scope = scope or QuizScope()
        language = scope.language
        if question_type == "english_to_japanese":
            target = self._weighted_word(question_type, scope, where=HAS_REAL_MEANING)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["meaning_ja"], *self._choice_candidates("m.meaning_ja", excluded_ids, target["part_of_speech"], target["meaning_ja"], target["language"], target["id"])]
            random.shuffle(choices)
            prompt = get_language(target["language"]).headword(target["word"], json.loads(target["forms"] or "{}"))
            return Question(target["id"], question_type, prompt, choices, target["meaning_ja"])
        if question_type == "japanese_to_english":
            target = self._weighted_word(question_type, scope, where=HAS_REAL_MEANING)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"], target["language"], target["id"])]
            random.shuffle(choices)
            return Question(target["id"], question_type, target["meaning_ja"], choices, target["word"])
        if question_type == "cloze":
            sentences = self._cloze_sentences(language)
            if not sentences:
                raise ValueError("穴埋めに使える例文がありません")
            # 例文が属する意味を出題対象にし、品詞 (誤答の候補) とヒントをその意味に合わせる。
            # vent の動詞の例文で、名詞「通気孔」の品詞やヒントが使われないようにするため
            meaning_ids = {item.meaning_id for items in sentences.values() for item in items if item.meaning_id is not None}
            unlinked_word_ids = {word_id for word_id, items in sentences.items() if any(item.meaning_id is None for item in items)}
            target = self._weighted_word(
                question_type, scope,
                where=f"{HAS_REAL_MEANING} AND (m.id IN ({', '.join(map(str, meaning_ids)) or 'NULL'})"
                      f" OR w.id IN ({', '.join(map(str, unlinked_word_ids)) or 'NULL'}))",
            )
            prompt, form, surface, _ = random.choice([
                item for item in sentences[target["id"]] if item.meaning_id in (None, target["meaning_id"])
            ])
            if show_hint:
                prompt += f"\nヒント: {target['meaning_ja']}"
            excluded_ids = self._choice_exclusion_ids(target["id"])
            language_spec = get_language(target["language"])
            if form in (None, "base") or language_spec.inflect_to is None:
                choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"], target["language"], target["id"])]
                random.shuffle(choices)
                return Question(target["id"], question_type, prompt, choices, target["word"])

            # 穴の語が活用形 (petrified など) なら、選択肢も同じ形にそろえる。過去形などは動詞だけから選ぶ
            required_part_of_speech = language_spec.form_parts_of_speech.get(form)
            candidates = self._choice_candidates(
                "w.word", excluded_ids, required_part_of_speech or target["part_of_speech"], target["word"], target["language"], target["id"],
                required_part_of_speech,
            )
            answer = surface.lower()
            choices = [answer]
            for candidate in candidates:
                inflected = language_spec.inflect_to(candidate, form)
                if inflected not in choices:
                    choices.append(inflected)
            random.shuffle(choices)
            return Question(target["id"], question_type, prompt, choices, answer, f"{answer} ({target['word']})")
        if question_type == "relation":
            scope_condition, scope_params = scope.condition()
            relation = self.connection.execute(
                f"""
                SELECT mr.*
                FROM meaning_relations mr
                JOIN meanings m ON m.id = mr.meaning_id
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE {scope_condition}
                ORDER BY RANDOM() LIMIT 1
                """,
                scope_params,
            ).fetchone()
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
                SELECT w.id, w.word, w.language, p.name AS part_of_speech
                FROM meanings m
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE m.id=?
                """,
                (relation["related_meaning_id"],),
            ).fetchone()
            related = related_row["word"]
            choices = [related, *self._choice_candidates("w.word", (target["id"], related_row["id"]), related_row["part_of_speech"], related, related_row["language"], related_row["id"])]
            random.shuffle(choices)
            label = "類義語" if relation["relation_type"] == "synonym" else "対義語"
            return Question(target["id"], question_type, f"{target['word']} の{label}は?", choices, related)
        if question_type in ("romanization", "gender", "plural") and not get_language(language or "").supports(question_type):
            raise ValueError(f"{language} はこの問題形式に対応していません")
        if question_type == "gender":
            target = self._weighted_word(
                question_type, scope, where=f"json_extract(m.forms, '$.gender') IN ({', '.join(repr(code) for code in GENDERS)})"
            )
            articles = get_language(target["language"]).articles
            gender = json.loads(target["forms"])["gender"]
            # der / die / das は毎回同じ並びで出す
            return Question(
                target["id"], question_type, f"{target['word']} ({target['meaning_ja']})", list(articles.values()), articles[gender],
                f"{articles[gender]} {target['word']}",
            )
        if question_type == "plural":
            # 複数形なし (Ausland の "-" など) は出題しない
            target = self._weighted_word(
                question_type, scope,
                where=f"TRIM(json_extract(m.forms, '$.plural')) NOT IN ({', '.join(repr(value) for value in NO_PLURAL)})",
            )
            forms = json.loads(target["forms"])
            language_spec = get_language(target["language"])
            answer = forms["plural"].strip()
            choices = [answer, *language_spec.plural_distractors(target["word"], answer)]
            random.shuffle(choices)
            # 単数形には冠詞を付けて出す (die Abfahrt)。性が未登録なら単語だけ
            headword = language_spec.headword(target["word"], {"gender": forms["gender"]} if forms.get("gender") else {})
            return Question(target["id"], question_type, f"{headword} ({target['meaning_ja']}) の複数形は?", choices, answer)
        if question_type == "romanization":
            language_spec = get_language(language or "")
            target = self._weighted_word(question_type, scope, where=HAS_REAL_MEANING)
            answer = language_spec.romanize(target["word"])
            if answer is None:
                raise ValueError(f"{target['word']} を転写できません")
            choices = [answer, *language_spec.romanization_distractors(target["word"], answer)]
            # 誤答が作りにくい短い語 (하나 など) は、同じ言語の他の単語の転写で補う
            if len(choices) < 4:
                for row in self.connection.execute(
                    "SELECT word FROM words WHERE language=? AND id != ? ORDER BY RANDOM() LIMIT 20", (target["language"], target["id"])
                ):
                    other = language_spec.romanize(row["word"])
                    if other and other not in choices:
                        choices.append(other)
                    if len(choices) == 4:
                        break
            random.shuffle(choices)
            return Question(target["id"], question_type, f"{target['word']} ({target['meaning_ja']})", choices, answer)
        if question_type == "derivative":
            target = self._weighted_word(question_type, scope, "JOIN derivations d ON d.word_id = w.id", HAS_REAL_MEANING)
            derivation = self.connection.execute(
                """
                SELECT w.word, d.part_of_speech
                FROM derivations d JOIN words w ON w.id = d.derived_word_id
                WHERE d.word_id=? ORDER BY RANDOM() LIMIT 1
                """,
                (target["id"],),
            ).fetchone()
            answer = derivation["word"]
            choices = [answer, *self._derivative_candidates(target, answer, derivation["part_of_speech"])]
            random.shuffle(choices)
            prompt = f"{target['word']} ({target['part_of_speech']}) の{derivation['part_of_speech']}形は?"
            return Question(target["id"], question_type, prompt, choices, answer)
        raise ValueError(f"未対応の問題形式です: {question_type}")

    def record_answer(self, question: Question, selected_answer: str) -> bool:
        correct = selected_answer == question.answer
        self.connection.execute("INSERT INTO answer_history(word_id, question_type, selected_answer, correct_answer, is_correct) VALUES (?, ?, ?, ?, ?)", (question.word_id, question.question_type, selected_answer, question.answer, int(correct)))
        self.connection.commit()
        return correct

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

"""DB のテーブル作成と、古い形式の DB の移行。"""
from __future__ import annotations

import json


class SchemaMixin:
    """LexiconDatabase のテーブルの作成と移行"""

    def initialize(self) -> None:
        self._migrate_legacy_schema()
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            -- separable_prefix: 分離動詞の前つづり (JSON の見出し語 ab|fahren の ab)。word には印を外した abfahren を入れる
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY, word TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, separable_prefix TEXT, UNIQUE(word, language)
            );
            -- 見出し: 単語がどの出典のどのレベルに載っているか。同じ単語が A1 と A2 に別見出しで載ることがある
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, source_id INTEGER NOT NULL, difficulty TEXT NOT NULL DEFAULT '',
                UNIQUE(word_id, source_id, difficulty),
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE, FOREIGN KEY (source_id) REFERENCES sources(id)
            );
            -- 見出しがどのJSONファイルから取り込まれたか (教材フォルダからの相対パス)。出題範囲をファイルで選ぶのに使う
            CREATE TABLE IF NOT EXISTS entry_files (
                entry_id INTEGER NOT NULL, file TEXT NOT NULL, PRIMARY KEY (entry_id, file),
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS parts_of_speech (
                id INTEGER PRIMARY KEY, word_id INTEGER NOT NULL, name TEXT NOT NULL,
                UNIQUE(word_id, name), FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS meanings (
                id INTEGER PRIMARY KEY, part_of_speech_id INTEGER NOT NULL, meaning_ja TEXT NOT NULL,
                forms TEXT, valency TEXT, UNIQUE(part_of_speech_id, meaning_ja), FOREIGN KEY (part_of_speech_id) REFERENCES parts_of_speech(id) ON DELETE CASCADE
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
        # words を作り直す移行のあとで列を足す
        self._migrate_entries()
        self._add_missing_columns()
        # 出題範囲の絞り込み (意味 → 見出し) と回答履歴の集計で引く列。移行で表を作り直したあとに作る
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS entry_meanings_meaning ON entry_meanings(meaning_id);
            CREATE INDEX IF NOT EXISTS entry_examples_example ON entry_examples(example_id);
            CREATE INDEX IF NOT EXISTS entries_word ON entries(word_id);
            CREATE INDEX IF NOT EXISTS derivations_word ON derivations(word_id);
            CREATE INDEX IF NOT EXISTS derivations_derived_word ON derivations(derived_word_id);
            CREATE INDEX IF NOT EXISTS meaning_relations_related ON meaning_relations(related_meaning_id);
            CREATE TABLE IF NOT EXISTS known_words (
                language TEXT NOT NULL, word TEXT NOT NULL, known INTEGER NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (language, word)
            );
            CREATE INDEX IF NOT EXISTS answer_history_type_word ON answer_history(question_type, word_id, id);
            CREATE INDEX IF NOT EXISTS answer_history_word ON answer_history(word_id);
            UPDATE answer_history SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS answer_history_uid ON answer_history(uid);
            """
        )
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
            # 格支配 (["Akk", "von+Dat"] の JSON)
            ("meanings", "valency", "TEXT"),
            ("examples", "targets", "TEXT"),
            ("examples", "meaning_id", "INTEGER REFERENCES meanings(id) ON DELETE SET NULL"),
            ("words", "separable_prefix", "TEXT"),
            # 問題の種類の内訳 (活用クイズの人称・時制) の JSON
            ("answer_history", "variant", "TEXT"),
            # 格支配クイズ用の例文の穴 ({"slot": "von+Dat", "phrase": "vom Bahnhof", "hint": "der Bahnhof"} の JSON)
            ("examples", "valency", "TEXT"),
            # 回答の一意なID。端末 (Mac と iPad) の間で回答履歴を同期するときに、同じ回答を二重に入れないために使う
            ("answer_history", "uid", "TEXT"),
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

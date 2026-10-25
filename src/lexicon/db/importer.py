"""教材JSONの取り込み。JSONの見出し (単語×出典×レベル) を正として、DBの品詞・意味・例文・関連語を作り直す。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from lexicon.languages import FORM_KEYS, GENDERS, get_language, split_separable
from lexicon.db.text import valency_preposition


LEGACY_POS_NAMES = {"noun": "名詞", "verb": "動詞", "adjective": "形容詞", "adverb": "副詞"}


def normalize_derivatives(entry: dict) -> list[dict]:
    """derivatives と旧形式の related_words ({"noun": "x"} など) を {word, part_of_speech, meaning_ja?, ...} の配列にそろえる"""
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
        # 派生語は「単語と品詞の付いた意味」。meaning_ja のほかに語形・類義語・例文なども意味と同じキーで書ける
        derivative = {**item, "word": item["word"].strip(), "part_of_speech": item["part_of_speech"].strip()}
        meaning_ja = derivative.pop("meaning_ja", None)
        if isinstance(meaning_ja, str) and meaning_ja.strip():
            derivative["meaning_ja"] = meaning_ja.strip()
        elif len(derivative) > 2:
            raise ValueError(f"{word}: 派生語 {derivative['word']} に例文などを付けるときは meaning_ja も書いてください")
        normalized.append(derivative)
    return normalized


@dataclass
class ImportedEntry:
    """1回の取り込みで見出し (単語×出典×レベル) に含まれていた品詞・意味・例文。
    meaning_ids には派生語の意味も含む (派生語は派生元の見出しの出典・レベル・ファイルで出題範囲に入る)"""
    part_ids: set[int] = field(default_factory=set)
    meaning_ids: set[int] = field(default_factory=set)
    sentences: set[str] = field(default_factory=set)


class ImportMixin:
    """LexiconDatabase の教材JSONの取り込み"""

    def import_json(self, json_path: Path, file_label: str | None = None) -> int:
        """JSONを取り込む。file_label は出題範囲の「ファイル」に出す名前 (省略時はファイル名)"""
        file_label = file_label or json_path.name
        with json_path.open(encoding="utf-8") as file:
            payload = json.load(file)

        entries = [payload] if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("JSONのルートは単語オブジェクトまたは単語オブジェクトの配列にしてください")

        # 同じファイルに同じ見出しが複数あってもまとめて扱えるよう、不要になった意味などの削除は最後に行う
        imported: dict[int, ImportedEntry] = {}
        try:
            # ファイルから消えた見出しのひも付けも外れるよう、このファイルの分は作り直す
            self.connection.execute("DELETE FROM entry_files WHERE file=?", (file_label,))
            for entry in entries:
                self._import_entry(entry, imported, file_label)
            for entry_id, contents in imported.items():
                self._prune_entry(entry_id, contents)
        except Exception:
            self.connection.rollback()
            raise
        self.connection.commit()
        return len(entries)

    def _import_entry(self, entry: dict, imported: dict[int, ImportedEntry], file_label: str = "") -> None:
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
        if file_label:
            self.connection.execute("INSERT OR IGNORE INTO entry_files(entry_id, file) VALUES (?, ?)", (entry_id, file_label))
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
                self._import_meaning(word, word_id, entry_id, part_id, part_name, meaning, language, contents)

        examples = entry.get("examples", []) if legacy_format else []
        if not isinstance(examples, list):
            raise ValueError(f"{word}: examples は配列にしてください")
        for example in examples:
            contents.sentences.add(self._insert_example(word, word_id, entry_id, example))

        for derivative in normalize_derivatives(entry):
            derived_word = derivative["word"]
            derived_word_id = self._ensure_word(derived_word, language)
            derived_part_id = self._ensure_part_of_speech(derived_word_id, derivative["part_of_speech"])
            # 派生語の意味も派生元の見出しにひも付け、出典・レベル・ファイルを共通にする。例文・類義語・語形は見出し語の意味と同じ形で書ける
            if derivative.get("meaning_ja"):
                self._import_meaning(
                    derived_word, derived_word_id, entry_id, derived_part_id, derivative["part_of_speech"], derivative, language, contents,
                    derivative=True,
                )
            if derived_word_id != word_id:
                self.connection.execute(
                    "INSERT OR IGNORE INTO derivations(word_id, derived_word_id, part_of_speech, entry_id) VALUES (?, ?, ?, ?)",
                    (word_id, derived_word_id, derivative["part_of_speech"], entry_id),
                )

    def _import_meaning(
        self, word: str, word_id: int, entry_id: int, part_id: int, part_name: str, meaning: dict, language: str, contents: ImportedEntry,
        derivative: bool = False,
    ) -> None:
        """意味を1つ取り込み、見出しにひも付ける。派生語の意味は word・word_id が派生語で、entry_id が派生元の見出し。
        派生語の意味は派生語自身の見出しにもあることがあるので、語形・格支配は書いてあるときだけ上書きする"""
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
        if forms or not derivative:
            self.connection.execute(
                "UPDATE meanings SET forms=? WHERE id=?",
                (json.dumps(forms, ensure_ascii=False) if forms else None, meaning_id),
            )
        valency = meaning.get("valency") or []
        if valency:
            language_spec = get_language(language)
            if not language_spec.cases:
                raise ValueError(f"{word}/{part_name}: {language} では valency (格支配) を使えません")
            try:
                valency = language_spec.parse_valency(valency)
            except ValueError as error:
                raise ValueError(f"{word}/{part_name}: {error}") from None
        if valency or not derivative:
            self.connection.execute(
                "UPDATE meanings SET valency=? WHERE id=?", (json.dumps(valency, ensure_ascii=False) if valency else None, meaning_id)
            )
        self._import_meaning_relations(meaning_id, meaning, language, part_name)

        examples = meaning.get("examples", [])
        if not isinstance(examples, list):
            raise ValueError(f"{word}/{part_name}: examples は配列にしてください")
        for example in examples:
            contents.sentences.add(self._insert_example(word, word_id, entry_id, example, meaning_id, language, part_name))

    def _clear_outgoing_links(self, entry_id: int) -> None:
        # 見出し語自身の意味と、派生語の意味の類義語を消す (取り込み直す)。
        # ただし派生語自身の見出しにもある意味の類義語は、その見出しのものなので残す
        self.connection.execute(
            """
            DELETE FROM meaning_relations WHERE meaning_id IN (
                SELECT em.meaning_id FROM entry_meanings em JOIN meanings m ON m.id = em.meaning_id
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id JOIN entries e ON e.id = em.entry_id
                WHERE em.entry_id=? AND (p.word_id = e.word_id OR NOT EXISTS (
                    SELECT 1 FROM entry_meanings own JOIN entries own_entry ON own_entry.id = own.entry_id
                    WHERE own.meaning_id = em.meaning_id AND own_entry.word_id = p.word_id
                ))
            )
            """,
            (entry_id,),
        )
        self.connection.execute("DELETE FROM derivations WHERE entry_id=?", (entry_id,))

    def _prune_entry(self, entry_id: int, contents: ImportedEntry) -> None:
        # 見出しから外れた意味・例文のひも付けを外し、どの見出しにも属さなくなったものを消す。
        # 類義語・派生語として名前だけ作られた意味 (見出しなし) もここで消える
        word_id = self.connection.execute("SELECT word_id FROM entries WHERE id=?", (entry_id,)).fetchone()[0]
        meaning_ids, sentences = tuple(contents.meaning_ids), tuple(contents.sentences)
        # 見出し語と、この見出しに意味がひも付いている (いた) 派生語。外れた意味をあとで消す
        word_ids = {word_id} | {
            row[0] for row in self.connection.execute(
                "SELECT DISTINCT p.word_id FROM meanings m JOIN parts_of_speech p ON p.id = m.part_of_speech_id"
                f" WHERE m.id IN (SELECT meaning_id FROM entry_meanings WHERE entry_id=?) OR m.id IN ({', '.join('?' for _ in meaning_ids)})",
                (entry_id, *meaning_ids),
            )
        }
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
            f"""
            DELETE FROM meanings
            WHERE part_of_speech_id IN (SELECT id FROM parts_of_speech WHERE word_id IN ({', '.join('?' for _ in word_ids)}))
              AND id NOT IN (SELECT meaning_id FROM entry_meanings)
              AND id NOT IN (SELECT related_meaning_id FROM meaning_relations)
            """,
            tuple(word_ids),
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
            f"DELETE FROM examples WHERE word_id IN ({', '.join('?' for _ in word_ids)}) AND id NOT IN (SELECT example_id FROM entry_examples)",
            tuple(word_ids),
        )

    def _example_valency(self, word: str, language: str, sentence: str, valency: object, part_of_speech: str = "") -> str | None:
        """例文の格支配の穴を検証して JSON にする。穴の句は、ヒントから作った正しい形のどれかと一致しなければならない"""
        if valency is None:
            return None
        if not isinstance(valency, dict) or not all(isinstance(valency.get(key), str) and valency[key].strip() for key in ("slot", "phrase", "hint")):
            raise ValueError(f"{word}: 例文の valency には slot・phrase・hint が必要です")
        spec = get_language(language)
        if spec.valency_answers is None:
            raise ValueError(f"{word}: {language} では例文の valency を使えません")
        slot = spec.parse_valency([valency["slot"]])[0]
        phrase = valency["phrase"].strip()
        if phrase not in sentence:
            raise ValueError(f"{word}: 例文に「{phrase}」がありません: {sentence}")
        answers = spec.valency_answers(slot, valency["hint"].strip(), valency_preposition(word, part_of_speech))
        if phrase.casefold() not in {answer.casefold() for answer in answers}:
            raise ValueError(f"{word}: 「{phrase}」は {slot} ({valency['hint']}) の形 ({' / '.join(answers)}) と一致しません: {sentence}")
        return json.dumps({"slot": slot, "phrase": phrase, "hint": valency["hint"].strip()}, ensure_ascii=False)

    def _insert_example(
        self, word: str, word_id: int, entry_id: int, example: object, meaning_id: int | None = None, language: str = "", part_of_speech: str = "",
    ) -> str:
        if not isinstance(example, dict) or not isinstance(example.get("sentence"), str):
            raise ValueError(f"{word}: examples の各要素には sentence が必要です")
        targets = example.get("targets")
        if targets is not None and (not isinstance(targets, list) or not all(isinstance(target, str) and target for target in targets)):
            raise ValueError(f"{word}: targets は文字列の配列にしてください")
        sentence = example["sentence"].strip()
        self.connection.execute(
            """
            INSERT INTO examples(word_id, sentence, translation_ja, targets, meaning_id, valency) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(word_id, sentence) DO UPDATE SET
                translation_ja=excluded.translation_ja, targets=excluded.targets, meaning_id=excluded.meaning_id, valency=excluded.valency
            """,
            (
                word_id, sentence, example.get("translation_ja"), json.dumps(targets, ensure_ascii=False) if targets else None, meaning_id,
                self._example_valency(word, language, sentence, example.get("valency"), part_of_speech),
            ),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO entry_examples(entry_id, example_id) SELECT ?, id FROM examples WHERE word_id=? AND sentence=?",
            (entry_id, word_id, sentence),
        )
        return sentence

    def _ensure_word(self, word: str, language: str) -> int:
        # 分離動詞の印 (ab|fahren) は外して登録し、前つづりを別に覚える。印のない書き方で登録されても前つづりは消さない
        word, prefix = split_separable(word)
        self.connection.execute("INSERT OR IGNORE INTO words (word, language) VALUES (?, ?)", (word, language))
        word_id = self.connection.execute("SELECT id FROM words WHERE word=? AND language=?", (word, language)).fetchone()[0]
        if prefix:
            self.connection.execute("UPDATE words SET separable_prefix=? WHERE id=?", (prefix, word_id))
        return word_id

    def _ensure_part_of_speech(self, word_id: int, name: str) -> int:
        self.connection.execute("INSERT OR IGNORE INTO parts_of_speech(word_id, name) VALUES (?, ?)", (word_id, name))
        return self.connection.execute("SELECT id FROM parts_of_speech WHERE word_id=? AND name=?", (word_id, name)).fetchone()[0]

    def _ensure_meaning(self, part_id: int, meaning_ja: str) -> int:
        self.connection.execute("INSERT OR IGNORE INTO meanings(part_of_speech_id, meaning_ja) VALUES (?, ?)", (part_id, meaning_ja))
        return self.connection.execute(
            "SELECT id FROM meanings WHERE part_of_speech_id=? AND meaning_ja=?", (part_id, meaning_ja)
        ).fetchone()[0]

    def _ensure_word_meaning(self, word: str, language: str, part_of_speech: str, meaning_ja: str | None) -> tuple[int, int | None]:
        # 類義語・対義語として名前だけ登録する。見出しにはしない
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

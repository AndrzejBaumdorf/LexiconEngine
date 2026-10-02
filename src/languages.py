"""言語ごとの仕様。新しい言語や言語固有の項目はここに追加する。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import english


VOWELS = "aeiou"
GERMAN_ENDINGS = ("", "e", "en", "n", "s", "es", "er", "em", "ern", "st", "t", "et", "te", "ten", "test", "tet", "end")
GENDER_NAMES = {"m": "男性", "f": "女性", "n": "中性"}


def _english_forms(word: str) -> set[str]:
    forms = {word, word + "s", word + "es", word + "ed", word + "d", word + "ing", word + "er", word + "est"}
    if len(word) > 2 and word.endswith("y") and word[-2] not in VOWELS:
        stem = word[:-1]
        forms |= {stem + "ies", stem + "ied", stem + "ier", stem + "iest"}
    if word.endswith("ie"):
        forms.add(word[:-2] + "ying")
    elif word.endswith("e"):
        forms.add(word[:-1] + "ing")
    if word.endswith("ic"):
        forms |= {word + "ked", word + "king"}
    # commit → committed のような子音字の重ね
    if len(word) > 2 and word[-1] not in VOWELS + "wxy" and word[-2] in VOWELS and word[-3] not in VOWELS:
        forms |= {word + word[-1] + suffix for suffix in ("ed", "ing", "er", "est")}
    return forms


def _german_forms(word: str) -> set[str]:
    stems = {word}
    for suffix in ("en", "n"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            stems.add(word[: -len(suffix)])
            break
    forms = {stem + ending for stem in stems for ending in GERMAN_ENDINGS}
    forms |= {"ge" + stem + ending for stem in stems if stem != word for ending in ("t", "et")}
    return forms


@dataclass(frozen=True)
class FormField:
    """品詞ごとに追加で登録する語形 (名詞の性・複数形、動詞の活用など)。JSONでは意味の下に key で保存する。"""
    key: str
    label: str
    # 例文穴埋めで活用形として使う語の位置。"hat gemacht" なら -1 で gemacht。None なら使わない
    cloze_token: int | None = 0
    # 出題時に見出し語の後ろへ付ける書式。None なら付けない
    headword_format: str | None = "{}"


@dataclass(frozen=True)
class Language:
    name: str
    aliases: tuple[str, ...] = ()
    # 問題形式名 (英→日 など) に使う1文字の略称。空なら name を使う
    short_name: str = ""
    parts_of_speech: tuple[str, ...] = ("名詞", "動詞", "形容詞", "副詞", "前置詞", "接続詞", "熟語")
    # 名詞の性 (コード → 冠詞)。key が gender の欄の選択肢になる
    articles: dict[str, str] = field(default_factory=dict)
    # 品詞 → その品詞のときだけエディタに出す語形の欄
    form_fields: dict[str, tuple[FormField, ...]] = field(default_factory=dict)
    # 例文穴埋めで見出し語の活用形を推測する。小文字の見出し語を受け取る
    inflect: Callable[[str], set[str]] = lambda word: {word}
    # 例文穴埋めで選択肢の形を穴の語にそろえる。classify_form(原形, 穴の語, 穴より前の文) → 形の名前、
    # inflect_to(原形, 形の名前) → 活用した語。None ならそろえずに原形のまま出す
    classify_form: Callable[[str, str, str], str | None] | None = None
    inflect_to: Callable[[str, str], str] | None = None
    # その形にできる品詞 (過去形なら動詞だけ)。ここにない形は出題語と同じ品詞から選ぶ
    form_parts_of_speech: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.short_name or self.name

    @property
    def has_forms(self) -> bool:
        return bool(self.form_fields)

    @property
    def all_form_fields(self) -> tuple[FormField, ...]:
        return tuple(form_field for fields in self.form_fields.values() for form_field in fields)

    def fields_for(self, part_of_speech: str) -> tuple[FormField, ...]:
        return self.form_fields.get(part_of_speech.strip(), ())

    def gender_label(self, code: str) -> str:
        return f"{code} ({self.articles[code]}・{GENDER_NAMES[code]})" if code in self.articles else code

    def cloze_forms(self, forms: dict) -> list[str]:
        """登録された語形から、例文中で見出し語の代わりに穴にしてよい語を取り出す"""
        words = []
        for form_field in self.all_form_fields:
            tokens = str(forms.get(form_field.key) or "").split()
            if tokens and form_field.cloze_token is not None:
                words.append(tokens[form_field.cloze_token])
        return words

    def headword(self, word: str, forms: dict) -> str:
        gender = forms.get("gender")
        if gender:
            word = f"{self.articles[gender]} {word}" if gender in self.articles else f"{word} ({gender})"
        suffixes = [
            form_field.headword_format.format(forms[form_field.key])
            for form_field in self.all_form_fields
            if form_field.key != "gender" and form_field.headword_format and forms.get(form_field.key)
        ]
        return f"{word} ({' - '.join(suffixes)})" if suffixes else word


ENGLISH = Language(
    "English",
    ("英語",),
    "英",
    inflect=_english_forms,
    classify_form=english.classify,
    inflect_to=english.inflect,
    form_parts_of_speech={"past": "動詞", "participle": "動詞", "ing": "動詞"},
)
GERMAN = Language(
    "German",
    ("Deutsch", "ドイツ語"),
    "独",
    parts_of_speech=("名詞", "動詞", "形容詞", "副詞", "前置詞", "接続詞", "代名詞", "冠詞", "数詞", "熟語"),
    articles={"m": "der", "f": "die", "n": "das"},
    form_fields={
        "名詞": (
            FormField("gender", "性", cloze_token=None),
            FormField("plural", "複数形", headword_format="複数: {}"),
        ),
        "動詞": (
            FormField("present_3sg", "現在 3人称単数"),
            FormField("past", "過去形"),
            FormField("perfect", "現在完了", cloze_token=-1),
        ),
    },
    inflect=_german_forms,
)
LANGUAGES = (ENGLISH, GERMAN)
GENDERS = tuple(GENDER_NAMES)
# どの言語でも語形として扱うキー。エディタではこれらを意味の編集対象として扱う
FORM_KEYS = tuple(dict.fromkeys(form_field.key for language in LANGUAGES for form_field in language.all_form_fields))


def find_language(name: str) -> Language | None:
    name = name.strip().lower()
    return next((language for language in LANGUAGES if name in (language.name.lower(), *(alias.lower() for alias in language.aliases))), None)


def get_language(name: str) -> Language:
    # 未登録の言語は活用の推測も性もない最小限の仕様で扱う
    return find_language(name) or Language(name.strip())

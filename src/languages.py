"""言語ごとの仕様。新しい言語や言語固有の項目はここに追加する。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import english
import german
import korean


VOWELS = "aeiou"
GERMAN_ENDINGS = ("", "e", "en", "n", "s", "es", "er", "em", "ern", "st", "t", "et", "te", "ten", "test", "tet", "end")
GENDER_NAMES = {"m": "男性", "f": "女性", "n": "中性"}
# 複数形がないことを表す書き方 (Ausland など)。複数形クイズには出さない
NO_PLURAL = ("", "-", "—", "–", "なし")


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
    # ラテン文字転写テスト。romanize(見出し語) → 正しい転写 (転写できなければ None)、
    # romanization_distractors(見出し語, 正しい転写) → 誤答。None ならその言語では出題しない
    romanize: Callable[[str], str | None] | None = None
    romanization_distractors: Callable[[str, str], list[str]] | None = None
    # 分離動詞の zu 不定詞で前つづりと動詞の間に入る語 (ab|fahren → abzufahren)
    separable_infix: str = ""
    # 再帰動詞の見出し語の先頭に付く代名詞 (sich anmelden)。例文では mich / dich などに変わるので穴埋めでは無視する
    reflexive_pronoun: str = ""
    # 例文穴埋めで、複合語の後半 (Stellenangebot の angebot) も穴にしてよいか
    compound_words: bool = False
    # 複数形に付ける冠詞 (ドイツ語の die)。複数形クイズで答えの前に付けてもよい
    plural_article: str = ""
    # 入力形式の問題で、入力欄の横にボタンとして出す文字 (キーボードで打ちにくいウムラウトなど)
    special_characters: str = ""
    # 格支配 (valency) に使える格の名前。空ならその言語では格支配を扱わない
    cases: tuple[str, ...] = ()
    # 格支配クイズ。valency_answers(格支配の1要素, ヒント) → 正解として認める句。例文の穴の句の検証にも使う
    valency_answers: Callable[[str, str, str], list[str]] | None = None
    # 活用クイズ。conjugation_table(印を外した見出し語, 前つづり, 登録された語形) → 人称・時制ごとの正解。
    # conjugation_pronouns は答えの前に付けてもよい主語 (du fährst ab)
    conjugation_table: Callable[[str, str | None, dict, str | None], list] | None = None
    conjugation_pronouns: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def supports(self, question_type: str) -> bool:
        """言語固有の問題形式 (転写・性・複数形) を出題できるか"""
        if question_type == "romanization":
            return self.romanize is not None
        if question_type == "gender":
            return bool(self.articles)
        if question_type == "plural":
            return any(form_field.key == "plural" for form_field in self.all_form_fields)
        if question_type == "valency":
            return self.valency_answers is not None
        if question_type == "conjugation":
            return self.conjugation_table is not None
        return True

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

    def parse_valency(self, items: list[str] | str) -> list[str]:
        """格支配を ["Akk", "von+Dat", "sich+Akk"] の形にそろえる。"Akk, von + Dat" のような文字列も受け付ける。
        各要素は 格 / 前置詞+格 / sich+格。使えない書き方なら ValueError"""
        if isinstance(items, str):
            items = [item for item in re.split(r"[,，、]", items) if item.strip()]
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError("valency は文字列の配列にしてください")
        normalized = []
        for item in items:
            item = re.sub(r"\s*\+\s*", "+", item.strip())
            match = VALENCY_PATTERN.fullmatch(item)
            if not match or match["case"] not in self.cases:
                raise ValueError(f"格支配「{item}」は {' / '.join(self.cases)}、前置詞+格 (von+Dat)、sich+格 の形で書いてください")
            normalized.append(item)
        return normalized

    @staticmethod
    def format_valency(items: list[str]) -> str:
        """表示用。["Akk", "von+Dat", "sich+Akk"] → + Akk, von + Dat, sich (Akk)"""
        shown = []
        for item in items:
            head, _, case = item.rpartition("+")
            shown.append(f"+ {case}" if not head else f"sich ({case})" if head == "sich" else f"{head} + {case}")
        return ", ".join(shown)

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
    plural_article="die",
    cases=("Nom", "Akk", "Dat", "Gen"),
    valency_answers=german.valency_answers,
    conjugation_table=german.conjugation_table,
    conjugation_pronouns=german.PERSON_PRONOUNS,
    separable_infix="zu",
    reflexive_pronoun="sich",
    compound_words=True,
    special_characters="äöüßÄÖÜ",
)
KOREAN = Language(
    "Korean",
    ("한국어", "韓国語"),
    "韓",
    parts_of_speech=("名詞", "代名詞", "数詞", "動詞", "形容詞", "存在詞", "副詞", "冠形詞", "感動詞", "接続詞", "熟語"),
    romanize=korean.romanize,
    romanization_distractors=korean.romanization_distractors,
)
LANGUAGES = (ENGLISH, GERMAN, KOREAN)
GENDERS = tuple(GENDER_NAMES)
# どの言語でも語形として扱うキー。エディタではこれらを意味の編集対象として扱う
FORM_KEYS = tuple(dict.fromkeys(form_field.key for language in LANGUAGES for form_field in language.all_form_fields))


SEPARABLE_MARK = "|"
# 格支配の1要素: 格 / 前置詞+格 / sich+格
VALENCY_PATTERN = re.compile(r"(?:(?P<head>[^\W\d_]+)\+)?(?P<case>[A-Z][a-z]+)")


def split_separable(word: str) -> tuple[str, str | None]:
    """分離動詞の印を外す。"ab|fahren" → ("abfahren", "ab")、"sich an|melden" → ("sich anmelden", "an")"""
    *head, last = word.strip().split(" ")
    if SEPARABLE_MARK not in last:
        return word.strip(), None
    prefix, stem = last.split(SEPARABLE_MARK, 1)
    return " ".join([*head, prefix + stem]), prefix or None


def mark_separable(word: str, prefix: str | None) -> str:
    """split_separable の逆。前つづりの後ろに印を付ける ("abfahren", "ab") → ab|fahren"""
    *head, last = word.split(" ")
    if not prefix or not last.startswith(prefix) or len(last) == len(prefix):
        return word
    return " ".join([*head, prefix + SEPARABLE_MARK + last[len(prefix):]])


def find_language(name: str) -> Language | None:
    name = name.strip().lower()
    return next((language for language in LANGUAGES if name in (language.name.lower(), *(alias.lower() for alias in language.aliases))), None)


def get_language(name: str) -> Language:
    # 未登録の言語は活用の推測も性もない最小限の仕様で扱う
    return find_language(name) or Language(name.strip())

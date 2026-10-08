"""ドイツ語の名詞句の格変化 (格支配クイズ用) と動詞の活用 (活用クイズ用)。

定冠詞 + 名詞の4格・3格・2格と、前置詞との融合形 (an dem → am) を作り、
格支配クイズの例文 (AI生成) の穴の句がヒントの名詞から作った正しい形と一致するかを確かめる。
活用は登録された3つの形 (3人称単数現在・過去・現在完了) から全人称を作り、規則で決まらない形は作らない。
"""
from __future__ import annotations

import re
from typing import NamedTuple

DEFINITE_ARTICLES = {
    # 性 → 格 → 冠詞。"pl" は複数
    "m": {"Nom": "der", "Akk": "den", "Dat": "dem", "Gen": "des"},
    "f": {"Nom": "die", "Akk": "die", "Dat": "der", "Gen": "der"},
    "n": {"Nom": "das", "Akk": "das", "Dat": "dem", "Gen": "des"},
    "pl": {"Nom": "die", "Akk": "die", "Dat": "den", "Gen": "der"},
}
# 前置詞 + 定冠詞の融合形。どちらで答えても正解にし、表示は融合形を使う
CONTRACTIONS = {
    ("an", "dem"): "am", ("an", "das"): "ans", ("bei", "dem"): "beim", ("in", "dem"): "im", ("in", "das"): "ins",
    ("von", "dem"): "vom", ("zu", "dem"): "zum", ("zu", "der"): "zur", ("auf", "das"): "aufs", ("für", "das"): "fürs", ("um", "das"): "ums",
}
GENITIVE_ES_ENDINGS = ("s", "ß", "x", "z", "sch", "tz")
ARTICLE_GENDERS = {"der": "m", "die": "f", "das": "n"}
# 弱変化 (den Jungen, dem Studenten) になりやすい男性名詞。この仕組みでは形を作れないのでヒントに使わない
WEAK_MASCULINE_ENDINGS = ("e", "ent", "ant", "ist", "oge", "nom", "graf", "soph")
WEAK_MASCULINE_NOUNS = {"Mensch", "Herr", "Nachbar", "Bär", "Held", "Prinz", "Bauer", "Fürst", "Graf", "Christ", "Narr", "Ochse", "Automat", "Soldat", "Planet"}
PLURAL_MARK = " (Pl.)"


def noun_phrase(noun: str, gender: str, case: str, plural: str | None = None) -> list[str]:
    """定冠詞 + 名詞の格変化形。正解として認める形をすべて返し、先頭を表示に使う。
    plural を渡すと複数形の名詞句を作る"""
    if plural:
        article = DEFINITE_ARTICLES["pl"][case]
        # 複数3格は -n を付ける。-n / -s で終わる複数形には付けない
        form = plural + "n" if case == "Dat" and not plural.endswith(("n", "s")) else plural
        return [f"{article} {form}"]
    article = DEFINITE_ARTICLES[gender][case]
    if case == "Gen" and gender in ("m", "n"):
        if noun.endswith(GENITIVE_ES_ENDINGS):
            return [f"{article} {noun}es"]
        # 1音節の語などは -es も使われるので、どちらも正解にする
        return [f"{article} {noun}s", f"{article} {noun}es"]
    return [f"{article} {noun}"]


def valency_answers(slot: str, hint: str, preposition: str = "") -> list[str]:
    """格支配クイズの正解として認める句。slot は格支配の1要素 (Akk / von+Dat / sich+Akk)、
    hint はヒントの名詞の1格 (der Bahnhof / die Kinder (Pl.) / 冠詞のない Japan) か、再帰代名詞なら主語 (ich / du / er …)。
    前置詞そのものの格支配 (an + Dat) は、slot が格だけなので preposition に見出し語を渡す"""
    head, _, case = slot.rpartition("+")
    head = head or preposition
    if head == "sich":
        person = SUBJECT_PERSONS.get(hint.strip().lower())
        if person is None or case not in REFLEXIVE_PRONOUNS:
            raise ValueError(f"再帰代名詞のヒントは主語 (ich / du / er / sie / es / wir / ihr / sie (Pl.)) にしてください: {hint}")
        return [REFLEXIVE_PRONOUNS[case][person]]
    plural = hint.endswith(PLURAL_MARK)
    article, _, noun = hint.removesuffix(PLURAL_MARK).partition(" ")
    if article in ARTICLE_GENDERS and noun:
        if article == "der" and not plural and (noun in WEAK_MASCULINE_NOUNS or noun.endswith(WEAK_MASCULINE_ENDINGS)):
            raise ValueError(f"弱変化の可能性がある男性名詞はヒントに使えません: {hint}")
        phrases = noun_phrase(noun, ARTICLE_GENDERS[article], case, noun if plural else None)
    else:
        # 冠詞のない固有名詞・物質名詞 (Japan, Holz, Deutsch) は形が変わらない
        phrases = [hint.removesuffix(PLURAL_MARK)]
    return with_preposition(head, phrases) if head else phrases


def with_preposition(preposition: str, phrases: list[str]) -> list[str]:
    """前置詞句。融合形があれば融合形を先頭にし、融合しない形も正解に含める (an dem Tisch / am Tisch)"""
    results = []
    for phrase in phrases:
        article, _, rest = phrase.partition(" ")
        contracted = CONTRACTIONS.get((preposition.lower(), article))
        if contracted:
            results.append(f"{contracted} {rest}")
        results.append(f"{preposition} {phrase}")
    return list(dict.fromkeys(results))


# --- 動詞の活用 ---

PERSONS = ("ich", "du", "er/sie/es", "wir", "ihr", "sie/Sie")
# 入力の先頭に付けてもよい主語 (du fährst ab と答えても正解)
PERSON_PRONOUNS = {"ich": ("ich",), "du": ("du",), "er/sie/es": ("er", "sie", "es"), "wir": ("wir",), "ihr": ("ihr",), "sie/Sie": ("sie",)}
# 主語 → 人称。再帰代名詞の形を決めるのに使う
SUBJECT_PERSONS = {"ich": "ich", "du": "du", "er": "er/sie/es", "sie": "er/sie/es", "es": "er/sie/es", "wir": "wir", "ihr": "ihr", "sie (pl.)": "sie/Sie"}
REFLEXIVE_PRONOUNS = {
    "Akk": {"ich": "mich", "du": "dich", "er/sie/es": "sich", "wir": "uns", "ihr": "euch", "sie/Sie": "sich"},
    "Dat": {"ich": "mir", "du": "dir", "er/sie/es": "sich", "wir": "uns", "ihr": "euch", "sie/Sie": "sich"},
}
AUXILIARIES = {
    "hat": ("habe", "hast", "hat", "haben", "habt", "haben"),
    "ist": ("bin", "bist", "ist", "sind", "seid", "sind"),
}
# 規則で活用を作れない動詞 (sein, haben, 話法の助動詞など)。分離動詞の動詞部分もこれで判定する
IRREGULAR_VERBS = {"sein", "haben", "werden", "können", "müssen", "dürfen", "sollen", "wollen", "mögen", "wissen", "tun"}
VOWELS = "aeiouäöüy"


class ConjugationCell(NamedTuple):
    person: str
    tense: str
    # 正解として認める形。先頭を表示に使う (fährst ab)
    answers: list[str]


def _needs_e(stem: str) -> bool:
    """語尾の -st / -t の前に e を入れる語幹か (arbeit-est, öffn-et)"""
    if stem.endswith(("t", "d")):
        return True
    # 子音 + m / n で終わる語幹 (öffnen, atmen)。l / r / h の後は入れない (lernen, qualmen)
    return len(stem) >= 2 and stem[-1] in "mn" and stem[-2] not in VOWELS + "lrhmn"


def _join(verb: str, reflexive: str, prefix: str) -> str:
    return " ".join(part for part in (verb, reflexive, prefix) if part)


def conjugation_table(infinitive: str, prefix: str | None, forms: dict, reflexive_case: str | None = None) -> list[ConjugationCell]:
    """全人称の現在形・過去形・現在完了を作る。infinitive は印を外した見出し語 (abfahren, sich anmelden)、
    prefix は分離動詞の前つづり、forms は登録された present_3sg / past / perfect。
    reflexive_case は再帰動詞の再帰代名詞の格 (格支配の sich+Akk / sich+Dat から)。作れない形は含めない"""
    if infinitive.startswith("sich ") and not reflexive_case:
        reflexive_case = "Akk"
    reflexives = REFLEXIVE_PRONOUNS.get(reflexive_case or "", {})
    verb = infinitive.removeprefix("sich ").strip()
    prefix = prefix or ""
    if prefix and verb.startswith(prefix):
        verb = verb[len(prefix):]
    if " " in verb or verb in IRREGULAR_VERBS or not verb.endswith("n"):
        return []
    # ändern / ärgern のような -ern / -eln の動詞は語幹に -n だけが付く
    short_ending = verb.endswith(("ern", "eln"))
    stem = verb[:-1] if short_ending else verb[:-2] if verb.endswith("en") else ""
    if not stem:
        return []

    def registered(key: str) -> str | None:
        # "fährt ab" → "fährt"。前つづりが登録と食い違う形は使わない
        tokens = (forms.get(key) or "").split()
        if not tokens or (prefix and tokens[-1] != prefix) or len(tokens) > (2 if prefix else 1):
            return None
        return tokens[0]

    cells: list[ConjugationCell] = []

    def add(person: str, tense: str, *verb_forms: str) -> None:
        own = reflexives.get(person, "")
        cells.append(ConjugationCell(person, tense, [_join(form, own, prefix) for form in verb_forms]))

    third = registered("present_3sg")
    if third:
        e = "e" if _needs_e(stem) else ""
        # -eln の動詞は ich sammle が標準 (sammele も正解にする)
        add("ich", "現在", *((stem[:-2] + "le", stem + "e") if verb.endswith("eln") else (stem + "e",)))
        add("wir", "現在", verb)
        add("ihr", "現在", stem + e + "t")
        add("sie/Sie", "現在", verb)
        add("er/sie/es", "現在", third)
        # du は3人称単数から作る (fährt → fährst, arbeitet → arbeitest, liest → liest)。
        # 語幹が -t / -d で終わる強変化 (hält, lädt) は du の形が規則で決まらないので作らない
        if third.endswith("et") or not stem.endswith(("t", "d")):
            base = third[:-1] if third.endswith("t") else None
            if base:
                add("du", "現在", base + "t" if base.endswith(("s", "ß", "x", "z")) else base + "st")

    past = registered("past")
    if past:
        if past.endswith("te"):
            # 弱変化 (holte, arbeitete, brachte)
            for person, ending in zip(PERSONS, ("", "st", "", "n", "t", "n")):
                add(person, "過去", past + ending)
        else:
            # 強変化 (fuhr, fing, stand)。du / ihr で e が入る形は両方を正解にする (fandest / fandst)
            e_needed = past.endswith(("t", "d", "s", "ß", "z"))
            add("ich", "過去", past)
            add("er/sie/es", "過去", past)
            add("du", "過去", *((past + "est", past + "st") if e_needed else (past + "st",)))
            # schrie → schrien (schrieen ではない)
            plural = past + "n" if past.endswith("e") else past + "en"
            add("wir", "過去", plural)
            add("ihr", "過去", *((past + "et",) if past.endswith(("t", "d")) else (past + "t",)))
            add("sie/Sie", "過去", plural)

    perfect = (forms.get("perfect") or "").split()
    if len(perfect) == 2 and perfect[0] in AUXILIARIES:
        auxiliary, participle = perfect
        for person, form in zip(PERSONS, AUXILIARIES[auxiliary]):
            own = reflexives.get(person, "")
            cells.append(ConjugationCell(person, "現在完了", [_join(form, own, participle)]))
    return cells

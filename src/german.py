"""ドイツ語名詞の複数形クイズの誤答づくり"""
from __future__ import annotations

import random


UMLAUTS = {"a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü"}
# 複数形がないことを表す書き方 (Ausland など)
NO_PLURAL = {"", "-", "—", "–", "なし"}
# 必ず -en / -nen になり、ウムラウトもしない語尾
WEAK_SUFFIXES = ("ung", "heit", "keit", "schaft", "ion", "tät", "ei", "ik")
VOWELS = "aeiouäöüAEIOUÄÖÜ"


def _umlauted(word: str) -> str | None:
    """語幹の最後の母音 a / o / u / au をウムラウトにする (Apfel → Äpfel, Arzt → Ärzt)。できなければ None"""
    stem = word
    for suffix in ("el", "er", "en"):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            stem = word[: -len(suffix)]
            break
    index = len(stem) - 1
    while index >= 0 and stem[index] not in VOWELS:
        index -= 1
    if index < 0:
        return None
    if stem[index - 1 : index + 1].lower() == "au":
        index -= 1
    elif stem[index] not in UMLAUTS or stem[index - 1 : index + 1].lower() == "eu":
        return None
    return word[:index] + UMLAUTS[word[index]] + word[index + 1 :]


def plural_candidates(word: str) -> list[str]:
    """よくある複数形の作り方を語の形に合わせて当てはめ、ありがちな順に返す (-e, -en, -n, -er, -s, 無語尾, ウムラウト)"""
    umlauted = _umlauted(word)
    if word.endswith("in"):
        # Lehrerin → Lehrerinnen
        forms = [word + "nen", word + "en", word + "s", word]
    elif word.endswith(WEAK_SUFFIXES):
        # Ahnung → Ahnungen
        forms = [word + "en", word + "e", word + "s", word]
    elif word.endswith("e"):
        # Adresse → Adressen。Auge → Äugen のようにウムラウトさせた形を混ぜ、作れなければ -er で補う
        stem_umlauted = _umlauted(word[:-1])
        forms = [word + "n", word + "s", word, stem_umlauted and stem_umlauted + "en", stem_umlauted and stem_umlauted + "e", word + "r"]
    elif word[-1] in VOWELS:
        # Auto → Autos
        forms = [word + "s", word, word + "n", word + "en"]
    elif word.endswith(("el", "er", "en")):
        # Apfel → Äpfel, Lehrer → Lehrer, Kartoffel → Kartoffeln
        forms = [word, umlauted, word + "n", word + "s", word + "e"]
    else:
        # Tag → Tage, Arzt → Ärzte, Buch → Bücher, Bett → Betten
        forms = [word + "e", umlauted and umlauted + "e", word + "en", word + "er", umlauted and umlauted + "er", word + "s", word]
    return list(dict.fromkeys(form for form in forms if form))


def plural_distractors(word: str, answer: str, count: int = 3) -> list[str]:
    candidates = [candidate for candidate in plural_candidates(word) if candidate != answer]
    # 正解のウムラウトの有無だけを変えた形 (Ärzte → Arzte) は紛らわしいので必ず入れる
    stripped = answer
    for plain, umlaut in UMLAUTS.items():
        stripped = stripped.replace(umlaut, plain)
    pinned = [candidates.pop(candidates.index(stripped))] if stripped in candidates else []
    # 候補はありがちな形から順に並んでいるので、上位から選んで毎回少し入れ替える
    pool = candidates[: count + 1 - len(pinned)]
    return pinned + random.sample(pool, min(count - len(pinned), len(pool)))

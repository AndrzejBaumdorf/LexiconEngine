"""韓国語のラテン文字転写 (国語のローマ字表記法 / Revised Romanization)。

音変化 (連音化・鼻音化・流音化・激音化・口蓋音化) を反映して転写する。
濃音化は表記に反映しない (RR の規定どおり)。ㄴ挿入など語彙ごとに決まる変化は扱わない。
"""
from __future__ import annotations

import random


SYLLABLE_START, SYLLABLE_END = 0xAC00, 0xD7A3
INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
FINALS = ("", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

INITIAL_LATIN = dict(zip(INITIALS, ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")))
VOWEL_LATIN = dict(zip(VOWELS, (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)))
# 終声の代表音 (ㄱ・ㄴ・ㄷ・ㄹ・ㅁ・ㅂ・ㅇ の7つ)
REPRESENTATIVE = {
    **dict.fromkeys("ㄱㄲㅋㄳㄺ", "ㄱ"), **dict.fromkeys("ㄴㄵㄶ", "ㄴ"), **dict.fromkeys("ㄷㅅㅆㅈㅊㅌㅎ", "ㄷ"),
    **dict.fromkeys("ㄹㄼㄽㄾㅀ", "ㄹ"), **dict.fromkeys("ㅁㄻ", "ㅁ"), **dict.fromkeys("ㅂㅍㅄㄿ", "ㅂ"), "ㅇ": "ㅇ",
}
FINAL_LATIN = {"ㄱ": "k", "ㄴ": "n", "ㄷ": "t", "ㄹ": "l", "ㅁ": "m", "ㅂ": "p", "ㅇ": "ng"}
# 二重終声 → (残る子音, 次の音節へ移る子音)
CLUSTERS = {
    "ㄳ": ("ㄱ", "ㅅ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄶ": ("ㄴ", "ㅎ"), "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"),
    "ㄽ": ("ㄹ", "ㅅ"), "ㄾ": ("ㄹ", "ㅌ"), "ㄿ": ("ㄹ", "ㅍ"), "ㅀ": ("ㄹ", "ㅎ"), "ㅄ": ("ㅂ", "ㅅ"),
}
ASPIRATED = {"ㄱ": "ㅋ", "ㄷ": "ㅌ", "ㅂ": "ㅍ", "ㅈ": "ㅊ"}
NASALIZED = {"ㄱ": "ㅇ", "ㄷ": "ㄴ", "ㅂ": "ㅁ"}


def is_hangul(text: str) -> bool:
    return any(SYLLABLE_START <= ord(char) <= SYLLABLE_END for char in text)


def _decompose(text: str) -> list[list[str]] | None:
    syllables = []
    for char in text:
        code = ord(char) - SYLLABLE_START
        if not 0 <= code <= SYLLABLE_END - SYLLABLE_START:
            return None
        syllables.append([INITIALS[code // 588], VOWELS[code % 588 // 28], FINALS[code % 28]])
    return syllables


def _apply_sound_changes(syllables: list[list[str]], aspirate_h: bool) -> None:
    for current, following in zip(syllables, syllables[1:]):
        final, initial = current[2], following[0]
        if not final:
            continue
        kept, moved = CLUSTERS.get(final, (final, None))
        if initial == "ㅇ":
            # 連音化。ㅎ は母音の前で発音しない (좋아 → 조아, 많아 → 마나)。ㅇ は移らない
            if final == "ㅇ":
                continue
            if moved is None:
                kept, moved = "", final
            # ㅎ は発音せず、二重終声なら残りの子音が移る (싫어 → 시러)
            if moved == "ㅎ":
                kept, moved = "", kept
            # 口蓋音化 (같이 → 가치, 굳이 → 구지)
            if following[1] == "ㅣ" and moved in ("ㄷ", "ㅌ"):
                moved = "ㅈ" if moved == "ㄷ" else "ㅊ"
            current[2], following[0] = kept, moved or "ㅇ"
            continue
        # ㅎ + ㄱㄷㅈ → 激音 (좋다 → 조타, 많다 → 만타)
        if (moved or final) == "ㅎ" and initial in ASPIRATED:
            current[2], following[0] = kept if moved else "", ASPIRATED[initial]
            continue
        if final == "ㅎ" and initial == "ㅅ":
            current[2], following[0] = "", "ㅆ"
            continue
        representative = REPRESENTATIVE[final]
        # ㄱㄷㅂㅈ + ㅎ → 激音。RR では用言 (～다) だけ反映し、名詞は h を残す (입학 iphak)
        if initial == "ㅎ" and aspirate_h and representative in ("ㄱ", "ㄷ", "ㅂ"):
            current[2], following[0] = "", ASPIRATED["ㅈ" if final == "ㅈ" else representative]
            continue
        # 流音化 (설날 → 설랄, 신라 → 실라)
        if (representative, initial) in (("ㄹ", "ㄴ"), ("ㄴ", "ㄹ")):
            current[2], following[0] = "ㄹ", "ㄹ"
            continue
        # ㄹ の鼻音化 (종로 → 종노, 독립 → 동닙)
        if initial == "ㄹ" and representative != "ㄹ":
            following[0] = "ㄴ"
            initial = "ㄴ"
        # 鼻音化 (학년 → 항년, 입문 → 임문)
        if initial in ("ㄴ", "ㅁ") and representative in NASALIZED:
            representative = NASALIZED[representative]
        current[2] = representative


def romanize(word: str) -> str | None:
    """ハングルの語を RR で転写する。ハングル以外を含む場合は None"""
    syllables = _decompose(word.replace(" ", ""))
    if not syllables:
        return None
    _apply_sound_changes(syllables, aspirate_h=word.endswith("다"))
    latin = []
    for index, (initial, vowel, final) in enumerate(syllables):
        # 語頭の ㄹ は r、終声と、終声 ㄹ に続く ㄹ は l (설날 seollal)
        if initial == "ㄹ" and index and syllables[index - 1][2] == "ㄹ":
            latin.append("l")
        else:
            latin.append(INITIAL_LATIN[initial])
        latin.append(VOWEL_LATIN[vowel])
        if final:
            latin.append(FINAL_LATIN[REPRESENTATIVE[final]])
    return "".join(latin)


def _spelled_out(word: str) -> str | None:
    """音変化を無視して綴りどおりに字母を並べた誤り (좋다 → johda, 읽다 → ilgda)"""
    syllables = _decompose(word.replace(" ", ""))
    if not syllables:
        return None
    letters = {**INITIAL_LATIN, "ㅇ": "ng", "ㄹ": "l"}
    latin = []
    for initial, vowel, final in syllables:
        latin += [INITIAL_LATIN[initial], VOWEL_LATIN[vowel]]
        for jamo in CLUSTERS.get(final, (final,)) if final else ():
            latin.append(letters[jamo])
    return "".join(latin)


def _unchanged(word: str) -> str | None:
    """終声を代表音にするだけで、連音化などの音変化をしない誤り (먹어요 → meokeoyo, 학년 → haknyeon)"""
    syllables = _decompose(word.replace(" ", ""))
    if not syllables:
        return None
    return "".join(
        INITIAL_LATIN[initial] + VOWEL_LATIN[vowel] + (FINAL_LATIN[REPRESENTATIVE[final]] if final else "")
        for initial, vowel, final in syllables
    )


# 取り違えやすい字母。別の字母に1か所置き換えた語の転写を誤答にする (어 ↔ 오、ㄱ ↔ ㅋ ↔ ㄲ など)
CONFUSABLE = (
    "ㅓㅗ", "ㅡㅜ", "ㅐㅔ", "ㅕㅛ", "ㅒㅖ", "ㅚㅙㅞ", "ㅝㅘ", "ㅢㅟ",
    "ㄱㅋㄲ", "ㄷㅌㄸ", "ㅂㅍㅃ", "ㅈㅊㅉ", "ㅅㅆ",
)


def _confused_spellings(word: str) -> list[str]:
    syllables = _decompose(word)
    if not syllables:
        return []
    words = []
    for index, syllable in enumerate(syllables):
        for position in (0, 1):
            group = next((group for group in CONFUSABLE if syllable[position] in group), "")
            for replacement in group.replace(syllable[position], ""):
                changed = [list(item) for item in syllables]
                changed[index][position] = replacement
                words.append("".join(
                    chr(SYLLABLE_START + INITIALS.index(i) * 588 + VOWELS.index(v) * 28 + FINALS.index(f)) for i, v, f in changed
                ))
    return words


def romanization_distractors(word: str, answer: str, count: int = 3) -> list[str]:
    """転写の誤答を作る。音変化を無視した転写を優先し、残りは字母を1か所取り違えた語の転写"""
    preferred = [variant for variant in (_unchanged(word), _spelled_out(word)) if variant]
    confused = [romanize(spelling) for spelling in _confused_spellings(word.replace(" ", ""))]
    random.shuffle(confused)
    distractors: list[str] = []
    for variant in (*preferred, *confused):
        if variant and variant != answer and variant not in distractors:
            distractors.append(variant)
        if len(distractors) == count:
            break
    return distractors

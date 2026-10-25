"""意味・見出し語の文字列の扱い (見出し語を伏せる、似た意味の判定、単語一覧の注記)。"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from lexicon.languages import get_language, split_separable


# 類義語・対義語を文字列だけで登録すると英単語自体が意味になるため、出題対象から除外する
HAS_REAL_MEANING = "m.meaning_ja != w.word"
SIMILAR_MEANING_RATIO = 0.6
ANNOTATION_PATTERN = re.compile(r"[〈（(［\[《][^〉）)］\]》]*[〉）)］\]》]")
CONTENT_PATTERN = re.compile(r"[\u3400-\u9fff々\u30a1-\u30ff]")


# 意味 (日本語) の中で外国語の単語の境目を判定する文字。\w だと「Angebotで」の「で」まで単語の続きになる
WORD_CHAR = "[A-Za-z\u00c0-\u024f\uac00-\ud7a3]"


def mask_headword(text: str, word: str, language: str) -> str:
    """答えが見出し語になる問題 (日→独、穴埋めのヒント) で、意味の中の見出し語を - に伏せる。
    （im Angebotで）→（im -で）。語幹だけの見出し語 (all-) は語頭の一致で伏せる (alles →  -es)"""
    plain = split_separable(word)[0]
    if plain.endswith("-"):
        stem = plain.rstrip("-")
        return re.sub(rf"(?<!{WORD_CHAR}){re.escape(stem)}(?={WORD_CHAR})", "-", text, flags=re.IGNORECASE) if stem else text
    forms = {plain.lower()} | get_language(language).inflect(plain.lower()) if " " not in plain else {plain.lower()}
    alternatives = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
    return re.sub(rf"(?<!{WORD_CHAR})(?:{alternatives})(?!{WORD_CHAR})", "-", text, flags=re.IGNORECASE)


def valency_preposition(word: str, part_of_speech: str) -> str:
    """前置詞そのものの格支配 (an + Dat) では見出し語が前置詞になる"""
    return word if part_of_speech == "前置詞" else ""


def hanja_note(forms_json: str | None) -> str:
    """一覧で意味の後ろに付ける、韓国語の元の漢字語の表示 (【家族】)。なければ空"""
    hanja = json.loads(forms_json).get("hanja") if forms_json else None
    return f" 【{hanja}】" if hanja else ""


def valency_note(language: str, valency_json: str | None) -> str:
    """問題文や一覧で意味の後ろに付ける格支配の表示 ( ［+ Akk, von + Dat］)。なければ空"""
    if not valency_json:
        return ""
    return f" ［{get_language(language).format_valency(json.loads(valency_json))}］"


def _meaning_key(meaning_ja: str) -> str:
    # 〈批評などが〉のような注記と記号を除き、漢字・カタカナがあればそれだけで比べる (辛らつな / 辛辣な → 辛 / 辛辣)
    text = re.sub(r"[〜～…、，,；;・\s]", "", ANNOTATION_PATTERN.sub("", meaning_ja))
    return "".join(CONTENT_PATTERN.findall(text)) or text


def meanings_similar(first: str, second: str) -> bool:
    first, second = _meaning_key(first), _meaning_key(second)
    return bool(first and second) and SequenceMatcher(None, first, second).ratio() >= SIMILAR_MEANING_RATIO

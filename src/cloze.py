from __future__ import annotations

import re

from languages import get_language


BLANK = "_____"
MARKED_PATTERN = re.compile(r"\[([^\[\]]+)\]")


def find_cloze_spans(
    sentence: str,
    word: str,
    language: str = "English",
    targets: list[str] | None = None,
    extra_forms: tuple[str, ...] | list[str] = (),
) -> list[tuple[int, int]] | None:
    """例文中で穴にする範囲を返す。targets があればその語を順に探し、なければ活用形から推測する。"""
    if targets:
        spans = []
        position = 0
        for target in targets:
            match = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", re.IGNORECASE).search(sentence, position)
            if match is None:
                return None
            spans.append(match.span())
            position = match.end()
        return spans

    first, *rest = word.split()
    forms = get_language(language).inflect(first.lower())
    if not rest:
        forms |= {form.lower() for form in extra_forms if form}
    alternatives = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
    pattern = f"(?:{alternatives})" + "".join(r"\s+" + re.escape(token) for token in rest)
    match = re.search(rf"(?<!\w){pattern}(?!\w)", sentence, re.IGNORECASE)
    return [match.span()] if match else None


def blank_out(sentence: str, spans: list[tuple[int, int]]) -> str:
    for start, end in reversed(spans):
        sentence = sentence[:start] + BLANK + sentence[end:]
    return sentence


def mark_spans(sentence: str, spans: list[tuple[int, int]]) -> str:
    for start, end in reversed(spans):
        sentence = sentence[:start] + "[" + sentence[start:end] + "]" + sentence[end:]
    return sentence


def parse_marked(sentence: str) -> tuple[str, list[str]]:
    """"He was [petrified] of it." → ("He was petrified of it.", ["petrified"])"""
    return MARKED_PATTERN.sub(r"\1", sentence), MARKED_PATTERN.findall(sentence)

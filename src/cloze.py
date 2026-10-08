from __future__ import annotations

import re

from languages import Language, get_language, split_separable


BLANK = "_____"
# 複合語の後半として穴にするとき、前半に必要な最低文字数
COMPOUND_MIN_HEAD = 3
# 分離動詞の前つづりを探す範囲の終わり (節の区切り)
CLAUSE_END = re.compile(r"[,;:.!?]")
MARKED_PATTERN = re.compile(r"\[([^\[\]]+)\]")


def find_cloze_spans(
    sentence: str,
    word: str,
    language: str = "English",
    targets: list[str] | None = None,
    extra_forms: tuple[str, ...] | list[str] = (),
) -> list[tuple[int, int]] | None:
    """例文中で穴にする範囲を返す。targets があればその語を順に探し、なければ活用形から推測する。
    分離動詞は見出し語に ab|fahren のように印を付けておくと、離れた2か所 (fahren ... ab) を穴にする。
    ドイツ語などでは、見つからなければ複合語の後半 (Stellen|angebot) も探す。"""
    if targets:
        spans = []
        position = 0
        for target in targets:
            # 複合語の一部に [ ] を付けた場合 (Stellen[angebot]) は単語の途中でも探す
            match = (
                re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", re.IGNORECASE).search(sentence, position)
                or re.compile(re.escape(target), re.IGNORECASE).search(sentence, position)
            )
            if match is None:
                return None
            spans.append(match.span())
            position = match.end()
        return spans

    language_spec = get_language(language)
    # 語幹だけの見出し語 (ander-) は記号を外して活用形を探す
    plain, prefix = split_separable(word.strip("-~〜"))
    tokens = plain.split()
    # 再帰動詞の sich は例文では mich / dich などになるので探さない
    if language_spec.reflexive_pronoun and len(tokens) > 1 and tokens[0].lower() == language_spec.reflexive_pronoun:
        tokens = tokens[1:]
    first, *rest = tokens
    extra = {form.lower() for form in extra_forms if form}
    if prefix and not rest:
        return _separable_spans(sentence, first.lower(), prefix.lower(), language_spec, extra)
    forms = language_spec.inflect(first.lower())
    if not rest:
        forms |= extra
    alternatives = _alternatives(forms)
    pattern = f"(?:{alternatives})" + "".join(r"\s+" + re.escape(token) for token in rest)
    match = re.search(rf"(?<!\w){pattern}(?!\w)", sentence, re.IGNORECASE)
    if match:
        return [match.span()]
    return None if rest else _compound_tail_spans(sentence, alternatives, language_spec)


def _alternatives(forms: set[str]) -> str:
    return "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))


def _separable_spans(sentence: str, word: str, prefix: str, language_spec: Language, extra_forms: set[str]) -> list[tuple[int, int]] | None:
    """分離動詞を探す。まず動詞部分と後ろの前つづりの2か所 (Ruhen Sie sich ... aus)、
    なければ1語になった形 (abfahren, abgefahren, abzufahren) を探す"""
    stem = word[len(prefix):]
    # 活用形の欄は "fährt ab" のように登録され、その1語目 (fährt) が extra_forms に入っている
    verb_forms = language_spec.inflect(stem) | extra_forms
    particle_pattern = re.compile(rf"(?<!\w){re.escape(prefix)}(?!\w)", re.IGNORECASE)
    for verb in re.finditer(rf"(?<!\w)(?:{_alternatives(verb_forms)})(?!\w)", sentence, re.IGNORECASE):
        # 前つづりは節の最後に来るので、同じ節の中で最後のものを選ぶ (passt ... auf die Kinder auf の前置詞 auf を避ける)
        clause_end = CLAUSE_END.search(sentence, verb.end())
        particles = list(particle_pattern.finditer(sentence, verb.end(), clause_end.start() if clause_end else len(sentence)))
        if particles:
            return [verb.span(), particles[-1].span()]
    joined_forms = {prefix + form for form in language_spec.inflect(stem)} | extra_forms
    if language_spec.separable_infix:
        joined_forms.add(prefix + language_spec.separable_infix + stem)
    match = re.search(rf"(?<!\w)(?:{_alternatives(joined_forms)})(?!\w)", sentence, re.IGNORECASE)
    return [match.span()] if match else None


def _compound_tail_spans(sentence: str, alternatives: str, language_spec: Language) -> list[tuple[int, int]] | None:
    """複合語の後半として探す (Angebot → Stellen|angebot, arm → fett|armen)。
    warm の arm のような偶然の一致を避けるため、前半が COMPOUND_MIN_HEAD 文字以上のときだけ使う"""
    if not language_spec.compound_words:
        return None
    match = re.search(rf"(?<=\w{{{COMPOUND_MIN_HEAD}}})(?:{alternatives})(?!\w)", sentence, re.IGNORECASE)
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

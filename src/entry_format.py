"""単語エディタの入力欄と JSON の見出しの相互変換。デスクトップ版 (word_editor.py) とブラウザ版で共通に使う。"""
from __future__ import annotations

import re

from cloze import find_cloze_spans, mark_spans, parse_marked
from database import normalize_derivatives
from languages import FORM_KEYS, split_separable


RELATION_TYPES = ("synonyms", "antonyms")
# フォームで編集するキー。これ以外 (usages など) は編集時もそのまま残す
WORD_KEYS = ("word", "language", "difficulty", "source", "parts_of_speech", "derivatives", "related_words")
MEANING_KEYS = ("meaning_ja", *FORM_KEYS, "valency", "synonyms", "antonyms", "examples")
DERIVATIVE_PATTERN = re.compile(r"^(?P<word>[^=(（]+?)\s*[(（]\s*(?P<pos>[^)）]+?)\s*[)）]\s*(?:=\s*(?P<meaning>.*))?$")


def parse_relations(text: str, part_of_speech: str) -> list[str | dict]:
    # "forsake=見捨てる" は意味付きのオブジェクト、"forsake" だけなら文字列として保存する
    relations: list[str | dict] = []
    for item in re.split(r"[,，]", text):
        word, _, meaning = (part.strip() for part in item.partition("="))
        if not word:
            continue
        relations.append({"word": word, "part_of_speech": part_of_speech, "meaning_ja": meaning} if meaning else word)
    return relations


def format_relations(relations: list[str | dict]) -> str:
    return ", ".join(
        relation if isinstance(relation, str)
        else f"{relation['word']}={relation['meaning_ja']}" if relation.get("meaning_ja") and relation["meaning_ja"] != relation["word"]
        else relation["word"]
        for relation in relations
    )


def parse_derivatives(text: str) -> list[dict]:
    # "placatory(形容詞)=なだめるような, placation(名詞)"
    derivatives = []
    for item in re.split(r"[,，]", text):
        item = item.strip()
        if not item:
            continue
        match = DERIVATIVE_PATTERN.match(item)
        if match is None:
            raise ValueError(f"派生語「{item}」は「placatory(形容詞)=なだめるような」の形で書いてください")
        derivative = {"word": match["word"], "part_of_speech": match["pos"]}
        if match["meaning"] and match["meaning"].strip():
            derivative["meaning_ja"] = match["meaning"].strip()
        derivatives.append(derivative)
    return derivatives


def format_derivatives(derivatives: list[dict]) -> str:
    return ", ".join(
        f"{item['word']}({item['part_of_speech']})" + (f"={item['meaning_ja']}" if item.get("meaning_ja") else "")
        for item in derivatives
    )


def parse_examples(sentences: str, translations: str) -> list[dict]:
    # 例文中の [ ] は穴埋めにする箇所として targets に保存し、本文からは外す
    translation_lines = translations.splitlines()
    examples = []
    for index, sentence in enumerate(sentences.splitlines()):
        if not sentence.strip():
            continue
        plain, targets = parse_marked(sentence.strip())
        example: dict = {"sentence": plain}
        if targets:
            example["targets"] = targets
        translation = translation_lines[index].strip() if index < len(translation_lines) else ""
        if translation:
            example["translation_ja"] = translation
        examples.append(example)
    return examples


def format_examples(examples: list[dict]) -> tuple[str, str]:
    sentences = []
    for example in examples:
        sentence = example["sentence"]
        spans = find_cloze_spans(sentence, "", targets=example["targets"]) if example.get("targets") else None
        sentences.append(mark_spans(sentence, spans) if spans else sentence)
    return "\n".join(sentences), "\n".join(example.get("translation_ja", "") for example in examples)


def _relation_key(relation: str | dict) -> str:
    return relation if isinstance(relation, str) else relation["word"]


def _merge_meaning(existing: dict, meaning: dict) -> None:
    for key in FORM_KEYS:
        if meaning.get(key):
            existing[key] = meaning[key]
    for key in RELATION_TYPES:
        known = {_relation_key(relation) for relation in existing.get(key, [])}
        for relation in meaning.get(key, []):
            if _relation_key(relation) not in known:
                existing.setdefault(key, []).append(relation)
    known_sentences = {example["sentence"] for example in existing.get("examples", [])}
    for example in meaning.get("examples", []):
        if example["sentence"] not in known_sentences:
            existing.setdefault("examples", []).append(example)


def entry_key(entry: dict) -> tuple[str, str, str, str]:
    """見出しの同一性。同じ単語でも出典やレベルが違えば別見出し (A1 の接続詞 aber と A2 の心態詞 aber など)"""
    # 分離動詞の印の有無 (ab|fahren / abfahren) は同じ見出しとして扱う
    return split_separable(entry.get("word") or "")[0], entry.get("language", "English"), entry.get("source") or "", entry.get("difficulty") or ""


def merge_entry(entries: list[dict], entry: dict) -> bool:
    """同じ見出しがあれば品詞と意味を追記し、なければ末尾に追加する。追記した場合は True。"""
    existing = next((item for item in entries if entry_key(item) == entry_key(entry)), None)
    if existing is None:
        entries.append(entry)
        return False
    parts = existing.setdefault("parts_of_speech", [])
    for part in entry["parts_of_speech"]:
        existing_part = next((item for item in parts if item.get("part_of_speech") == part["part_of_speech"]), None)
        if existing_part is None:
            parts.append(part)
            continue
        meanings = existing_part.setdefault("meanings", [])
        for meaning in part["meanings"]:
            existing_meaning = next((item for item in meanings if item.get("meaning_ja") == meaning["meaning_ja"]), None)
            if existing_meaning is None:
                meanings.append(meaning)
            else:
                _merge_meaning(existing_meaning, meaning)
    derivatives = normalize_derivatives(existing)
    known = {item["word"] for item in derivatives}
    derivatives += [item for item in entry.get("derivatives", []) if item["word"] not in known]
    existing.pop("related_words", None)
    if derivatives:
        existing["derivatives"] = derivatives
    return True

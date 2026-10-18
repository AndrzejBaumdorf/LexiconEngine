"""ブラウザ版の単語エディタ。教材JSONの写しをブラウザ内のファイルに持ち、保存すると写しとこの端末のDBを書き換えて、
編集を未同期の編集 (edits.json) に記録する。同期のときに Mac へ送る (edits.py)。
入力欄と JSON の変換はデスクトップ版のエディタと同じ (entry_format.py)。"""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cloze import find_cloze_spans
from edits import entry_hash, force_edit, key_of, material_path
from entry_format import (
    MEANING_KEYS, RELATION_TYPES, WORD_KEYS, entry_key, format_derivatives, format_examples, format_relations, merge_entry,
    parse_derivatives, parse_examples, parse_relations,
)
from database import normalize_derivatives
from languages import FORM_KEYS, LANGUAGES, find_language, get_language
from materials import load_entries, save_entries
import web_bridge

MATERIALS_DIR = Path("/home/pyodide/materials")
EDITS_PATH = Path("/home/pyodide/data/edits.json")


def configure(materials_dir: str, edits_path: str) -> None:
    global MATERIALS_DIR, EDITS_PATH
    MATERIALS_DIR, EDITS_PATH = Path(materials_dir), Path(edits_path)


# --- 未同期の編集 ---

def _load_edits() -> list[dict]:
    return json.loads(EDITS_PATH.read_text(encoding="utf-8")) if EDITS_PATH.exists() else []


def _save_edits(edits: list[dict]) -> None:
    EDITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EDITS_PATH.write_text(json.dumps(edits, ensure_ascii=False), encoding="utf-8")


def pending_edits() -> str:
    return json.dumps(_load_edits(), ensure_ascii=False)


def _record_edit(file: str, original: dict | None, new: dict | None) -> None:
    """original (編集前の写しの見出し、新規なら None) を new (削除なら None) にした編集を記録する。
    同じ見出しをもう一度編集したときは、前の編集の base (最後に同期したときの中身) を引き継いで1つにまとめる"""
    edits = _load_edits()
    previous = None
    if original is not None:
        previous = next((
            edit for edit in edits
            if edit["file"] == file and edit.get("entry") is not None and entry_hash(edit["entry"]) == entry_hash(original)
        ), None)
    if previous is not None:
        if previous.get("base_key") is None and new is None:
            # この端末で追加してまだ同期していない見出しを消した: 編集ごとなくす
            edits.remove(previous)
        else:
            previous.update(entry=new, time=_now(), word=(new or original).get("word", ""))
    else:
        edits.append({
            "id": uuid.uuid4().hex, "file": file, "time": _now(), "word": (new or original or {}).get("word", ""),
            "base_key": key_of(original) if original is not None else None,
            "base_hash": entry_hash(original) if original is not None else None,
            "entry": new,
        })
    _save_edits(edits)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --- 教材ファイル ---

def files() -> str:
    return json.dumps(sorted(path.relative_to(MATERIALS_DIR).as_posix() for path in MATERIALS_DIR.rglob("*.json")), ensure_ascii=False)


def _entries(file: str) -> list[dict]:
    return load_entries(material_path(MATERIALS_DIR, file))


def _default_language(file: str) -> str:
    top = file.split("/", 1)[0] if "/" in file else ""
    language = find_language(top)
    return language.name if language else LANGUAGES[0].name


def open_file(file: str) -> str:
    """単語の一覧と、新しい単語の初期値 (言語・出典)"""
    entries = _entries(file)
    counts = Counter((entry.get("word"), entry.get("language", "English")) for entry in entries)
    items = []
    for index, entry in enumerate(entries):
        language = entry.get("language", "English")
        label = entry.get("word", "")
        if counts[entry.get("word"), language] > 1:
            label += f" ({entry.get('difficulty') or '-'} / {entry.get('source') or '-'})"
        items.append({"index": index, "label": label, "word": entry.get("word", "")})
    last = entries[-1] if entries else {}
    edited = {
        json.dumps(key_of(edit["entry"]), ensure_ascii=False)
        for edit in _load_edits() if edit["file"] == file and edit.get("entry") is not None
    }
    for item, entry in zip(items, entries):
        item["edited"] = json.dumps(key_of(entry), ensure_ascii=False) in edited
    return json.dumps({
        "entries": items,
        "language": last.get("language") or _default_language(file),
        "difficulty": last.get("difficulty") or "",
        "source": last.get("source") or "",
        "sources": sorted({entry["source"] for entry in entries if isinstance(entry.get("source"), str)}),
    }, ensure_ascii=False)


def language_spec(language: str) -> str:
    spec = get_language(language)
    return json.dumps({
        "languages": [item.name for item in LANGUAGES],
        "parts_of_speech": list(spec.parts_of_speech),
        "form_fields": {part: [{"key": field.key, "label": field.label} for field in fields] for part, fields in spec.form_fields.items()},
        "genders": [{"value": code, "label": spec.gender_label(code)} for code in spec.articles],
        "cases": bool(spec.cases),
    }, ensure_ascii=False)


def load_entry(file: str, index: int) -> str:
    """見出しを入力欄の形にする"""
    entry = _entries(file)[index]
    meanings = []
    for part in entry.get("parts_of_speech", []):
        for meaning in part.get("meanings", []):
            meaning = {"meaning_ja": meaning} if isinstance(meaning, str) else meaning
            sentences, translations = format_examples(meaning.get("examples", []))
            meanings.append({
                "part_of_speech": part.get("part_of_speech") or part.get("name") or "",
                "meaning_ja": meaning.get("meaning_ja", ""),
                "forms": {key: meaning[key] for key in FORM_KEYS if meaning.get(key)},
                "valency": ", ".join(meaning.get("valency", [])),
                "synonyms": format_relations(meaning.get("synonyms", [])),
                "antonyms": format_relations(meaning.get("antonyms", [])),
                "examples": sentences,
                "translations": translations,
                # 入力欄にないキー (usages など) は保存時にそのまま戻す
                "extra": {key: value for key, value in meaning.items() if key not in MEANING_KEYS},
            })
    return json.dumps({
        "word": entry.get("word", ""),
        "language": entry.get("language", "English"),
        "difficulty": entry.get("difficulty") or "",
        "source": entry.get("source") or "",
        "derivatives": format_derivatives(normalize_derivatives(entry)),
        "meanings": meanings,
        "extra": {key: value for key, value in entry.items() if key not in WORD_KEYS},
    }, ensure_ascii=False)


def _meaning_from_form(form: dict, language: str, word: str) -> tuple[str, dict, list[str]]:
    spec = get_language(language)
    part_of_speech = form.get("part_of_speech", "").strip()
    meaning_ja = form.get("meaning_ja", "").strip()
    if not part_of_speech or not meaning_ja:
        raise ValueError("各意味に品詞と意味を入力してください")
    meaning: dict = {"meaning_ja": meaning_ja}
    for field in spec.fields_for(part_of_speech):
        value = str(form.get("forms", {}).get(field.key) or "").strip()
        if value:
            meaning[field.key] = value
    if spec.cases and form.get("valency", "").strip():
        try:
            meaning["valency"] = spec.parse_valency(form["valency"])
        except ValueError as error:
            raise ValueError(f"{meaning_ja}: {error}") from None
    for key in RELATION_TYPES:
        relations = parse_relations(form.get(key, ""), part_of_speech)
        if relations:
            meaning[key] = relations
    meaning.update(form.get("extra") or {})
    examples = parse_examples(form.get("examples", ""), form.get("translations", ""))
    unusable = [
        example["sentence"] for example in examples
        if word and not find_cloze_spans(example["sentence"], word, language, example.get("targets"), spec.cloze_forms(meaning))
    ]
    if examples:
        meaning["examples"] = examples
    return part_of_speech, meaning, unusable


def _entry_from_form(form: dict) -> tuple[dict, list[str]]:
    word = form.get("word", "").strip()
    if not word:
        raise ValueError("単語を入力してください")
    language = form.get("language", "").strip() or "English"
    if not form.get("meanings"):
        raise ValueError("意味を1つ以上追加してください")
    entry: dict = {"word": word, "language": language}
    if form.get("difficulty", "").strip():
        entry["difficulty"] = form["difficulty"].strip()
    if form.get("source", "").strip():
        entry["source"] = form["source"].strip()
    entry["parts_of_speech"] = []
    unusable_all = []
    for meaning_form in form["meanings"]:
        part_of_speech, meaning, unusable = _meaning_from_form(meaning_form, language, word)
        unusable_all += unusable
        part = next((item for item in entry["parts_of_speech"] if item["part_of_speech"] == part_of_speech), None)
        if part is None:
            part = {"part_of_speech": part_of_speech, "meanings": []}
            entry["parts_of_speech"].append(part)
        part["meanings"].append(meaning)
    derivatives = parse_derivatives(form.get("derivatives", ""))
    if derivatives:
        entry["derivatives"] = derivatives
    entry.update(form.get("extra") or {})
    return entry, unusable_all


def save_entry(file: str, index: int | None, form_json: str, confirmed: bool = False) -> str:
    """見出しを保存する。確認が要るとき (穴埋めに使えない例文・登録済みの見出しへの追記) は
    {"status": "confirm", "message"} を返すので、確認後に confirmed=True でもう一度呼ぶ"""
    try:
        entry, unusable = _entry_from_form(json.loads(form_json))
    except ValueError as error:
        return json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False)
    entries = _entries(file)
    if index is not None and index < 0:
        index = None
    duplicate = next((i for i, item in enumerate(entries) if i != index and entry_key(item) == entry_key(entry)), None)
    if index is not None and duplicate is not None:
        return json.dumps({"status": "error", "message": f"{entry['word']} は同じ出典・レベルで既に登録されています"}, ensure_ascii=False)
    if not confirmed:
        messages = []
        if unusable:
            messages.append("次の例文は単語の位置を判定できないため穴埋めに出題されません ([ ] で囲むと出題できます):\n" + "\n".join(unusable))
        if index is None and duplicate is not None:
            messages.append(f"{entry['word']} は同じ出典・レベルで登録済みです。品詞・意味を追記します。")
        if messages:
            return json.dumps({"status": "confirm", "message": "\n\n".join(messages) + "\n\nこのまま保存しますか?"}, ensure_ascii=False)
    if index is not None:
        original = entries[index]
        entries[index] = entry
        action = "更新"
    elif duplicate is not None:
        original = copy.deepcopy(entries[duplicate])
        merge_entry(entries, entry)
        entry = entries[duplicate]
        action = "既存の単語に追記"
    else:
        original = None
        entries.append(entry)
        action = "追加"
    _write(file, entries)
    _record_edit(file, original, entry)
    if original is not None and entry_key(original) != entry_key(entry):
        web_bridge.session.database.remove_entry(*entry_key(original))
    _import_local(file)
    return json.dumps({"status": "saved", "message": f"{entry['word']} を{action}しました", "index": entries.index(entry)}, ensure_ascii=False)


def delete_entry(file: str, index: int) -> str:
    entries = _entries(file)
    original = entries.pop(index)
    _write(file, entries)
    _record_edit(file, original, None)
    web_bridge.session.database.remove_entry(*entry_key(original))
    return json.dumps({"status": "deleted", "message": f"{original.get('word')} を削除しました"}, ensure_ascii=False)


def create_file(file: str) -> str:
    path = material_path(MATERIALS_DIR, file)
    if path.exists():
        return json.dumps({"status": "error", "message": f"{file} は既にあります"}, ensure_ascii=False)
    save_entries(path, [])
    return json.dumps({"status": "created"})


def _write(file: str, entries: list[dict]) -> None:
    save_entries(material_path(MATERIALS_DIR, file), entries)


def _import_local(file: str) -> None:
    # この端末のDBにすぐ反映して、同期の前でも出題に使えるようにする
    web_bridge.session.database.import_json(material_path(MATERIALS_DIR, file), file)


# --- 同期 ---

def finish_edits(applied_json: str) -> str:
    """Mac に反映された編集を未同期の編集から外す"""
    applied = set(json.loads(applied_json))
    edits = [edit for edit in _load_edits() if edit["id"] not in applied]
    _save_edits(edits)
    return json.dumps({"remaining": len(edits)})


def resolve_conflict(edit_id: str, choice: str, mac_entry_json: str) -> None:
    """競合の解決。choice が "mac" なら編集を捨て、"ipad" なら Mac の今の中身を base にして次の送信で上書きさせる"""
    edits = _load_edits()
    edit = next(edit for edit in edits if edit["id"] == edit_id)
    if choice == "mac":
        edits.remove(edit)
    else:
        mac_entry = json.loads(mac_entry_json)
        edit["base_key"] = key_of(mac_entry) if mac_entry else None
        edit["base_hash"] = entry_hash(mac_entry) if mac_entry else None
    _save_edits(edits)


def replace_materials(files_json: str) -> str:
    """教材の写しを Mac のものに置き換え、まだ反映されていない編集を重ねる。重ねたファイルはこの端末のDBにも取り込み直す"""
    files_from_mac = json.loads(files_json)
    if MATERIALS_DIR.exists():
        shutil.rmtree(MATERIALS_DIR)
    for file, entries in files_from_mac.items():
        save_entries(material_path(MATERIALS_DIR, file), entries)
    touched = set()
    for edit in _load_edits():
        path = material_path(MATERIALS_DIR, edit["file"])
        entries = load_entries(path)
        force_edit(entries, edit)
        save_entries(path, entries)
        touched.add(edit["file"])
    for file in sorted(touched):
        _import_local(file)
    return json.dumps({"files": len(files_from_mac), "overlaid": sorted(touched)}, ensure_ascii=False)


def entry_summary(entry_json: str) -> str:
    """競合の確認画面に出す、見出しの中身の短い説明"""
    entry = json.loads(entry_json)
    if entry is None:
        return "(削除)"
    lines = [f"{entry.get('word', '')}  [{entry.get('difficulty') or '-'} / {entry.get('source') or '-'}]"]
    for part in entry.get("parts_of_speech", []):
        for meaning in part.get("meanings", []):
            meaning = {"meaning_ja": meaning} if isinstance(meaning, str) else meaning
            forms = " / ".join(str(meaning[key]) for key in FORM_KEYS if meaning.get(key))
            lines.append(f"・({part.get('part_of_speech', '')}) {meaning.get('meaning_ja', '')}" + (f"  {forms}" if forms else ""))
            lines += [f"    {example.get('sentence', '')}" for example in meaning.get("examples", [])]
    derivatives = normalize_derivatives(entry)
    if derivatives:
        lines.append("派生語: " + format_derivatives(derivatives))
    return "\n".join(lines)

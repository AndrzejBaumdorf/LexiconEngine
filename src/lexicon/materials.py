"""教材フォルダ (materials/) のJSONファイルの場所・読み書き。デスクトップ版・同期サーバー・ブラウザ版で共通に使う。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from lexicon.languages import find_language
from lexicon.paths import MATERIALS_DIR, SAMPLE_JSON_PATH


def file_label(path: Path, materials_dir: Path = MATERIALS_DIR) -> str:
    """出題範囲の「ファイル」に出す名前。教材フォルダの中なら相対パス (basic/basic_time.json)、外ならファイル名"""
    try:
        return path.resolve().relative_to(materials_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def material_paths(materials_dir: Path = MATERIALS_DIR) -> list[Path]:
    return sorted(materials_dir.rglob("*.json"))


def sync_paths(materials_dir: Path = MATERIALS_DIR) -> list[tuple[Path, str]]:
    """「JSONと照合」で使うファイル: 教材フォルダのJSONすべてと、新規DBに自動で入るサンプル"""
    paths = [(path, file_label(path, materials_dir)) for path in material_paths(materials_dir)]
    if SAMPLE_JSON_PATH.exists():
        paths.append((SAMPLE_JSON_PATH, SAMPLE_JSON_PATH.name))
    return paths


def material_files() -> list[str]:
    # materials/English/<教材>/unit01.json のようなサブフォルダも含めて一覧にする
    return sorted(display_path(path) for path in MATERIALS_DIR.rglob("*.json"))


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(MATERIALS_DIR).as_posix()
    except ValueError:
        return str(path)


def resolve_material(text: str) -> Path:
    path = Path(text.strip()).expanduser()
    return (path if path.is_absolute() else MATERIALS_DIR / path).resolve()


def default_language(path: Path) -> str | None:
    relative = display_path(path)
    top = relative.split("/", 1)[0] if "/" in relative else ""
    language = find_language(top)
    return language.name if language else None


# ファイルごとの設定。ファイルを {"source": ..., "difficulty": ..., "words": [...]} の形にすると、
# 出典・レベルを書いていない単語はファイルの値になる。エディタで新しく入力する単語の初期値にもなる
FILE_SETTING_KEYS = ("source", "difficulty")
WORDS_KEY = "words"


def split_payload(payload: object) -> tuple[dict, list[dict]]:
    """JSONの中身を (ファイルの設定, 単語の一覧) に分ける。ルートは単語の配列、単語1つ、または設定付きの形"""
    if isinstance(payload, dict) and WORDS_KEY in payload:
        settings = {key: payload[key] for key in FILE_SETTING_KEYS if isinstance(payload.get(key), str)}
        entries = payload[WORDS_KEY]
    else:
        settings, entries = {}, [payload] if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("JSONのルートは単語オブジェクトの配列か、{\"words\": [...]} の形にしてください")
    return settings, entries


def apply_settings(settings: dict, entries: list[dict]) -> list[dict]:
    """出典・レベルを書いていない単語にファイルの値を入れる"""
    return [{**entry, **{key: value for key, value in settings.items() if key not in entry}} for entry in entries]


def read_payload(path: Path) -> object:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(path: Path) -> dict:
    return split_payload(read_payload(path))[0]


def load_entries(path: Path) -> list[dict]:
    """単語の一覧。出典・レベルを書いていない単語にはファイルの設定の値が入る"""
    settings, entries = split_payload(read_payload(path))
    return apply_settings(settings, entries)


def write_payload(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def save_entries(path: Path, entries: list[dict], settings: dict | None = None) -> None:
    """単語の一覧を保存する。settings を省略するとファイルの今の設定を残す。設定がなければ単語の配列のまま保存する。
    設定があるときは、ファイルの値と同じ出典・レベルは単語に書かない (ファイルの値を変えれば全部変わるように)"""
    if settings is None:
        settings = load_settings(path) if path.exists() else {}
    settings = {key: settings[key] for key in FILE_SETTING_KEYS if settings.get(key)}
    if not settings:
        write_payload(path, entries)
        return
    words = [{key: value for key, value in entry.items() if settings.get(key) != value} for entry in entries]
    write_payload(path, {**settings, WORDS_KEY: words})


def file_values(settings: dict, entries: list[dict]) -> dict:
    """ファイルの出典・レベル。設定がなければ単語で一番多い値 (設定のない配列のファイルを一括で変えるときの基準)"""
    values = {}
    for key in FILE_SETTING_KEYS:
        if key in settings:
            values[key] = settings[key]
        elif entries:
            values[key] = Counter(entry.get(key) or "" for entry in entries).most_common(1)[0][0]
        else:
            values[key] = ""
    return values


def change_settings(entries: list[dict], old: dict, new: dict) -> list[dict]:
    """ファイルの出典・レベルを old (file_values) から new に変えたときの単語の一覧。
    old と同じ値の単語を new にする。ほかの値を書いた単語 (ファイルの中の例外) はそのまま"""
    changed = []
    for entry in entries:
        entry = dict(entry)
        for key in FILE_SETTING_KEYS:
            if key in new and (entry.get(key) or "") == (old.get(key) or ""):
                if new[key]:
                    entry[key] = new[key]
                else:
                    entry.pop(key, None)
        changed.append(entry)
    return changed

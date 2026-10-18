"""教材フォルダ (materials/) のJSONファイルの場所・読み書き。デスクトップ版・同期サーバー・ブラウザ版で共通に使う。"""
from __future__ import annotations

import json
from pathlib import Path

from languages import find_language

MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"
SAMPLE_JSON_PATH = Path(__file__).resolve().parent.parent / "samples" / "sample_words.json"


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


def load_entries(path: Path) -> list[dict]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = [payload] if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("JSONのルートは単語オブジェクトの配列にしてください")
    return entries


def save_entries(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)

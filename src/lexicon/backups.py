"""DB と教材JSONの自動バックアップ。「JSONと照合」の反映や、iPad の編集の反映など、消える可能性のある操作の前に取る。
data/backups/ に日時付きで保存し、新しいものから KEEP 個だけ残す。"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from lexicon.paths import BACKUP_DIR

KEEP = 30


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_database(connection: sqlite3.Connection, reason: str, backup_dir: Path = BACKUP_DIR) -> Path:
    """開いている DB をそのまま (回答履歴ごと) コピーする。reason はファイル名に入れる操作の名前"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"lexicon-{_stamp()}-{reason}.db"
    connection.commit()
    target = sqlite3.connect(path)
    try:
        connection.backup(target)
    finally:
        target.close()
    _prune(backup_dir, "lexicon-*.db")
    return path


def backup_materials(paths: list[Path], materials_dir: Path, reason: str, backup_dir: Path = BACKUP_DIR) -> Path | None:
    """教材JSONを書き換える前に、そのファイルを教材フォルダからの相対パスのまま写す"""
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    folder = backup_dir / f"materials-{_stamp()}-{reason}"
    for path in existing:
        target = folder / path.resolve().relative_to(materials_dir.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    _prune(backup_dir, "materials-*")
    return folder


def database_backups(backup_dir: Path = BACKUP_DIR) -> list[Path]:
    return sorted(backup_dir.glob("lexicon-*.db"), reverse=True)


def _prune(backup_dir: Path, pattern: str) -> None:
    # 名前に日時が入っているので、名前順の新しいものから KEEP 個を残す
    for old in sorted(backup_dir.glob(pattern), reverse=True)[KEEP:]:
        if old.is_dir():
            shutil.rmtree(old)
        else:
            old.unlink()

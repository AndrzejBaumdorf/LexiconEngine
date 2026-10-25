"""プロジェクトのフォルダの場所。src/ の外 (data/・materials/・samples/) を指すものはここにまとめる。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "lexicon.db"
BACKUP_DIR = DATA_DIR / "backups"
CERT_DIR = DATA_DIR / "certs"
MATERIALS_DIR = ROOT / "materials"
SAMPLE_JSON_PATH = ROOT / "samples" / "sample_words.json"

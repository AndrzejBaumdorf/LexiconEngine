"""端末 (iPad のブラウザ版) で行った単語編集を、同期のときに Mac の教材JSONへ反映する。

編集は「最後に同期したときこの見出しはこうだった (base) → こう変えた (entry)」の形で記録する。
Mac 側の見出しが base のままなら変更を反映し、Mac でも変わっていれば競合として、どちらを残すかを利用者に選ばせる。
見出しの同一性は entry_format.entry_key (単語×言語×出典×レベル)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lexicon.entry_format import entry_key
from lexicon.materials import load_entries, save_entries


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def key_of(entry: dict) -> list[str]:
    return list(entry_key(entry))


def _find(entries: list[dict], key: list[str] | tuple, prefer_hash: str | None = None, exclude: int | None = None) -> int | None:
    matches = [index for index, entry in enumerate(entries) if index != exclude and key_of(entry) == list(key)]
    if not matches:
        return None
    # 同じ見出しが2つあるときは base と同じ中身のものを選ぶ
    return next((index for index in matches if entry_hash(entries[index]) == prefer_hash), matches[0])


def material_path(materials_dir: Path, file: str) -> Path:
    """端末から来たファイル名を教材フォルダの中のパスにする。フォルダの外や JSON 以外は受け付けない"""
    path = (materials_dir / file).resolve()
    if materials_dir.resolve() not in path.parents or path.suffix != ".json":
        raise ValueError(f"教材フォルダの外のファイルは編集できません: {file}")
    return path


def apply_edit(entries: list[dict], edit: dict) -> tuple[str, dict | None, str]:
    """1つの編集を entries に反映する。(結果, 競合したときの Mac 側の見出し, 理由) を返す。
    結果は "applied" (反映した / すでに同じ) か "conflict" """
    base_key, base_hash, new = edit.get("base_key"), edit.get("base_hash"), edit.get("entry")
    if base_key is None:
        # 新しく追加した見出し
        index = _find(entries, key_of(new))
        if index is None:
            entries.append(new)
            return "applied", None, ""
        if entry_hash(entries[index]) == entry_hash(new):
            return "applied", None, ""
        return "conflict", entries[index], "Mac にも同じ見出しがあります"
    index = _find(entries, base_key, base_hash)
    if index is None:
        if new is None:
            return "applied", None, ""
        same = _find(entries, key_of(new))
        if same is not None and entry_hash(entries[same]) == entry_hash(new):
            return "applied", None, ""
        return "conflict", None, "Mac で削除されたか、見出し (単語・出典・レベル) が変わっています"
    current = entries[index]
    if entry_hash(current) != base_hash:
        if new is not None and entry_hash(current) == entry_hash(new):
            return "applied", None, ""
        return "conflict", current, "Mac でも変更されています"
    if new is None:
        del entries[index]
        return "applied", None, ""
    duplicate = _find(entries, key_of(new), exclude=index)
    if duplicate is not None:
        return "conflict", entries[duplicate], "同じ見出しが Mac に別にあります"
    entries[index] = new
    return "applied", None, ""


def force_edit(entries: list[dict], edit: dict) -> None:
    """競合を気にせず編集どおりにする (未同期の編集を、Mac から受け取った教材に重ねて表示するため)"""
    base_key, new = edit.get("base_key"), edit.get("entry")
    index = _find(entries, base_key, edit.get("base_hash")) if base_key is not None else None
    if index is None and new is not None:
        index = _find(entries, key_of(new))
    if new is None:
        if index is not None:
            del entries[index]
    elif index is None:
        entries.append(new)
    else:
        entries[index] = new


def apply_edits(edits: list[dict], materials_dir: Path) -> dict:
    """編集をファイルごとに教材JSONへ反映して保存する。
    {"applied": [編集ID], "conflicts": [...], "renames": [(言語, 古い単語, 新しい単語)], "changed": [ファイル]} を返す"""
    applied, conflicts, renames, changed = [], [], [], []
    by_file: dict[str, list[dict]] = {}
    for edit in edits:
        by_file.setdefault(edit["file"], []).append(edit)
    for file, file_edits in by_file.items():
        path = material_path(materials_dir, file)
        entries = load_entries(path)
        before = json.dumps(entries, ensure_ascii=False)
        for edit in file_edits:
            result, mac_entry, reason = apply_edit(entries, edit)
            if result == "applied":
                applied.append(edit["id"])
                base_key, new = edit.get("base_key"), edit.get("entry")
                # 単語の綴りを直した編集は、回答履歴を新しい綴りに引き継ぐ
                if base_key and new and key_of(new)[0] != base_key[0] and key_of(new)[1] == base_key[1]:
                    renames.append((base_key[1], base_key[0], key_of(new)[0]))
            else:
                conflicts.append({
                    "id": edit["id"], "file": file, "reason": reason, "word": edit.get("word", ""),
                    "mac_entry": mac_entry, "ipad_entry": edit.get("entry"),
                })
        if json.dumps(entries, ensure_ascii=False) != before:
            save_entries(path, entries)
            changed.append(file)
    return {"applied": applied, "conflicts": conflicts, "renames": renames, "changed": changed}

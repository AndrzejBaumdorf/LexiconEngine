"""出題範囲 (言語・出典・レベル・品詞・ファイル) の絞り込み。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuizScope:
    """出題範囲。None や空の条件は絞り込まない。誤答の選択肢は範囲に関係なく同じ言語の単語から選ぶ"""
    language: str | None = None
    sources: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()
    parts_of_speech: tuple[str, ...] = ()
    # 取り込んだJSONファイル (教材フォルダからの相対パス)。"/" で終わるものはフォルダで、その下のファイルすべて
    files: tuple[str, ...] = ()

    def condition(self, word: str = "w", meaning: str = "m", part: str = "p") -> tuple[str, tuple]:
        # 出典・レベルは意味ごとに判定する。A1 の aber (接続詞) と A2 の aber (心態詞) をレベルで出し分けるため
        clauses, params = [f"(? IS NULL OR {word}.language = ?)"], [self.language, self.language]
        if self.parts_of_speech:
            clauses.append(f"{part}.name IN ({', '.join('?' for _ in self.parts_of_speech)})")
            params += self.parts_of_speech
        if self.sources or self.difficulties or self.files:
            entry_clauses = [f"em.meaning_id = {meaning}.id"]
            if self.sources:
                entry_clauses.append(f"s.name IN ({', '.join('?' for _ in self.sources)})")
                params += self.sources
            if self.difficulties:
                entry_clauses.append(f"e.difficulty IN ({', '.join('?' for _ in self.difficulties)})")
                params += self.difficulties
            if self.files:
                files = [file for file in self.files if not file.endswith("/")]
                folders = [file for file in self.files if file.endswith("/")]
                file_clauses = [f"ef.file IN ({', '.join('?' for _ in files)})"] if files else []
                file_clauses += ["substr(ef.file, 1, ?) = ?"] * len(folders)
                entry_clauses.append(
                    f"EXISTS (SELECT 1 FROM entry_files ef WHERE ef.entry_id = e.id AND ({' OR '.join(file_clauses)}))"
                )
                params += files
                for folder in folders:
                    params += (len(folder), folder)
            clauses.append(
                "EXISTS (SELECT 1 FROM entry_meanings em JOIN entries e ON e.id = em.entry_id"
                f" JOIN sources s ON s.id = e.source_id WHERE {' AND '.join(entry_clauses)})"
            )
        return " AND ".join(clauses), tuple(params)


def with_folders(files: list[str]) -> list[str]:
    """出題範囲の「ファイル」の選択肢。ファイルに加えて、その上のフォルダ ("English/JapanTimes_EX/") も選べるようにする。
    一番上 (言語名) のフォルダは、言語で選ぶのと同じなので出さない。パスの順に並べるので、フォルダはその中のファイルの前に来る"""
    folders = {file.rsplit("/", depth)[0] + "/" for file in files for depth in range(1, file.count("/"))}
    return sorted({*files, *folders})

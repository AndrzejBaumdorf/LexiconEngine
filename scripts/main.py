from __future__ import annotations

import argparse
import csv
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.db"


@dataclass(frozen=True)
class Question:
	word_id: int
	prompt: str
	choices: list[str]
	answer: str


SAMPLE_WORDS = [
	("abundant", "豊富な", "The region has abundant natural resources."),
	("accurate", "正確な", "Please provide accurate information."),
	("adapt", "適応する", "Animals adapt to changes in their environment."),
	("clarify", "明確にする", "Could you clarify your main point?"),
	("eliminate", "取り除く", "The new process will eliminate unnecessary steps."),
]


class LexiconDatabase:
	def __init__(self, path: Path):
		self.path = path
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(self.path)
		self.connection.row_factory = sqlite3.Row

	def close(self) -> None:
		self.connection.close()

	def initialize(self, seed_samples: bool = True) -> None:
		self.connection.executescript(
			"""
			PRAGMA foreign_keys = ON;
			CREATE TABLE IF NOT EXISTS words (
				id INTEGER PRIMARY KEY,
				word TEXT NOT NULL UNIQUE,
				meaning_ja TEXT NOT NULL,
				example_sentence TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			);
			CREATE TABLE IF NOT EXISTS word_relations (
				word_id INTEGER NOT NULL,
				related_word_id INTEGER NOT NULL,
				relation_type TEXT NOT NULL CHECK (relation_type IN ('synonym', 'antonym')),
				PRIMARY KEY (word_id, related_word_id, relation_type),
				FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
				FOREIGN KEY (related_word_id) REFERENCES words(id) ON DELETE CASCADE
			);
			CREATE TABLE IF NOT EXISTS answer_history (
				id INTEGER PRIMARY KEY,
				word_id INTEGER NOT NULL,
				question_type TEXT NOT NULL,
				selected_answer TEXT NOT NULL,
				is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
				answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
			);
			"""
		)
		if seed_samples:
			self.connection.executemany(
				"INSERT OR IGNORE INTO words (word, meaning_ja, example_sentence) VALUES (?, ?, ?)",
				SAMPLE_WORDS,
			)
		self.connection.commit()

	def import_csv(self, csv_path: Path) -> int:
		with csv_path.open(newline="", encoding="utf-8-sig") as file:
			rows = csv.DictReader(file)
			if not rows.fieldnames or not {"word", "meaning_ja"}.issubset(rows.fieldnames):
				raise ValueError("CSVには word, meaning_ja 列が必要です")
			imported = 0
			for row in rows:
				self.connection.execute(
					"""
					INSERT INTO words (word, meaning_ja, example_sentence) VALUES (?, ?, ?)
					ON CONFLICT(word) DO UPDATE SET
						meaning_ja = excluded.meaning_ja,
						example_sentence = excluded.example_sentence
					""",
					(row["word"].strip(), row["meaning_ja"].strip(), (row.get("example_sentence") or "").strip()),
				)
				imported += 1
		self.connection.commit()
		return imported

	def create_english_to_japanese_question(self) -> Question:
		word_count = self.connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
		if word_count < 4:
			raise ValueError("4択には4語以上の単語データが必要です")
		target = self.connection.execute(
			"SELECT id, word, meaning_ja FROM words ORDER BY RANDOM() LIMIT 1"
		).fetchone()
		distractors = self.connection.execute(
			"SELECT meaning_ja FROM words WHERE id != ? ORDER BY RANDOM() LIMIT 3",
			(target["id"],),
		).fetchall()
		choices = [target["meaning_ja"], *(row["meaning_ja"] for row in distractors)]
		random.shuffle(choices)
		return Question(target["id"], target["word"], choices, target["meaning_ja"])

	def record_answer(self, question: Question, selected_answer: str) -> bool:
		is_correct = selected_answer == question.answer
		self.connection.execute(
			"""
			INSERT INTO answer_history
				(word_id, question_type, selected_answer, is_correct)
			VALUES (?, 'english_to_japanese', ?, ?)
			""",
			(question.word_id, selected_answer, int(is_correct)),
		)
		self.connection.commit()
		return is_correct

	def recent_accuracy(self, limit: int = 10) -> tuple[int, int, float]:
		row = self.connection.execute(
			"""
			SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
			FROM (SELECT is_correct FROM answer_history ORDER BY id DESC LIMIT ?)
			""",
			(limit,),
		).fetchone()
		total, correct = row["total"], row["correct"]
		return total, correct, (correct / total * 100 if total else 0.0)


def run_quiz(database: LexiconDatabase) -> None:
	total, correct, accuracy = database.recent_accuracy()
	print("英単語クイズを開始します。終了するには q を入力してください。")
	if total:
		print(f"直近{total}問の正答率: {accuracy:.1f}% ({correct}/{total})")
	while True:
		try:
			question = database.create_english_to_japanese_question()
		except ValueError as error:
			print(error)
			return
		print(f"\n{question.prompt}")
		for index, choice in enumerate(question.choices, start=1):
			print(f"  {index}. {choice}")
		answer = input("回答番号: ").strip().lower()
		if answer == "q":
			print("終了しました。")
			return
		if not answer.isdigit() or not 1 <= int(answer) <= len(question.choices):
			print("1〜4の番号を入力してください。")
			continue
		selected = question.choices[int(answer) - 1]
		if database.record_answer(question, selected):
			print("正解!")
		else:
			print(f"不正解。正解は {question.answer} です。")
		total, correct, accuracy = database.recent_accuracy()
		print(f"直近{total}問の正答率: {accuracy:.1f}% ({correct}/{total})")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="SQLiteベースの英単語クイズ")
	parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLiteファイルのパス")
	parser.add_argument("--init", action="store_true", help="DBとサンプルデータを初期化")
	parser.add_argument("--import-csv", type=Path, help="単語CSVを取り込む")
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	database_exists = args.db.exists()
	database = LexiconDatabase(args.db)
	try:
		
		database.initialize(seed_samples=args.init or not database_exists)
		if args.import_csv:
			print(f"{database.import_csv(args.import_csv)}件取り込みました。")
		run_quiz(database)
	finally:
		database.close()


if __name__ == "__main__":
	main()

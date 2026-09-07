from __future__ import annotations

import argparse
from pathlib import Path

from database import LexiconDatabase


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.db"
DEFAULT_SAMPLE_JSON_PATH = Path(__file__).resolve().parent.parent / "materials" / "sample_words.json"
QUESTION_TYPES = {"1": "english_to_japanese", "2": "japanese_to_english", "3": "cloze", "4": "relation"}
QUESTION_LABELS = {"english_to_japanese": "英→日", "japanese_to_english": "日→英", "cloze": "例文穴埋め", "relation": "類義語・対義語"}


def run_quiz(database: LexiconDatabase) -> None:
    print("問題形式: 1 英→日 / 2 日→英 / 3 穴埋め / 4 類義語・対義語 / q 終了")
    while True:
        selected = input("形式: ").strip().lower()
        if selected == "q":
            print("終了しました。")
            return
        question_type = QUESTION_TYPES.get(selected)
        if not question_type:
            print("1〜4またはqを入力してください。")
            continue
        while True:
            question_count_input = input("出題数 (1〜100): ").strip().lower()
            if question_count_input == "q":
                print("終了しました。")
                return
            if question_count_input.isdigit() and 1 <= int(question_count_input) <= 100:
                question_count = int(question_count_input)
                break
            print("出題数は1〜100の整数で入力してください。")
        show_hint = selected == "3" and input("日本語ヒントを表示しますか? (y/N): ").strip().lower() == "y"
        correct_count = 0
        answered_count = 0
        for question_number in range(1, question_count + 1):
            try:
                question = database.create_question(question_type, show_hint)
            except ValueError as error:
                print(error)
                break
            print(f"\n[{question_number}/{question_count}] {question.prompt}")
            for index, choice in enumerate(question.choices, 1):
                print(f"  {index}. {choice}")
            answer = input("回答番号 (qで中断): ").strip().lower()
            if answer == "q":
                print("終了しました。")
                return
            if not answer.isdigit() or not 1 <= int(answer) <= len(question.choices):
                print("1〜4の番号を入力してください。")
                continue
            answered_count += 1
            selected_answer = question.choices[int(answer) - 1]
            if database.record_answer(question, selected_answer):
                correct_count += 1
                print("正解!")
            else:
                print(f"不正解。正解は {question.answer} です。")
        if answered_count:
            total, correct, accuracy = database.recent_accuracy(question.question_type)
            print(f"\n今回: {correct_count}/{answered_count} ({correct_count / answered_count * 100:.1f}%)")
            print(f"{QUESTION_LABELS[question.question_type]} 直近{total}問: {accuracy:.1f}% ({correct}/{total})")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLiteベースの英単語クイズ")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--import-json", type=Path)
    args = parser.parse_args()
    database_exists = args.db.exists()
    database = LexiconDatabase(args.db)
    try:
        database.initialize()
        if not args.import_json and (args.init or not database_exists):
            print(f"{database.import_json(DEFAULT_SAMPLE_JSON_PATH)}件取り込みました。")
        if args.import_json:
            print(f"{database.import_json(args.import_json)}件取り込みました。")
        run_quiz(database)
    finally:
        database.close()


if __name__ == "__main__":
    main()

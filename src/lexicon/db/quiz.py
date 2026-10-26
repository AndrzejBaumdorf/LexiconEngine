"""出題と採点。問題形式ごとの問題文・選択肢の作り方と、スコアによる出題の重み。"""
from __future__ import annotations

import json
import random
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from lexicon.cloze import blank_out, find_cloze_spans
from lexicon.languages import GENDERS, NO_PLURAL, get_language, mark_separable, split_separable
from lexicon.db.scope import QuizScope, with_folders
from lexicon.db.scores import SCORE_MODES, incorrect_score, unlearned_score
from lexicon.db.text import HAS_REAL_MEANING, mask_headword, meanings_similar, valency_note, valency_preposition


CHOICE_POOL_SIZE = 30
# 活用クイズの重みの下限。正解し続けた動詞・人称・時制でもたまには出す (未回答の組み合わせは 0.5³ = 0.125)
MIN_CONJUGATION_WEIGHT = 0.01
# 長いセットでは、この問題数ごとに活用クイズの重みを回答履歴から計算し直す
WEIGHT_REFRESH_INTERVAL = 50
MEANING_SEPARATOR = "\x1f"
# 出題の重みは「基礎の重み + 5 × スコア」。既習にした単語は基礎の重みを下げる
BASE_WEIGHT = 1.0
KNOWN_BASE_WEIGHT = 0.3
# テストモード (範囲の単語を1周ずつ順番に出す) に使える問題形式
TEST_QUESTION_TYPES = ("english_to_japanese", "japanese_to_english")


class ClozeSentence(NamedTuple):
    prompt: str
    # 穴の語の形 (英語の past / participle など)。判定できなければ None
    form: str | None
    surface: str
    # 例文が属する意味。旧形式のJSONから取り込んだ例文は None
    meaning_id: int | None


@dataclass(frozen=True)
class Question:
    word_id: int
    question_type: str
    prompt: str
    # 空なら選択式ではなく入力式 (複数形など、誤った形を見せたくない問題)
    choices: list[str]
    answer: str
    # 正解の補足表示 (穴埋めで活用形が正解のとき "petrified (petrify)" など)
    answer_detail: str = ""
    # 入力式で答えの前に付けてもよい語 (複数形の die など)
    optional_prefixes: tuple[str, ...] = ()
    # 入力式で answer のほかに正解とする書き方 (am Tisch に対する an dem Tisch など)
    accepted: tuple[str, ...] = ()
    # 問題の種類の内訳。回答履歴に残して集計に使う (活用クイズの {"person": "du", "tense": "現在"})
    variant: dict | None = None

    @property
    def is_typed(self) -> bool:
        return not self.choices

    def is_correct(self, selected_answer: str) -> bool:
        if not self.is_typed:
            return selected_answer == self.answer
        # 入力式は前後・連続する空白と、先頭の die などを無視する。大文字・小文字は区別しない
        words = selected_answer.split()
        if len(words) > 1 and words[0].lower() in self.optional_prefixes:
            words = words[1:]
        typed = " ".join(words).casefold()
        return any(typed == " ".join(answer.split()).casefold() for answer in (self.answer, *self.accepted))


class QuizMixin:
    """LexiconDatabase の出題と採点"""

    def begin_quiz_set(self, score_mode: str = "unlearned") -> None:
        """新しいセットを始める。score_mode は出題の重みに使うスコア (誤答スコア / 未習得スコア)。
        活用クイズの重みはセットの最初の出題時に回答履歴から計算し、
        WEIGHT_REFRESH_INTERVAL 問ごとに計算し直す。その間は固定する (直前に間違えた形ばかり続けて出ないように)"""
        if score_mode not in SCORE_MODES:
            raise ValueError(f"出題モードは {' / '.join(SCORE_MODES)} のどれかにしてください: {score_mode}")
        self._set_weights = {}
        self.score_mode = score_mode

    def _related_word_ids(self, word_id: int) -> tuple[int, ...]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT related_word.id
            FROM meaning_relations mr
            JOIN meanings related_meaning ON related_meaning.id = mr.related_meaning_id
            JOIN parts_of_speech related_pos ON related_pos.id = related_meaning.part_of_speech_id
            JOIN words related_word ON related_word.id = related_pos.word_id
            JOIN meanings target_meaning ON target_meaning.id = mr.meaning_id
            JOIN parts_of_speech target_pos ON target_pos.id = target_meaning.part_of_speech_id
            WHERE target_pos.word_id=?
            UNION
            SELECT DISTINCT target_word.id
            FROM meaning_relations mr
            JOIN meanings target_meaning ON target_meaning.id = mr.meaning_id
            JOIN parts_of_speech target_pos ON target_pos.id = target_meaning.part_of_speech_id
            JOIN words target_word ON target_word.id = target_pos.word_id
            JOIN meanings related_meaning ON related_meaning.id = mr.related_meaning_id
            JOIN parts_of_speech related_pos ON related_pos.id = related_meaning.part_of_speech_id
            WHERE related_pos.word_id=?
            UNION
            SELECT derived_word_id FROM derivations WHERE word_id=?
            UNION
            SELECT word_id FROM derivations WHERE derived_word_id=?
            """,
            (word_id, word_id, word_id, word_id),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _choice_exclusion_ids(self, word_id: int) -> tuple[int, ...]:
        return (word_id, *self._related_word_ids(word_id))

    def _choice_candidates(
        self, column: str, excluded_ids: tuple[int, ...], part_of_speech: str, answer: str, language: str, answer_word_id: int,
        required_part_of_speech: str | None = None,
    ) -> list[str]:
        # 同じ言語・同じ品詞の候補を優先し、足りない場合のみ他の品詞で補う
        placeholders = ", ".join("?" for _ in excluded_ids)
        rows = self.connection.execute(
            f"""
            SELECT {column} AS choice, MIN(w.id) AS word_id, GROUP_CONCAT(m.meaning_ja, char(31)) AS meanings
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE w.id NOT IN ({placeholders}) AND {column} != ? AND {HAS_REAL_MEANING} AND w.language = ?
              AND (? IS NULL OR p.name = ?)
            GROUP BY {column}
            ORDER BY MAX(p.name = ?) DESC, RANDOM()
            LIMIT ?
            """,
            (*excluded_ids, answer, language, required_part_of_speech, required_part_of_speech, part_of_speech, CHOICE_POOL_SIZE),
        ).fetchall()

        # 正解や他の選択肢とほぼ同じ意味の候補 (辛辣な / 辛らつな など) は、正解が複数になったり
        # 消去法で答えが分かったりするので避ける。互いに類義語・派生語の関係にある候補も避ける
        picked: list[sqlite3.Row] = []
        used_meanings = self._word_meanings(answer_word_id)
        related_ids: set[int] = set()
        for row in rows:
            meanings = row["meanings"].split(MEANING_SEPARATOR)
            if row["word_id"] in related_ids or any(meanings_similar(meaning, used) for meaning in meanings for used in used_meanings):
                continue
            picked.append(row)
            used_meanings += meanings
            related_ids.update(self._related_word_ids(row["word_id"]))
            if len(picked) == 3:
                break
        # 候補が少なくて足りないときは、条件を外して残りから補う
        for row in rows:
            if len(picked) == 3:
                break
            if row not in picked:
                picked.append(row)
        return [row["choice"] for row in picked]

    def _word_meanings(self, word_id: int) -> list[str]:
        rows = self.connection.execute(
            "SELECT m.meaning_ja FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id WHERE p.word_id=?",
            (word_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def _derivative_candidates(self, base: sqlite3.Row, answer: str, part_of_speech: str) -> list[str]:
        # 元の単語や別品詞の派生語を優先し、同じ品詞の派生語は正解が複数になるので除外する
        rows = self.connection.execute(
            """
            SELECT w.word
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            LEFT JOIN derivations d ON d.word_id = ? AND d.derived_word_id = w.id
            WHERE w.language = ? AND w.word != ?
              AND (d.part_of_speech IS NULL OR d.part_of_speech != ?)
            GROUP BY w.word
            ORDER BY MAX(w.id = ? OR d.word_id IS NOT NULL) DESC, MAX(p.name = ?) DESC, RANDOM()
            LIMIT 3
            """,
            (base["id"], base["language"], answer, part_of_speech, base["id"], part_of_speech),
        ).fetchall()
        return [row[0] for row in rows]

    def _cloze_sentences(self, language: str | None) -> dict[int, list[ClozeSentence]]:
        """穴埋めに使える例文を単語ごとに返す"""
        rows = self.connection.execute(
            """
            SELECT e.word_id, e.sentence, e.targets, e.meaning_id, w.word, w.language, w.separable_prefix,
                (SELECT GROUP_CONCAT(m.forms, char(31))
                 FROM parts_of_speech p JOIN meanings m ON m.part_of_speech_id = p.id
                 WHERE p.word_id = w.id AND m.forms IS NOT NULL) AS forms
            FROM examples e
            JOIN words w ON w.id = e.word_id
            WHERE ? IS NULL OR w.language = ?
            """,
            (language, language),
        ).fetchall()
        sentences: dict[int, list[ClozeSentence]] = {}
        for row in rows:
            targets = json.loads(row["targets"]) if row["targets"] else None
            # 登録された複数形・活用形 (gemacht など) も穴にする語として使う
            language_spec = get_language(row["language"])
            extra_forms = [
                form for forms in (row["forms"].split("\x1f") if row["forms"] else ())
                for form in language_spec.cloze_forms(json.loads(forms))
            ]
            headword = mark_separable(row["word"], row["separable_prefix"])
            spans = find_cloze_spans(row["sentence"], headword, row["language"], targets, extra_forms)
            if not spans:
                continue
            start, end = spans[0]
            surface = row["sentence"][start:end]
            form = None
            if len(spans) == 1 and language_spec.classify_form:
                form = language_spec.classify_form(row["word"], surface, row["sentence"][:start])
            sentences.setdefault(row["word_id"], []).append(ClozeSentence(blank_out(row["sentence"], spans), form, surface, row["meaning_id"]))
        return sentences

    def _target_filter(self, question_type: str, language: str | None) -> tuple[str, str]:
        """問題形式ごとの、出題できる意味の条件 (JOIN 句, WHERE 句)。出題・テストモード・習得率で共通に使う"""
        if question_type in ("english_to_japanese", "japanese_to_english", "romanization"):
            return "", HAS_REAL_MEANING
        if question_type == "cloze":
            # 例文が属する意味を出題対象にする (例文の意味が分からない旧形式の例文は単語ごと)
            sentences = self._cloze_sentences(language)
            meaning_ids = {item.meaning_id for items in sentences.values() for item in items if item.meaning_id is not None}
            unlinked_word_ids = {word_id for word_id, items in sentences.items() if any(item.meaning_id is None for item in items)}
            return "", (
                f"{HAS_REAL_MEANING} AND (m.id IN ({', '.join(map(str, meaning_ids)) or 'NULL'})"
                f" OR w.id IN ({', '.join(map(str, unlinked_word_ids)) or 'NULL'}))"
            )
        if question_type == "gender":
            return "", f"json_extract(m.forms, '$.gender') IN ({', '.join(repr(code) for code in GENDERS)})"
        if question_type == "plural":
            # 複数形なし (Ausland の "-" など) は出題しない
            return "", f"TRIM(json_extract(m.forms, '$.plural')) NOT IN ({', '.join(repr(value) for value in NO_PLURAL)})"
        if question_type == "derivative":
            return "JOIN derivations d ON d.word_id = w.id", HAS_REAL_MEANING
        if question_type == "valency":
            return "", "m.id IN (SELECT meaning_id FROM examples WHERE valency IS NOT NULL AND meaning_id IS NOT NULL)"
        if question_type == "relation":
            return "", "m.id IN (SELECT meaning_id FROM meaning_relations)"
        raise ValueError(f"未対応の問題形式です: {question_type}")

    def _target_rows(self, question_type: str, scope: QuizScope, word_id: int | None = None) -> list[sqlite3.Row]:
        """出題範囲で出題できる (単語, 意味) の一覧。known は既習にした単語なら 1"""
        join, where = self._target_filter(question_type, scope.language)
        scope_condition, scope_params = scope.condition()
        word_condition = "w.id = ?" if word_id is not None else "1"
        return self.connection.execute(
            f"""
            SELECT w.id, w.word, w.language, w.separable_prefix, p.name AS part_of_speech, m.id AS meaning_id, m.meaning_ja, m.forms, m.valency,
                EXISTS (SELECT 1 FROM known_words k WHERE k.language = w.language AND k.word = w.word AND k.known = 1) AS known
            FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            {join}
            WHERE {where} AND {scope_condition} AND {word_condition}
            GROUP BY w.id, m.id
            ORDER BY RANDOM()
            """,
            (*scope_params, *(() if word_id is None else (word_id,))),
        ).fetchall()

    def _weighted_word(self, question_type: str, scope: QuizScope, word_id: int | None = None) -> sqlite3.Row:
        """出題する (単語, 意味) を重み「基礎の重み + 5 × スコア」で選ぶ。word_id を指定するとその単語の意味から選ぶ (テストモード)"""
        rows = self._target_rows(question_type, scope, word_id)
        if not rows:
            raise ValueError("出題範囲の条件に合う単語データがありません")
        if word_id is not None:
            return rows[0]
        scores = self._word_scores(question_type)
        return random.choices(
            rows, weights=[(KNOWN_BASE_WEIGHT if row["known"] else BASE_WEIGHT) + 5.0 * scores[row["id"]] for row in rows], k=1,
        )[0]

    def test_word_ids(self, question_type: str, scope: QuizScope, rounds: int = 1) -> list[int]:
        """テストモードの出題順。範囲の単語を1周ずつランダムな順で並べ、rounds 周分つなげる (周の境目で同じ単語が続かないようにする)"""
        if question_type not in TEST_QUESTION_TYPES:
            raise ValueError("テストモードは外→日・日→外の問題形式で使えます")
        word_ids = sorted({row["id"] for row in self._target_rows(question_type, scope)})
        if not word_ids:
            raise ValueError("出題範囲の条件に合う単語データがありません")
        order: list[int] = []
        for _ in range(rounds):
            lap = random.sample(word_ids, len(word_ids))
            if order and len(lap) > 1 and lap[0] == order[-1]:
                lap[0], lap[-1] = lap[-1], lap[0]
            order += lap
        return order

    def mastery(self, question_type: str, scope: QuizScope) -> tuple[float, int]:
        """習得率 = 1 - Σ未習得スコア / 単語数 (出題範囲でこの問題形式に出せる単語)。(習得率, 単語数) を返す。単語がなければ (0, 0)"""
        if question_type == "conjugation":
            word_ids = {candidate[0]["id"] for candidate in self._conjugation_candidates(scope)}
        else:
            word_ids = {row["id"] for row in self._target_rows(question_type, scope)}
        if not word_ids:
            return 0.0, 0
        results = self._recent_results("h.question_type = ?", (question_type,))
        scores = {word_id: unlearned_score(values) for (word_id, _), values in results.items()}
        return 1.0 - sum(scores.get(word_id, 1.0) for word_id in word_ids) / len(word_ids), len(word_ids)

    def _word_scores(self, question_type: str) -> defaultdict[int, float]:
        """出題モードのスコアを単語ごとに返す。回答のない単語は、誤答スコアなら 0、未習得スコアなら 1"""
        results = self._recent_results("h.question_type = ?", (question_type,))
        if self.score_mode == "unlearned":
            return defaultdict(lambda: 1.0, {word_id: unlearned_score(values) for (word_id, _), values in results.items()})
        return defaultdict(float, {word_id: incorrect_score(values) for (word_id, _), values in results.items()})

    def languages(self) -> list[str]:
        """登録されている言語を単語数の多い順に返す"""
        rows = self.connection.execute("SELECT language FROM words GROUP BY language ORDER BY COUNT(*) DESC, language").fetchall()
        return [row[0] for row in rows]

    def scope_options(self, language: str) -> tuple[list[str], list[str], list[str], list[str]]:
        """その言語で選べる出典・レベル・品詞・ファイルを返す。品詞は見出し語の意味が多い順"""
        sources = self.connection.execute(
            "SELECT DISTINCT s.name FROM entries e JOIN words w ON w.id = e.word_id JOIN sources s ON s.id = e.source_id"
            " WHERE w.language=? ORDER BY s.name",
            (language,),
        ).fetchall()
        difficulties = self.connection.execute(
            "SELECT DISTINCT e.difficulty FROM entries e JOIN words w ON w.id = e.word_id"
            " WHERE w.language=? AND e.difficulty != '' ORDER BY e.difficulty",
            (language,),
        ).fetchall()
        parts_of_speech = self.connection.execute(
            f"""
            SELECT p.name FROM words w
            JOIN parts_of_speech p ON p.word_id = w.id
            JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE w.language=? AND {HAS_REAL_MEANING} AND m.id IN (SELECT meaning_id FROM entry_meanings)
            GROUP BY p.name ORDER BY COUNT(*) DESC, p.name
            """,
            (language,),
        ).fetchall()
        files = [row[0] for row in self.connection.execute(
            "SELECT DISTINCT f.file FROM entry_files f JOIN entries e ON e.id = f.entry_id JOIN words w ON w.id = e.word_id"
            " WHERE w.language=? ORDER BY f.file",
            (language,),
        )]
        return [row[0] for row in sources], [row[0] for row in difficulties], [row[0] for row in parts_of_speech], with_folders(files)

    def create_question(
        self, question_type: str, show_hint: bool = False, scope: QuizScope | None = None, word_id: int | None = None,
    ) -> Question:
        """問題を作る。word_id を指定するとその単語を出す (テストモード。外→日・日→外だけ)"""
        scope = scope or QuizScope()
        language = scope.language
        if word_id is not None and question_type not in TEST_QUESTION_TYPES:
            raise ValueError("単語を指定して出題できるのは外→日・日→外だけです")
        if question_type == "english_to_japanese":
            target = self._weighted_word(question_type, scope, word_id)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["meaning_ja"], *self._choice_candidates("m.meaning_ja", excluded_ids, target["part_of_speech"], target["meaning_ja"], target["language"], target["id"])]
            random.shuffle(choices)
            # 分離動詞は ab|fahren のように印を付けて見せる
            headword = mark_separable(target["word"], target["separable_prefix"])
            prompt = get_language(target["language"]).headword(headword, json.loads(target["forms"] or "{}"))
            return Question(target["id"], question_type, prompt + valency_note(target["language"], target["valency"]), choices, target["meaning_ja"])
        if question_type == "japanese_to_english":
            target = self._weighted_word(question_type, scope, word_id)
            excluded_ids = self._choice_exclusion_ids(target["id"])
            choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"], target["language"], target["id"])]
            random.shuffle(choices)
            prompt = mask_headword(target["meaning_ja"], target["word"], target["language"]) + valency_note(target["language"], target["valency"])
            return Question(target["id"], question_type, prompt, choices, target["word"])
        if question_type == "cloze":
            sentences = self._cloze_sentences(language)
            if not sentences:
                raise ValueError("穴埋めに使える例文がありません")
            # 例文が属する意味を出題対象にし、品詞 (誤答の候補) とヒントをその意味に合わせる。
            # vent の動詞の例文で、名詞「通気孔」の品詞やヒントが使われないようにするため
            target = self._weighted_word(question_type, scope)
            prompt, form, surface, _ = random.choice([
                item for item in sentences[target["id"]] if item.meaning_id in (None, target["meaning_id"])
            ])
            if show_hint:
                prompt += f"\nヒント: {mask_headword(target['meaning_ja'], target['word'], target['language'])}"
            excluded_ids = self._choice_exclusion_ids(target["id"])
            language_spec = get_language(target["language"])
            if form in (None, "base") or language_spec.inflect_to is None:
                choices = [target["word"], *self._choice_candidates("w.word", excluded_ids, target["part_of_speech"], target["word"], target["language"], target["id"])]
                random.shuffle(choices)
                return Question(target["id"], question_type, prompt, choices, target["word"])

            # 穴の語が活用形 (petrified など) なら、選択肢も同じ形にそろえる。過去形などは動詞だけから選ぶ
            required_part_of_speech = language_spec.form_parts_of_speech.get(form)
            candidates = self._choice_candidates(
                "w.word", excluded_ids, required_part_of_speech or target["part_of_speech"], target["word"], target["language"], target["id"],
                required_part_of_speech,
            )
            answer = surface.lower()
            choices = [answer]
            for candidate in candidates:
                inflected = language_spec.inflect_to(candidate, form)
                if inflected not in choices:
                    choices.append(inflected)
            random.shuffle(choices)
            return Question(target["id"], question_type, prompt, choices, answer, f"{answer} ({target['word']})")
        if question_type == "relation":
            scope_condition, scope_params = scope.condition()
            relation = self.connection.execute(
                f"""
                SELECT mr.*
                FROM meaning_relations mr
                JOIN meanings m ON m.id = mr.meaning_id
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE {scope_condition}
                ORDER BY RANDOM() LIMIT 1
                """,
                scope_params,
            ).fetchone()
            if not relation:
                raise ValueError("類義語・対義語データがありません")
            target = self.connection.execute(
                """
                SELECT w.id, w.word, m.meaning_ja
                FROM meanings m
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE m.id=?
                """,
                (relation["meaning_id"],),
            ).fetchone()
            related_row = self.connection.execute(
                """
                SELECT w.id, w.word, w.language, p.name AS part_of_speech
                FROM meanings m
                JOIN parts_of_speech p ON p.id = m.part_of_speech_id
                JOIN words w ON w.id = p.word_id
                WHERE m.id=?
                """,
                (relation["related_meaning_id"],),
            ).fetchone()
            related = related_row["word"]
            choices = [related, *self._choice_candidates("w.word", (target["id"], related_row["id"]), related_row["part_of_speech"], related, related_row["language"], related_row["id"])]
            random.shuffle(choices)
            label = "類義語" if relation["relation_type"] == "synonym" else "対義語"
            return Question(target["id"], question_type, f"{target['word']} の{label}は?", choices, related)
        if question_type in ("romanization", "gender", "plural", "valency", "conjugation") and not get_language(language or "").supports(question_type):
            raise ValueError(f"{language} はこの問題形式に対応していません")
        if question_type == "gender":
            target = self._weighted_word(question_type, scope)
            articles = get_language(target["language"]).articles
            gender = json.loads(target["forms"])["gender"]
            # der / die / das は毎回同じ並びで出す
            return Question(
                target["id"], question_type, f"{target['word']} ({target['meaning_ja']})", list(articles.values()), articles[gender],
                f"{articles[gender]} {target['word']}",
            )
        if question_type == "valency":
            return self._valency_question(scope)
        if question_type == "conjugation":
            return self._conjugation_question(scope)
        if question_type == "plural":
            target = self._weighted_word(question_type, scope)
            forms = json.loads(target["forms"])
            language_spec = get_language(target["language"])
            answer = forms["plural"].strip()
            # 誤った複数形を目にしないよう、選択肢は出さずに入力させる。単数形には冠詞を付けて出す (die Abfahrt)
            headword = language_spec.headword(target["word"], {"gender": forms["gender"]} if forms.get("gender") else {})
            article = language_spec.plural_article
            return Question(
                target["id"], question_type, f"{headword} ({target['meaning_ja']}) の複数形は?", [], answer,
                f"{article} {answer}" if article else "", (article,) if article else (),
            )
        if question_type == "romanization":
            language_spec = get_language(language or "")
            target = self._weighted_word(question_type, scope)
            answer = language_spec.romanize(target["word"])
            if answer is None:
                raise ValueError(f"{target['word']} を転写できません")
            choices = [answer, *language_spec.romanization_distractors(target["word"], answer)]
            # 誤答が作りにくい短い語 (하나 など) は、同じ言語の他の単語の転写で補う
            if len(choices) < 4:
                for row in self.connection.execute(
                    "SELECT word FROM words WHERE language=? AND id != ? ORDER BY RANDOM() LIMIT 20", (target["language"], target["id"])
                ):
                    other = language_spec.romanize(row["word"])
                    if other and other not in choices:
                        choices.append(other)
                    if len(choices) == 4:
                        break
            random.shuffle(choices)
            return Question(target["id"], question_type, f"{target['word']} ({target['meaning_ja']})", choices, answer)
        if question_type == "derivative":
            target = self._weighted_word(question_type, scope)
            derivation = self.connection.execute(
                """
                SELECT w.word, d.part_of_speech
                FROM derivations d JOIN words w ON w.id = d.derived_word_id
                WHERE d.word_id=? ORDER BY RANDOM() LIMIT 1
                """,
                (target["id"],),
            ).fetchone()
            answer = derivation["word"]
            choices = [answer, *self._derivative_candidates(target, answer, derivation["part_of_speech"])]
            random.shuffle(choices)
            prompt = f"{target['word']} ({target['part_of_speech']}) の{derivation['part_of_speech']}形は?"
            return Question(target["id"], question_type, prompt, choices, answer)
        raise ValueError(f"未対応の問題形式です: {question_type}")

    def _valency_question(self, scope: QuizScope) -> Question:
        """格支配クイズ。格支配の穴を持つ例文 (AI生成) の穴に、格変化させた句 (前置詞句・再帰代名詞) を入力させる。
        例: ab|holen 迎えに行く ｜ Mein Vater holt mich ___ ab. (der Bahnhof) → vom Bahnhof"""
        target = self._weighted_word("valency", scope)
        example = random.choice(self.connection.execute(
            "SELECT sentence, translation_ja, valency FROM examples WHERE meaning_id=? AND valency IS NOT NULL", (target["meaning_id"],)
        ).fetchall())
        valency = json.loads(example["valency"])
        spec = get_language(target["language"])
        answers = spec.valency_answers(valency["slot"], valency["hint"], valency_preposition(target["word"], target["part_of_speech"]))
        # 例文に書かれた形を正解の表示に使い、融合しない形 (von dem Bahnhof) なども正解にする
        phrase = valency["phrase"]
        accepted = tuple(answer for answer in answers if answer.casefold() != phrase.casefold())
        hint = "再帰代名詞" if valency["slot"].startswith("sich+") else valency["hint"].replace(" (Pl.)", "、複数")
        headword = mark_separable(target["word"], target["separable_prefix"])
        sentence = example["sentence"].replace(phrase, "___", 1)
        prompt = f"{headword}  {target['meaning_ja']}\n{sentence}  ({hint})"
        if example["translation_ja"]:
            prompt += f"\n{example['translation_ja']}"
        return Question(target["id"], "valency", prompt, [], phrase, example["sentence"], (), accepted)

    def _conjugation_question(self, scope: QuizScope) -> Question:
        """活用クイズ。人称と時制を示して、活用した形 (分離動詞は前つづりも、再帰動詞は再帰代名詞も) を入力させる。
        例: ab|fahren 出発する ｜ du・現在 → fährst ab
        出題は 動詞の誤り率 × 人称の誤り率 × 時制の誤り率 に比例させ、苦手な人称・時制を別の動詞でも練習させる"""
        key = ("conjugation", scope)
        # (候補と重み, 計算してから出した問題数)。WEIGHT_REFRESH_INTERVAL 問出したら計算し直す
        cached = self._set_weights.get(key) if self._set_weights is not None else None
        if cached is None or cached[1] >= WEIGHT_REFRESH_INTERVAL:
            cached = (self._conjugation_candidates(scope), 0)
        candidates = cached[0]
        if self._set_weights is not None:
            self._set_weights[key] = (candidates, cached[1] + 1)
        if not candidates:
            raise ValueError("出題範囲に、活用クイズにできる動詞 (現在形・過去形などが登録された動詞) がありません")
        target, cell, spec = random.choices([candidate[:3] for candidate in candidates], weights=[candidate[3] for candidate in candidates], k=1)[0]
        headword = mark_separable(target["word"], target["separable_prefix"])
        prompt = f"{headword}  {target['meaning_ja']}\n{cell.person}・{cell.tense} → ___"
        pronouns = spec.conjugation_pronouns.get(cell.person, ())
        return Question(
            target["id"], "conjugation", prompt, [], cell.answers[0], f"{pronouns[0] if pronouns else ''} {cell.answers[0]}".strip(),
            pronouns, tuple(cell.answers[1:]), {"person": cell.person, "tense": cell.tense},
        )

    def _conjugation_candidates(self, scope: QuizScope) -> list[tuple]:
        """出題範囲の (意味, 人称・時制, 言語仕様, 重み) をすべて作る。誤り率は回答履歴から (誤答+1)/(回答+2) で平滑化する
        (未回答は 0.5)。どの組み合わせも出なくならないよう重みに下限を設ける"""
        scope_condition, scope_params = scope.condition()
        rows = self.connection.execute(
            f"""
            SELECT w.id, w.word, w.language, w.separable_prefix, m.id AS meaning_id, m.meaning_ja, m.forms, m.valency,
                EXISTS (SELECT 1 FROM known_words k WHERE k.language = w.language AND k.word = w.word AND k.known = 1) AS known
            FROM words w JOIN parts_of_speech p ON p.word_id = w.id JOIN meanings m ON m.part_of_speech_id = p.id
            WHERE m.forms IS NOT NULL
              AND {scope_condition}
            """,
            scope_params,
        ).fetchall()

        def smoothed(rows_: list[sqlite3.Row]) -> dict:
            return {row["key"]: (row["answered"] - row["correct"] + 1) / (row["answered"] + 2) for row in rows_}

        history = """
            FROM answer_history h JOIN words w ON w.id = h.word_id
            WHERE h.question_type = 'conjugation' AND (? IS NULL OR w.language = ?)
        """
        language_params = (scope.language, scope.language)
        verb_rates = smoothed(self.connection.execute(
            f"SELECT h.word_id AS key, COUNT(*) AS answered, SUM(h.is_correct) AS correct {history} GROUP BY h.word_id", language_params
        ).fetchall())
        person_rates, tense_rates = (
            smoothed(self.connection.execute(
                f"SELECT json_extract(h.variant, '$.{field}') AS key, COUNT(*) AS answered, SUM(h.is_correct) AS correct {history}"
                f" AND h.variant IS NOT NULL GROUP BY key",
                language_params,
            ).fetchall())
            for field in ("person", "tense")
        )
        # 意味が複数ある動詞 (aus|sehen) が意味の数だけ出やすくならないよう、意味の数で割る
        meanings_per_word = Counter(row["id"] for row in rows)
        candidates = []
        for row in rows:
            spec = get_language(row["language"])
            if spec.conjugation_table is None:
                continue
            # 格支配に sich+Akk / sich+Dat がある意味 (sich ändern) は再帰代名詞も入れて活用する
            reflexive = next((item.rpartition("+")[2] for item in json.loads(row["valency"] or "[]") if item.startswith("sich+")), None)
            for cell in spec.conjugation_table(split_separable(row["word"])[0], row["separable_prefix"], json.loads(row["forms"]), reflexive):
                weight = verb_rates.get(row["id"], 0.5) * person_rates.get(cell.person, 0.5) * tense_rates.get(cell.tense, 0.5)
                # 既習にした動詞は、ほかの問題形式の基礎の重みと同じ割合で出にくくする
                if row["known"]:
                    weight *= KNOWN_BASE_WEIGHT / BASE_WEIGHT
                candidates.append((row, cell, spec, max(weight, MIN_CONJUGATION_WEIGHT) / meanings_per_word[row["id"]]))
        return candidates

    def record_answer(self, question: Question, selected_answer: str) -> bool:
        """回答を記録して正誤を返す。間違えた単語の既習の印を外したら unmarked_known が True になる"""
        correct = question.is_correct(selected_answer)
        self.connection.execute(
            "INSERT INTO answer_history(word_id, question_type, selected_answer, correct_answer, is_correct, variant, uid)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                question.word_id, question.question_type, selected_answer, question.answer, int(correct),
                json.dumps(question.variant, ensure_ascii=False) if question.variant else None, uuid.uuid4().hex,
            ),
        )
        self.connection.commit()
        # 既習にした単語を間違えたら (スキップも含む) 既習の印を外す
        self.unmarked_known = not correct and self._unmark_known(question.word_id)
        return correct

    def _unmark_known(self, word_id: int) -> bool:
        """単語に既習の印があれば外す。外したら True"""
        row = self.connection.execute(
            "SELECT w.language, w.word FROM words w JOIN known_words k ON k.language = w.language AND k.word = w.word"
            " WHERE w.id = ? AND k.known = 1",
            (word_id,),
        ).fetchone()
        if row is None:
            return False
        self.set_known(row["language"], row["word"], False)
        return True

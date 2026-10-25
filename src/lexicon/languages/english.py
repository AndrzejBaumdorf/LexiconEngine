"""英語の活用形の判定と生成。例文穴埋めで選択肢の形を穴の語にそろえるために使う。"""
from __future__ import annotations

import re


VOWELS = "aeiou"
# 原形: (過去形, 過去分詞)
IRREGULAR_VERBS = {
    "arise": ("arose", "arisen"), "awake": ("awoke", "awoken"), "be": ("was", "been"), "bear": ("bore", "borne"),
    "beat": ("beat", "beaten"), "become": ("became", "become"), "begin": ("began", "begun"), "bend": ("bent", "bent"),
    "bet": ("bet", "bet"), "bid": ("bid", "bid"), "bind": ("bound", "bound"), "bite": ("bit", "bitten"),
    "bleed": ("bled", "bled"), "blow": ("blew", "blown"), "break": ("broke", "broken"), "breed": ("bred", "bred"),
    "bring": ("brought", "brought"), "build": ("built", "built"), "burst": ("burst", "burst"), "buy": ("bought", "bought"),
    "cast": ("cast", "cast"), "catch": ("caught", "caught"), "choose": ("chose", "chosen"), "cling": ("clung", "clung"),
    "come": ("came", "come"), "cost": ("cost", "cost"), "creep": ("crept", "crept"), "cut": ("cut", "cut"),
    "deal": ("dealt", "dealt"), "dig": ("dug", "dug"), "do": ("did", "done"), "draw": ("drew", "drawn"),
    "drink": ("drank", "drunk"), "drive": ("drove", "driven"), "eat": ("ate", "eaten"), "fall": ("fell", "fallen"),
    "feed": ("fed", "fed"), "feel": ("felt", "felt"), "fight": ("fought", "fought"), "find": ("found", "found"),
    "flee": ("fled", "fled"), "fling": ("flung", "flung"), "fly": ("flew", "flown"), "forbid": ("forbade", "forbidden"),
    "forget": ("forgot", "forgotten"), "forgive": ("forgave", "forgiven"), "forsake": ("forsook", "forsaken"),
    "freeze": ("froze", "frozen"), "get": ("got", "gotten"), "give": ("gave", "given"), "go": ("went", "gone"),
    "grind": ("ground", "ground"), "grow": ("grew", "grown"), "hang": ("hung", "hung"), "have": ("had", "had"),
    "hear": ("heard", "heard"), "hide": ("hid", "hidden"), "hit": ("hit", "hit"), "hold": ("held", "held"),
    "hurt": ("hurt", "hurt"), "keep": ("kept", "kept"), "kneel": ("knelt", "knelt"), "know": ("knew", "known"),
    "lay": ("laid", "laid"), "lead": ("led", "led"), "lean": ("leant", "leant"), "leap": ("leapt", "leapt"),
    "leave": ("left", "left"), "lend": ("lent", "lent"), "let": ("let", "let"), "lie": ("lay", "lain"),
    "light": ("lit", "lit"), "lose": ("lost", "lost"), "make": ("made", "made"), "mean": ("meant", "meant"),
    "meet": ("met", "met"), "pay": ("paid", "paid"), "put": ("put", "put"), "quit": ("quit", "quit"),
    "read": ("read", "read"), "ride": ("rode", "ridden"), "ring": ("rang", "rung"), "rise": ("rose", "risen"),
    "run": ("ran", "run"), "say": ("said", "said"), "see": ("saw", "seen"), "seek": ("sought", "sought"),
    "sell": ("sold", "sold"), "send": ("sent", "sent"), "set": ("set", "set"), "shake": ("shook", "shaken"),
    "shed": ("shed", "shed"), "shine": ("shone", "shone"), "shoot": ("shot", "shot"), "show": ("showed", "shown"),
    "shrink": ("shrank", "shrunk"), "shut": ("shut", "shut"), "sing": ("sang", "sung"), "sink": ("sank", "sunk"),
    "sit": ("sat", "sat"), "slay": ("slew", "slain"), "sleep": ("slept", "slept"), "slide": ("slid", "slid"),
    "sling": ("slung", "slung"), "slit": ("slit", "slit"), "speak": ("spoke", "spoken"), "speed": ("sped", "sped"),
    "spend": ("spent", "spent"), "spin": ("spun", "spun"), "spit": ("spat", "spat"), "split": ("split", "split"),
    "spread": ("spread", "spread"), "spring": ("sprang", "sprung"), "stand": ("stood", "stood"), "steal": ("stole", "stolen"),
    "stick": ("stuck", "stuck"), "sting": ("stung", "stung"), "stink": ("stank", "stunk"), "stride": ("strode", "stridden"),
    "strike": ("struck", "struck"), "strive": ("strove", "striven"), "swear": ("swore", "sworn"), "sweep": ("swept", "swept"),
    "swell": ("swelled", "swollen"), "swim": ("swam", "swum"), "swing": ("swung", "swung"), "take": ("took", "taken"),
    "teach": ("taught", "taught"), "tear": ("tore", "torn"), "tell": ("told", "told"), "think": ("thought", "thought"),
    "throw": ("threw", "thrown"), "thrust": ("thrust", "thrust"), "tread": ("trod", "trodden"), "wake": ("woke", "woken"),
    "wear": ("wore", "worn"), "weave": ("wove", "woven"), "weep": ("wept", "wept"), "win": ("won", "won"),
    "wind": ("wound", "wound"), "wring": ("wrung", "wrung"), "write": ("wrote", "written"),
}
# override → over + ride のように接頭辞付きの不規則動詞も表から引く
IRREGULAR_PREFIXES = ("over", "under", "with", "fore", "out", "mis", "up", "be", "re", "un")
THIRD_PERSON_IRREGULAR = {"be": "is", "have": "has", "do": "does", "go": "goes"}
# 2音節以上で語末の子音字を重ねる語尾 (infer → inferred, commit → committed)。offer などは例外
DOUBLING_ENDINGS = ("fer", "mit", "cur", "pel", "bel", "trol", "tol", "gret", "bed", "ret")
NO_DOUBLING = {"offer", "differ", "suffer", "proffer", "buffer", "pilfer", "summit", "limit", "vomit", "edit", "visit", "credit", "covet", "rivet", "inherit", "target", "budget", "market"}
PERFECT_OR_PASSIVE = {"am", "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "having", "get", "gets", "got", "gotten", "getting", "become", "became"}


def _irregular(word: str) -> tuple[str, str] | None:
    if word in IRREGULAR_VERBS:
        return IRREGULAR_VERBS[word]
    for prefix in IRREGULAR_PREFIXES:
        rest = word[len(prefix):]
        if word.startswith(prefix) and rest in IRREGULAR_VERBS:
            past, participle = IRREGULAR_VERBS[rest]
            return prefix + past, prefix + participle
    return None


def _doubles_final_consonant(word: str) -> bool:
    if len(word) < 3 or word in NO_DOUBLING:
        return False
    if word[-1] in VOWELS + "wxy" or word[-2] not in VOWELS or word[-3] in VOWELS:
        return False
    syllables = len(re.findall(r"[aeiouy]+", word))
    return syllables == 1 or word.endswith(DOUBLING_ENDINGS)


def _regular_suffix(word: str, suffix: str) -> str:
    # suffix は "ed" か "ing"
    if suffix == "ing" and word.endswith("ie"):
        return word[:-2] + "ying"
    if word.endswith("e") and (suffix == "ed" or not word.endswith(("ee", "ye", "oe"))):
        return word[:-1] + suffix
    if suffix == "ed" and len(word) > 2 and word.endswith("y") and word[-2] not in VOWELS:
        return word[:-1] + "ied"
    if word.endswith("ic"):
        return word + "k" + suffix
    if _doubles_final_consonant(word):
        return word + word[-1] + suffix
    return word + suffix


def _with_s(word: str) -> str:
    if word in THIRD_PERSON_IRREGULAR:
        return THIRD_PERSON_IRREGULAR[word]
    if word.endswith(("go", "do")) and _irregular(word):
        return word + "es"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 2 and word.endswith("y") and word[-2] not in VOWELS:
        return word[:-1] + "ies"
    return word + "s"


def inflect(word: str, form: str) -> str:
    """原形を form (base / s / past / participle / ing) の形にする。熟語は先頭の語だけを活用させる。"""
    first, *rest = word.split()
    lower = first.lower()
    irregular = _irregular(lower)
    if form == "s":
        inflected = _with_s(lower)
    elif form in ("past", "participle"):
        inflected = irregular[0 if form == "past" else 1] if irregular else _regular_suffix(lower, "ed")
    elif form == "ing":
        inflected = _regular_suffix(lower, "ing")
    else:
        inflected = lower
    return " ".join([inflected, *rest])


def classify(word: str, surface: str, preceding: str) -> str | None:
    """例文中の surface が原形 word のどの形かを返す。判定できなければ None。preceding は surface より前の文。"""
    word, surface = word.lower(), surface.lower()
    if " " in word or " " in surface:
        return "base" if word == surface else None
    if surface == word:
        return "base"
    irregular = _irregular(word)
    if irregular and surface in irregular and irregular[0] != irregular[1]:
        return "past" if surface == irregular[0] else "participle"
    if surface in {word + "s", word + "es", word[:-1] + "ies", _with_s(word)}:
        return "s"
    if surface in {word + "ing", word[:-1] + "ing", word[:-2] + "ying", word + word[-1] + "ing", word + "king"}:
        return "ing"
    ed_forms = {word + "ed", word + "d", word[:-1] + "ied", word + word[-1] + "ed", word + "ked", *(irregular or ())}
    if surface not in ed_forms:
        return None
    # 規則変化の -ed や過去形と過去分詞が同じ形は、直前の be / have などがあれば過去分詞と判断する
    previous_words = re.findall(r"[a-z']+", preceding.lower())[-3:]
    return "participle" if PERFECT_OR_PASSIVE & set(previous_words) else "past"

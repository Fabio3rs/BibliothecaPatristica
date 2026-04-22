"""
Linguistic integrity checks for keywords across Patrística corpora.
Refatorado para operação heavy-RAM (128GB): pré-carrega CLTK, léxicos
externos e caches globais, habilita paralelismo e heurística para scripts
mistos.
"""
from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import sqlite3
import atexit
from pathlib import Path
import re
import threading
import unicodedata
from typing import Iterable, Optional, Dict, List, Tuple

# External lookup (Whitaker/Ollama)
from lex_lookup import lookup_term, LookupResult, LookupCandidate

# Optional disk cache
CACHE_DB_PATH = os.getenv("KW_CACHE_DB", "data/cache/keyword_cache.db")
_CACHE_DB_CONN = None

# ---------------------------------------------------------------------------
# Logging / silencing noisy deps
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.getenv("KW_LOG_LEVEL", "ERROR").upper()
for noisy in ["cltk", "gensim", "stanza", "cltk.data.fetch", "urllib3"]:
    logging.getLogger(noisy).setLevel(_LOG_LEVEL)
warnings.filterwarnings("ignore", category=UserWarning, module="stanza")
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


class IntegrityStatus(str, Enum):
    VALID = "VALID"
    SUSPECT = "SUSPECT"
    STOPWORD = "STOPWORD"
    NOISE = "NOISE"
    MIXED = "MIXED_SCRIPT"
    UNKNOWN = "UNKNOWN"


@dataclass
class ValidationEvidence:
    raw_term: str
    normalized_term: str
    status: IntegrityStatus
    reasons: List[str] = field(default_factory=list)
    lemma: Optional[str] = None
    matched_vocab: bool = False
    matched_stopword: bool = False
    control_chars_found: bool = False
    script_flags: Dict[str, bool] = field(default_factory=dict)
    confidence: float = 0.0
    language: str = "unknown"  # ISO-ish hint used in the check
    external_source: Optional[str] = None
    external_notes: str = ""
    tokens: List["TokenEvidence"] = field(default_factory=list)


@dataclass
class TokenEvidence:
    raw: str
    normalized: str
    corrected: str = ""
    lemma: Optional[str] = None
    language: str = "unknown"
    status: IntegrityStatus = IntegrityStatus.UNKNOWN
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------


CONTROL_RE = re.compile(r"[\x00-\x1F\x7F]")
MULTISPACE_RE = re.compile(r"\s+")
LINEBREAK_HYPHEN_RE = re.compile(r"(\w)[-–—]\s*\n\s*(\w)")
EDGE_PUNCT_RE = re.compile(
    r"^[^\w\u0370-\u03FF\u1F00-\u1FFF\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
    r"\u0590-\u05FF\u0700-\u074F\u0530-\u058F\u1200-\u137F]+|"
    r"[^\w\u0370-\u03FF\u1F00-\u1FFF\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
    r"\u0590-\u05FF\u0700-\u074F\u0530-\u058F\u1200-\u137F]+$"
)

# Citações bíblicas simples (PT/LA) – tolerar dígitos/capítulo:verso.
BIBLE_CITATION_RE = re.compile(
    r"""^\s*
    \d{0,3}\s*                           # número do livro (ex: 1, 2, 3)
    [A-Za-zÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç]{3,}  # nome do livro
    (?:\s+[A-Za-zÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç]{2,})*  # partes adicionais (ex: dos Reis)
    (?:\s+\d+(?:[:.,]\d+)?(?:-\d+)?\s*)? # capítulo e verso opcionais
    $""",
    re.VERBOSE,
)


def strip_control_chars(text: str) -> str:
    return CONTROL_RE.sub("", text)


def fix_ocr_hyphenation(text: str) -> str:
    return LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def basic_cleanup(text: str) -> str:
    text = fix_ocr_hyphenation(text)
    text = strip_control_chars(text)
    text = normalize_unicode(text)
    text = MULTISPACE_RE.sub(" ", text).strip()
    return text


def clean_keyword_token(term: str) -> str:
    term = basic_cleanup(term)
    term = EDGE_PUNCT_RE.sub("", term)
    return term.strip()


# ---------------------------------------------------------------------------
# Script detection
# ---------------------------------------------------------------------------


def has_greek(text: str) -> bool:
    return any("\u0370" <= ch <= "\u03FF" or "\u1F00" <= ch <= "\u1FFF" for ch in text)


def has_latin_letters(text: str) -> bool:
    return any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in text)


def has_digits(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def has_arabic(text: str) -> bool:
    return any(
        "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F" or "\u08A0" <= ch <= "\u08FF"
        for ch in text
    )


def has_hebrew(text: str) -> bool:
    return any("\u0590" <= ch <= "\u05FF" for ch in text)


def has_syriac(text: str) -> bool:
    return any("\u0700" <= ch <= "\u074F" for ch in text)


def has_armenian(text: str) -> bool:
    return any("\u0530" <= ch <= "\u058F" for ch in text)


def has_ethiopic(text: str) -> bool:
    return any("\u1200" <= ch <= "\u137F" for ch in text)


def has_weird_symbols(text: str) -> bool:
    allowed = set(" -'’")
    for ch in text:
        if ch.isalnum() or ch in allowed:
            continue
        if (
            has_greek(ch)
            or has_arabic(ch)
            or has_hebrew(ch)
            or has_syriac(ch)
            or has_armenian(ch)
            or has_ethiopic(ch)
        ):
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            continue
        if cat.startswith("L"):
            continue
        return True
    return False


def detect_script_flags(term: str) -> Dict[str, bool]:
    return {
        "has_greek": has_greek(term),
        "has_latin": has_latin_letters(term),
        "has_digits": has_digits(term),
        "has_arabic": has_arabic(term),
        "has_hebrew": has_hebrew(term),
        "has_syriac": has_syriac(term),
        "has_armenian": has_armenian(term),
        "has_ethiopic": has_ethiopic(term),
    }


def guess_language(term: str, language_hint: Optional[str] = None) -> str:
    if language_hint and language_hint != "multi":
        return language_hint
    flags = detect_script_flags(term)
    if flags["has_arabic"]:
        return "ara"
    if flags["has_hebrew"]:
        return "heb"
    if flags["has_syriac"]:
        return "syr"
    if flags["has_armenian"]:
        return "hye"
    if flags["has_ethiopic"]:
        return "gez"
    if flags["has_greek"]:
        return "grc"
    return "unknown"


# ---------------------------------------------------------------------------
# Mixed scripts helper
# ---------------------------------------------------------------------------


def split_mixed_scripts(term: str) -> List[str]:
    return [p for p in re.split(r"[^a-zA-Z\u0370-\u03FF\u1F00-\u1FFF]+", term) if len(p) > 1]


# ---------------------------------------------------------------------------
# Stopwords & variants
# ---------------------------------------------------------------------------


LATIN_STOPWORDS = {
    "et",
    "in",
    "de",
    "ad",
    "non",
    "qui",
    "quae",
    "quod",
    "cum",
    "ut",
    "per",
    "ex",
    "sed",
    "nec",
    "ne",
    "aut",
    "uel",
    "vel",
    "si",
    "est",
    "sunt",
    "erat",
    "esse",
    "eo",
    "ea",
    "id",
}

GREEK_STOPWORDS = {
    "και",
    "δε",
    "γαρ",
    "ουν",
    "της",
    "των",
    "τον",
    "του",
    "εν",
    "εις",
    "εκ",
    "ου",
    "ο",
    "η",
    "το",
    "τα",
    "οι",
    "αι",
}

ARABIC_STOPWORDS = {"و", "في", "من", "على", "الى", "عن", "هذا", "ذلك", "ما", "لا", "إن", "أن", "كان"}
HEBREW_STOPWORDS = {"ו", "ה", "של", "על", "אל", "עם", "לא", "כן", "זה", "הוא"}
SYRIAC_STOPWORDS = {"ܘ", "ܕ", "ܒ", "ܠ", "ܡܢ", "ܥܠ", "ܠܐ", "ܗܘ", "ܗܝ"}
ARMENIAN_STOPWORDS = {"եւ", "ու", "է", "ի", "որ", "թե", "նա", "մէ", "մէջ", "դէպի"}
ETHIOPIC_STOPWORDS = {"ወ", "እና", "በ", "ለ", "እንደ", "ከ", "ዘ", "ያ", "ስለ"}

FALLBACK_FRA_STOP = {"de", "la", "le", "les", "et", "ou", "un", "une", "des", "du", "au", "aux", "pour", "dans", "en", "que"}
FALLBACK_ENG_STOP = {"the", "and", "of", "in", "to", "for", "on", "with", "by", "from", "is", "are", "be", "as", "at", "it"}
FALLBACK_ENG_VOCAB = {"kingdom", "church", "faith", "god", "lord", "truth", "love"}
FALLBACK_FRA_VOCAB = {"dieu", "eglise", "église", "royaume", "amour"}


# ---------------------------------------------------------------------------
# Variants helpers
# ---------------------------------------------------------------------------


def normalize_latin_orthography(term: str) -> str:
    t = term.lower()
    t = t.replace("j", "i").replace("v", "u")
    t = t.replace("æ", "ae").replace("œ", "oe")
    return t


def latin_variants(term: str) -> set[str]:
    base = term.lower()
    norm = normalize_latin_orthography(base)
    variants = {base, norm, norm.replace("u", "v"), norm.replace("i", "j")}
    return {v for v in variants if v}


def strip_greek_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def greek_variants(term: str) -> set[str]:
    base = term.strip()
    return {base, base.lower(), strip_greek_diacritics(base.lower())}


# ---------------------------------------------------------------------------
# CLTK bridge (defensive)
# ---------------------------------------------------------------------------


class CLTKBridge:
    def __init__(self) -> None:
        self._models: Dict[str, Tuple[str, object]] = {}
        self._init_backends()

    def _init_backends(self) -> None:
        try:
            from cltk import NLP  # type: ignore

            modern_codes = ["lat", "lati1261", "grc", "syr", "hye", "hbo", "ara", "arb", "gez"]
            for code in modern_codes:
                try:
                    self._models[code] = ("modern", NLP(code, suppress_banner=True))
                except Exception:
                    continue
        except Exception:
            pass

        # Legacy fallback only for lat/grc
        try:
            from cltk.lemmatize.lat import LatinBackoffLemmatizer  # type: ignore
            from cltk.lemmatize.grc import GreekBackoffLemmatizer  # type: ignore

            if "lat" not in self._models:
                self._models["lat"] = ("legacy", LatinBackoffLemmatizer())
            if "grc" not in self._models:
                self._models["grc"] = ("legacy", GreekBackoffLemmatizer())
        except Exception:
            pass

    @property
    def available_codes(self) -> set[str]:
        return set(self._models.keys())

    def lemma(self, lang: str, term: str) -> Optional[str]:
        lang = self._alias(lang)
        if lang not in self._models:
            return None
        mode, obj = self._models[lang]
        try:
            if mode == "modern":
                doc = obj.analyze(text=term)
                for w in getattr(doc, "words", []):
                    lemma = getattr(w, "lemma", None)
                    if lemma:
                        return str(lemma)
            else:
                result = obj.lemmatize([term])
                if result and result[0][1]:
                    return str(result[0][1])
        except Exception:
            return None
        return None

    def lemma_tokens(self, lang: str, text: str) -> List[Tuple[str, Optional[str]]]:
        """Return (token, lemma) for each token; best-effort, tolerant to missing models."""
        lang = self._alias(lang)
        if lang not in self._models:
            return []
        mode, obj = self._models[lang]
        results: List[Tuple[str, Optional[str]]] = []
        try:
            if mode == "modern":
                doc = obj.analyze(text=text)
                for w in getattr(doc, "words", []):
                    token = getattr(w, "string", None) or getattr(w, "raw", None) or ""
                    lemma = getattr(w, "lemma", None)
                    if token:
                        results.append((str(token), str(lemma) if lemma else None))
            else:
                # legacy lemmatizer expects list of tokens
                tokens = [t for t in re.split(r"\s+", text) if t]
                lemmatized = obj.lemmatize(tokens)
                for tok, lem in lemmatized:
                    results.append((tok, str(lem) if lem else None))
        except Exception:
            return []
        return results

    @staticmethod
    def _alias(lang: str) -> str:
        if lang == "ell":
            return "grc"
        if lang == "heb":
            return "hbo"
        if lang == "arb":
            return "ara"
        if lang == "eth":
            return "gez"
        return lang


# ---------------------------------------------------------------------------
# NLTK helpers
# ---------------------------------------------------------------------------


NLTK_PACKAGES = ["punkt", "stopwords", "floresta", "words", "omw-1.4"]


def ensure_nltk_data(nltk_module) -> None:
    for pkg in NLTK_PACKAGES:
        try:
            nltk_module.data.find(pkg)
        except LookupError:
            try:
                nltk_module.download(pkg, quiet=True)
            except Exception:
                pass


def load_portuguese_resources(nltk_module) -> Tuple[set[str], set[str]]:
    try:
        ensure_nltk_data(nltk_module)
        from nltk.corpus import floresta, stopwords  # type: ignore

        pt_vocab = {w.lower() for w in floresta.words() if w.strip()}
        pt_stopwords = set(stopwords.words("portuguese"))
        pt_stopwords |= {"d'", "n'", "s'", "p.", "pp."}
        return pt_vocab, pt_stopwords
    except Exception:
        fallback_stop = {"de", "da", "do", "em", "para", "por", "a", "o"}
        return set(), fallback_stop


def load_french_resources(nltk_module) -> Tuple[set[str], set[str]]:
    try:
        ensure_nltk_data(nltk_module)
        from nltk.corpus import stopwords  # type: ignore

        fra_stop = set(stopwords.words("french"))
    except Exception:
        fra_stop = set(FALLBACK_FRA_STOP)
    fra_vocab = set(FALLBACK_FRA_VOCAB)
    return fra_vocab, fra_stop


def load_english_resources(nltk_module) -> Tuple[set[str], set[str]]:
    try:
        ensure_nltk_data(nltk_module)
        from nltk.corpus import stopwords, words  # type: ignore

        eng_stop = set(stopwords.words("english"))
        eng_vocab = {w.lower() for w in words.words()}
    except Exception:
        eng_stop = set(FALLBACK_ENG_STOP)
        eng_vocab = set(FALLBACK_ENG_VOCAB)
    return eng_vocab, eng_stop


# ---------------------------------------------------------------------------
# Singleton heavy checker
# ---------------------------------------------------------------------------


_GLOBAL_LOCK = threading.Lock()
_GLOBAL_CHECKER: Optional["KeywordIntegrityChecker"] = None
_CACHE_DB_CONN: Optional[sqlite3.Connection] = None


def get_checker() -> "KeywordIntegrityChecker":
    global _GLOBAL_CHECKER
    if _GLOBAL_CHECKER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_CHECKER is None:
                _GLOBAL_CHECKER = KeywordIntegrityChecker()
                _init_cache_db()
                _load_cache_from_db(_GLOBAL_CHECKER)
    return _GLOBAL_CHECKER


def _init_cache_db() -> None:
    """Initialize sqlite cache if KW_CACHE_DB is set."""
    global _CACHE_DB_CONN
    if _CACHE_DB_CONN or not CACHE_DB_PATH:
        return
    db_path = Path(CACHE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=3000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_cache (
            term TEXT NOT NULL,
            lang_hint TEXT,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY(term, lang_hint)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lemma_cache (
            lang TEXT NOT NULL,
            term TEXT NOT NULL,
            lemma TEXT,
            PRIMARY KEY(lang, term)
        )
        """
    )
    conn.commit()
    _CACHE_DB_CONN = conn
    atexit.register(_close_cache_db)


def _close_cache_db() -> None:
    global _CACHE_DB_CONN
    if _CACHE_DB_CONN:
        try:
            _CACHE_DB_CONN.close()
        finally:
            _CACHE_DB_CONN = None


def _evidence_to_json(ev: ValidationEvidence) -> str:
    data = asdict(ev)
    data["status"] = ev.status.value
    # tokens embed enum -> stringify for persistence
    if data.get("tokens"):
        for t in data["tokens"]:
            t["status"] = t["status"].value if hasattr(t.get("status"), "value") else t.get("status")
    return json.dumps(data, ensure_ascii=False)


def _evidence_from_json(data: str) -> ValidationEvidence:
    obj = json.loads(data)
    status_str = obj.get("status", "UNKNOWN")
    try:
        status = IntegrityStatus(status_str)
    except Exception:
        status = IntegrityStatus.UNKNOWN
    tokens_raw = obj.get("tokens") or []
    tokens: List[TokenEvidence] = []
    for t in tokens_raw:
        try:
            t_status = IntegrityStatus(t.get("status", "UNKNOWN"))
        except Exception:
            t_status = IntegrityStatus.UNKNOWN
        tokens.append(
            TokenEvidence(
                raw=t.get("raw", ""),
                normalized=t.get("normalized", t.get("raw", "")),
                corrected=t.get("corrected", ""),
                lemma=t.get("lemma"),
                language=t.get("language", "unknown"),
                status=t_status,
                reasons=t.get("reasons", []) or [],
            )
        )

    return ValidationEvidence(
        raw_term=obj.get("raw_term", ""),
        normalized_term=obj.get("normalized_term", obj.get("raw_term", "")),
        status=status,
        reasons=obj.get("reasons", []) or [],
        lemma=obj.get("lemma"),
        matched_vocab=obj.get("matched_vocab", False),
        matched_stopword=obj.get("matched_stopword", False),
        control_chars_found=obj.get("control_chars_found", False),
        script_flags=obj.get("script_flags", {}) or {},
        confidence=obj.get("confidence", 0.0),
        language=obj.get("language", "unknown"),
        external_source=obj.get("external_source"),
        external_notes=obj.get("external_notes", ""),
        tokens=tokens,
    )


def _db_upsert_evidence(term: str, lang_hint: Optional[str], ev: ValidationEvidence) -> None:
    if not _CACHE_DB_CONN:
        return
    _CACHE_DB_CONN.execute(
        "INSERT OR REPLACE INTO evidence_cache(term, lang_hint, evidence_json) VALUES (?, ?, ?)",
        (term, lang_hint, _evidence_to_json(ev)),
    )
    _CACHE_DB_CONN.commit()


def _db_upsert_lemma(lang: str, term: str, lemma: Optional[str]) -> None:
    if not _CACHE_DB_CONN:
        return
    _CACHE_DB_CONN.execute(
        "INSERT OR REPLACE INTO lemma_cache(lang, term, lemma) VALUES (?, ?, ?)",
        (lang, term, lemma),
    )
    _CACHE_DB_CONN.commit()


def _load_cache_from_db(checker: "KeywordIntegrityChecker") -> None:
    if not _CACHE_DB_CONN:
        return
    try:
        for term, lang_hint, ev_json in _CACHE_DB_CONN.execute(
            "SELECT term, lang_hint, evidence_json FROM evidence_cache"
        ):
            ev = _evidence_from_json(ev_json)
            checker.evidence_cache[(term, lang_hint)] = ev
        for lang, term, lemma in _CACHE_DB_CONN.execute(
            "SELECT lang, term, lemma FROM lemma_cache"
        ):
            checker.lemma_cache[(lang, term)] = lemma
    except Exception:
        # fail-open: ignore DB load errors
        pass


class KeywordIntegrityChecker:
    def __init__(self) -> None:
        self._init_once()

    def _init_once(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.missing_resources: List[str] = []
        self.evidence_cache: Dict[Tuple[str, Optional[str]], ValidationEvidence] = {}
        self.lemma_cache: Dict[Tuple[str, str], Optional[str]] = {}
        self.lexicon_sets: Dict[str, set[str]] = {}
        self.embedding_models: Dict[str, object] = {}
        self._embed_vocab_cache: Dict[str, set[str]] = {}

        # NLTK
        try:
            import nltk  # type: ignore

            self.nltk = nltk
        except Exception:
            self.nltk = None
            self.missing_resources.append("nltk_missing")

        if self.nltk:
            self.pt_vocab, self.pt_stopwords = load_portuguese_resources(self.nltk)
            self.fra_vocab, self.fra_stopwords = load_french_resources(self.nltk)
            self.eng_vocab, self.eng_stopwords = load_english_resources(self.nltk)
        else:
            self.pt_vocab, self.pt_stopwords = set(), load_portuguese_resources(None)[1]
            self.fra_vocab, self.fra_stopwords = set(FALLBACK_FRA_VOCAB), set(FALLBACK_FRA_STOP)
            self.eng_vocab, self.eng_stopwords = set(FALLBACK_ENG_VOCAB), set(FALLBACK_ENG_STOP)

        # CLTK heavy load
        self.cltk = CLTKBridge()
        if not self.cltk.available_codes:
            self.missing_resources.append("cltk_missing")

        # Preload extra lexica (kept per instance to avoid mutating module constants)
        self._load_external_lexica()

        # Optional embeddings
        self._load_embedding_models()

    # --------------------------- loaders ---------------------------
    def _load_external_lexica(self) -> None:
        lex_dir = Path(os.getenv("KW_LEXICON_DIR", Path("data") / "lexica"))
        if not lex_dir.exists():
            return
        for txt in lex_dir.glob("*.txt"):
            lang_hint = txt.stem.split("_")[0].lower()
            try:
                with txt.open("r", encoding="utf-8", errors="ignore") as fh:
                    words = {line.strip().lower() for line in fh if line.strip() and not line.startswith("#")}
                if not words:
                    continue
                self.lexicon_sets.setdefault(lang_hint, set()).update(words)
            except Exception:
                continue
        # Stopword extensions *_stopwords.txt
        for txt in lex_dir.glob("*_stopwords.txt"):
            lang_hint = txt.stem.split("_")[0].lower()
            try:
                with txt.open("r", encoding="utf-8", errors="ignore") as fh:
                    words = {line.strip().lower() for line in fh if line.strip() and not line.startswith("#")}
                if not words:
                    continue
                if lang_hint == "lat":
                    LATIN_STOPWORDS.update(words)
                elif lang_hint == "grc":
                    GREEK_STOPWORDS.update(words)
            except Exception:
                continue

    def _load_embedding_models(self) -> None:
        lex_dir = Path(os.getenv("KW_LEXICON_DIR", Path("data") / "lexica"))
        if not lex_dir.exists():
            return
        try:
            from gensim.models import KeyedVectors  # type: ignore
        except Exception:
            return
        for vec in list(lex_dir.glob("*.bin")) + list(lex_dir.glob("*.vec")):
            lang_hint = vec.stem.split("_")[0].lower()
            try:
                if vec.suffix == ".bin":
                    kv = KeyedVectors.load(str(vec), mmap="r")
                else:
                    kv = KeyedVectors.load_word2vec_format(str(vec), binary=False)
                self.embedding_models[lang_hint] = kv
            except Exception:
                continue

    # --------------------------- helpers ---------------------------
    def _is_noise(self, term: str) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if not term.strip():
            reasons.append("empty")
            return True, reasons
        if CONTROL_RE.search(term):
            reasons.append("control_chars")
            return True, reasons
        if len(term) <= 1:
            reasons.append("too_short")
            return True, reasons
        is_bible = bool(BIBLE_CITATION_RE.match(term))
        if has_digits(term) and not is_bible:
            reasons.append("digits")
        if has_weird_symbols(term) and not is_bible:
            reasons.append("weird_symbols")
        return False, reasons

    @staticmethod
    def _lemma_is_informative(term: str, lemma: Optional[str]) -> bool:
        """Reject trivial lemmatization results (empty, 1-char, identical)."""
        if not lemma:
            return False
        l = lemma.lower().strip()
        t = term.lower().strip()
        if not l or len(l) <= 1:
            return False
        if l == t:
            return False
        return True

    def _lemma_cached(self, lang: str, term: str) -> Optional[str]:
        key = (lang, term)
        if key in self.lemma_cache:
            return self.lemma_cache[key]
        lemma = self.cltk.lemma(lang, term) if self.cltk else None
        self.lemma_cache[key] = lemma
        _db_upsert_lemma(lang, term, lemma)
        return lemma

    def _store_evidence(self, cache_key: Tuple[str, Optional[str]], evidence: ValidationEvidence) -> None:
        self.evidence_cache[cache_key] = evidence
        _db_upsert_evidence(cache_key[0], cache_key[1], evidence)

    def _check_modern(self, term: str, lang: str) -> Tuple[bool, Optional[str], Optional[str]]:
        t = term.lower()
        tokens = [tok for tok in re.split(r"[\s-]+", t) if tok]
        if lang == "pt":
            vocab, stop = self.pt_vocab, self.pt_stopwords
        elif lang == "fra":
            vocab, stop = self.fra_vocab, self.fra_stopwords
        else:
            vocab, stop = self.eng_vocab, self.eng_stopwords

        # Stopword only if the entire term is a single-token stopword
        if len(tokens) == 1 and tokens[0] in stop:
            return False, None, f"full_term_is_{lang}_stopword"
        if t in vocab:
            return True, None, f"{lang}_vocab"
        if tokens and all(tok in vocab for tok in tokens):
            return True, None, f"{lang}_vocab"
        return False, None, None

    def _check_latin(self, term: str) -> Tuple[bool, Optional[str], Optional[str]]:
        for variant in latin_variants(term):
            if " " not in variant and variant in LATIN_STOPWORDS:
                return False, None, "full_term_is_lat_stopword"
            lemma = self._lemma_cached("lat", variant)
            if self._lemma_is_informative(variant, lemma):
                return True, lemma, "lat_lemma"
            if variant in self.lexicon_sets.get("lat", set()):
                return True, None, "lat_lexicon"
            if variant in self._embedding_vocab("lat"):
                return True, None, "lat_embed_vocab"
        return False, None, None

    def _check_latin_tokenized(self, tokens: List[str]) -> Tuple[List[TokenEvidence], IntegrityStatus, Optional[str]]:
        evidences: List[TokenEvidence] = []
        lemmas: List[str] = []
        statuses: List[IntegrityStatus] = []
        for tok in tokens:
            reasons: List[str] = []
            if tok in LATIN_STOPWORDS:
                status = IntegrityStatus.STOPWORD
                reasons.append("lat_stopword")
                lemma = None
            else:
                lemma = self._lemma_cached("lat", tok)
                if self._lemma_is_informative(tok, lemma):
                    status = IntegrityStatus.VALID
                    reasons.append("lat_lemma")
                elif tok in self.lexicon_sets.get("lat", set()):
                    status = IntegrityStatus.VALID
                    reasons.append("lat_lexicon")
                elif tok in self._embedding_vocab("lat"):
                    status = IntegrityStatus.VALID
                    reasons.append("lat_embed_vocab")
                else:
                    status = IntegrityStatus.SUSPECT
            evidences.append(
                TokenEvidence(
                    raw=tok,
                    normalized=tok,
                    corrected=tok,
                    lemma=lemma,
                    language="lat",
                    status=status,
                    reasons=reasons,
                )
            )
            if lemma:
                lemmas.append(lemma)
            statuses.append(status)
        agg_status = self._aggregate_status(statuses)
        lemma_joined = "+".join(lemmas) if lemmas else None
        return evidences, agg_status, lemma_joined

    def _check_greek(self, term: str) -> Tuple[bool, Optional[str], Optional[str]]:
        for variant in greek_variants(term):
            if " " not in variant and variant in GREEK_STOPWORDS:
                return False, None, "full_term_is_grc_stopword"
            lemma = self._lemma_cached("grc", variant)
            if self._lemma_is_informative(variant, lemma):
                return True, lemma, "grc_lemma"
            if variant in self.lexicon_sets.get("grc", set()):
                return True, None, "grc_lexicon"
            if variant in self._embedding_vocab("grc"):
                return True, None, "grc_embed_vocab"
        return False, None, None

    def _check_cltk_language(self, term: str, lang: str) -> Tuple[bool, Optional[str], Optional[str]]:
        lemma = self._lemma_cached(lang, term)
        if self._lemma_is_informative(term, lemma):
            return True, lemma, f"{lang}_lemma"
        if term.lower() in self.lexicon_sets.get(lang, set()):
            return True, None, f"{lang}_lexicon"
        return False, None, None

    def _check_cltk_language_tokens(self, tokens: List[str], lang: str) -> Tuple[List[TokenEvidence], IntegrityStatus, Optional[str]]:
        evidences: List[TokenEvidence] = []
        lemmas: List[str] = []
        statuses: List[IntegrityStatus] = []
        tokens_with_lemma = self.cltk.lemma_tokens(lang, " ".join(tokens)) if self.cltk else []
        lemma_map = {t: l for t, l in tokens_with_lemma if t}

        for tok in tokens:
            reasons: List[str] = []
            lemma = lemma_map.get(tok) or self._lemma_cached(lang, tok)
            if self._lemma_is_informative(tok, lemma):
                status = IntegrityStatus.VALID
                reasons.append(f"{lang}_lemma")
            elif tok.lower() in self.lexicon_sets.get(lang, set()):
                status = IntegrityStatus.VALID
                reasons.append(f"{lang}_lexicon")
            else:
                status = IntegrityStatus.SUSPECT
            evidences.append(
                TokenEvidence(
                    raw=tok,
                    normalized=tok,
                    corrected=tok,
                    lemma=lemma,
                    language=lang,
                    status=status,
                    reasons=reasons,
                )
            )
            if lemma:
                lemmas.append(lemma)
            statuses.append(status)

        agg_status = self._aggregate_status(statuses)
        lemma_joined = "+".join(lemmas) if lemmas else None
        return evidences, agg_status, lemma_joined

    def _embedding_vocab(self, lang: str) -> set[str]:
        if lang not in self._embed_vocab_cache:
            kv = self.embedding_models.get(lang)
            try:
                self._embed_vocab_cache[lang] = set(kv.key_to_index.keys()) if kv else set()
            except Exception:
                self._embed_vocab_cache[lang] = set()
        return self._embed_vocab_cache[lang]

    # --------------------------- phrase helpers ---------------------------
    def _tokenize_phrase(self, term: str) -> List[str]:
        return [p for p in re.split(r"[\s\-]+", term) if len(p) > 0]

    def _aggregate_status(self, token_statuses: List[IntegrityStatus]) -> IntegrityStatus:
        if any(s == IntegrityStatus.NOISE for s in token_statuses):
            return IntegrityStatus.NOISE
        if token_statuses and all(s == IntegrityStatus.STOPWORD for s in token_statuses):
            return IntegrityStatus.STOPWORD
        if token_statuses and all(s in {IntegrityStatus.VALID, IntegrityStatus.STOPWORD} for s in token_statuses):
            return IntegrityStatus.VALID
        return IntegrityStatus.SUSPECT

    def _majority_language(self, langs: List[str]) -> str:
        if not langs:
            return "unknown"
        from collections import Counter

        return Counter([l or "unknown" for l in langs]).most_common(1)[0][0]

    # ------------------------------------------------------------------
    def check_keyword_integrity(
        self, term: str, is_canon_name: bool = False, language_hint: Optional[str] = None
    ) -> ValidationEvidence:
        # Cache fast path
        cache_key = (term, language_hint)
        if cache_key in self.evidence_cache:
            return self.evidence_cache[cache_key]

        raw = term
        cleaned = clean_keyword_token(term)
        script_flags = detect_script_flags(cleaned)
        evidence = ValidationEvidence(
            raw_term=raw,
            normalized_term=cleaned,
            status=IntegrityStatus.UNKNOWN,
            script_flags=script_flags,
            language=guess_language(cleaned, language_hint),
        )

        if is_canon_name:
            evidence.status = IntegrityStatus.VALID
            evidence.reasons.append("canon_name_bypass")
            evidence.confidence = 1.0
            self._store_evidence(cache_key, evidence)
            return evidence

        is_noise, noise_reasons = self._is_noise(cleaned)
        evidence.reasons.extend(noise_reasons)
        if is_noise:
            evidence.status = IntegrityStatus.NOISE
            evidence.confidence = 0.99
            evidence.control_chars_found = "control_chars" in noise_reasons
            self._store_evidence(cache_key, evidence)
            return evidence

        # Mixed scripts heuristic (Latin + Greek)
        if script_flags["has_greek"] and script_flags["has_latin"]:
            parts = split_mixed_scripts(cleaned)
            # Guard against unsplittable strings to avoid infinite recursion
            if not parts or parts == [cleaned]:
                evidence.status = IntegrityStatus.MIXED
                evidence.reasons.append("mixed_scripts_no_split")
                evidence.confidence = 0.6
                self._store_evidence(cache_key, evidence)
                return evidence

            sub_results = [self.check_keyword_integrity(p, language_hint=None) for p in parts]
            if all(sr.status in {IntegrityStatus.VALID, IntegrityStatus.STOPWORD} for sr in sub_results):
                evidence.status = IntegrityStatus.VALID
                evidence.reasons.append("mixed_parts_valid")
                evidence.confidence = 0.9
                evidence.lemma = "+".join([sr.lemma or sr.normalized_term for sr in sub_results])
            else:
                evidence.status = IntegrityStatus.MIXED
                evidence.reasons.append(f"mixed_scripts_detected:{parts}")
                evidence.confidence = 0.6
            self._store_evidence(cache_key, evidence)
            return evidence

        lang = evidence.language

        # Phrase-level path when whitespace present
        if " " in cleaned or "-" in cleaned:
            tokens_raw = self._tokenize_phrase(cleaned)
            # First try CLTK/lexicon token path for homogeneous scripts
            token_lang = lang if lang != "unknown" else language_hint or "unknown"
            token_evidences: List[TokenEvidence] = []
            agg_status: IntegrityStatus = IntegrityStatus.UNKNOWN
            agg_lemma: Optional[str] = None

            # Route by detected language/script
            if token_lang in {"lat"}:
                token_evidences, agg_status, agg_lemma = self._check_latin_tokenized(tokens_raw)
            elif token_lang == "grc":
                token_evidences, agg_status, agg_lemma = self._check_cltk_language_tokens(tokens_raw, "grc")
            elif token_lang in {"syr", "hye", "ara", "arb", "heb", "hbo", "eth", "gez"}:
                token_evidences, agg_status, agg_lemma = self._check_cltk_language_tokens(tokens_raw, self.cltk._alias(token_lang) if self.cltk else token_lang)

            if token_evidences:
                evidence.tokens = token_evidences
                evidence.status = agg_status
                evidence.lemma = agg_lemma
                evidence.language = self._majority_language([t.language for t in token_evidences])
                evidence.confidence = 0.85 if agg_status == IntegrityStatus.VALID else 0.7
                if agg_status in {IntegrityStatus.VALID, IntegrityStatus.STOPWORD, IntegrityStatus.NOISE}:
                    self._store_evidence(cache_key, evidence)
                    return evidence

        # 1) Script-driven checks
        if lang == "grc":
            ok, lemma, why = self._check_greek(cleaned)
            if why == "grc_stopword":
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append(why)
                evidence.confidence = 0.95
                self._store_evidence(cache_key, evidence)
                return evidence
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "grc_ok")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        if lang == "syr":
            ok, lemma, why = self._check_cltk_language(cleaned, "syr")
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "syr_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence
            if cleaned.lower() in SYRIAC_STOPWORDS:
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append("syr_stopword")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        if lang == "hye":
            ok, lemma, why = self._check_cltk_language(cleaned, "hye")
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "hye_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence
            if cleaned.lower() in ARMENIAN_STOPWORDS:
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append("hye_stopword")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        if lang in {"ara", "arb"}:
            ok, lemma, why = self._check_cltk_language(cleaned, "ara")
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "ara_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence
            if cleaned.lower() in ARABIC_STOPWORDS:
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append("ara_stopword")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        if lang in {"heb", "hbo"}:
            ok, lemma, why = self._check_cltk_language(cleaned, "hbo")
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "hbo_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence
            if cleaned.lower() in HEBREW_STOPWORDS:
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append("hbo_stopword")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        if lang in {"eth", "gez"}:
            ok, lemma, why = self._check_cltk_language(cleaned, "gez")
            if ok:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma
                evidence.reasons.append(why or "gez_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence
            if cleaned.lower() in ETHIOPIC_STOPWORDS:
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append("gez_stopword")
                evidence.confidence = 0.9
                self._store_evidence(cache_key, evidence)
                return evidence

        # Latin + modern languages
        if script_flags["has_latin"]:
            modern_order = ["pt", "fra", "eng"]
            if language_hint in {"fra", "fr"}:
                modern_order = ["fra", "pt", "eng"]
            elif language_hint in {"eng", "en"}:
                modern_order = ["eng", "pt", "fra"]
            latin_first = language_hint == "lat"

            if not latin_first:
                for modern_lang in modern_order:
                    ok_mod, _, why_mod = self._check_modern(cleaned, modern_lang)
                    if why_mod and why_mod.endswith("_stopword"):
                        evidence.status = IntegrityStatus.STOPWORD
                        evidence.matched_stopword = True
                        evidence.reasons.append(why_mod)
                        evidence.confidence = 0.9
                        self._store_evidence(cache_key, evidence)
                        return evidence
                    if ok_mod:
                        evidence.status = IntegrityStatus.VALID
                        evidence.reasons.append(why_mod or f"{modern_lang}_ok")
                        evidence.matched_vocab = True
                        evidence.confidence = 0.85
                        self._store_evidence(cache_key, evidence)
                        return evidence

            ok_lat, lemma_lat, why_lat = self._check_latin(cleaned)
            if why_lat == "lat_stopword":
                evidence.status = IntegrityStatus.STOPWORD
                evidence.matched_stopword = True
                evidence.reasons.append(why_lat)
                evidence.confidence = 0.95
                self._store_evidence(cache_key, evidence)
                return evidence
            if ok_lat:
                evidence.status = IntegrityStatus.VALID
                evidence.lemma = lemma_lat
                evidence.reasons.append(why_lat or "lat_ok")
                evidence.confidence = 0.88
                self._store_evidence(cache_key, evidence)
                return evidence

            if latin_first:
                for modern_lang in modern_order:
                    ok_mod, _, why_mod = self._check_modern(cleaned, modern_lang)
                    if why_mod and why_mod.endswith("_stopword"):
                        evidence.status = IntegrityStatus.STOPWORD
                        evidence.matched_stopword = True
                        evidence.reasons.append(why_mod)
                        evidence.confidence = 0.9
                        self._store_evidence(cache_key, evidence)
                        return evidence
                    if ok_mod:
                        evidence.status = IntegrityStatus.VALID
                        evidence.reasons.append(why_mod or f"{modern_lang}_ok")
                        evidence.matched_vocab = True
                        evidence.confidence = 0.85
                        self._store_evidence(cache_key, evidence)
                        return evidence

        # No linguistic match -> external lookup fallback (Whitaker/Ollama)
        lookup = lookup_term(evidence.normalized_term, language_hint=language_hint)
        if lookup.found:
            # If LLM/lookup returned multiple candidates and we haven't tokenized yet, try token alignment
            if not evidence.tokens and lookup.candidates and (" " in cleaned or "-" in cleaned):
                tokens = self._tokenize_phrase(cleaned)
                aligned_tokens: List[TokenEvidence] = []

                def _score(tok: str, cand: LookupCandidate) -> float:
                    tok_norm = tok.lower()
                    cand_norm = (cand.correcao or cand.palavra or "").lower()
                    score = 0.0
                    if cand_norm == tok_norm:
                        score += 100
                    if tok_norm and cand_norm and tok_norm in cand_norm:
                        score += 20
                    if tok_norm and cand_norm and cand_norm in tok_norm:
                        score += 10
                    # simple script match heuristic
                    if (has_greek(tok) and has_greek(cand_norm)) or (has_latin_letters(tok) and has_latin_letters(cand_norm)):
                        score += 5
                    try:
                        from difflib import SequenceMatcher

                        ratio = SequenceMatcher(None, tok_norm, cand_norm).ratio()
                        score += ratio * 10
                    except Exception:
                        pass
                    score += len(cand_norm) * 0.1
                    return score

                for tok in tokens:
                    best_cand = None
                    best_score = -1.0
                    for cand in lookup.candidates:
                        sc = _score(tok, cand)
                        if sc > best_score:
                            best_score = sc
                            best_cand = cand
                    corrected = (best_cand.correcao or best_cand.palavra or tok) if best_cand else tok
                    lang_tok = (best_cand.lingua if best_cand and best_cand.lingua else lookup.language) or evidence.language
                    aligned_tokens.append(
                        TokenEvidence(
                            raw=tok,
                            normalized=tok,
                            corrected=corrected,
                            lemma=corrected,
                            language=lang_tok,
                            status=IntegrityStatus.VALID if corrected else IntegrityStatus.SUSPECT,
                            reasons=["external_lookup_alignment"],
                        )
                    )
                if aligned_tokens:
                    evidence.tokens = aligned_tokens
                    agg_status = self._aggregate_status([t.status for t in aligned_tokens])
                    evidence.status = agg_status
                    evidence.lemma = "+".join([t.lemma or t.corrected or t.raw for t in aligned_tokens]) if aligned_tokens else evidence.lemma
                    evidence.language = self._majority_language([t.language for t in aligned_tokens])
                    evidence.confidence = 0.8
                    evidence.external_source = lookup.source
                    if lookup.notes:
                        evidence.external_notes = lookup.notes
                    evidence.reasons.append(f"external_lookup_{lookup.source}_aligned")
                    self._store_evidence(cache_key, evidence)
                    return evidence

            if lookup.source == "ollama" or lookup.source == "openai":
                if lookup.corrected.lower() == evidence.normalized_term.lower():
                    evidence.status = IntegrityStatus.VALID
                else:
                    evidence.status = IntegrityStatus.SUSPECT
            else:
                evidence.status = IntegrityStatus.VALID
            evidence.lemma = lookup.lemma or evidence.normalized_term
            evidence.language = lookup.language or evidence.language
            evidence.external_source = lookup.source
            if lookup.notes:
                evidence.external_notes = lookup.notes
            evidence.reasons.append(f"external_lookup_{lookup.source}")
            evidence.confidence = 0.75
            self._store_evidence(cache_key, evidence)
            return evidence

        # No match anywhere
        evidence.status = IntegrityStatus.SUSPECT
        evidence.reasons.append(f"no_linguistic_match/{lang}")
        evidence.confidence = 0.8
        self._store_evidence(cache_key, evidence)
        return evidence


# ---------------------------------------------------------------------------
# Parallel batch processing
# ---------------------------------------------------------------------------


def batch_check_keywords(
    keywords: Iterable[str],
    is_canon_flags: Optional[Iterable[bool]] = None,
    checker: Optional[KeywordIntegrityChecker] = None,
    language_hint: Optional[str] = None,
    num_workers: Optional[int] = None,
    chunksize: int = 64,
    parallel: bool = True,
) -> List[ValidationEvidence]:
    if checker is None:
        checker = get_checker()

    flags_list = list(is_canon_flags) if is_canon_flags is not None else None
    keywords_list = list(keywords)

    if not parallel or len(keywords_list) < chunksize or num_workers == 1:
        evidences: List[ValidationEvidence] = []
        for idx, term in enumerate(keywords_list):
            is_canon = False
            if flags_list is not None and idx < len(flags_list):
                is_canon = bool(flags_list[idx])
            evidences.append(
                checker.check_keyword_integrity(term, is_canon_name=is_canon, language_hint=language_hint)
            )
        return evidences

    workers = num_workers or os.cpu_count() or 1
    from concurrent.futures import ProcessPoolExecutor

    def _task(args: Tuple[int, str, bool]):
        idx, term, canon = args
        ev = checker.check_keyword_integrity(term, is_canon_name=canon, language_hint=language_hint)
        return idx, ev

    tasks: List[Tuple[int, str, bool]] = []
    for idx, term in enumerate(keywords_list):
        canon = False
        if flags_list is not None and idx < len(flags_list):
            canon = bool(flags_list[idx])
        tasks.append((idx, term, canon))

    evidences: List[ValidationEvidence] = [None] * len(tasks)  # type: ignore
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for idx, ev in pool.map(_task, tasks, chunksize=chunksize):
            evidences[idx] = ev

    return evidences


__all__ = [
    "IntegrityStatus",
    "ValidationEvidence",
    "KeywordIntegrityChecker",
    "batch_check_keywords",
    "get_checker",
    "split_mixed_scripts",
]

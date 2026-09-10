"""Deterministic, persistent scripture-citation index for OCR volumes.

The alphabetical-index payload remains the semantic authority.  This module
stores volume-local OCR occurrences and exposes them as compact locator
evidence.  Raw OCR is preserved; matching uses a derived logical text layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    import ahocorasick
except ImportError:  # pragma: no cover - dependency is installed in production
    ahocorasick = None

from .common import PROJECT_ROOT, page_number, page_sort_key
from .editorial_page_estimator import best_guess_pages, estimate_editorial_pages
from .ocr_xml_utils import normalize_visible_text, parse_ocr_xml_page
from .scripture_book_catalog import (
    BOOKS,
    aliases_for_book,
    canonical_book_key,
    canonical_book_label,
    contextual_book_tradition,
    historical_noncanonical_book_key,
    normalize_book_alias,
)


DEFAULT_CITATION_DB = PROJECT_ROOT / "data" / "scripture_citations.db"
DEFAULT_PAYLOAD_DIR = PROJECT_ROOT / "data" / "alphabetical_index_payloads"
SCHEMA_VERSION = 1
DETECTOR_VERSION = 7
MAX_SCAN_WORKERS = 20

_PHYSICAL_SUFFIX_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"^[0-9oil|sb]{1,4}$", re.IGNORECASE)
_NOTE_CALL_BEFORE_BOOK_RE = re.compile(
    r"(?<!\w)(?:\[|\()?\s*(?P<start>[0-9oil|sbzg?]{1,4})"
    r"(?:\s*[-–—]\s*(?P<end>[0-9oil|sbzg?]{1,4}))?"
    r"\s*(?:\]|\))?[.)]?\s+$",
    re.IGNORECASE,
)
_NUMBER_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "i": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "b": "8",
    }
)
_NOTE_NUMBER_TRANSLATION = str.maketrans({"Z": "2", "z": "2", "G": "6", "g": "6"})
_APPARATUS_TYPE_RE = re.compile(
    r"(?i)(?:apparat|aparat|apart|aparo).{0,8}(?:critic|critico)?"
)
_APPARATUS_TEXT_RE = re.compile(
    r"(?i)\b(?:apparatus|apparat(?:us|o|um)?|aparato|"
    r"variae?\s+lectiones|variantia|notae?\s+critic)\b"
)
_APPARATUS_SIGNAL_RE = re.compile(
    r"(?i)(?:\b(?:codd?|codices|mss?|manuscr|lectio|variant|"
    r"omitt?|addit?)\b|\[[^\]\n]{1,100}\]|[†‡※])"
)
_PAGE_LABEL_RE = re.compile(
    r"(?i)\b(?:pag(?:e|ina)?|p\.)\s*(\d{1,4})\b"
)
_FACING_PAGE_RE = re.compile(r"^(\d{1,4})\b.*\b(\d{1,4})$", re.DOTALL)
_BRACKET_PAGE_RE = re.compile(r"[\[(]\s*(\d{1,4})\s*[\])]")
_PSALM_HEADING_RE = re.compile(
    r"(?i)\b(?:ex\s+)?psalm(?:us|o|um|i|os|s)?\.?\s+"
    r"(?P<chapter>[ivxlcdm]{1,8}|\d{1,3})\b"
)
_VERSE_ONLY_RE = re.compile(
    r"(?i)(?:^|[\s.;])(?:v|vers(?:e|et|iculo)?)\.?\s*"
    r"(?P<verse>\d{1,3})(?:\s*[-–—]\s*(?P<verse_end>\d{1,3}))?"
)
_OPEN_ENDED_RE = re.compile(r"(?i)\b(?:seq|seqq|s|ss)\.?\b")
_PUNCTUATION_REQUIRED_ALIASES = {"est", "ex", "is", "ne", "os", "si"}
_SOFT_HYPHENS = {"\u00ad", "\u1806"}
_ZERO_WIDTH_FORMATTING = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_HYPHEN_LIKE = {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}
_MAX_CHAPTER_BY_BOOK = {
    "genesis": 50,
    "exodo": 40,
    "levitico": 27,
    "numeros": 36,
    "deuteronomio": 34,
    "josue": 24,
    "juizes": 21,
    "rute": 4,
    "1 samuel": 31,
    "2 samuel": 24,
    "1 reis": 22,
    "2 reis": 25,
    "1 cronicas": 29,
    "2 cronicas": 36,
    "esdras": 10,
    "neemias": 13,
    "tobias": 14,
    "judite": 16,
    "ester": 16,
    "1 macabeus": 16,
    "2 macabeus": 15,
    "jo": 42,
    "salmos": 151,
    "proverbios": 31,
    "eclesiastes": 12,
    "cantico dos canticos": 8,
    "sabedoria": 19,
    "eclesiastico": 51,
    "isaias": 66,
    "jeremias": 52,
    "lamentacoes": 5,
    "baruc": 6,
    "ezequiel": 48,
    "daniel": 14,
    "oseias": 14,
    "joel": 4,
    "amos": 9,
    "abdias": 1,
    "jonas": 4,
    "miqueias": 7,
    "naum": 3,
    "habacuc": 3,
    "sofonias": 3,
    "ageu": 2,
    "zacarias": 14,
    "malaquias": 4,
    "mateus": 28,
    "marcos": 16,
    "lucas": 24,
    "joao": 21,
    "atos": 28,
    "romanos": 16,
    "1 corintios": 16,
    "2 corintios": 13,
    "galatas": 6,
    "efesios": 6,
    "filipenses": 4,
    "colossenses": 4,
    "1 tessalonicenses": 5,
    "2 tessalonicenses": 3,
    "1 timoteo": 6,
    "2 timoteo": 4,
    "tito": 3,
    "filemon": 1,
    "hebreus": 13,
    "tiago": 5,
    "1 pedro": 5,
    "2 pedro": 3,
    "1 joao": 5,
    "2 joao": 1,
    "3 joao": 1,
    "judas": 1,
    "apocalipse": 22,
}

# The Clementine Psalter has one unusually long chapter (118, with 176
# verses).  The exact Psalm bounds prevent printed page/column numbers next to
# headings from becoming verses, e.g. ``PSALMUS XLII. 782``.  Other books use
# the same conservative absolute ceiling until a versioned multi-versification
# table is promoted to the detector contract.
_ABSOLUTE_MAX_VERSE = 176
_PSALM_MAX_VERSE = (
    6, 13, 9, 10, 13, 11, 18, 10, 39, 8, 9, 6, 7, 5, 11, 15, 51, 15,
    10, 14, 32, 6, 10, 22, 12, 14, 9, 11, 13, 25, 11, 22, 23, 28, 13,
    40, 23, 14, 18, 14, 12, 6, 26, 18, 12, 10, 15, 21, 23, 21, 11, 7,
    9, 24, 13, 12, 12, 18, 14, 9, 13, 12, 11, 14, 20, 8, 36, 37, 6,
    24, 20, 28, 23, 11, 13, 21, 72, 13, 20, 17, 8, 19, 13, 14, 17, 7,
    19, 53, 17, 16, 16, 5, 23, 11, 13, 12, 9, 9, 5, 8, 29, 22, 35, 45,
    48, 43, 14, 31, 7, 10, 10, 9, 26, 9, 10, 2, 29, 176, 7, 8, 9, 4,
    8, 5, 7, 5, 6, 8, 8, 3, 18, 3, 3, 21, 27, 9, 8, 24, 14, 10, 8,
    12, 15, 21, 10, 11, 9, 14, 9, 6,
)


SCHEMA_SQL = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS citation_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    scope_json TEXT NOT NULL,
    workers INTEGER NOT NULL,
    detector_version INTEGER NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    scanned_file_count INTEGER NOT NULL DEFAULT 0,
    skipped_file_count INTEGER NOT NULL DEFAULT 0,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS citation_volumes (
    volume_id TEXT PRIMARY KEY,
    collection TEXT NOT NULL CHECK (collection IN ('PG', 'PL', 'PO')),
    source_root TEXT NOT NULL,
    scan_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (scan_status IN ('pending', 'partial', 'complete')),
    profile_fingerprint TEXT NOT NULL,
    last_run_id INTEGER REFERENCES citation_runs(run_id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES citation_volumes(volume_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL UNIQUE,
    source_relpath TEXT NOT NULL,
    fascicle_key TEXT NOT NULL,
    physical_file_seq INTEGER,
    physical_index INTEGER NOT NULL,
    file_size INTEGER NOT NULL,
    file_mtime_ns INTEGER NOT NULL,
    file_sha256 TEXT NOT NULL,
    parser_status TEXT NOT NULL,
    page_type TEXT NOT NULL,
    is_index_source INTEGER NOT NULL DEFAULT 0 CHECK (is_index_source IN (0, 1)),
    detector_version INTEGER NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    scanned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_citation_files_volume_seq
    ON citation_files(volume_id, physical_file_seq);
CREATE INDEX IF NOT EXISTS idx_citation_files_volume_index
    ON citation_files(volume_id, physical_index);
CREATE INDEX IF NOT EXISTS idx_citation_files_fascicle
    ON citation_files(volume_id, fascicle_key);

CREATE TABLE IF NOT EXISTS citation_file_pages (
    file_id INTEGER NOT NULL REFERENCES citation_files(file_id) ON DELETE CASCADE,
    editorial_page INTEGER NOT NULL,
    page_side TEXT NOT NULL CHECK (page_side IN ('left', 'right', 'single', 'unknown')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_source TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (file_id, editorial_page, page_side, evidence_source)
);
CREATE INDEX IF NOT EXISTS idx_citation_file_pages_page
    ON citation_file_pages(editorial_page, file_id);

CREATE TABLE IF NOT EXISTS citation_book_aliases (
    alias_norm TEXT NOT NULL,
    book_key TEXT NOT NULL,
    collection_scope TEXT NOT NULL DEFAULT '',
    volume_id TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    sample_raw TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (alias_norm, book_key, collection_scope, volume_id, provenance)
);

CREATE TABLE IF NOT EXISTS citation_format_profiles (
    profile_key TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    volume_id TEXT NOT NULL,
    section_key TEXT NOT NULL DEFAULT '',
    profile_json TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT NOT NULL UNIQUE,
    file_id INTEGER NOT NULL REFERENCES citation_files(file_id) ON DELETE CASCADE,
    block_index INTEGER NOT NULL,
    block_type TEXT NOT NULL,
    block_script TEXT NOT NULL,
    bbox TEXT NOT NULL,
    source_start INTEGER NOT NULL,
    source_end INTEGER NOT NULL,
    citation_raw TEXT NOT NULL,
    snippet_raw TEXT NOT NULL,
    context_kind TEXT NOT NULL,
    page_side TEXT NOT NULL CHECK (page_side IN ('left', 'right', 'both', 'unknown')),
    detector_rule TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_citation_groups_file
    ON citation_groups(file_id, block_index, source_start);
CREATE INDEX IF NOT EXISTS idx_citation_groups_context
    ON citation_groups(context_kind);

CREATE TABLE IF NOT EXISTS citation_occurrences (
    occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_key TEXT NOT NULL UNIQUE,
    group_id INTEGER NOT NULL REFERENCES citation_groups(group_id) ON DELETE CASCADE,
    item_order INTEGER NOT NULL,
    book_raw TEXT NOT NULL,
    book_key TEXT,
    historical_book_key TEXT,
    ref_raw TEXT NOT NULL,
    ref_norm TEXT,
    chapter_start INTEGER,
    verse_start INTEGER,
    chapter_end INTEGER,
    verse_end INTEGER,
    is_range INTEGER NOT NULL DEFAULT 0 CHECK (is_range IN (0, 1)),
    open_ended INTEGER NOT NULL DEFAULT 0 CHECK (open_ended IN (0, 1)),
    normalization_status TEXT NOT NULL
        CHECK (normalization_status IN ('normalized', 'ambiguous', 'incomplete', 'historical_noncanonical')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    book_search TEXT NOT NULL,
    ref_search TEXT NOT NULL,
    snippet_search TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE (group_id, item_order)
);
CREATE INDEX IF NOT EXISTS idx_citation_occurrences_book_ref
    ON citation_occurrences(book_key, chapter_start, verse_start);
CREATE INDEX IF NOT EXISTS idx_citation_occurrences_status
    ON citation_occurrences(normalization_status);

CREATE TABLE IF NOT EXISTS citation_index_seeds (
    seed_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    entry_key TEXT NOT NULL,
    scripture_ref_order INTEGER NOT NULL,
    book_raw TEXT,
    book_key TEXT,
    chapter_start INTEGER,
    verse_start INTEGER,
    chapter_end INTEGER,
    verse_end INTEGER,
    cited_pages_json TEXT NOT NULL,
    source_payload TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    seed_status TEXT NOT NULL
        CHECK (seed_status IN ('valid', 'ambiguous', 'incomplete'))
);
CREATE INDEX IF NOT EXISTS idx_citation_seeds_lookup
    ON citation_index_seeds(volume_id, book_key, chapter_start, verse_start);

CREATE TABLE IF NOT EXISTS citation_seed_links (
    seed_key TEXT NOT NULL REFERENCES citation_index_seeds(seed_key) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL REFERENCES citation_occurrences(occurrence_key) ON DELETE CASCADE,
    match_kind TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (seed_key, occurrence_key)
);

CREATE VIRTUAL TABLE IF NOT EXISTS citation_occurrences_fts USING fts5(
    book_search,
    ref_search,
    snippet_search,
    content='citation_occurrences',
    content_rowid='occurrence_id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS citation_occurrences_ai AFTER INSERT ON citation_occurrences BEGIN
    INSERT INTO citation_occurrences_fts(
        rowid, book_search, ref_search, snippet_search
    ) VALUES (new.occurrence_id, new.book_search, new.ref_search, new.snippet_search);
END;
CREATE TRIGGER IF NOT EXISTS citation_occurrences_ad AFTER DELETE ON citation_occurrences BEGIN
    INSERT INTO citation_occurrences_fts(
        citation_occurrences_fts, rowid, book_search, ref_search, snippet_search
    ) VALUES ('delete', old.occurrence_id, old.book_search, old.ref_search, old.snippet_search);
END;
CREATE TRIGGER IF NOT EXISTS citation_occurrences_au AFTER UPDATE ON citation_occurrences BEGIN
    INSERT INTO citation_occurrences_fts(
        citation_occurrences_fts, rowid, book_search, ref_search, snippet_search
    ) VALUES ('delete', old.occurrence_id, old.book_search, old.ref_search, old.snippet_search);
    INSERT INTO citation_occurrences_fts(
        rowid, book_search, ref_search, snippet_search
    ) VALUES (new.occurrence_id, new.book_search, new.ref_search, new.snippet_search);
END;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_citation_db(path: Path | str = DEFAULT_CITATION_DB) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_citation_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    now = _now_iso()
    for key, value in (
        ("schema_version", str(SCHEMA_VERSION)),
        ("detector_version", str(DETECTOR_VERSION)),
    ):
        con.execute(
            """INSERT INTO citation_meta(meta_key, meta_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET
                meta_value = excluded.meta_value,
                updated_at = excluded.updated_at""",
            (key, value, now),
        )


@dataclass(frozen=True)
class LogicalText:
    text: str
    source_offsets: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if not self.source_offsets or end <= start:
            return 0, 0
        start = min(max(0, start), len(self.source_offsets) - 1)
        end = min(max(start + 1, end), len(self.source_offsets))
        return self.source_offsets[start], self.source_offsets[end - 1] + 1


def citation_logical_text(source: str) -> LogicalText:
    """Fold OCR for matching while preserving source offsets.

    This is the citation-safe subset of the repository OCR cleanup: Unicode
    spacing and invisible OCR artifacts are normalized, alphabetic ``-\n``
    wraps are joined, and numeric ranges retain one hyphen.  Destructive
    cleanup is deliberately avoided so matches still map to the original XML
    block text.
    """

    raw = normalize_visible_text(source or "")
    logical_chars: list[tuple[str, int]] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char in _SOFT_HYPHENS or char in _ZERO_WIDTH_FORMATTING:
            index += 1
            continue
        if char in _HYPHEN_LIKE:
            match = re.match(r"[-\u2010-\u2014]\s*\n\s*", raw[index:])
            if match:
                end = index + match.end()
                previous = raw[index - 1] if index else ""
                following = raw[end] if end < len(raw) else ""
                if previous.isalpha() and following.isalpha():
                    index = end
                    continue
                if previous.isdigit() and following.isdigit():
                    logical_chars.append(("-", index))
                    index = end
                    continue
            logical_chars.append(("-", index))
            index += 1
            continue
        logical_chars.append((char, index))
        index += 1

    folded: list[str] = []
    offsets: list[int] = []
    for char, source_offset in logical_chars:
        replacements = (
            unicodedata.normalize("NFKD", char)
            .replace("Æ", "AE")
            .replace("æ", "ae")
            .replace("Œ", "OE")
            .replace("œ", "oe")
            .replace("ſ", "s")
            .casefold()
        )
        for replacement in replacements:
            if unicodedata.combining(replacement):
                continue
            folded.append(replacement)
            offsets.append(source_offset)
    return LogicalText("".join(folded), tuple(offsets))


def _roman_to_int(value: str) -> int | None:
    text = str(value or "").casefold().replace("ı", "i")
    if not _ROMAN_RE.fullmatch(text):
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for char in reversed(text):
        current = values[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total or None


def _chapter_number(value: str) -> int | None:
    raw = str(value or "").strip()
    roman = _roman_to_int(raw)
    if roman is not None:
        return roman
    translated = raw.translate(_NUMBER_TRANSLATION)
    return int(translated) if translated.isdigit() and 0 < int(translated) < 1000 else None


def _verse_number(value: str) -> int | None:
    raw = str(value or "").strip()
    if not _NUMBER_TOKEN_RE.fullmatch(raw):
        return None
    translated = raw.translate(_NUMBER_TRANSLATION)
    return int(translated) if translated.isdigit() and 0 < int(translated) < 1000 else None


def _note_call_number(value: str | None) -> int | None:
    raw = str(value or "").translate(_NUMBER_TRANSLATION).translate(
        _NOTE_NUMBER_TRANSLATION
    )
    return int(raw) if raw.isdigit() and 0 < int(raw) < 10000 else None


def _plausible_chapter(book_key: str | None, chapter: int | None) -> bool:
    if chapter is None:
        return False
    maximum = _MAX_CHAPTER_BY_BOOK.get(book_key or "", 151)
    return 1 <= chapter <= maximum


def _plausible_verse(
    book_key: str | None,
    chapter: int | None,
    verse: int | None,
) -> bool:
    if chapter is None or verse is None or verse < 1:
        return False
    if book_key == "salmos" and 1 <= chapter <= len(_PSALM_MAX_VERSE):
        return verse <= _PSALM_MAX_VERSE[chapter - 1]
    return verse <= _ABSOLUTE_MAX_VERSE


def _has_chapter_only_body_boundary(logical_text: str, end: int) -> bool:
    tail = logical_text[end:]
    if not tail:
        return True
    return bool(re.match(r"\s*(?:[.)\]}:;]|$)", tail))


def _fold_search(value: str) -> str:
    return normalize_book_alias(value)


def _alias_regex(alias: str) -> str:
    tokens = normalize_book_alias(alias).split()
    if not tokens:
        return ""
    separator = r"(?:[\s.]+)"
    pattern = separator.join(re.escape(token) for token in tokens)
    if len(tokens) == 1 and tokens[0] in _PUNCTUATION_REQUIRED_ALIASES:
        pattern += r"\."
    elif len(tokens) == 1 and len(tokens[0]) <= 3:
        pattern += r"\.?"
    return pattern


def _catalog_alias_records(
    observed_aliases: Sequence[tuple[str, str]] = (),
) -> tuple[tuple[str, str], ...]:
    records: set[tuple[str, str]] = set()
    for book in BOOKS:
        for alias in aliases_for_book(book.key):
            records.add((alias, book.key))
    for raw_alias, book_key in observed_aliases:
        folded = normalize_book_alias(raw_alias)
        if folded and book_key and len(folded) >= 2:
            records.add((folded, book_key))
    contextual_aliases = {
        "i regum": "1 samuel",
        "ii regum": "2 samuel",
        "iii regum": "1 reis",
        "iv regum": "2 reis",
        "i reg": "1 samuel",
        "ii reg": "2 samuel",
        "iii reg": "1 reis",
        "iv reg": "2 reis",
        "i rois": "1 samuel",
        "ii rois": "2 samuel",
        "iii rois": "1 reis",
        "iv rois": "2 reis",
        "i kings": "1 reis",
        "ii kings": "2 reis",
        "iii kings": "1 reis",
        "iv kings": "2 reis",
        "i esdrae": "esdras",
        "ii esdrae": "neemias",
        "iii esdrae": "",
        "iv esdrae": "",
        "iii esdras": "",
        "iv esdras": "",
    }
    for alias, book_key in contextual_aliases.items():
        records.add((alias, book_key))
    return tuple(sorted(records, key=lambda item: (-len(item[0]), item[0], item[1])))


def _alias_probe_text(value: str) -> str:
    """Project logical OCR to the separators accepted by alias regexes."""

    return re.sub(r"[.\s]+", " ", value).strip()


@lru_cache(maxsize=64)
def _compiled_alias_prefilter(records: tuple[tuple[str, str], ...]) -> Any:
    aliases = tuple(dict.fromkeys(alias for alias, _book_key in records if alias))
    if ahocorasick is None:
        return aliases
    automaton = ahocorasick.Automaton()
    for index, alias in enumerate(aliases):
        automaton.add_word(alias, (index, alias))
    automaton.make_automaton()
    return automaton


def _contains_book_alias(
    logical_text: str,
    records: tuple[tuple[str, str], ...],
) -> bool:
    """Cheap, boundary-aware alias gate before the citation regex parser.

    Aho-Corasick is only a prefilter.  Regex remains responsible for proving
    the adjacent chapter/verse syntax, which avoids promoting prose mentions
    of biblical books to citations.
    """

    text = f" {_alias_probe_text(logical_text)} "
    matcher = _compiled_alias_prefilter(records)
    if ahocorasick is None:
        return any(
            re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text)
            for alias in matcher
        )
    for end, (_index, alias) in matcher.iter(text):
        start = end - len(alias) + 1
        before = text[start - 1] if start > 0 else " "
        after = text[end + 1] if end + 1 < len(text) else " "
        if not (before.isalnum() or before == "_") and not (
            after.isalnum() or after == "_"
        ):
            return True
    return False


@lru_cache(maxsize=64)
def _compiled_alias_pattern(
    records: tuple[tuple[str, str], ...],
) -> tuple[re.Pattern[str], dict[str, set[str]]]:
    alias_to_keys: dict[str, set[str]] = {}
    patterns: list[str] = []
    seen_patterns: set[str] = set()
    for alias, book_key in records:
        alias_to_keys.setdefault(alias, set()).add(book_key)
        pattern = _alias_regex(alias)
        if pattern and pattern not in seen_patterns:
            seen_patterns.add(pattern)
            patterns.append(pattern)
    citation = re.compile(
        rf"(?<!\w)(?P<book>{'|'.join(patterns)})(?!\w)"
        r"\s*[.,:]?\s*"
        r"(?P<chapter>[0-9oil|sb]{1,3}|[ivxlcdm]{1,10})"
        r"\s*(?P<cv_separator>[,.:]|\s+)\s*"
        r"(?P<verse>[0-9oil|sb]{1,3})(?!\w)"
        r"(?:\s*[-–—]\s*(?P<range_chapter>[ivxlcdm]{1,10}|\d{1,3})"
        r"\s*(?P<range_cv_separator>[,.:])\s*(?P<range_verse>\d{1,3})"
        r"|\s*[-–—]\s*(?P<verse_end>\d{1,3}))?",
        re.IGNORECASE,
    )
    return citation, alias_to_keys


@lru_cache(maxsize=64)
def _compiled_book_chapter_pattern(
    records: tuple[tuple[str, str], ...],
) -> tuple[re.Pattern[str], dict[str, set[str]]]:
    alias_to_keys: dict[str, set[str]] = {}
    patterns: list[str] = []
    seen_patterns: set[str] = set()
    for alias, book_key in records:
        alias_to_keys.setdefault(alias, set()).add(book_key)
        pattern = _alias_regex(alias)
        if pattern and pattern not in seen_patterns:
            patterns.append(pattern)
            seen_patterns.add(pattern)
    return (
        re.compile(
            rf"(?<!\w)(?P<book>{'|'.join(patterns)})(?!\w)"
            r"\s*[.,:]?\s*"
            r"(?P<chapter>\d{1,3}(?!\w)|[ivxlcdm]{1,10}(?!\w))",
            re.IGNORECASE,
        ),
        alias_to_keys,
    )


def _leading_note_call(
    logical_text: str,
    book_start: int,
) -> tuple[re.Match[str], int, int | None] | None:
    prefix_start = max(0, book_start - 24)
    match = _NOTE_CALL_BEFORE_BOOK_RE.search(logical_text[prefix_start:book_start])
    if match is None:
        return None
    start_value = _note_call_number(match.group("start"))
    return match, prefix_start, start_value


def _note_call_evidence(
    source: str,
    logical: LogicalText,
    book_start: int,
) -> dict[str, Any] | None:
    found = _leading_note_call(logical.text, book_start)
    if found is None:
        return None
    match, prefix_start, start_value = found
    logical_start = prefix_start + match.start()
    logical_end = prefix_start + match.end()
    source_start, source_end = logical.source_span(logical_start, logical_end)
    end_value = _note_call_number(match.group("end")) if match.group("end") else None
    return {
        "raw": source[source_start:source_end].strip(),
        "number_start": start_value,
        "number_end": end_value,
        "ocr_numeric_repair": bool(
            re.search(r"[oil|sbzg?]", match.group(0), re.IGNORECASE)
        ),
    }


def _dot_verse_is_next_footnote_call(
    logical_text: str,
    match: re.Match[str],
    alias_records: tuple[tuple[str, str], ...],
) -> bool:
    """Detect ``43 Dan. XIII. 44 Gen. XXXIX.``-style note chains."""

    if match.group("cv_separator") != ".":
        return False
    leading = _leading_note_call(logical_text, match.start("book"))
    if leading is None:
        return False
    _leading_match, _prefix_start, leading_number = leading
    trailing_number = _verse_number(match.group("verse"))
    if (
        leading_number is not None
        and trailing_number is not None
        and trailing_number == leading_number + 1
    ):
        # Facing-page headers and chained reference calls commonly have the
        # shape ``781 PSALMUS XLII. 782`` or ``45 Matth. V. 46``.  The final
        # number is another editorial marker, not a verse.
        return True
    suffix = logical_text[match.end("verse") : match.end("verse") + 48]
    return bool(re.match(r"\s+", suffix)) and _contains_book_alias(
        suffix,
        alias_records,
    )


def _range_endpoint_is_next_footnote_call(
    logical_text: str,
    match: re.Match[str],
) -> bool:
    """Detect ``18 Joan. 1, 1-3. 19 Act.``-style note chains."""

    if (
        match.group("range_chapter") is None
        or match.group("range_verse") is None
        or match.group("range_cv_separator") != "."
    ):
        return False
    leading = _leading_note_call(logical_text, match.start("book"))
    if leading is None:
        return False
    _leading_match, _prefix_start, leading_number = leading
    trailing_number = _verse_number(match.group("range_verse"))
    if (
        leading_number is None
        or trailing_number is None
        or trailing_number != leading_number + 1
    ):
        return False
    return True


def _resolve_book(
    book_raw: str,
    *,
    collection: str,
    alias_to_keys: Mapping[str, set[str]],
) -> tuple[str | None, str | None, str]:
    historical = historical_noncanonical_book_key(book_raw)
    if historical:
        return None, historical, "historical_noncanonical"
    folded = normalize_book_alias(book_raw)
    local_tradition = contextual_book_tradition(collection, book_raw)
    if collection == "PO" and re.fullmatch(r"(?:i|ii|1|2)\s+kings", folded):
        return None, None, "ambiguous"
    tradition = local_tradition or (
        "vulgate_migne" if collection in {"PG", "PL"} else None
    )
    key = canonical_book_key(book_raw, tradition=tradition)
    if key:
        return key, None, "normalized"
    keys = alias_to_keys.get(folded, set())
    if len(keys) == 1:
        return next(iter(keys)), None, "normalized"
    return None, None, "ambiguous"


def _context_kind(
    *,
    block_type: str,
    page_type: str,
    text: str,
    is_index_source: bool,
) -> str:
    if is_index_source or page_type == "indice":
        return "index"
    folded_type = normalize_book_alias(block_type)
    if _APPARATUS_TYPE_RE.search(folded_type):
        return "critical_apparatus"
    if folded_type in {"nota", "nota marginal", "nota rodape"}:
        return "note"
    if folded_type in {"cabecalho", "header"}:
        return "header"
    if folded_type in {"rodape", "footer"}:
        return "footer"
    if _APPARATUS_TEXT_RE.search(text) or len(_APPARATUS_SIGNAL_RE.findall(text)) >= 2:
        return "critical_apparatus"
    return "body"


def _parse_bbox(value: str) -> tuple[float, float, float, float] | None:
    try:
        parts = [float(item.strip()) for item in str(value or "").split(",")]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]


def _page_side(bbox: str, max_x: float) -> str:
    parsed = _parse_bbox(bbox)
    if parsed is None or max_x <= 0:
        return "unknown"
    left, _, right, _ = parsed
    width = right - left
    if width >= max_x * 0.72:
        return "both"
    center = (left + right) / 2
    if center < max_x * 0.46:
        return "left"
    if center > max_x * 0.54:
        return "right"
    return "both"


def _snippet(source: str, start: int, end: int, limit: int = 260) -> str:
    lower = max(0, start - limit // 2)
    upper = min(len(source), end + limit // 2)
    value = re.sub(r"\s+", " ", source[lower:upper]).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _ref_norm(
    book_key: str | None,
    chapter: int | None,
    verse: int | None,
    chapter_end: int | None,
    verse_end: int | None,
) -> str | None:
    label = canonical_book_label(book_key) if book_key else None
    if not label or chapter is None:
        return None
    value = f"{label} {chapter}"
    if verse is not None:
        value += f",{verse}"
    if chapter_end is not None and verse_end is not None:
        value += (
            f"-{verse_end}"
            if chapter_end == chapter
            else f"-{chapter_end},{verse_end}"
        )
    elif verse_end is not None:
        value += f"-{verse_end}"
    return value


def _extend_list_items(
    folded: str,
    start: int,
    *,
    chapter: int,
) -> tuple[list[tuple[int, int | None]], int]:
    items: list[tuple[int, int | None]] = []
    cursor = start
    while True:
        match = re.match(
            r"\s*,\s*(?P<verse>\d{1,3})(?:\s*[-–—]\s*(?P<end>\d{1,3}))?",
            folded[cursor:],
        )
        if not match:
            break
        verse = _verse_number(match.group("verse"))
        verse_end = _verse_number(match.group("end")) if match.group("end") else None
        if verse is None:
            break
        items.append((verse, verse_end))
        cursor += match.end()
    return items, cursor


@lru_cache(maxsize=1)
def _compiled_implicit_reference_pattern() -> re.Pattern[str]:
    number = r"(?:[0-9oil|sb]{1,3}|[ivxlcdm]{1,10})"
    return re.compile(
        rf"(?:"
        rf"\s*;\s*(?P<semicolon_chapter>{number})\s*[,.:]\s*"
        rf"(?P<semicolon_verse>[0-9oil|sb]{{1,3}})"
        rf"|\s*,?\s*(?:et|and|e|ac|atque)\.?\s+"
        rf"(?:(?P<conjunction_chapter>{number})\s*[,.:]\s*)?"
        rf"(?P<conjunction_verse>[0-9oil|sb]{{1,3}})"
        rf")"
        rf"(?:\s*[-–—]\s*(?:(?P<end_chapter>{number})\s*[,.:]\s*)?"
        rf"(?P<end_verse>[0-9oil|sb]{{1,3}}))?",
        re.IGNORECASE,
    )


def _extend_implicit_reference_items(
    folded: str,
    start: int,
    *,
    chapter: int,
) -> tuple[list[tuple[int, int, int | None, int | None]], int]:
    """Parse same-book continuations after a complete reference.

    Supported examples include ``Luc. XXIV, 4 et 5``,
    ``Deut. I, 16, et XVI, 18`` and ``Psal. 4, 9; 91, 1``.
    """

    continuation = _compiled_implicit_reference_pattern()
    items: list[tuple[int, int, int | None, int | None]] = []
    cursor = start
    while True:
        match = continuation.match(folded, cursor)
        if match is None:
            break
        chapter_raw = match.group("semicolon_chapter") or match.group(
            "conjunction_chapter"
        )
        verse_raw = match.group("semicolon_verse") or match.group(
            "conjunction_verse"
        )
        item_chapter = _chapter_number(chapter_raw) if chapter_raw else chapter
        item_verse = _verse_number(verse_raw)
        end_chapter = (
            _chapter_number(match.group("end_chapter"))
            if match.group("end_chapter")
            else item_chapter
            if match.group("end_verse")
            else None
        )
        end_verse = (
            _verse_number(match.group("end_verse"))
            if match.group("end_verse")
            else None
        )
        if item_chapter is None or item_verse is None or (
            match.group("end_verse") and end_verse is None
        ):
            break
        items.append((item_chapter, item_verse, end_chapter, end_verse))
        cursor = match.end()
    return items, cursor


def _group_key(
    file_path: str,
    block_index: int,
    start: int,
    end: int,
    rule: str,
) -> str:
    return _fingerprint([file_path, block_index, start, end, rule])


def _occurrence_key(group_key: str, item_order: int, values: Sequence[Any]) -> str:
    return _fingerprint([group_key, item_order, *values])


def _extract_explicit_citations(
    source: str,
    *,
    file_path: str,
    block_index: int,
    block_type: str,
    block_script: str,
    bbox: str,
    page_side: str,
    context_kind: str,
    collection: str,
    alias_records: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    logical = citation_logical_text(source)
    if not _contains_book_alias(logical.text, alias_records):
        return []
    pattern, alias_to_keys = _compiled_alias_pattern(alias_records)
    groups: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in pattern.finditer(logical.text):
        chapter = _chapter_number(match.group("chapter"))
        verse = _verse_number(match.group("verse"))
        if chapter is None or verse is None:
            continue
        if _dot_verse_is_next_footnote_call(
            logical.text,
            match,
            alias_records,
        ):
            continue
        book_start, book_end = logical.source_span(
            match.start("book"), match.end("book")
        )
        book_raw = source[book_start:book_end]
        note_call = _note_call_evidence(
            source,
            logical,
            match.start("book"),
        )
        book_key, historical_key, status = _resolve_book(
            match.group("book"),
            collection=collection,
            alias_to_keys=alias_to_keys,
        )
        if not _plausible_chapter(book_key, chapter):
            continue
        primary_verse_plausible = _plausible_verse(book_key, chapter, verse)
        if (
            not primary_verse_plausible
            and book_key == "salmos"
            and match.group("cv_separator") == "."
        ):
            # Leave dot-separated Psalm headings free for the chapter-only
            # detector.  A typical false verse is the facing page number in
            # ``PSALMUS XLII. 782``.  Comma/colon forms remain preserved below
            # as incomplete evidence instead of being silently rewritten.
            continue
        footnote_endpoint = _range_endpoint_is_next_footnote_call(
            logical.text,
            match,
        )
        if footnote_endpoint:
            chapter_end = chapter
            verse_end = _verse_number(match.group("range_chapter"))
        else:
            chapter_end = (
                _chapter_number(match.group("range_chapter"))
                if match.group("range_chapter")
                else None
            )
            verse_end = (
                _verse_number(match.group("range_verse"))
                if match.group("range_verse")
                else _verse_number(match.group("verse_end"))
                if match.group("verse_end")
                else None
            )
        if verse_end is not None and chapter_end is None:
            chapter_end = chapter
        if chapter_end is not None and not _plausible_chapter(
            book_key,
            chapter_end,
        ):
            continue
        if footnote_endpoint:
            list_items = []
            implicit_items = []
            logical_end = match.end("range_chapter")
        else:
            list_items, logical_end = _extend_list_items(
                logical.text,
                match.end(),
                chapter=chapter,
            )
            implicit_items, logical_end = _extend_implicit_reference_items(
                logical.text,
                logical_end,
                chapter=chapter,
            )
        if any(
            not _plausible_chapter(book_key, item_chapter)
            or (
                item_chapter_end is not None
                and not _plausible_chapter(book_key, item_chapter_end)
            )
            for (
                item_chapter,
                _item_verse,
                item_chapter_end,
                _item_verse_end,
            ) in implicit_items
        ):
            continue
        logical_end = max(logical_end, match.end())
        source_start, source_end = logical.source_span(match.start(), logical_end)
        if any(start <= source_start < end for start, end in occupied):
            continue
        occupied.append((source_start, source_end))
        citation_raw = source[source_start:source_end]
        open_ended = bool(
            _OPEN_ENDED_RE.search(source[source_end : source_end + 12])
        )
        rule = "explicit_book_chapter_verse"
        confidence = 0.94
        if context_kind == "critical_apparatus":
            confidence = 0.98
        elif context_kind == "index":
            confidence = 0.90
        if status == "ambiguous":
            confidence = min(confidence, 0.62)
        group_key = _group_key(
            file_path,
            block_index,
            source_start,
            source_end,
            rule,
        )
        atoms = [
            (chapter, verse, chapter_end, verse_end),
            *[
                (
                    chapter,
                    item_verse,
                    chapter if item_end is not None else None,
                    item_end,
                )
                for item_verse, item_end in list_items
            ],
            *implicit_items,
        ]
        occurrences = []
        for item_order, (
            item_chapter,
            item_verse,
            item_chapter_end,
            item_verse_end,
        ) in enumerate(
            atoms,
            start=1,
        ):
            item_status = status
            verse_plausible = _plausible_verse(
                book_key,
                item_chapter,
                item_verse,
            ) and (
                item_verse_end is None
                or _plausible_verse(
                    book_key,
                    item_chapter_end
                    if item_chapter_end is not None
                    else item_chapter,
                    item_verse_end,
                )
            )
            if not verse_plausible and item_status == "normalized":
                item_status = "incomplete"
            ref_norm = (
                _ref_norm(
                    book_key,
                    item_chapter,
                    item_verse,
                    item_chapter_end,
                    item_verse_end,
                )
                if verse_plausible
                else None
            )
            if ref_norm is None and item_status == "normalized":
                item_status = "incomplete"
            occurrence_key = _occurrence_key(
                group_key,
                item_order,
                (
                    book_key,
                    historical_key,
                    item_chapter,
                    item_verse,
                    item_chapter_end,
                    item_verse_end,
                ),
            )
            occurrences.append(
                {
                    "occurrence_key": occurrence_key,
                    "item_order": item_order,
                    "book_raw": book_raw,
                    "book_key": book_key,
                    "historical_book_key": historical_key,
                    "ref_raw": citation_raw,
                    "ref_norm": ref_norm,
                    "chapter_start": item_chapter,
                    "verse_start": item_verse,
                    "chapter_end": item_chapter_end,
                    "verse_end": item_verse_end,
                    "is_range": int(item_verse_end is not None),
                    "open_ended": int(open_ended),
                    "normalization_status": item_status,
                    "confidence": confidence,
                    "raw_json": {
                        "list_item": len(atoms) > 1,
                        "source_group_raw": citation_raw,
                    },
                }
            )
        groups.append(
            {
                "group_key": group_key,
                "block_index": block_index,
                "block_type": block_type,
                "block_script": block_script,
                "bbox": bbox,
                "source_start": source_start,
                "source_end": source_end,
                "citation_raw": citation_raw,
                "snippet_raw": _snippet(source, source_start, source_end),
                "context_kind": context_kind,
                "page_side": page_side,
                "detector_rule": rule,
                "confidence": confidence,
                "evidence_json": {
                    "kind": "scripture_regex",
                    "book_alias": book_raw,
                    "context_kind": context_kind,
                    "matching_pipeline": "offset_preserving_ocr_clean+aho_prefilter+regex",
                    **({"note_call": note_call} if note_call else {}),
                },
                "occurrences": occurrences,
            }
        )
    return groups


def _extract_chapter_only_citations(
    source: str,
    *,
    file_path: str,
    block_index: int,
    block_type: str,
    block_script: str,
    bbox: str,
    page_side: str,
    context_kind: str,
    collection: str,
    alias_records: tuple[tuple[str, str], ...],
    occupied_spans: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    logical = citation_logical_text(source)
    if not _contains_book_alias(logical.text, alias_records):
        return []
    pattern, alias_to_keys = _compiled_book_chapter_pattern(alias_records)
    groups = []
    for match in pattern.finditer(logical.text):
        chapter = _chapter_number(match.group("chapter"))
        if chapter is None:
            continue
        if context_kind == "body" and not _has_chapter_only_body_boundary(
            logical.text,
            match.end(),
        ):
            continue
        source_start, source_end = logical.source_span(match.start(), match.end())
        if any(start <= source_start < end for start, end in occupied_spans):
            continue
        book_start, book_end = logical.source_span(
            match.start("book"),
            match.end("book"),
        )
        book_raw = source[book_start:book_end]
        note_call = _note_call_evidence(
            source,
            logical,
            match.start("book"),
        )
        book_key, historical_key, status = _resolve_book(
            match.group("book"),
            collection=collection,
            alias_to_keys=alias_to_keys,
        )
        if not _plausible_chapter(book_key, chapter):
            continue
        citation_raw = source[source_start:source_end]
        rule = "explicit_book_chapter"
        confidence = {
            "critical_apparatus": 0.78,
            "body": 0.74,
            "note": 0.72,
            "footer": 0.72,
            "index": 0.70,
        }.get(context_kind, 0.70)
        if status == "ambiguous":
            confidence = min(confidence, 0.56)
        group_key = _group_key(
            file_path,
            block_index,
            source_start,
            source_end,
            rule,
        )
        ref_norm = _ref_norm(book_key, chapter, None, None, None)
        groups.append(
            {
                "group_key": group_key,
                "block_index": block_index,
                "block_type": block_type,
                "block_script": block_script,
                "bbox": bbox,
                "source_start": source_start,
                "source_end": source_end,
                "citation_raw": citation_raw,
                "snippet_raw": _snippet(source, source_start, source_end),
                "context_kind": context_kind,
                "page_side": page_side,
                "detector_rule": rule,
                "confidence": confidence,
                "evidence_json": {
                    "kind": "scripture_chapter_regex",
                    "book_alias": book_raw,
                    "context_kind": context_kind,
                    "matching_pipeline": "offset_preserving_ocr_clean+aho_prefilter+regex",
                    **({"note_call": note_call} if note_call else {}),
                },
                "occurrences": [
                    {
                        "occurrence_key": _occurrence_key(
                            group_key,
                            1,
                            (book_key, historical_key, chapter),
                        ),
                        "item_order": 1,
                        "book_raw": book_raw,
                        "book_key": book_key,
                        "historical_book_key": historical_key,
                        "ref_raw": citation_raw,
                        "ref_norm": ref_norm,
                        "chapter_start": chapter,
                        "verse_start": None,
                        "chapter_end": None,
                        "verse_end": None,
                        "is_range": 0,
                        "open_ended": 0,
                        "normalization_status": status,
                        "confidence": confidence,
                        "raw_json": {"chapter_only": True},
                    }
                ],
            }
        )
    return groups


def _extract_psalm_heading_citations(
    source: str,
    *,
    file_path: str,
    block_index: int,
    block_type: str,
    block_script: str,
    bbox: str,
    page_side: str,
    context_kind: str,
) -> list[dict[str, Any]]:
    logical = citation_logical_text(source)
    headings = list(_PSALM_HEADING_RE.finditer(logical.text))
    if not headings:
        return []
    groups: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        chapter = _chapter_number(heading.group("chapter"))
        if not _plausible_chapter("salmos", chapter):
            continue
        upper = headings[index + 1].start() if index + 1 < len(headings) else len(logical.text)
        for verse_match in _VERSE_ONLY_RE.finditer(
            logical.text,
            heading.end(),
            upper,
        ):
            verse = _verse_number(verse_match.group("verse"))
            verse_end = (
                _verse_number(verse_match.group("verse_end"))
                if verse_match.group("verse_end")
                else None
            )
            if verse is None:
                continue
            if not _plausible_verse("salmos", chapter, verse):
                continue
            if verse_end is not None and not _plausible_verse(
                "salmos", chapter, verse_end
            ):
                continue
            source_start, source_end = logical.source_span(
                verse_match.start(),
                verse_match.end(),
            )
            citation_raw = source[source_start:source_end]
            rule = "psalm_heading_inheritance"
            group_key = _group_key(
                file_path,
                block_index,
                source_start,
                source_end,
                rule,
            )
            ref_norm = _ref_norm(
                "salmos",
                chapter,
                verse,
                chapter if verse_end is not None else None,
                verse_end,
            )
            occurrence_key = _occurrence_key(
                group_key,
                1,
                ("salmos", chapter, verse, verse_end),
            )
            confidence = 0.92 if context_kind in {"critical_apparatus", "index"} else 0.80
            heading_start, heading_end = logical.source_span(
                heading.start(),
                heading.end(),
            )
            groups.append(
                {
                    "group_key": group_key,
                    "block_index": block_index,
                    "block_type": block_type,
                    "block_script": block_script,
                    "bbox": bbox,
                    "source_start": source_start,
                    "source_end": source_end,
                    "citation_raw": citation_raw,
                    "snippet_raw": _snippet(source, source_start, source_end),
                    "context_kind": context_kind,
                    "page_side": page_side,
                    "detector_rule": rule,
                    "confidence": confidence,
                    "evidence_json": {
                        "kind": "inherited_scripture_heading",
                        "book_key": "salmos",
                        "chapter": chapter,
                    },
                    "occurrences": [
                        {
                            "occurrence_key": occurrence_key,
                            "item_order": 1,
                            "book_raw": "Psal.",
                            "book_key": "salmos",
                            "historical_book_key": None,
                            "ref_raw": citation_raw,
                            "ref_norm": ref_norm,
                            "chapter_start": chapter,
                            "verse_start": verse,
                            "chapter_end": chapter if verse_end is not None else None,
                            "verse_end": verse_end,
                            "is_range": int(verse_end is not None),
                            "open_ended": int(
                                bool(
                                    _OPEN_ENDED_RE.search(
                                        source[source_end : source_end + 12]
                                    )
                                )
                            ),
                            "normalization_status": "normalized",
                            "confidence": confidence,
                            "raw_json": {
                                "heading_raw": source[heading_start:heading_end],
                            },
                        }
                    ],
                }
            )
    return groups


def _direct_header_pages(header_text: str) -> list[dict[str, Any]]:
    pages: dict[tuple[int, str], dict[str, Any]] = {}
    for raw_line in header_text.splitlines()[:5]:
        line = re.sub(r"<[^>]+>", " ", raw_line).strip()
        facing = _FACING_PAGE_RE.match(line)
        if facing:
            left, right = int(facing.group(1)), int(facing.group(2))
            if 0 < left < 10000 and right == left + 1:
                pages[(left, "left")] = {
                    "editorial_page": left,
                    "page_side": "left",
                    "confidence": 0.90,
                    "evidence_source": "direct_header",
                    "evidence_json": {"kind": "facing_page_header", "raw": line},
                }
                pages[(right, "right")] = {
                    "editorial_page": right,
                    "page_side": "right",
                    "confidence": 0.90,
                    "evidence_source": "direct_header",
                    "evidence_json": {"kind": "facing_page_header", "raw": line},
                }
        for value in _BRACKET_PAGE_RE.findall(line):
            page = int(value)
            if 0 < page < 10000:
                pages.setdefault(
                    (page, "single"),
                    {
                        "editorial_page": page,
                        "page_side": "single",
                        "confidence": 0.76,
                        "evidence_source": "direct_header",
                        "evidence_json": {"kind": "bracketed_header", "raw": line},
                    },
                )
        for value in _PAGE_LABEL_RE.findall(line):
            page = int(value)
            if 0 < page < 10000:
                pages.setdefault(
                    (page, "single"),
                    {
                        "editorial_page": page,
                        "page_side": "single",
                        "confidence": 0.78,
                        "evidence_source": "direct_header",
                        "evidence_json": {"kind": "page_label", "raw": line},
                    },
                )
    return sorted(pages.values(), key=lambda item: (item["editorial_page"], item["page_side"]))


def _fascicle_key(path: Path) -> str:
    return _PHYSICAL_SUFFIX_RE.sub("", path.name)


@dataclass(frozen=True)
class ScanTask:
    volume_id: str
    collection: str
    source_root: str
    file_path: str
    physical_index: int
    is_index_source: bool
    observed_aliases: tuple[tuple[str, str], ...]
    estimator_pages: tuple[dict[str, Any], ...]
    profile_fingerprint: str


def scan_file_task(task: ScanTask) -> dict[str, Any]:
    path = Path(task.file_path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    page = parse_ocr_xml_page(raw)
    max_x = 0.0
    for block in page.blocks:
        bbox = _parse_bbox(block.bbox)
        if bbox is not None:
            max_x = max(max_x, bbox[2])
    alias_records = _catalog_alias_records(task.observed_aliases)
    groups: list[dict[str, Any]] = []
    for block_index, block in enumerate(page.blocks):
        source = normalize_visible_text(block.content_raw)
        if not source.strip():
            continue
        context_kind = _context_kind(
            block_type=block.tipo or block.tag_name,
            page_type=page.tipo,
            text=source,
            is_index_source=task.is_index_source,
        )
        side = _page_side(block.bbox, max_x)
        common = {
            "source": source,
            "file_path": str(path.resolve()),
            "block_index": block_index,
            "block_type": block.tipo or block.tag_name,
            "block_script": block.script,
            "bbox": block.bbox,
            "page_side": side,
            "context_kind": context_kind,
        }
        explicit = _extract_explicit_citations(
            **common,
            collection=task.collection,
            alias_records=alias_records,
        )
        groups.extend(explicit)
        groups.extend(
            _extract_chapter_only_citations(
                **common,
                collection=task.collection,
                alias_records=alias_records,
                occupied_spans=[
                    (int(item["source_start"]), int(item["source_end"]))
                    for item in explicit
                ],
            )
        )
        groups.extend(_extract_psalm_heading_citations(**common))
    direct_pages = _direct_header_pages(page.header_text)
    estimated = list(task.estimator_pages)
    page_keys = {
        (
            int(item["editorial_page"]),
            str(item["page_side"]),
            str(item["evidence_source"]),
        )
        for item in direct_pages
    }
    for item in estimated:
        key = (
            int(item["editorial_page"]),
            str(item["page_side"]),
            str(item["evidence_source"]),
        )
        if key not in page_keys:
            direct_pages.append(dict(item))
            page_keys.add(key)
    stat = path.stat()
    return {
        "volume_id": task.volume_id,
        "collection": task.collection,
        "source_root": task.source_root,
        "file_path": str(path.resolve()),
        "source_relpath": str(path.resolve().relative_to(Path(task.source_root).resolve())),
        "fascicle_key": _fascicle_key(path),
        "physical_file_seq": page_number(path),
        "physical_index": task.physical_index,
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
        "file_sha256": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest(),
        "parser_status": "xml" if page.is_xml and page.parse_ok else "xml_fallback" if page.is_xml else "plain_text",
        "page_type": page.tipo,
        "is_index_source": int(task.is_index_source),
        "detector_version": DETECTOR_VERSION,
        "profile_fingerprint": task.profile_fingerprint,
        "scanned_at": _now_iso(),
        "pages": sorted(
            direct_pages,
            key=lambda item: (
                int(item["editorial_page"]),
                str(item["page_side"]),
                str(item["evidence_source"]),
            ),
        ),
        "groups": sorted(
            groups,
            key=lambda item: (
                int(item["block_index"]),
                int(item["source_start"]),
                item["group_key"],
            ),
        ),
    }


def _payload_path(volume_id: str, payload_dir: Path) -> Path:
    return payload_dir / f"{volume_id}_alphabetical_indices.json"


def _load_payload_profile(
    volume_id: str,
    collection: str,
    payload_dir: Path,
) -> dict[str, Any]:
    path = _payload_path(volume_id, payload_dir)
    if not path.is_file():
        return {
            "path": str(path),
            "fingerprint": _fingerprint([volume_id, collection, "no-payload"]),
            "observed_aliases": [],
            "seeds": [],
            "index_ranges": [],
            "formats": [],
        }
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    entries = {
        str(entry.get("entry_key") or ""): entry
        for entry in payload.get("entries") or []
        if isinstance(entry, Mapping)
    }
    material_by_entry: dict[str, list[int]] = {}
    for ref in payload.get("refs") or []:
        if not isinstance(ref, Mapping):
            continue
        page = ref.get("page_ref_int")
        if isinstance(page, int) and 0 < page < 10000:
            material_by_entry.setdefault(str(ref.get("entry_key") or ""), []).append(page)
    observed_aliases: set[tuple[str, str]] = set()
    seeds: list[dict[str, Any]] = []
    formats: dict[str, dict[str, Any]] = {}
    for scripture in payload.get("scripture_refs") or []:
        if not isinstance(scripture, Mapping):
            continue
        entry_key = str(scripture.get("entry_key") or "")
        entry = entries.get(entry_key, {})
        section_key = str(entry.get("section_key") or "")
        book_raw = str(scripture.get("book_raw") or "").strip()
        local_profile = None
        tradition = contextual_book_tradition(
            collection,
            book_raw,
            local_profile=local_profile,
        )
        book_key = canonical_book_key(
            scripture.get("book_key")
            or scripture.get("book_norm")
            or book_raw,
            tradition=tradition or ("vulgate_migne" if collection in {"PG", "PL"} else None),
        )
        historical = historical_noncanonical_book_key(book_raw)
        if book_raw and book_key:
            observed_aliases.add((book_raw, book_key))
        chapter = scripture.get("chapter_start")
        verse = scripture.get("verse_start")
        status = (
            "valid"
            if book_key and isinstance(chapter, int)
            else "ambiguous"
            if book_raw
            else "incomplete"
        )
        ref_order = int(scripture.get("ref_order") or 1)
        seed_key = _fingerprint([volume_id, entry_key, ref_order])
        seeds.append(
            {
                "seed_key": seed_key,
                "volume_id": volume_id,
                "section_key": section_key,
                "entry_key": entry_key,
                "scripture_ref_order": ref_order,
                "book_raw": book_raw or None,
                "book_key": book_key,
                "chapter_start": chapter if isinstance(chapter, int) else None,
                "verse_start": verse if isinstance(verse, int) else None,
                "chapter_end": scripture.get("chapter_end") if isinstance(scripture.get("chapter_end"), int) else None,
                "verse_end": scripture.get("verse_end") if isinstance(scripture.get("verse_end"), int) else None,
                "cited_pages_json": sorted(set(material_by_entry.get(entry_key, []))),
                "source_payload": str(path.resolve()),
                "source_fingerprint": hashlib.sha256(raw).hexdigest(),
                "seed_status": status,
            }
        )
        if section_key:
            profile = formats.setdefault(
                section_key,
                {
                    "section_key": section_key,
                    "book_aliases": set(),
                    "separators": set(),
                    "sample_refs": [],
                },
            )
            if book_raw:
                profile["book_aliases"].add(book_raw)
            ref_raw = str(scripture.get("ref_raw") or "")
            profile["separators"].update(re.findall(r"[:,;.]|[-–—]", ref_raw))
            if ref_raw and len(profile["sample_refs"]) < 12:
                profile["sample_refs"].append(ref_raw)
    index_ranges = []
    for section in payload.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        start = str(section.get("file_start") or "")
        end = str(section.get("file_end") or "")
        if start and end:
            index_ranges.append(
                {
                    "start": start,
                    "end": end,
                    "start_seq": page_number(Path(start)),
                    "end_seq": page_number(Path(end)),
                }
            )
    normalized_formats = []
    for section_key, value in sorted(formats.items()):
        normalized_formats.append(
            {
                "section_key": section_key,
                "book_aliases": sorted(value["book_aliases"], key=normalize_book_alias),
                "separators": sorted(value["separators"]),
                "sample_refs": value["sample_refs"],
            }
        )
    return {
        "path": str(path.resolve()),
        "fingerprint": hashlib.sha256(raw).hexdigest(),
        "observed_aliases": sorted(observed_aliases),
        "seeds": seeds,
        "index_ranges": index_ranges,
        "formats": normalized_formats,
    }


def _is_index_file(path: Path, ranges: Sequence[Mapping[str, Any]]) -> bool:
    resolved = path.resolve()
    seq = page_number(path)
    for item in ranges:
        start = Path(str(item.get("start") or "")).expanduser().resolve()
        end = Path(str(item.get("end") or "")).expanduser().resolve()
        if resolved in {start, end}:
            return True
        start_seq = item.get("start_seq")
        end_seq = item.get("end_seq")
        if (
            seq is not None
            and isinstance(start_seq, int)
            and isinstance(end_seq, int)
            and min(start_seq, end_seq) <= seq <= max(start_seq, end_seq)
        ):
            return True
    return False


def _estimator_pages_by_file(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
) -> dict[str, tuple[dict[str, Any], ...]]:
    if collection not in {"PG", "PL"}:
        return {}
    payload = estimate_editorial_pages(
        volume_id=volume_id,
        collection=collection,
        source_root=source_root,
        window=4,
    )
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for item in payload.get("files") or []:
        file_path = str(Path(str(item.get("file") or "")).resolve())
        pages = best_guess_pages(item.get("best_guess"))
        sides = (
            ["left", "right"]
            if len(pages) == 2
            else ["single"] * len(pages)
        )
        result[file_path] = tuple(
            {
                "editorial_page": page,
                "page_side": side,
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_source": "editorial_page_estimator",
                "evidence_json": {
                    "confidence_label": item.get("confidence_label"),
                    "evidence": item.get("evidence") or [],
                    "warnings": item.get("warnings") or [],
                },
            }
            for page, side in zip(pages, sides)
        )
    return result


def _write_profile_rows(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    collection: str,
    profile: Mapping[str, Any],
) -> None:
    con.execute("DELETE FROM citation_book_aliases WHERE volume_id = ?", (volume_id,))
    for alias, book_key in profile.get("observed_aliases") or []:
        con.execute(
            """INSERT OR REPLACE INTO citation_book_aliases(
                alias_norm, book_key, collection_scope, volume_id,
                provenance, sample_raw, occurrence_count
            ) VALUES (?, ?, ?, ?, 'alphabetical_payload', ?, 1)""",
            (normalize_book_alias(alias), book_key, collection, volume_id, alias),
        )
    con.execute("DELETE FROM citation_format_profiles WHERE volume_id = ?", (volume_id,))
    for item in profile.get("formats") or []:
        profile_key = _fingerprint([volume_id, item.get("section_key"), item])
        con.execute(
            """INSERT INTO citation_format_profiles(
                profile_key, collection, volume_id, section_key,
                profile_json, profile_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                profile_key,
                collection,
                volume_id,
                str(item.get("section_key") or ""),
                _stable_json(item),
                str(profile["fingerprint"]),
            ),
        )
    con.execute("DELETE FROM citation_index_seeds WHERE volume_id = ?", (volume_id,))
    for seed in profile.get("seeds") or []:
        con.execute(
            """INSERT INTO citation_index_seeds(
                seed_key, volume_id, section_key, entry_key,
                scripture_ref_order, book_raw, book_key,
                chapter_start, verse_start, chapter_end, verse_end,
                cited_pages_json, source_payload, source_fingerprint, seed_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                seed["seed_key"],
                seed["volume_id"],
                seed["section_key"],
                seed["entry_key"],
                seed["scripture_ref_order"],
                seed["book_raw"],
                seed["book_key"],
                seed["chapter_start"],
                seed["verse_start"],
                seed["chapter_end"],
                seed["verse_end"],
                _stable_json(seed["cited_pages_json"]),
                seed["source_payload"],
                seed["source_fingerprint"],
                seed["seed_status"],
            ),
        )


def _write_scan_result(con: sqlite3.Connection, result: Mapping[str, Any]) -> int:
    con.execute("DELETE FROM citation_files WHERE file_path = ?", (result["file_path"],))
    cursor = con.execute(
        """INSERT INTO citation_files(
            volume_id, file_path, source_relpath, fascicle_key,
            physical_file_seq, physical_index, file_size, file_mtime_ns,
            file_sha256, parser_status, page_type, is_index_source,
            detector_version, profile_fingerprint, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result["volume_id"],
            result["file_path"],
            result["source_relpath"],
            result["fascicle_key"],
            result["physical_file_seq"],
            result["physical_index"],
            result["file_size"],
            result["file_mtime_ns"],
            result["file_sha256"],
            result["parser_status"],
            result["page_type"],
            result["is_index_source"],
            result["detector_version"],
            result["profile_fingerprint"],
            result["scanned_at"],
        ),
    )
    file_id = int(cursor.lastrowid)
    for page in result.get("pages") or []:
        con.execute(
            """INSERT INTO citation_file_pages(
                file_id, editorial_page, page_side, confidence,
                evidence_source, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                file_id,
                page["editorial_page"],
                page["page_side"],
                page["confidence"],
                page["evidence_source"],
                _stable_json(page["evidence_json"]),
            ),
        )
    occurrence_count = 0
    for group in result.get("groups") or []:
        cursor = con.execute(
            """INSERT INTO citation_groups(
                group_key, file_id, block_index, block_type, block_script,
                bbox, source_start, source_end, citation_raw, snippet_raw,
                context_kind, page_side, detector_rule, confidence, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                group["group_key"],
                file_id,
                group["block_index"],
                group["block_type"],
                group["block_script"],
                group["bbox"],
                group["source_start"],
                group["source_end"],
                group["citation_raw"],
                group["snippet_raw"],
                group["context_kind"],
                group["page_side"],
                group["detector_rule"],
                group["confidence"],
                _stable_json(group["evidence_json"]),
            ),
        )
        group_id = int(cursor.lastrowid)
        for occurrence in group.get("occurrences") or []:
            con.execute(
                """INSERT INTO citation_occurrences(
                    occurrence_key, group_id, item_order, book_raw, book_key,
                    historical_book_key, ref_raw, ref_norm, chapter_start,
                    verse_start, chapter_end, verse_end, is_range, open_ended,
                    normalization_status, confidence, book_search, ref_search,
                    snippet_search, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    occurrence["occurrence_key"],
                    group_id,
                    occurrence["item_order"],
                    occurrence["book_raw"],
                    occurrence["book_key"],
                    occurrence["historical_book_key"],
                    occurrence["ref_raw"],
                    occurrence["ref_norm"],
                    occurrence["chapter_start"],
                    occurrence["verse_start"],
                    occurrence["chapter_end"],
                    occurrence["verse_end"],
                    occurrence["is_range"],
                    occurrence["open_ended"],
                    occurrence["normalization_status"],
                    occurrence["confidence"],
                    _fold_search(
                        " ".join(
                            value
                            for value in (
                                occurrence["book_raw"],
                                occurrence["book_key"] or "",
                                canonical_book_label(occurrence["book_key"]) or "",
                            )
                            if value
                        )
                    ),
                    _fold_search(
                        occurrence["ref_norm"] or occurrence["ref_raw"]
                    ),
                    _fold_search(group["snippet_raw"]),
                    _stable_json(occurrence["raw_json"]),
                ),
            )
            occurrence_count += 1
    return occurrence_count


def _refresh_seed_links(con: sqlite3.Connection, volume_id: str) -> int:
    con.execute(
        """DELETE FROM citation_seed_links
        WHERE seed_key IN (
            SELECT seed_key FROM citation_index_seeds WHERE volume_id = ?
        )""",
        (volume_id,),
    )
    rows = con.execute(
        """SELECT s.seed_key, o.occurrence_key,
                  CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM citation_file_pages fp
                        WHERE fp.file_id = f.file_id
                          AND fp.editorial_page IN (
                            SELECT CAST(value AS INTEGER)
                            FROM json_each(s.cited_pages_json)
                          )
                    ) THEN 0.99
                    ELSE 0.82
                  END AS score,
                  CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM citation_file_pages fp
                        WHERE fp.file_id = f.file_id
                          AND fp.editorial_page IN (
                            SELECT CAST(value AS INTEGER)
                            FROM json_each(s.cited_pages_json)
                          )
                    ) THEN 'canonical_ref_and_editorial_page'
                    ELSE 'canonical_ref'
                  END AS match_kind
        FROM citation_index_seeds s
        JOIN citation_occurrences o
          ON o.book_key = s.book_key
         AND o.chapter_start = s.chapter_start
         AND (
             s.verse_start IS NULL
             OR o.verse_start = s.verse_start
         )
        JOIN citation_groups g ON g.group_id = o.group_id
        JOIN citation_files f ON f.file_id = g.file_id
        WHERE s.volume_id = ?
          AND f.volume_id = s.volume_id
          AND f.is_index_source = 0
          AND s.seed_status = 'valid'""",
        (volume_id,),
    ).fetchall()
    for row in rows:
        con.execute(
            """INSERT OR REPLACE INTO citation_seed_links(
                seed_key, occurrence_key, match_kind, score, evidence_json
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                row["seed_key"],
                row["occurrence_key"],
                row["match_kind"],
                row["score"],
                _stable_json({"kind": row["match_kind"]}),
            ),
        )
    return len(rows)


def _unchanged_file(
    row: sqlite3.Row | None,
    *,
    path: Path,
    profile_fingerprint: str,
) -> bool:
    if row is None:
        return False
    stat = path.stat()
    return (
        int(row["file_size"]) == stat.st_size
        and int(row["file_mtime_ns"]) == stat.st_mtime_ns
        and int(row["detector_version"]) == DETECTOR_VERSION
        and str(row["profile_fingerprint"]) == profile_fingerprint
    )


def _scan_tasks(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    profile: Mapping[str, Any],
    estimator_pages: Mapping[str, tuple[dict[str, Any], ...]],
    con: sqlite3.Connection,
    force: bool,
) -> tuple[list[ScanTask], int]:
    files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    existing = {
        str(row["file_path"]): row
        for row in con.execute(
            """SELECT file_path, file_size, file_mtime_ns,
                      detector_version, profile_fingerprint
            FROM citation_files WHERE volume_id = ?""",
            (volume_id,),
        )
    }
    current_paths = {str(path.resolve()) for path in files}
    for stored_path in sorted(set(existing) - current_paths):
        con.execute("DELETE FROM citation_files WHERE file_path = ?", (stored_path,))
    tasks: list[ScanTask] = []
    skipped = 0
    observed = tuple(tuple(item) for item in profile.get("observed_aliases") or [])
    for physical_index, path in enumerate(files):
        resolved = str(path.resolve())
        if not force and _unchanged_file(
            existing.get(resolved),
            path=path,
            profile_fingerprint=str(profile["fingerprint"]),
        ):
            skipped += 1
            continue
        tasks.append(
            ScanTask(
                volume_id=volume_id,
                collection=collection,
                source_root=str(source_root.resolve()),
                file_path=resolved,
                physical_index=physical_index,
                is_index_source=_is_index_file(path, profile.get("index_ranges") or []),
                observed_aliases=observed,
                estimator_pages=estimator_pages.get(resolved, ()),
                profile_fingerprint=str(profile["fingerprint"]),
            )
        )
    return tasks, skipped


def _iter_results(
    tasks: Sequence[ScanTask],
    *,
    workers: int,
    chunksize: int,
) -> Iterator[dict[str, Any]]:
    if workers <= 1:
        for task in tasks:
            yield scan_file_task(task)
        return
    with ProcessPoolExecutor(max_workers=workers) as executor:
        yield from executor.map(
            scan_file_task,
            tasks,
            chunksize=max(1, chunksize),
        )


def discover_volume_roots(
    *,
    corpus_root: Path = PROJECT_ROOT / "teste",
    volume_ids: Sequence[str] | None = None,
    collections: Sequence[str] | None = None,
) -> list[tuple[str, str, Path]]:
    requested = {value.strip() for value in volume_ids or [] if value.strip()}
    collection_filter = {
        value.strip().upper() for value in collections or [] if value.strip()
    }
    results = []
    for volume_dir in sorted(corpus_root.iterdir() if corpus_root.is_dir() else []):
        match = re.match(r"^(PG|PL|PO)\d", volume_dir.name)
        if not match:
            continue
        collection = match.group(1)
        if requested and volume_dir.name not in requested:
            continue
        if collection_filter and collection not in collection_filter:
            continue
        source_root = volume_dir / "text"
        if source_root.is_dir():
            results.append((volume_dir.name, collection, source_root.resolve()))
    missing = requested - {item[0] for item in results}
    if missing:
        raise FileNotFoundError(f"volume source roots not found: {sorted(missing)}")
    return results


def build_citation_database(
    *,
    db_path: Path = DEFAULT_CITATION_DB,
    volumes: Sequence[tuple[str, str, Path]],
    workers: int | None = None,
    batch_size: int = 64,
    chunksize: int = 4,
    force: bool = False,
    payload_dir: Path = DEFAULT_PAYLOAD_DIR,
) -> dict[str, Any]:
    worker_count = (
        max(1, int(workers))
        if workers is not None
        else min(8, max(1, (os.cpu_count() or 1) - 1))
    )
    if worker_count > MAX_SCAN_WORKERS:
        raise ValueError(
            f"workers must be between 1 and {MAX_SCAN_WORKERS}; got {worker_count}"
        )
    con = connect_citation_db(db_path)
    init_citation_schema(con)
    scope = {
        "volumes": [volume_id for volume_id, _, _ in volumes],
        "force": force,
    }
    run_cursor = con.execute(
        """INSERT INTO citation_runs(
            started_at, status, scope_json, workers, detector_version
        ) VALUES (?, 'running', ?, ?, ?)""",
        (_now_iso(), _stable_json(scope), worker_count, DETECTOR_VERSION),
    )
    run_id = int(run_cursor.lastrowid)
    con.commit()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "detector_version": DETECTOR_VERSION,
        "db": str(db_path.resolve()),
        "run_id": run_id,
        "workers": worker_count,
        "volume_count": len(volumes),
        "file_count": 0,
        "scanned_file_count": 0,
        "skipped_file_count": 0,
        "occurrence_count": 0,
        "seed_link_count": 0,
        "volumes": [],
    }
    try:
        for volume_id, collection, source_root in volumes:
            profile = _load_payload_profile(
                volume_id,
                collection,
                payload_dir,
            )
            con.execute(
                """INSERT INTO citation_volumes(
                    volume_id, collection, source_root, scan_status,
                    profile_fingerprint, last_run_id, updated_at
                ) VALUES (?, ?, ?, 'partial', ?, ?, ?)
                ON CONFLICT(volume_id) DO UPDATE SET
                    collection = excluded.collection,
                    source_root = excluded.source_root,
                    scan_status = 'partial',
                    profile_fingerprint = excluded.profile_fingerprint,
                    last_run_id = excluded.last_run_id,
                    updated_at = excluded.updated_at""",
                (
                    volume_id,
                    collection,
                    str(source_root),
                    profile["fingerprint"],
                    run_id,
                    _now_iso(),
                ),
            )
            _write_profile_rows(
                con,
                volume_id=volume_id,
                collection=collection,
                profile=profile,
            )
            con.commit()
            estimator_pages = _estimator_pages_by_file(
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
            )
            tasks, skipped = _scan_tasks(
                volume_id=volume_id,
                collection=collection,
                source_root=source_root,
                profile=profile,
                estimator_pages=estimator_pages,
                con=con,
                force=force,
            )
            file_count = len(list(source_root.glob("*.txt")))
            volume_occurrences = 0
            for result_index, result in enumerate(
                _iter_results(
                    tasks,
                    workers=worker_count,
                    chunksize=chunksize,
                ),
                start=1,
            ):
                volume_occurrences += _write_scan_result(con, result)
                if result_index % max(1, batch_size) == 0:
                    con.commit()
            con.commit()
            seed_links = _refresh_seed_links(con, volume_id)
            con.execute(
                """UPDATE citation_volumes
                SET scan_status = 'complete', updated_at = ?
                WHERE volume_id = ?""",
                (_now_iso(), volume_id),
            )
            con.commit()
            volume_summary = {
                "volume_id": volume_id,
                "collection": collection,
                "file_count": file_count,
                "scanned_file_count": len(tasks),
                "skipped_file_count": skipped,
                "occurrence_count": volume_occurrences,
                "seed_count": len(profile.get("seeds") or []),
                "seed_link_count": seed_links,
            }
            summary["volumes"].append(volume_summary)
            summary["file_count"] += file_count
            summary["scanned_file_count"] += len(tasks)
            summary["skipped_file_count"] += skipped
            summary["occurrence_count"] += volume_occurrences
            summary["seed_link_count"] += seed_links
        con.execute(
            """UPDATE citation_runs
            SET finished_at = ?, status = 'completed', file_count = ?,
                scanned_file_count = ?, skipped_file_count = ?,
                occurrence_count = ?
            WHERE run_id = ?""",
            (
                _now_iso(),
                summary["file_count"],
                summary["scanned_file_count"],
                summary["skipped_file_count"],
                summary["occurrence_count"],
                run_id,
            ),
        )
        con.commit()
    except BaseException as exc:
        con.rollback()
        con.execute(
            """UPDATE citation_runs
            SET finished_at = ?, status = 'failed', error_json = ?
            WHERE run_id = ?""",
            (
                _now_iso(),
                _stable_json(
                    {"type": type(exc).__name__, "message": str(exc)}
                ),
                run_id,
            ),
        )
        con.commit()
        raise
    finally:
        con.close()
    return summary


def citation_database_report(
    db_path: Path = DEFAULT_CITATION_DB,
) -> dict[str, Any]:
    with connect_citation_db(db_path) as con:
        init_citation_schema(con)
        by_collection = [
            dict(row)
            for row in con.execute(
                """SELECT f.collection, COUNT(DISTINCT f.volume_id) AS volumes,
                          COUNT(DISTINCT cf.file_id) AS files,
                          COUNT(o.occurrence_id) AS occurrences
                FROM citation_volumes f
                LEFT JOIN citation_files cf ON cf.volume_id = f.volume_id
                LEFT JOIN citation_groups g ON g.file_id = cf.file_id
                LEFT JOIN citation_occurrences o ON o.group_id = g.group_id
                GROUP BY f.collection ORDER BY f.collection"""
            )
        ]
        by_book = [
            dict(row)
            for row in con.execute(
                """SELECT COALESCE(book_key, '[ambiguous]') AS book_key,
                          COUNT(*) AS occurrences
                FROM citation_occurrences
                GROUP BY COALESCE(book_key, '[ambiguous]')
                ORDER BY occurrences DESC, book_key"""
            )
        ]
        by_context = [
            dict(row)
            for row in con.execute(
                """SELECT context_kind, COUNT(*) AS occurrences
                FROM citation_occurrences o
                JOIN citation_groups g ON g.group_id = o.group_id
                GROUP BY context_kind
                ORDER BY occurrences DESC, context_kind"""
            )
        ]
        seed_status = [
            dict(row)
            for row in con.execute(
                """SELECT s.seed_status,
                          COUNT(DISTINCT s.seed_key) AS seeds,
                          COUNT(DISTINCT l.seed_key) AS linked
                FROM citation_index_seeds s
                LEFT JOIN citation_seed_links l ON l.seed_key = s.seed_key
                GROUP BY s.seed_status ORDER BY s.seed_status"""
            )
        ]
        formats = [
            {
                **dict(row),
                "profile": json.loads(row["profile_json"]),
            }
            for row in con.execute(
                """SELECT collection, volume_id, section_key, profile_json
                FROM citation_format_profiles
                ORDER BY collection, volume_id, section_key"""
            )
        ]
        detector_versions = [
            dict(row)
            for row in con.execute(
                """SELECT detector_version,
                          COUNT(*) AS files,
                          COUNT(DISTINCT volume_id) AS volumes
                FROM citation_files
                GROUP BY detector_version
                ORDER BY detector_version"""
            )
        ]
        outdated_detector_file_count = int(
            con.execute(
                """SELECT COUNT(*) FROM citation_files
                WHERE detector_version != ?""",
                (DETECTOR_VERSION,),
            ).fetchone()[0]
        )
        outdated_detector_volume_count = int(
            con.execute(
                """SELECT COUNT(DISTINCT volume_id) FROM citation_files
                WHERE detector_version != ?""",
                (DETECTOR_VERSION,),
            ).fetchone()[0]
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "detector_version": DETECTOR_VERSION,
            "generated_at": _now_iso(),
            "db": str(db_path.resolve()),
            "by_collection": by_collection,
            "by_book": by_book,
            "by_context": by_context,
            "seed_status": seed_status,
            "format_profiles": formats,
            "stored_detector_versions": detector_versions,
            "outdated_detector_file_count": outdated_detector_file_count,
            "outdated_detector_volume_count": outdated_detector_volume_count,
        }


def _query_side_clause(alias: str = "g") -> str:
    return (
        f"({alias}.page_side IN ('both', 'unknown') "
        f"OR {alias}.page_side = fp.page_side "
        f"OR fp.page_side IN ('single', 'unknown'))"
    )


def search_citations(
    *,
    db_path: Path = DEFAULT_CITATION_DB,
    volume_id: str,
    editorial_page: int | None = None,
    physical_page: int | None = None,
    fascicle: str | None = None,
    book: str | None = None,
    reference: str | None = None,
    text_query: str | None = None,
    context: str | None = None,
    include_index: bool = False,
    include_ambiguous: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if (editorial_page is None) == (physical_page is None):
        raise ValueError(
            "provide exactly one of editorial_page or physical_page"
        )
    params: list[Any] = [volume_id]
    joins = [
        "JOIN citation_groups g ON g.group_id = o.group_id",
        "JOIN citation_files f ON f.file_id = g.file_id",
    ]
    where = ["f.volume_id = ?"]
    page_projection = """
        SELECT file_id, editorial_page,
               CASE
                 WHEN MIN(page_side) = MAX(page_side) THEN MIN(page_side)
                 ELSE 'unknown'
               END AS page_side,
               MAX(confidence) AS confidence
        FROM citation_file_pages
        GROUP BY file_id, editorial_page
    """
    if editorial_page is not None:
        joins.append(
            f"""JOIN ({page_projection}) fp
            ON fp.file_id = f.file_id"""
        )
        where.append("fp.editorial_page = ?")
        where.append(_query_side_clause())
        params.append(editorial_page)
    else:
        joins.append(
            f"""LEFT JOIN ({page_projection}) fp
            ON fp.file_id = f.file_id
            AND fp.editorial_page = (
                SELECT MIN(fp_min.editorial_page)
                FROM citation_file_pages fp_min
                WHERE fp_min.file_id = f.file_id
            )"""
        )
        where.append("f.physical_file_seq = ?")
        params.append(physical_page)
    if fascicle:
        where.append("f.fascicle_key = ?")
        params.append(fascicle)
    if book:
        book_key = canonical_book_key(book)
        if book_key:
            where.append("o.book_key = ?")
            params.append(book_key)
        else:
            where.append("o.book_search LIKE ?")
            params.append(f"%{normalize_book_alias(book)}%")
    if reference:
        where.append("o.ref_search LIKE ?")
        params.append(f"%{normalize_book_alias(reference)}%")
    if context:
        where.append("g.context_kind = ?")
        params.append(context)
    if not include_index:
        where.append("f.is_index_source = 0")
        where.append("g.context_kind != 'index'")
    if not include_ambiguous:
        where.append("o.normalization_status = 'normalized'")
    if text_query:
        joins.append(
            """JOIN citation_occurrences_fts fts
            ON fts.rowid = o.occurrence_id"""
        )
        where.append("citation_occurrences_fts MATCH ?")
        params.append(text_query)
    params.append(max(1, limit))
    sql = f"""
        SELECT o.occurrence_key, f.volume_id, f.file_path,
               f.source_relpath, f.fascicle_key, f.physical_file_seq,
               f.physical_index, fp.editorial_page,
               fp.page_side AS editorial_page_side,
               fp.confidence AS editorial_page_confidence,
               g.block_index, g.block_type, g.block_script, g.bbox,
               g.source_start, g.source_end, g.context_kind, g.page_side,
               g.detector_rule, g.snippet_raw, g.citation_raw,
               o.book_raw, o.book_key, o.historical_book_key,
               o.ref_raw, o.ref_norm, o.chapter_start, o.verse_start,
               o.chapter_end, o.verse_end, o.is_range, o.open_ended,
               o.normalization_status, o.confidence,
               EXISTS (
                   SELECT 1 FROM citation_seed_links sl
                   WHERE sl.occurrence_key = o.occurrence_key
               ) AS linked_to_index
        FROM citation_occurrences o
        {' '.join(joins)}
        WHERE {' AND '.join(where)}
        ORDER BY linked_to_index DESC,
                 CASE g.context_kind
                   WHEN 'critical_apparatus' THEN 0
                   WHEN 'note' THEN 1
                   WHEN 'body' THEN 2
                   ELSE 3
                 END,
                 o.confidence DESC,
                 f.physical_file_seq,
                 g.block_index,
                 g.source_start,
                 o.item_order
        LIMIT ?
    """
    with connect_citation_db(db_path) as con:
        return [dict(row) for row in con.execute(sql, params)]


def _path_in_index_range(
    path: Path,
    *,
    start: str | None,
    end: str | None,
) -> bool:
    if not start or not end:
        return False
    target = path.resolve()
    boundaries = [Path(start).expanduser().resolve(), Path(end).expanduser().resolve()]
    if target in boundaries:
        return True
    target_seq = page_number(target)
    start_seq = page_number(boundaries[0])
    end_seq = page_number(boundaries[1])
    return (
        target_seq is not None
        and start_seq is not None
        and end_seq is not None
        and min(start_seq, end_seq) <= target_seq <= max(start_seq, end_seq)
    )


def enrich_locator_items_from_citation_db(
    locator_items: Sequence[Mapping[str, Any]],
    *,
    db_path: Path = DEFAULT_CITATION_DB,
    source_root: Path,
    max_candidates: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from copy import deepcopy

    items = [deepcopy(dict(item)) for item in locator_items]
    volume_id = source_root.parent.name
    if not db_path.is_file():
        return items, {
            "schema_version": 1,
            "stage": "persistent_scripture_evidence",
            "coverage_status": "missing",
            "db": str(db_path),
            "volume_id": volume_id,
            "items": [],
            "table_repair_groups": [],
        }
    with connect_citation_db(db_path) as con:
        volume = con.execute(
            """SELECT scan_status, last_run_id, profile_fingerprint, source_root
            FROM citation_volumes WHERE volume_id = ?""",
            (volume_id,),
        ).fetchone()
        if volume is None or volume["scan_status"] != "complete":
            return items, {
                "schema_version": 1,
                "stage": "persistent_scripture_evidence",
                "coverage_status": "partial" if volume else "missing",
                "db": str(db_path),
                "volume_id": volume_id,
                "items": [],
                "table_repair_groups": [],
            }
        stored_files = con.execute(
            """SELECT file_path, file_size, file_mtime_ns, detector_version,
                      profile_fingerprint
            FROM citation_files
            WHERE volume_id=?""",
            (volume_id,),
        ).fetchall()
        source_matches = (
            Path(str(volume["source_root"])).resolve() == source_root.resolve()
        )
        current_files = {
            str(path.resolve()): path
            for path in source_root.glob("*.txt")
            if path.is_file()
        }
        stored_paths = {str(Path(str(row["file_path"])).resolve()) for row in stored_files}
        stale_files = 0
        for row in stored_files:
            path = Path(str(row["file_path"]))
            try:
                stat = path.stat()
            except OSError:
                stale_files += 1
                continue
            if (
                int(row["file_size"]) != stat.st_size
                or int(row["file_mtime_ns"]) != stat.st_mtime_ns
                or int(row["detector_version"]) != DETECTOR_VERSION
                or str(row["profile_fingerprint"]) != str(volume["profile_fingerprint"])
            ):
                stale_files += 1
        if (
            not source_matches
            or stored_paths != set(current_files)
            or stale_files
        ):
            return items, {
                "schema_version": 1,
                "stage": "persistent_scripture_evidence",
                "coverage_status": "stale",
                "db": str(db_path),
                "volume_id": volume_id,
                "stored_file_count": len(stored_files),
                "current_file_count": len(current_files),
                "stale_file_count": stale_files,
                "source_root_matches": source_matches,
                "items": [],
                "table_repair_groups": [],
            }
        artifacts = []
        for item in items:
            scripture = item.get("scripture_ref")
            if not isinstance(scripture, Mapping):
                continue
            book_key = str(scripture.get("book_key") or "").strip()
            if not book_key:
                collection = volume_id[:2]
                tradition = "vulgate_migne" if collection in {"PG", "PL"} else None
                book_key = canonical_book_key(
                    scripture.get("book_norm") or scripture.get("book_raw"),
                    tradition=tradition,
                ) or ""
            chapter = scripture.get("chapter_start")
            verse = scripture.get("verse_start")
            if not book_key or not isinstance(chapter, int):
                continue
            cited_pages = {
                int(value)
                for value in item.get("cited_pages") or []
                if isinstance(value, int) and 0 < value < 10000
            }
            params: list[Any] = [volume_id, book_key, chapter]
            joins = [
                "JOIN citation_groups g ON g.group_id = o.group_id",
                "JOIN citation_files f ON f.file_id = g.file_id",
            ]
            conditions = [
                "f.volume_id = ?",
                "o.book_key = ?",
                "o.chapter_start = ?",
                "f.is_index_source = 0",
                "g.context_kind != 'index'",
            ]
            if isinstance(verse, int):
                conditions.append("o.verse_start = ?")
                params.append(verse)
            if cited_pages:
                joins.append(
                    "JOIN citation_file_pages fp ON fp.file_id = f.file_id"
                )
                placeholders = ",".join("?" for _ in cited_pages)
                conditions.append(f"fp.editorial_page IN ({placeholders})")
                params.extend(sorted(cited_pages))
            else:
                joins.append(
                    "LEFT JOIN citation_file_pages fp ON fp.file_id = f.file_id"
                )
            candidate_fascicles = {
                _fascicle_key(Path(str(candidate["file"])))
                for candidate in item.get("candidates") or []
                if isinstance(candidate, Mapping)
                and candidate.get("file")
                and (
                    not cited_pages
                    or candidate.get("matched_page") in cited_pages
                    or candidate.get("inferred_printed_page") in cited_pages
                )
            }
            if volume_id.startswith("PO") and len(candidate_fascicles) == 1:
                conditions.append("f.fascicle_key = ?")
                params.append(next(iter(candidate_fascicles)))
            rows = con.execute(
                f"""SELECT o.occurrence_key, f.file_path,
                           f.physical_file_seq, g.context_kind,
                           g.snippet_raw, o.ref_norm, o.confidence,
                           fp.editorial_page, fp.confidence AS page_confidence,
                           EXISTS (
                               SELECT 1 FROM citation_seed_links sl
                               WHERE sl.occurrence_key = o.occurrence_key
                           ) AS linked_to_index
                    FROM citation_occurrences o
                    {' '.join(joins)}
                    WHERE {' AND '.join(conditions)}
                    ORDER BY linked_to_index DESC,
                             CASE g.context_kind
                               WHEN 'critical_apparatus' THEN 0
                               WHEN 'note' THEN 1
                               ELSE 2
                             END,
                             o.confidence DESC,
                             f.physical_file_seq
                    LIMIT ?""",
                (*params, max(12, max_candidates * 4)),
            ).fetchall()
            best_by_file: dict[str, dict[str, Any]] = {}
            excluded = 0
            for row in rows:
                file_path = Path(str(row["file_path"]))
                if _path_in_index_range(
                    file_path,
                    start=str(item.get("section_file_start") or "") or None,
                    end=str(item.get("section_file_end") or "") or None,
                ):
                    excluded += 1
                    continue
                page_match = (
                    row["editorial_page"] in cited_pages
                    if cited_pages and row["editorial_page"] is not None
                    else False
                )
                probability = float(row["confidence"])
                evidence = [
                    {
                        "kind": "persistent_scripture_occurrence",
                        "raw": row["snippet_raw"],
                        "weight": round(probability, 4),
                        "occurrence_key": row["occurrence_key"],
                    }
                ]
                if page_match:
                    probability = min(0.995, probability + 0.12)
                    evidence.append(
                        {
                            "kind": "editorial_header_match",
                            "raw": f"printed page {row['editorial_page']}",
                            "weight": 0.12,
                        }
                    )
                if row["context_kind"] == "critical_apparatus":
                    evidence.append(
                        {
                            "kind": "critical_apparatus_context",
                            "raw": "stored OCR block classification",
                            "weight": 0.08,
                        }
                    )
                candidate = {
                    "file": str(file_path),
                    "probability": round(probability, 4),
                    "candidate_role": "persistent_scripture_evidence",
                    "reason_summary": (
                        "stored exact book/chapter/verse occurrence"
                        + (" on cited editorial page" if page_match else "")
                        + (
                            " in critical apparatus"
                            if row["context_kind"] == "critical_apparatus"
                            else ""
                        )
                    ),
                    "evidence": evidence,
                }
                existing = best_by_file.get(str(file_path))
                if existing is None or candidate["probability"] > existing["probability"]:
                    best_by_file[str(file_path)] = candidate
            persisted = sorted(
                best_by_file.values(),
                key=lambda candidate: (
                    -float(candidate["probability"]),
                    str(candidate["file"]),
                ),
            )[:max_candidates]
            combined = {
                str(candidate.get("file")): deepcopy(dict(candidate))
                for candidate in item.get("candidates") or []
                if isinstance(candidate, Mapping) and candidate.get("file")
            }
            for candidate in persisted:
                existing = combined.get(candidate["file"])
                if existing is None or float(candidate["probability"]) > float(
                    existing.get("probability") or -1
                ):
                    combined[candidate["file"]] = candidate
            item["candidates"] = sorted(
                combined.values(),
                key=lambda candidate: (
                    -float(candidate.get("probability") or -1),
                    str(candidate.get("file") or ""),
                ),
            )[:max_candidates]
            artifacts.append(
                {
                    "locator_key": item.get("locator_key"),
                    "candidate_count": len(persisted),
                    "excluded_index_hit_count": excluded,
                }
            )
        return items, {
            "schema_version": 1,
            "stage": "persistent_scripture_evidence",
            "coverage_status": "complete",
            "db": str(db_path.resolve()),
            "volume_id": volume_id,
            "run_id": volume["last_run_id"],
            "profile_fingerprint": volume["profile_fingerprint"],
            "scripture_locator_count": len(artifacts),
            "matched_reference_count": sum(
                int(item["candidate_count"] > 0) for item in artifacts
            ),
            "files_read": 0,
            "citation_format_profiles": {},
            "table_repair_groups": [],
            "items": artifacts,
        }


__all__ = [
    "DEFAULT_CITATION_DB",
    "DETECTOR_VERSION",
    "SCHEMA_VERSION",
    "ScanTask",
    "build_citation_database",
    "citation_database_report",
    "citation_logical_text",
    "connect_citation_db",
    "discover_volume_roots",
    "enrich_locator_items_from_citation_db",
    "init_citation_schema",
    "scan_file_task",
    "search_citations",
]

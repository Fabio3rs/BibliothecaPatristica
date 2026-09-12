"""Deterministic, volume-local evidence for locating biblical citations.

The semantic extractor remains authoritative.  This module consumes its compact
locator items, scans each OCR file at most once, and adds bounded candidates for
the LLM locator to verify.  It never creates or resolves semantic records.
"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .book_catalog import (
    aliases_for_book,
    canonical_book_key,
    normalize_book_alias,
)


ReadText = Callable[[Path], str]


@dataclass(frozen=True)
class ScriptureEvidenceConfig:
    max_candidates: int = 4
    snippet_chars: int = 220
    apparatus_context_chars: int = 360
    max_table_suggestions: int = 8
    max_table_repair_lines_per_section: int = 60
    ocr_fuzzy_book_threshold: float = 0.78


@dataclass(frozen=True)
class LogicalText:
    text: str
    source_offsets: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if not self.source_offsets or end <= start:
            return 0, 0
        bounded_start = min(max(0, start), len(self.source_offsets) - 1)
        bounded_end = min(max(bounded_start + 1, end), len(self.source_offsets))
        return (
            self.source_offsets[bounded_start],
            self.source_offsets[bounded_end - 1] + 1,
        )


def _collection_profile(collection: Any) -> str:
    normalized = str(collection or "").strip().upper()
    if normalized in {"PG", "PL"}:
        return "migne_patrologia"
    if normalized == "PO":
        return "section_local_multilingual"
    return "volume_local"


_APPARATUS_HEADING_RE = re.compile(
    r"(?i)\b(?:apparat(?:us|o|um)?|aparato(?:[_\s]+critico)?"
    r"|apparatus\s+criticus|notae?\s+critic"
    r"|variantia|variae?\s+lectiones|textual\s+notes?)\b"
)
_APPARATUS_SIGNAL_RE = re.compile(
    r"(?i)(?:\b(?:codd?|codices|mss?|manuscr|lectio|variant|omitt?|addit?)\b"
    r"|\[[^\]\n]{1,100}\]|[†‡※])"
)
_APPARATUS_TAG_RE = re.compile(
    r"(?is)(?:<apparat(?:us)?\b[^>]*>.*?</apparat(?:us)?>"
    r"|\[apparat(?:us)?\].*?\[/apparat(?:us)?\]"
    r"|<bloco\b[^>]*\btipo\s*=\s*[\"'](?:aparato[_\s]+critico|apparatus)[\"'][^>]*>"
    r".*?</bloco>)"
)
_PHYSICAL_SUFFIX_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
_HEADER_BLOCK_RE = re.compile(
    r"(?is)<bloco\b[^>]*\btipo\s*=\s*[\"']cabecalho[\"'][^>]*>(.*?)</bloco>"
)
_ARABIC_RE = re.compile(r"\d{1,4}")
_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_OCR_CITATION_LOCATOR_RE = re.compile(
    r"(?<!\w)(?P<chapter>[0-9oil|sb]{1,3}|[ivxlcdm]{1,10})"
    r"\s*[,.:]\s*"
    r"(?P<verse>[0-9oil|sb]{1,3})(?!\w)",
    re.IGNORECASE,
)
_OCR_NUMBER_TRANSLATION = str.maketrans(
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


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    plain = plain.casefold().replace("ſ", "s")
    plain = re.sub(r"[^\w\s]+", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _fold_for_scan(text: str) -> str:
    """Accent-insensitive text whose character offsets remain stable."""

    normalized = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    return plain.lower().replace("ſ", "s")


def _join_soft_wrapped_lines(text: str) -> str:
    return _logical_text_with_offsets(text).text


def _logical_text_with_offsets(text: str) -> LogicalText:
    source = str(text or "")
    wrap_pattern = re.compile(
        r"(?P<alpha>(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_]))"
        r"|(?P<numeric>(?<=\d)-\s*\n\s*(?=\d))",
        re.UNICODE,
    )
    parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for match in wrap_pattern.finditer(source):
        unchanged = source[cursor : match.start()]
        parts.append(unchanged)
        offsets.extend(range(cursor, match.start()))
        if match.lastgroup == "numeric":
            parts.append("-")
            offsets.append(match.start())
        cursor = match.end()
    parts.append(source[cursor:])
    offsets.extend(range(cursor, len(source)))
    return LogicalText("".join(parts), tuple(offsets))


def _canonical_book_key(
    book_norm: Any,
    book_raw: Any,
    *,
    collection_profile: str | None = None,
) -> str:
    for value in (book_norm, book_raw):
        key = canonical_book_key(value, tradition=collection_profile)
        if key:
            return key
    return ""


def _aliases_for_book(key: str, *raw_values: Any) -> set[str]:
    return aliases_for_book(key, *raw_values)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _physical_number(path: Path | str | None) -> int | None:
    match = _PHYSICAL_SUFFIX_RE.search(Path(str(path or "")).name)
    return int(match.group(1)) if match else None


def _physical_sort_key(path: Path) -> tuple[int, int, str]:
    number = _physical_number(path)
    return (number is None, number or 0, str(path))


def _path_in_section(path: Path, item: Mapping[str, Any]) -> bool:
    values = [
        str(item.get(field) or "").strip()
        for field in ("section_file_start", "section_file_end")
    ]
    boundaries = [Path(value).expanduser().resolve() for value in values if value]
    target = path.expanduser().resolve()
    if target in boundaries:
        return True
    if any(boundary.name == target.name for boundary in boundaries):
        return True
    if len(boundaries) != 2:
        return False
    numbers = (_physical_number(target), *(_physical_number(item) for item in boundaries))
    if any(number is None for number in numbers):
        return False
    target_number, start_number, end_number = (int(number) for number in numbers)
    lower, upper = sorted((start_number, end_number))
    return lower <= target_number <= upper


def _compile_book_pattern(
    refs_by_book: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    alias_to_book: dict[str, str] = {}
    for key, refs in refs_by_book.items():
        for ref in refs:
            for alias in _aliases_for_book(key, ref.get("book_norm"), ref.get("book_raw")):
                alias_to_book.setdefault(alias, key)
    if not alias_to_book:
        return None, {}
    alternatives = sorted(alias_to_book, key=lambda value: (-len(value), value))
    alias_pattern = "|".join(
        re.escape(alias).replace(r"\ ", r"\s+") for alias in alternatives
    )
    citation = re.compile(
        rf"(?<!\w)(?P<book>{alias_pattern})\s*[.,:]?\s*"
        rf"(?P<chapter>\d{{1,3}}|[ivxlcdm]{{1,10}})"
        rf"\s*(?:[,.:]\s*|\s+)(?P<verse>\d{{1,3}})"
        rf"(?:\s*[-–—]\s*(?P<verse_end>\d{{1,3}}))?",
        re.IGNORECASE,
    )
    return citation, alias_to_book


def _roman_to_int(value: str) -> int | None:
    text = value.casefold()
    if not _ROMAN_RE.fullmatch(text):
        return _positive_int(text)
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for char in reversed(text):
        current = values[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total or None


def _ocr_locator_values(value: str, *, allow_roman: bool) -> set[int]:
    raw = str(value or "").strip()
    values: set[int] = set()
    if allow_roman and _ROMAN_RE.fullmatch(raw):
        roman = _roman_to_int(raw)
        if roman is not None:
            values.add(roman)
    translated = raw.translate(_OCR_NUMBER_TRANSLATION)
    if translated.isdigit():
        numeric = _positive_int(translated)
        if numeric is not None:
            values.add(numeric)
    return values


def _best_book_similarity(context: str, aliases: Sequence[str]) -> float:
    folded_context = _fold(context)
    context_tokens = folded_context.split()
    if not context_tokens:
        return 0.0
    best = 0.0
    for raw_alias in aliases:
        alias = _fold(raw_alias)
        if len(alias) < 3:
            continue
        if alias in folded_context:
            return 1.0
        alias_tokens = alias.split()
        if len(alias_tokens) == 1 and len(alias) < 5:
            continue
        for width in {
            max(1, len(alias_tokens) - 1),
            len(alias_tokens),
            len(alias_tokens) + 1,
        }:
            if width > len(context_tokens):
                continue
            for start in range(len(context_tokens) - width + 1):
                candidate = " ".join(context_tokens[start : start + width])
                if abs(len(candidate) - len(alias)) > max(3, len(alias) // 3):
                    continue
                best = max(
                    best,
                    SequenceMatcher(None, alias, candidate, autojunk=False).ratio(),
                )
    return best


def _ocr_fuzzy_scripture_match(
    text: str,
    *,
    aliases: Sequence[str],
    chapter: int,
    verse: int,
    threshold: float,
    snippet_chars: int,
) -> dict[str, Any] | None:
    """Find one OCR-tolerant citation near an expected editorial page.

    This deliberately runs only on page candidates already selected by printed
    pagination or the estimator. It is not a corpus-wide fuzzy search.
    """

    logical = _logical_text_with_offsets(text)
    folded = _fold_for_scan(logical.text)
    best: tuple[float, re.Match[str]] | None = None
    for match in _OCR_CITATION_LOCATOR_RE.finditer(folded):
        found_chapters = _ocr_locator_values(
            match.group("chapter"),
            allow_roman=True,
        )
        found_verses = _ocr_locator_values(
            match.group("verse"),
            allow_roman=False,
        )
        if chapter not in found_chapters or verse not in found_verses:
            continue
        context_start = max(0, match.start() - 90)
        similarity = _best_book_similarity(
            logical.text[context_start : match.start()],
            aliases,
        )
        if similarity < threshold:
            continue
        if best is None or similarity > best[0]:
            best = (similarity, match)
    if best is None:
        return None
    similarity, match = best
    source_start, source_end = logical.source_span(match.start(), match.end())
    return {
        "similarity": round(similarity, 4),
        "source_start": source_start,
        "source_end": source_end,
        "snippet": _compact_snippet(
            text,
            source_start,
            source_end,
            snippet_chars,
        ),
    }


def _compact_snippet(text: str, start: int, end: int, limit: int) -> str:
    half = max(20, limit // 2)
    lower = max(0, start - half)
    upper = min(len(text), end + half)
    snippet = re.sub(r"\s+", " ", text[lower:upper]).strip()
    if len(snippet) > limit:
        snippet = snippet[: max(1, limit - 1)].rstrip() + "…"
    return snippet


def _editorial_header_pages(text: str) -> dict[int, str]:
    blocks = _HEADER_BLOCK_RE.findall(text[:4000])
    header_chunks = (
        blocks[:3]
        if blocks
        else ["\n".join(text[:500].splitlines()[:3])]
    )
    confirmed: dict[int, str] = {}
    for chunk in header_chunks:
        for value in re.findall(r"[\[(]\s*(\d{1,4})\s*[\])]", chunk):
            page = int(value)
            if 0 < page < 10000:
                confirmed[page] = "bracketed header page"
        for value in re.findall(
            r"(?i)\b(?:pag(?:e|ina)?|p\.)\s*(\d{1,4})\b",
            chunk,
        ):
            page = int(value)
            if 0 < page < 10000:
                confirmed[page] = "explicit page label in header"
        for raw_line in chunk.splitlines():
            line = re.sub(r"<[^>]+>", " ", raw_line).strip()
            match = re.match(r"^(\d{1,4})\b.*\b(\d{1,4})$", line)
            if not match:
                continue
            left, right = (int(match.group(1)), int(match.group(2)))
            if 0 < left < 10000 and right == left + 1:
                confirmed[left] = "consecutive facing-page header"
                confirmed[right] = "consecutive facing-page header"
    return confirmed


def _apparatus_context(text: str, start: int, end: int, window: int) -> tuple[bool, str]:
    lower = max(0, start - window)
    upper = min(len(text), end + window)
    context = text[lower:upper]
    tagged = any(match.start() <= start <= match.end() for match in _APPARATUS_TAG_RE.finditer(text))
    heading = bool(_APPARATUS_HEADING_RE.search(context))
    signals = len(_APPARATUS_SIGNAL_RE.findall(context))
    is_apparatus = tagged or heading or signals >= 2
    if tagged:
        return True, "explicit apparatus block/tag"
    if heading:
        return True, "apparatus/variant heading near citation"
    if signals >= 2:
        return True, f"{signals} critical-apparatus signals near citation"
    return False, ""


def _scripture_refs_from_items(
    locator_items: Sequence[Mapping[str, Any]],
    *,
    collection_profile: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, int], dict[str, Any]]]:
    by_book: dict[str, list[dict[str, Any]]] = {}
    by_locator: dict[tuple[str, int], dict[str, Any]] = {}
    for raw_item in locator_items:
        scripture = raw_item.get("scripture_ref")
        if not isinstance(scripture, Mapping):
            continue
        chapter = _positive_int(scripture.get("chapter_start"))
        verse = _positive_int(scripture.get("verse_start"))
        if chapter is None or verse is None:
            continue
        book_key = _canonical_book_key(
            scripture.get("book_norm"),
            scripture.get("book_raw"),
            collection_profile=collection_profile,
        )
        if not book_key:
            continue
        normalized = {
            **dict(scripture),
            "book_key": book_key,
            "chapter": chapter,
            "verse": verse,
        }
        locator_pair = (
            str(raw_item.get("entry_key") or ""),
            int(raw_item.get("ref_order") or 0),
        )
        by_locator[locator_pair] = normalized
        by_book.setdefault(book_key, []).append(normalized)
    return by_book, by_locator


def infer_citation_format_profiles(
    semantic_payload: Mapping[str, Any],
    *,
    collection: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Infer small, section-local table grammars from the extracted JSON."""

    volume = semantic_payload.get("volume")
    volume_collection = (
        volume.get("collection") if isinstance(volume, Mapping) else None
    )
    profile_kind = _collection_profile(collection or volume_collection)
    entries = {
        str(entry.get("entry_key") or ""): entry
        for entry in semantic_payload.get("entries") or []
        if isinstance(entry, Mapping)
    }
    section_samples: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for scripture in semantic_payload.get("scripture_refs") or []:
        if not isinstance(scripture, Mapping):
            continue
        entry = entries.get(str(scripture.get("entry_key") or ""))
        if entry is None:
            continue
        section_key = str(entry.get("section_key") or "")
        section_samples.setdefault(section_key, []).append((scripture, entry))

    profiles: dict[str, dict[str, Any]] = {}
    for section_key, samples in sorted(section_samples.items()):
        aliases = sorted(
            {
                str(scripture.get("book_raw") or "").strip()
                for scripture, _ in samples
                if str(scripture.get("book_raw") or "").strip()
            },
            key=lambda value: (_fold(value), value),
        )
        separators = sorted(
            {
                token
                for scripture, entry in samples
                for token in re.findall(
                    r"[:,;.]|[-–—]|\t|\|",
                    " ".join(
                        str(value or "")
                        for value in (
                            scripture.get("ref_raw"),
                            entry.get("entry_raw"),
                        )
                    ),
                )
            }
        )
        inherited_count = sum(
            1
            for scripture, entry in samples
            if str(scripture.get("book_raw") or "").strip()
            and _fold(str(scripture.get("book_raw") or ""))
            not in _fold(str(entry.get("entry_raw") or ""))
        )
        raw_entries = [str(entry.get("entry_raw") or "") for _, entry in samples]
        column_style = (
            "tab"
            if any("\t" in value for value in raw_entries)
            else "pipe"
            if any("|" in value for value in raw_entries)
            else "spaced"
            if any(re.search(r"\S {2,}\S", value) for value in raw_entries)
            else "linear"
        )
        has_ranges = any(
            scripture.get("chapter_end") is not None
            or scripture.get("verse_end") is not None
            for scripture, _ in samples
        )
        profiles[section_key] = {
            "schema_version": 1,
            "section_key": section_key,
            "collection_profile": profile_kind,
            "pattern_reuse_scope": (
                "migne_volume"
                if profile_kind == "migne_patrologia"
                else "section_only"
                if profile_kind == "section_local_multilingual"
                else "volume"
            ),
            "observed_book_aliases": aliases[:24],
            "field_order": [
                "book_or_inherited_heading",
                "chapter",
                "verse_or_range",
                "editorial_pages",
            ],
            "separators": separators[:12],
            "inherits_book_heading": inherited_count > 0,
            "column_style": column_style,
            "supports_ranges": has_ranges,
            "sample_count": len(samples),
        }
    return profiles


def build_scripture_table_repair_prompt(
    *,
    volume_id: str,
    section_profile: Mapping[str, Any],
    unresolved_lines: Sequence[Mapping[str, Any]],
    max_lines: int = 20,
    max_line_chars: int = 320,
) -> str:
    """Build a micro-prompt only for table rows the regex could not close."""

    import json

    if max_lines < 1 or max_line_chars < 40:
        raise ValueError("repair prompt limits must be positive")
    compact_lines = []
    for index, record in enumerate(unresolved_lines[:max_lines]):
        raw_line = str(record.get("raw_line") or "").rstrip("\r\n")
        partial = record.get("partial_json")
        if not isinstance(partial, Mapping):
            partial = {}
        compact_lines.append(
            {
                "line_id": str(record.get("line_id") or f"line-{index + 1:04d}"),
                "raw_line": raw_line[:max_line_chars],
                "partial_json": {
                    key: deepcopy(partial.get(key))
                    for key in ("entry_key", "entry", "scripture_refs", "refs")
                    if key in partial
                },
            }
        )
    profile_json = json.dumps(
        dict(section_profile), ensure_ascii=False, separators=(",", ":")
    )
    lines_json = json.dumps(compact_lines, ensure_ascii=False, separators=(",", ":"))
    return f"""$alphabetical-index-extractor

TASK
Repair only the unresolved scripture-table rows below for {volume_id}. Apply this section's
observed citation format; do not inspect or reproduce whole OCR pages.

CITATION_FORMAT_PROFILE
{profile_json}

UNRESOLVED_ROWS
{lines_json}

OUTPUT CONTRACT
- Return one compact JSON object with `schema_version: 1` and a `results` array.
- Emit exactly one result per `line_id`, and no additional entries.
- Each result may contain only `line_id`, `status`, `scripture_refs`, `refs`, and `reason`.
- `scripture_refs` contains only passage objects justified by that raw line/profile.
- `refs` contains only printed editorial-page objects justified by that raw line/profile.
- Preserve uncertain OCR in raw fields; never invent a book, chapter, verse, or page.
- Use `status: "unresolved"` with a short reason when ambiguity remains.
"""


def _section_source_files(
    files: Sequence[Path],
    locator_items: Sequence[Mapping[str, Any]],
) -> dict[str, set[Path]]:
    result: dict[str, set[Path]] = {}
    for item in locator_items:
        section_key = str(item.get("section_key") or "")
        section_files = result.setdefault(section_key, set())
        for path in files:
            if _path_in_section(path, item):
                section_files.add(path.resolve())
    return result


def _split_layout_columns(
    raw_line: str,
    profile: Mapping[str, Any],
) -> list[tuple[int, str]]:
    style = str(profile.get("column_style") or "linear")
    if style == "pipe":
        parts = raw_line.split("|")
    elif style == "tab":
        parts = raw_line.split("\t")
    elif style == "spaced":
        parts = re.split(r" {2,}", raw_line)
    else:
        parts = [raw_line]
    return [(index, part) for index, part in enumerate(parts)]


def _heading_state(
    cell: str,
    *,
    heading_patterns: Mapping[str, re.Pattern[str]],
) -> tuple[str, int | None] | None:
    plain = re.sub(r"<[^>]+>", " ", cell)
    folded = _fold_for_scan(plain).strip(" \t.,:;|()[]")
    if not folded or len(folded) > 120 or re.search(r"\d", folded):
        return None
    matches = [
        (book_key, match)
        for book_key, pattern in heading_patterns.items()
        if (match := pattern.search(folded)) is not None
    ]
    if len(matches) != 1:
        return None
    book_key, match = matches[0]
    residue = (folded[: match.start()] + " " + folded[match.end() :]).strip()
    if residue and not re.fullmatch(
        r"(?:(?:ex|in|ad|epistola|liber|caput|psalmo|psalmus)\s+)*[ivxlcdm]*",
        residue,
        re.IGNORECASE,
    ):
        return None
    default_chapter = None
    if book_key == "salmos":
        chapter_match = re.search(
            r"(?i)\b(?:psalm\w*|salmo|psaume)\s+([ivxlcdm]{1,8})\b",
            folded,
        )
        if chapter_match:
            default_chapter = _roman_to_int(chapter_match.group(1))
    return book_key, default_chapter


def _table_observations(
    *,
    file_texts: Mapping[Path, str],
    section_files: Mapping[str, set[Path]],
    refs_by_section_book: Mapping[
        str, Mapping[str, Sequence[Mapping[str, Any]]]
    ],
    profiles: Mapping[str, Mapping[str, Any]],
    config: ScriptureEvidenceConfig,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    bare_passage = re.compile(
        r"(?<!\d)(?P<chapter>\d{1,3}|[ivxlcdm]{1,10})"
        r"\s*(?:[,.:]\s*|\s+)(?P<verse>\d{1,3})(?!\d)"
        r"(?:\s*[-–—]\s*\d{1,3})?",
        re.IGNORECASE,
    )
    verse_only = re.compile(
        r"(?i)(?:^|\s)v(?:ers(?:e|et|iculo)?)?\s*\.?\s*"
        r"(?P<verse>\d{1,3})(?!\d)(?:\s*[-–—]\s*\d{1,3})?"
    )
    for section_key, index_files in sorted(section_files.items()):
        refs_by_book = refs_by_section_book.get(section_key, {})
        citation_pattern, alias_to_book = _compile_book_pattern(refs_by_book)
        if citation_pattern is None:
            continue
        profile = profiles.get(section_key, {})
        reuse_scope = str(profile.get("pattern_reuse_scope") or "section_only")
        if (
            str(profile.get("collection_profile") or "")
            == "section_local_multilingual"
            or reuse_scope
            not in {"section_only", "migne_volume", "volume"}
        ):
            reuse_scope = "section_only"
        observed_keys = {
            key
            for value in profile.get("observed_book_aliases") or []
            if (
                key := canonical_book_key(
                    value,
                    tradition=str(profile.get("collection_profile") or ""),
                )
            )
        }
        heading_books = {
            key: refs
            for key, refs in refs_by_book.items()
            if reuse_scope != "section_only" or not observed_keys or key in observed_keys
        }
        heading_patterns = {
            book_key: re.compile(
                r"(?<!\w)(?:"
                + "|".join(
                    re.escape(alias).replace(r"\ ", r"\s+")
                    for alias in sorted(
                        {
                            alias
                            for ref in refs
                            for alias in _aliases_for_book(
                                book_key,
                                ref.get("book_norm"),
                                ref.get("book_raw"),
                            )
                            if len(alias) >= 3
                        },
                        key=lambda value: (-len(value), value),
                    )
                )
                + r")(?!\w)",
                re.IGNORECASE,
            )
            for book_key, refs in heading_books.items()
        }
        anchored = {
            (book, int(ref["chapter"]), int(ref["verse"]))
            for book, refs in refs_by_book.items()
            for ref in refs
        }
        inherit_heading = bool(profile.get("inherits_book_heading", True))
        state_by_column: dict[int, tuple[str, int | None]] = {}
        for path in sorted(index_files, key=_physical_sort_key):
            logical_text = _join_soft_wrapped_lines(file_texts.get(path, ""))
            for raw_line in logical_text.splitlines():
                for column, cell in _split_layout_columns(raw_line, profile):
                    heading = _heading_state(cell, heading_patterns=heading_patterns)
                    if heading is not None:
                        state_by_column[column] = heading
                    folded_cell = _fold_for_scan(cell)
                    matches: list[tuple[str, re.Match[str], int, int]] = []
                    explicit_spans: list[tuple[int, int]] = []
                    for match in citation_pattern.finditer(folded_cell):
                        book_key = alias_to_book.get(normalize_book_alias(match.group("book")))
                        chapter = _roman_to_int(match.group("chapter"))
                        verse = _positive_int(match.group("verse"))
                        if book_key and chapter and verse:
                            matches.append((book_key, match, chapter, verse))
                            explicit_spans.append(match.span())
                    if inherit_heading and column in state_by_column:
                        book_key, default_chapter = state_by_column[column]
                        for match in bare_passage.finditer(folded_cell):
                            if any(start <= match.start() < end for start, end in explicit_spans):
                                continue
                            chapter = _roman_to_int(match.group("chapter"))
                            verse = _positive_int(match.group("verse"))
                            if chapter and verse:
                                matches.append((book_key, match, chapter, verse))
                        if default_chapter:
                            for match in verse_only.finditer(folded_cell):
                                verse = _positive_int(match.group("verse"))
                                if verse:
                                    matches.append(
                                        (book_key, match, default_chapter, verse)
                                    )
                    for book_key, match, chapter, verse in matches:
                        if (book_key, chapter, verse) not in anchored:
                            continue
                        tail = folded_cell[match.end() :]
                        pages = [
                            int(value)
                            for value in _ARABIC_RE.findall(tail)
                            if 0 < int(value) < 10000
                        ][: config.max_table_suggestions]
                        observations.append(
                            {
                                "section_key": section_key,
                                "book_key": book_key,
                                "chapter": chapter,
                                "verse": verse,
                                "file": str(path),
                                "suggested_editorial_pages": list(
                                    dict.fromkeys(pages)
                                ),
                                "evidence": raw_line[: config.snippet_chars],
                            }
                        )
    return observations


def add_scripture_evidence_candidates(
    locator_items: Sequence[Mapping[str, Any]],
    *,
    source_root: Path,
    collection: str | None = None,
    config: ScriptureEvidenceConfig | None = None,
    citation_format_profiles: Mapping[str, Mapping[str, Any]] | None = None,
    read_text: ReadText | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add bounded scripture-regex candidates without resolving any locator.

    The function reads every ``*.txt`` below one volume root exactly once.  It
    builds an inverted index of citation hits, then performs O(items + hits)
    joins instead of O(items * files) searches.
    """

    settings = config or ScriptureEvidenceConfig()
    if settings.max_candidates < 1 or settings.snippet_chars < 40:
        raise ValueError("scripture evidence limits must be positive and bounded")
    root = source_root.expanduser().resolve()
    reader = read_text or (lambda path: path.read_text(encoding="utf-8", errors="replace"))
    normalized_items = [deepcopy(dict(item)) for item in locator_items]
    profiles = {
        str(key): deepcopy(dict(value))
        for key, value in (citation_format_profiles or {}).items()
    }
    profile_kind = _collection_profile(collection)
    refs_by_book, ref_by_locator = _scripture_refs_from_items(
        normalized_items,
        collection_profile=profile_kind,
    )
    if not ref_by_locator:
        return normalized_items, {
            "schema_version": 1,
            "stage": "scripture_evidence",
            "source_root": str(root),
            "collection": str(collection or ""),
            "collection_profile": profile_kind,
            "files_read": 0,
            "scripture_locator_count": 0,
            "matched_reference_count": 0,
            "index_source_file_count": 0,
            "citation_format_profiles": profiles,
            "table_repair_groups": [],
            "items": [],
        }
    citation_pattern, alias_to_book = _compile_book_pattern(refs_by_book)
    files = sorted(
        (path.resolve() for path in root.rglob("*.txt") if path.is_file()),
        key=lambda path: str(path),
    )
    file_texts = {path: reader(path) for path in files}
    header_pages_by_file = {
        path: _editorial_header_pages(text)
        for path, text in file_texts.items()
    }
    files_by_editorial_page: dict[int, set[Path]] = {}
    for path, header_pages in header_pages_by_file.items():
        for page in header_pages:
            files_by_editorial_page.setdefault(page, set()).add(path)
    section_files = _section_source_files(files, normalized_items)
    index_files = {
        path
        for paths in section_files.values()
        for path in paths
    }
    refs_by_section_book: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in normalized_items:
        locator_pair = (
            str(item.get("entry_key") or ""),
            int(item.get("ref_order") or 0),
        )
        scripture = ref_by_locator.get(locator_pair)
        if scripture is None:
            continue
        section_key = str(item.get("section_key") or "")
        refs_by_section_book.setdefault(section_key, {}).setdefault(
            str(scripture["book_key"]),
            [],
        ).append(scripture)
    hits: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    excluded_index_hits: dict[tuple[str, int, int], int] = {}
    anchored_references = {
        (book, int(ref["chapter"]), int(ref["verse"]))
        for book, refs in refs_by_book.items()
        for ref in refs
    }
    if citation_pattern is not None:
        for path, text in file_texts.items():
            logical = _logical_text_with_offsets(text)
            folded = _fold_for_scan(logical.text)
            for match in citation_pattern.finditer(folded):
                book_key = alias_to_book.get(normalize_book_alias(match.group("book")))
                chapter = _roman_to_int(match.group("chapter"))
                verse = _positive_int(match.group("verse"))
                if not book_key or chapter is None or verse is None:
                    continue
                key = (book_key, chapter, verse)
                if key not in anchored_references:
                    continue
                if path in index_files:
                    excluded_index_hits[key] = excluded_index_hits.get(key, 0) + 1
                    continue
                source_start, source_end = logical.source_span(
                    match.start(),
                    match.end(),
                )
                apparatus, apparatus_reason = _apparatus_context(
                    text,
                    source_start,
                    source_end,
                    settings.apparatus_context_chars,
                )
                probability = 0.82 if apparatus else 0.66
                evidence = [
                    {
                        "kind": "scripture_regex",
                        "raw": _compact_snippet(
                            text,
                            source_start,
                            source_end,
                            settings.snippet_chars,
                        ),
                        "weight": 0.66,
                    }
                ]
                if apparatus:
                    evidence.append(
                        {
                            "kind": "critical_apparatus_context",
                            "raw": apparatus_reason,
                            "weight": 0.16,
                        }
                    )
                hits.setdefault(key, []).append(
                    {
                        "file": str(path),
                        "probability": probability,
                        "candidate_role": "scripture_body_regex",
                        "reason_summary": (
                            "exact book/chapter/verse hit in critical apparatus"
                            if apparatus
                            else "exact book/chapter/verse hit"
                        ),
                        "evidence": evidence,
                    }
                )

    table_observations = _table_observations(
        file_texts=file_texts,
        section_files=section_files,
        refs_by_section_book=refs_by_section_book,
        profiles=profiles,
        config=settings,
    )
    table_by_ref: dict[tuple[str, str, int, int], list[dict[str, Any]]] = {}
    for observation in table_observations:
        key = (
            str(observation["section_key"]),
            str(observation["book_key"]),
            int(observation["chapter"]),
            int(observation["verse"]),
        )
        table_by_ref.setdefault(key, []).append(observation)

    item_artifacts: list[dict[str, Any]] = []
    unresolved_by_section: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for item in normalized_items:
        locator_pair = (str(item.get("entry_key") or ""), int(item.get("ref_order") or 0))
        scripture = ref_by_locator.get(locator_pair)
        if scripture is None:
            continue
        reference_key = (
            str(scripture["book_key"]),
            int(scripture["chapter"]),
            int(scripture["verse"]),
        )
        section_key = str(item.get("section_key") or "")
        cited_pages = {
            int(value)
            for value in item.get("cited_pages") or []
            if _positive_int(value) is not None
        }
        page_candidate_paths = {
            path
            for page in cited_pages
            for path in files_by_editorial_page.get(page, set())
        }
        for raw_candidate in item.get("candidates") or []:
            if not isinstance(raw_candidate, Mapping) or not raw_candidate.get("file"):
                continue
            candidate_path = Path(str(raw_candidate["file"])).expanduser()
            if not candidate_path.is_absolute():
                candidate_path = root / candidate_path
            candidate_path = candidate_path.resolve()
            if candidate_path in file_texts:
                page_candidate_paths.add(candidate_path)
        best_by_file: dict[str, dict[str, Any]] = {}
        for candidate in hits.get(reference_key, []):
            candidate_path = Path(str(candidate["file"]))
            enriched_candidate = deepcopy(candidate)
            matched_headers = sorted(
                cited_pages
                & set(header_pages_by_file.get(candidate_path.resolve(), {}))
            )
            if matched_headers:
                enriched_candidate["probability"] = min(
                    0.98,
                    float(enriched_candidate["probability"]) + 0.14,
                )
                enriched_candidate["reason_summary"] += (
                    f"; editorial header confirms {matched_headers}"
                )
                enriched_candidate.setdefault("evidence", []).append(
                    {
                        "kind": "editorial_header_match",
                        "raw": f"printed page(s) {matched_headers}",
                        "weight": 0.14,
                    }
                )
            existing = best_by_file.get(str(candidate["file"]))
            if (
                existing is None
                or float(enriched_candidate["probability"])
                > float(existing["probability"])
            ):
                best_by_file[str(candidate["file"])] = enriched_candidate

        aliases = sorted(
            _aliases_for_book(
                str(scripture["book_key"]),
                scripture.get("book_norm"),
                scripture.get("book_raw"),
            ),
            key=lambda value: (-len(value), value),
        )
        for candidate_path in sorted(page_candidate_paths, key=_physical_sort_key):
            if candidate_path in index_files or str(candidate_path) in best_by_file:
                continue
            fuzzy = _ocr_fuzzy_scripture_match(
                file_texts.get(candidate_path, ""),
                aliases=aliases,
                chapter=int(scripture["chapter"]),
                verse=int(scripture["verse"]),
                threshold=settings.ocr_fuzzy_book_threshold,
                snippet_chars=settings.snippet_chars,
            )
            if fuzzy is None:
                continue
            matched_headers = sorted(
                cited_pages & set(header_pages_by_file.get(candidate_path, {}))
            )
            probability = 0.68
            evidence = [
                {
                    "kind": "scripture_ocr_fuzzy",
                    "raw": fuzzy["snippet"],
                    "weight": round(0.48 + 0.18 * float(fuzzy["similarity"]), 4),
                    "similarity": fuzzy["similarity"],
                }
            ]
            reason = (
                "OCR-tolerant book/chapter/verse hit on an editorial-page candidate"
            )
            if matched_headers:
                probability += 0.18
                reason += f"; editorial header confirms {matched_headers}"
                evidence.append(
                    {
                        "kind": "editorial_header_match",
                        "raw": f"printed page(s) {matched_headers}",
                        "weight": 0.18,
                    }
                )
            apparatus, apparatus_reason = _apparatus_context(
                file_texts.get(candidate_path, ""),
                int(fuzzy["source_start"]),
                int(fuzzy["source_end"]),
                settings.apparatus_context_chars,
            )
            if apparatus:
                probability += 0.08
                reason += "; critical-apparatus context"
                evidence.append(
                    {
                        "kind": "critical_apparatus_context",
                        "raw": apparatus_reason,
                        "weight": 0.08,
                    }
                )
            best_by_file[str(candidate_path)] = {
                "file": str(candidate_path),
                "probability": min(0.94, probability),
                "candidate_role": "scripture_editorial_ocr_fuzzy",
                "reason_summary": reason,
                "evidence": evidence,
            }
        regex_candidates = sorted(
            best_by_file.values(),
            key=lambda candidate: (
                Path(str(candidate["file"])).resolve() not in page_candidate_paths,
                -float(candidate["probability"]),
                str(candidate["file"]),
            ),
        )[: settings.max_candidates]

        combined: dict[str, dict[str, Any]] = {}
        excluded_existing_candidates = 0
        for candidate in item.get("candidates") or []:
            if not isinstance(candidate, Mapping) or not candidate.get("file"):
                continue
            candidate_path = Path(str(candidate["file"])).expanduser()
            if not candidate_path.is_absolute():
                candidate_path = root / candidate_path
            candidate_path = candidate_path.resolve()
            if candidate_path in index_files:
                excluded_existing_candidates += 1
                continue
            combined[str(candidate.get("file"))] = deepcopy(dict(candidate))
        for candidate in regex_candidates:
            file_key = str(candidate["file"])
            previous = combined.get(file_key)
            if previous is None:
                combined[file_key] = candidate
                continue
            previous_probability = float(previous.get("probability") or -1.0)
            candidate_probability = float(candidate.get("probability") or -1.0)
            winner = candidate if candidate_probability > previous_probability else previous
            merged_evidence = []
            seen_evidence: set[tuple[str, str]] = set()
            for evidence_item in [
                *(previous.get("evidence") or []),
                *(candidate.get("evidence") or []),
            ]:
                if not isinstance(evidence_item, Mapping):
                    continue
                evidence_key = (
                    str(evidence_item.get("kind") or ""),
                    str(
                        evidence_item.get("raw")
                        or evidence_item.get("detail")
                        or ""
                    ),
                )
                if evidence_key in seen_evidence:
                    continue
                seen_evidence.add(evidence_key)
                merged_evidence.append(deepcopy(dict(evidence_item)))
            combined[file_key] = {
                **deepcopy(dict(winner)),
                "probability": max(previous_probability, candidate_probability),
                # Keep both the pre-existing editorial-page evidence and the
                # newly discovered scripture-content evidence.  Six records
                # were insufficient once the deterministic text locator began
                # contributing its own auditable signals first.
                "evidence": merged_evidence[:12],
            }
        item["candidates"] = sorted(
            combined.values(),
            key=lambda candidate: (
                -float(candidate.get("probability") or -1.0),
                str(candidate.get("file") or ""),
            ),
        )[: settings.max_candidates]

        table_suggestions = []
        table_key = (section_key, *reference_key)
        for observation in table_by_ref.get(table_key, []):
            suggested = set(observation["suggested_editorial_pages"])
            table_suggestions.append(
                {
                    "file": observation["file"],
                    "suggested_editorial_pages": observation["suggested_editorial_pages"],
                    "agreement": (
                        "matches_existing"
                        if cited_pages and cited_pages & suggested
                        else "differs_or_unanchored"
                    ),
                    "evidence": observation["evidence"],
                }
            )
        if table_suggestions:
            item["table_reference_suggestions"] = table_suggestions[
                : settings.max_table_suggestions
            ]
        elif section_files.get(section_key):
            scripture_order = int(item.get("scripture_ref_order") or 0)
            unresolved_key = (str(item.get("entry_key") or ""), scripture_order)
            bucket = unresolved_by_section.setdefault(section_key, {})
            unresolved = bucket.setdefault(
                unresolved_key,
                {
                    "line_id": f"{unresolved_key[0]}::scripture:{scripture_order:06d}",
                    "raw_line": str(item.get("entry_excerpt") or "")[
                        : settings.snippet_chars
                    ],
                    "partial_json": {
                        "entry_key": unresolved_key[0],
                        "scripture_refs": [deepcopy(dict(item["scripture_ref"]))],
                        "refs": [],
                    },
                },
            )
            unresolved["partial_json"]["refs"].append(
                {
                    field: deepcopy(item.get(field))
                    for field in (
                        "ref_order",
                        "scripture_ref_order",
                        "ref_kind",
                        "ref_raw",
                        "page_ref_raw",
                        "page_ref_int",
                        "range_start_raw",
                        "range_end_raw",
                    )
                    if item.get(field) is not None
                }
            )
        item_artifacts.append(
            {
                "locator_key": item.get("locator_key"),
                "reference": {
                    "book_key": scripture["book_key"],
                    "chapter": scripture["chapter"],
                    "verse": scripture["verse"],
                },
                "candidate_count": len(regex_candidates),
                "excluded_index_hit_count": excluded_index_hits.get(
                    reference_key,
                    0,
                )
                + excluded_existing_candidates,
                "table_suggestions": table_suggestions[: settings.max_table_suggestions],
            }
        )

    table_repairs = []
    for section_key, unresolved in sorted(unresolved_by_section.items()):
        lines = list(unresolved.values())
        limit = settings.max_table_repair_lines_per_section
        table_repairs.append(
            {
                "section_key": section_key,
                "unresolved_line_count": len(lines),
                "truncated": len(lines) > limit,
                "unresolved_lines": lines[:limit],
            }
        )

    artifact = {
        "schema_version": 1,
        "stage": "scripture_evidence",
        "source_root": str(root),
        "collection": str(collection or ""),
        "collection_profile": profile_kind,
        "files_read": len(files),
        "scripture_locator_count": len(ref_by_locator),
        "matched_reference_count": len(hits),
        "index_source_file_count": len(index_files),
        "citation_format_profiles": profiles,
        "table_repair_groups": table_repairs,
        "items": item_artifacts,
    }
    return normalized_items, artifact


__all__ = [
    "ScriptureEvidenceConfig",
    "add_scripture_evidence_candidates",
    "build_scripture_table_repair_prompt",
    "infer_citation_format_profiles",
]

"""Deterministic reading hints for alphabetical-index OCR pages.

This module does not assemble canonical semantic entries.  It turns repetitive
editorial signals into a compact, auditable artifact so the semantic agent only
has to decide real segmentation and damaged/ambiguous readings.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import ahocorasick
except ImportError:  # pragma: no cover - requirements install it in production
    ahocorasick = None

from tools.corpus_utils import page_sort_key
from tools.scripture.book_catalog import BOOKS, normalize_book_alias


HEADING_RULES: tuple[tuple[str, str, str], ...] = (
    ("auctorum et operum", "stop_boundary", "editorial_closure"),
    ("conspectus tomi", "stop_boundary", "editorial_closure"),
    ("syllabus auctorum", "stop_boundary", "editorial_closure"),
    ("elenchus operum", "stop_boundary", "editorial_closure"),
    ("index capitum", "stop_boundary", "editorial_closure"),
    ("ordo operum", "stop_boundary", "editorial_closure"),
    ("table du tome", "stop_boundary", "editorial_closure"),
    ("table of contents", "stop_boundary", "editorial_closure"),
    ("index rerum et verborum", "owned_heading", "analytic_subject"),
    ("index analyticus", "owned_heading", "analytic_subject"),
    ("index analytique", "owned_heading", "analytic_subject"),
    ("index rerum", "owned_heading", "analytic_subject"),
    ("index alphabeticus", "owned_heading", "alphabetical_general"),
    ("table alphabetique", "owned_heading", "alphabetical_general"),
    ("index onomasticus", "owned_heading", "onomastic_mixed"),
    ("elenchus personarum", "owned_heading", "onomastic_person"),
    ("elenchus onomasticus localis", "owned_heading", "onomastic_place"),
    ("table des noms propres", "owned_heading", "onomastic_mixed"),
    ("index scriptorum", "owned_heading", "author_index"),
    ("index auctorum", "owned_heading", "author_index"),
    ("table des citations des peres", "owned_heading", "author_index"),
    ("index scripturae sacrae", "owned_heading", "scripture_index"),
    ("index locorum ex scriptura sacra", "owned_heading", "scripture_index"),
    ("table des citations de la bible", "owned_heading", "scripture_index"),
    ("table des citations bibliques", "owned_heading", "scripture_index"),
    ("index des citations des ecritures", "owned_heading", "scripture_index"),
    ("table des pericopes de l ecriture", "owned_heading", "pericope_index"),
    ("table des pericopes", "owned_heading", "pericope_index"),
    ("table de concordance", "owned_heading", "concordance_index"),
    ("index graecitatis", "owned_heading", "foreign_terms"),
    ("table des mots grecs", "owned_heading", "foreign_terms"),
    ("table des mots syriaques", "owned_heading", "foreign_terms"),
    ("index orationum", "owned_heading", "crosswalk_index"),
    ("ordo rerum", "stop_boundary", "ordo_rerum"),
    ("addenda et corrigenda", "stop_boundary", "editorial_closure"),
    ("addenda", "stop_boundary", "editorial_closure"),
    ("corrigenda", "stop_boundary", "editorial_closure"),
    ("errata", "stop_boundary", "editorial_closure"),
)

NOTATION_RULES: tuple[tuple[str, str], ...] = (
    ("ibidem", "ibid"),
    ("ibid", "ibid"),
    ("seqq", "seqq"),
    ("seq", "seq"),
    ("passim", "passim"),
    ("vide", "vide"),
    ("vid", "vide"),
    ("voir", "vide"),
    ("confer", "cf"),
    ("cf", "cf"),
    ("usque", "usque"),
    ("fin", "fin"),
)

_BLOCK_START_RE = re.compile(r"<bloco\b[^>]*\btipo=\"([^\"]+)\"[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_DOT_LEADER_RE = re.compile(r"(?:\.\s*){3,}")
_UPPER_GROUP_RE = re.compile(r"^[A-ZÆŒÀ-ÖØ-Þ][A-ZÆŒÀ-ÖØ-Þ\s.,'’\-]{1,80}$")
_ORDINAL_GROUP_RE = re.compile(r"^(?:[IVXLCDM]+|\d+)\.$", re.I)
_LETTER_GROUP_RE = re.compile(r"^[A-ZÆŒ]$", re.I)
_PAGE_LINE_RE = re.compile(
    r"(?<!\d)(?P<page>\d{2,4})\s*,\s*(?P<line>\d{1,3})"
    r"\s*(?P<open>seq\.?|seqq\.?|sqq\.?|l(?:in)?\.?\s*\d*)",
    re.I,
)
_RANGE_RE = re.compile(
    r"(?<![\w,])(?P<start>\d{1,4})\s*(?P<separator>[-–—]|a|à|ad|usque)\s*"
    r"(?P<end>\d{1,4})(?!\w)",
    re.I,
)
_NUMBER_RE = re.compile(r"(?<![\w,])(?P<page>\d{1,4})(?!\w)")
_IBID_RE = re.compile(r"\bibid(?:em)?\.?", re.I)
_OPEN_RE = re.compile(r"\b(?:seq|seqq|sqq)\.?\b", re.I)
_CROSS_REF_RE = re.compile(r"\b(?:vide|vid|voir|cf|confer)\.?\b", re.I)
_ROMAN = r"[ivxlcdm]+"
_SCRIPTURE_ROW_RE = re.compile(
    rf"^\s*(?:[-—]\s*)?(?P<chapter>{_ROMAN}|\d{{1,3}})\s*[,.:]\s*"
    rf"(?P<verse>\d{{1,3}})(?:\s*[-–—]\s*(?:(?P<chapter_end>{_ROMAN}|\d{{1,3}})\s*[,.:]\s*)?"
    r"(?P<verse_end>\d{1,3}))?",
    re.I,
)
_SOFT_WRAP_RE = re.compile(r"(?<=[^\W\d_])[-‐‑]\s*$", re.UNICODE)
_OCR_NUMBER_TOKEN_RE = re.compile(
    r"(?<!\w)([0-9OIl|SB]{1,4})(?!\w)",
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
_OCR_HEADING_TRANSLATION = str.maketrans(
    {"0": "O", "1": "I", "|": "I", "5": "S", "8": "B"}
)


def normalize_mechanical_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w\s]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _build_automaton(rules: Iterable[tuple[Any, ...]]) -> Any:
    records = list(rules)
    if ahocorasick is None:
        return records
    automaton = ahocorasick.Automaton()
    for index, record in enumerate(records):
        key = normalize_mechanical_text(record[0])
        automaton.add_word(key, (index, key, record))
    automaton.make_automaton()
    return automaton


_HEADING_AUTOMATON = _build_automaton(HEADING_RULES)
_NOTATION_AUTOMATON = _build_automaton(NOTATION_RULES)


def _aho_matches(text: str, automaton: Any) -> list[tuple[Any, ...]]:
    normalized = f" {normalize_mechanical_text(text)} "
    matches: list[tuple[int, tuple[Any, ...]]] = []
    if ahocorasick is None:
        for record in automaton:
            key = normalize_mechanical_text(record[0])
            match = re.search(rf"(?<!\w){re.escape(key)}(?!\w)", normalized)
            if match:
                matches.append((len(key), record))
    else:
        for end, (_index, key, record) in automaton.iter(normalized):
            start = end - len(key) + 1
            before = normalized[start - 1] if start > 0 else " "
            after = normalized[end + 1] if end + 1 < len(normalized) else " "
            if not before.isalnum() and before != "_" and not after.isalnum() and after != "_":
                matches.append((len(key), record))
    return [record for _length, record in sorted(matches, key=lambda item: -item[0])]


def detect_heading(text: str) -> dict[str, Any] | None:
    repaired = re.sub(
        r"(?<=[^\W\d_])[-‐‑]\s+(?=[^\W\d_])",
        "",
        text,
        flags=re.UNICODE,
    ).translate(_OCR_HEADING_TRANSLATION)
    matches = _aho_matches(repaired, _HEADING_AUTOMATON)
    if not matches:
        compact = normalize_mechanical_text(repaired).replace(" ", "")
        matches = [
            record
            for record in HEADING_RULES
            if len(normalize_mechanical_text(record[0])) >= 10
            and normalize_mechanical_text(record[0]).replace(" ", "") in compact
        ]
        matches.sort(
            key=lambda record: len(normalize_mechanical_text(record[0])),
            reverse=True,
        )
    if not matches:
        return None
    phrase, role, section_kind = matches[0]
    return {
        "phrase": phrase,
        "role": role,
        "section_kind": section_kind,
    }


def detect_notations(text: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for phrase, notation_key in _aho_matches(text, _NOTATION_AUTOMATON):
        if notation_key in seen:
            continue
        seen.add(notation_key)
        result.append({"notation_key": notation_key, "matched_phrase": phrase})
    return result


_BOOK_HEADING_ALIASES: dict[str, set[str]] = {}
for _book in BOOKS:
    for _alias in _book.aliases:
        _normalized_alias = normalize_book_alias(_alias)
        if len(_normalized_alias) >= 4:
            _BOOK_HEADING_ALIASES.setdefault(_normalized_alias, set()).add(_book.key)


def detect_scripture_book_heading(text: str) -> dict[str, Any] | None:
    normalized = normalize_book_alias(text.strip(" .:;()[]"))
    keys = sorted(_BOOK_HEADING_ALIASES.get(normalized, set()))
    if len(keys) != 1:
        return None
    return {"book_raw": text.strip(), "book_key_candidates": keys}


def _visible_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    block_type: str | None = None
    inside_notes = False
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if "<notas" in raw_line:
            inside_notes = True
        closes_notes = "</notas>" in raw_line
        start = _BLOCK_START_RE.search(raw_line)
        if start:
            block_type = start.group(1)
            raw_line = raw_line[start.end() :]
        closes_block = "</bloco>" in raw_line
        visible = _SPACE_RE.sub(" ", _TAG_RE.sub(" ", raw_line)).strip()
        if visible and not inside_notes and block_type not in {"rodape"}:
            records.append(
                {
                    "line": line_number,
                    "block_type": block_type,
                    "text": visible,
                }
            )
        if closes_block:
            block_type = None
        if closes_notes:
            inside_notes = False
    return records


def _right_hand_reference_zone(text: str) -> tuple[str, int]:
    leaders = list(_DOT_LEADER_RE.finditer(text))
    if leaders:
        last = leaders[-1]
        return text[last.end() :], last.end()
    matches = list(re.finditer(r"(?<!\w)(?:ibid(?:em)?\.?|\d{1,4})(?=[\s,.;\]\-–—]|$)", text, re.I))
    if not matches:
        matches = [
            match
            for match in _OCR_NUMBER_TOKEN_RE.finditer(text)
            if any(char.isdigit() for char in match.group(1))
        ]
    if not matches:
        return "", len(text)
    minimum_start = max(2, len(text) // 5)
    material_matches = [match for match in matches if match.start() >= minimum_start]
    if not material_matches:
        return "", len(text)
    start = material_matches[0].start()
    return text[start:], start


def extract_material_locators(text: str) -> list[dict[str, Any]]:
    if "|" in text:
        results: list[dict[str, Any]] = []
        offset = 0
        for cell in text.split("|"):
            for locator in extract_material_locators(cell):
                locator = dict(locator)
                locator["char_start"] += offset
                locator["char_end"] += offset
                results.append(locator)
            offset += len(cell) + 1
        return results
    zone, offset = _right_hand_reference_zone(text)
    if not zone:
        return []
    occupied: list[tuple[int, int]] = []
    results: list[dict[str, Any]] = []

    normalized_zone_chars = list(zone)
    for token_match in _OCR_NUMBER_TOKEN_RE.finditer(zone):
        raw_token = token_match.group(1)
        if not any(char.isdigit() for char in raw_token):
            continue
        normalized_token = raw_token.translate(_OCR_NUMBER_TRANSLATION)
        if not normalized_token.isdigit():
            continue
        normalized_zone_chars[token_match.start() : token_match.end()] = normalized_token
    normalized_zone = "".join(normalized_zone_chars)

    def overlaps(start: int, end: int) -> bool:
        return any(start < right and end > left for left, right in occupied)

    for match in _PAGE_LINE_RE.finditer(zone):
        page_value = int(match.group("page"))
        line_value = int(match.group("line"))
        if page_value < 1000 or line_value > 200:
            continue
        occupied.append(match.span())
        results.append(
            {
                "kind": "editorial_page_line",
                "raw": match.group(0),
                "page_ref_int": page_value,
                "line_ref_raw": match.group("line"),
                "open_ended": bool(match.group("open")),
                "char_start": offset + match.start(),
                "char_end": offset + match.end(),
            }
        )
    for match in _RANGE_RE.finditer(zone):
        if overlaps(*match.span()):
            continue
        occupied.append(match.span())
        results.append(
            {
                "kind": "editorial_range",
                "raw": match.group(0),
                "range_start_raw": match.group("start"),
                "range_end_raw": match.group("end"),
                "separator": match.group("separator"),
                "char_start": offset + match.start(),
                "char_end": offset + match.end(),
            }
        )
    if normalized_zone != zone:
        for match in _PAGE_LINE_RE.finditer(normalized_zone):
            if overlaps(*match.span()):
                continue
            page_value = int(match.group("page"))
            line_value = int(match.group("line"))
            if page_value < 1000 or line_value > 200:
                continue
            occupied.append(match.span())
            results.append(
                {
                    "kind": "editorial_page_line",
                    "raw": zone[match.start() : match.end()],
                    "page_ref_int": page_value,
                    "line_ref_raw": match.group("line"),
                    "open_ended": bool(match.group("open")),
                    "ocr_normalized": True,
                    "char_start": offset + match.start(),
                    "char_end": offset + match.end(),
                }
            )
        for match in _RANGE_RE.finditer(normalized_zone):
            if overlaps(*match.span()):
                continue
            occupied.append(match.span())
            results.append(
                {
                    "kind": "editorial_range",
                    "raw": zone[match.start() : match.end()],
                    "range_start_raw": match.group("start"),
                    "range_end_raw": match.group("end"),
                    "separator": match.group("separator"),
                    "ocr_normalized": True,
                    "char_start": offset + match.start(),
                    "char_end": offset + match.end(),
                }
            )
    for match in _NUMBER_RE.finditer(zone):
        if overlaps(*match.span()):
            continue
        occupied.append(match.span())
        record = {
            "kind": "editorial_page",
            "raw": match.group(0),
            "page_ref_int": int(match.group("page")),
            "char_start": offset + match.start(),
            "char_end": offset + match.end(),
        }
        suffix = zone[match.end() : match.end() + 12]
        if _OPEN_RE.search(suffix):
            record["open_ended"] = True
        results.append(record)
    if normalized_zone != zone:
        for match in _NUMBER_RE.finditer(normalized_zone):
            if overlaps(*match.span()):
                continue
            raw = zone[match.start() : match.end()]
            if not any(char.isdigit() for char in raw):
                continue
            occupied.append(match.span())
            results.append(
                {
                    "kind": "editorial_page",
                    "raw": raw,
                    "page_ref_int": int(match.group("page")),
                    "ocr_normalized": True,
                    "char_start": offset + match.start(),
                    "char_end": offset + match.end(),
                }
            )
    for match in _IBID_RE.finditer(zone):
        results.append(
            {
                "kind": "inherited_locator",
                "raw": match.group(0),
                "notation_key": "ibid",
                "char_start": offset + match.start(),
                "char_end": offset + match.end(),
            }
        )
    return sorted(results, key=lambda item: (item["char_start"], item["char_end"]))


def analyze_index_file(path: Path) -> dict[str, Any]:
    lines = _visible_lines(path)
    candidates: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        text = str(line["text"])
        heading = None
        heading_text = text
        heading_line_end = int(line["line"])
        for width in (1, 2, 3, 4, 5):
            window = lines[index : index + width]
            if len(window) != width:
                continue
            candidate_heading_text = " ".join(str(item["text"]) for item in window)
            candidate_heading = detect_heading(candidate_heading_text)
            if candidate_heading is None:
                continue
            if heading is None or len(str(candidate_heading["phrase"])) > len(
                str(heading["phrase"])
            ):
                heading = candidate_heading
                heading_text = candidate_heading_text
                heading_line_end = int(window[-1]["line"])
        book_heading = detect_scripture_book_heading(text)
        scripture_row = _SCRIPTURE_ROW_RE.match(text)
        locators = extract_material_locators(text)
        joined_text: str | None = None
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            next_text = str(next_line["text"])
            if _SOFT_WRAP_RE.search(text) or (
                not locators
                and heading is None
                and next_text[:1]
                and (next_text[:1].islower() or next_text[:1].isdigit())
            ):
                joined_text = f"{text}\n{next_text}"
                joined_locators = extract_material_locators(joined_text)
                if joined_locators:
                    locators = joined_locators
        notations = detect_notations(text)
        role = None
        if heading:
            role = heading["role"]
        elif book_heading:
            role = "scripture_book_heading"
        elif _ORDINAL_GROUP_RE.fullmatch(text):
            role = "ordinal_group"
        elif _LETTER_GROUP_RE.fullmatch(text):
            role = "letter_group"
        elif _UPPER_GROUP_RE.fullmatch(text) and not locators:
            role = "heading_group"
        elif scripture_row and locators:
            role = "scripture_row_candidate"
        elif _CROSS_REF_RE.search(text) and not locators:
            role = "cross_reference_candidate"
        elif locators:
            role = "entry_or_continuation_candidate"
        elif _SOFT_WRAP_RE.search(text):
            role = "soft_wrap_start"
        if role is None:
            continue
        record = {
            **line,
            "text": heading_text if heading else text,
            "line_end": heading_line_end if heading else line["line"],
            "role": role,
            "heading": heading,
            "scripture_book_heading": book_heading,
            "scripture_row": scripture_row.groupdict() if scripture_row else None,
            "material_locators": locators,
            "notations": notations,
        }
        if joined_text is not None and locators:
            record["wrapped_locator_candidate"] = {
                "line_end": lines[index + 1]["line"],
                "raw": joined_text,
            }
        if _SOFT_WRAP_RE.search(text) and index + 1 < len(lines):
            next_line = lines[index + 1]
            next_text = str(next_line["text"])
            if next_text[:1].islower():
                record["soft_wrap_candidate"] = {
                    "next_line": next_line["line"],
                    "raw": f"{text}\n{next_text}",
                }
        candidates.append(record)
    return {
        "file": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "visible_line_count": len(lines),
        "candidates": candidates,
    }


def _candidate_paths(
    *,
    source_root: Path,
    filtered_pages: Mapping[str, Any],
) -> list[Path]:
    root = source_root.resolve()
    selected: set[Path] = set()
    for value in filtered_pages.get("candidate_files") or []:
        path = Path(str(value)).expanduser()
        candidates = (
            [path.resolve()]
            if path.is_absolute()
            else [path.resolve(), (root / path).resolve()]
        )
        for resolved in candidates:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.is_file() and resolved.suffix.casefold() == ".txt":
                selected.add(resolved)
                break
    if not selected:
        selected.update(sorted(root.glob("*.txt"), key=page_sort_key)[-32:])
    return sorted(selected, key=page_sort_key)


def build_mechanical_analysis(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    filtered_pages: Mapping[str, Any],
) -> dict[str, Any]:
    files = [
        analyze_index_file(path)
        for path in _candidate_paths(
            source_root=source_root,
            filtered_pages=filtered_pages,
        )
    ]
    role_counts: dict[str, int] = {}
    section_leads: list[dict[str, Any]] = []
    locator_count = 0
    for file_record in files:
        for candidate in file_record["candidates"]:
            role = str(candidate["role"])
            role_counts[role] = role_counts.get(role, 0) + 1
            locator_count += len(candidate.get("material_locators") or [])
            if role in {"owned_heading", "stop_boundary"}:
                section_leads.append(
                    {
                        "file": file_record["file"],
                        "line": candidate["line"],
                        "text": candidate["text"],
                        **dict(candidate.get("heading") or {}),
                    }
                )
    file_position = {
        str(file_record["file"]): index for index, file_record in enumerate(files)
    }
    previous_by_signature: dict[tuple[str, str, str], int] = {}
    for lead in section_leads:
        signature = (
            str(lead.get("role") or ""),
            str(lead.get("section_kind") or ""),
            str(lead.get("phrase") or ""),
        )
        position = file_position[str(lead["file"])]
        previous = previous_by_signature.get(signature)
        lead["occurrence_role"] = (
            "running_header"
            if previous is not None and position == previous + 1
            else "section_start_candidate"
        )
        previous_by_signature[signature] = position
    return {
        "schema_version": 1,
        "stage": "mechanical_semantic_analysis",
        "engine": "pyahocorasick+regex" if ahocorasick is not None else "regex_fallback",
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(source_root.resolve()),
        "inspected_file_count": len(files),
        "candidate_line_count": sum(len(item["candidates"]) for item in files),
        "material_locator_count": locator_count,
        "role_counts": dict(sorted(role_counts.items())),
        "section_leads": section_leads,
        "files": files,
        "limitations": [
            "Candidates are evidence, not canonical semantic entries.",
            "Numeric OCR tokens remain literal when page/line/range roles compete.",
            "Soft-wrap candidates never mutate raw OCR.",
        ],
    }


__all__ = [
    "analyze_index_file",
    "build_mechanical_analysis",
    "detect_heading",
    "detect_notations",
    "detect_scripture_book_heading",
    "extract_material_locators",
    "normalize_mechanical_text",
]

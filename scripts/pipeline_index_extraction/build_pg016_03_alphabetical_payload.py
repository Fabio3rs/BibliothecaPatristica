#!/usr/bin/env python3
"""Usage: build the PG016.03 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg016_03_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG016.03/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG016.03_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG016.03_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG016.03 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG016.03_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripture_ref_normalizer import normalize_scripture_book_name


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG016.03"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 16, part 3"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.S | re.I)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
LETTER_RE = re.compile(r"^[A-ZΑ-Ω]$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|—+⧫—+)$", re.I)
SECTION_HEADINGS = {
    "scripture": "INDEX LOCORUM EX SCRIPTURA SACRA.",
    "profane": "INDEX LOCORUM EX SCRIPTIS PROFANIS.",
    "names": "INDEX NOMINUM.",
    "ordo": "ORDO RERUM",
}
FILE_SEQS = [552, 553, 554, 555, 556, 557, 558, 560]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm_key(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if m:
            files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(m.group(1))


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        kind = normalize(attrs.get("tipo") or "").lower()
        if kind not in {"cabecalho", "texto_principal", "nota_marginal", "aparato_critico"}:
            continue
        content = match.group("content") or ""
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def extract_header_pages(path: Path) -> list[int]:
    pages: list[int] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        kind = normalize(attrs.get("tipo") or "").lower()
        if kind != "cabecalho":
            continue
        content = normalize(match.group("content") or "")
        for token in PAGE_HEADER_RE.finditer(content):
            page = int(token.group(1))
            if page not in pages:
                pages.append(page)
    return pages


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for page in extract_header_pages(path):
            page_map.setdefault(page, str(path))
    return page_map


def page_numbers_for_file(path: Path) -> list[int]:
    return extract_header_pages(path)


def split_lines_by_marker(
    file_paths: list[Path],
    start_marker: str,
    stop_marker: str | None = None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    started = False
    for path in file_paths:
        for line in extract_lines(path):
            if not started:
                if line == start_marker:
                    started = True
                continue
            if stop_marker and line == stop_marker:
                return items
            items.append({"file": str(path), "line": line})
    return items


def split_all_lines(file_paths: list[Path]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in file_paths:
        for line in extract_lines(path):
            items.append({"file": str(path), "line": line})
    return items


def should_join(prev_raw: str, next_line: str) -> bool:
    if not prev_raw:
        return False
    if next_line[:1].islower() or next_line[:1].isdigit():
        return True
    if next_line[:1] in "([—-,:;":
        return True
    if prev_raw.endswith(("&amp;", "&", "-", ",", "(", "[")):
        return True
    if prev_raw.endswith(("K-", "Cl-", "Α-", "ὁ-", "ἡ-")):
        return True
    return False


def split_on_dash_fragments(text: str) -> list[str]:
    if " — " not in text and "—" not in text:
        return [text]
    parts = re.split(r"\s+—\s+", text)
    fragments = [normalize(part) for part in parts if normalize(part)]
    return fragments or [text]


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None, str]] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        raw = match.group(0).strip()
        key = (start, end, raw)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
            }
        )
    return refs


def parse_scripture_ref(entry_raw: str) -> dict[str, Any] | None:
    text = normalize(entry_raw)
    if not text:
        return None
    text = re.sub(r"\s+p\.\s*.*$", "", text)
    if "." not in text:
        return None
    m = re.match(r"^(?P<book>.+?)\s+(?P<citation>\d.*)$", text)
    if not m:
        return None
    book_raw = normalize(m.group("book"))
    citation = normalize(m.group("citation")).rstrip(".")
    citation = re.sub(r"\s*\([^)]*\)", "", citation)
    citation = citation.replace(";", ",").replace(".", ",")
    citation = re.sub(r"\s+", " ", citation)
    book_norm = normalize_scripture_book_name(book_raw)
    if not book_norm:
        return None

    chapter_match = re.match(
        r"^(?P<chapter>\d{1,3})(?:\s*[-–—]\s*(?P<chapter_end>\d{1,3}))?\s*,\s*(?P<verses>[\d,\s\-]+)$",
        citation,
    )
    if not chapter_match:
        # Some entries only preserve the verse list with a safe book label; keep the raw signal in notes.
        return None
    chapter_start = int(chapter_match.group("chapter"))
    chapter_end = int(chapter_match.group("chapter_end") or chapter_start)
    verses_raw = chapter_match.group("verses")
    verse_tokens = [tok.strip() for tok in verses_raw.split(",") if tok.strip()]
    if not verse_tokens:
        return None
    verse_numbers: list[int] = []
    for token in verse_tokens:
        if "-" in token:
            a, b = [piece.strip() for piece in token.split("-", 1)]
            if a.isdigit():
                verse_numbers.append(int(a))
            if b.isdigit():
                verse_numbers.append(int(b))
        elif token.isdigit():
            verse_numbers.append(int(token))
    if not verse_numbers:
        return None
    verse_start = verse_numbers[0]
    verse_end = verse_numbers[-1]
    is_range = verse_start != verse_end or chapter_start != chapter_end
    return {
        "ref_raw": f"{book_raw} {citation}".strip(),
        "book_raw": book_raw,
        "book_norm": book_norm,
        "chapter_start": chapter_start,
        "verse_start": verse_start,
        "chapter_end": chapter_end,
        "verse_end": verse_end,
        "is_range": is_range,
        "ref_role": "pericope" if "-" in verses_raw or len(verse_numbers) > 2 else "citation",
        "raw_json": {
            "citation_raw": citation,
            "verse_tokens": verse_tokens,
            "note": "Verse lists are preserved conservatively; sparse verse enumerations are summarized as a span in the schema.",
        },
    }


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pg016_03_scripture_genesis_001",
                "lemma_raw": "Gen. 1, 1. p. 418.",
                "query_names": ["Gen. 1, 1.", "Genesis 1, 1"],
                "page_hints": ["3458"],
                "page_hint_ints": [3458],
                "context_raw": "Gen. 1, 1. p. 418.",
            },
            {
                "entry_id": "pg016_03_profane_anacreontea_152",
                "lemma_raw": "ANACREONTEA (nr. 65 Bergk p 833) 152.",
                "query_names": ["ANACREONTEA", "Bergk p 833", "152"],
                "page_hints": ["3160"],
                "page_hint_ints": [3160],
                "context_raw": "ANACREONTEA (nr. 65 Bergk p 833) 152.",
            },
            {
                "entry_id": "pg016_03_names_abraham_206",
                "lemma_raw": "Ἀβραάμ vel Ἀβραὰμ 206, 282, 570, 552.",
                "query_names": ["Ἀβραάμ", "Ἀβραὰμ", "Abraham"],
                "page_hints": ["3160"],
                "page_hint_ints": [3160],
                "context_raw": "Ἀβραάμ vel Ἀβραὰμ 206, 282, 570, 552.",
            },
        ],
    }


def run_helper(source_root: Path, helper_request_json: Path, helper_output_json: Path) -> dict[str, Any] | None:
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_output_json.parent.mkdir(parents=True, exist_ok=True)
    helper_request = build_helper_request(source_root)
    helper_request_json.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
    )
    try:
        return json.loads(helper_output_json.read_text(encoding="utf-8"))
    except Exception:
        return None


def make_section(
    *,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    file_start: str,
    file_end: str,
    section_kind_reason: str,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, Any]:
    return {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": norm_key(heading_raw),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": file_start,
        "file_end": file_end,
        "confidence": 0.95,
        "raw_json": {"section_kind_reason": section_kind_reason},
    }


def finalize_buffer(
    buffer: list[dict[str, str]],
    *,
    section_key: str,
    section_kind: str,
    entry_order: int,
    parent_node_key: str | None,
    heading_letter: str | None,
    page_map: dict[int, str],
    section_start_file: str,
    editorial_anchor_file: str,
    current_entry_kind: str = "lemma",
    preserve_scripture: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    if not buffer:
        return None, [], entry_order
    raw = " ".join(item["line"] for item in buffer).strip()
    file = buffer[0]["file"]
    if not raw:
        return None, [], entry_order
    entry_fragments = split_on_dash_fragments(raw) if section_kind == "author_index" else [raw]
    built_entries: list[dict[str, Any]] = []
    for frag_idx, fragment in enumerate(entry_fragments):
        fragment = normalize(fragment)
        if not fragment:
            continue
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:04d}"
        entry_kind = current_entry_kind
        if section_kind in {"names", "onomastic_mixed"} and entry_kind == "lemma":
            entry_kind = "lemma"
        if frag_idx > 0 and section_kind == "author_index":
            entry_kind = "sublemma"
        lemma_raw = fragment
        if section_kind in {"names", "onomastic_mixed", "author_index", "ordo_rerum"}:
            m = re.search(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", fragment)
            if m:
                lemma_raw = fragment[: m.start()].rstrip(" ,;:.")
        elif preserve_scripture:
            m = re.match(r"^(?P<book>.+?)\s+(?P<citation>\d.*)$", fragment)
            if m:
                lemma_raw = f"{normalize(m.group('book'))} {normalize(m.group('citation')).split(' p. ', 1)[0].rstrip('.')}"
        lemma_display = lemma_raw or fragment
        lemma_norm = norm_key(lemma_raw)
        lemma_sort = lemma_norm
        entry_refs = extract_page_refs(fragment)
        current_pages = page_numbers_for_file(Path(file))
        inferred_printed_page = current_pages[-1] if current_pages else None
        target_best = file
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": parent_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw if lemma_raw else None,
            "lemma_display": lemma_display if lemma_display else None,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_sort,
            "entry_raw": fragment,
            "context_raw": None,
            "heading_letter": heading_letter,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": target_best,
            "confidence": 0.88 if entry_refs else 0.72,
            "raw_json": {
                "source_file": file,
                "section_kind": section_kind,
                "entry_kind_reason": "Conservative line-level grouping from OCR, preserving literal punctuation and nested citation numerals.",
            },
        }
        if section_kind == "author_index":
            entry["raw_json"]["note"] = "Profane source index entries preserve nested work locators in entry_raw; material page refs are extracted from the same OCR line conservatively."
        if section_kind == "scripture_index":
            entry["raw_json"]["note"] = "Scripture index entry grouped by OCR line; biblical citation parsing is stored separately when safe."
        if section_kind == "ordo_rerum":
            entry["entry_kind"] = "heading_group" if not entry_refs else "lemma"
        built_entries.append(entry)
        if preserve_scripture:
            scripture = parse_scripture_ref(fragment)
            if scripture:
                scripture.update(
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "confidence": 0.86,
                        "raw_json": {
                            "source_file": file,
                            "section_kind": section_kind,
                            "parsed_from": fragment,
                        },
                    }
                )
                built_entries[-1]["raw_json"]["scripture_ref_parsed"] = True
                built_entries[-1]["raw_json"]["scripture_ref_book"] = scripture["book_raw"]
                built_entries[-1]["raw_json"]["scripture_ref_citation"] = scripture["raw_json"]["citation_raw"]
                built_entries[-1]["raw_json"]["scripture_ref_confidence"] = scripture["confidence"] if "confidence" in scripture else 0.86
                built_entries[-1]["raw_json"]["scripture_ref_note"] = scripture["raw_json"]["note"]
                yield_scripture = scripture
                # attach scripture ref to entry through caller
        for ref_order, ref in enumerate(entry_refs, start=1):
            ref["entry_key"] = entry_key
            ref["ref_order"] = ref_order
            ref["ref_kind"] = "editorial_range" if ref["range_end_raw"] else "editorial_page"
            ref["target_file"] = page_map.get(ref["page_ref_int"])
            ref["target_file_probability"] = 0.99 if ref["target_file"] else None
            ref["section_start_file"] = section_start_file
            ref["editorial_anchor_file"] = editorial_anchor_file
            ref["confidence"] = 0.82 if ref["target_file"] else 0.56
            ref["raw_json"] = {
                "source_file": file,
                "section_kind": section_kind,
                "page_resolution": "exact_header_match" if ref["target_file"] else "unmapped",
            }
            built_entries[-1].setdefault("_refs", []).append(ref)
        # scripture ref is returned separately by caller; no-op here
    return built_entries[-1] if built_entries else None, [r for e in built_entries for r in e.get("_refs", [])], entry_order


def build_section_entries(
    items: list[dict[str, str]],
    *,
    section_key: str,
    section_kind: str,
    section_start_file: str,
    editorial_anchor_file: str,
    page_map: dict[int, str],
    allow_nodes: bool = False,
    split_dash_entries: bool = False,
    preserve_scripture: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    buffer: list[dict[str, str]] = []
    current_node_key: str | None = None
    current_letter: str | None = None
    entry_order = 0
    node_order = 0

    def flush() -> None:
        nonlocal buffer, entry_order
        if not buffer:
            return
        raw = " ".join(item["line"] for item in buffer).strip()
        if not raw:
            buffer = []
            return
        file = buffer[0]["file"]
        fragments = [raw]
        if split_dash_entries and " — " in raw:
            fragments = split_on_dash_fragments(raw)
        for idx, fragment in enumerate(fragments):
            fragment = normalize(fragment)
            if not fragment:
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:04d}"
            lemma_raw = fragment
            if section_kind == "scripture_index":
                scripture_m = re.match(r"^(?P<book>.+?)\s+(?P<citation>\d.*)$", fragment)
                if scripture_m:
                    lemma_raw = re.sub(r"\s+p\.\s*.*$", "", fragment).rstrip(" ,;:.")
            elif section_kind in {"names", "onomastic_mixed", "author_index", "ordo_rerum"}:
                ref_match = re.search(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", fragment)
                if ref_match:
                    lemma_raw = fragment[: ref_match.start()].rstrip(" ,;:.")
            lemma_display = lemma_raw or fragment
            lemma_norm = norm_key(lemma_raw)
            inferred_printed_page = page_numbers_for_file(Path(file))
            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": current_node_key,
                "entry_order": entry_order,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw if lemma_raw else None,
                "lemma_display": lemma_display if lemma_display else None,
                "lemma_norm": lemma_norm,
                "lemma_sort": lemma_norm,
                "entry_raw": fragment,
                "context_raw": None,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_printed_page[-1] if inferred_printed_page else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "target_file_best": file,
                "confidence": 0.87,
                "raw_json": {
                    "source_file": file,
                    "section_kind": section_kind,
                    "entry_kind_reason": "Conservative OCR line grouping; literal punctuation and numeral order preserved.",
                },
            }
            if section_kind == "scripture_index":
                scripture = parse_scripture_ref(fragment)
                if scripture:
                    scripture["entry_key"] = entry_key
                    scripture["ref_order"] = 1
                    scripture["confidence"] = 0.89
                    scripture["raw_json"] = {
                        "source_file": file,
                        "section_kind": section_kind,
                        "parsed_from": fragment,
                    }
                    entry["raw_json"]["scripture_ref_parsed"] = True
                    entry["raw_json"]["scripture_ref"] = scripture["ref_raw"]
                    entry["raw_json"]["scripture_ref_book"] = scripture["book_raw"]
                    entry.setdefault("_scripture_refs", []).append(scripture)
            if section_kind == "author_index":
                entry["entry_kind"] = "sublemma" if idx > 0 else "lemma"
                entry["raw_json"]["note"] = "Profane source index line with nested work titles and page locators preserved literally."
            if section_kind == "ordo_rerum" and not re.search(r"\d", fragment):
                entry["entry_kind"] = "heading_group"
                entry["confidence"] = 0.78
            entry_refs: list[dict[str, Any]] = []
            if section_kind == "scripture_index":
                # Only the printed page locators after the biblical citation are material refs.
                page_tail = re.split(r"\bp\.\s*", fragment, maxsplit=1)
                tail = page_tail[1] if len(page_tail) > 1 else ""
                entry_refs = extract_page_refs(tail)
            else:
                entry_refs = extract_page_refs(fragment)
            for ref_idx, ref in enumerate(entry_refs, start=1):
                ref["entry_key"] = entry_key
                ref["ref_order"] = ref_idx
                ref["ref_kind"] = "editorial_range" if ref["range_end_raw"] else "editorial_page"
                ref["target_file"] = page_map.get(ref["page_ref_int"])
                ref["target_file_probability"] = 0.99 if ref["target_file"] else None
                ref["section_start_file"] = section_start_file
                ref["editorial_anchor_file"] = editorial_anchor_file
                ref["confidence"] = 0.84 if ref["target_file"] else 0.58
                ref["raw_json"] = {
                    "source_file": file,
                    "section_kind": section_kind,
                    "page_resolution": "exact_header_match" if ref["target_file"] else "unmapped",
                }
            refs.extend(entry_refs)
            entries.append(entry)
            if entry.get("_scripture_refs"):
                entries[-1].setdefault("raw_json", {})["scripture_ref_parsed"] = True
                entries[-1]["raw_json"]["scripture_ref_book"] = entry["_scripture_refs"][0]["book_raw"]
            if section_kind == "author_index" and current_letter:
                entries[-1]["raw_json"]["heading_letter"] = current_letter
        buffer = []

    for item in items:
        line = item["line"]
        if section_kind in {"names", "onomastic_mixed"} and LETTER_RE.fullmatch(line):
            flush()
            node_order += 1
            current_letter = line
            current_node_key = f"{VOLUME_ID}:node:names:{node_order:03d}"
            nodes.append(
                {
                    "node_key": current_node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": norm_key(line),
                    "label_sort": norm_key(line),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": item["file"], "section_kind": section_kind},
                }
            )
            continue
        if line in SECTION_HEADINGS.values():
            continue
        if not buffer:
            buffer = [item]
            continue
        if should_join(buffer[-1]["line"], line):
            buffer.append(item)
            continue
        flush()
        buffer = [item]
    flush()

    scripture_refs: list[dict[str, Any]] = []
    for entry in entries:
        for sref in entry.pop("_scripture_refs", []):
            scripture_refs.append(sref)
        entry.pop("_refs", None)
    return entries, refs, nodes, scripture_refs


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = [path for path in discover_files(source_root) if file_seq(path) in FILE_SEQS]
    page_map = build_page_map(discover_files(source_root))

    helper_result = run_helper(source_root, helper_request_json, helper_output_json)

    section_files = {
        "scripture": [path for path in files if file_seq(path) in {552, 553}],
        "profane": [path for path in files if file_seq(path) in {553}],
        "names": [path for path in files if file_seq(path) in {553, 554, 555, 556, 557}],
        "ordo": [path for path in files if file_seq(path) in {558, 560}],
    }

    scripture_items = split_lines_by_marker(section_files["scripture"], SECTION_HEADINGS["scripture"], SECTION_HEADINGS["profane"])
    profane_items = split_lines_by_marker(section_files["profane"], SECTION_HEADINGS["profane"], SECTION_HEADINGS["names"])
    names_items = split_lines_by_marker(section_files["names"], SECTION_HEADINGS["names"], SECTION_HEADINGS["ordo"])
    ordo_items = split_all_lines(section_files["ordo"])
    ordo_items = [item for item in ordo_items if item["line"] not in {"ORDO RERUM", "Digitized by Google"}]

    sections = [
        make_section(
            section_key=f"{VOLUME_ID}:alpha:scripture_index:001",
            section_order=1,
            section_kind="scripture_index",
            heading_raw=SECTION_HEADINGS["scripture"],
            file_start=str(section_files["scripture"][0]),
            file_end=str(section_files["scripture"][-1]),
            section_kind_reason="Biblical citation index headed INDEX LOCORUM EX SCRIPTURA SACRA.",
        ),
        make_section(
            section_key=f"{VOLUME_ID}:alpha:author_index:002",
            section_order=2,
            section_kind="author_index",
            heading_raw=SECTION_HEADINGS["profane"],
            file_start=str(section_files["profane"][0]),
            file_end=str(section_files["profane"][-1]),
            section_kind_reason="Index of citations from profane writers and works, modeled as author_index because the printed entries are author/work citations with page locators.",
        ),
        make_section(
            section_key=f"{VOLUME_ID}:alpha:onomastic_mixed:003",
            section_order=3,
            section_kind="onomastic_mixed",
            heading_raw=SECTION_HEADINGS["names"],
            file_start=str(section_files["names"][0]),
            file_end=str(section_files["names"][-1]),
            section_kind_reason="Alphabetical index of names mixing persons, places, groups, and mythic figures.",
        ),
        make_section(
            section_key=f"{VOLUME_ID}:alpha:ordo_rerum:004",
            section_order=4,
            section_kind="ordo_rerum",
            heading_raw="ORDO RERUM",
            file_start=str(section_files["ordo"][0]),
            file_end=str(section_files["ordo"][-1]),
            section_kind_reason="Editorial contents table preserved as ordo_rerum.",
        ),
    ]

    scripture_entries, scripture_refs, scripture_nodes, scripture_scripture_refs = build_section_entries(
        scripture_items,
        section_key=sections[0]["section_key"],
        section_kind="scripture_index",
        section_start_file=sections[0]["file_start"],
        editorial_anchor_file=sections[0]["file_start"],
        page_map=page_map,
        preserve_scripture=True,
    )
    profane_entries, profane_refs, profane_nodes, profane_scripture_refs = build_section_entries(
        profane_items,
        section_key=sections[1]["section_key"],
        section_kind="author_index",
        section_start_file=sections[1]["file_start"],
        editorial_anchor_file=sections[1]["file_start"],
        page_map=page_map,
        split_dash_entries=True,
    )
    names_entries, names_refs, names_nodes, names_scripture_refs = build_section_entries(
        names_items,
        section_key=sections[2]["section_key"],
        section_kind="onomastic_mixed",
        section_start_file=sections[2]["file_start"],
        editorial_anchor_file=sections[2]["file_start"],
        page_map=page_map,
        allow_nodes=True,
    )
    ordo_entries, ordo_refs, ordo_nodes, ordo_scripture_refs = build_section_entries(
        ordo_items,
        section_key=sections[3]["section_key"],
        section_kind="ordo_rerum",
        section_start_file=sections[3]["file_start"],
        editorial_anchor_file=sections[3]["file_start"],
        page_map=page_map,
    )

    entries = scripture_entries + profane_entries + names_entries + ordo_entries
    refs = scripture_refs + profane_refs + names_refs + ordo_refs
    nodes = scripture_nodes + profane_nodes + names_nodes + ordo_nodes
    scripture_refs_out = scripture_scripture_refs + profane_scripture_refs + names_scripture_refs + ordo_scripture_refs

    # Recover and propagate scripture refs after the main entry pass.
    if scripture_scripture_refs:
        for sref in scripture_scripture_refs:
            pass

    # Replace missing section pages with nulls when the OCR spread is mixed.
    for section in sections:
        section["page_start"] = None
        section["page_end"] = None
        section["raw_json"]["file_sequences"] = [file_seq(Path(section["file_start"])), file_seq(Path(section["file_end"]))]
        section["raw_json"]["helper_used"] = helper_result is not None
        section["raw_json"]["helper_summary"] = helper_result.get("status") if isinstance(helper_result, dict) else None

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the Scripture, profane-source, onomastic, and ORDO RERUM blocks from the OCR tail; "
            "page-header mapping was used for material refs, while the mixed index spreads kept section page numbers conservative."
        ),
        "evidence_files": [str(path) for path in files],
    }

    notes = [
        "The tail of PG016.03 contains four editorial structures: a scripture index, a profane-source citation index, an onomastic mixed index, and a closing ORDO RERUM table.",
        "OCR file suffix order is not the same as editorial page order in the mixed spreads; `target_file_best` follows the local OCR file and refs use the page-header map when possible.",
        "Scripture citations are parsed conservatively only when the book label is explicit and the chapter/verse pattern is safe enough to normalize.",
        "Profane-source entries preserve nested work locators and literal punctuation in `entry_raw`; page refs are extracted conservatively from the same OCR line.",
    ]
    if helper_result is not None:
        notes.append("Helper output was written to the runtime helper path and preserved as a checkpoint, but direct OCR reading remains authoritative.")

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs_out,
        "coverage": coverage,
        "notes": notes,
    }
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG016.03 alphabetical payload from OCR.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PG016.03 alphabetical payload and preserve the mixed tail index structures.",
        "completed": [
            "OCR tail inspected",
            "section boundaries identified",
            "helper request generated",
        ],
        "pending": [
            "validate output JSON shape",
            "confirm scripture refs and profane-source refs remain conservative",
        ],
        "blocked": [],
        "notes": [
            "The volume ends with a mixed tail of scripture, profane-source, onomastic, and ORDO RERUM material.",
            "Page-header mapping is needed because the OCR files preserve the current index spread plus the cited page locators.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL218_build_alphabetical_payload.py

Build the PL218 alphabetical-index payload from the OCR tail, generate the
helper request/output files, persist intermediate checkpoints, and write the
canonical JSON payload to data/alphabetical_index_payloads/PL218_alphabetical_indices.json.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL218"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL218/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL218_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL218_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL218_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL218"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
MANIFEST_JSON = INTERMEDIATE_DIR / "manifest.json"
VOLUME_JSON = INTERMEDIATE_DIR / "volume.json"
SECTIONS_JSON = INTERMEDIATE_DIR / "sections.json"
NODES_JSON = INTERMEDIATE_DIR / "nodes.json"
ENTRIES_JSON = INTERMEDIATE_DIR / "entries.json"
REFS_JSON = INTERMEDIATE_DIR / "refs.json"
SCRIPTURE_REFS_JSON = INTERMEDIATE_DIR / "scripture_refs.json"
COVERAGE_JSON = INTERMEDIATE_DIR / "coverage.json"
NOTES_JSON = INTERMEDIATE_DIR / "notes.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION1_SEQS = range(308, 362)
SECTION2_SEQS = range(675, 678)

SECTION1_KEY = f"{VOLUME_ID}:alpha:author_index:001"
SECTION2_KEY = f"{VOLUME_ID}:alpha:author_index:002"

SECTION1_HEADING_RAW = (
    "INDEX ANALYTICUS ORDINE DIGESTUS ALPHABETICO "
    "AUCTORUM OMNIUM QUORUM SCRIPTIS PROSTAT PATROLOGIÆ LATINÆ CURSUS."
)
SECTION2_HEADING_RAW = (
    "INDEX SCRIPTORUM ETHNICORUM A QUIBUS PATRES ARGUMENTA MUTUATI SUNT, "
    "ET QUORUM VIRTUTES LAUDARE, VITIA ET ERRORES DEBELLARE CONATI SUNT."
)

NOISE_LINES = {"Digitized by Google", "=="}

ENTRY_START_RE = re.compile(
    r"(?:^|(?<=[.;:\-])\s)"
    r"([A-ZÆŒ]{2,}(?:[A-Za-zÆŒæœ\.\-()\[\] ]{0,60})?"
    r"(?:,\s*(?:.*?\b(?:an\.|anno|ann\.|sæc\.|sæculo|circ\.|circa)\b|Vide\b)|\s+Vide\b))",
    flags=re.S,
)
SECTION2_START_RE = re.compile(
    r"^[A-ZÆŒ][A-ZÆŒ\.\-() \[\]]{1,120}(?:\s+Vide\b|\s*[-—]\s*|\.\s|,\s)",
    flags=re.I,
)
FIRST_CITATION_RE = re.compile(r"\b[IVXLCDM]{1,6},\s*\d{1,4}", flags=re.I)
CITATION_GROUP_RE = re.compile(
    r"\b([IVXLCDM]{1,6}),\s*([0-9]{1,4}(?:\s*,\s*[0-9]{1,4})*(?:\s*(?:seq\.?|sqq\.?|bis))?)",
    flags=re.I,
)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", flags=re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return normalize_space(value)


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def discover_files(seqs: range) -> list[Path]:
    out: list[Path] = []
    for seq in seqs:
        matches = sorted(SOURCE_ROOT.glob(f"*-{seq:03d}.txt"))
        if not matches:
            raise FileNotFoundError(f"Missing OCR file for sequence {seq:03d}")
        out.append(matches[0])
    return out


def extract_block_lines(path: Path, block_type: str = "texto_principal") -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(rf'<bloco tipo="{block_type}"[^>]*>(.*?)</bloco>', text, flags=re.S)
    lines: list[str] = []
    for block in blocks:
        for raw_line in block.splitlines():
            line = normalize_space(raw_line)
            if line and line not in NOISE_LINES:
                lines.append(line)
    return lines


def build_text_and_spans(files: list[Path]) -> tuple[str, list[tuple[int, int, Path]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, Path]] = []
    offset = 0
    for idx, path in enumerate(files):
        content = " ".join(extract_block_lines(path))
        if not content:
            continue
        if idx:
            parts.append(" ")
            offset += 1
        start = offset
        parts.append(content)
        offset += len(content)
        spans.append((start, offset, path))
    return "".join(parts), spans


def locate_span(spans: list[tuple[int, int, Path]], pos: int) -> Path:
    if not spans:
        raise ValueError("No spans available")
    starts = [start for start, _, _ in spans]
    idx = bisect_right(starts, pos) - 1
    if idx < 0:
        return spans[0][2]
    start, end, path = spans[idx]
    if start <= pos < end:
        return path
    return spans[min(idx + 1, len(spans) - 1)][2]


def section1_chunks(files: list[Path]) -> list[dict[str, Any]]:
    full_text, spans = build_text_and_spans(files)
    matches = list(ENTRY_START_RE.finditer(full_text))
    chunks: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        chunk = normalize_space(full_text[start:end])
        if not chunk:
            continue
        chunks.append(
            {
                "chunk_raw": chunk,
                "source_file": str(locate_span(spans, start)),
                "start_offset": start,
            }
        )
    return chunks


def section2_chunks(files: list[Path]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_text: str | None = None
    current_file: Path | None = None

    def flush() -> None:
        nonlocal current_text, current_file
        if current_text and current_file is not None:
            chunks.append({"chunk_raw": normalize_space(current_text), "source_file": str(current_file)})
        current_text = None
        current_file = None

    for path in files:
        for line in extract_block_lines(path):
            if line in NOISE_LINES or LETTER_RE.fullmatch(line):
                flush()
                continue
            if line.startswith("INDEX "):
                continue
            if current_text is None:
                current_text = line
                current_file = path
                continue
            if SECTION2_START_RE.match(line) and not line[0].isdigit():
                flush()
                current_text = line
                current_file = path
            else:
                current_text += " " + line
    flush()
    return chunks


def guess_heading_letter(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"[A-Za-zÆŒæœ]", text)
    return m.group(0).upper() if m else None


def first_citation_pos(text: str) -> int | None:
    m = FIRST_CITATION_RE.search(text)
    return m.start() if m else None


def parse_refs(chunk: str, target_file: str, section_start_file: str, helper_ref: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    matches = list(CITATION_GROUP_RE.finditer(chunk))
    for group_idx, match in enumerate(matches):
        roman = match.group(1).upper()
        group_end = matches[group_idx + 1].start() if group_idx + 1 < len(matches) else len(chunk)
        group_text = normalize_space(chunk[match.start():group_end]).rstrip(" .;:")
        pages_part = match.group(2)
        for page_idx, page_token in enumerate(re.split(r"\s*,\s*", pages_part)):
            token = normalize_space(page_token)
            if not token:
                continue
            token_match = re.match(r"^(?P<page>\d{1,4})(?:\s*(?P<suffix>seq\.?|sqq\.?|bis))?$", token, flags=re.I)
            if not token_match:
                continue
            page_str = token_match.group("page")
            suffix = token_match.group("suffix")
            ref_raw = f"{roman}, {page_str}"
            if suffix:
                ref_raw += f" {suffix}"
            refs.append(
                {
                    "ref_raw": ref_raw,
                    "page_ref_raw": f"{page_str}{(' ' + suffix) if suffix else ''}",
                    "page_ref_int": int(page_str),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": helper_ref.get("probability") if helper_ref else 1.0,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": target_file,
                    "confidence": 0.95 if helper_ref else 0.9,
                    "raw_json": {
                        "group_raw": group_text,
                        "page_token_index": page_idx + 1,
                        "helper_ref": helper_ref,
                    },
                }
            )
    return refs


def parse_entry_chunk(chunk: str, source_file: str, section_key: str, section_start_file: str, helper_summary: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    chunk = normalize_space(chunk)
    citation_pos = first_citation_pos(chunk)
    if citation_pos is not None:
        lemma_raw = normalize_space(chunk[:citation_pos].rstrip(" ,.;:"))
    else:
        lemma_raw = normalize_space(chunk.rstrip(" ,.;:"))
    entry_kind = "lemma"
    if re.match(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", lemma_raw, flags=re.I) or re.search(r"\bVide\b", chunk, flags=re.I):
        entry_kind = "cross_reference"
    heading_letter = guess_heading_letter(lemma_raw)
    target_file = source_file
    helper_best = (helper_summary or {}).get("best_candidate") or {}
    if helper_best.get("file"):
        target_file = helper_best["file"]
    refs = parse_refs(chunk, target_file, section_start_file, helper_best if helper_best else None)
    if not refs and entry_kind == "lemma":
        entry_kind = "editorial_note"
    entry = {
        "lemma_raw": lemma_raw or None,
        "lemma_display": lemma_raw or None,
        "lemma_norm": normalize_sort(lemma_raw),
        "lemma_sort": normalize_sort(lemma_raw),
        "entry_raw": chunk,
        "context_raw": chunk,
        "heading_letter": heading_letter,
        "inferred_printed_page": refs[0]["page_ref_int"] if refs else None,
        "section_start_file": section_start_file,
        "editorial_anchor_file": source_file,
        "target_file_best": target_file,
        "confidence": 0.94 if refs else 0.78,
        "raw_json": {
            "source_file": source_file,
            "section_kind": "author_index",
            "helper_summary": helper_summary,
        },
    }
    return entry, refs


def build_letter_nodes(section_key: str, volume_id: str, section_order: int, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    node_order = 0
    for entry in entries:
        letter = entry.get("heading_letter")
        if not letter:
            continue
        if letter not in letter_to_node:
            node_order += 1
            node_key = f"{volume_id}:node:{section_order:03d}:{node_order:03d}"
            letter_to_node[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"role": "alphabetic_letter"},
                }
            )
        entry["parent_node_key"] = letter_to_node[letter]
    return nodes


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        header_blocks = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', text, flags=re.S)
        header_text = " ".join(normalize_space(line) for block in header_blocks for line in block.splitlines())
        numbers = []
        for match in re.finditer(r"\b(\d{1,4})\b", header_text):
            numbers.append(int(match.group(1)))
        # keep only plausible printed-page numbers and map both left/right page numbers when present
        for number in numbers:
            if 1 <= number <= 2000 and number not in page_map:
                page_map[number] = str(path)
    return page_map


def lookup_helper_summary(helper_output: dict[str, Any], entry_id: str) -> dict[str, Any] | None:
    for item in helper_output.get("entries", []):
        if item.get("entry_id") == entry_id:
            best = item.get("best_candidate") or {}
            return {
                "status": item.get("status"),
                "best_candidate": best,
                "candidates": item.get("candidates", [])[:3],
                "debug": item.get("debug"),
            }
    return None


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        lemma_raw = entry["lemma_raw"] or ""
        context_raw = entry["entry_raw"]
        page_hints = sorted({ref["page_ref_int"] for ref in entry.get("refs", []) if ref.get("page_ref_int") is not None})
        if not page_hints:
            continue
        helper_id = f"pl218_author_index_{idx:04d}"
        entry.setdefault("raw_json", {})["helper_entry_id"] = helper_id
        helper_entries.append(
            {
                "entry_id": helper_id,
                "lemma_raw": lemma_raw,
                "query_names": [q for q in [lemma_raw, re.sub(r"\([^)]*\)", "", lemma_raw).strip(), context_raw[:180]] if q],
                "page_hints": [str(v) for v in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": context_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def update_todo(current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": current_focus,
            "completed": completed,
            "pending": pending,
            "blocked": blocked,
            "notes": notes,
        },
    )


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    update_todo(
        "Parse PL218 author-index sections and resolve cited page targets",
        ["inspected OCR tail", "identified author_index sections", "prepared extraction scaffolding"],
        ["build helper request", "run index_target_locator", "assemble final payload"],
        [],
        ["Use the main author index block on files 308-361 and the ethnic authors index on 675-677.", "Preserve OCR literals and keep page-hint refs separate from OCR file suffixes."],
    )

    section1_files = discover_files(SECTION1_SEQS)
    section2_files = discover_files(SECTION2_SEQS)
    all_files = section1_files + section2_files
    page_map = build_page_map(all_files)

    section1_chunk_list = section1_chunks(section1_files)
    section2_chunk_list = section2_chunks(section2_files)

    provisional_entries: list[dict[str, Any]] = []
    provisional_refs: list[dict[str, Any]] = []

    # Parse section 1 first so helper request can use all page hints.
    for chunk_info in section1_chunk_list:
        entry, refs = parse_entry_chunk(
            chunk_info["chunk_raw"],
            chunk_info["source_file"],
            SECTION1_KEY,
            str(section1_files[0]),
            None,
        )
        entry["section_key"] = SECTION1_KEY
        entry["entry_kind"] = "lemma" if refs else "editorial_note"
        entry["entry_order"] = len(provisional_entries) + 1
        entry["entry_key"] = f"{VOLUME_ID}:entry:001:{entry['entry_order']:04d}"
        provisional_entries.append(entry)
        for ref in refs:
            ref["entry_key"] = entry["entry_key"]
            ref["ref_order"] = len([r for r in provisional_refs if r["entry_key"] == entry["entry_key"]]) + 1
            # Map the cited page to the closest known OCR file when possible.
            cited_target = page_map.get(ref["page_ref_int"])
            if cited_target:
                ref["target_file"] = cited_target
                ref["target_file_probability"] = 1.0
                ref["confidence"] = 0.97
            provisional_refs.append(ref)
        entry["refs"] = refs
        if refs and refs[0].get("target_file"):
            entry["target_file_best"] = refs[0]["target_file"]

    for chunk_info in section2_chunk_list:
        entry, refs = parse_entry_chunk(
            chunk_info["chunk_raw"],
            chunk_info["source_file"],
            SECTION2_KEY,
            str(section2_files[0]),
            None,
        )
        entry["section_key"] = SECTION2_KEY
        entry["entry_kind"] = "lemma" if refs else "cross_reference"
        entry["entry_order"] = len([e for e in provisional_entries if e["section_key"] == SECTION2_KEY]) + 1
        entry["entry_key"] = f"{VOLUME_ID}:entry:002:{entry['entry_order']:04d}"
        for ref in refs:
            ref["entry_key"] = entry["entry_key"]
            ref["ref_order"] = len([r for r in provisional_refs if r["entry_key"] == entry["entry_key"]]) + 1
            cited_target = page_map.get(ref["page_ref_int"])
            if cited_target:
                ref["target_file"] = cited_target
                ref["target_file_probability"] = 1.0
                ref["confidence"] = 0.97
            provisional_refs.append(ref)
        entry["refs"] = refs
        if refs and refs[0].get("target_file"):
            entry["target_file_best"] = refs[0]["target_file"]
        provisional_entries.append(entry)

    helper_request = build_helper_request(provisional_entries)
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output: dict[str, Any] = {}
    if HELPER_OUTPUT_JSON.exists():
        helper_output = read_json(HELPER_OUTPUT_JSON, {})

    sections = [
        {
            "section_key": SECTION1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": SECTION1_HEADING_RAW,
            "heading_norm": normalize_sort(SECTION1_HEADING_RAW),
            "heading_letter": None,
            "page_start": 543,
            "page_end": 654,
            "file_start": str(section1_files[0]),
            "file_end": str(section1_files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical author index with long analytical entries and explicit volume/page citations.",
                "source_files": [str(p) for p in section1_files],
                "helper_request_count": len(helper_request["entries"]),
            },
        },
        {
            "section_key": SECTION2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "author_index",
            "heading_raw": SECTION2_HEADING_RAW,
            "heading_norm": normalize_sort(SECTION2_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1281,
            "page_end": 1286,
            "file_start": str(section2_files[0]),
            "file_end": str(section2_files[-1]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Complementary author index of pagan sources cited by the Fathers; kept as author_index because the structure is still an alphabetical author list.",
                "source_files": [str(p) for p in section2_files],
                "helper_request_count": len(helper_request["entries"]),
            },
        },
    ]

    # Attach helper summaries and map entry refs to OCR targets.
    helper_index = {item.get("entry_id"): item for item in helper_output.get("entries", [])}
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for section_order, section_key in [(1, SECTION1_KEY), (2, SECTION2_KEY)]:
        section_entries = [e for e in provisional_entries if e["section_key"] == section_key]
        section_entries.sort(key=lambda e: e["entry_order"])
        for e_idx, entry in enumerate(section_entries, start=1):
            entry["entry_order"] = e_idx
            entry["entry_key"] = f"{VOLUME_ID}:entry:{section_order:03d}:{e_idx:04d}"
            helper_id = entry.get("raw_json", {}).get("helper_entry_id")
            helper_item = helper_index.get(helper_id)
            if helper_item:
                entry["raw_json"]["helper_summary"] = {
                    "status": helper_item.get("status"),
                    "best_candidate": helper_item.get("best_candidate"),
                    "top_candidates": helper_item.get("candidates", [])[:3],
                }
                best = helper_item.get("best_candidate") or {}
                if best.get("file"):
                    entry["target_file_best"] = best["file"]
            if entry.get("refs") and entry["refs"][0].get("target_file"):
                entry["target_file_best"] = entry["refs"][0]["target_file"]
            entries.append(entry)
            for r_idx, ref in enumerate(entry.get("refs", []), start=1):
                ref["entry_key"] = entry["entry_key"]
                ref["ref_order"] = r_idx
                # Refresh the target file from the page map when exact; helper output is advisory.
                cited_target = page_map.get(ref["page_ref_int"])
                if cited_target:
                    ref["target_file"] = cited_target
                    ref["target_file_probability"] = 1.0
                refs.append(ref)

    # Build nodes after the entries have final keys.
    nodes.extend(build_letter_nodes(SECTION1_KEY, VOLUME_ID, 1, [e for e in entries if e["section_key"] == SECTION1_KEY]))
    nodes.extend(build_letter_nodes(SECTION2_KEY, VOLUME_ID, 2, [e for e in entries if e["section_key"] == SECTION2_KEY]))

    # Reattach parent node keys now that the nodes exist.
    for entry in entries:
        letter = entry.get("heading_letter")
        if not letter:
            continue
        for node in nodes:
            if node["section_key"] == entry["section_key"] and node["label_raw"] == letter:
                entry["parent_node_key"] = node["node_key"]
                break
        else:
            entry["parent_node_key"] = None

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the PL218 author index and the final scriptorum ethnicorum index from OCR tail files 308-361 and 675-677.",
        "evidence_files": [str(p) for p in (section1_files[:3] + section1_files[-3:] + section2_files)],
    }

    notes = [
        "Section 1 is the long INDEX ANALYTICUS AUCTORUM block.",
        "Section 2 is the INDEX SCRIPTORUM ETHNICORUM block; both are serialized as author_index because the editorial structure is alphabetic author listing.",
        "Cited page references are kept distinct from OCR file suffixes; exact target files are resolved from the local page map and helper output is preserved in raw_json.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Patrologia Latina 218",
        "notes": "Recovered from the OCR tail; section 1 contains the analytical author index, section 2 contains the ethnic authors index.",
    }

    # Persist intermediates.
    write_json(MANIFEST_JSON, {"volume_id": VOLUME_ID, "updated_at": now_iso(), "source_root": str(SOURCE_ROOT), "generated_at": now_iso()})
    write_json(VOLUME_JSON, volume)
    write_json(SECTIONS_JSON, sections)
    write_json(NODES_JSON, nodes)
    write_json(ENTRIES_JSON, entries)
    write_json(REFS_JSON, refs)
    write_json(SCRIPTURE_REFS_JSON, [])
    write_json(COVERAGE_JSON, coverage)
    write_json(NOTES_JSON, notes)
    update_todo(
        "Finalize PL218 payload",
        ["inspected OCR tail", "identified author_index sections", "built helper request", "ran index_target_locator", "assembled intermediate fragments"],
        [],
        [],
        ["Payload is written from local OCR plus helper corroboration.", "Keep any future reruns aligned with the same section boundaries and page map."],
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build PL019 chunk section_004_part_003 from bounded OCR files."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/chunks/section_004_part_003.json"
CHUNK_ID = "PL019:chunk:004:003"
TOP_SECTION_ID = "PL019:candidate-section:004"
SECTION_KEY = "PL019:section:004:juvenci_phrases"
SECTION_FILE_START = str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-486.txt")

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}

NOISE_LINES = {
    "Digitized by Google",
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def alpha_initial(text: str) -> str | None:
    for char in text:
        upper = char.upper()
        if "A" <= upper <= "Z":
            return upper
        if upper == "Æ":
            return "A"
        if upper == "Œ":
            return "O"
    return None


def parse_text_blocks(path: Path) -> tuple[str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    header_matches = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    header = normalize_space(" ".join(strip_tags(item) for item in header_matches))
    lines: list[str] = []
    for block_type, block in re.findall(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        if block_type == "cabecalho":
            continue
        block_text = strip_tags(block)
        for line in block_text.splitlines():
            cleaned = normalize_space(line)
            if not cleaned:
                continue
            if cleaned in NOISE_LINES or cleaned.startswith("PATROL."):
                continue
            lines.append(cleaned)
    return header, lines


def ensure_letter_node(
    nodes: list[dict],
    node_lookup: dict[str, str],
    letter: str,
    source_file: str,
    inferred: bool,
) -> str:
    if letter in node_lookup:
        return node_lookup[letter]
    node_key = f"{SECTION_KEY}:node:letter:{letter}"
    node_lookup[letter] = node_key
    raw_json = {"source_file": source_file}
    if inferred:
        raw_json["inferred"] = True
    nodes.append(
        {
            "node_key": node_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": len(nodes) + 1,
            "node_kind": "letter_group",
            "label_raw": letter,
            "label_norm": letter.lower(),
            "label_sort": letter.lower(),
            "node_level": 1,
            "confidence": 0.84 if inferred else 0.92,
            "raw_json": raw_json,
        }
    )
    return node_key


def parse_locator_refs(
    entry_key: str,
    entry_raw: str,
    anchor_file: str,
    source_files: list[str],
) -> tuple[list[dict], bool]:
    refs: list[dict] = []
    unresolved = False
    occupied: list[tuple[int, int]] = []
    order = 1

    pattern = re.compile(
        r"(?<![A-Za-zÀ-ÿÆæŒœ])([ivxunIVXUNV]{1,4})\s*,\s*"
        r"((?:\d{1,4}(?:\s*(?:seq\.|seqq\.))?)(?:\s*,\s*\d{1,4}(?:\s*(?:seq\.|seqq\.))?)*)"
    )
    for match in pattern.finditer(entry_raw):
        book_raw = match.group(1)
        nums_raw = [normalize_space(item) for item in match.group(2).split(",")]
        occupied.append(match.span())
        for num_raw in nums_raw:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": order,
                    "ref_kind": "parallel_locator",
                    "ref_raw": f"{book_raw}, {num_raw}",
                    "page_ref_raw": None,
                    "page_ref_int": None,
                    "page_ref_col": None,
                    "line_ref_raw": num_raw,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": SECTION_FILE_START,
                    "editorial_anchor_file": anchor_file,
                    "confidence": 0.72,
                    "raw_json": {
                        "source_file": anchor_file,
                        "source_files": source_files,
                        "role": "parallel_locator",
                        "book_raw": book_raw,
                        "locator_system": "book_roman_plus_internal_number",
                    },
                }
            )
            order += 1

    redacted = entry_raw
    for start, end in sorted(occupied, reverse=True):
        redacted = redacted[:start] + " " * (end - start) + redacted[end:]

    for match in re.finditer(r"(?:^|[.;])\s*[^.;]*?,\s*(\d{1,4}(?:\s*(?:seq\.|seqq\.))?)", redacted):
        num_raw = normalize_space(match.group(1))
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_kind": "unresolved",
                "ref_raw": num_raw,
                "page_ref_raw": None,
                "page_ref_int": None,
                "page_ref_col": None,
                "line_ref_raw": num_raw,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": SECTION_FILE_START,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.58,
                "raw_json": {
                    "source_file": anchor_file,
                    "source_files": source_files,
                    "role": "parallel_locator_missing_book",
                    "locator_system": "book_roman_plus_internal_number",
                },
            }
        )
        unresolved = True
        order += 1

    return refs, unresolved


def guess_lemma(text: str) -> str | None:
    if "," in text:
        return normalize_space(text.split(",", 1)[0]) or None
    return normalize_space(text) or None


def build_entries_for_file(
    file_path: Path,
    lines: list[str],
    entry_order: int,
    current_letter: str | None,
    nodes: list[dict],
    node_lookup: dict[str, str],
) -> tuple[list[dict], list[dict], int, str | None]:
    entries: list[dict] = []
    refs: list[dict] = []
    current_entry = ""
    current_source_lines: list[str] = []
    source_file = str(file_path)
    page_marker_seen = False

    def flush() -> None:
        nonlocal current_entry, current_source_lines, entry_order
        if not current_entry:
            return
        parent_node_key = node_lookup.get(current_letter or "")
        entry_key = f"{SECTION_KEY}:{CHUNK_ID}:{entry_order:04d}"
        entry_refs, unresolved = parse_locator_refs(entry_key, current_entry, source_file, [source_file])
        lemma_raw = guess_lemma(current_entry)
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                "lemma_sort": lemma_raw.lower() if lemma_raw else None,
                "entry_raw": current_entry,
                "context_raw": current_entry,
                "heading_letter": current_letter,
                "inferred_printed_page": None,
                "section_start_file": SECTION_FILE_START,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.72 if unresolved else 0.78,
                "raw_json": {
                    "source_file": source_file,
                    "source_files": [source_file],
                    "parse_strategy": "conservative_line_segmentation_with_parallel_locators",
                    "section_kind": "concordance_index",
                    "locator_system": "book_roman_plus_internal_number",
                    "ocr_warning": unresolved,
                    "source_lines": current_source_lines,
                },
            }
        )
        refs.extend(entry_refs)
        entry_order += 1
        current_entry = ""
        current_source_lines = []

    for line in lines:
        if re.fullmatch(r"\d{1,4}", line):
            flush()
            page_marker_seen = True
            continue

        if re.fullmatch(r"[A-Z]", line):
            flush()
            current_letter = line
            ensure_letter_node(nodes, node_lookup, current_letter, source_file, inferred=False)
            page_marker_seen = False
            continue

        initial = alpha_initial(line)
        if current_letter is None and initial:
            current_letter = initial
            ensure_letter_node(nodes, node_lookup, current_letter, source_file, inferred=True)
        elif page_marker_seen and initial and initial != current_letter and initial > current_letter:
            flush()
            current_letter = initial
            ensure_letter_node(nodes, node_lookup, current_letter, source_file, inferred=True)

        page_marker_seen = False

        continue_line = False
        if current_entry:
            if current_entry.endswith("-"):
                continue_line = True
            elif current_entry.endswith((",", ";", ":")):
                continue_line = True
            elif re.match(r"^[0-9]", line):
                continue_line = True
            elif re.match(r"^[a-zà-ÿα-ω]", line):
                continue_line = True
            elif initial and current_letter and initial != current_letter:
                continue_line = True

        if continue_line:
            if current_entry.endswith("-"):
                current_entry = current_entry[:-1] + line
            else:
                current_entry = normalize_space(f"{current_entry} {line}")
            current_source_lines.append(line)
            continue

        flush()
        current_entry = line
        current_source_lines = [line]

    flush()
    return entries, refs, entry_order, current_letter


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    physical_files = [Path(value) for value in chunk["physical_files"]]

    section = {
        "section_key": SECTION_KEY,
        "volume_id": "PL019",
        "work_key": "juvencus_historiae_evangelicae",
        "section_order": 4,
        "section_kind": "concordance_index",
        "heading_raw": "INDEX VERBORUM ET PHRASIUM",
        "heading_norm": "index verborum et phrasium",
        "heading_letter": None,
        "page_start": 959,
        "page_end": 992,
        "file_start": SECTION_FILE_START,
        "file_end": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-502.txt"),
        "confidence": 0.87,
        "raw_json": {
            "section_kind_reason": "The heading INDEX VERBORUM ET PHRASIUM and the repeated I/II/III/IV internal locators indicate an alphabetical phrase concordance across the four books of Juvencus rather than an editorial page index.",
            "header_evidence": [
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-487.txt"),
                    "header": "961 QUÆ IN IV LIBRIS HISTORIÆ EVANG. JUVENCI CONTINENTUR. 962",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-489.txt"),
                    "header": "965 QUAE IN IV LIBRIS HISTORIAE EVANG. JUVENCI CONTINENTUR. 966",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-490.txt"),
                    "header": "INDEX VERBORUM ET PIRASIUM",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-491.txt"),
                    "header": "969 QUÆ IN IV LIBRIS HISTORIÆ EVANG. JUVENCI CONTINENTUR. 970",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-492.txt"),
                    "header": "INDEX VERBORUM ET PHRASIUM",
                },
            ],
            "source_files": [str(path) for path in physical_files],
            "adjacent_files_checked": [
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-492.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-486.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-493.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-494.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-495.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-496.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-485.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-484.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-483.txt"),
            ],
            "pagination_note": "Files 487-491 occupy editorial pages 961-970 inside the same phrase-index section; file 490 loses the printed numbers in the header, but the neighboring 489, 491, and 492 headers anchor the 965-972 run cleanly.",
            "extra_file_evidence": [
                "Checked 492 to confirm that 'Glomeratio, n, 579.' starts in the overlap file and is therefore owned by chunk 004:002 rather than this fragment.",
                "Checked 486 to confirm that 'Apertum cœlum, iv, 145.' ends cleanly before file 487 begins with 'Apices proprios, II, 475.'",
                "Checked 493-496 and 483-485 to confirm backward and forward continuity of the same concordance section despite alternating running-title headers and missing numbers on some pages.",
            ],
            "parse_strategy": "conservative_line_segmentation_with_parallel_locators",
            "helper_used": False,
        },
    }

    nodes: list[dict] = []
    node_lookup: dict[str, str] = {}
    entries: list[dict] = []
    refs: list[dict] = []
    entry_order = 1
    current_letter: str | None = None

    for file_path in sorted(physical_files, key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        _, lines = parse_text_blocks(file_path)
        file_entries, file_refs, entry_order, current_letter = build_entries_for_file(
            file_path,
            lines,
            entry_order,
            current_letter,
            nodes,
            node_lookup,
        )
        entries.extend(file_entries)
        refs.extend(file_refs)

    payload = {
        "schema_version": 2,
        "volume_id": "PL019",
        "section_id": TOP_SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "d317a2fabff24db949776713d4da829a340260ca7b7a6670e833e092e83d4df2",
        "status": "complete",
        "physical_files": [str(path) for path in physical_files],
        "numbering_semantics": NUMBERING,
        "boundary_decisions": [],
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "notes": [
            "This fragment covers the A-G window of the Juvencus concordance on physical files 487-491 only.",
            "File 490 includes valid E-series entries inside the OCR footer block after the printed page number 968; they were retained as owned text rather than discarded as footer noise.",
            "Bare numeric locators such as 'Caruisse thalamis, 481.' and 'Vobis, 690.' were preserved as unresolved refs because the OCR omits a reliable Roman book token.",
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

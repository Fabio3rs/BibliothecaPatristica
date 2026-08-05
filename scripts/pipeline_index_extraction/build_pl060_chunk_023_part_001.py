#!/usr/bin/env python3
"""Build PL060 chunk section_023_part_001 from the bounded OCR file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/chunks/section_023_part_001.json"
CHUNK_ID = "PL060:chunk:023:001"
TOP_SECTION_ID = "PL060:candidate-section:023"
SECTION_KEY = TOP_SECTION_ID
SECTION_FILE = PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-544.txt"
SECTION_FILE_START = str(SECTION_FILE)

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}

NOISE_PREFIXES = ("Digitized by Google", "Patrol.")
SUSPICIOUS_BOOKS = {"1", "11", "m", "n", "n.", "I I", "I,I", "I.I", "u", "ui", "v", "in"}


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


def parse_text_blocks(path: Path) -> tuple[str, list[dict[str, object]]]:
    raw = path.read_text(encoding="utf-8")
    header_matches = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    header = normalize_space(" ".join(strip_tags(item) for item in header_matches))
    records: list[dict[str, object]] = []
    body_line_no = 0
    text_block_no = 0
    for block_type, block in re.findall(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        if block_type == "cabecalho":
            continue
        if block_type != "texto_principal":
            continue
        text_block_no += 1
        side = "left" if text_block_no == 1 else "right"
        raw_line_no = 0
        for raw_line in strip_tags(block).splitlines():
            raw_line_no += 1
            cleaned = normalize_space(raw_line)
            if not cleaned:
                continue
            if any(cleaned.startswith(prefix) for prefix in NOISE_PREFIXES):
                continue
            body_line_no += 1
            records.append(
                {
                    "text": cleaned,
                    "body_line_no": body_line_no,
                    "block_no": text_block_no,
                    "block_line_no": raw_line_no,
                    "side": side,
                }
            )
    return header, records


def ensure_letter_node(
    nodes: list[dict],
    node_lookup: dict[str, str],
    letter: str,
    source_file: str,
    source_line: int,
    source: str,
    inferred: bool,
    reason: str | None = None,
) -> str:
    if letter in node_lookup:
        return node_lookup[letter]
    node_key = f"{SECTION_KEY}:node:{letter}"
    raw_json = {
        "source_file": source_file,
        "source_line": source_line,
        "source": source,
    }
    if inferred:
        raw_json["inferred"] = True
    if reason:
        raw_json["reason"] = reason
    node_lookup[letter] = node_key
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
            "confidence": 0.9 if inferred else 0.99,
            "raw_json": raw_json,
        }
    )
    return node_key


GROUP_PATTERN = re.compile(
    r"(?<![A-Za-zÀ-ÿÆæŒœ])"
    r"(?P<book>III|VII|II|IN|UI|I\s*,\s*I|I\s*\.\s*I|I\s+I|11|1|I|S|M|N\.?|U|V)"
    r"\s*(?:[.,]|\s)\s*"
    r"(?P<numlist>\d{1,4}(?:\s*(?:seq\.|seqq\.))?(?:\s*,\s*\d{1,4}(?:\s*(?:seq\.|seqq\.))?)*)",
    flags=re.IGNORECASE,
)


def canonicalize_book(book_raw: str) -> str | None:
    compact = re.sub(r"[\s.]+", "", book_raw).upper()
    mapping = {
        "1": "I",
        "I": "I",
        "11": "II",
        "II": "II",
        "III": "III",
        "IN": "III",
        "M": "III",
        "N": "II",
        "S": "S",
        "U": "II",
        "UI": "III",
        "V": None,
        "VII": None,
        "I,I": "II",
        "I.I": "II",
    }
    return mapping.get(compact)


def parse_locator_refs(
    entry_key: str,
    entry_raw: str,
    anchor_file: str,
    source_line_range: list[int],
) -> tuple[list[dict], bool, list[str]]:
    refs: list[dict] = []
    occupied: list[tuple[int, int]] = []
    suspicious_books: list[str] = []
    order = 1

    for match in GROUP_PATTERN.finditer(entry_raw):
        book_raw = normalize_space(match.group("book"))
        numlist = [normalize_space(item) for item in match.group("numlist").split(",")]
        occupied.append(match.span())
        if book_raw.lower() in SUSPICIOUS_BOOKS and book_raw.lower() not in suspicious_books:
            suspicious_books.append(book_raw.lower())
        book_norm = canonicalize_book(book_raw)
        for num_raw in numlist:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": order,
                    "ref_kind": "parallel_locator",
                    "ref_raw": f"{book_raw}, {num_raw}" if not book_raw.endswith(".") else f"{book_raw} {num_raw}",
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
                    "confidence": 0.76 if book_raw.lower() in SUSPICIOUS_BOOKS else 0.84,
                    "raw_json": {
                        "source_file": anchor_file,
                        "source_files": [anchor_file],
                        "source_line": entry_raw,
                        "source_line_range": source_line_range,
                        "role": "parallel_locator",
                        "book_raw": book_raw,
                        "book_norm": book_norm,
                        "book_token_status": "ocr_variant" if book_raw.lower() in SUSPICIOUS_BOOKS else "canonical",
                        "locator_system": "book_roman_plus_internal_number",
                    },
                }
            )
            order += 1

    redacted = entry_raw
    for start, end in sorted(occupied, reverse=True):
        redacted = redacted[:start] + (" " * (end - start)) + redacted[end:]

    unresolved = False
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
                    "source_files": [anchor_file],
                    "source_line": entry_raw,
                    "source_line_range": source_line_range,
                    "role": "parallel_locator_missing_book",
                    "locator_system": "book_roman_plus_internal_number",
                },
            }
        )
        unresolved = True
        order += 1

    return refs, unresolved, suspicious_books


def guess_lemma(text: str) -> str | None:
    if "," in text:
        return normalize_space(text.split(",", 1)[0]) or None
    return normalize_space(text) or None


def build_entries_for_file(
    file_path: Path,
    records: list[dict[str, object]],
    entry_order: int,
    current_letter: str | None,
    nodes: list[dict],
    node_lookup: dict[str, str],
) -> tuple[list[dict], list[dict], int, str | None]:
    entries: list[dict] = []
    refs: list[dict] = []
    current_entry = ""
    current_source_lines: list[str] = []
    current_line_numbers: list[int] = []
    current_block = 1
    current_side = "left"
    source_file = str(file_path)
    page_marker_seen = False

    def flush() -> None:
        nonlocal current_entry, current_source_lines, current_line_numbers, entry_order, current_block, current_side
        if not current_entry:
            return
        parent_node_key = node_lookup.get(current_letter or "")
        entry_key = f"{SECTION_KEY}:{CHUNK_ID}:{entry_order:04d}"
        entry_refs, unresolved, suspicious_books = parse_locator_refs(
            entry_key,
            current_entry,
            source_file,
            current_line_numbers,
        )
        lemma_raw = guess_lemma(current_entry)
        confidence = 0.82 if unresolved or suspicious_books else 0.88
        raw_json = {
            "source_file": source_file,
            "source_files": [source_file],
            "source_block": current_block,
            "physical_page_side": current_side,
            "source_line_range": current_line_numbers,
            "source_lines": current_source_lines,
            "source_line": current_entry,
            "parse_strategy": "conservative_line_segmentation_with_parallel_locators",
            "section_kind": "concordance_index",
            "locator_system": "book_roman_plus_internal_number",
            "ocr_warning": bool(unresolved or suspicious_books),
        }
        if suspicious_books:
            raw_json["locator_token_variants"] = suspicious_books
        if unresolved:
            raw_json["ocr_warning_detail"] = "Bare numeric locator(s) without a stable book token were preserved as unresolved refs."
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
                "inferred_printed_page": 1059,
                "section_start_file": SECTION_FILE_START,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": confidence,
                "raw_json": raw_json,
            }
        )
        refs.extend(entry_refs)
        entry_order += 1
        current_entry = ""
        current_source_lines = []
        current_line_numbers = []

    for record in records:
        line = str(record["text"])
        body_line_no = int(record["body_line_no"])
        block_no = int(record["block_no"])
        side = str(record["side"])

        if re.fullmatch(r"\d{1,4}", line):
            flush()
            page_marker_seen = True
            continue

        if re.fullmatch(r"[A-Z]", line):
            flush()
            current_letter = line
            ensure_letter_node(
                nodes,
                node_lookup,
                current_letter,
                source_file,
                body_line_no,
                "explicit_letter_marker",
                inferred=False,
            )
            page_marker_seen = False
            continue

        initial = alpha_initial(line)
        if current_letter is None and initial:
            current_letter = initial
            ensure_letter_node(
                nodes,
                node_lookup,
                current_letter,
                source_file,
                body_line_no,
                "inferred_from_initial_entry_run",
                inferred=True,
            )
        elif page_marker_seen and initial and current_letter and initial != current_letter and initial > current_letter:
            flush()
            current_letter = initial
            ensure_letter_node(
                nodes,
                node_lookup,
                current_letter,
                source_file,
                body_line_no,
                "inferred_after_page_marker",
                inferred=True,
            )

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
            current_line_numbers.append(body_line_no)
            continue

        flush()
        current_entry = line
        current_source_lines = [line]
        current_line_numbers = [body_line_no]
        current_block = block_no
        current_side = side

    flush()
    return entries, refs, entry_order, current_letter


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    physical_files = [Path(value) for value in chunk["physical_files"]]

    header_540 = "4051 INDICES IN DRACONTIUM. 4052"
    header_541 = "1053 INDEX VERBORUM ET PHRASIUM. 1054"
    header_542 = "1055 INDICES IN DRACONTIUM. 1056"
    header_543 = "1057 INDEX VERBORUM ET PHRASIUM 1058"
    header_544 = "INDICES IN DRACONTIUM."
    header_545 = "1061 INDEX VERBORUM ET PHRAISUM. 1062"
    header_546 = "1033 INDICES IN DRACONTIUM. 1064"
    header_547 = "1065 INDEX VERBORUM ET PHRAISUM. 1066"
    header_548 = "1067 INDICES IN DRACONTIUM. 1068"

    section = {
        "section_key": SECTION_KEY,
        "section_id": TOP_SECTION_ID,
        "volume_id": "PL060",
        "section_order": 23,
        "section_kind": "concordance_index",
        "heading_raw": "INDICES IN DRACONTIUM.",
        "heading_norm": "indices in dracontium",
        "heading_letter": None,
        "page_start": 1059,
        "page_end": 1060,
        "file_start": SECTION_FILE_START,
        "file_end": SECTION_FILE_START,
        "confidence": 0.92,
        "raw_json": {
            "section_kind_reason": "The owned spread keeps only the generic running head `INDICES IN DRACONTIUM`, but adjacent files 543 and 545 print the explicit `INDEX VERBORUM ET PHRASIUM/PHRAISUM` title and all owned lines use the same Dracontius internal I/II/III/S locator system, so this page remains part of the verbal concordance.",
            "header_raw": header_544,
            "source_file": SECTION_FILE_START,
            "source_files": [SECTION_FILE_START],
            "adjacent_files_checked": [
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-548.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-547.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-546.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-545.txt"),
                SECTION_FILE_START,
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-543.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-542.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-541.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-540.txt"),
            ],
            "header_sequence_evidence": {
                "540_header": header_540,
                "541_header": header_541,
                "542_header": header_542,
                "543_header": header_543,
                "544_header": header_544,
                "545_header": header_545,
                "546_header": header_546,
                "547_header": header_547,
                "548_header": header_548,
            },
            "numbering_inference": "The owned file preserves only the generic running head. The surrounding sequence 1057-1058 on file 543, 1061-1062 on file 545, 1063-1064 on file 546, and 1065-1068 on files 547-548 establishes that the owned spread must be editorial pages 1059-1060.",
            "inspection_evidence": {
                "540_548_header_window_checked": True,
                "543_545_body_window_checked": True,
                "owned_file_body_checked_via_read_ocr_page_text": True,
                "raw_544_tail_checked": True,
            },
            "section_scope_note": "Only entries beginning on owned physical file 544 were emitted. File 543 ends the N run and the owned page opens the O run before an explicit `P` marker. File 545 continues the P run, but no 545 entry was re-emitted here; the damaged closing line `Tenebrans molem, 11, 34.` was retained inside the last P entry because the page never leaves the P run and the local search found no independent T entry in this window.",
            "locator_system": "book_roman_plus_internal_number",
            "extra_investigation": {
                "files_checked": [
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-548.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-547.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-546.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-545.txt"),
                    SECTION_FILE_START,
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-543.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-542.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-541.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-540.txt"),
                ],
                "searches_checked": [
                    "header sequence review across physical files 540-548",
                    "rg -n -S 'Tenebrans molem|enebrans molem|Penebrans molem|Penetrat caput|Peuetrat caput|Tenebrans|Penes te' /homessddata/Projects/pdfocr/teste/PL060/text",
                    "raw source review of the tail of file 544 with line numbers",
                ],
                "reason": "Used to infer the missing 1059-1060 page numbers, confirm that file 543 closes the N run while file 545 continues P, and document the damaged final line that would otherwise look like a spurious T entry."
            },
        },
    }

    nodes: list[dict] = []
    node_lookup: dict[str, str] = {}
    entries: list[dict] = []
    refs: list[dict] = []
    entry_order = 1
    current_letter: str | None = None
    all_records: list[dict[str, object]] = []

    for file_path in sorted(physical_files, key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        _, records = parse_text_blocks(file_path)
        all_records.extend(records)
        file_entries, file_refs, entry_order, current_letter = build_entries_for_file(
            file_path,
            records,
            entry_order,
            current_letter,
            nodes,
            node_lookup,
        )
        entries.extend(file_entries)
        refs.extend(file_refs)

    for entry in entries:
        if "Tenebrans molem" in entry["entry_raw"]:
            entry["confidence"] = 0.78
            entry["raw_json"]["ocr_warning"] = True
            entry["raw_json"]["ocr_warning_detail"] = (
                "The final OCR line begins with `T`, but the owned page remains inside the P run and neighboring files show no T heading here; this tail was preserved as damaged OCR within the last P entry."
            )
            entry["raw_json"]["tail_line_review"] = {
                "search_hits": [
                    "file 544 line 181: Tenebrans molem, 11, 34.",
                    "file 545 line 6: Peuetrat caput, n, 41.",
                ],
                "decision": "kept_with_previous_p_entry",
            }
            break

    wrapped_entries = sum(1 for entry in entries if len(entry["raw_json"]["source_line_range"]) > 1)

    payload = {
        "schema_version": 2,
        "volume_id": "PL060",
        "section_id": TOP_SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "f278c19b6e3ed05b67598ae087e046ecd87bdd030b5806d141d93335ad382af9",
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
            f"The owned file yields {len(all_records)} non-empty OCR body lines including the explicit `P` marker; {len(entries)} concordance entries were serialized after joining {wrapped_entries} wrapped continuations.",
            "The generic running head on file 544 was retained in `heading_raw`, but the editorial spread was inferred as 1059-1060 from the surrounding 540-548 header sequence because the owned file omits both printed page numbers.",
            "Damaged book tokens such as `1`, `11`, `n`, and `in` were preserved in `ref_raw`; their best local interpretations were recorded in `book_norm` only when the Dracontius concordance pattern made the mapping defensible.",
            "The closing OCR line `Tenebrans molem, 11, 34.` was not promoted to a new T entry; it was kept as damaged tail text inside the final P entry because the local alphabetical sequence and neighboring files do not support a T boundary on this page."
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

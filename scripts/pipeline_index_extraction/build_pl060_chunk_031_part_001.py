#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl060_chunk_031_part_001.py
"""Build PL060 chunk section_031_part_001 from the bounded OCR file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL060/chunks/section_031_part_001.json"
CHUNK_ID = "PL060:chunk:031:001"
TOP_SECTION_ID = "PL060:candidate-section:031"
SECTION_KEY = TOP_SECTION_ID
SECTION_FILE = PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-536.txt"
SECTION_FILE_START = str(SECTION_FILE)
CONTEXT_D_FILE = str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-535.txt")

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}

NOISE_PREFIXES = ("Digitized by Google", "Patrol.")
LOCATOR_TOKEN_PATTERN = re.compile(
    r"(?P<ibid>\bibid\.)|"
    r"(?<![A-Za-zÀ-ÿÆæŒœ])"
    r"(?P<book>III|VII|II|IN|UI|I\s*,\s*I|I\s*\.\s*I|I\s+I|11|1|I|S|M|N\.?|U|V)"
    r"\s*(?:[.,]|\s)\s*"
    r"(?P<numlist>\d{1,4}(?:\s*(?:seq\.|seqq\.))?(?:\s*,\s*\d{1,4}(?:\s*(?:seq\.|seqq\.))?)*)",
    flags=re.IGNORECASE,
)
SUSPICIOUS_BOOKS = {"m", "n", "n.", "1", "11", "i i", "i,i", "i.i", "u", "ui", "v", "in"}


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
    *,
    source_block: int | None = None,
    reason: str | None = None,
) -> str:
    if letter in node_lookup:
        return node_lookup[letter]
    node_key = f"{SECTION_KEY}:node:{letter}"
    raw_json: dict[str, object] = {
        "source_file": source_file,
        "source_line": source_line,
        "source": source,
    }
    if source_block is not None:
        raw_json["source_block"] = source_block
    if inferred:
        raw_json["inferred"] = True
    if reason:
        raw_json["reason"] = reason
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
            "confidence": 0.93 if inferred else 0.99,
            "raw_json": raw_json,
        }
    )
    node_lookup[letter] = node_key
    return node_key


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


def format_ref_raw(book_raw: str, num_raw: str) -> str:
    if book_raw.upper().rstrip(".") == "S":
        return f"S. {num_raw}"
    if book_raw.endswith("."):
        return f"{book_raw} {num_raw}"
    return f"{book_raw}, {num_raw}"


def parse_locator_refs(
    entry_key: str,
    entry_raw: str,
    anchor_file: str,
    source_line_range: list[int],
) -> tuple[list[dict], bool, list[str]]:
    refs: list[dict] = []
    order = 1
    unresolved = False
    suspicious_books: list[str] = []
    previous_ref: dict | None = None
    occupied: list[tuple[int, int]] = []

    for match in LOCATOR_TOKEN_PATTERN.finditer(entry_raw):
        occupied.append(match.span())
        if match.group("ibid"):
            ref = {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_kind": "parallel_locator",
                "ref_raw": "ibid.",
                "page_ref_raw": None,
                "page_ref_int": None,
                "page_ref_col": None,
                "line_ref_raw": previous_ref["line_ref_raw"] if previous_ref else None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": SECTION_FILE_START,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.72 if previous_ref else 0.55,
                "raw_json": {
                    "source_file": anchor_file,
                    "source_files": [anchor_file],
                    "source_line": entry_raw,
                    "source_line_range": source_line_range,
                    "role": "parallel_locator_ibid",
                    "locator_system": "book_roman_plus_internal_number",
                },
            }
            if previous_ref:
                ref["raw_json"]["ibid_resolves_to"] = {
                    "ref_raw": previous_ref["ref_raw"],
                    "book_raw": previous_ref["raw_json"].get("book_raw"),
                    "book_norm": previous_ref["raw_json"].get("book_norm"),
                    "line_ref_raw": previous_ref["line_ref_raw"],
                }
            else:
                unresolved = True
                ref["raw_json"]["reason"] = "ibid. appeared without a previous local locator in the same entry."
            refs.append(ref)
            previous_ref = ref
            order += 1
            continue

        book_raw = normalize_space(match.group("book"))
        numlist = [normalize_space(item) for item in match.group("numlist").split(",")]
        book_key = book_raw.lower()
        if book_key in SUSPICIOUS_BOOKS and book_key not in suspicious_books:
            suspicious_books.append(book_key)
        book_norm = canonicalize_book(book_raw)
        for num_raw in numlist:
            ref = {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_kind": "parallel_locator",
                "ref_raw": format_ref_raw(book_raw, num_raw),
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
                "confidence": 0.76 if book_key in SUSPICIOUS_BOOKS else 0.84,
                "raw_json": {
                    "source_file": anchor_file,
                    "source_files": [anchor_file],
                    "source_line": entry_raw,
                    "source_line_range": source_line_range,
                    "role": "parallel_locator",
                    "book_raw": book_raw,
                    "book_norm": book_norm,
                    "book_token_status": "ocr_variant" if book_key in SUSPICIOUS_BOOKS else "canonical",
                    "locator_system": "book_roman_plus_internal_number",
                },
            }
            refs.append(ref)
            previous_ref = ref
            order += 1

    redacted = entry_raw
    for start, end in sorted(occupied, reverse=True):
        redacted = redacted[:start] + (" " * (end - start)) + redacted[end:]
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
                    "role": "unresolved_locator",
                    "locator_system": "book_roman_plus_internal_number",
                    "reason": "Bare numeric locator without a stable preceding book token was preserved unresolved.",
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
) -> tuple[list[dict], list[dict], int]:
    entries: list[dict] = []
    refs: list[dict] = []
    current_entry = ""
    current_source_lines: list[str] = []
    current_line_numbers: list[int] = []
    current_block = 1
    current_side = "left"
    source_file = str(file_path)

    def inferred_printed_page(side: str) -> int:
        return 1043 if side == "left" else 1044

    def flush() -> None:
        nonlocal current_entry, current_source_lines, current_line_numbers, entry_order, current_block, current_side
        if not current_entry:
            return
        entry_key = f"{SECTION_KEY}:{CHUNK_ID}:{entry_order:04d}"
        entry_refs, unresolved, suspicious_books = parse_locator_refs(
            entry_key,
            current_entry,
            source_file,
            current_line_numbers,
        )
        lemma_raw = guess_lemma(current_entry)
        confidence = 0.82 if unresolved or suspicious_books else 0.88
        raw_json: dict[str, object] = {
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
            raw_json["locator_token_note"] = (
                "The OCR page visibly renders several II/III tokens as `n`/`m`; these literals were preserved."
            )
        if unresolved:
            raw_json["ocr_warning_detail"] = "One or more locators remained unresolved after preserving the OCR literal."
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": node_lookup.get(current_letter or ""),
                "entry_order": entry_order,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                "lemma_sort": lemma_raw.lower() if lemma_raw else None,
                "entry_raw": current_entry,
                "context_raw": current_entry,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_printed_page(current_side),
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
                source_block=block_no,
            )
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
                source_block=block_no,
            )

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
    return entries, refs, entry_order


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    input_fingerprint = str(chunk["input_fingerprint"])

    header_raw, records = parse_text_blocks(SECTION_FILE)
    section = {
        "section_key": SECTION_KEY,
        "section_id": TOP_SECTION_ID,
        "volume_id": "PL060",
        "section_order": 31,
        "section_kind": "concordance_index",
        "heading_raw": header_raw,
        "heading_norm": "index verborum et phrasium",
        "heading_letter": None,
        "page_start": 1043,
        "page_end": 1044,
        "file_start": SECTION_FILE_START,
        "file_end": SECTION_FILE_START,
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "The owned spread keeps a badly corrupted running head `4013 INDICES IN DRACONTIUM. VOL. I`, but neighboring files 535 and 537 print the clean `INDEX VERBORUM ET PHRASIUM` title with consecutive pagination 1041-1042 and 1045-1046. The body uses the same Dracontius internal I/II/III/S locator system, so the page remains part of the phrase concordance rather than a separate index.",
            "header_raw": header_raw,
            "source_file": SECTION_FILE_START,
            "source_files": [SECTION_FILE_START],
            "adjacent_files_checked": [
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-540.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-539.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-538.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-537.txt"),
                SECTION_FILE_START,
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-535.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-534.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-533.txt"),
                str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-532.txt"),
            ],
            "header_sequence_evidence": {
                "532_header_raw": "1035 INDICES IN DRACONTIUM. 1056",
                "533_header_raw": "1037 INDEX VERBORUM ET PHRASIUM 1038",
                "534_header_raw": "1039 INDICES IN DRACONTIUM. 1040",
                "535_header": "1041 INDEX VERBORUM ET PHRASIUM. 1042",
                "536_header_raw": header_raw,
                "537_header": "1045 INDEX VERBORUM ET PHRASIUM. 1046",
                "538_header_raw": "1247 INDICES IN DRACONTIUM. 1018",
                "539_header": "1049 INDEX VERBORUM ET PHRASIUM. 1050",
            },
            "numbering_inference": "Because file 535 is the 1041-1042 spread and file 537 is the 1045-1046 spread, the owned file 536 must carry editorial pages 1043-1044 even though the OCR reads `4013 ... VOL. I`.",
            "inspection_evidence": {
                "532_540_context_window_checked": True,
                "535_536_537_boundary_window_checked": True,
                "line_numbered_raw_536_checked": True,
                "volume_rg_header_search_checked": True,
                "deus_auctor_ibid_line_checked": True,
            },
            "section_scope_note": "Only entries beginning on owned physical file 536 were emitted. The opening D-run inherits its letter from the explicit marginal `D` marker on file 535, and the right-page `E` marker on file 536 starts the short closing E-run that ends before file 537 opens a fresh E entry.",
            "locator_system": "book_roman_plus_internal_number",
            "letter_markers": {
                "D": "explicit marginal marker on context file 535 and continued on the owned file",
                "E": "explicit marker on the owned file 536",
            },
            "extra_investigation": {
                "files_checked": [
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-540.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-539.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-538.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-537.txt"),
                    SECTION_FILE_START,
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-535.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-534.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-533.txt"),
                    str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-532.txt"),
                ],
                "searches_checked": [
                    "read_ocr_page_text body/block review for files 532-540",
                    "rg -n -S 'INDEX VERBORUM ET PHRASIUM|INDICES IN DRACONTIUM' teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-53{2,3,4,5,6,7,8,9,0}.txt",
                    "rg -n -S 'Nunquam maculabilis|Ubique clarus|Deus auctor Dominusque' teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-536.txt",
                ],
                "reason": "Used to anchor the corrupted header inside the 1041-1050 sequence, confirm the D/E transition, and keep the local `ibid.` in the long `Deus auctor Dominusque` entry explicit instead of silently normalizing it away.",
            },
        },
    }

    nodes: list[dict] = []
    node_lookup: dict[str, str] = {}
    ensure_letter_node(
        nodes,
        node_lookup,
        "D",
        CONTEXT_D_FILE,
        1,
        "explicit_letter_marker_from_context",
        inferred=True,
        source_block=8,
        reason="File 535 prints an explicit marginal `D` marker immediately before the owned file, and file 536 continues only D-entries until the explicit E marker on its right page.",
    )

    entries, refs, _ = build_entries_for_file(
        SECTION_FILE,
        records,
        1,
        "D",
        nodes,
        node_lookup,
    )

    fragment = {
        "schema_version": 2,
        "volume_id": "PL060",
        "section_id": TOP_SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": input_fingerprint,
        "status": "complete",
        "physical_files": [SECTION_FILE_START],
        "numbering_semantics": NUMBERING,
        "boundary_decisions": [
            {
                "physical_left_file": CONTEXT_D_FILE,
                "physical_right_file": SECTION_FILE_START,
                "decision": "not_same_entry",
                "reason": "Context file 535 ends with `Defleverat, II 664.` while the owned file begins a fresh D-entry `Deflevit funera, m, 392.`; no cross-page continuation is supported.",
            },
            {
                "physical_left_file": SECTION_FILE_START,
                "physical_right_file": str(PROJECT_ROOT / "teste/PL060/text/7b581ab9-9247-465f-8153-307a7d4f29ff-537.txt"),
                "decision": "not_same_entry",
                "reason": "The owned file closes with `Eductis fetibus, I, 206.` after the explicit E marker, and file 537 starts a distinct E-entry `E-luxit minor, III, 144. Sub sole novo, I, 177.` rather than continuing the same line.",
            },
        ],
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "notes": [
            "File 536 belongs to the same Dracontius phrase concordance as files 533-539 despite the corrupted running head.",
            "The OCR visibly renders several II/III locator tokens as `n`/`m`; those literals were preserved in entries and refs instead of silently normalizing them.",
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(fragment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

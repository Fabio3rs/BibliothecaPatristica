#!/usr/bin/env python3
"""Build PL019 chunk section_003_part_002 from bounded OCR files."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment

WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/chunks/section_003_part_002.json"
CHUNK_ID = "PL019:chunk:003:002"
TOP_SECTION_ID = "PL019:candidate-section:003"

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}

CONTINUATION_PREFIXES = {
    "vide",
    "ibid",
    "et",
    "ejus",
    "ejusdem",
    "ipsius",
    "quibus",
    "quarum",
    "quod",
    "quam",
    "qui",
    "quæ",
    "quae",
    "quonam",
    "quibusnam",
    "quibusque",
    "in",
    "non",
    "sed",
    "vel",
    "seu",
    "fortasse",
    "florentinus",
    "notatur",
    "rejicitur",
    "refellitur",
    "varia",
    "vita",
    "vitæ",
    "vetus",
    "de",
    "ad",
    "an",
    "au",
    "editor",
    "manichæus",
    "stapulensis",
    "joann.",
    "quid",
    "cum",
    "partem",
    "sæpius",
    "prima",
    "secunda",
    "malorum",
    "sepulti",
    "unguento",
    "ipsorum",
    "quot",
    "seq.",
}

INTRO_PATTERNS = (
    "INDEX RERUM ET NOMINUM",
    "Quae in Carminibus Juvenci, Prolegomenis, et notis continentur.",
    "Numeri respondent seriei crassiorum numerorum qui textui inseruntur a col. 9 hujus tomi usque ad 346.",
)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def parse_text_blocks(path: Path) -> tuple[str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    header_matches = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    header = normalize_space(" ".join(strip_tags(item) for item in header_matches))
    lines: list[str] = []
    block_matches = re.findall(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    for block_type, block in block_matches:
        if block_type not in {"texto_principal", "nota_marginal"}:
            continue
        block_text = strip_tags(block)
        for line in block_text.splitlines():
            cleaned = normalize_space(line)
            if not cleaned:
                continue
            if block_type == "nota_marginal" and not re.fullmatch(r"[A-Z]", cleaned):
                continue
            lines.append(cleaned)
    return header, lines


def split_dash_entries(text: str) -> list[str]:
    parts = [normalize_space(part) for part in re.split(r"\s+—\s+", text) if normalize_space(part)]
    return parts or [text]


def split_inline_entries(text: str) -> list[str]:
    pieces = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", text)
    if len(pieces) == 1:
        return [text]
    segments: list[str] = [normalize_space(pieces[0])]
    for piece in pieces[1:]:
        cleaned = normalize_space(piece)
        first = cleaned.split(" ", 1)[0].rstrip(".,;:").lower()
        if first in CONTINUATION_PREFIXES or not re.search(r"(?<![A-Za-z])\d{1,4}", segments[-1]):
            segments[-1] = normalize_space(f"{segments[-1]} {cleaned}")
        else:
            segments.append(cleaned)
    return segments


def should_continue(current: str, line: str) -> bool:
    if not current:
        return False
    if current.endswith("-"):
        return True
    if re.match(r"^[0-9]", line):
        return True
    if re.match(r"^[a-zà-ÿ]", line):
        return True
    first = line.split(" ", 1)[0].rstrip(".,;:").lower()
    if first in CONTINUATION_PREFIXES:
        return True
    if current.endswith(",") or current.endswith(";") or current.endswith(":"):
        return True
    return False


def first_page_number(text: str) -> int | None:
    match = re.search(r"(?<![A-Za-z])(\d{1,4})(?:\s*(?:seq\.|seqq\.))?", text)
    return int(match.group(1)) if match else None


def make_refs(entry_key: str, entry_raw: str, section_file: str, anchor_file: str, source_files: list[str]) -> list[dict]:
    refs: list[dict] = []
    for idx, match in enumerate(
        re.finditer(r"(?<![A-Za-z])(\d{1,4}(?:\s*(?:seq\.|seqq\.))?)", entry_raw),
        start=1,
    ):
        ref_raw = match.group(1)
        number_match = re.match(r"(\d{1,4})", ref_raw)
        page_ref_int = int(number_match.group(1)) if number_match else None
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": idx,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": section_file,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.85,
                "raw_json": {
                    "source_file": source_files[0],
                    "parse_strategy": "regex_number",
                    "source_files": source_files,
                },
            }
        )
    return refs


def guess_lemma(text: str) -> str:
    if "," in text:
        return normalize_space(text.split(",", 1)[0])
    if "." in text:
        return normalize_space(text.split(".", 1)[0])
    return normalize_space(text)


def build_entries(
    section_key: str,
    section_file: str,
    file_path: Path,
    lines: list[str],
    nodes: list[dict],
    node_lookup: dict[str, str],
    entry_start_order: int,
    current_letter: str | None,
    intro_mode: bool,
) -> tuple[list[dict], list[dict], int, str | None, bool]:
    entries: list[dict] = []
    refs: list[dict] = []
    current_entry = ""
    buffered_source_lines: list[str] = []
    source_files = [str(file_path)]
    source_file = source_files[0]

    def flush_entry() -> None:
        nonlocal current_entry, buffered_source_lines, entry_start_order
        if not current_entry:
            return
        final_segments: list[str] = []
        for inline_segment in split_inline_entries(current_entry):
            final_segments.extend(split_dash_entries(inline_segment))
        for segment_index, segment in enumerate(final_segments, start=1):
            lemma_raw = guess_lemma(segment)
            entry_kind = "cross_reference" if "vide" in segment.lower() and first_page_number(segment) is None else "lemma"
            entry_key = f"{section_key}:{CHUNK_ID}:{entry_start_order:04d}"
            parent_node_key = node_lookup.get(current_letter or "")
            inferred_page = first_page_number(segment)
            confidence = 0.9 if len(final_segments) == 1 else 0.82
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_start_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw or None,
                    "lemma_display": lemma_raw or None,
                    "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                    "lemma_sort": lemma_raw.lower() if lemma_raw else None,
                    "entry_raw": segment,
                    "context_raw": current_entry,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": section_file,
                    "editorial_anchor_file": source_file,
                    "target_file_best": source_file,
                    "confidence": confidence,
                    "raw_json": {
                        "source_file": source_file,
                        "source_files": source_files,
                        "parse_strategy": "line_based_with_digit_and_lowercase_continuations",
                        "source_lines": buffered_source_lines,
                        "split_segment_index": segment_index,
                    },
                }
            )
            refs.extend(make_refs(entry_key, segment, section_file, source_file, source_files))
            entry_start_order += 1
        current_entry = ""
        buffered_source_lines = []

    for line in lines:
        if intro_mode and line in INTRO_PATTERNS:
            continue
        if intro_mode and re.fullmatch(r"[A-Z]", line):
            intro_mode = False
        elif intro_mode:
            continue

        if re.fullmatch(r"[A-Z]", line):
            flush_entry()
            current_letter = line
            if current_letter not in node_lookup:
                node_key = f"{section_key}:node:letter:{current_letter}"
                node_lookup[current_letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter.lower(),
                        "label_sort": current_letter.lower(),
                        "node_level": 1,
                        "confidence": 0.9,
                        "raw_json": {},
                    }
                )
            continue

        if should_continue(current_entry, line):
            if current_entry.endswith("-"):
                current_entry = current_entry[:-1] + line
            else:
                current_entry = normalize_space(f"{current_entry} {line}")
            buffered_source_lines.append(line)
            continue

        flush_entry()
        current_entry = line
        buffered_source_lines = [line]

    flush_entry()
    return entries, refs, entry_start_order, current_letter, intro_mode


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    physical_files = [Path(value) for value in chunk["physical_files"]]

    section_key = "PL019:section:003:juvenci"
    section = {
        "section_key": section_key,
        "volume_id": "PL019",
        "work_key": None,
        "section_order": 4,
        "section_kind": "analytic_subject",
        "heading_raw": "993 QUÆ IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR. 994",
        "heading_norm": "quæ in carminibus juvenci prolegomenis et notis continentur",
        "heading_letter": None,
        "page_start": 993,
        "page_end": 1002,
        "file_start": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-503.txt"),
        "file_end": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-507.txt"),
        "confidence": 0.92,
        "raw_json": {
            "section_kind_reason": "Alphabetical subject/name index for Juvencus continuing the same INDEX RERUM ET NOMINUM section on the earlier A-M pages.",
            "header_evidence": [
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-503.txt"),
                    "header": "993 QUIE IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR. 994",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-504.txt"),
                    "header": "995 INDEX RERUM ET NOMINUM 996",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-505.txt"),
                    "header": "997 QUÆ IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR. 998",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-506.txt"),
                    "header": "999 INDEX RERUM ET NOMINUM 1000",
                },
                {
                    "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-507.txt"),
                    "header": "4001 QUÆ IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR. 4002",
                },
            ],
            "source_files": [str(path) for path in physical_files],
            "adjacent_files_checked": [
                str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-502.txt"),
                str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
            ],
            "pagination_note": "Files 503-506 establish the 993-1000 sequence; file 507's 4001/4002 header is a CER corruption and is inferred as editorial pages 1001-1002 from the local run.",
            "parse_strategy": "line_based_with_digit_and_lowercase_continuations",
        },
    }

    nodes: list[dict] = []
    node_lookup: dict[str, str] = {}
    entries: list[dict] = []
    refs: list[dict] = []
    entry_order = 1
    current_letter: str | None = None
    intro_mode = True

    for file_path in sorted(physical_files, key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        header, lines = parse_text_blocks(file_path)
        file_entries, file_refs, entry_order, current_letter, intro_mode = build_entries(
            section_key,
            section["file_start"],
            file_path,
            lines,
            nodes,
            node_lookup,
            entry_order,
            current_letter,
            intro_mode,
        )
        for entry in file_entries:
            entry["raw_json"]["header"] = header
        entries.extend(file_entries)
        refs.extend(file_refs)

    boundary_decisions = [
        {
            "physical_left_file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-502.txt"),
            "physical_right_file": str(PROJECT_ROOT / "teste/PL019/text/1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-503.txt"),
            "decision": "not_same_entry",
            "reason": "File 502 belongs to the preceding INDEX VERBORUM ET PHRASIUM with poem-book locators such as 'II, 28', while file 503 opens the independent Juvencus INDEX RERUM ET NOMINUM on letter A.",
        },
        {
            "physical_left_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-507.txt"),
            "physical_right_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
            "decision": "not_same_entry",
            "reason": "File 507 ends cleanly within the M-series at 'Matthæi (Christianus Frider.), 55.', and file 508 opens the next chunk on a fresh N-series entry 'Matthæus ad apostolatum vocatur, 171.' rather than a cross-page continuation.",
        },
    ]

    payload = {
        "schema_version": 2,
        "volume_id": "PL019",
        "section_id": TOP_SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "36d575b436ed7df46a13fc8781105790bfecf0321e8cee2fe46f9a92e68d4a60",
        "status": "complete",
        "physical_files": [str(path) for path in physical_files],
        "numbering_semantics": NUMBERING,
        "boundary_decisions": boundary_decisions,
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "notes": [
            "This fragment continues the same Juvencus alphabetical section already serialized in section_003_part_001, but for the earlier A-M pages only.",
            "The 4001/4002 header on file 507 was treated as CER corruption of editorial pages 1001-1002 based on the surrounding 993-1000 and 1003-1010 sequence.",
            "Adjacent files 502 and 508 were inspected to confirm both chunk boundaries and avoid re-emitting entries owned by the neighboring fragments.",
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

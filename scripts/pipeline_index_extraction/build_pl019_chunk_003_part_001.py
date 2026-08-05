#!/usr/bin/env python3
"""Build PL019 chunk section_003_part_001 from bounded OCR files."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment
WORKPLAN_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/workplan.json"
OUTPUT_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL019/chunks/section_003_part_001.json"
CHUNK_ID = "PL019:chunk:003:001"
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
    "flo­rentinus",
    "florentinus",
    "timuthensis",
    "crucis",
    "sedulii",
    "imitator",
    "notatur",
    "rejicitur",
    "refellitur",
    "varia",
    "vita",
    "vitæ",
    "veta",
    "vetus",
    "de",
    "ad",
    "an",
    "au",
}

INTRO_PATTERNS = (
    "INDEX",
    "RERUM, NOMINUM, ET VERBORUM",
    "Quae in Prolegomenis",
    "Numeri respondent",
    "usque ad 794.",
)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def parse_text_blocks(path: Path) -> tuple[str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    header_matches = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    text_matches = re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    header = normalize_space(" ".join(strip_tags(item) for item in header_matches))
    lines: list[str] = []
    for block in text_matches:
        for line in strip_tags(block).splitlines():
            cleaned = normalize_space(line)
            if cleaned:
                lines.append(cleaned)
    return header, lines


def split_dash_entries(text: str) -> list[str]:
    parts = [normalize_space(part) for part in re.split(r"\s+—\s+", text) if normalize_space(part)]
    return parts or [text]


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


def make_refs(entry_key: str, entry_raw: str, section_file: str, source_files: list[str]) -> list[dict]:
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
                "editorial_anchor_file": section_file,
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


def section_from_file_seq(file_seq: int) -> str:
    return "sedulii" if file_seq >= 512 else "juvenci"


def build_entries(
    section_key: str,
    section_file: str,
    file_path: Path,
    lines: list[str],
    nodes: list[dict],
    node_lookup: dict[tuple[str, str], str],
    entry_start_order: int,
) -> tuple[list[dict], list[dict], int]:
    entries: list[dict] = []
    refs: list[dict] = []
    current_letter: str | None = None
    current_entry = ""
    buffered_source_lines: list[str] = []
    source_file = str(file_path)

    def flush_entry() -> None:
        nonlocal current_entry, buffered_source_lines, entries, refs, entry_start_order
        if not current_entry:
            return
        for segment_index, segment in enumerate(split_dash_entries(current_entry), start=1):
            lemma_raw = guess_lemma(segment)
            entry_kind = "cross_reference" if "vide" in segment.lower() and first_page_number(segment) is None else "lemma"
            entry_key = f"{section_key}:{CHUNK_ID}:{entry_start_order:04d}"
            parent_node_key = node_lookup.get((section_key, current_letter or ""))
            inferred_page = first_page_number(segment)
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
                    "confidence": 0.9 if len(split_dash_entries(current_entry)) == 1 else 0.82,
                    "raw_json": {
                        "source_file": source_file,
                        "source_files": [source_file],
                        "parse_strategy": "line_based_with_digit_and_lowercase_continuations",
                        "source_lines": buffered_source_lines,
                        "split_segment_index": segment_index,
                    },
                }
            )
            refs.extend(make_refs(entry_key, segment, section_file, [source_file]))
            entry_start_order += 1
        current_entry = ""
        buffered_source_lines = []

    intro_mode = True
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
            if (section_key, current_letter) not in node_lookup:
                node_key = f"{section_key}:node:letter:{current_letter}"
                node_lookup[(section_key, current_letter)] = node_key
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
    return entries, refs, entry_start_order


def main() -> None:
    workplan = _read_json(WORKPLAN_PATH)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    physical_files = [Path(value) for value in chunk["physical_files"]]

    sections = [
        {
            "section_key": "PL019:section:003:sedulii",
            "volume_id": "PL019",
            "work_key": None,
            "section_order": 3,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM NOMINUM ET VERBORUM",
            "heading_norm": "index rerum nominum et verborum",
            "heading_letter": None,
            "page_start": 1011,
            "page_end": 1014,
            "file_start": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
            "file_end": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-513.txt"),
            "confidence": 0.91,
            "raw_json": {
                "section_kind_reason": "Introductory alphabetical index for Sedulius marked by the title INDEX RERUM NOMINUM ET VERBORUM and the explanatory line about Prolegomena, Operibus Sedulii, et Scholiis.",
                "header_evidence": [
                    {
                        "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
                        "header": "INDEX RERUM NOMINUM ET VERBORUM",
                    },
                    {
                        "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-513.txt"),
                        "header": "1013 QUÆ IN PROLEGOMENIS, OPERIBUS SEDULII, ETC., CONTINENTUR. 1014",
                    },
                ],
                "source_files": [
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-513.txt"),
                ],
                "adjacent_files_checked": [
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-514.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-511.txt"),
                ],
                "pagination_note": "Printed numbers are absent from file 512 but are inferred as 1011-1012 from neighboring headers 1009-1010 and 1013-1014.",
            },
        },
        {
            "section_key": "PL019:section:003:juvenci",
            "volume_id": "PL019",
            "work_key": None,
            "section_order": 4,
            "section_kind": "analytic_subject",
            "heading_raw": "1003 INDEX RERUM ET NOMINUM 1004",
            "heading_norm": "index rerum et nominum",
            "heading_letter": None,
            "page_start": 1003,
            "page_end": 1010,
            "file_start": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
            "file_end": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-511.txt"),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject/name index for Juvencus evidenced by running headers QUÆ IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR and the explicit 1003 INDEX RERUM ET NOMINUM 1004 heading.",
                "header_evidence": [
                    {
                        "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
                        "header": "1003 INDEX RERUM ET NOMINUM 1004",
                    },
                    {
                        "file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-511.txt"),
                        "header": "1009 QUAE IN CARMINIBUS JUVENCI, PROLEGOMENIS ET NOTIS CONTINENTUR. 1010",
                    },
                ],
                "source_files": [
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-509.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-510.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-511.txt"),
                ],
                "adjacent_files_checked": [
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-507.txt"),
                    str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
                ],
                "parse_strategy": "line_based_with_digit_and_lowercase_continuations",
            },
        },
    ]

    nodes: list[dict] = []
    node_lookup: dict[tuple[str, str], str] = {}
    entries: list[dict] = []
    refs: list[dict] = []
    entry_order = 1

    for file_path in sorted(physical_files, key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        header, lines = parse_text_blocks(file_path)
        file_seq = int(file_path.stem.rsplit("-", 1)[1])
        section_name = section_from_file_seq(file_seq)
        section_key = f"PL019:section:003:{section_name}"
        section_file = next(section["file_start"] for section in sections if section["section_key"] == section_key)
        file_entries, file_refs, entry_order = build_entries(
            section_key,
            section_file,
            file_path,
            lines,
            nodes,
            node_lookup,
            entry_order,
        )
        for entry in file_entries:
            entry["raw_json"]["header"] = header
        entries.extend(file_entries)
        refs.extend(file_refs)

    boundary_decisions = [
        {
            "physical_left_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-511.txt"),
            "physical_right_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
            "decision": "not_same_entry",
            "reason": "File 511 closes the Juvencus index on Z-entries, while file 512 restarts with the independent Sedulius title page and the A letter-group. The boundary is a section shift, not a cross-page entry continuation.",
        },
        {
            "physical_left_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
            "physical_right_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-513.txt"),
            "decision": "not_same_entry",
            "reason": "File 512 ends cleanly on the A-series with the cross-reference 'Avlia, vide Iferian.', and file 513 opens with the standalone letter heading B, followed by new B/C entries.",
        },
        {
            "physical_left_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-507.txt"),
            "physical_right_file": str(PROJECT_ROOT / "teste/PL019/text/28f9984f-b1b8-4590-a4cb-ec017c316d06-508.txt"),
            "decision": "not_same_entry",
            "reason": "The overlap-context file 507 ends the previous fragment before the current chunk opens its owned page 508 on 'Matthæus ad apostolatum vocatur'. No proven cross-page continuation starts in 507 and continues into 508.",
        },
    ]

    payload = {
        "schema_version": 2,
        "volume_id": "PL019",
        "section_id": TOP_SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "1ce72681a46e2b4f83f92d0d0b849552d65f0a4261c9aeb979a20b9cad85cbfd",
        "status": "complete",
        "physical_files": [str(path) for path in physical_files],
        "numbering_semantics": NUMBERING,
        "boundary_decisions": boundary_decisions,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "notes": [
            {
                "kind": "coverage_note",
                "text": "This fragment contains two adjacent alphabetical index sections grouped by the workplan into one chunk: the Juvencus index in files 508-511 and the Sedulius index in files 512-513.",
            }
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT_PATH, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()

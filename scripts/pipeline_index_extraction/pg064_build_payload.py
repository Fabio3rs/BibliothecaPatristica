#!/usr/bin/env python3
"""Build PG064 alphabetical-index payload from OCR files.

Usage:
  python scripts/pipeline_index_extraction/pg064_build_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG064/text \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG064_alphabetical_indices.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG064_helper_output.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List


INDEX_FILES = list(range(722, 729))
ORDO_FILES = [781, 782]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[\[\]{}()\"'`^~•·]", " ", value)
    value = re.sub(r"[^0-9a-zA-ZΑ-Ωα-ω\u0370-\u03ff]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_blocks(text: str, block_type: str) -> List[str]:
    pattern = re.compile(rf'<bloco tipo="{re.escape(block_type)}"[^>]*>(.*?)</bloco>', re.S)
    return [match.group(1) for match in pattern.finditer(text)]


def flatten_block(block: str) -> str:
    lines = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        lines.append(s)
    return " ".join(lines)


def split_entry_chunks(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Split on sentence-like boundaries while protecting short abbreviations.
    text = re.sub(r"\b([SI])\.\s+(?=[A-ZΑ-ΩΆΈΉΊΌΎΏ])", r"\1<<<PG064_ABBR>>>", text)
    text = re.sub(r"\bCap\.\s+(?=[IVXLC])", "Cap<<<PG064_CAP>>> ", text)
    sentinel = "<<<PG064_SPLIT>>>"
    text = re.sub(r"\.\s+(?=[\"'\(\[\]A-ZΑ-ΩΆΈΉΊΌΎΏ])", sentinel, text)
    text = text.replace("<<<PG064_ABBR>>>", ". ")
    text = text.replace("<<<PG064_CAP>>>", ".")
    raw_parts = [part.strip() for part in text.split(sentinel) if part.strip()]
    parts: List[str] = []
    i = 0
    while i < len(raw_parts):
        part = raw_parts[i]
        if part == "Cap" and i + 1 < len(raw_parts):
            parts.append("Cap. " + raw_parts[i + 1])
            i += 2
            continue
        parts.append(part)
        i += 1
    return parts


def clean_heading_letter(text: str) -> str | None:
    m = re.match(r'^[\"\'\(\[]*([A-ZΑ-Ω])\b', text)
    if m:
        return m.group(1)
    return None


def parse_page_numbers(text: str) -> List[int]:
    # Capture printed page numbers, ignoring roman numerals and edition labels.
    values: List[int] = []
    for match in re.finditer(r"(?<![A-Za-zΑ-Ωα-ω])(\d{1,4})(?=[,\.\]\s]|$)", text):
        n = int(match.group(1))
        if n not in values:
            values.append(n)
    return values


def extract_section_text(source_root: Path, file_num: int) -> tuple[str, str]:
    for path in sorted(source_root.glob("*.txt")):
        if path.name.endswith(f"-{file_num}.txt"):
            text = path.read_text(encoding="utf-8")
            blocks = extract_blocks(text, "texto_principal")
            return path.as_posix(), " ".join(flatten_block(block) for block in blocks)
    raise FileNotFoundError(file_num)


def build_entries(
    section_key: str,
    section_kind: str,
    file_nums: Iterable[int],
    source_root: Path,
    section_start_file: str,
    helper_lookup: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    refs: list[dict] = []
    entry_order = 1
    ref_orders: dict[str, int] = {}
    for file_num in file_nums:
        file_path, combined = extract_section_text(source_root, file_num)
        chunks = split_entry_chunks(combined)
        for chunk in chunks:
            # Keep section titles and obvious structural labels out of entries.
            if re.fullmatch(r"[A-ZΑ-Ω]\.?", chunk):
                continue
            if re.match(r"^(ORDO RERUM|INDEX RERUM|SUPPLEMENTUM AD OPERA CHRYSOSTOMI|QUAE IN HOC TOMO CONTINENTUR\.?|FINIS TOMI|Proemium\.|Index totius operis\.)$", chunk):
                continue

            lemma_raw = chunk
            heading_letter = clean_heading_letter(chunk)
            page_nums = parse_page_numbers(chunk)
            inferred_printed_page = page_nums[-1] if page_nums else None
            entry_key = f"{section_key}_e{entry_order:04d}"
            confidence = 0.93
            if len(chunk) < 25 or chunk[0].islower():
                confidence = 0.82
            if helper_lookup and entry_key in helper_lookup:
                confidence = max(confidence, 0.95)

            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize_text(lemma_raw),
                    "lemma_sort": normalize_text(lemma_raw),
                    "entry_raw": chunk,
                    "context_raw": None,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": inferred_printed_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": file_path,
                    "target_file_best": file_path,
                    "confidence": confidence,
                    "raw_json": {
                        "source_file": file_path,
                        "file_num": file_num,
                        "section_kind": section_kind,
                        "split_strategy": "page_boundary_after_numeric_locator",
                        "page_numbers_detected": page_nums,
                        "helper_match": bool(helper_lookup and entry_key in helper_lookup),
                    },
                }
            )

            if page_nums:
                ref_orders[entry_key] = 1
                for idx, page_num in enumerate(page_nums, start=1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": idx,
                            "ref_kind": "editorial_page",
                            "ref_raw": str(page_num),
                            "page_ref_raw": str(page_num),
                            "page_ref_int": page_num,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": file_path,
                            "target_file_probability": 0.99,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": file_path,
                            "confidence": 0.95 if idx == 1 else 0.88,
                            "raw_json": {
                                "source_file": file_path,
                                "file_num": file_num,
                                "page_numbers_detected": page_nums,
                            },
                        }
                    )
            entry_order += 1
    return entries, refs


def build_sections(volume_id: str, source_root: str) -> list[dict]:
    return [
        {
            "section_key": f"{volume_id}_index_rerum",
            "volume_id": volume_id,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM",
            "heading_norm": "index rerum",
            "heading_letter": None,
            "page_start": 1319,
            "page_end": 1324,
            "file_start": f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-722.txt",
            "file_end": f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-728.txt",
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Subject index with alphabetical and Greek lemma groups.",
            },
        },
        {
            "section_key": f"{volume_id}_ordo_rerum",
            "volume_id": volume_id,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM",
            "heading_norm": "ordo rerum",
            "heading_letter": None,
            "page_start": 1425,
            "page_end": 1428,
            "file_start": f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-781.txt",
            "file_end": f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-782.txt",
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Closing contents / order-of-matter section with crosswalk-like incipits and table-of-contents items.",
            },
        },
    ]


def build_notes() -> list[str]:
    return [
        "PG064 contains an INDEX RERUM subject index in files 722-728 and an ORDO RERUM closing section in files 781-782.",
        "OCR segmentation is conservative and preserves literal page-locator strings; OCR line fragments were kept as separate entries when boundaries were not fully reliable.",
    ]


def load_helper_output(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lookup: dict[str, dict] = {}
    for item in data.get("entries", []):
        lookup[item.get("entry_id") or item.get("entry_key") or ""] = item
    return lookup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--helper-output", required=False)
    parser.add_argument("--helper-request", required=False)
    args = parser.parse_args()

    source_root = Path(args.source_root)
    output_path = Path(args.output)
    helper_output = Path(args.helper_output) if args.helper_output else None
    helper_lookup = load_helper_output(helper_output) if helper_output else {}

    volume_id = "PG064"
    sections = build_sections(volume_id, source_root.as_posix())

    index_section_key = f"{volume_id}_index_rerum"
    ordo_section_key = f"{volume_id}_ordo_rerum"

    index_entries, index_refs = build_entries(
        index_section_key,
        "analytic_subject",
        INDEX_FILES,
        source_root,
        sections[0]["file_start"],
        helper_lookup,
    )
    ordo_entries, ordo_refs = build_entries(
        ordo_section_key,
        "ordo_rerum",
        ORDO_FILES,
        source_root,
        sections[1]["file_start"],
        helper_lookup,
    )

    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "volume": {
            "volume_id": volume_id,
            "collection": "PG",
            "source_root": source_root.as_posix(),
            "volume_label": "PG064",
        },
        "sections": sections,
        "nodes": [],
        "entries": index_entries + ordo_entries,
        "refs": index_refs + ordo_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Index entries and closing order-of-matter items were recovered from the OCR files provided in the filtered tail window.",
            "evidence_files": [
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-722.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-723.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-724.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-725.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-726.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-727.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-728.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-781.txt",
                f"{source_root}/6f20648f-fa9e-423c-88e4-4d58591fc22b-782.txt",
            ],
        },
        "notes": build_notes(),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

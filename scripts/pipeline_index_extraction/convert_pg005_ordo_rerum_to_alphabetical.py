"""Convert the existing PG005 closure extraction into the alphabetical payload schema.

Usage:
  python scripts/pipeline_index_extraction/convert_pg005_ordo_rerum_to_alphabetical.py \
    --source data/index_payloads/PG005_indices.json \
    --output data/alphabetical_index_payloads/PG005_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def ascii_norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\n", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("œ", "oe").replace("æ", "ae")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def confidence_value(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        low = value.lower()
        if low == "high":
            return 0.96
        if low == "medium":
            return 0.86
        if low == "low":
            return 0.72
    return 0.9


def make_entry_key(section_key: str, order: int) -> str:
    return f"{section_key}:entry:{order:04d}"


def make_ref_raw(entry: dict) -> str | None:
    page_ref_raw = entry.get("page_ref_raw")
    if page_ref_raw:
        return str(page_ref_raw)
    page_ref_int = entry.get("page_ref_int")
    if page_ref_int is not None:
        return str(page_ref_int)
    return None


def build_payload(source_path: Path) -> dict:
    old = json.loads(source_path.read_text(encoding="utf-8"))
    sections_in = [
        section
        for section in old.get("sections", [])
        if section.get("section_key", "").startswith("PG005:volume_end:ordo_rerum:")
    ]
    sections_in.sort(key=lambda s: (s.get("page_start") or 0, s.get("file_start") or ""))

    sections = []
    entries = []
    refs = []
    nodes = []

    for section_order, section in enumerate(sections_in, start=1):
        section_key = section["section_key"]
        heading_raw = section.get("heading_raw", "")
        section_obj = {
            "section_key": section_key,
            "volume_id": old["volume"]["volume_id"],
            "work_key": section.get("work_key"),
            "section_order": section_order,
            "section_kind": "ordo_rerum",
            "heading_raw": heading_raw,
            "heading_norm": ascii_norm(heading_raw),
            "heading_letter": None,
            "page_start": section.get("page_start"),
            "page_end": section.get("page_end"),
            "file_start": section.get("file_start"),
            "file_end": section.get("file_end"),
            "confidence": confidence_value(section.get("confidence")),
            "raw_json": {
                **section.get("raw_json", {}),
                "source_section_key": section_key,
                "legacy_scope_kind": section.get("scope_kind"),
            },
        }
        sections.append(section_obj)

        node_key = f"{section_key}:node:001"
        top_label = heading_raw.split("\n")[-1].strip() if heading_raw else section_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": 1,
                "node_kind": "heading_group",
                "label_raw": top_label,
                "label_norm": ascii_norm(top_label),
                "label_sort": ascii_norm(top_label),
                "node_level": 1,
                "confidence": section_obj["confidence"],
                "raw_json": {
                    "source_section_key": section_key,
                    "source_file_start": section.get("file_start"),
                },
            }
        )

        for entry_order, entry in enumerate(section.get("entries", []), start=1):
            page_ref_int = entry.get("page_ref_int")
            page_ref_raw = make_ref_raw(entry)
            entry_raw = entry.get("entry_raw")
            target_file = entry.get("target_file")
            entry_kind = "lemma" if page_ref_int is not None else "editorial_note"
            if page_ref_int is None and (entry_raw or "").strip().upper().startswith("APPENDIX"):
                entry_kind = "heading_group"

            entry_key = make_entry_key(section_key, entry_order)
            entry_obj = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": entry_raw,
                "lemma_display": entry_raw,
                "lemma_norm": ascii_norm(entry_raw),
                "lemma_sort": ascii_norm(entry_raw),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_ref_int,
                "section_start_file": section.get("file_start"),
                "editorial_anchor_file": section.get("file_start"),
                "target_file_best": target_file,
                "confidence": confidence_value(entry.get("confidence")),
                "raw_json": {
                    **(entry.get("raw_json") or {}),
                    "legacy_target_raw": entry.get("target_raw"),
                    "legacy_page_ref_raw": page_ref_raw,
                    "legacy_page_ref_int": page_ref_int,
                    "legacy_target_file": target_file,
                    "legacy_source_section_key": section_key,
                },
            }
            entries.append(entry_obj)

            if page_ref_int is not None:
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_kind": "editorial_page",
                        "ref_raw": page_ref_raw,
                        "page_ref_raw": page_ref_raw,
                        "page_ref_int": page_ref_int,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": 1.0 if target_file else None,
                        "section_start_file": section.get("file_start"),
                        "editorial_anchor_file": section.get("file_start"),
                        "confidence": confidence_value(entry.get("confidence")),
                        "raw_json": {
                            "legacy_target_raw": entry.get("target_raw"),
                            "legacy_source_section_key": section_key,
                        },
                    }
                )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": old["volume"]["volume_id"],
            "collection": old["volume"]["collection"],
            "source_root": old["volume"]["source_root"],
            "volume_label": old["volume"]["volume_label"],
            "notes": "Transformed from the existing PG005 index extraction for the closing ORDO RERUM blocks.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": (
                "Recovered the closing ORDO RERUM subblocks from the existing PG005 index extraction and "
                "verified the OCR window in files 752, 754, 755, 756, 757, and 758."
            ),
            "evidence_files": [
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-752.txt",
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-754.txt",
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-755.txt",
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-756.txt",
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-757.txt",
                "/homessddata/Projects/pdfocr/teste/PG005/text/b11ce1c5-209a-4aea-8893-7a1a7ff43965-758.txt",
            ],
        },
        "notes": [
            "Helper locator was run on representative PG005 tail entries; the final payload keeps the more complete prior OCR-backed mapping from data/index_payloads/PG005_indices.json.",
            "OCR file suffixes, printed page numbers, and cited references remain separate in the payload.",
        ],
    }

    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = build_payload(Path(args.source))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

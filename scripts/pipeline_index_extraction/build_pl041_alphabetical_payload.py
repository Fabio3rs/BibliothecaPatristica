"""Usage: rebuild the PL041 alphabetical payload from the two validated chunk files.

Run:
  python scripts/pipeline_index_extraction/build_pl041_alphabetical_payload.py \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL041_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHUNK_FILES = [
    ROOT / "data" / "intermediate_payloads" / "PL041" / "chunks" / "section_001_part_002.json",
    ROOT / "data" / "intermediate_payloads" / "PL041" / "chunks" / "section_001_part_001.json",
]
DEFAULT_OUTPUT_FILE = ROOT / "data" / "alphabetical_index_payloads" / "PL041_alphabetical_indices.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_payload() -> dict:
    chunks = [load_json(path) for path in CHUNK_FILES]

    sections = [deepcopy(chunks[0]["sections"][0])]
    nodes = []
    entries = []
    refs = []
    scripture_refs = []

    expected_entry_order = 1
    for chunk in chunks:
        chunk_entries = chunk["entries"]
        chunk_refs = chunk["refs"]
        if len(chunk_entries) != len(chunk_refs):
            raise SystemExit(
                f"Chunk {chunk.get('chunk_id')} has {len(chunk_entries)} entries but {len(chunk_refs)} refs"
            )

        for entry, ref in zip(chunk_entries, chunk_refs):
            new_entry = deepcopy(entry)
            new_entry["entry_key"] = f"PL041:entry:{expected_entry_order:04d}"
            new_entry["entry_order"] = expected_entry_order
            entries.append(new_entry)

            new_ref = deepcopy(ref)
            new_ref["entry_key"] = new_entry["entry_key"]
            if not new_ref.get("ref_raw"):
                new_ref["ref_raw"] = "ibid."
                new_ref["page_ref_raw"] = "ibid."
                if new_entry.get("inferred_printed_page") is not None:
                    new_ref["page_ref_int"] = new_entry["inferred_printed_page"]
                new_ref.setdefault("raw_json", {})
                new_ref["raw_json"]["page_ref_strategy"] = "inherited_ibid_fallback"
            refs.append(new_ref)

            expected_entry_order += 1

        scripture_refs.extend(deepcopy(chunk.get("scripture_refs", [])))

    if expected_entry_order != 632:
        raise SystemExit(f"Unexpected merged entry count: {expected_entry_order - 1}")

    volume = {
        "volume_id": "PL041",
        "collection": "PL",
        "source_root": "/homessddata/Projects/pdfocr/teste/PL041/text",
        "volume_label": "PL041",
        "notes": (
            "Final volume index block identified as INDEX RERUM. The section is editorially an "
            "ordo rerum / contents index rather than a strict alphabetical index, but it is the "
            "closing index payload for the volume."
        ),
    }

    coverage = {
        "entries_status": "partial_extracted",
        "entries_status_reason": (
            "Recovered the closing INDEX RERUM section from OCR files 431-440, including the "
            "last index item before the appendix body begins. A few target pages required manual "
            "front-matter mapping or nearest-page fallback where the header map was incomplete."
        ),
        "evidence_files": [
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-431.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-432.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-433.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-434.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-435.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-436.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-437.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-438.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-439.txt",
            "/homessddata/Projects/pdfocr/teste/PL041/text/3ada7f44-d776-4de3-b3d2-c1a5232c3459-440.txt",
        ],
    }

    notes = [
        "The section is INDEX RERUM / ordo rerum, not alphabetical, so heading_group entries and lemma entries coexist in a contents-like sequence.",
        "The appendix body starting after APPENDIX TOMI SEPTIMI OPERUM S. AUGUSTINI. 803-806 was excluded from the index payload.",
        "Target-file anchors rely on a mix of header-map lookup, manual front-matter mapping for pages 9-16, and nearest-page fallback where the OCR header map had gaps.",
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL041 alphabetical index payload.")
    ap.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Destination JSON payload path.",
    )
    args = ap.parse_args()

    payload = build_payload()
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with args.output_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()

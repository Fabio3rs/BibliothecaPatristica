#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/assemble_pl119_final_payload.py

Build the canonical PL119 alphabetical-index payload from the validated
assembled_fragments.json checkpoint and write it to the final output path.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
ASSEMBLED = ROOT / "data/intermediate_payloads/PL119/assembled_fragments.json"
OUTPUT = ROOT / "data/alphabetical_index_payloads/PL119_alphabetical_indices.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    assembled = json.loads(ASSEMBLED.read_text())
    data = assembled["data"]

    payload = {
        "schema_version": assembled["schema_version"],
        "generated_at": utc_now(),
        "volume": {
            "volume_id": "PL119",
            "collection": "PL",
            "source_root": "/homessddata/Projects/pdfocr/teste/PL119/text",
            "volume_label": "Patrologia Latina 119",
        },
        "sections": copy.deepcopy(data["sections"]),
        "nodes": copy.deepcopy(data["nodes"]),
        "entries": copy.deepcopy(data["entries"]),
        "refs": copy.deepcopy(data["refs"]),
        "scripture_refs": copy.deepcopy(data["scripture_refs"]),
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": (
                "Recovered the alphabetical index continuation, the INDEX IN S. LUPUM section, "
                "and the closing contents table from the OCR tail with conservative line-based segmentation."
            ),
            "evidence_files": [
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-611.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-612.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-613.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-614.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-615.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-616.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-617.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-618.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-619.txt",
                "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-620.txt",
            ],
        },
        "notes": [
            "Sections are ordered from the physical end toward the beginning: ordo_rerum, INDEX IN S. LUPUM., then the main alphabetical index continuation.",
            "The 617/618 page break is a joined entry; OCR artifact 39 is not serialized as text.",
            "Printed-page anchors drift relative to the OCR suffixes; refs retain the literal citations and target files from the local volume sequence.",
        ],
    }

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

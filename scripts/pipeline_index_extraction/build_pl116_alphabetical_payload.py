#!/usr/bin/env python3
"""Usage: build the PL116 final alphabetical payload from the validated intermediate fragment.

Run:
  python scripts/pipeline_index_extraction/build_pl116_alphabetical_payload.py \
    --intermediate data/intermediate_payloads/PL116/assembled_fragments.json \
    --output data/alphabetical_index_payloads/PL116_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(intermediate_path: Path) -> dict[str, Any]:
    fragment = read_json(intermediate_path)
    data = fragment["data"]
    volume_id = fragment["volume_id"]
    source_root = "/homessddata/Projects/pdfocr/teste/PL116/text"

    payload = {
        "schema_version": fragment.get("schema_version", 1),
        "generated_at": utc_now(),
        "volume": {
            "volume_id": volume_id,
            "collection": volume_id[:2],
            "source_root": source_root,
            "volume_label": volume_id,
            "notes": [
                "Recovered the closing Ordo Rerum contents list at the end of the volume.",
                "Validated against OCR on files 542-543 and excluded the volume-closure marker from the index entries.",
            ],
        },
        "sections": data["sections"],
        "nodes": data["nodes"],
        "entries": data["entries"],
        "refs": data["refs"],
        "scripture_refs": data.get("scripture_refs", []),
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": (
                "The closing contents list is fully recovered from OCR files 542-543; "
                "the final material entry is In psalmum LXV. 447, and FINIS TOMI CENTESIMI "
                "DECIMI SEXTI. is excluded as a closure marker."
            ),
            "evidence_files": [
                "/homessddata/Projects/pdfocr/teste/PL116/text/504caeee-cc1a-4344-b704-5a6a5a20c17d-542.txt",
                "/homessddata/Projects/pdfocr/teste/PL116/text/504caeee-cc1a-4344-b704-5a6a5a20c17d-543.txt",
            ],
        },
        "notes": [
            "The previous payload checkpoint was verified against OCR and rebuilt from the validated fragment state.",
            "The opening commentary line 'Incipit commentarius in Psal-mos.' is represented structurally, not as a material index entry.",
        ],
    }

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL116 alphabetical payload from validated fragments.")
    ap.add_argument(
        "--intermediate",
        type=Path,
        default=Path("data/intermediate_payloads/PL116/assembled_fragments.json"),
        help="Validated intermediate fragment JSON path",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("data/alphabetical_index_payloads/PL116_alphabetical_indices.json"),
        help="Final payload output path",
    )
    args = ap.parse_args()

    payload = build_payload(args.intermediate)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

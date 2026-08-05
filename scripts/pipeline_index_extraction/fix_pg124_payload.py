#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/fix_pg124_payload.py

Apply the OCR-verified PG124 payload fixes:
- resolve line-break hyphen artifacts in heading nodes
- override target files where direct OCR headers beat helper drift
- refresh the intermediate todo checkpoint
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG124_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG124/todo.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def set_manual_override(obj: dict, *, target_file: str, confidence: float, header_excerpt: str, note: str) -> None:
    obj["target_file_best"] = target_file
    obj["confidence"] = confidence
    raw = obj.setdefault("raw_json", {})
    raw["manual_target_override"] = {
        "selected_file": target_file,
        "resolution_source": "direct_ocr_header",
        "header_excerpt": header_excerpt,
        "reason": note,
    }


def set_ref_override(obj: dict, *, target_file: str, confidence: float, header_excerpt: str, note: str) -> None:
    obj["target_file"] = target_file
    obj["target_file_probability"] = confidence
    obj["confidence"] = confidence
    raw = obj.setdefault("raw_json", {})
    raw["manual_target_override"] = {
        "selected_file": target_file,
        "resolution_source": "direct_ocr_header",
        "header_excerpt": header_excerpt,
        "reason": note,
    }


def main() -> None:
    now = utc_now()
    payload = json.loads(PAYLOAD_PATH.read_text())

    payload["generated_at"] = now

    node0 = next(node for node in payload["nodes"] if node["node_key"] == "PG124:node:theophylactus-bulgariae-archipriscopus")
    node0["label_raw"] = "THEOPHYLACTUS BULGARIÆ ARCHIPRISCOPUS."
    node0["label_norm"] = "theophylactus bulgariae archipriscopus"
    node0["label_sort"] = "theophylactus bulgariae archipriscopus"
    node0_raw = node0.setdefault("raw_json", {})
    node0_raw["ocr_lines_raw"] = deepcopy(node0_raw.get("ocr_lines", []))
    node0_raw["ocr_lines"] = ["THEOPHYLACTUS BULGARIÆ ARCHIPRISCOPUS."]
    node0_raw["linebreak_hyphen_resolved"] = True

    node1 = next(node for node in payload["nodes"] if node["node_key"] == "PG124:node:commentarius-in-joannis-evangelium-continuatio")
    node1["label_raw"] = "COMMENTARIUS IN JOANNIS EVANGELIUM. (Continuatio.)"
    node1["label_norm"] = "commentarius in joannis evangelium continuatio"
    node1["label_sort"] = "commentarius in joannis evangelium continuatio"
    node1_raw = node1.setdefault("raw_json", {})
    node1_raw["ocr_lines_raw"] = deepcopy(node1_raw.get("ocr_lines", []))
    node1_raw["ocr_lines"] = ["COMMENTARIUS IN JOANNIS EVANGELIUM. (Continuatio.)"]
    node1_raw["linebreak_hyphen_resolved"] = True

    payload["notes"] = [
        "The printed page numbers inside the contents table are cited references, not OCR file suffixes.",
        'The heading "COMMENTARIUS IN JOANNIS EVANGELIUM. (Continuatio.)" is preserved as a structural node because it has no material locator in this tail block.',
    ]

    entry_overrides = {
        "pg124_ordo_0007": {
            "target_file": "/homessddata/Projects/pdfocr/teste/PG124/text/df2975e6-2a49-4df7-b2ff-4c981e1c0379-301.txt",
            "confidence": 0.99,
            "header_excerpt": "565 EXPOSITIO IN EPIST. I AD COR. — CAP. I. 565",
            "note": "Direct OCR inspection confirmed printed page 565 on file 301 after the helper preferred neighboring file 300 by heading similarity.",
        },
        "pg124_ordo_0008": {
            "target_file": "/homessddata/Projects/pdfocr/teste/PG124/text/e7a09f34-ed0e-4653-a6c4-a50cccecf4b3-421.txt",
            "confidence": 0.99,
            "header_excerpt": "793 EXPOSITIO IN EPIST. I AD COR. — CAP. XVI. 794",
            "note": "Direct OCR inspection confirmed printed page 793 on file 421 after the helper drifted to file 423.",
        },
        "pg124_ordo_0010": {
            "target_file": "/homessddata/Projects/pdfocr/teste/PG124/text/e5ff63b2-1a81-4fa7-8bd3-9e481ea02a37-552.txt",
            "confidence": 0.99,
            "header_excerpt": "1051 THEOPHYLACTI BULGARIÆ ARCHIEP. 1052",
            "note": "Direct OCR inspection confirmed printed page 1051 on file 552 after the helper favored earlier files by body-heading carryover.",
        },
        "pg124_ordo_0011": {
            "target_file": "/homessddata/Projects/pdfocr/teste/PG124/text/0f938f88-4b2e-4601-b7b1-11351694feb7-610.txt",
            "confidence": 0.99,
            "header_excerpt": "1159 THEOPHYLACTI BULGARIÆ ARCHIEP: 1160",
            "note": "Direct OCR inspection confirmed printed page 1159 on file 610 after the helper preferred an earlier neighboring file.",
        },
    }

    for entry in payload["entries"]:
        if entry["entry_key"] in entry_overrides:
            override = entry_overrides[entry["entry_key"]]
            set_manual_override(entry, **override)

    for ref in payload["refs"]:
        if ref["entry_key"] in entry_overrides:
            override = entry_overrides[ref["entry_key"]]
            set_ref_override(ref, **override)

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    todo = json.loads(TODO_PATH.read_text())
    todo["updated_at"] = now
    todo["current_focus"] = "PG124 ORDO RERUM payload verified against OCR and corrected."
    todo["completed"] = [
        "inspected the final OCR tail",
        "confirmed the volume closes with ORDO RERUM rather than a separate alphabetical subject index",
        "reran the target locator helper",
        "resolved the heading line-break hyphen artifacts against OCR",
        "manually corrected drifted target files with direct OCR header evidence",
        "rewrote the final payload",
    ]
    todo["pending"] = []
    todo["blocked"] = []
    todo["notes"] = [
        "The section is editorial closure material and remains separate from the contents lines.",
        "Page numbers in the table are cited references, not OCR file suffixes.",
        "Direct OCR header checks overrode helper drift for entries 0007, 0008, 0010, and 0011.",
    ]
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

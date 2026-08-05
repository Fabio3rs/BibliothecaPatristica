#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/repair_pl015_fragment_refs.py

Restore the PL015 ref groups whose stable fragment objects were dropped or
renumbered in the final payload by replacing those entry-level ref slices with
their validated versions from assembled_fragments.json.
"""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PL015_alphabetical_indices.json"
FRAGMENTS_PATH = PROJECT_ROOT / "data/intermediate_payloads/PL015/assembled_fragments.json"

TARGET_ENTRY_KEYS = {
    "PL015:candidate-section:003:PL015:chunk:003:004:021",
    "PL015:candidate-section:003:PL015:chunk:003:004:024",
    "PL015:candidate-section:003:PL015:chunk:003:004:027",
    "PL015:candidate-section:003:PL015:chunk:003:004:060",
    "PL015:candidate-section:003:PL015:chunk:003:004:146",
    "PL015:candidate-section:004:PL015:chunk:004:001:004",
    "PL015:candidate-section:004:PL015:chunk:004:001:005",
}


def materialize_inherited_ibid_refs(refs: list[dict]) -> list[dict]:
    refs = sorted(refs, key=lambda r: (r["entry_key"], r["ref_order"]))
    grouped: dict[str, list[dict]] = {}
    for ref in refs:
        grouped.setdefault(ref["entry_key"], []).append(ref)

    repaired: list[dict] = []
    for entry_key, entry_refs in grouped.items():
        previous_material = None
        for ref in entry_refs:
            raw = (ref.get("ref_raw") or "").strip()
            lowered = raw.lower()
            if lowered.startswith("ibid"):
                repaired_ref = dict(ref)
                inherited = previous_material
                if inherited is not None:
                    repaired_ref["page_ref_raw"] = inherited.get("page_ref_raw")
                    repaired_ref["page_ref_int"] = inherited.get("page_ref_int")
                    repaired_ref["page_ref_col"] = inherited.get("page_ref_col")
                    repaired_ref["line_ref_raw"] = (
                        repaired_ref.get("line_ref_raw") or inherited.get("line_ref_raw")
                    )
                    if repaired_ref["ref_kind"] == "unresolved":
                        repaired_ref["ref_kind"] = inherited.get("ref_kind") or "editorial_page_line"
                    raw_json = dict(repaired_ref.get("raw_json") or {})
                    raw_json["inherited_material_anchor"] = {
                        "from_ref_order": inherited["ref_order"],
                        "page_ref_raw": inherited.get("page_ref_raw"),
                        "page_ref_int": inherited.get("page_ref_int"),
                        "page_ref_col": inherited.get("page_ref_col"),
                        "line_ref_raw": inherited.get("line_ref_raw"),
                        "ref_raw": inherited.get("ref_raw"),
                    }
                    raw_json["inheritance_note"] = (
                        "Material anchor inherited from the previous ref while preserving literal ibid. OCR."
                    )
                    repaired_ref["raw_json"] = raw_json
                repaired.append(repaired_ref)
            else:
                repaired.append(ref)

            if repaired[-1].get("page_ref_raw") or repaired[-1].get("page_ref_int") is not None:
                previous_material = repaired[-1]

    return repaired


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    fragments = json.loads(FRAGMENTS_PATH.read_text())["data"]["refs"]

    payload_refs = [r for r in payload["refs"] if r["entry_key"] not in TARGET_ENTRY_KEYS]
    fragment_refs = [r for r in fragments if r["entry_key"] in TARGET_ENTRY_KEYS]
    payload["refs"] = materialize_inherited_ibid_refs(payload_refs + fragment_refs)

    notes = payload.setdefault("notes", [])
    repair_note = (
        "2026-07-28: Restored seven stable ref objects and original ref_order "
        "slices for affected PL015 entries directly from assembled_fragments.json, "
        "materializing inherited ibid. anchors in raw_json without changing OCR literals."
    )
    if repair_note not in notes:
        notes.append(repair_note)

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

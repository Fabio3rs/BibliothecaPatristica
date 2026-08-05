#!/usr/bin/env python3
"""Update unresolved PG105 target_file_best anchors using OCR-backed local evidence.

Usage:
  python scripts/pipeline_index_extraction/update_pg105_null_targets.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG105_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG105/entries.json"
TODO = ROOT / "data/intermediate_payloads/PG105/todo.json"

EPISTOLA_FILE = (
    "/homessddata/Projects/pdfocr/teste/PG105/text/"
    "820ae7fc-ab9a-4be4-a877-407e301282d2-493.txt"
)

ENTRY_UPDATES = {
    "PG105:section:analytic_subject:001:entry:0029": {
        "target_file_best": EPISTOLA_FILE,
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_neighbor_ocr",
                "resolution_kind": "epistola_nuncupatoria_anchor",
                "target_file_best_reason": (
                    "Index note says 'in Epist. dedic.'; ORDO RERUM names "
                    "'Epistola nuncupatoria. 978'; OCR file 493 opens at printed "
                    "pages 977-978 with the dedication to Benedictus Odescalcus."
                ),
                "evidence_files": [
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "d90ee8d6-7800-4a3c-9255-b1a4fcd7dfed-718.txt",
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "d90ee8d6-7800-4a3c-9255-b1a4fcd7dfed-720.txt",
                    EPISTOLA_FILE,
                ],
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0118": {
        "target_file_best": EPISTOLA_FILE,
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_neighbor_ocr",
                "resolution_kind": "epistola_nuncupatoria_anchor",
                "target_file_best_reason": (
                    "Entry says 'in epist. dedic.' and the dedication section begins "
                    "on OCR file 493 at the opening of the Mariale."
                ),
                "evidence_files": [
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "d90ee8d6-7800-4a3c-9255-b1a4fcd7dfed-719.txt",
                    EPISTOLA_FILE,
                ],
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0132": {
        "target_file_best": EPISTOLA_FILE,
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_neighbor_ocr",
                "resolution_kind": "epistola_nuncupatoria_anchor",
                "target_file_best_reason": (
                    "Paulus Odescalcus is discussed within the dedicatory epistle "
                    "opened on OCR file 493 and continued on 494."
                ),
                "evidence_files": [
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "d90ee8d6-7800-4a3c-9255-b1a4fcd7dfed-719.txt",
                    EPISTOLA_FILE,
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "820ae7fc-ab9a-4be4-a877-407e301282d2-494.txt",
                ],
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0134": {
        "target_file_best": EPISTOLA_FILE,
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_neighbor_ocr",
                "resolution_kind": "epistola_nuncupatoria_anchor",
                "target_file_best_reason": (
                    "Petrus Georgius Odescalcus is named in the dedicatory epistle "
                    "continued on OCR file 494, anchored from file 493."
                ),
                "evidence_files": [
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "d90ee8d6-7800-4a3c-9255-b1a4fcd7dfed-719.txt",
                    EPISTOLA_FILE,
                    "/homessddata/Projects/pdfocr/teste/PG105/text/"
                    "820ae7fc-ab9a-4be4-a877-407e301282d2-494.txt",
                ],
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0096": {
        "inherit_from_lemma": "Thalamus dicitur B. Virgo",
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_internal_cross_reference",
                "resolution_kind": "vide_cross_reference",
                "target_file_best_reason": (
                    "Cross-reference 'Vide Thalamus.' inherits the target from the "
                    "resolved Thalamus lemma entry in the same index."
                ),
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0097": {
        "inherit_from_lemma": "Thronus dicitur B. Virgo",
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_internal_cross_reference",
                "resolution_kind": "vide_cross_reference",
                "target_file_best_reason": (
                    "Cross-reference 'Vide Thronus.' inherits the target from the "
                    "resolved Thronus lemma entry in the same index."
                ),
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0098": {
        "inherit_from_lemma": "Virga dicitur B. Virgo",
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_internal_cross_reference",
                "resolution_kind": "vide_cross_reference",
                "target_file_best_reason": (
                    "Cross-reference 'Vide Virga' inherits the target from the "
                    "resolved Virga lemma entry in the same index."
                ),
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0205": {
        "inherit_from_lemma": "Templum dicitur B. Virgo",
        "raw_json_updates": {
            "locator_resolution": {
                "status": "resolved_by_internal_cross_reference",
                "resolution_kind": "vide_not_cross_reference",
                "target_file_best_reason": (
                    "The note-only remission stays without material refs, but the "
                    "entry can inherit the main Templum lemma target for navigation."
                ),
            }
        },
    },
    "PG105:section:analytic_subject:001:entry:0050": {
        "target_file_best": None,
        "raw_json_updates": {
            "locator_resolution": {
                "status": "remains_unresolved_after_ocr_check",
                "resolution_kind": "distributed_note_reference",
                "target_file_best_reason": (
                    "Entry says 'late adsritur passim in notis' and does not point to "
                    "one material locus; local OCR confirms a distributed note claim "
                    "rather than a single anchorable target."
                ),
                "attempted_searches": [
                    "direct OCR review of index file 718",
                    "neighboring index files 719-720",
                    "dedicatory/front-matter OCR window around printed 978 only for epist. dedic. notes",
                ],
            }
        },
    },
}


def merge_dict(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            merge_dict(dst[key], value)
        else:
            dst[key] = value
    return dst


def load_entries(path: Path) -> list[dict]:
    with path.open() as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return data["entries"]
    return data


def write_json(path: Path, data) -> None:
    with path.open("w") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main() -> None:
    with PAYLOAD.open() as fh:
        payload = json.load(fh)
    entries = payload["entries"]
    intermediate_entries = load_entries(INTERMEDIATE)

    lemma_to_target = {
        e["lemma_raw"]: {
            "target_file_best": e.get("target_file_best"),
            "entry_key": e["entry_key"],
        }
        for e in entries
        if e.get("lemma_raw")
    }

    def apply_updates(entry_list: list[dict]) -> None:
        entry_map = {entry["entry_key"]: entry for entry in entry_list}
        for entry_key, spec in ENTRY_UPDATES.items():
            entry = entry_map[entry_key]
            if "inherit_from_lemma" in spec:
                inherited = lemma_to_target[spec["inherit_from_lemma"]]
                entry["target_file_best"] = inherited["target_file_best"]
                spec["raw_json_updates"]["locator_resolution"]["inherited_from_entry_key"] = inherited["entry_key"]
                spec["raw_json_updates"]["locator_resolution"]["inherited_from_lemma"] = spec["inherit_from_lemma"]
            else:
                entry["target_file_best"] = spec["target_file_best"]
            merge_dict(entry.setdefault("raw_json", {}), spec["raw_json_updates"])

    apply_updates(entries)
    apply_updates(intermediate_entries)

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_json(PAYLOAD, payload)
    write_json(INTERMEDIATE, intermediate_entries)

    with TODO.open() as fh:
        todo = json.load(fh)
    todo["updated_at"] = payload["generated_at"]
    todo["current_focus"] = "PG105 payload finalized after null target_file_best audit"
    todo["completed"] = [
        "alphabetical section recovered",
        "closing contents table recovered",
        "helper run for missing-page cases",
        "null target_file_best audit completed with OCR-backed resolutions",
    ]
    todo["pending"] = []
    todo["blocked"] = []
    todo["notes"] = [
        "Epist. dedic. notes now anchor to the opening Epistola nuncupatoria on OCR file 493.",
        "Cross-references inherit target_file_best from their resolved lemma targets.",
        "The 'passim in notis' editorial note remains intentionally anchorless.",
    ]
    write_json(TODO, todo)


if __name__ == "__main__":
    main()

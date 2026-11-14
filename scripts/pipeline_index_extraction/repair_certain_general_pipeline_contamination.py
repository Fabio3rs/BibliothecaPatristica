#!/usr/bin/env python3
"""Remove confirmed closing-index contamination from general payloads.

The rules here are deliberately volume-specific.  Each rule asserts the known
pre-repair shape, preserves validated assembly objects when they exist, and is
idempotent through a durable repair marker.  Run without ``--apply`` to preview.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_DIR = ROOT / "data" / "index_payloads"
ASSEMBLY_ROOT = ROOT / "data" / "index_intermediate_payloads"
BACKUP_DIR = PAYLOAD_DIR / ".repair_backups" / "cross_pipeline_20260906"
REPAIR_MARKER = "deterministic_cross_pipeline_cleanup_2026_09_06"

VOLUMES = (
    "PL046",
    "PL166",
    "PL183",
    "PO011",
    "PL011",
    "PG021",
    "PG158",
    "PG072",
)

EXPECTED_BEFORE_ENTRIES = {
    "PL046": 23226,
    "PL166": 3041,
    "PL183": 4955,
    "PO011": 2542,
    "PL011": 2466,
    "PG021": 1792,
    "PG158": 1873,
    "PG072": 951,
}

EXPECTED_AFTER_ENTRIES = {
    "PL046": 809,
    "PL166": 1346,
    "PL183": 644,
    "PO011": 249,
    "PL011": 524,
    "PG021": 435,
    "PG158": 97,
    "PG072": 9,
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def entry_count(payload: dict[str, Any]) -> int:
    return sum(
        len(section.get("entries") or [])
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    )


def assembly_data(volume_id: str) -> dict[str, Any]:
    path = ASSEMBLY_ROOT / volume_id / "assembled_fragments.json"
    document = read_json(path)
    if document.get("status") != "complete" or not isinstance(document.get("data"), dict):
        raise ValueError(f"Incomplete or malformed assembly for {volume_id}: {path}")
    return document["data"]


def select_sections(payload: dict[str, Any], keys: set[str]) -> list[dict[str, Any]]:
    by_key = {
        str(section.get("section_key")): section
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    }
    missing = keys - set(by_key)
    if missing:
        raise ValueError(f"Missing expected sections: {sorted(missing)}")
    return [copy.deepcopy(section) for section in payload["sections"] if section.get("section_key") in keys]


def add_missing_entry_keys(payload: dict[str, Any]) -> None:
    seen: set[str] = set()
    for section in payload["sections"]:
        section_key = str(section["section_key"])
        for position, entry in enumerate(section.get("entries") or [], start=1):
            entry_key = str(entry.get("entry_key") or "").strip()
            if not entry_key:
                order = entry.get("entry_order")
                suffix = int(order) if isinstance(order, int) and not isinstance(order, bool) else position
                entry_key = f"{section_key}:legacy-entry:{suffix:05d}"
                entry["entry_key"] = entry_key
            if entry_key in seen:
                raise ValueError(f"Duplicate entry_key after repair: {entry_key}")
            seen.add(entry_key)


def append_repair_note(payload: dict[str, Any], note: str) -> None:
    notes = payload.setdefault("notes", [])
    if not isinstance(notes, list):
        raise ValueError("Payload notes must be a list")
    marker_note = f"{REPAIR_MARKER}: {note}"
    if marker_note not in notes:
        notes.append(marker_note)


def has_repair_marker(payload: dict[str, Any]) -> bool:
    return any(
        isinstance(note, str) and note.startswith(f"{REPAIR_MARKER}:")
        for note in payload.get("notes") or []
    )


def repair_pl046(payload: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "PL046:section:volume_front:elenchus:001",
        "PL046:section:volume_front:supplementum:002",
        "PL046:volume_front:INDICULUS:001",
        "PL046:volume_front:OPERA_APPENDICUM:001",
        "PL046:volume_end:INDEX_SUPPLEMENTI:001",
    }
    payload["sections"] = select_sections(payload, keep)
    append_repair_note(
        payload,
        "removed INDEX ALPHABETICUS, both INDEX GENERALIS blocks, and the old/new order concordances; retained structural inventories only",
    )
    return payload


def repair_pl166(payload: dict[str, Any]) -> dict[str, Any]:
    stable_keys = {
        str(section["section_key"])
        for section in assembly_data("PL166").get("sections") or []
    }
    payload["sections"] = select_sections(payload, stable_keys)
    append_repair_note(
        payload,
        "removed four legacy sections that duplicated the validated assembly over the same physical spans",
    )
    return payload


def repair_pl183(payload: dict[str, Any]) -> dict[str, Any]:
    payload["sections"] = select_sections(
        payload,
        {
            "pl183_front_elenchus",
            "pl183_end_ordo_rerum",
            "pl183_end_ordo_rerum_tomo_iv",
        },
    )
    append_repair_note(
        payload,
        "removed INDEX RERUM ET VERBORUM, NOMENCLATOR VOCUM EXOTICARUM, and prose FLORES serialized as index entries",
    )
    return payload


def repair_po011(payload: dict[str, Any]) -> dict[str, Any]:
    excluded_scopes = {
        "work_index_nominal",
        "work_index_scripture",
        "work_index_alphabetical",
        "work_index_analytic",
    }
    payload["sections"] = [
        copy.deepcopy(section)
        for section in payload["sections"]
        if section.get("scope_kind") not in excluded_scopes
    ]
    append_repair_note(
        payload,
        "removed nominal, scripture-reference, Syriac-word, and analytical indexes owned by the closing-index pipeline",
    )
    return payload


def repair_pl011(payload: dict[str, Any]) -> dict[str, Any]:
    payload["sections"] = select_sections(
        payload,
        {"pl011_volume_front_elenchus", "pl011_volume_end_index_rerum"},
    )
    final = next(
        section
        for section in payload["sections"]
        if section["section_key"] == "pl011_volume_end_index_rerum"
    )
    if len(final["entries"]) != 550:
        raise ValueError("PL011 final section no longer has the reviewed 550-entry boundary")
    final["entries"] = final["entries"][46:]
    append_repair_note(
        payload,
        "removed two INDEX ANALYTICUS sections and the 46-entry alphabetical tail preceding the true final ORDO contents",
    )
    return payload


def repair_pg021(payload: dict[str, Any]) -> dict[str, Any]:
    current = {
        str(section["section_key"]): section for section in payload.get("sections") or []
    }
    required = {"PG021-sec-work-01", "PG021-sec-end-04"}
    if missing := required - set(current):
        raise ValueError(f"Missing expected PG021 sections: {sorted(missing)}")
    final = copy.deepcopy(current["PG021-sec-end-04"])
    if len(final["entries"]) != 484:
        raise ValueError("PG021 final section no longer has the reviewed 484-entry boundary")
    final["entries"] = final["entries"][61:]
    stable = assembly_data("PG021")
    payload["sections"] = copy.deepcopy(stable["sections"]) + [
        copy.deepcopy(current["PG021-sec-work-01"]),
        final,
    ]
    work_keys = {str(work.get("work_key")) for work in payload.get("works") or []}
    payload["works"].extend(
        copy.deepcopy(work)
        for work in stable.get("works") or []
        if str(work.get("work_key")) not in work_keys
    )
    append_repair_note(
        payload,
        "removed author/analytical/LOCUPLETISSIMUS indexes and the 61-entry alphabetical tail preceding the true ORDO contents",
    )
    return payload


def repair_pg158(payload: dict[str, Any]) -> dict[str, Any]:
    payload["sections"] = select_sections(
        payload,
        {
            "PG158:section:volume_front:elenchus:001",
            "PG158:section:volume_end:ordo_rerum:001",
        },
    )
    append_repair_note(
        payload,
        "removed the SYLLABUS AUCTORUM and INDEX RERUM ET VERBORUM already owned by the alphabetical pipeline",
    )
    return payload


def repair_pg072(payload: dict[str, Any]) -> dict[str, Any]:
    current = {
        str(section["section_key"]): section for section in payload.get("sections") or []
    }
    final_key = "pg072-section-end-ordo-rerum"
    if final_key not in current or len(current[final_key].get("entries") or []) != 5:
        raise ValueError("PG072 final ORDO boundary differs from the reviewed five entries")
    stable = assembly_data("PG072")
    payload["works"] = copy.deepcopy(stable["works"])
    payload["sections"] = copy.deepcopy(stable["sections"]) + [
        copy.deepcopy(current[final_key])
    ]
    append_repair_note(
        payload,
        "removed two INDEX RERUM ET VERBORUM sections and restored the four-entry stable front ELENCHUS before retaining the final ORDO",
    )
    return payload


REPAIRERS = {
    "PL046": repair_pl046,
    "PL166": repair_pl166,
    "PL183": repair_pl183,
    "PO011": repair_po011,
    "PL011": repair_pl011,
    "PG021": repair_pg021,
    "PG158": repair_pg158,
    "PG072": repair_pg072,
}


def repair_volume(volume_id: str) -> tuple[dict[str, Any], dict[str, int | bool]]:
    path = PAYLOAD_DIR / f"{volume_id}_indices.json"
    original = read_json(path)
    before = entry_count(original)
    already_repaired = has_repair_marker(original)
    if already_repaired:
        candidate = copy.deepcopy(original)
    else:
        expected = EXPECTED_BEFORE_ENTRIES[volume_id]
        if before != expected:
            raise ValueError(
                f"{volume_id} has {before} entries; expected reviewed pre-repair count {expected}"
            )
        candidate = REPAIRERS[volume_id](copy.deepcopy(original))
        add_missing_entry_keys(candidate)
    after = entry_count(candidate)
    if after != EXPECTED_AFTER_ENTRIES[volume_id]:
        raise ValueError(
            f"{volume_id} repaired count {after}; expected {EXPECTED_AFTER_ENTRIES[volume_id]}"
        )
    return candidate, {
        "before_entries": before,
        "after_entries": after,
        "removed_entries": before - after,
        "already_repaired": already_repaired,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write repaired payloads after checks")
    parser.add_argument(
        "--volume-id",
        action="append",
        choices=VOLUMES,
        help="Limit the repair to one or more reviewed volumes (repeatable)",
    )
    args = parser.parse_args()
    volumes = tuple(args.volume_id or VOLUMES)
    results: list[dict[str, Any]] = []
    for volume_id in volumes:
        payload_path = PAYLOAD_DIR / f"{volume_id}_indices.json"
        candidate, summary = repair_volume(volume_id)
        if args.apply and not summary["already_repaired"]:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_path = BACKUP_DIR / payload_path.name
            if not backup_path.exists():
                shutil.copy2(payload_path, backup_path)
            write_json(payload_path, candidate)
        results.append(
            {
                "volume_id": volume_id,
                **summary,
                "written": bool(args.apply and not summary["already_repaired"]),
                "payload": str(payload_path),
            }
        )
    print(json.dumps({"status": "ok", "apply": args.apply, "results": results}, indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()

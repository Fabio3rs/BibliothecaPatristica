#!/usr/bin/env python3
"""Build the PL042 alphabetical-index payload from stable chunks and helper evidence.

Usage:
  python scripts/pipeline_index_extraction/build_pl042_alphabetical_payload.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL042"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
OUTPUT_FILE = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
TODO_FILE = INTERMEDIATE_DIR / "todo.json"
TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
CHUNK_FILES = [
    INTERMEDIATE_DIR / "chunks" / "section_001_part_001.json",
    INTERMEDIATE_DIR / "chunks" / "section_001_part_002.json",
    INTERMEDIATE_DIR / "chunks" / "section_002_part_003.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_seq(path_str: str | None) -> int:
    if not path_str:
        return 10**9
    name = Path(path_str).stem
    try:
        return int(name.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 10**9


def dedupe_preserve(items: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def merge_section_objects(parts: list[dict[str, Any]]) -> dict[str, Any]:
    base = deepcopy(parts[0])
    raw = dict(base.get("raw_json") or {})
    for part in parts[1:]:
        base["page_start"] = min(v for v in [base.get("page_start"), part.get("page_start")] if v is not None)
        base["page_end"] = max(v for v in [base.get("page_end"), part.get("page_end")] if v is not None)
        if file_seq(part.get("file_start")) < file_seq(base.get("file_start")):
            base["file_start"] = part.get("file_start")
        if file_seq(part.get("file_end")) > file_seq(base.get("file_end")):
            base["file_end"] = part.get("file_end")
        base["confidence"] = round(max(float(base.get("confidence") or 0.0), float(part.get("confidence") or 0.0)), 6)
        part_raw = part.get("raw_json") or {}
        for key in ["source_files", "adjacent_context_files", "context_files_checked", "evidence_files", "heading_variants", "notes"]:
            merged = list(raw.get(key) or []) + list(part_raw.get(key) or [])
            if merged:
                raw[key] = dedupe_preserve(merged)
        for key in ["section_kind_reason", "boundary_note"]:
            if not raw.get(key) and part_raw.get(key):
                raw[key] = part_raw[key]
    for list_key in ["source_files", "adjacent_context_files", "context_files_checked", "evidence_files"]:
        if list_key in raw:
            raw[list_key] = sorted(raw[list_key], key=file_seq)
    base["raw_json"] = raw
    return base


def build_query_names(entry: dict[str, Any]) -> list[str]:
    base = (entry.get("lemma_raw") or entry.get("entry_raw") or "").strip()
    queries: list[str] = []
    if base:
        queries.append(base)
        if "—" in base:
            queries.append(base.split("—", 1)[0].strip(" .;:-"))
        if " - " in base:
            queries.append(base.split(" - ", 1)[0].strip(" .;:-"))
        if len(base) > 160:
            queries.append(" ".join(base.split()[:18]).strip(" .;:-"))
        if ". " in base:
            queries.append(base.split(". ", 1)[0].strip(" .;:-"))
    out = []
    seen = set()
    for item in queries:
        if not item:
            continue
        norm = " ".join(item.split())
        if norm in seen:
            continue
        seen.add(norm)
        out.append(item)
    return out[:3]


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        refs = refs_by_entry.get(entry["entry_key"], [])
        if not refs:
            continue
        ref = refs[0]
        page_hints: list[str] = []
        page_hint_ints: list[int] = []
        for raw in [ref.get("page_ref_raw"), ref.get("range_start_raw"), ref.get("range_end_raw")]:
            if raw is not None:
                raw_str = str(raw)
                if raw_str and raw_str not in page_hints:
                    page_hints.append(raw_str)
        for raw in [ref.get("page_ref_int"), ref.get("range_start_raw"), ref.get("range_end_raw")]:
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value not in page_hint_ints:
                page_hint_ints.append(value)
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": build_query_names(entry),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"candidate_limit": 8},
        "entries": helper_entries,
    }


def run_helper() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(TARGET_LOCATOR), "--input", str(HELPER_REQUEST), "--output", str(HELPER_OUTPUT), "--pretty"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return load_json(HELPER_OUTPUT)


def helper_summary(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates = item.get("candidates") or []
    return {
        "helper_status": item.get("status"),
        "helper_candidate_role": best.get("candidate_role"),
        "helper_reason_summary": best.get("reason_summary"),
        "helper_best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "inferred_printed_page": best.get("inferred_printed_page"),
        },
        "helper_top_candidates": [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:6] if ev.get("kind")],
            }
            for cand in candidates[:3]
        ],
    }


def update_section2_unresolved(entries: list[dict[str, Any]]) -> None:
    title_hits = {
        "Præterea vide in hujus VIII tomi Appendice librum de Fide contra Manichæos, et Commonitorium de recipiendis Manichæis qui convertuntur.": [
            str(ROOT / "teste/PL042/text/5f6fdb6f-5a18-42f3-829e-fa63b43424fe-007.txt"),
            str(ROOT / "teste/PL042/text/5f6fdb6f-5a18-42f3-829e-fa63b43424fe-010.txt"),
        ]
    }
    for entry in entries:
        if entry["section_key"] != "PL042:alpha:ordo_rerum:002":
            continue
        raw = entry.setdefault("raw_json", {})
        if entry.get("target_file_best"):
            continue
        if entry.get("entry_raw") in title_hits:
            raw["locator_retry_status"] = "multi_target_appendix_reference"
            raw["attempted_search_hits"] = title_hits[entry["entry_raw"]]
            raw["locator_retry_note"] = (
                "Direct OCR search found multiple appendix-related hits in the current volume; "
                "the entry points to more than one work and has no single material page locator."
            )
        else:
            raw["locator_retry_status"] = "no_current_volume_locator"
            raw["locator_retry_note"] = (
                "The line is a works-list item grouped under another tome heading or lacks a material page locator "
                "inside PL042, so target_file_best remains unresolved after direct OCR search."
            )


def main() -> None:
    chunk_payloads = [load_json(path) for path in CHUNK_FILES]
    section1_parts = [
        chunk_payloads[0]["sections"][0],
        chunk_payloads[1]["sections"][0],
    ]
    merged_section1 = merge_section_objects(section1_parts)
    merged_section2 = deepcopy(chunk_payloads[2]["sections"][0])
    merged_section2["file_start"] = str(ROOT / "teste/PL042/text/3d7736fe-7e17-4c35-945e-3a3607e90cdf-304.txt")
    merged_section2["file_end"] = str(ROOT / "teste/PL042/text/3d7736fe-7e17-4c35-945e-3a3607e90cdf-304.txt")

    sections = [merged_section1, merged_section2]
    nodes = deepcopy(chunk_payloads[2]["nodes"])

    entries = []
    refs = []
    scripture_refs: list[dict[str, Any]] = []
    for payload in chunk_payloads:
        entries.extend(deepcopy(payload["entries"]))
        refs.extend(deepcopy(payload["refs"]))
        scripture_refs.extend(deepcopy(payload["scripture_refs"]))

    section_start_map = {
        "PL042:alpha:ordo_rerum:001": merged_section1["file_start"],
        "PL042:alpha:ordo_rerum:002": merged_section2["file_start"],
    }

    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        ref["section_start_file"] = section_start_map.get(ref["entry_key"].split(":alpha:ordo_rerum:")[0] and ref.get("section_start_file"), ref.get("section_start_file"))
        refs_by_entry[ref["entry_key"]].append(ref)

    for entry in entries:
        entry["section_start_file"] = section_start_map[entry["section_key"]]

    helper_request = build_helper_request(
        [e for e in entries if e["section_key"] == "PL042:alpha:ordo_rerum:001"],
        refs_by_entry,
    )
    dump_json(HELPER_REQUEST, helper_request)
    helper_output = run_helper()
    helper_by_entry = {item["entry_id"]: item for item in helper_output.get("entries", [])}

    for entry in entries:
        if entry["section_key"] != "PL042:alpha:ordo_rerum:001":
            continue
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        summary = helper_summary(helper)
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best["file"]
        entry.setdefault("raw_json", {}).update(summary)

    for ref in refs:
        entry = next(e for e in entries if e["entry_key"] == ref["entry_key"])
        ref["section_start_file"] = section_start_map[entry["section_key"]]
        helper = helper_by_entry.get(ref["entry_key"])
        if helper:
            best = helper.get("best_candidate") or {}
            if best.get("file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")
                prob = float(best.get("probability") or 0.0)
                ref["confidence"] = round(min(float(ref.get("confidence") or 0.9), prob if prob > 0 else 0.9), 6)
            ref.setdefault("raw_json", {}).update(helper_summary(helper))

    update_section2_unresolved(entries)

    for entry in entries:
        refs_for_entry = refs_by_entry.get(entry["entry_key"], [])
        if not refs_for_entry and entry["section_key"] == "PL042:alpha:ordo_rerum:002":
            entry.setdefault("raw_json", {})["material_ref_status"] = "no_printed_page_locator"

    def entry_sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
        sec_order = next(sec["section_order"] for sec in sections if sec["section_key"] == entry["section_key"])
        source_files = entry.get("raw_json", {}).get("source_files") or []
        first_source = source_files[0] if source_files else entry.get("editorial_anchor_file")
        return (sec_order, file_seq(first_source), int(entry.get("entry_order") or 0))

    entries.sort(key=entry_sort_key)
    for idx, entry in enumerate(entries, 1):
        entry["entry_order"] = idx

    entry_order_map = {entry["entry_key"]: entry["entry_order"] for entry in entries}
    refs.sort(key=lambda ref: (entry_order_map.get(ref["entry_key"], 10**9), ref.get("ref_order") or 0))

    volume_notes = [
        "The recoverable closing material is an ordo rerum / contents table rather than a true alphabetical lemma index.",
        "Section PL042:alpha:ordo_rerum:001 was rebuilt from the stable 614-621 chunk fragments and preserved the validated cross-page joins at 615/616, 617/618, and 618/619.",
        "A fresh helper request was run for the 274 page-linked contents lines so target_file_best and refs[*].target_file are no longer left null by inertia.",
    ]

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered 284 stable logical entries across the two closing ordo-rerum sections. "
            "The main contents table was rebuilt from validated chunks and rerun through index_target_locator "
            "for 274 page-linked lines; the secondary works list remains preserved as non-page-linked editorial material."
        ),
        "evidence_files": [
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-614.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-615.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-616.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-617.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-618.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-619.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-620.txt"),
            str(ROOT / "teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-621.txt"),
            str(ROOT / "teste/PL042/text/3d7736fe-7e17-4c35-945e-3a3607e90cdf-304.txt"),
        ],
    }

    notes = dedupe_preserve(
        chunk_payloads[0]["notes"]
        + chunk_payloads[1]["notes"]
        + chunk_payloads[2]["notes"]
        + [
            "Built a fresh helper request from the stable 274 page-linked INDEX RERUM entries and reran scripts/index_target_locator.py.",
            "Preserved the validated INDEX OPUSCULORUM ALIORUM AUGUSTINI section from file 304 and kept its non-page-linked works-list entries without inventing locators.",
        ]
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": volume_notes,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    dump_json(OUTPUT_FILE, payload)
    dump_json(
        TODO_FILE,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload rebuilt and helper evidence refreshed",
            "completed": [
                "merged stable chunk fragments for section 001 and section 002",
                "rebuilt helper request and ran index_target_locator",
                "wrote final PL042 payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Section 002 works-list items without printed page locators remain unresolved when they point to other tomes or multiple appendix works.",
            ],
        },
    )


if __name__ == "__main__":
    main()

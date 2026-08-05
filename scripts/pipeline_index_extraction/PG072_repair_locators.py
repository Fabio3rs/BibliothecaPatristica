#!/usr/bin/env python3
"""Repair PG072 alphabetical-index locators.

Usage:
  python scripts/pipeline_index_extraction/PG072_repair_locators.py build-helper
  python scripts/index_target_locator.py --input data/alphabetical_index_payloads/PG072_helper_request.json --output data/alphabetical_index_payloads/PG072_helper_output.json --pretty
  python scripts/pipeline_index_extraction/PG072_repair_locators.py apply-helper
  python scripts/pipeline_index_extraction/PG072_repair_locators.py validate
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG072"
SOURCE_ROOT = ROOT / "teste/PG072/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG072_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG072_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG072_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG072"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entry_query_names(entry: dict[str, Any]) -> list[str]:
    candidates = []
    for key in ("lemma_raw", "lemma_display"):
        value = entry.get(key)
        if value:
            candidates.append(str(value))
    raw = str(entry.get("entry_raw") or "")
    if raw:
        without_tail_refs = re.sub(r"(?:,\s*)?\b\d{1,4}\b(?:\s*(?:,|et|ad)\s*\d{1,4}\b)*\.?\s*$", "", raw).strip(" ,.;")
        if without_tail_refs:
            candidates.append(without_tail_refs)
        tokens = re.findall(r"[A-Za-zÀ-ÿÆæŒœ]{4,}", without_tail_refs or raw)
        if len(tokens) >= 2:
            candidates.append(" ".join(tokens[:5]))
    out = []
    seen = set()
    for item in candidates:
        norm = item.casefold()
        if norm not in seen:
            out.append(item)
            seen.add(norm)
    return out[:4]


def helper_request_entry(entry: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any] | None:
    page = ref.get("page_ref_int")
    if not isinstance(page, int):
        return None
    query_names = entry_query_names(entry)
    if not query_names:
        query_names = [str(entry.get("entry_raw") or "")]
    return {
        "entry_id": f"{entry['entry_key']}::ref::{ref['ref_order']}",
        "lemma_raw": entry.get("lemma_raw") or entry.get("lemma_display") or entry.get("entry_raw"),
        "query_names": query_names,
        "page_hints": [ref.get("page_ref_raw") or ref.get("ref_raw") or str(page)],
        "page_hint_ints": [page],
        "context_raw": entry.get("entry_raw"),
    }


def build_helper() -> None:
    payload = read_json(PAYLOAD_PATH)
    entries = {entry["entry_key"]: entry for entry in payload["entries"]}
    request_entries = []
    seen = set()
    for ref in payload["refs"]:
        if ref.get("target_file") is not None:
            continue
        entry = entries.get(ref["entry_key"])
        if not entry:
            continue
        item = helper_request_entry(entry, ref)
        if not item:
            continue
        if item["entry_id"] in seen:
            continue
        seen.add(item["entry_id"])
        request_entries.append(item)

    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": request_entries,
    }
    write_json(HELPER_REQUEST_PATH, request)
    print(f"wrote {HELPER_REQUEST_PATH} entries={len(request_entries)}")


def compact_helper_evidence(result: dict[str, Any]) -> dict[str, Any]:
    best = result.get("best_candidate") or {}
    candidates = []
    for candidate in (result.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [item.get("kind") for item in (candidate.get("evidence") or [])[:8]],
            }
        )
    return {
        "status": result.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "top_candidates": candidates,
    }


def is_usable_best(result: dict[str, Any]) -> bool:
    best = result.get("best_candidate") or {}
    probability = float(best.get("probability") or 0.0)
    role = best.get("candidate_role")
    status = result.get("status")
    return bool(best.get("file")) and role == "target_candidate" and (status == "resolved" or probability >= 0.45)


def apply_helper() -> None:
    payload = read_json(PAYLOAD_PATH)
    helper = read_json(HELPER_OUTPUT_PATH)
    results = {item.get("entry_id"): item for item in helper.get("entries") or []}
    entries = {entry["entry_key"]: entry for entry in payload["entries"]}
    entry_ref_targets: dict[str, list[str]] = {}
    repaired_refs = 0
    evidence_refs = 0

    for ref in payload["refs"]:
        entry_id = f"{ref['entry_key']}::ref::{ref['ref_order']}"
        result = results.get(entry_id)
        if not result:
            continue
        evidence = compact_helper_evidence(result)
        raw_json = ref.setdefault("raw_json", {})
        raw_json["pg072_rerun_helper_locator"] = evidence
        evidence_refs += 1
        if ref.get("target_file") is None and is_usable_best(result):
            best = result["best_candidate"]
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")
            min_confidence = 0.78 if result.get("status") == "resolved" else 0.62
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), min_confidence)
            repaired_refs += 1
        if ref.get("target_file"):
            entry_ref_targets.setdefault(ref["entry_key"], []).append(ref["target_file"])

    page_targets: dict[int, dict[str, int]] = {}
    for ref in payload["refs"]:
        page = ref.get("page_ref_int")
        target = ref.get("target_file")
        if not isinstance(page, int) or not target:
            continue
        page_targets.setdefault(page, {})
        page_targets[page][target] = page_targets[page].get(target, 0) + 1

    propagated_refs = 0
    for ref in payload["refs"]:
        if ref.get("target_file") is not None:
            continue
        page = ref.get("page_ref_int")
        if not isinstance(page, int):
            continue
        candidates = page_targets.get(page) or {}
        if len(candidates) != 1:
            continue
        target, support_count = next(iter(candidates.items()))
        ref["target_file"] = target
        ref["target_file_probability"] = ref.get("target_file_probability") or 0.72
        ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.72)
        ref.setdefault("raw_json", {})["pg072_rerun_same_page_propagation"] = {
            "reason": "Only one target_file was resolved elsewhere for the same cited editorial page in this PG072 payload.",
            "page_ref_int": page,
            "target_file": target,
            "supporting_resolved_ref_count": support_count,
        }
        entry_ref_targets.setdefault(ref["entry_key"], []).append(target)
        propagated_refs += 1

    repaired_entries = 0
    evidence_entries = 0
    for entry in payload["entries"]:
        targets = entry_ref_targets.get(entry["entry_key"]) or []
        if not targets:
            continue
        best_target = targets[0]
        raw_json = entry.setdefault("raw_json", {})
        raw_json["pg072_rerun_target_file_from_ref"] = {
            "target_file": best_target,
            "resolved_ref_count": len(targets),
        }
        evidence_entries += 1
        if entry.get("target_file_best") is None:
            entry["target_file_best"] = best_target
            entry["confidence"] = max(float(entry.get("confidence") or 0.0), 0.78)
            repaired_entries += 1

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "partial_recovery"
    coverage["entries_status_reason"] = (
        "Recovered PG072 main alphabetical index, supplement continuation through files 490-492, "
        "and ORDO RERUM. This rerun rebuilt the material-locator helper request for unresolved "
        "refs and propagated strong target candidates while preserving ambiguous helper evidence."
    )
    notes = payload.setdefault("notes", [])
    notes.append(
        {
            "note_kind": "locator_rerun",
            "note_text": (
                f"PG072 locator rerun recorded helper evidence for {evidence_refs} refs, "
                f"repaired {repaired_refs} ref target_file values, propagated {propagated_refs} same-page "
                f"ref targets, and filled {repaired_entries} entry target_file_best values."
            ),
            "raw_json": {"helper_request": str(HELPER_REQUEST_PATH), "helper_output": str(HELPER_OUTPUT_PATH)},
        }
    )

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "PG072 locator rerun complete; remaining nulls are weak or ambiguous helper cases.",
            "completed": [
                "verified index sections in OCR files 480, 490, and 492",
                "rebuilt helper request for unresolved material refs",
                "applied strong helper target candidates",
                "updated final payload and intermediate fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Do not force helper candidates marked index_page_candidate or mixed_signal.",
                "Remaining unresolved locators need manual page-window checks if stricter completeness is required.",
            ],
        },
    )
    print(
        f"repaired_refs={repaired_refs} propagated_refs={propagated_refs} "
        f"evidence_refs={evidence_refs} repaired_entries={repaired_entries} evidence_entries={evidence_entries}"
    )


def validate() -> None:
    payload = read_json(PAYLOAD_PATH)
    required_top = ["schema_version", "generated_at", "volume", "sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"]
    missing = [key for key in required_top if key not in payload]
    if missing:
        raise SystemExit(f"missing top-level keys: {missing}")
    entry_keys = [entry["entry_key"] for entry in payload["entries"]]
    if len(entry_keys) != len(set(entry_keys)):
        raise SystemExit("duplicate entry_key")
    node_keys = [node["node_key"] for node in payload["nodes"]]
    if len(node_keys) != len(set(node_keys)):
        raise SystemExit("duplicate node_key")
    section_keys = {section["section_key"] for section in payload["sections"]}
    for entry in payload["entries"]:
        if entry["section_key"] not in section_keys:
            raise SystemExit(f"bad entry section_key: {entry['entry_key']}")
    for ref in payload["refs"]:
        if ref["entry_key"] not in set(entry_keys):
            raise SystemExit(f"bad ref entry_key: {ref['entry_key']}")
    seen_ref_orders = set()
    for ref in payload["refs"]:
        key = (ref["entry_key"], ref["ref_order"])
        if key in seen_ref_orders:
            raise SystemExit(f"duplicate ref_order: {key}")
        seen_ref_orders.add(key)
        for file_key in ("target_file", "section_start_file", "editorial_anchor_file"):
            value = ref.get(file_key)
            if value and not str(value).startswith(str(SOURCE_ROOT)):
                raise SystemExit(f"{file_key} outside source_root: {value}")
    for entry in payload["entries"]:
        for file_key in ("target_file_best", "section_start_file", "editorial_anchor_file"):
            value = entry.get(file_key)
            if value and not str(value).startswith(str(SOURCE_ROOT)):
                raise SystemExit(f"{file_key} outside source_root: {value}")
    print(
        "ok",
        "sections", len(payload["sections"]),
        "entries", len(payload["entries"]),
        "refs", len(payload["refs"]),
        "entry_target_null", sum(1 for entry in payload["entries"] if entry.get("target_file_best") is None),
        "ref_target_null", sum(1 for ref in payload["refs"] if ref.get("target_file") is None),
    )


def finalize_notes() -> None:
    payload = read_json(PAYLOAD_PATH)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["generated_at"] = generated_at
    original_notes = payload.setdefault("notes", [])
    payload["notes"] = [
        note
        for note in original_notes
        if not (isinstance(note, dict) and note.get("note_kind") == "locator_rerun")
    ]
    helper_evidence_refs = sum(
        1
        for ref in payload["refs"]
        if isinstance(ref.get("raw_json"), dict) and ref["raw_json"].get("pg072_rerun_helper_locator")
    )
    same_page_refs = sum(
        1
        for ref in payload["refs"]
        if isinstance(ref.get("raw_json"), dict) and ref["raw_json"].get("pg072_rerun_same_page_propagation")
    )
    payload["notes"].append(
        {
            "note_kind": "locator_rerun",
            "note_text": (
                "PG072 locator rerun rebuilt the helper request for unresolved material refs, "
                f"recorded helper evidence for {helper_evidence_refs} refs, used same-page propagation "
                f"for {same_page_refs} refs, and reduced unresolved refs to "
                f"{sum(1 for ref in payload['refs'] if ref.get('target_file') is None)}."
            ),
            "raw_json": {"helper_request": str(HELPER_REQUEST_PATH), "helper_output": str(HELPER_OUTPUT_PATH)},
        }
    )
    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    print("finalized notes")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build-helper", "apply-helper", "finalize-notes", "validate"])
    args = parser.parse_args()
    if args.command == "build-helper":
        build_helper()
    elif args.command == "apply-helper":
        apply_helper()
    elif args.command == "finalize-notes":
        finalize_notes()
    else:
        validate()


if __name__ == "__main__":
    main()

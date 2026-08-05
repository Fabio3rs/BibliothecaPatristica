#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/build_pl135_alphabetical_payload.py

Rebuild the PL135 alphabetical payload from validated fragments and a helper-backed
locator pass for unresolved material refs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL135"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
ASSEMBLED_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
TODO_PATH = INTERMEDIATE_DIR / "todo.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
EXISTING_PAYLOAD_PATH = OUTPUT_PATH


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def normalize_key(text: str | None) -> str:
    return normalize_space(text).casefold()


def digits(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(match) for match in re.findall(r"\d{1,4}", text)]


def helper_page_hints(entry: dict[str, Any], ref: dict[str, Any]) -> list[int]:
    hints: list[int] = []
    if isinstance(entry.get("inferred_printed_page"), int):
        hints.append(int(entry["inferred_printed_page"]))
    if isinstance(ref.get("page_ref_int"), int):
        hints.append(int(ref["page_ref_int"]))
    else:
        hints.extend(digits(str(ref.get("page_ref_raw") or "")))
    if not hints:
        hints.extend(digits(entry.get("entry_raw")))
    if not hints:
        hints.extend(digits(entry.get("context_raw")))
    out: list[int] = []
    for value in hints:
        if 0 < value < 10000 and value not in out:
            out.append(value)
    return out


def helper_query_names(entry: dict[str, Any]) -> list[str]:
    lemma = normalize_space(entry.get("lemma_display") or entry.get("lemma_raw"))
    lemma_raw = normalize_space(entry.get("lemma_raw"))
    entry_raw = normalize_space(entry.get("entry_raw"))
    context_raw = normalize_space(entry.get("context_raw"))
    first_clause = normalize_space(entry_raw.split(",", 1)[0]) if entry_raw else ""
    names = [value for value in [lemma, lemma_raw, first_clause, context_raw[:180]] if value]
    if entry_raw and entry_raw not in names:
        names.append(entry_raw[:180])
    return names[:4]


def build_helper_request(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> dict[str, Any]:
    entry_map = {entry["entry_key"]: entry for entry in entries}
    helper_entries: list[dict[str, Any]] = []
    for ref in refs:
        entry = entry_map[ref["entry_key"]]
        page_hints = helper_page_hints(entry, ref)
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": f"{entry['entry_key']}#ref{int(ref['ref_order']):03d}",
                "lemma_raw": normalize_space(entry.get("lemma_raw") or entry.get("lemma_display") or entry.get("entry_raw")),
                "query_names": helper_query_names(entry),
                "page_hints": [str(value) for value in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": normalize_space(entry.get("context_raw") or entry.get("entry_raw"))[:600],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def summarize_candidates(result: dict[str, Any] | None, limit: int = 3) -> list[dict[str, Any]]:
    if not result:
        return []
    out: list[dict[str, Any]] = []
    for candidate in (result.get("candidates") or [])[:limit]:
        if not isinstance(candidate, dict):
            continue
        evidence_kinds = [
            item.get("kind")
            for item in candidate.get("evidence") or []
            if isinstance(item, dict) and item.get("kind")
        ]
        out.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": evidence_kinds[:6],
            }
        )
    return out


def run_helper(request: dict[str, Any]) -> dict[str, Any]:
    write_json(HELPER_REQUEST_PATH, request)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        check=True,
        cwd=ROOT,
    )
    return read_json(HELPER_OUTPUT_PATH)


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> int:
    helper_map = {item.get("entry_id"): item for item in helper_output.get("entries") or [] if isinstance(item, dict)}
    entry_map = {entry["entry_key"]: entry for entry in entries}
    best_by_entry: dict[str, tuple[float, str]] = {}
    resolved_refs = 0

    for ref in refs:
        helper_id = f"{ref['entry_key']}#ref{int(ref['ref_order']):03d}"
        result = helper_map.get(helper_id)
        raw = dict(ref.get("raw_json") or {})
        raw["helper_status"] = result.get("status") if result else "missing"
        if result:
            raw["helper_ambiguity_reason"] = result.get("ambiguity_reason")
            best = result.get("best_candidate") or {}
            raw["helper_candidate_role"] = best.get("candidate_role")
            raw["helper_reason_summary"] = best.get("reason_summary")
            raw["helper_top_candidates"] = summarize_candidates(result)
            if best.get("file") and best.get("candidate_role") == "target_candidate":
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")
                resolved_refs += 1
                probability = best.get("probability")
                if isinstance(probability, (int, float)):
                    current = best_by_entry.get(ref["entry_key"])
                    if current is None or float(probability) > current[0]:
                        best_by_entry[ref["entry_key"]] = (float(probability), str(best["file"]))
                ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.74)
            raw["helper_best_candidate"] = {
                "file": best.get("file") if result else None,
                "probability": (best.get("probability") if result else None),
                "candidate_role": best.get("candidate_role") if result else None,
            }
        else:
            raw["helper_best_candidate"] = None
        ref["raw_json"] = raw

    for entry in entries:
        if entry["entry_key"] in best_by_entry:
            entry["target_file_best"] = best_by_entry[entry["entry_key"]][1]
        elif not entry.get("target_file_best"):
            entry["target_file_best"] = None

    for entry in entries:
        entry_refs = [ref for ref in refs if ref["entry_key"] == entry["entry_key"] and ref.get("target_file")]
        if entry_refs and not entry.get("target_file_best"):
            entry["target_file_best"] = entry_refs[0]["target_file"]

    return resolved_refs


def build_checkpoint_indexes(
    checkpoint: dict[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    entry_index: dict[tuple[str, str], dict[str, Any]] = {}
    ref_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    entry_key_to_raw: dict[str, str] = {}

    for entry in checkpoint.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        entry_raw = normalize_key(entry.get("entry_raw"))
        lemma_raw = normalize_key(entry.get("lemma_raw") or entry.get("lemma_display"))
        inferred = entry.get("inferred_printed_page")
        entry_target = entry.get("target_file_best")
        entry_key_to_raw[str(entry.get("entry_key") or "")] = entry_raw
        if entry_target:
            entry_index.setdefault((entry_raw, "entry_raw"), entry)
            entry_index.setdefault((lemma_raw, "lemma_raw"), entry)
            if isinstance(inferred, int):
                entry_index.setdefault((f"{lemma_raw}#{inferred}", "lemma_page"), entry)

    for ref in checkpoint.get("refs") or []:
        if not isinstance(ref, dict):
            continue
        target = ref.get("target_file")
        if not target:
            continue
        entry_raw = entry_key_to_raw.get(str(ref.get("entry_key") or ""), "")
        ref_raw = normalize_key(ref.get("ref_raw"))
        page_ref = str(ref.get("page_ref_int") or ref.get("page_ref_raw") or "")
        ref_index.setdefault((entry_raw, ref_raw, page_ref), ref)

    return entry_index, ref_index


def apply_checkpoint_targets(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    checkpoint: dict[str, Any],
) -> tuple[int, int]:
    entry_map = {entry["entry_key"]: entry for entry in entries}
    entry_index, ref_index = build_checkpoint_indexes(checkpoint)
    entry_hits = 0
    ref_hits = 0

    checkpoint_entries = checkpoint.get("entries") or []
    checkpoint_refs = checkpoint.get("refs") or []

    # Exact transcription matches are the safest reuse path.
    exact_entry_by_raw: dict[str, dict[str, Any]] = {}
    exact_entry_by_lemma: dict[str, dict[str, Any]] = {}
    for entry in checkpoint_entries:
        if not isinstance(entry, dict) or not entry.get("target_file_best"):
            continue
        exact_entry_by_raw.setdefault(normalize_key(entry.get("entry_raw")), entry)
        exact_entry_by_lemma.setdefault(normalize_key(entry.get("lemma_raw") or entry.get("lemma_display")), entry)

    exact_ref_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    checkpoint_entry_key_to_raw: dict[str, str] = {}
    for entry in checkpoint_entries:
        if isinstance(entry, dict):
            checkpoint_entry_key_to_raw[str(entry.get("entry_key") or "")] = normalize_key(entry.get("entry_raw"))

    for ref in checkpoint_refs:
        if not isinstance(ref, dict) or not ref.get("target_file"):
            continue
        exact_ref_by_key.setdefault(
            (
                checkpoint_entry_key_to_raw.get(str(ref.get("entry_key") or ""), ""),
                normalize_key(ref.get("ref_raw")),
                str(ref.get("page_ref_int") or ref.get("page_ref_raw") or ""),
            ),
            ref,
        )

    for entry in entries:
        if entry.get("target_file_best"):
            continue
        by_raw = exact_entry_by_raw.get(normalize_key(entry.get("entry_raw")))
        by_lemma = exact_entry_by_lemma.get(normalize_key(entry.get("lemma_raw") or entry.get("lemma_display")))
        candidate = by_raw or by_lemma
        if not candidate:
            inferred = entry.get("inferred_printed_page")
            if isinstance(inferred, int):
                candidate = entry_index.get((f"{normalize_key(entry.get('lemma_raw') or entry.get('lemma_display'))}#{inferred}", "lemma_page"))
        if candidate and candidate.get("target_file_best"):
            entry["target_file_best"] = candidate["target_file_best"]
            raw = dict(entry.get("raw_json") or {})
            raw["checkpoint_target_file_best"] = candidate["target_file_best"]
            raw["checkpoint_target_source"] = candidate.get("entry_key")
            entry["raw_json"] = raw
            entry_hits += 1

    for ref in refs:
        if ref.get("target_file"):
            continue
        key = (
            normalize_key(entry_map[ref["entry_key"]].get("entry_raw")),
            normalize_key(ref.get("ref_raw")),
            str(ref.get("page_ref_int") or ref.get("page_ref_raw") or ""),
        )
        candidate = exact_ref_by_key.get(key)
        if candidate and candidate.get("target_file"):
            ref["target_file"] = candidate["target_file"]
            ref["target_file_probability"] = candidate.get("target_file_probability")
            raw = dict(ref.get("raw_json") or {})
            raw["checkpoint_target_file"] = candidate["target_file"]
            raw["checkpoint_target_source"] = candidate.get("entry_key")
            ref["raw_json"] = raw
            ref_hits += 1

    return entry_hits, ref_hits


def build_payload() -> dict[str, Any]:
    assembled = read_json(ASSEMBLED_PATH)
    data = assembled["data"]

    sections = deepcopy(data.get("sections") or [])
    nodes = deepcopy(data.get("nodes") or [])
    entries = deepcopy(data.get("entries") or [])
    refs = deepcopy(data.get("refs") or [])
    scripture_refs = deepcopy(data.get("scripture_refs") or [])
    notes = deepcopy(data.get("notes") or [])

    entry_map = {entry["entry_key"]: entry for entry in entries}
    helper_request = build_helper_request(entries, refs)
    helper_output = run_helper(helper_request)
    resolved_refs = apply_helper(entries, refs, helper_output)

    existing_payload = read_json(EXISTING_PAYLOAD_PATH) if EXISTING_PAYLOAD_PATH.exists() else {}
    volume = existing_payload.get("volume") or {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Flodoardus, Historia ecclesiae Remensis",
    }
    volume = dict(volume)
    volume.update(
        {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
        }
    )
    volume.setdefault("volume_label", "Flodoardus, Historia ecclesiae Remensis")
    volume["notes"] = [
        "Rebuilt from validated PL135 fragment assembly and a helper-backed locator pass for unresolved refs.",
        "The chunk-level closing-index sections were preserved from the stable assembly instead of reusing the older checkpoint payload.",
    ]

    evidence_files = []
    for section in sections:
        for key in ("file_start", "file_end"):
            value = section.get(key)
            if value and value not in evidence_files:
                evidence_files.append(value)

    unresolved_refs = sum(1 for ref in refs if not ref.get("target_file"))

    checkpoint_entry_hits = 0
    checkpoint_ref_hits = 0
    if existing_payload:
        checkpoint_entry_hits, checkpoint_ref_hits = apply_checkpoint_targets(entries, refs, existing_payload)
        unresolved_refs = sum(1 for ref in refs if not ref.get("target_file"))

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the closing alphabetical index and ORDO RERUM block from the validated fragment assembly; "
            f"helper pass resolved {resolved_refs} refs to OCR targets, checkpoint reuse recovered {checkpoint_ref_hits} refs and {checkpoint_entry_hits} entries, "
            f"and {unresolved_refs} refs remain unresolved."
        ),
        "evidence_files": evidence_files,
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes
        + [
            {
                "note_type": "assembly",
                "status": "complete",
                "source": "assembled_fragments.json",
            },
            {
                "note_type": "helper",
                "status": "complete",
                "source": str(HELPER_OUTPUT_PATH),
                "resolved_refs": resolved_refs,
                "unresolved_refs": unresolved_refs,
            },
            {
                "note_type": "checkpoint_reuse",
                "status": "complete",
                "source": str(EXISTING_PAYLOAD_PATH),
                "entry_hits": checkpoint_entry_hits,
                "ref_hits": checkpoint_ref_hits,
            },
        ],
    }

    # Keep entry ordering stable and make sure each section-scoped order is sequential.
    per_section_order: dict[str, int] = defaultdict(int)
    for entry in payload["entries"]:
        per_section_order[entry["section_key"]] += 1
        entry["entry_order"] = per_section_order[entry["section_key"]]

    for entry_key in entry_map:
        entry_refs = [ref for ref in payload["refs"] if ref["entry_key"] == entry_key]
        if entry_refs and not entry_map[entry_key].get("target_file_best"):
            entry_map[entry_key]["target_file_best"] = next(
                (ref["target_file"] for ref in entry_refs if ref.get("target_file")),
                entry_map[entry_key].get("target_file_best"),
            )

    return payload


def update_todo(payload: dict[str, Any]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": payload["generated_at"],
        "current_focus": "Validate PL135 final payload after helper-backed locator pass.",
        "completed": [
            "Read the extraction contract, output format, workplan, and existing fragment assembly.",
            "Confirmed the chunk fragments and OCR tail evidence for PL135.",
            "Built a helper request for unresolved refs and merged helper evidence into the payload.",
            "Wrote the canonical PL135 alphabetical payload.",
        ],
        "pending": [
            "Run payload validation against the importer.",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals and unresolved locator ambiguity explicit in raw_json when helper evidence is not decisive.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    payload = build_payload()
    write_json(OUTPUT_PATH, payload)
    update_todo(payload)


if __name__ == "__main__":
    main()

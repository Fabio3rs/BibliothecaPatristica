#!/usr/bin/env python3
"""Build the PL015 alphabetical-index payload from validated fragments and helper locators.

Usage:
  python scripts/pipeline_index_extraction/build_pl015_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.common import page_number
from patristica_pipeline.index_localization_helpers import run_helper_locator

VOLUME_ID = "PL015"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
ASSEMBLED_PATH = INTERMEDIATE_DIR / "assembled_fragments.json"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"

MERGED_MAIN_SECTION_KEY = "PL015:section:alphabetical_general:003"
OLD_OPENING_SECTION_KEY = "PL015:candidate-section:004"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def digits(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(match) for match in re.findall(r"\d{1,4}", text)]


def source_seq(item: dict[str, Any]) -> tuple[int, int, str]:
    raw = item.get("raw_json") or {}
    source_file = raw.get("source_file")
    if not source_file:
        files = raw.get("source_files") or []
        source_file = files[0] if files else ""
    line = raw.get("source_line_start") or 0
    return (page_number(Path(source_file)) or 0, int(line), str(source_file))


def build_todo() -> None:
    payload = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Merge validated PL015 fragments, rerun target locator per ref, and rewrite canonical payload.",
        "completed": [
            "Read skill contract and repository docs.",
            "Inspected assembled_fragments.json and confirmed stable objects must be consumed.",
            "Verified OCR around files 521 and 522 to confirm the opening A-entries belong to the main INDEX RERUM ET SENTENTIARUM section.",
        ],
        "pending": [
            "Build helper_request from fragment refs.",
            "Run index_target_locator for PL015 helper entries.",
            "Assemble final payload and validate with import_alphabetical_index_json.py --validate-only.",
        ],
        "blocked": [],
        "notes": [
            "Section candidate-section:004 is semantically the opening of the same main alphabetical section as alphabetical_general:003 and will be merged without dropping keys or entries.",
            "Most fragment refs still lack target_file and need helper-backed resolution.",
        ],
    }
    write_json(TODO_PATH, payload)


def normalize_sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [dict(section) for section in data["sections"]]
    kept: list[dict[str, Any]] = []
    for section in sections:
        if section["section_key"] == OLD_OPENING_SECTION_KEY:
            continue
        kept.append(section)

    for section in kept:
        if section["section_key"] == "PL015:section:ordo_rerum:001":
            section["page_start"] = 2301
            section["page_end"] = 2304
        elif section["section_key"] == "PL015:section:alphabetical_general:002":
            section["page_start"] = 2297
            section["page_end"] = 2300
        elif section["section_key"] == MERGED_MAIN_SECTION_KEY:
            section["page_start"] = 2227
            section["page_end"] = 2293
            section["file_start"] = str(SOURCE_ROOT / "0333a527-f935-4155-8c37-45cef4662344-521.txt")
            section["file_end"] = str(SOURCE_ROOT / "00147bd1-9b19-49c9-8195-f9beb666a21a-555.txt")
            raw = dict(section.get("raw_json") or {})
            evidence_files = list(raw.get("evidence_files") or [])
            opening = str(SOURCE_ROOT / "0333a527-f935-4155-8c37-45cef4662344-521.txt")
            if opening not in evidence_files:
                evidence_files.insert(0, opening)
            raw["evidence_files"] = evidence_files
            header_evidence = list(raw.get("header_evidence") or [])
            header_evidence.insert(
                0,
                {
                    "physical_file": opening,
                    "editorial_header_raw": "2227 INDEX RERUM ET SENTENTIARUM. 2228",
                },
            )
            raw["header_evidence"] = header_evidence
            raw["merged_opening_chunk"] = {
                "former_section_key": OLD_OPENING_SECTION_KEY,
                "reason": "File 521 contains the opening letter A and continues directly into file 522 under the same INDEX RERUM ET SENTENTIARUM heading.",
            }
            section["raw_json"] = raw
    kept.sort(key=lambda item: item["section_order"])
    return kept


def normalize_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [dict(node) for node in data["nodes"]]
    for node in nodes:
        if node["section_key"] == OLD_OPENING_SECTION_KEY:
            node["section_key"] = MERGED_MAIN_SECTION_KEY
    return nodes


def normalize_entries(data: dict[str, Any], sections_by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    entries = [dict(entry) for entry in data["entries"]]
    for entry in entries:
        if entry["section_key"] == OLD_OPENING_SECTION_KEY:
            entry["section_key"] = MERGED_MAIN_SECTION_KEY
        raw = dict(entry.get("raw_json") or {})
        source_files = list(raw.get("source_files") or [])
        source_file = raw.get("source_file")
        if not source_files and source_file:
            source_files = [source_file]
            raw["source_files"] = source_files
        if source_file and "source_file" not in raw:
            raw["source_file"] = source_file
        section = sections_by_key[entry["section_key"]]
        entry["section_start_file"] = section.get("file_start")
        if entry.get("entry_kind") == "ordinal_group":
            entry["entry_kind"] = "heading_group"
        if not entry.get("editorial_anchor_file"):
            entry["editorial_anchor_file"] = source_file or (source_files[0] if source_files else None)
        entry["raw_json"] = raw

    entries.sort(key=source_seq)
    per_section_order: defaultdict[str, int] = defaultdict(int)
    for entry in entries:
        per_section_order[entry["section_key"]] += 1
        entry["entry_order"] = per_section_order[entry["section_key"]]
    return entries


def normalize_refs(refs: list[dict[str, Any]], entry_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ref in refs:
        item = dict(ref)
        ref_raw = str(item.get("ref_raw") or "").strip()
        if re.match(r"^(?:ibid\.?|ib\.?)(?:\b|,)", ref_raw, re.IGNORECASE) and not item.get("page_ref_int"):
            continue
        entry = entry_map[item["entry_key"]]
        item["section_start_file"] = entry.get("section_start_file")
        item["editorial_anchor_file"] = entry.get("editorial_anchor_file")
        item.setdefault("target_file", None)
        item.setdefault("target_file_probability", None)
        raw = dict(item.get("raw_json") or {})
        source_files = raw.get("source_files")
        if not source_files:
            entry_raw = entry.get("raw_json") or {}
            if entry_raw.get("source_files"):
                raw["source_files"] = list(entry_raw["source_files"])
            elif entry_raw.get("source_file"):
                raw["source_files"] = [entry_raw["source_file"]]
        item["raw_json"] = raw
        out.append(item)
    return out


def build_helper_request(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> dict[str, Any]:
    entry_map = {entry["entry_key"]: entry for entry in entries}
    helper_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        page_hint_ints: list[int] = []
        if isinstance(ref.get("page_ref_int"), int):
            page_hint_ints.append(int(ref["page_ref_int"]))
        elif ref.get("page_ref_raw"):
            page_hint_ints.extend(digits(str(ref["page_ref_raw"])))
        page_hint_ints = [value for value in page_hint_ints if 0 < value < 10000]
        if not page_hint_ints:
            continue
        entry = entry_map[ref["entry_key"]]
        entry_id = f"{entry['entry_key']}#ref{int(ref['ref_order']):03d}"
        if entry_id in seen:
            continue
        seen.add(entry_id)
        lemma = str(entry.get("lemma_display") or entry.get("lemma_raw") or "").strip()
        context = str(entry.get("entry_raw") or entry.get("context_raw") or "").strip()
        helper_entries.append(
            {
                "entry_id": entry_id,
                "lemma_raw": lemma,
                "query_names": [value for value in [
                    lemma,
                    str(entry.get("lemma_raw") or "").strip(),
                    context[:180].strip() if len(context.split()) <= 32 else "",
                ] if value][:4],
                "page_hints": [str(ref.get("page_ref_raw") or page_hint_ints[0])],
                "page_hint_ints": sorted(set(page_hint_ints)),
                "context_raw": context[:600],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 4,
        },
        "entries": helper_entries,
    }
    write_json(HELPER_REQUEST_PATH, request)
    return request


def summarize_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in (result.get("candidates") or [])[:3]:
        evidence = [
            item.get("kind")
            for item in candidate.get("evidence") or []
            if isinstance(item, dict) and item.get("kind")
        ]
        out.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "evidence_kinds": evidence[:6],
            }
        )
    return out


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = {item["entry_id"]: item for item in helper_output.get("entries") or []}
    entry_map = {entry["entry_key"]: entry for entry in entries}
    best_by_entry: dict[str, tuple[float, str]] = {}

    for ref in refs:
        helper_id = f"{ref['entry_key']}#ref{int(ref['ref_order']):03d}"
        result = helper_map.get(helper_id)
        raw = dict(ref.get("raw_json") or {})
        if not result:
            raw["helper_status"] = "missing"
            ref["raw_json"] = raw
            continue
        best = result.get("best_candidate") or {}
        helper_status = result.get("status")
        helper_role = best.get("candidate_role")
        probability = best.get("probability")
        if helper_role == "target_candidate" and best.get("file"):
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = probability
            if isinstance(probability, (int, float)):
                current = best_by_entry.get(ref["entry_key"])
                if current is None or probability > current[0]:
                    best_by_entry[ref["entry_key"]] = (float(probability), str(best["file"]))
        raw.update(
            {
                "helper_status": helper_status,
                "helper_ambiguity_reason": result.get("ambiguity_reason"),
                "helper_candidate_role": helper_role,
                "helper_reason_summary": best.get("reason_summary"),
                "helper_best_candidate": {
                    "file": best.get("file"),
                    "probability": probability,
                    "candidate_role": helper_role,
                }
                if best
                else None,
                "helper_top_candidates": summarize_candidates(result),
            }
        )
        ref["raw_json"] = raw

    for entry in entries:
        if entry["entry_key"] in best_by_entry:
            entry["target_file_best"] = best_by_entry[entry["entry_key"]][1]
        elif not entry.get("target_file_best"):
            entry["target_file_best"] = None


def build_payload() -> dict[str, Any]:
    assembled = read_json(ASSEMBLED_PATH)
    data = assembled["data"]

    sections = normalize_sections(data)
    sections_by_key = {section["section_key"]: section for section in sections}
    nodes = normalize_nodes(data)
    entries = normalize_entries(data, sections_by_key)
    entry_map = {entry["entry_key"]: entry for entry in entries}
    refs = normalize_refs(data["refs"], entry_map)

    helper_request = build_helper_request(entries, refs)
    helper_output = run_helper_locator(helper_request, HELPER_OUTPUT_PATH)
    apply_helper(entries, refs, helper_output)

    for entry in entries:
        entry.setdefault("parent_node_key", None)

    unresolved_refs = sum(1 for ref in refs if not ref.get("target_file"))
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 15",
            "notes": [
                "Payload rebuilt from validated closing-index chunk fragments plus a fresh per-ref target locator pass.",
                "The opening file 521 was merged into the main INDEX RERUM ET SENTENTIARUM section after direct OCR verification against file 522.",
                "INDEX RERUM IN NOTIS EXPLICATARUM and INDEX MATERIARUM remain separate closing sections.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": (
                "All stable validated fragment objects were consumed. "
                f"Target locator rerun resolved {len(refs) - unresolved_refs} of {len(refs)} material refs to OCR files."
            ),
            "evidence_files": [section["file_start"] for section in sections if section.get("file_start")],
        },
        "notes": list(data.get("notes") or [])
        + [
            f"Helper request written to {HELPER_REQUEST_PATH}.",
            f"Helper output written to {HELPER_OUTPUT_PATH}.",
            f"Unresolved target_file refs after helper pass: {unresolved_refs}.",
        ],
    }
    return payload


def main() -> None:
    build_todo()
    payload = build_payload()
    write_json(OUTPUT_PATH, payload)
    todo = read_json(TODO_PATH)
    todo["updated_at"] = now_iso()
    todo["current_focus"] = "Payload written; validate with importer."
    todo["completed"].extend(
        [
            "Built PL015 helper request from fragment refs.",
            "Ran index_target_locator and merged helper evidence into refs.",
            f"Wrote canonical payload to {OUTPUT_PATH}.",
        ]
    )
    todo["pending"] = [
        "Run import_alphabetical_index_json.py --validate-only against the rewritten payload.",
    ]
    write_json(TODO_PATH, todo)


if __name__ == "__main__":
    main()

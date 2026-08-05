#!/usr/bin/env python3
"""Usage: rebuild PL012 from validated fragments, rerun helper locators, and write the final payload."""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL012"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
ASSEMBLED_FRAGMENTS = INTERMEDIATE_DIR / "assembled_fragments.json"
HELPER_REQUEST = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
OUTPUT_FILE = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
TODO_FILE = INTERMEDIATE_DIR / "todo.json"

SECTION_ORDO = f"{VOLUME_ID}:section:001"
SECTION_ALPHA = f"{VOLUME_ID}:section:002"
PAGE_RE = re.compile(r"\b(1[0-4]\d{2}|130[0-9])\b")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def alpha_sort_key(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower()).strip()


def normalize_lemma(text: str | None) -> str | None:
    if text is None:
        return None
    norm = re.sub(r"\s+", " ", text).strip()
    return norm if norm else None


def parse_entry_page_tokens(entry_raw: str) -> list[str]:
    tokens = PAGE_RE.findall(entry_raw)
    return tokens


def build_helper_request(alpha_entries: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for index, entry in enumerate(alpha_entries, start=1):
        page_tokens = parse_entry_page_tokens(entry["entry_raw"])
        entries.append(
            {
                "entry_id": f"pl012_alpha_{index:04d}",
                "entry_key": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"]] if entry.get("lemma_raw") else [],
                "page_hints": page_tokens,
                "page_hint_ints": [int(token) for token in page_tokens],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "max_candidates": 5,
            "neighbor_window": 3,
        },
        "entries": entries,
    }


def run_helper() -> None:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST),
            "--output",
            str(HELPER_OUTPUT),
            "--pretty",
        ],
        cwd=ROOT,
        check=True,
    )


def page_map_from_helper(
    helper_entries: list[dict[str, Any]],
    request_entry_by_id: dict[str, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    best_by_page: dict[int, dict[str, Any]] = {}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in helper_entries:
        request_entry = request_entry_by_id.get(item.get("entry_id", ""))
        hints = (request_entry or {}).get("page_hint_ints") or []
        best = item.get("best_candidate") or {}
        if len(hints) != 1 or not best.get("file") or best.get("candidate_role") != "target_candidate":
            continue
        grouped[hints[0]].append(
            {
                "file": best["file"],
                "probability": best.get("probability"),
                "file_seq": best.get("file_seq"),
                "entry_id": item.get("entry_id"),
                "reason_summary": best.get("reason_summary"),
            }
        )
    for page, candidates in grouped.items():
        candidates.sort(key=lambda item: ((item.get("probability") or 0.0), -(item.get("file_seq") or 0)), reverse=True)
        best_by_page[page] = candidates[0]
    return best_by_page


def nearest_page_candidate(page: int, page_map: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    if page in page_map:
        return page_map[page]
    if not page_map:
        return None
    nearest_page = min(page_map, key=lambda known: (abs(known - page), known))
    return page_map[nearest_page] | {"nearest_page": nearest_page}


def parse_refs_from_entry(entry_raw: str, inherited_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    last_page = inherited_page
    for match in PAGE_RE.finditer(entry_raw):
        page = int(match.group(1))
        refs.append(
            {
                "ref_raw": match.group(1),
                "page_ref_raw": match.group(1),
                "page_ref_int": page,
                "inherited": False,
            }
        )
        last_page = page
    if not refs and re.search(r"\b[Ii]bid\.?\b", entry_raw) and inherited_page is not None:
        refs.append(
            {
                "ref_raw": "Ibid.",
                "page_ref_raw": "Ibid.",
                "page_ref_int": inherited_page,
                "inherited": True,
            }
        )
        last_page = inherited_page
    return refs, last_page


def build_alpha_refs(
    alpha_entries: list[dict[str, Any]],
    helper_by_entry_key: dict[str, dict[str, Any]],
    page_map: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    refs: list[dict[str, Any]] = []
    best_target_by_entry: dict[str, str | None] = {}
    previous_page: int | None = None
    for entry in alpha_entries:
        helper_item = helper_by_entry_key.get(entry["entry_key"])
        helper_best = (helper_item or {}).get("best_candidate") or {}
        parsed_refs, previous_page = parse_refs_from_entry(entry["entry_raw"], previous_page)
        if not parsed_refs and helper_best.get("file"):
            best_target_by_entry[entry["entry_key"]] = helper_best["file"]
            continue
        entry_target_file = helper_best.get("file")
        for order, parsed_ref in enumerate(parsed_refs, start=1):
            page_int = parsed_ref["page_ref_int"]
            candidate = nearest_page_candidate(page_int, page_map) if page_int is not None else None
            target_file = candidate["file"] if candidate else helper_best.get("file")
            target_probability = candidate.get("probability") if candidate else helper_best.get("probability")
            raw_json: dict[str, Any] = {
                "source": "helper_page_map",
                "parsed_from_entry_raw": entry["entry_raw"],
            }
            if candidate:
                raw_json["page_map_candidate"] = {
                    "page": page_int,
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "file_seq": candidate.get("file_seq"),
                    "reason_summary": candidate.get("reason_summary"),
                }
                if candidate.get("nearest_page") is not None and candidate["nearest_page"] != page_int:
                    raw_json["page_map_note"] = f"Nearest resolved page {candidate['nearest_page']} reused for cited page {page_int}."
            if helper_best:
                raw_json["helper_best_candidate"] = {
                    "file": helper_best.get("file"),
                    "file_seq": helper_best.get("file_seq"),
                    "probability": helper_best.get("probability"),
                    "candidate_role": helper_best.get("candidate_role"),
                    "reason_summary": helper_best.get("reason_summary"),
                }
            if parsed_ref["inherited"]:
                raw_json["inherited_page_reason"] = "Ibid. inherited from the preceding resolved entry page."
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": order,
                    "ref_kind": "editorial_page",
                    "ref_raw": parsed_ref["ref_raw"],
                    "page_ref_raw": parsed_ref["page_ref_raw"],
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_probability,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.84 if target_file else 0.58,
                    "raw_json": raw_json,
                }
            )
            if order == 1 and target_file:
                entry_target_file = target_file
        best_target_by_entry[entry["entry_key"]] = entry_target_file
    return refs, best_target_by_entry


def update_alpha_entries(
    alpha_entries: list[dict[str, Any]],
    helper_by_entry_key: dict[str, dict[str, Any]],
    best_target_by_entry: dict[str, str | None],
) -> list[dict[str, Any]]:
    updated_entries = []
    for entry in alpha_entries:
        helper_item = helper_by_entry_key.get(entry["entry_key"]) or {}
        helper_best = helper_item.get("best_candidate") or {}
        raw_json = dict(entry.get("raw_json") or {})
        raw_json["material_locator_resolution"] = helper_item.get("status", "not_attempted")
        if helper_best:
            raw_json["helper_best_candidate"] = {
                "file": helper_best.get("file"),
                "file_seq": helper_best.get("file_seq"),
                "probability": helper_best.get("probability"),
                "candidate_role": helper_best.get("candidate_role"),
                "reason_summary": helper_best.get("reason_summary"),
            }
        candidates = helper_item.get("candidates") or []
        if candidates:
            raw_json["helper_top_candidates"] = [
                {
                    "rank": candidate.get("rank"),
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "file_seq": candidate.get("file_seq"),
                    "candidate_role": candidate.get("candidate_role"),
                    "reason_summary": candidate.get("reason_summary"),
                }
                for candidate in candidates[:3]
            ]
        updated_entries.append(
            {
                **entry,
                "target_file_best": best_target_by_entry.get(entry["entry_key"]) or helper_best.get("file"),
                "raw_json": raw_json,
            }
        )
    return updated_entries


def build_payload() -> dict[str, Any]:
    fragments = load_json(ASSEMBLED_FRAGMENTS)
    data = fragments["data"]
    sections = data["sections"]
    nodes = data["nodes"]
    entries = data["entries"]
    notes = list(data.get("notes") or [])
    section1_refs = [ref for ref in data["refs"] if ref["entry_key"].startswith("PL012:candidate-section:001")]
    alpha_entries = [entry for entry in entries if entry["section_key"] == SECTION_ALPHA]

    helper_request = build_helper_request(alpha_entries)
    write_json(HELPER_REQUEST, helper_request)
    run_helper()
    helper_output = load_json(HELPER_OUTPUT)
    helper_entries = helper_output["entries"]
    request_entry_by_id = {item["entry_id"]: item for item in helper_request["entries"]}
    request_entry_key_by_id = {item["entry_id"]: item["entry_key"] for item in helper_request["entries"]}
    helper_by_entry_key = {
        request_entry_key_by_id[item["entry_id"]]: item
        for item in helper_entries
        if item.get("entry_id") in request_entry_key_by_id
    }
    page_map = page_map_from_helper(helper_entries, request_entry_by_id)
    alpha_refs, best_target_by_entry = build_alpha_refs(alpha_entries, helper_by_entry_key, page_map)
    updated_alpha_entries = update_alpha_entries(alpha_entries, helper_by_entry_key, best_target_by_entry)

    other_entries = [entry for entry in entries if entry["section_key"] != SECTION_ALPHA]
    all_entries = other_entries + updated_alpha_entries
    all_entries.sort(key=lambda item: (item["section_key"], item["entry_order"]))

    all_refs = section1_refs + alpha_refs
    all_refs.sort(key=lambda item: (item["entry_key"], item["ref_order"]))

    notes.extend(
        [
            "Final payload rebuilt from validated chunk fragments in assembled_fragments.json.",
            "PL012 helper request/output were regenerated for all INDEX IN PHILASTRIUM entries to restore missing material locators.",
            "Alpha-section page anchors were propagated by helper-derived page map; inherited Ibid. locators remain explicit in raw_json.",
        ]
    )

    volume = {
        "volume_id": VOLUME_ID,
        "collection": "PL",
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_ID,
        "notes": [
            "Recovered two closing sections from validated chunk fragments: an ordo rerum table and an alphabetical Philastrius index.",
            "OCR file suffixes remain distinct from editorial page references and cited material locators.",
            "Helper output was regenerated for the alphabetical section and preserved conservatively in raw_json.",
        ],
    }
    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Validated chunk fragments were preserved and the alphabetical section received regenerated helper-backed material locators.",
        "evidence_files": [
            str(SOURCE_ROOT / "ed855fd5-35ce-4b79-98d3-9e5c1a325eba-690.txt"),
            str(SOURCE_ROOT / "ed855fd5-35ce-4b79-98d3-9e5c1a325eba-691.txt"),
            str(SOURCE_ROOT / "ed855fd5-35ce-4b79-98d3-9e5c1a325eba-694.txt"),
            str(SOURCE_ROOT / "ed855fd5-35ce-4b79-98d3-9e5c1a325eba-695.txt"),
        ],
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def update_todo() -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": utc_now(),
        "current_focus": "Payload rebuilt from validated fragments and helper-backed locators reintroduced.",
        "completed": [
            "Read workplan and validated assembled_fragments.json",
            "Confirmed two closing sections in OCR files 690-695",
            "Regenerated helper request/output for INDEX IN PHILASTRIUM entries",
            "Rebuilt final payload with validated chunk objects preserved",
        ],
        "pending": [
            "Run import_alphabetical_index_json.py --validate-only",
        ],
        "blocked": [],
        "notes": [
            "Section 001 intentionally remains clustered per validated chunk semantics.",
            "Section 002 refs use helper-derived page mapping; nearest-page reuse is explicit in raw_json when exact single-page evidence was unavailable.",
        ],
    }
    write_json(TODO_FILE, todo)


def main() -> None:
    payload = build_payload()
    write_json(OUTPUT_FILE, payload)
    update_todo()


if __name__ == "__main__":
    main()

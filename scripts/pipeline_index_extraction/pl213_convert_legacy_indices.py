#!/usr/bin/env python3
"""
Convert the legacy PL213 index payload into the alphabetical-index schema.

Run:
  python scripts/pipeline_index_extraction/pl213_convert_legacy_indices.py \
    --legacy-input data/index_payloads/PL213_indices.json \
    --helper-output data/alphabetical_index_payloads/PL213_helper_output.json \
    --helper-request data/alphabetical_index_payloads/PL213_helper_request.json \
    --output data/alphabetical_index_payloads/PL213_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PL213/text")
LEGACY_FILE = Path("/homessddata/Projects/pdfocr/data/index_payloads/PL213_indices.json")
MEGINHARDUS_TARGET = SOURCE_ROOT / "c667fde0-b031-4d40-8045-4d85ccc2446a-498.txt"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_for_sort(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def strip_locator(entry_raw: str) -> str:
    text = entry_raw.strip()
    text = re.sub(r"\s+", " ", text)
    # Remove trailing locator fragments when they are clearly not part of the lemma.
    text = re.sub(r"(?:,\s*)?(?:capite|cap\.)\s*[\d,\s]+\.?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[\d,\s]+\.?$", "", text)
    return text.strip()


def split_locator_values(page_ref_raw: Any, page_ref_int: Any) -> list[tuple[str | None, int | None]]:
    if page_ref_raw is None and page_ref_int is None:
        return [(None, None)]

    if isinstance(page_ref_int, int) and isinstance(page_ref_raw, str):
        parts = [part.strip() for part in page_ref_raw.split(",") if part.strip()]
        if len(parts) > 1:
            values: list[tuple[str | None, int | None]] = []
            for part in parts:
                match = re.search(r"\d+", part)
                values.append((match.group(0) if match else part, int(match.group(0)) if match else None))
            return values

    if isinstance(page_ref_int, int):
        return [(str(page_ref_int), page_ref_int)]

    if isinstance(page_ref_raw, str):
        parts = [part.strip() for part in page_ref_raw.split(",") if part.strip()]
        values: list[tuple[str | None, int | None]] = []
        for part in parts:
            match = re.search(r"\d+", part)
            values.append((match.group(0) if match else part, int(match.group(0)) if match else None))
        return values or [(page_ref_raw, None)]

    return [(str(page_ref_int), page_ref_int if isinstance(page_ref_int, int) else None)]


def infer_helper_names(entry_raw: str) -> list[str]:
    base = strip_locator(entry_raw)
    names = [base] if base else [entry_raw.strip()]
    if "," in base:
        head = base.split(",", 1)[0].strip()
        if head and head not in names:
            names.append(head)
    if " ep. " in base:
        names.append(base.replace(" ep. ", " episcopus "))
    if " P. M." in base:
        names.append(base.replace(" P. M.", ""))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        normalized = normalize_for_sort(name) or name
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(name)
    return result


def section_kind_for_heading(heading_raw: str) -> str:
    upper = heading_raw.upper()
    if "INDEX ALPHABETICUS" in upper:
        return "author_index"
    if "ORDO RERUM" in upper:
        return "ordo_rerum"
    if "INDEX CAPITULORUM" in upper:
        return "alphabetical_general"
    if "ELENCHUS" in upper:
        return "alphabetical_general"
    return "alphabetical_general"


def confidence_to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().casefold()
        return {
            "high": 0.95,
            "medium": 0.82,
            "low": 0.62,
        }.get(text, 0.75)
    return 0.75


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_helper_request(legacy: dict[str, Any]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    section = next(
        sec for sec in legacy["sections"] if "INDEX ALPHABETICUS" in sec["heading_raw"].upper()
    )
    for entry in section["entries"]:
        ref_values = split_locator_values(entry.get("page_ref_raw"), entry.get("page_ref_int"))
        for index, (page_hint_raw, page_hint_int) in enumerate(ref_values, start=1):
            entry_id = f"pl213_index_alphabeticus_{entry['entry_order']:03d}"
            if len(ref_values) > 1:
                entry_id = f"{entry_id}_{index}"
            helper_entries.append(
                {
                    "entry_id": entry_id,
                    "lemma_raw": strip_locator(entry["entry_raw"]),
                    "query_names": infer_helper_names(entry["entry_raw"]),
                    "page_hints": [page_hint_raw] if page_hint_raw is not None else [],
                    "page_hint_ints": [page_hint_int] if page_hint_int is not None else [],
                    "context_raw": entry["entry_raw"],
                }
            )
    return {
        "volume_id": "PL213",
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def build_helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        index[item.get("entry_id")] = item
    return index


def helper_best_file(helper_item: dict[str, Any] | None) -> str | None:
    if not helper_item:
        return None
    best = helper_item.get("best_candidate") or {}
    file = best.get("file")
    if isinstance(file, str) and file:
        return file
    candidates = helper_item.get("candidates") or []
    if candidates:
        first = candidates[0]
        file = first.get("file")
        if isinstance(file, str) and file:
            return file
    return None


def build_index_alphabeticus_target_map(helper_output: dict[str, Any] | None) -> dict[tuple[int, int], dict[str, Any]]:
    if not helper_output:
        return {}
    index: dict[tuple[int, int], dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id") or ""
        match = re.search(r"pl213_index_alphabeticus_(\d{3})(?:_(\d+))?$", entry_id)
        if not match:
            continue
        order = int(match.group(1))
        part = int(match.group(2) or 1)
        index[(order, part)] = item
    return index


def build_payload(legacy: dict[str, Any], helper_output: dict[str, Any] | None) -> dict[str, Any]:
    helper_map = build_index_alphabeticus_target_map(helper_output)
    sections_out: list[dict[str, Any]] = []
    nodes_out: list[dict[str, Any]] = []
    entries_out: list[dict[str, Any]] = []
    refs_out: list[dict[str, Any]] = []
    scripture_refs_out: list[dict[str, Any]] = []
    notes_out: list[Any] = []

    alpha_letter_nodes: dict[str, str] = {}
    alpha_section_key = None

    for sec_order, sec in enumerate(legacy["sections"], start=1):
        section_key = sec["section_key"]
        kind = section_kind_for_heading(sec["heading_raw"])
        if "INDEX ALPHABETICUS" in sec["heading_raw"].upper():
            alpha_section_key = section_key

        section_out = {
            "section_key": section_key,
            "volume_id": legacy["volume"]["volume_id"],
            "work_key": sec.get("work_key"),
            "section_order": sec_order,
            "section_kind": kind,
            "heading_raw": sec["heading_raw"],
            "heading_norm": sec.get("heading_norm"),
            "heading_letter": None,
            "page_start": sec.get("page_start"),
            "page_end": sec.get("page_end"),
            "file_start": sec.get("file_start"),
            "file_end": sec.get("file_end"),
            "confidence": confidence_to_float(sec.get("confidence")),
            "raw_json": {
                **(sec.get("raw_json") or {}),
                "legacy_scope_kind": sec.get("scope_kind"),
                "legacy_index_kind": sec.get("index_kind"),
            },
        }
        sections_out.append(section_out)

        if "INDEX ALPHABETICUS" in sec["heading_raw"].upper():
            current_letter = None
            letter_order = 0
            for entry in sec["entries"]:
                entry_raw = entry["entry_raw"]
                entry_lemma = strip_locator(entry_raw)
                entry_kind = "lemma"
                ref_parts = split_locator_values(entry.get("page_ref_raw"), entry.get("page_ref_int"))
                if entry["entry_order"] == 82:
                    ref_parts = [("111", 111)]

                # Infer the letter node from the lemma's initial character.
                first_char = (normalize_for_sort(entry_lemma) or entry_lemma or "")[:1].upper()
                if not first_char:
                    first_char = "?"
                if first_char != current_letter:
                    current_letter = first_char
                    letter_order += 1
                    node_key = f"{section_key}:node:{current_letter}"
                    alpha_letter_nodes[current_letter] = node_key
                    nodes_out.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": letter_order,
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter.casefold(),
                            "label_sort": current_letter.casefold(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source": "alphabetic gutter letter"},
                        }
                    )

                helper_items: list[dict[str, Any]] = []
                for idx, _ in enumerate(ref_parts, start=1):
                    helper_item = helper_map.get((entry["entry_order"], idx))
                    if helper_item:
                        helper_items.append(helper_item)

                best_target = helper_best_file(helper_items[0] if helper_items else None)
                editorial_anchor = sec.get("file_start")
                if entry["entry_order"] >= 83:
                    editorial_anchor = sec.get("file_end")
                if entry["entry_order"] == 82:
                    best_target = str(MEGINHARDUS_TARGET)
                    editorial_anchor = sec.get("file_start")
                if helper_items:
                    # Prefer the strongest helper file for the whole entry, but keep per-ref data below.
                    candidate_files = [helper_best_file(item) for item in helper_items if helper_best_file(item)]
                    if candidate_files:
                        best_target = candidate_files[0]
                if entry["entry_order"] == 82:
                    best_target = str(MEGINHARDUS_TARGET)

                entry_key = f"{section_key}:entry:{entry['entry_order']:03d}"
                entry_out = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": alpha_letter_nodes[current_letter],
                    "entry_order": entry["entry_order"],
                    "entry_kind": entry_kind,
                    "lemma_raw": entry_lemma,
                    "lemma_display": entry_lemma,
                    "lemma_norm": normalize_for_sort(entry_lemma),
                    "lemma_sort": normalize_for_sort(entry_lemma),
                    "entry_raw": entry_raw,
                    "context_raw": entry_raw,
                    "heading_letter": current_letter,
                    "inferred_printed_page": 111 if entry["entry_order"] == 82 else entry.get("page_ref_int"),
                    "section_start_file": sec.get("file_start"),
                    "editorial_anchor_file": editorial_anchor,
                    "target_file_best": best_target,
                    "confidence": 0.9 if best_target else 0.72,
                    "raw_json": {
                        **(entry.get("raw_json") or {}),
                        "legacy_entry_order": entry["entry_order"],
                        "legacy_target_raw": entry.get("target_raw"),
                        "helper_results": helper_items,
                    },
                }
                entries_out.append(entry_out)

                for ref_idx, (page_ref_raw, page_ref_int) in enumerate(ref_parts, start=1):
                    helper_item = helper_map.get((entry["entry_order"], ref_idx))
                    ref_target_file = helper_best_file(helper_item) if helper_item else best_target
                    if entry["entry_order"] == 82:
                        ref_target_file = str(MEGINHARDUS_TARGET)
                    ref_kind = "target_locator"
                    if section_key != alpha_section_key:
                        ref_kind = "editorial_page"
                    page_ref_str = page_ref_raw if page_ref_raw is not None else (str(page_ref_int) if page_ref_int is not None else None)
                    ref_raw = page_ref_str or entry_raw
                    refs_out.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_idx,
                            "ref_kind": ref_kind,
                            "ref_raw": ref_raw,
                            "page_ref_raw": page_ref_str,
                            "page_ref_int": page_ref_int,
                            "page_ref_col": entry.get("page_ref_col"),
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": ref_target_file,
                            "target_file_probability": 0.9 if helper_item else None,
                            "section_start_file": sec.get("file_start"),
                            "editorial_anchor_file": editorial_anchor,
                            "confidence": 0.88 if ref_target_file else 0.7,
                            "raw_json": {
                                "legacy_target_raw": entry.get("target_raw"),
                                "helper_result": helper_item,
                                "manual_override": entry["entry_order"] == 82,
                            },
                        }
                    )
            continue

        # Non-alphabetic sections are converted conservatively from the legacy payload.
        for entry in sec["entries"]:
            entry_raw = entry["entry_raw"]
            entry_kind = "lemma"
            if "INDEX CAPITULORUM" in sec["heading_raw"].upper():
                entry_kind = "lemma"
            elif "ORDO RERUM" in sec["heading_raw"].upper():
                entry_kind = "lemma"

            entry_key = f"{section_key}:entry:{entry['entry_order']:03d}"
            entry_out = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry["entry_order"],
                "entry_kind": entry_kind,
                "lemma_raw": strip_locator(entry_raw),
                "lemma_display": strip_locator(entry_raw),
                "lemma_norm": normalize_for_sort(strip_locator(entry_raw)),
                "lemma_sort": normalize_for_sort(strip_locator(entry_raw)),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": entry.get("page_ref_int"),
                "section_start_file": sec.get("file_start"),
                "editorial_anchor_file": sec.get("file_start"),
                "target_file_best": entry.get("target_file"),
                "confidence": confidence_to_float(entry.get("confidence")),
                "raw_json": {
                    **(entry.get("raw_json") or {}),
                    "legacy_entry_order": entry["entry_order"],
                    "legacy_target_raw": entry.get("target_raw"),
                },
            }
            entries_out.append(entry_out)

            ref_values = split_locator_values(entry.get("page_ref_raw"), entry.get("page_ref_int"))
            for ref_idx, (page_ref_raw, page_ref_int) in enumerate(ref_values, start=1):
                refs_out.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": page_ref_raw if page_ref_raw is not None else entry_raw,
                        "page_ref_raw": page_ref_raw,
                        "page_ref_int": page_ref_int,
                        "page_ref_col": entry.get("page_ref_col"),
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": entry.get("target_file"),
                        "target_file_probability": None,
                        "section_start_file": sec.get("file_start"),
                        "editorial_anchor_file": sec.get("file_start"),
                        "confidence": confidence_to_float(entry.get("confidence")),
                        "raw_json": {"legacy_target_raw": entry.get("target_raw")},
                    }
                )

    if helper_output:
        notes_out.append(
            {
                "kind": "helper_summary",
                "status": helper_output.get("status"),
                "reason_summary": helper_output.get("reason_summary"),
                "candidate_role": helper_output.get("candidate_role"),
            }
        )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": legacy["volume"]["volume_id"],
            "collection": legacy["volume"].get("collection"),
            "source_root": legacy["volume"].get("source_root"),
            "volume_label": legacy["volume"].get("volume_label") or legacy["volume"]["volume_id"],
            "notes": legacy["volume"].get("notes"),
        },
        "sections": sections_out,
        "nodes": nodes_out,
        "entries": entries_out,
        "refs": refs_out,
        "scripture_refs": scripture_refs_out,
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Legacy payload transformed and index alphabeticus target files re-resolved with helper output.",
            "evidence_files": [
                str(SOURCE_ROOT / "c667fde0-b031-4d40-8045-4d85ccc2446a-499.txt"),
                str(SOURCE_ROOT / "c667fde0-b031-4d40-8045-4d85ccc2446a-500.txt"),
                str(SOURCE_ROOT / "663f854c-4a0d-4f28-9cc4-215cad2d2e7e-486.txt"),
            ],
        },
        "notes": notes_out,
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-input", type=Path, default=LEGACY_FILE)
    ap.add_argument("--helper-request", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--todo", type=Path)
    args = ap.parse_args()

    legacy = load_json(args.legacy_input)
    helper_request = build_helper_request(legacy)
    args.helper_request.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    helper_output = None
    if args.helper_output.exists():
        helper_output = load_json(args.helper_output)

    payload = build_payload(legacy, helper_output)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.todo:
        args.todo.write_text(
            json.dumps(
                {
                    "volume_id": "PL213",
                    "updated_at": now_iso(),
                    "current_focus": "Convert legacy PL213 indices into the alphabetical-index schema",
                    "completed": [
                        "legacy payload inspected",
                        "INDEX ALPHABETICUS helper request prepared",
                    ],
                    "pending": [
                        "run helper",
                        "review helper output",
                        "validate final payload",
                    ],
                    "blocked": [],
                    "notes": [
                        "Keep the OCR source_root unchanged.",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

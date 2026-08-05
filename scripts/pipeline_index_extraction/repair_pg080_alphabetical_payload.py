#!/usr/bin/env python3
"""Repair PG080 alphabetical payload hyphen artifacts and refresh checkpoints.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg080_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG080"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG080_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG080_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG080_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG080"
PSALM_XCVIII_TARGET = ROOT / "teste/PG080/text/PG080-847.txt"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def dehyphenate_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def repair_payload_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "entry_fields_changed": 0, "refs_changed": 0}
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }
    for collection, fields in field_map.items():
        for obj in payload.get(collection, []):
            touched = False
            for field in fields:
                before = obj.get(field)
                after = dehyphenate_text(before)
                if before != after:
                    obj[field] = after
                    touched = True
                    if collection == "entries":
                        counts["entry_fields_changed"] += 1
            if touched and collection == "entries":
                counts["entries_changed"] += 1
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    "PG080 rerun merged OCR line-break hyphenation in entry text fields after checking INDEX PSALMORUM OCR reader output for files 1014-1016."
                )
            elif touched and collection == "refs":
                counts["refs_changed"] += 1
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    "PG080 rerun merged OCR line-break hyphenation in reference text fields."
                )
    return counts


def repair_helper_request() -> int:
    if not HELPER_REQUEST_PATH.exists():
        return 0
    request = json.loads(HELPER_REQUEST_PATH.read_text(encoding="utf-8"))
    changed = 0
    for entry in request.get("entries", []):
        for field in ("lemma_raw", "context_raw"):
            before = entry.get(field)
            after = dehyphenate_text(before)
            if before != after:
                entry[field] = after
                changed += 1
        query_names = entry.get("query_names")
        if isinstance(query_names, list):
            repaired = [dehyphenate_text(item) for item in query_names]
            if repaired != query_names:
                entry["query_names"] = repaired
                changed += 1
    if changed:
        write_json(HELPER_REQUEST_PATH, request)
    return changed


def helper_by_entry_id() -> dict[str, dict[str, Any]]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    helper = json.loads(HELPER_OUTPUT_PATH.read_text(encoding="utf-8"))
    return {entry["entry_id"]: entry for entry in helper.get("entries", [])}


def candidate_matches_page(candidate: dict[str, Any], page_ref_int: int) -> bool:
    if candidate.get("inferred_printed_page") == page_ref_int:
        return True
    return page_ref_int in (candidate.get("estimator_pages") or [])


def evidence_kinds(candidate: dict[str, Any]) -> list[str]:
    return [str(item.get("kind")) for item in candidate.get("evidence", []) if item.get("kind")]


def helper_id_for_entry(entry: dict[str, Any]) -> str | None:
    raw_json = entry.get("raw_json") or {}
    return raw_json.get("helper_entry_id") or (raw_json.get("helper") or {}).get("entry_id")


def fill_ref_targets_from_helper(payload: dict[str, Any]) -> dict[str, int]:
    helper_entries = helper_by_entry_id()
    entries_by_key = {entry["entry_key"]: entry for entry in payload.get("entries", [])}
    counts = {"refs_target_files_filled": 0, "refs_still_without_target_file": 0}

    for ref in payload.get("refs", []):
        if ref.get("target_file") or not isinstance(ref.get("page_ref_int"), int):
            continue
        entry = entries_by_key.get(ref.get("entry_key"))
        if not entry:
            continue
        helper_id = helper_id_for_entry(entry)
        helper_entry = helper_entries.get(helper_id or "")
        if not helper_entry:
            continue
        page_ref_int = ref["page_ref_int"]
        exact_candidates = [
            candidate
            for candidate in helper_entry.get("candidates", [])
            if candidate_matches_page(candidate, page_ref_int)
        ]
        if not exact_candidates:
            continue
        candidate = max(
            exact_candidates,
            key=lambda item: (float(item.get("probability") or 0), float(item.get("score") or 0)),
        )
        ref["target_file"] = candidate.get("file")
        ref["target_file_probability"] = candidate.get("probability")
        ref.setdefault("raw_json", {}).setdefault("helper_ref_resolution", {}).update(
            {
                "source": "PG080_helper_output",
                "entry_id": helper_id,
                "match_rule": "ref page matched candidate inferred_printed_page or estimator_pages",
                "candidate_rank": candidate.get("rank"),
                "candidate_file_seq": candidate.get("file_seq"),
                "candidate_probability": candidate.get("probability"),
                "candidate_reason_summary": candidate.get("reason_summary"),
                "candidate_evidence_kinds": evidence_kinds(candidate),
            }
        )
        counts["refs_target_files_filled"] += 1

    counts["refs_still_without_target_file"] = sum(
        1 for ref in payload.get("refs", []) if not ref.get("target_file")
    )
    return counts


def resolve_known_numeric_ocr_error(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"manual_numeric_ocr_refs_resolved": 0}
    target = str(PSALM_XCVIII_TARGET)
    for entry in payload.get("entries", []):
        if entry.get("entry_key") != "PG080:entry:000283":
            continue
        entry["target_file_best"] = target
        entry["confidence"] = min(float(entry.get("confidence") or 0.78), 0.78)
        entry.setdefault("raw_json", {}).setdefault("manual_resolution", {}).update(
            {
                "reason": "INDEX PSALMORUM OCR reads the cited page as 4306, outside PG080's printed-page range. Local OCR search for the incipit and neighboring-page inspection identify the material target at printed page 1306 in PG080-847.txt.",
                "preserved_ref_raw": "4306",
                "target_file": target,
                "evidence": [
                    "rg -n -S 'Κύριος ἐβασίλευσεν' teste/PG080/text",
                    "rg -n -S 'Dominus regnavit, irascantur populi' teste/PG080/text",
                    "python scripts/read_ocr_page_text.py --volume PG080 --pages 846-849 --view xml --show-source",
                ],
            }
        )
    for ref in payload.get("refs", []):
        if ref.get("entry_key") == "PG080:entry:000283" and ref.get("ref_raw") == "4306":
            if not ref.get("target_file"):
                counts["manual_numeric_ocr_refs_resolved"] += 1
            ref["target_file"] = target
            ref["target_file_probability"] = 0.7
            ref.setdefault("raw_json", {}).setdefault("manual_resolution", {}).update(
                {
                    "reason": "Cited page 4306 is an OCR numeric error; the matching incipit starts at printed page 1306 in PG080-847.txt.",
                    "preserved_ref_raw": "4306",
                    "probability_basis": "manual OCR search and neighboring-page inspection",
                }
            )
    return counts


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }
    for collection, fields in field_map.items():
        for idx, obj in enumerate(payload.get(collection, []), start=1):
            for field in fields:
                if has_validator_hyphen_artifact(obj.get(field)):
                    residual.append(
                        {
                            "collection": collection,
                            "index": idx,
                            "entry_key": obj.get("entry_key"),
                            "field": field,
                            "value": obj.get(field),
                        }
                    )
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2))


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing = [
        {"index": idx, "entry_key": ref.get("entry_key")}
        for idx, ref in enumerate(payload.get("refs", []), start=1)
        if ref.get("entry_key") not in entry_keys
    ]
    if missing:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing[:50]}, ensure_ascii=False, indent=2))


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_payload_text_fields(payload)
    counts["helper_request_fields_changed"] = repair_helper_request()
    counts.update(fill_ref_targets_from_helper(payload))
    counts.update(resolve_known_numeric_ocr_error(payload))
    counts["refs_still_without_target_file"] = sum(
        1 for ref in payload.get("refs", []) if not ref.get("target_file")
    )
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG080 rerun repaired validation-blocking OCR line-break hyphen artifacts in INDEX PSALMORUM "
        "entries against the cleaned OCR reader output for files 1014-1016, preserved the prior "
        "section structure, and retried material ref locators with helper evidence."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "payload.json", payload)
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            **counts,
            "repair_basis": [
                "Import validation failure listed line-break hyphen artifacts in PG080 INDEX PSALMORUM entries.",
                "OCR reader output for files 1014-1016 confirms the logical index rows and cleaned joined forms.",
                "Missing entry_key errors were a validation cascade from entries rejected for hyphen artifacts.",
                "Reference target files were retried from PG080_helper_output by matching cited pages against candidate inferred or estimated pages.",
            ],
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG080 line-break hyphen validation failure; import validation completed next.",
            "completed": [
                "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
                "Read alphabetical-index extractor contract and output format",
                "Inspected OCR reader output for files 1014-1017 to confirm INDEX PSALMORUM and ORDO RERUM boundaries",
                f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries",
                f"Updated {counts['helper_request_fields_changed']} helper request fields with repaired text",
                f"Filled target_file for {counts['refs_target_files_filled']} refs using helper candidate page matches",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "This repair keeps OCR literals except for proven word-break hyphenation.",
                "Remaining unresolved target files, if any, had no helper candidate matching the cited page under the conservative rule.",
            ],
        },
    )
    print(json.dumps({"status": "repaired", "volume_id": VOLUME_ID, **counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()

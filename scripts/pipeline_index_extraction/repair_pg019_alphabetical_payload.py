#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg019_alphabetical_payload.py
# Repairs PG019 alphabetical payload OCR line-break hyphen artifacts, refreshes
# the helper request, reruns target location helper, updates TODO, and validates.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG019_alphabetical_indices.json"
HELPER_REQUEST_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG019_helper_request.json"
HELPER_OUTPUT_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG019_helper_output.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG019/todo.json"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG019/text"

OCR_EVIDENCE_FILES = [
    str(SOURCE_ROOT / "e653a93a-1c83-4a8d-917b-204c2f59da62-816.txt"),
    str(SOURCE_ROOT / "e653a93a-1c83-4a8d-917b-204c2f59da62-817.txt"),
    str(SOURCE_ROOT / "e653a93a-1c83-4a8d-917b-204c2f59da62-818.txt"),
    str(SOURCE_ROOT / "e653a93a-1c83-4a8d-917b-204c2f59da62-820.txt"),
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TERMINAL_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: str | None) -> str | None:
    if value is None:
        return None
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return TERMINAL_WORD_HYPHEN_RE.sub(r"\1", merged)
        updated = merged


def repair_payload_text(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    repaired_entries: set[str] = set()
    for entry in payload["entries"]:
        for field in (
            "lemma_raw",
            "lemma_display",
            "lemma_norm",
            "lemma_sort",
            "entry_raw",
            "context_raw",
        ):
            old = entry.get(field)
            if not isinstance(old, str):
                continue
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] += 1
                repaired_entries.add(entry["entry_key"])
        if entry["entry_key"] in repaired_entries:
            entry.setdefault("raw_json", {})["rerun_hyphen_repair"] = {
                "reason": "Merged OCR line-break hyphenation after checking the cleaned OCR reader output.",
                "evidence_files": OCR_EVIDENCE_FILES[:2],
            }

    for ref in payload["refs"]:
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            old = ref.get(field)
            if not isinstance(old, str):
                continue
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] += 1

    changed["entries.repaired"] = len(repaired_entries)
    return dict(changed)


def repair_helper_request_text(request: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    for helper_entry in request.get("entries", []):
        for field in ("lemma_raw", "context_raw"):
            old = helper_entry.get(field)
            if isinstance(old, str):
                new = merge_linebreak_hyphens(old)
                if new != old:
                    helper_entry[field] = new
                    changed[f"helper.entries.{field}"] += 1
        query_names = helper_entry.get("query_names")
        if isinstance(query_names, list):
            repaired_queries = []
            for query in query_names:
                repaired_queries.append(merge_linebreak_hyphens(query) if isinstance(query, str) else query)
            if repaired_queries != query_names:
                helper_entry["query_names"] = repaired_queries
                changed["helper.entries.query_names"] += 1
    return dict(changed)


def assert_internal_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing_refs = [ref["entry_key"] for ref in payload["refs"] if ref["entry_key"] not in entry_keys]
    if missing_refs:
        raise SystemExit(f"refs with missing entry_key remain: {missing_refs[:20]}")

    bad_entry_fields: list[tuple[str, str]] = []
    for entry in payload["entries"]:
        for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            value = entry.get(field)
            if isinstance(value, str) and (LINEBREAK_HYPHEN_RE.search(value) or value.endswith("-")):
                bad_entry_fields.append((entry["entry_key"], field))
    if bad_entry_fields:
        raise SystemExit(f"line-break hyphen artifacts remain: {bad_entry_fields[:20]}")


def update_notes(payload: dict[str, Any], changed: dict[str, int], helper_changed: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    repair_note = (
        "PG019 rerun repaired validation-blocking OCR line-break hyphen artifacts in "
        "INDEX ANALYTICUS entries after checking the cleaned OCR reader output for files 816-817."
    )
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    if repair_note not in volume_notes:
        volume_notes.append(repair_note)

    note_obj = {
        "type": "rerun_validation",
        "created_at": now_iso(),
        "message": "Fixed PG019 import failure caused by OCR line-break hyphen artifacts cascading into missing entry_key refs.",
        "changed_field_counts": changed,
        "helper_request_changed_field_counts": helper_changed,
        "evidence_files": OCR_EVIDENCE_FILES,
    }
    notes = payload.setdefault("notes", [])
    notes.append(note_obj)


def update_todo() -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": "PG019",
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG019 hyphen-artifact import failure.",
            "completed": [
                "Read prior validation failure for entries 0079, 0082, 0085, 0091, 0099, 0100, 0103, 0105, 0120, 0123, 0126, 0129, 0130, 0134, 0135, 0136, 0138, 0144, 0151, 0154, and 0165",
                "Checked cleaned OCR reader output for INDEX ANALYTICUS files 816-817 and ORDO RERUM files 818/820",
                "Merged OCR line-break hyphen artifacts in payload entry text fields",
                "Refreshed helper request text and reran index_target_locator.py",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The missing refs reported by validation were a cascade from rejected entry records.",
                "Section classification remains INDEX ANALYTICUS as analytic_subject and ORDO RERUM as ordo_rerum.",
                "OCR file suffixes, printed pages, and cited references remain separate.",
            ],
        },
    )


def run_helper() -> None:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    helper_request = load_json(HELPER_REQUEST_PATH)

    changed = repair_payload_text(payload)
    helper_changed = repair_helper_request_text(helper_request)
    assert_internal_relationships(payload)

    update_notes(payload, changed, helper_changed)
    dump_json(PAYLOAD_PATH, payload)
    dump_json(HELPER_REQUEST_PATH, helper_request)

    run_helper()
    validate_payload()
    update_todo()
    print(json.dumps({"status": "ok", "changed": changed, "helper_changed": helper_changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()

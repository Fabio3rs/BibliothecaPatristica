#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_pg002_alphabetical_payload.py
# Repairs PG002 alphabetical payload OCR line-break hyphen artifacts, updates helper
# metadata/TODO checkpoints, and validates the final JSON payload.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG002_alphabetical_indices.json"
HELPER_REQUEST_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG002_helper_request.json"
HELPER_OUTPUT_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG002_helper_output.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG002/todo.json"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG002/text"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TERMINAL_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-+$")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def normalize_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    for entry in payload["entries"]:
        for field in ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw", "context_raw"):
            if field not in entry:
                continue
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] += 1
    for ref in payload["refs"]:
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            if field not in ref:
                continue
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] += 1
    return dict(changed)


def renumber_refs(payload: dict[str, Any]) -> None:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in payload["refs"]:
        refs_by_entry[ref["entry_key"]].append(ref)
    for refs in refs_by_entry.values():
        refs.sort(key=lambda item: item.get("ref_order") or 0)
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for ref in refs:
            sig = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
                ref.get("target_file"),
            )
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(ref)
        for order, ref in enumerate(unique, start=1):
            ref["ref_order"] = order
        refs[:] = unique
    ordered_refs: list[dict[str, Any]] = []
    entry_order = {entry["entry_key"]: entry["entry_order"] for entry in payload["entries"]}
    for entry_key in sorted(refs_by_entry, key=lambda key: entry_order.get(key, 10**9)):
        ordered_refs.extend(sorted(refs_by_entry[entry_key], key=lambda item: item["ref_order"]))
    payload["refs"] = ordered_refs


def write_helper_request(payload: dict[str, Any]) -> None:
    sample_entries = [
        entry
        for entry in payload["entries"]
        if entry["entry_key"] in {"PG002:entry:0009", "PG002:entry:0010", "PG002:entry:0058"}
    ]
    helper_entries = []
    for entry in sample_entries:
        page_hint = entry.get("inferred_printed_page")
        helper_entries.append(
            {
                "entry_id": entry["entry_key"].replace(":", "_").lower(),
                "entry_key": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [entry.get("lemma_raw")] if entry.get("lemma_raw") else [],
                "page_hints": [page_hint] if page_hint is not None else [],
                "page_hint_ints": [page_hint] if isinstance(page_hint, int) else [],
                "context_raw": entry.get("entry_raw", "")[:500],
            }
        )
    dump_json(
        HELPER_REQUEST_PATH,
        {
            "volume_id": "PG002",
            "source_root": str(SOURCE_ROOT),
            "options": {
                "candidate_window": 3,
                "notes": "Rerun helper request for entries involved in the PG002 hyphen-artifact validation failure.",
            },
            "entries": helper_entries,
        },
    )


def update_payload_notes(payload: dict[str, Any], changed: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    payload.setdefault("volume", {}).setdefault("notes", [])
    note = (
        "Rerun repaired OCR line-break hyphen artifacts in entry/ref text fields and validated "
        "entry/ref key relationships for PG002."
    )
    if note not in payload["volume"]["notes"]:
        payload["volume"]["notes"].append(note)
    payload.setdefault("notes", [])
    payload["notes"].append(
        {
            "type": "rerun_validation",
            "created_at": now_iso(),
            "message": "Fixed PG002 import failure caused by OCR line-break hyphen artifacts cascading into missing entry_key refs.",
            "changed_field_counts": changed,
            "helper_request": str(HELPER_REQUEST_PATH),
            "helper_output": str(HELPER_OUTPUT_PATH),
            "ocr_spot_checks": [
                str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-633.txt"),
                str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-637.txt"),
                str(SOURCE_ROOT / "1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-641.txt"),
            ],
        }
    )


def update_todo() -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": "PG002",
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG002 hyphen-artifact import failure.",
            "completed": [
                "Reviewed OCR spot checks for files 633, 637, and 641",
                "Removed OCR line-break hyphen artifacts from payload text fields",
                "Rebuilt helper request for entries named in the validation failure",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "OCR file suffixes, printed pages, and cited references remain separate.",
                "No refs point to missing entry_key values after validation.",
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
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed = normalize_text_fields(payload)
    renumber_refs(payload)
    write_helper_request(payload)
    run_helper()
    update_payload_notes(payload, changed)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo()
    print(json.dumps({"status": "ok", "changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()

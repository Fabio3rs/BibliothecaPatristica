#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_pg006_alphabetical_payload.py
# Repairs PG006 alphabetical payload OCR line-break hyphen artifacts, preserves
# the existing extracted structure, updates the volume TODO, and validates it.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG006_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG006/todo.json"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG006/text"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TERMINAL_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-+$")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return TERMINAL_WORD_HYPHEN_RE.sub(r"\1", merged)
        updated = merged


def repair_payload_text(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)

    for entry in payload.get("entries", []):
        for field in (
            "lemma_raw",
            "lemma_display",
            "lemma_norm",
            "lemma_sort",
            "entry_raw",
            "context_raw",
            "heading_letter",
        ):
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] += 1

    for ref in payload.get("refs", []):
        for field in (
            "ref_raw",
            "page_ref_raw",
            "line_ref_raw",
            "range_start_raw",
            "range_end_raw",
        ):
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] += 1

    for scripture_ref in payload.get("scripture_refs", []):
        for field in ("ref_raw", "book_raw", "book_norm"):
            old = scripture_ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                scripture_ref[field] = new
                changed[f"scripture_refs.{field}"] += 1

    return dict(changed)


def renumber_refs(payload: dict[str, Any]) -> dict[str, int]:
    entry_order = {entry["entry_key"]: entry.get("entry_order", 10**9) for entry in payload.get("entries", [])}
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in payload.get("refs", []):
        refs_by_entry[ref["entry_key"]].append(ref)

    duplicate_refs_removed = 0
    for refs in refs_by_entry.values():
        refs.sort(key=lambda item: item.get("ref_order") or 0)
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for ref in refs:
            sig = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("page_ref_int"),
                ref.get("page_ref_col"),
                ref.get("line_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
                ref.get("target_file"),
            )
            if sig in seen:
                duplicate_refs_removed += 1
                continue
            seen.add(sig)
            unique.append(ref)
        for order, ref in enumerate(unique, start=1):
            ref["ref_order"] = order
        refs[:] = unique

    payload["refs"] = [
        ref
        for entry_key in sorted(refs_by_entry, key=lambda key: entry_order.get(key, 10**9))
        for ref in sorted(refs_by_entry[entry_key], key=lambda item: item["ref_order"])
    ]
    return {"refs.duplicates_removed": duplicate_refs_removed}


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


def update_notes(payload: dict[str, Any], changed: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    payload.setdefault("notes", [])
    payload["notes"].append(
        {
            "type": "rerun_validation",
            "created_at": now_iso(),
            "message": "PG006 rerun repaired OCR line-break hyphen artifacts reported by import validation while preserving the verified section structure.",
            "changed_field_counts": changed,
            "ocr_spot_checks": [
                str(SOURCE_ROOT / "814dc761-cf7e-4e23-9b26-2828efb64ed1-813.txt"),
                str(SOURCE_ROOT / "16558f7c-f9cd-4306-a64d-aba3dda9d30a-858.txt"),
                str(SOURCE_ROOT / "16558f7c-f9cd-4306-a64d-aba3dda9d30a-915.txt"),
            ],
        }
    )


def update_todo() -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": "PG006",
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG006 hyphen-artifact import failure.",
            "completed": [
                "Confirmed section windows for INDEX RERUM, ORDO RERUM, INDEX ANALYTICUS, and INDEX SCRIPTORUM",
                "Removed OCR line-break hyphen artifacts from entry/ref text fields",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "OCR file suffixes, printed pages, and cited references remain distinct.",
                "Existing helper output remains checkpoint evidence; this rerun targeted the concrete import failure.",
            ],
        },
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed = repair_payload_text(payload)
    changed.update(renumber_refs(payload))
    update_notes(payload, changed)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo()
    print(json.dumps({"status": "ok", "changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg106_alphabetical_payload.py
# Repairs PG106 alphabetical payload OCR line-break hyphen artifacts, merges verified
# page-boundary continuations, updates the volume TODO, and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG106"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG106_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG106/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)

TERMINAL_MERGES = (
    {
        "keep": "PG106:entry:0188",
        "continuation": "PG106:entry:0190",
        "remove": ["PG106:entry:0189"],
        "ocr_evidence_files": [
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-707.txt"),
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-708.txt"),
        ],
        "reason": "Merged the verified page-boundary continuation 'Conciliatores con-' + 'nubii...' and dropped the stray running-head entry.",
    },
    {
        "keep": "PG106:entry:1484",
        "continuation": "PG106:entry:1486",
        "remove": ["PG106:entry:1485"],
        "ocr_evidence_files": [
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-715.txt"),
        ],
        "reason": "Merged the verified 'Ma-' + 'ximus' split inside INDEX RERUM ET SENTENTIARUM and dropped the spurious page header entry.",
    },
    {
        "keep": "PG106:entry:1696",
        "continuation": "PG106:entry:1699",
        "remove": ["PG106:entry:1697", "PG106:entry:1698"],
        "ocr_evidence_files": [
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-718.txt"),
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-719.txt"),
        ],
        "reason": "Merged the verified ORDO RERUM continuation 'e quo-' + 'rum numero...' across the 1417/1419 page boundary and removed footer/header pseudo-entries.",
    },
    {
        "keep": "PG106:entry:1696",
        "continuation": "PG106:entry:1701",
        "remove": ["PG106:entry:1700"],
        "ocr_evidence_files": [
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-719.txt"),
            str(PROJECT_ROOT / "teste/PG106/text/697f6512-9e58-43be-9376-dfce44ec1134-720.txt"),
        ],
        "reason": "Merged the verified ORDO RERUM continuation 'locu-' + 'stis...' across the 1419/1421 page boundary and removed the stray running-head entry.",
    },
)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    previous = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", previous)
        if merged == previous:
            return merged
        previous = merged


def clean_join(left: Any, right: Any) -> Any:
    if not isinstance(left, str):
        return merge_linebreak_hyphens(right)
    if not isinstance(right, str):
        return merge_linebreak_hyphens(left)
    left = left.rstrip()
    right = right.lstrip()
    if left.endswith("-"):
        return merge_linebreak_hyphens(left[:-1] + right)
    return merge_linebreak_hyphens(f"{left} {right}")


def has_validator_hyphen(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(collapsed))


def repair_entry_fields(payload: dict[str, Any]) -> dict[str, int]:
    changed = defaultdict(int)
    note = (
        "PG106 rerun merged validation-blocking OCR line-break hyphenation in entry text fields "
        "after checking representative OCR XML output in the PG106 tail."
    )
    for entry in payload.get("entries", []):
        touched_fields: list[str] = []
        for field in ENTRY_FIELDS:
            before = entry.get(field)
            after = merge_linebreak_hyphens(before)
            if before == after:
                continue
            entry[field] = after
            touched_fields.append(field)
            changed[field] += 1
        if touched_fields:
            entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(note)
            entry["raw_json"]["pg106_rerun_linebreak_hyphen_repaired"] = {
                "fields": touched_fields,
                "reason": note,
            }
    return dict(changed)


def apply_terminal_merge_spec(
    payload: dict[str, Any],
    keep_key: str,
    continuation_key: str,
    remove_keys: list[str],
    evidence_files: list[str],
    reason: str,
) -> bool:
    entries = payload.get("entries", [])
    by_key = {entry["entry_key"]: entry for entry in entries}
    keep = by_key.get(keep_key)
    continuation = by_key.get(continuation_key)
    if not keep or not continuation:
        return False

    for field in ENTRY_FIELDS:
        keep[field] = clean_join(keep.get(field), continuation.get(field))

    keep_raw_json = keep.setdefault("raw_json", {})
    keep_raw_json.setdefault("repair_notes", []).append(reason)
    keep_raw_json["pg106_rerun_terminal_hyphen_merge"] = {
        "continuation_entry_key": continuation_key,
        "removed_entry_keys": remove_keys,
        "ocr_evidence_files": evidence_files,
        "reason": reason,
    }

    refs = payload.get("refs", [])
    for ref in refs:
        if ref.get("entry_key") == continuation_key:
            ref["entry_key"] = keep_key
            ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                f"PG106 rerun remapped this ref from {continuation_key} to {keep_key} after merging a verified page-boundary continuation."
            )

    removed_set = set(remove_keys) | {continuation_key}
    payload["entries"] = [entry for entry in entries if entry["entry_key"] not in removed_set]
    payload["refs"] = [ref for ref in refs if ref.get("entry_key") not in set(remove_keys)]

    for order, entry in enumerate(payload["entries"], start=1):
        entry["entry_order"] = order

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in payload["refs"]:
        grouped[str(ref["entry_key"])].append(ref)

    rebuilt_refs: list[dict[str, Any]] = []
    for entry_key, entry_refs in grouped.items():
        entry_refs.sort(key=lambda ref: (int(ref.get("ref_order") or 0), str(ref.get("ref_raw") or "")))
        for ref_order, ref in enumerate(entry_refs, start=1):
            ref["ref_order"] = ref_order
            rebuilt_refs.append(ref)
    payload["refs"] = rebuilt_refs
    return True


def repair_terminal_merges(payload: dict[str, Any]) -> int:
    changed = 0
    for spec in TERMINAL_MERGES:
        keep_entry = next(
            (entry for entry in payload.get("entries", []) if entry["entry_key"] == spec["keep"]),
            None,
        )
        if not keep_entry:
            continue
        if not any(has_validator_hyphen(keep_entry.get(field)) for field in ENTRY_FIELDS):
            continue
        merged = apply_terminal_merge_spec(
            payload,
            keep_key=spec["keep"],
            continuation_key=spec["continuation"],
            remove_keys=list(spec["remove"]),
            evidence_files=list(spec["ocr_evidence_files"]),
            reason=str(spec["reason"]),
        )
        if merged:
            changed += 1
    return changed


def assert_no_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", [])):
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str):
                continue
            collapsed = re.sub(r"\s+", " ", value).strip()
            if collapsed.endswith("-") or VALIDATOR_HYPHEN_RE.search(collapsed):
                residual.append(f"entries[{idx}].{field}")
    if residual:
        raise SystemExit(f"Residual OCR line-break hyphen artifacts remain: {residual[:50]}")


def update_payload_notes(payload: dict[str, Any], field_changes: dict[str, int], terminal_merges: int) -> None:
    payload["generated_at"] = now_iso()
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    note = (
        "PG106 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload and "
        "merged four OCR-confirmed page-boundary continuation entries."
    )
    if note not in volume_notes:
        volume_notes.append(note)
    payload.setdefault("notes", [])
    summary = {
        "timestamp": payload["generated_at"],
        "action": "pg106_rerun_hyphen_repair",
        "entry_field_changes": field_changes,
        "terminal_merges": terminal_merges,
    }
    payload["notes"].append(summary)


def update_todo(field_changes: dict[str, int], terminal_merges: int) -> None:
    todo = load_json(TODO_PATH) if TODO_PATH.exists() else {"volume_id": VOLUME_ID}
    todo["updated_at"] = now_iso()
    todo["current_focus"] = "PG106 payload repaired and validated"
    completed = list(todo.get("completed", []))
    completed.append("Rechecked validation-blocking OCR line-break hyphen artifacts against PG106 OCR XML")
    completed.append(
        f"Repaired entry text fields {field_changes} and merged {terminal_merges} verified page-boundary continuation entries"
    )
    completed.append("Validated final payload with scripts/import_alphabetical_index_json.py --validate-only --print-summary")
    todo["completed"] = completed
    todo["pending"] = []
    todo["blocked"] = []
    todo["notes"] = [
        "The rerun kept the prior section structure and targeted only OCR-confirmed hyphenation defects.",
        "Stray running heads and one footer entry were removed only where they interrupted a verified continuation entry.",
    ]
    dump_json(TODO_PATH, todo)


def validate_payload() -> None:
    cmd = [
        "python",
        "scripts/import_alphabetical_index_json.py",
        "--input",
        str(PAYLOAD_PATH),
        "--validate-only",
        "--print-summary",
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            "PG106 validation failed after repair.\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    field_changes = repair_entry_fields(payload)
    terminal_merges = repair_terminal_merges(payload)
    assert_no_hyphen_artifacts(payload)
    update_payload_notes(payload, field_changes, terminal_merges)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo(field_changes, terminal_merges)


if __name__ == "__main__":
    main()

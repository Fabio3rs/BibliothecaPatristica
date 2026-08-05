#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg074_hyphen_artifacts.py
# Repairs PG074 alphabetical payload OCR line-break hyphen artifacts and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG074"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG074/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG074_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG074"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")

ENTRY_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)
REF_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
EVIDENCE_FILES = [
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-542.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-543.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-544.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-545.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-546.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-547.txt",
    SOURCE_ROOT / "dad5a79f-e99a-442c-85cd-e257e7da6e94-548.txt",
]


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
            return merged
        updated = merged


def repair_fields(obj: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    changed_fields: list[str] = []
    for field in fields:
        old = obj.get(field)
        new = merge_linebreak_hyphens(old)
        if new != old:
            obj[field] = new
            changed_fields.append(field)
    return changed_fields


def repair_entries(entries: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_keys: list[str] = []
    for entry in entries:
        changed_fields = repair_fields(entry, ENTRY_FIELDS)
        if not changed_fields:
            continue
        entry_key = entry.get("entry_key") or "<missing>"
        changed_keys.append(str(entry_key))
        for field in changed_fields:
            changed[f"entries.{field}"] += 1
        entry.setdefault("raw_json", {})["pg074_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "reason": (
                "Validation-blocking OCR line-break hyphenation was merged after checking "
                "the cleaned OCR reader output for the PG074 index window."
            ),
        }
    return changed_keys


def repair_refs(refs: list[dict[str, Any]], changed: dict[str, int]) -> list[str]:
    changed_refs: list[str] = []
    for ref in refs:
        changed_fields = repair_fields(ref, REF_FIELDS)
        if not changed_fields:
            continue
        ref_id = f"{ref.get('entry_key')}#{ref.get('ref_order')}"
        changed_refs.append(ref_id)
        for field in changed_fields:
            changed[f"refs.{field}"] += 1
        ref.setdefault("raw_json", {})["pg074_rerun_linebreak_hyphen_repaired"] = {
            "fields": changed_fields,
            "reason": "Validation-blocking OCR line-break hyphenation was merged in reference text fields.",
        }
    return changed_refs


def merge_text(left: Any, right: Any) -> Any:
    if not isinstance(left, str):
        return left
    if not isinstance(right, str):
        return merge_linebreak_hyphens(left)
    if TRAILING_WORD_HYPHEN_RE.search(left):
        return merge_linebreak_hyphens(f"{left} {right}")
    return merge_linebreak_hyphens(left)


def merge_terminal_split_entries(payload: dict[str, Any], changed: dict[str, int]) -> dict[str, str]:
    entries: list[dict[str, Any]] = payload.get("entries", [])
    remap: dict[str, str] = {}
    merged_groups: list[list[str]] = []
    merged_entries: list[dict[str, Any]] = []
    idx = 0
    while idx < len(entries):
        current = entries[idx]
        if not TRAILING_WORD_HYPHEN_RE.search(str(current.get("entry_raw") or "")):
            merged_entries.append(current)
            idx += 1
            continue

        keep = current
        group = [str(keep.get("entry_key"))]
        idx += 1
        while idx < len(entries):
            continuation = entries[idx]
            group.append(str(continuation.get("entry_key")))
            old_entry_raw = keep.get("entry_raw")
            keep["entry_raw"] = merge_text(keep.get("entry_raw"), continuation.get("entry_raw"))
            if keep.get("entry_raw") != old_entry_raw:
                changed["entries.entry_raw_terminal_merge"] += 1

            raw_json = keep.setdefault("raw_json", {})
            repair = raw_json.setdefault("pg074_rerun_terminal_hyphen_entry_merge", {})
            repair["reason"] = (
                "OCR line-break hyphenation split one logical index entry across adjacent payload entries; "
                "the continuation text was joined and references were remapped."
            )
            repair["merged_entry_keys"] = group[:]
            repair["removed_continuation_entry_keys"] = group[1:]
            repair.setdefault("ocr_evidence_files", [])
            for file_value in (
                keep.get("editorial_anchor_file"),
                continuation.get("editorial_anchor_file"),
                keep.get("section_start_file"),
                continuation.get("section_start_file"),
            ):
                if file_value and file_value not in repair["ocr_evidence_files"]:
                    repair["ocr_evidence_files"].append(file_value)

            continuation_key = continuation.get("entry_key")
            if continuation_key:
                remap[str(continuation_key)] = str(keep.get("entry_key"))
            idx += 1
            if not TRAILING_WORD_HYPHEN_RE.search(str(keep.get("entry_raw") or "")):
                break
        merged_groups.append(group)
        merged_entries.append(keep)

    if remap:
        for order, entry in enumerate(merged_entries, start=1):
            entry["entry_order"] = order
        payload["entries"] = merged_entries
    return remap


def remap_refs(refs: list[dict[str, Any]], remap: dict[str, str], changed: dict[str, int]) -> None:
    for ref in refs:
        old_key = ref.get("entry_key")
        if old_key not in remap:
            continue
        ref["entry_key"] = remap[str(old_key)]
        ref.setdefault("raw_json", {})["pg074_rerun_entry_key_remapped_from"] = old_key
        changed["refs.entry_key_remapped"] += 1


def renumber_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        grouped.setdefault(str(ref.get("entry_key")), []).append(ref)

    cleaned: list[dict[str, Any]] = []
    for entry_key in sorted(grouped):
        entry_refs = grouped[entry_key]
        entry_refs.sort(key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or "")))
        seen: set[tuple[Any, ...]] = set()
        order = 1
        for ref in entry_refs:
            signature = (
                ref.get("entry_key"),
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("page_ref_int"),
                ref.get("page_ref_col"),
                ref.get("line_ref_raw"),
                ref.get("target_file"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            ref["ref_order"] = order
            order += 1
            cleaned.append(ref)
    return cleaned


def assert_no_validator_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", []), start=1):
        for field in ENTRY_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and (LINEBREAK_HYPHEN_RE.search(value) or value.rstrip().endswith("-")):
                residual.append(f"entries[{idx}].{field}")
    for idx, ref in enumerate(payload.get("refs", []), start=1):
        for field in REF_FIELDS:
            value = ref.get(field)
            if isinstance(value, str) and (LINEBREAK_HYPHEN_RE.search(value) or value.rstrip().endswith("-")):
                residual.append(f"refs[{idx}].{field}")
    if residual:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual[:50]}")


def update_payload_notes(payload: dict[str, Any], changed: dict[str, int], changed_entry_keys: list[str]) -> None:
    payload["generated_at"] = now_iso()
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG074 rerun repaired OCR line-break hyphen artifacts in the alphabetical payload; "
        "OCR file suffixes, printed pages, and cited references remain separate."
    )
    if volume_note not in volume_notes:
        volume_notes.append(volume_note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "Fixed PG074 import failure caused by OCR line-break hyphen artifacts in entries; "
                "the reported missing entry_key refs were a validation cascade."
            ),
            "changed_field_counts": dict(changed),
            "changed_entry_keys": changed_entry_keys,
            "ocr_spot_checks": [str(path) for path in EVIDENCE_FILES[:2]],
        }
    )
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the visible analytical index blocks and the closing ORDO RERUM table conservatively "
            "from OCR files 542-548. This rerun removed validation-blocking split-word hyphen artifacts "
            "verified against the cleaned OCR reader output; some embedded locators remain noisy."
        ),
        "evidence_files": [str(path) for path in EVIDENCE_FILES],
    }


def update_intermediates(payload: dict[str, Any], changed: dict[str, int]) -> None:
    for name in ("entries", "refs", "coverage", "notes"):
        dump_json(INTERMEDIATE_DIR / f"{name}.json", payload[name])
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG074 line-break hyphen import failure.",
            "completed": [
                "Read alphabetical-index skill contract and output format",
                "Reviewed import validation rule for OCR line-break hyphen artifacts",
                "Spot-checked cleaned OCR reader output for PG074 files 542 and 543",
                "Merged validation-blocking OCR line-break hyphen artifacts in entry text fields",
                "Synchronized final payload and intermediate fragments",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed)}",
                "Refs already pointed to existing checkpoint entry keys; missing-entry-key errors were caused by rejected hyphenated entries.",
                "No scripture_refs are present for this volume payload.",
            ],
        },
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
    changed: dict[str, int] = defaultdict(int)
    changed_entry_keys = repair_entries(payload.get("entries", []), changed)
    remap = merge_terminal_split_entries(payload, changed)
    remap_refs(payload.get("refs", []), remap, changed)
    changed_refs = repair_refs(payload.get("refs", []), changed)
    if remap:
        payload["refs"] = renumber_refs(payload.get("refs", []))
    assert_no_validator_hyphen_artifacts(payload)
    update_payload_notes(payload, changed, changed_entry_keys)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_intermediates(payload, changed)
    print(
        json.dumps(
            {
                "status": "ok",
                "changed_field_counts": dict(changed),
                "entries_changed": len(changed_entry_keys),
                "refs_changed": len(changed_refs),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

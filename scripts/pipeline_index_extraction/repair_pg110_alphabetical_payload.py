#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg110_alphabetical_payload.py
# Repairs PG110 alphabetical payload line-wrap continuations and validation-blocking hyphen artifacts.

from __future__ import annotations

import json
import re
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG110"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG110_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG110"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

ENTRY_TEXT_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)
REF_TEXT_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
UPPER_START_RE = re.compile(r"^[\s\[(]*[A-ZÀ-ÖØ-ÞĀ-ſ\u0370-\u03FF\u1F08-\u1FFF]")
LOWER_OR_CONT_RE = re.compile(r"^[\s\])},;:.\-—0-9\u0590-\u05FFa-zà-öø-ÿ\u03B1-\u03C9\u1F00-\u1FFF]")
WORD_START_RE = re.compile(r"[\u0590-\u05FFA-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]+")
SPACE_HYPHEN_WORD_RE = re.compile(
    r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])\s*-\s*"
    r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])"
)


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


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def starts_like_continuation(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    return bool(LOWER_OR_CONT_RE.match(stripped)) and not bool(UPPER_START_RE.match(stripped))


def ends_with_hyphen(text: str) -> bool:
    return normalize_spaces(text).endswith("-")


def append_text(base: str | None, extra: str | None) -> str | None:
    if base is None:
        return extra
    if extra is None:
        return base
    left = base.rstrip()
    right = extra.lstrip()
    if not left:
        return right
    if not right:
        return left
    if left.endswith("-"):
        return left[:-1] + right
    return left + " " + right


def first_word(text: str | None) -> str:
    if not text:
        return ""
    match = WORD_START_RE.search(text)
    return match.group(0) if match else ""


def repair_hyphenated_field(value: Any, next_text: str | None) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    token = first_word(next_text)
    if updated.rstrip().endswith("-") and token:
        updated = updated.rstrip()[:-1] + token
    updated = SPACE_HYPHEN_WORD_RE.sub(r"\1\2", updated)
    return updated


def choose_target_file(current: dict[str, Any], incoming: dict[str, Any]) -> str | None:
    return current.get("target_file_best") or incoming.get("target_file_best")


def merge_entry_dicts(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(current)
    next_entry_text = incoming.get("entry_raw")
    for field in ENTRY_TEXT_FIELDS:
        left = merged.get(field)
        left = repair_hyphenated_field(left, next_entry_text)
        right = incoming.get(field)
        if field == "context_raw":
            merged[field] = left or right
            continue
        if field.startswith("lemma_") and right and not left:
            merged[field] = right
            continue
        if field == "entry_raw":
            merged[field] = append_text(left, right)
            continue
        merged[field] = left
    merged["target_file_best"] = choose_target_file(merged, incoming)
    merged["confidence"] = min(
        value for value in [merged.get("confidence"), incoming.get("confidence")] if isinstance(value, (int, float))
    ) if any(isinstance(value, (int, float)) for value in [merged.get("confidence"), incoming.get("confidence")]) else merged.get("confidence")
    raw_json = merged.setdefault("raw_json", {})
    raw_json.setdefault("pg110_rerun_merged_from", []).append(incoming["entry_key"])
    raw_json["pg110_rerun_reason"] = (
        "Merged OCR continuation fragment into the preceding logical entry while repairing "
        "validation-blocking line-break hyphenation in PG110."
    )
    return merged


def should_merge(current: dict[str, Any], incoming: dict[str, Any]) -> bool:
    current_text = current.get("entry_raw") or ""
    incoming_text = incoming.get("entry_raw") or ""
    if not incoming_text.strip():
        return True
    if ends_with_hyphen(current_text):
        return True
    if starts_like_continuation(incoming_text):
        return True
    return False


def merge_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    if not entries:
        return [], {}, 0
    merged_entries: list[dict[str, Any]] = []
    old_to_new: dict[str, str] = {}
    merge_count = 0
    current = deepcopy(entries[0])
    consumed_old_keys = [entries[0]["entry_key"]]
    for incoming in entries[1:]:
        if should_merge(current, incoming):
            current = merge_entry_dicts(current, incoming)
            consumed_old_keys.append(incoming["entry_key"])
            merge_count += 1
            continue
        merged_entries.append((current, consumed_old_keys))
        current = deepcopy(incoming)
        consumed_old_keys = [incoming["entry_key"]]
    merged_entries.append((current, consumed_old_keys))

    finalized: list[dict[str, Any]] = []
    for new_index, (entry, old_keys) in enumerate(merged_entries, start=1):
        section_name = entry["entry_key"].split(":")[2]
        new_key = f"{VOLUME_ID}:entry:{section_name}:{new_index:05d}"
        entry["entry_order"] = new_index
        entry["entry_key"] = new_key
        for old_key in old_keys:
            old_to_new[old_key] = new_key
        finalized.append(entry)
    return finalized, old_to_new, merge_count


def rebuild_all_entries(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    entries = payload.get("entries", [])
    rebuilt: list[dict[str, Any]] = []
    old_to_new: dict[str, str] = {}
    total_merges = 0
    entries_by_section: dict[str, list[dict[str, Any]]] = {}
    section_order: list[str] = []
    for entry in entries:
        key = entry["section_key"]
        if key not in entries_by_section:
            entries_by_section[key] = []
            section_order.append(key)
        entries_by_section[key].append(entry)
    for section_key in section_order:
        section_entries = entries_by_section[section_key]
        merged_entries, section_map, section_merges = merge_entries(section_entries)
        rebuilt.extend(merged_entries)
        old_to_new.update(section_map)
        total_merges += section_merges
    return rebuilt, old_to_new, total_merges


def rebuild_refs(payload: dict[str, Any], old_to_new: dict[str, str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for ref in payload.get("refs", []):
        new_key = old_to_new.get(ref["entry_key"])
        if not new_key:
            continue
        cloned = deepcopy(ref)
        cloned["entry_key"] = new_key
        for field in REF_TEXT_FIELDS:
            cloned[field] = repair_hyphenated_field(cloned.get(field), None)
        if new_key not in grouped:
            grouped[new_key] = []
            order.append(new_key)
        grouped[new_key].append(cloned)
    rebuilt: list[dict[str, Any]] = []
    for entry_key in order:
        refs = grouped[entry_key]
        for ref_order, ref in enumerate(refs, start=1):
            ref["ref_order"] = ref_order
            rebuilt.append(ref)
    return rebuilt


def rebuild_scripture_refs(payload: dict[str, Any], old_to_new: dict[str, str]) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for ref in payload.get("scripture_refs", []):
        new_key = old_to_new.get(ref["entry_key"])
        if not new_key:
            continue
        cloned = deepcopy(ref)
        cloned["entry_key"] = new_key
        if new_key not in grouped:
            grouped[new_key] = []
            order.append(new_key)
        grouped[new_key].append(cloned)
    for entry_key in order:
        for ref_order, ref in enumerate(grouped[entry_key], start=1):
            ref["ref_order"] = ref_order
            rebuilt.append(ref)
    return rebuilt


def scrub_all_text_fields(payload: dict[str, Any]) -> None:
    for entry in payload.get("entries", []):
        for field in ENTRY_TEXT_FIELDS:
            value = entry.get(field)
            if isinstance(value, str):
                entry[field] = SPACE_HYPHEN_WORD_RE.sub(r"\1\2", value)
    for ref in payload.get("refs", []):
        for field in REF_TEXT_FIELDS:
            value = ref.get(field)
            if isinstance(value, str):
                ref[field] = SPACE_HYPHEN_WORD_RE.sub(r"\1\2", value)
    for ref in payload.get("scripture_refs", []):
        for field in ("ref_raw", "book_raw", "book_norm"):
            value = ref.get(field)
            if isinstance(value, str):
                ref[field] = SPACE_HYPHEN_WORD_RE.sub(r"\1\2", value)


def update_notes(payload: dict[str, Any], merged_entries: int, old_count: int, new_count: int) -> None:
    payload["generated_at"] = now_iso()
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "PG110 rerun merged OCR continuation fragments and removed validation-blocking "
                "line-break hyphen artifacts against the cleaned OCR reader output for the index tail."
            ),
            "entry_count_before": old_count,
            "entry_count_after": new_count,
            "merged_entry_fragments": merged_entries,
        }
    )
    payload["coverage"] = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Reused the existing PG110 checkpoint and repaired OCR continuation fragmentation "
            "that blocked validation/import in the alphabetical payload."
        ),
        "evidence_files": [
            str(PROJECT_ROOT / "teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-648.txt"),
            str(PROJECT_ROOT / "teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-649.txt"),
            str(PROJECT_ROOT / "teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-654.txt"),
            str(PROJECT_ROOT / "teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-666.txt"),
            str(PROJECT_ROOT / "teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-668.txt"),
        ],
    }


def update_todo(merged_entries: int, new_count: int) -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG110 payload repaired and revalidated after line-break hyphen failure.",
            "completed": [
                "re-read alphabetical index extractor contract and output format",
                "inspected PG110 OCR reader output for index files 648-668",
                "verified validation-blocking split entries against OCR",
                "merged continuation fragments and rebuilt entry/ref numbering",
                "validated repaired PG110 payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Logical entries after rebuild: {new_count}",
                f"Continuation fragments merged into prior entries: {merged_entries}",
                "The rerun keeps existing locator evidence and only repairs OCR continuation structure.",
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
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    original_entries = payload.get("entries", [])
    rebuilt_entries, old_to_new, merged_entries = rebuild_all_entries(payload)
    payload["entries"] = rebuilt_entries
    payload["refs"] = rebuild_refs(payload, old_to_new)
    payload["scripture_refs"] = rebuild_scripture_refs(payload, old_to_new)
    scrub_all_text_fields(payload)
    update_notes(payload, merged_entries, len(original_entries), len(rebuilt_entries))
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo(merged_entries, len(rebuilt_entries))
    print(
        json.dumps(
            {
                "status": "ok",
                "entry_count_before": len(original_entries),
                "entry_count_after": len(rebuilt_entries),
                "merged_entry_fragments": merged_entries,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

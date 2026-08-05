#!/usr/bin/env python3
"""Repair PG084 alphabetical payload after OCR line-break hyphen validation.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg084_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG084"
SOURCE_ROOT = ROOT / "teste/PG084/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG084_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG084_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG084_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG084"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")

ENTRY_TEXT_FIELDS = (
    "entry_raw",
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "context_raw",
)
REF_TEXT_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
SCRIPTURE_TEXT_FIELDS = ("ref_raw", "book_raw", "book_norm")

OCR_EVIDENCE_FILES = [
    str(SOURCE_ROOT / "87e47512-f9ac-494d-86eb-fd972072f767-629.txt"),
    str(SOURCE_ROOT / "87e47512-f9ac-494d-86eb-fd972072f767-646.txt"),
    str(SOURCE_ROOT / "87e47512-f9ac-494d-86eb-fd972072f767-650.txt"),
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    while True:
        updated = LINEBREAK_HYPHEN_RE.sub("", value)
        if updated == value:
            return updated
        value = updated


def has_trailing_word_hyphen(value: Any) -> bool:
    return isinstance(value, str) and bool(TRAILING_WORD_HYPHEN_RE.search(value))


def merge_field(primary: Any, continuation: Any) -> Any:
    if not isinstance(primary, str):
        return primary
    if has_trailing_word_hyphen(primary) and isinstance(continuation, str):
        return dehyphenate(f"{primary} {continuation}")
    return dehyphenate(primary)


def repair_inline_text_fields(
    objects: list[dict[str, Any]],
    fields: tuple[str, ...],
    note_key: str,
    counts: defaultdict[str, int],
) -> None:
    for obj in objects:
        changed_fields: list[str] = []
        for field in fields:
            before = obj.get(field)
            after = dehyphenate(before)
            if before != after:
                obj[field] = after
                changed_fields.append(field)
                counts[f"{note_key}.{field}_inline_dehyphenated"] += 1
        if changed_fields:
            obj.setdefault("raw_json", {}).setdefault("pg084_rerun_linebreak_hyphen_repair", []).append(
                {
                    "fields": changed_fields,
                    "reason": "Merged OCR line-break hyphenation inside payload text fields.",
                }
            )


def merge_terminal_split_entries(payload: dict[str, Any], counts: defaultdict[str, int]) -> dict[str, str]:
    entries: list[dict[str, Any]] = payload["entries"]
    merged_entries: list[dict[str, Any]] = []
    remap: dict[str, str] = {}
    idx = 0

    while idx < len(entries):
        current = entries[idx]
        if not has_trailing_word_hyphen(current.get("entry_raw")):
            merged_entries.append(current)
            idx += 1
            continue

        keep = current
        merged_keys = [str(keep.get("entry_key"))]
        idx += 1
        while idx < len(entries):
            continuation = entries[idx]
            continuation_key = str(continuation.get("entry_key"))
            merged_keys.append(continuation_key)

            for field in ENTRY_TEXT_FIELDS:
                before = keep.get(field)
                keep[field] = merge_field(keep.get(field), continuation.get(field))
                if keep.get(field) != before:
                    counts[f"entries.{field}_terminal_merge"] += 1

            remap[continuation_key] = str(keep.get("entry_key"))
            repair = keep.setdefault("raw_json", {}).setdefault("pg084_rerun_terminal_hyphen_entry_merge", {})
            repair["reason"] = (
                "OCR line-break hyphenation split one logical index entry across adjacent payload entries; "
                "the continuation entry was merged and its refs were remapped."
            )
            repair["merged_entry_keys"] = merged_keys[:]
            repair["removed_continuation_entry_keys"] = merged_keys[1:]
            repair["ocr_evidence_files"] = OCR_EVIDENCE_FILES

            idx += 1
            if not has_trailing_word_hyphen(keep.get("entry_raw")):
                break

        merged_entries.append(keep)

    if remap:
        for order, entry in enumerate(merged_entries, start=1):
            entry["entry_order"] = order
        payload["entries"] = merged_entries
    return remap


def remap_entry_keys(objects: list[dict[str, Any]], remap: dict[str, str], label: str, counts: defaultdict[str, int]) -> None:
    for obj in objects:
        old_key = obj.get("entry_key")
        if old_key not in remap:
            continue
        obj["entry_key"] = remap[str(old_key)]
        obj.setdefault("raw_json", {}).setdefault("pg084_rerun_entry_key_remapped_from", []).append(old_key)
        counts[f"{label}.entry_key_remapped"] += 1


def merge_terminal_split_refs(refs: list[dict[str, Any]], counts: defaultdict[str, int]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    idx = 0
    while idx < len(refs):
        current = refs[idx]
        if not has_trailing_word_hyphen(current.get("ref_raw")):
            cleaned.append(current)
            idx += 1
            continue

        merged_keys = [f"{current.get('entry_key')}#{current.get('ref_order')}"]
        idx += 1
        while idx < len(refs):
            continuation = refs[idx]
            if continuation.get("entry_key") != current.get("entry_key"):
                break
            merged_keys.append(f"{continuation.get('entry_key')}#{continuation.get('ref_order')}")
            before = current.get("ref_raw")
            current["ref_raw"] = merge_field(current.get("ref_raw"), continuation.get("ref_raw"))
            if current.get("ref_raw") != before:
                counts["refs.ref_raw_terminal_merge"] += 1
            for field in (
                "page_ref_raw",
                "page_ref_int",
                "page_ref_col",
                "line_ref_raw",
                "range_start_raw",
                "range_end_raw",
                "target_file",
                "target_file_probability",
                "section_start_file",
                "editorial_anchor_file",
            ):
                if current.get(field) is None and continuation.get(field) is not None:
                    current[field] = continuation.get(field)
            current.setdefault("raw_json", {}).setdefault("pg084_rerun_terminal_hyphen_ref_merge", {})[
                "reason"
            ] = "OCR line-break hyphenation split one logical unresolved ref across adjacent ref rows."
            current["raw_json"]["pg084_rerun_terminal_hyphen_ref_merge"]["merged_ref_ids"] = merged_keys[:]
            idx += 1
            if not has_trailing_word_hyphen(current.get("ref_raw")):
                break
        cleaned.append(current)
    return cleaned


def repair_terminal_scripture_refs_from_entries(payload: dict[str, Any], counts: defaultdict[str, int]) -> None:
    entries_by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    for ref in payload["scripture_refs"]:
        if not has_trailing_word_hyphen(ref.get("ref_raw")):
            continue
        entry = entries_by_key.get(ref.get("entry_key"))
        entry_raw = entry.get("entry_raw") if entry else None
        if isinstance(entry_raw, str) and not has_trailing_word_hyphen(entry_raw):
            ref["ref_raw"] = entry_raw
            ref.setdefault("raw_json", {}).setdefault("pg084_rerun_scripture_ref_hyphen_repair", {})[
                "reason"
            ] = (
                "The scripture_ref raw text was copied from a terminal OCR split fragment; "
                "after entry-level continuation merge, ref_raw was refreshed from the repaired entry text."
            )
            counts["scripture_refs.ref_raw_terminal_refreshed_from_entry"] += 1


def dedupe_and_renumber_refs(refs: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        grouped[str(ref.get("entry_key"))].append(ref)

    cleaned: list[dict[str, Any]] = []
    for entry_key in sorted(grouped):
        seen: set[tuple[Any, ...]] = set()
        order = 1
        for ref in sorted(grouped[entry_key], key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or ""))):
            signature = tuple(ref.get(field) for field in fields)
            if signature in seen:
                continue
            seen.add(signature)
            ref["ref_order"] = order
            order += 1
            cleaned.append(ref)
    return cleaned


def residual_hyphen_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    residual: list[dict[str, Any]] = []
    collections = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw",),
    }
    for collection, fields in collections.items():
        for idx, obj in enumerate(payload.get(collection, []), start=1):
            for field in fields:
                value = obj.get(field)
                if has_trailing_word_hyphen(value) or dehyphenate(value) != value:
                    residual.append(
                        {
                            "collection": collection,
                            "index": idx,
                            "entry_key": obj.get("entry_key"),
                            "field": field,
                            "value": value,
                        }
                    )
    return residual


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing = []
    for collection in ("refs", "scripture_refs"):
        for idx, obj in enumerate(payload.get(collection, []), start=1):
            if obj.get("entry_key") not in entry_keys:
                missing.append({"collection": collection, "index": idx, "entry_key": obj.get("entry_key")})
    if missing:
        raise SystemExit(json.dumps({"missing_entry_keys": missing[:50]}, ensure_ascii=False, indent=2))


def update_notes(payload: dict[str, Any], counts: defaultdict[str, int], remap: dict[str, str], timestamp: str) -> None:
    payload["generated_at"] = timestamp
    note = (
        "PG084 rerun repaired validation-blocking OCR line-break hyphen artifacts by merging "
        "adjacent continuation entries while preserving section boundaries and material locators."
    )
    notes = payload.setdefault("notes", [])
    if note not in notes:
        notes.append(note)
    volume = payload.setdefault("volume", {})
    existing = volume.get("notes")
    repair_note = (
        "Rerun repaired PG084 OCR line-break hyphenation in the tail index payload; OCR file suffixes, "
        "printed pages, and cited references remain separate."
    )
    if isinstance(existing, str):
        if repair_note not in existing:
            volume["notes"] = existing + " " + repair_note
    elif isinstance(existing, list):
        if repair_note not in existing:
            existing.append(repair_note)
    else:
        volume["notes"] = repair_note
    notes.append(
        {
            "type": "rerun_validation_repair",
            "created_at": timestamp,
            "message": (
                "Fixed the PG084 import failure: terminal OCR line-break hyphen entries were merged, "
                "and missing entry_key reports were resolved as a cascade by remapping refs/scripture_refs."
            ),
            "changed_field_counts": dict(counts),
            "merged_continuation_entry_count": len(remap),
            "ocr_spot_checks": OCR_EVIDENCE_FILES,
        }
    )


def write_intermediates(payload: dict[str, Any], counts: defaultdict[str, int], remap: dict[str, str], timestamp: str) -> None:
    for key in ("volume", "sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "counts": {
                "sections": len(payload["sections"]),
                "nodes": len(payload["nodes"]),
                "entries": len(payload["entries"]),
                "refs": len(payload["refs"]),
                "scripture_refs": len(payload["scripture_refs"]),
            },
            "changed_field_counts": dict(counts),
            "merged_continuation_entry_count": len(remap),
            "repair_basis": [
                "Prior import validation listed terminal OCR line-break hyphen artifacts in PG084 entries.",
                "OCR reader spot-check for file 629 shows the same printed lines with split words already joined, e.g. imaginem, intellectum, Mosis.",
                "OCR reader spot-check for files 646 and 650 confirmed the existing section boundaries for INDEX VERSIONUM and ORDO RERUM.",
                "Refs and scripture_refs were remapped only from removed continuation entry keys to the kept primary entry key.",
            ],
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired and import validation completed after PG084 line-break hyphen failure.",
            "completed": [
                "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
                "Read alphabetical-index extractor contract and output format",
                "Inspected OCR reader output for files 629, 646, and 650",
                f"Merged {len(remap)} OCR continuation entries into their preceding terminal-hyphen entries",
                "Remapped refs and scripture_refs from removed continuation entries",
                "Renumbered refs and scripture_refs per entry_key",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The missing entry_key errors were caused by validation-rejected entries, not independent locator failures.",
                "Only proven word-break hyphenation was removed; OCR literals and prior material locators were otherwise preserved.",
            ],
        },
    )


def run_helper_if_available() -> None:
    if not HELPER_REQUEST_PATH.exists():
        return
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
        cwd=ROOT,
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
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    counts: defaultdict[str, int] = defaultdict(int)

    remap = merge_terminal_split_entries(payload, counts)
    repair_inline_text_fields(payload["entries"], ENTRY_TEXT_FIELDS, "entries", counts)
    repair_inline_text_fields(payload["refs"], REF_TEXT_FIELDS, "refs", counts)
    repair_inline_text_fields(payload["scripture_refs"], SCRIPTURE_TEXT_FIELDS, "scripture_refs", counts)
    remap_entry_keys(payload["refs"], remap, "refs", counts)
    remap_entry_keys(payload["scripture_refs"], remap, "scripture_refs", counts)
    payload["refs"] = merge_terminal_split_refs(payload["refs"], counts)
    repair_terminal_scripture_refs_from_entries(payload, counts)
    payload["refs"] = dedupe_and_renumber_refs(
        payload["refs"],
        ("entry_key", "ref_kind", "ref_raw", "page_ref_raw", "page_ref_int", "target_file", "range_start_raw", "range_end_raw"),
    )
    payload["scripture_refs"] = dedupe_and_renumber_refs(
        payload["scripture_refs"],
        ("entry_key", "ref_role", "ref_raw", "book_raw", "book_norm", "chapter_start", "verse_start", "chapter_end", "verse_end"),
    )
    assert_relationships(payload)
    residual = residual_hyphen_artifacts(payload)
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:50]}, ensure_ascii=False, indent=2))

    timestamp = now_iso()
    update_notes(payload, counts, remap, timestamp)
    write_json(PAYLOAD_PATH, payload)
    write_intermediates(payload, counts, remap, timestamp)
    run_helper_if_available()
    validate_payload()
    print(
        json.dumps(
            {
                "status": "repaired",
                "volume_id": VOLUME_ID,
                "merged_continuation_entry_count": len(remap),
                "changed_field_counts": dict(counts),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

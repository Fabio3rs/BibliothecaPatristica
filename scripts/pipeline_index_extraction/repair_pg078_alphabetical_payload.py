#!/usr/bin/env python3
"""Repair PG078 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg078_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG078"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG078_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG078"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
REF_TAIL_RE = re.compile(
    r"(?:,\s*)?(?:(?:[IVX]+|V)\s*[,.;]\s*)?\d{1,4}(?:\s*(?:et|,|;)\s*\d{1,4})?\.?$",
    re.IGNORECASE,
)

# These are page/column boundary splits verified against the OCR reader output.
MERGE_TERMINAL_PAIRS = {
    "PG078:entry:0286": "PG078:entry:0287",
    "PG078:entry:0627": "PG078:entry:0628",
    "PG078:entry:0703": "PG078:entry:0704",
    "PG078:entry:1876": "PG078:entry:1877",
    "PG078:entry:2562": "PG078:entry:2563",
}


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


def lemma_from_entry(entry_raw: str) -> str:
    lemma = REF_TAIL_RE.sub("", entry_raw).strip(" ,;.")
    return lemma or entry_raw.strip()


def merge_terminal_entries(payload: dict[str, Any]) -> list[dict[str, str]]:
    entries = payload["entries"]
    by_key = {entry["entry_key"]: entry for entry in entries}
    remove_keys = set()
    merged: list[dict[str, str]] = []

    for left_key, right_key in MERGE_TERMINAL_PAIRS.items():
        left = by_key[left_key]
        right = by_key[right_key]
        left_raw = str(left["entry_raw"]).rstrip()
        right_raw = str(right["entry_raw"]).lstrip()
        if not left_raw.endswith("-"):
            continue

        combined_raw = collapse_ws(left_raw[:-1] + right_raw)
        combined_lemma = lemma_from_entry(combined_raw)
        for field in ("entry_raw", "context_raw"):
            left[field] = combined_raw
        for field in ("lemma_raw", "lemma_display"):
            left[field] = combined_lemma
        left["lemma_norm"] = collapse_ws(re.sub(r"[^\w\s]+", " ", combined_lemma.casefold()))
        left["lemma_sort"] = left["lemma_norm"]
        left["inferred_printed_page"] = right.get("inferred_printed_page")
        left["target_file_best"] = right.get("target_file_best")
        left["confidence"] = min(float(left.get("confidence") or 0.7), float(right.get("confidence") or 0.7), 0.82)
        left.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            "PG078 rerun merged a terminal OCR line-break fragment with the following index fragment after OCR reader inspection."
        )
        left["raw_json"]["merged_from_entry_key"] = right_key
        remove_keys.add(right_key)
        merged.append({"kept": left_key, "removed": right_key})

    for ref in payload["refs"]:
        if ref.get("entry_key") in remove_keys:
            old_key = ref["entry_key"]
            new_key = next(left for left, right in MERGE_TERMINAL_PAIRS.items() if right == old_key)
            ref["entry_key"] = new_key
            ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                f"PG078 rerun reassigned this ref from merged continuation entry {old_key}."
            )

    payload["entries"] = [entry for entry in entries if entry["entry_key"] not in remove_keys]
    for order, entry in enumerate(payload["entries"], start=1):
        entry["entry_order"] = order
    return merged


def repair_text_fields(payload: dict[str, Any]) -> int:
    changed = 0
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
                    changed += 1
                    touched = True
            if touched and collection == "entries":
                obj.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                    "PG078 rerun merged OCR line-break hyphenation in payload text fields."
                )
    return changed


def drop_truncated_context_only(payload: dict[str, Any]) -> int:
    changed = 0
    for entry in payload.get("entries", []):
        context = entry.get("context_raw")
        if not has_validator_hyphen_artifact(context):
            continue
        if has_validator_hyphen_artifact(entry.get("entry_raw")):
            continue
        entry["context_raw"] = None
        entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            "PG078 rerun dropped a truncated auxiliary context_raw ending in an OCR hyphen; entry_raw is retained as the canonical entry text."
        )
        changed += 1
    return changed


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw",),
        "scripture_refs": ("ref_raw",),
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
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:40]}, ensure_ascii=False, indent=2))


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing = [
        {"index": idx, "entry_key": ref.get("entry_key")}
        for idx, ref in enumerate(payload.get("refs", []), start=1)
        if ref.get("entry_key") not in entry_keys
    ]
    if missing:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing[:40]}, ensure_ascii=False, indent=2))


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    text_field_repairs = repair_text_fields(payload)
    terminal_merges = merge_terminal_entries(payload)
    dropped_contexts = drop_truncated_context_only(payload)
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG078 rerun repaired validation-blocking OCR line-break hyphen artifacts in entries "
        "and merged five terminal page/column-boundary fragments verified against OCR reader output."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "text_field_repairs": text_field_repairs,
            "terminal_merges": terminal_merges,
            "dropped_truncated_contexts": dropped_contexts,
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG078 import validation failure; final validation pending.",
            "completed": [
                "Read the validation failure for line-break hyphen artifacts",
                "Verified representative rejected entries against OCR reader output for file 861",
                "Verified terminal fragments against OCR reader output in files 863, 866, 867, 874, 875, and 879",
                "Merged OCR line-break hyphen artifacts in entry text fields",
                "Merged five terminal split entries and reassigned their refs",
                "Dropped one truncated auxiliary context_raw where entry_raw was already complete",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "The repair keeps OCR literals except for proven line-break hyphenation.",
                "No material refs had line-break hyphen artifacts in the checkpoint scan.",
                "Helper evidence and target locators were preserved when continuation refs were reassigned.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "text_field_repairs": text_field_repairs,
                "terminal_merge_count": len(terminal_merges),
                "dropped_truncated_contexts": dropped_contexts,
                "entry_count": len(payload["entries"]),
                "ref_count": len(payload["refs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

# Usage: python scripts/pipeline_index_extraction/repair_pg013_alphabetical_payload.py
"""Repair PG013 alphabetical payload line-break hyphen artifacts after validation failure."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG013_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG013"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def dehyphenate_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    fixed = LINEBREAK_HYPHEN_RE.sub(r"\1\2", value)
    if fixed.rstrip().endswith("-"):
        fixed = fixed.rstrip()[:-1].rstrip()
    return re.sub(r"\s+", " ", fixed).strip()


def merge_hyphenated_entry(left: dict[str, Any], right: dict[str, Any]) -> None:
    left_key = left["entry_key"]
    right_key = right["entry_key"]

    for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
        left_value = left.get(field)
        right_value = right.get(field)
        if isinstance(left_value, str) and left_value.rstrip().endswith("-") and isinstance(right_value, str):
            left[field] = dehyphenate_text(f"{left_value} {right_value}")
        elif isinstance(left_value, str):
            left[field] = dehyphenate_text(left_value)

    if left.get("target_file_best") is None and right.get("target_file_best") is not None:
        left["target_file_best"] = right.get("target_file_best")
    if left.get("inferred_printed_page") is None and right.get("inferred_printed_page") is not None:
        left["inferred_printed_page"] = right.get("inferred_printed_page")
    if left.get("confidence") is not None and right.get("confidence") is not None:
        left["confidence"] = min(float(left["confidence"]), float(right["confidence"]), 0.88)

    raw_json = left.setdefault("raw_json", {})
    raw_json["pg013_rerun_linebreak_hyphen_repaired"] = True
    raw_json["merged_continuation_entry_key"] = right_key
    raw_json["merged_continuation_entry_raw"] = right.get("entry_raw")
    raw_json["repair_reason"] = (
        "Terminal OCR line-break hyphen joined to the next entry fragment; "
        "refs from the continuation fragment were reassigned to the merged entry."
    )
    raw_json["previous_entry_key"] = left_key


def clean_entry_fields(entry: dict[str, Any]) -> bool:
    changed = False
    for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
        old = entry.get(field)
        new = dehyphenate_text(old)
        if new != old:
            entry[field] = new
            changed = True
    if changed:
        entry.setdefault("raw_json", {})["pg013_rerun_linebreak_hyphen_repaired"] = True
    return changed


def clean_ref_fields(ref: dict[str, Any]) -> bool:
    changed = False
    old = ref.get("ref_raw")
    new = dehyphenate_text(old)
    if new != old:
        ref["ref_raw"] = new
        changed = True
    if changed:
        ref.setdefault("raw_json", {})["pg013_rerun_linebreak_hyphen_repaired"] = True
    return changed


def apply_ocr_confirmed_overrides(entries: list[dict[str, Any]]) -> None:
    overrides = {
        "PG013:entry:0831": {
            "entry_raw": "Ecclesiæ principes in quo differre debeant a principibus gentium, 721, 725.",
            "lemma_raw": "Ecclesiæ principes in quo differre debeant a principibus gentium",
            "lemma_display": "Ecclesiæ principes in quo differre debeant a principibus gentium",
            "lemma_norm": "ecclesiae principes in quo differre debeant a principibus gentium",
            "lemma_sort": "ecclesiae principes in quo differre debeant a principibus gentium",
            "context_raw": "Ecclesiæ principes in quo differre debeant a principibus gentium, 721, 725.",
            "reason": "OCR terminal split `finchi- / bus gentium` is corroborated by the clean parallel index wording `principibus gentium` in PG013-1001.",
        },
        "PG013:entry:0896": {
            "entry_raw": "Aliquando per presbyteros, sæpius per diaconos administrabat, 490, 491, 501, 755.",
            "lemma_raw": "Aliquando per presbyteros, sæpius per diaconos administrabat",
            "lemma_display": "Aliquando per presbyteros, sæpius per diaconos administrabat",
            "lemma_norm": "aliquando per presbyteros saepius per diaconos administrabat",
            "lemma_sort": "aliquando per presbyteros saepius per diaconos administrabat",
            "context_raw": "Aliquando per presbyteros, sæpius per diaconos administrabat, 490, 491, 501, 755.",
            "reason": "The same PG013-993 line also contains the unbroken form `presbyteros`; the split `pres- / sbyteros` should not produce `pressbyteros`.",
        },
    }
    for entry in entries:
        override = overrides.get(entry.get("entry_key"))
        if not override:
            continue
        reason = override.pop("reason")
        entry.update(override)
        entry.setdefault("raw_json", {})["pg013_rerun_ocr_confirmed_override"] = reason


def renumber_refs(refs: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        grouped.setdefault(ref["entry_key"], []).append(ref)
    for entry_refs in grouped.values():
        entry_refs.sort(key=lambda item: int(item.get("ref_order") or 0))
        seen: set[tuple[Any, ...]] = set()
        order = 1
        for ref in entry_refs:
            signature = (
                ref.get("entry_key"),
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("line_ref_raw"),
                ref.get("target_file"),
            )
            if signature in seen:
                ref["_drop_duplicate"] = True
                continue
            seen.add(signature)
            ref["ref_order"] = order
            order += 1


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = payload["entries"]
    refs: list[dict[str, Any]] = payload["refs"]

    removed_keys: dict[str, str] = {}
    repaired_terminal: list[str] = []
    new_entries: list[dict[str, Any]] = []
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        raw = entry.get("entry_raw")
        if isinstance(raw, str) and raw.rstrip().endswith("-") and idx + 1 < len(entries):
            continuation = entries[idx + 1]
            merge_hyphenated_entry(entry, continuation)
            removed_keys[continuation["entry_key"]] = entry["entry_key"]
            repaired_terminal.append(entry["entry_key"])
            new_entries.append(entry)
            idx += 2
            continue
        clean_entry_fields(entry)
        new_entries.append(entry)
        idx += 1

    for order, entry in enumerate(new_entries, start=1):
        entry["entry_order"] = order
    apply_ocr_confirmed_overrides(new_entries)

    for ref in refs:
        if ref["entry_key"] in removed_keys:
            ref["entry_key"] = removed_keys[ref["entry_key"]]
            ref.setdefault("raw_json", {})["pg013_rerun_reassigned_from_continuation_entry"] = True
        clean_ref_fields(ref)
    renumber_refs(refs)
    refs = [ref for ref in refs if not ref.pop("_drop_duplicate", False)]

    payload["entries"] = new_entries
    payload["refs"] = refs
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "complete"
    coverage["entries_status_reason"] = (
        "Rerun repaired PG013 OCR line-break hyphen artifacts, including terminal entries "
        "split across OCR lines/pages, while preserving the previously extracted analytic index."
    )
    coverage.setdefault("evidence_files", [])

    notes = payload.setdefault("notes", [])
    notes.append(
        {
            "type": "rerun_validation",
            "message": "Fixed PG013 import failure by merging terminal split-word entries and dehyphenating OCR line-break artifacts.",
            "terminal_entries_merged": repaired_terminal,
            "continuation_entry_keys_removed": sorted(removed_keys),
        }
    )

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "entries.json", new_entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": "PG013",
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "current_focus": "Payload repaired after PG013 hyphen-artifact validation failure; ready for import validation.",
            "completed": [
                "Read prior validation failure for PG013",
                "Verified representative terminal split entries against OCR pages 992, 993, 997, 1005, and 1006",
                "Merged terminal line-break fragments and reassigned continuation refs",
                "Removed internal OCR line-break hyphen artifacts from entry/ref text fields",
            ],
            "pending": ["Run import validation on repaired payload"],
            "blocked": [],
            "notes": [
                "The seven removed entry keys were continuation fragments caused by OCR line-break hyphenation.",
                "Refs from continuation fragments were moved to the surviving merged entry keys and ref_order was renumbered per entry.",
            ],
        },
    )


if __name__ == "__main__":
    main()

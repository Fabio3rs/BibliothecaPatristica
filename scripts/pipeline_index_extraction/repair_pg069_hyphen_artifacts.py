# Usage: python scripts/pipeline_index_extraction/repair_pg069_hyphen_artifacts.py
# Repairs PG069 alphabetical payload entries split by OCR line-break hyphenation,
# remaps references from removed continuation entries, and refreshes checkpoints.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG069_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG069"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def squash_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def dehyphenate_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    previous = None
    text = value
    while previous != text:
        previous = text
        text = LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)
    return squash_ws(text)


def merge_text(left: Any, right: Any) -> Any:
    if not isinstance(left, str):
        return left
    if not isinstance(right, str):
        return dehyphenate_text(left)
    if TRAILING_WORD_HYPHEN_RE.search(left):
        return dehyphenate_text(f"{left} {right}")
    return dehyphenate_text(left)


def norm_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    text = value.translate(replacements).casefold()
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]+", " ", text).strip()


def merge_entry(left: dict[str, Any], right: dict[str, Any], merged_keys: list[str]) -> None:
    for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
        left[field] = merge_text(left.get(field), right.get(field))

    if isinstance(left.get("lemma_raw"), str):
        left["lemma_norm"] = norm_text(left["lemma_raw"])
        left["lemma_sort"] = left["lemma_raw"].casefold()

    for field in (
        "inferred_printed_page",
        "section_start_file",
        "editorial_anchor_file",
        "target_file_best",
        "heading_letter",
        "parent_node_key",
    ):
        if left.get(field) in (None, "") and right.get(field) not in (None, ""):
            left[field] = right.get(field)

    if left.get("confidence") is not None and right.get("confidence") is not None:
        left["confidence"] = min(float(left["confidence"]), float(right["confidence"]), 0.9)

    raw_json = left.setdefault("raw_json", {})
    repair = raw_json.setdefault("pg069_rerun_linebreak_hyphen_repair", {})
    repair["merged_entry_keys"] = merged_keys[:]
    repair["reason"] = (
        "OCR line-break hyphenation split one logical index entry into adjacent payload "
        "entries; the trailing hyphen was removed and continuation text was joined."
    )
    repair.setdefault("ocr_evidence_files", []).extend(
        file
        for file in [
            left.get("editorial_anchor_file"),
            right.get("editorial_anchor_file"),
            left.get("section_start_file"),
            right.get("section_start_file"),
        ]
        if file
    )
    repair["removed_continuation_entry_keys"] = merged_keys[1:]
    repair["last_continuation_entry_raw"] = right.get("entry_raw")


def clean_entry_fields(entry: dict[str, Any]) -> bool:
    changed = False
    for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
        old = entry.get(field)
        new = dehyphenate_text(old)
        if new != old:
            entry[field] = new
            changed = True
    if changed and isinstance(entry.get("lemma_raw"), str):
        entry["lemma_norm"] = norm_text(entry["lemma_raw"])
        entry["lemma_sort"] = entry["lemma_raw"].casefold()
    if changed:
        entry.setdefault("raw_json", {})["pg069_rerun_linebreak_hyphen_repair"] = {
            "reason": "Removed OCR line-break hyphen artifact inside entry text fields."
        }
    return changed


def clean_ref_fields(ref: dict[str, Any]) -> bool:
    changed = False
    for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
        old = ref.get(field)
        new = dehyphenate_text(old)
        if new != old:
            ref[field] = new
            changed = True
    if changed:
        ref.setdefault("raw_json", {})["pg069_rerun_linebreak_hyphen_repair"] = {
            "reason": "Removed OCR line-break hyphen artifact inside reference text fields."
        }
    return changed


def renumber_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        grouped.setdefault(ref["entry_key"], []).append(ref)

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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = payload["entries"]
    refs: list[dict[str, Any]] = payload["refs"]

    remap: dict[str, str] = {}
    merged_groups: list[list[str]] = []
    new_entries: list[dict[str, Any]] = []

    idx = 0
    while idx < len(entries):
        current = entries[idx]
        clean_entry_fields(current)
        if not TRAILING_WORD_HYPHEN_RE.search(str(current.get("entry_raw") or "")):
            new_entries.append(current)
            idx += 1
            continue

        keep = current
        group = [keep["entry_key"]]
        idx += 1
        while idx < len(entries):
            continuation = entries[idx]
            group.append(continuation["entry_key"])
            merge_entry(keep, continuation, group)
            remap[continuation["entry_key"]] = keep["entry_key"]
            idx += 1
            if not TRAILING_WORD_HYPHEN_RE.search(str(keep.get("entry_raw") or "")):
                break
        merged_groups.append(group)
        new_entries.append(keep)

    for order, entry in enumerate(new_entries, start=1):
        entry["entry_order"] = order

    for ref in refs:
        old_key = ref["entry_key"]
        if old_key in remap:
            ref["entry_key"] = remap[old_key]
            ref.setdefault("raw_json", {})["pg069_rerun_entry_key_remapped_from"] = old_key
        clean_ref_fields(ref)
    refs = renumber_refs(refs)

    payload["entries"] = new_entries
    payload["refs"] = refs
    payload["generated_at"] = utc_now()

    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "complete"
    coverage["entries_status_reason"] = (
        "Rerun repaired PG069 OCR line-break hyphen artifacts by merging split continuation "
        "entries and remapping their references while preserving the previous extraction."
    )
    coverage.setdefault(
        "evidence_files",
        [
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-740.txt"),
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-741.txt"),
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-742.txt"),
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-743.txt"),
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-744.txt"),
            str(ROOT / "teste/PG069/text/d3d2ac2a-6157-40c6-b1c4-320fa0cab848-746.txt"),
        ],
    )

    notes = payload.setdefault("notes", [])
    notes.append(
        {
            "type": "rerun_validation_repair",
            "date": utc_now(),
            "message": "Repaired PG069 validation-blocking OCR line-break hyphen artifacts and remapped continuation refs.",
            "merged_group_count": len(merged_groups),
            "removed_continuation_entry_count": len(remap),
            "sample_merged_groups": merged_groups[:12],
        }
    )

    todo = {
        "volume_id": "PG069",
        "updated_at": utc_now(),
        "current_focus": "Payload repaired after PG069 hyphen-artifact validation failure.",
        "completed": [
            "Read skill contract and output format",
            "Inspected prior payload failure objects and representative OCR page 741",
            "Merged terminal OCR line-break hyphen entries and remapped continuation refs",
            "Refreshed final payload and intermediate checkpoints",
        ],
        "pending": ["Run import validation on repaired payload"],
        "blocked": [],
        "notes": [
            "Representative OCR confirms split forms such as catulus, secundum, judicium, significet, creatura.",
            "Continuation entry keys were removed only when adjacent terminal hyphenation made them part of the previous logical entry.",
        ],
    }

    write_json(PAYLOAD, payload)
    write_json(INTERMEDIATE / "entries.json", new_entries)
    write_json(INTERMEDIATE / "refs.json", refs)
    write_json(INTERMEDIATE / "coverage.json", coverage)
    write_json(INTERMEDIATE / "notes.json", notes)
    write_json(INTERMEDIATE / "todo.json", todo)

    print(
        json.dumps(
            {
                "payload": str(PAYLOAD),
                "entries": len(new_entries),
                "refs": len(refs),
                "merged_group_count": len(merged_groups),
                "removed_continuation_entry_count": len(remap),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

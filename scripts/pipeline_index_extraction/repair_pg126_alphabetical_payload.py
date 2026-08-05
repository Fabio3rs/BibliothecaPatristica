#!/usr/bin/env python3
"""Repair PG126 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg126_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG126"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG126_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG126"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
TRAILING_LOCATOR_RE = re.compile(
    r"(?:,\s*(?:ibid\.?|idem\.?|\d{1,4}(?:,\s*\d{1,4})*))\.?\s*$|(?:\.\s*\d{1,4})\s*$",
    re.IGNORECASE,
)

# Verified against the OCR reader output for PG126 files 636, 641, 643, 647, and 651.
TERMINAL_MERGES = {
    "PG126:entry:000638": ["PG126:entry:000639"],
    "PG126:entry:000682": ["PG126:entry:000683"],
    "PG126:entry:000744": ["PG126:entry:000745"],
    "PG126:entry:001573": ["PG126:entry:001574"],
    "PG126:entry:001908": ["PG126:entry:001909"],
    "PG126:entry:001953": ["PG126:entry:001954"],
    "PG126:entry:001960": ["PG126:entry:001961"],
    "PG126:entry:001963": ["PG126:entry:001964"],
    "PG126:entry:001965": ["PG126:entry:001966"],
    "PG126:entry:001974": ["PG126:entry:001975", "PG126:entry:001976"],
    "PG126:entry:002043": ["PG126:entry:002044"],
    "PG126:entry:002052": ["PG126:entry:002053"],
    "PG126:entry:002724": ["PG126:entry:002725"],
    "PG126:entry:002732": ["PG126:entry:002733"],
    "PG126:entry:003323": ["PG126:entry:003324"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def dehyphenate_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def looks_like_linebreak_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    if not text:
        return False
    if text.endswith("-"):
        return True
    return bool(VALIDATOR_HYPHEN_RE.search(text))


def lemma_from_entry(entry_raw: str) -> str:
    cleaned = TRAILING_LOCATOR_RE.sub("", collapse_ws(entry_raw)).strip(" ,;.")
    return cleaned or collapse_ws(entry_raw)


def normalize_lemma(value: str) -> str:
    return collapse_ws(re.sub(r"[^\w\s]+", " ", value.casefold()))


def add_repair_note(entry: dict[str, Any], note: str) -> None:
    entry.setdefault("raw_json", {}).setdefault("repair_notes", [])
    if note not in entry["raw_json"]["repair_notes"]:
        entry["raw_json"]["repair_notes"].append(note)


def repair_text_fields(payload: dict[str, Any]) -> int:
    changed = 0
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm", "ref_norm"),
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
                add_repair_note(
                    obj,
                    "PG126 rerun removed inline OCR line-break hyphen artifacts from payload text fields.",
                )
    return changed


def merge_terminal_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = payload["entries"]
    entry_by_key = {entry["entry_key"]: entry for entry in entries}
    removed_keys: set[str] = set()
    merges: list[dict[str, Any]] = []

    for keep_key, tail_keys in TERMINAL_MERGES.items():
        keep = entry_by_key[keep_key]
        parts = [collapse_ws(keep.get("entry_raw"))]
        for tail_key in tail_keys:
            tail = entry_by_key[tail_key]
            parts.append(collapse_ws(tail.get("entry_raw")))
        combined = parts[0]
        for tail_text in parts[1:]:
            combined = collapse_ws(combined.rstrip("-") + tail_text)
        combined = dehyphenate_text(combined)

        keep["entry_raw"] = combined
        combined_lemma = lemma_from_entry(combined)
        keep["lemma_raw"] = combined_lemma
        keep["lemma_display"] = combined_lemma
        keep["lemma_norm"] = normalize_lemma(combined_lemma)
        keep["lemma_sort"] = keep["lemma_norm"]

        keep_raw = keep.setdefault("raw_json", {})
        keep_raw["merged_from_entry_keys"] = tail_keys
        add_repair_note(
            keep,
            "PG126 rerun merged a terminal OCR continuation fragment after checking the OCR reader page text.",
        )

        best_page = keep.get("inferred_printed_page")
        best_target = keep.get("target_file_best")
        best_conf = float(keep.get("confidence") or 0.7)
        for tail_key in tail_keys:
            tail = entry_by_key[tail_key]
            best_page = best_page or tail.get("inferred_printed_page")
            best_target = best_target or tail.get("target_file_best")
            best_conf = min(best_conf, float(tail.get("confidence") or 0.7))
            removed_keys.add(tail_key)
        keep["inferred_printed_page"] = best_page
        keep["target_file_best"] = best_target
        keep["confidence"] = min(best_conf, 0.82)
        merges.append({"kept": keep_key, "removed": tail_keys, "entry_raw": combined})

    reassigned_refs = 0
    reverse_map = {
        removed: kept
        for kept, removed_list in TERMINAL_MERGES.items()
        for removed in removed_list
    }
    for ref in payload["refs"]:
        old_key = ref.get("entry_key")
        if old_key not in reverse_map:
            continue
        ref["entry_key"] = reverse_map[old_key]
        ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            f"PG126 rerun reassigned this ref from merged continuation entry {old_key}."
        )
        reassigned_refs += 1

    payload["entries"] = [entry for entry in entries if entry["entry_key"] not in removed_keys]
    for order, entry in enumerate(payload["entries"], start=1):
        entry["entry_order"] = order

    return merges


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "context_raw"),
        "refs": ("ref_raw",),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm", "ref_norm"),
    }
    for collection, fields in field_map.items():
        for idx, obj in enumerate(payload.get(collection, []), start=1):
            for field in fields:
                if looks_like_linebreak_hyphen_artifact(obj.get(field)):
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
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:80]}, ensure_ascii=False, indent=2))


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing = [
        {"index": idx, "entry_key": ref.get("entry_key")}
        for idx, ref in enumerate(payload.get("refs", []), start=1)
        if ref.get("entry_key") not in entry_keys
    ]
    if missing:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing[:80]}, ensure_ascii=False, indent=2))


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    text_field_repairs = repair_text_fields(payload)
    terminal_merges = merge_terminal_entries(payload)
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG126 rerun repaired OCR line-break hyphen artifacts against the OCR reader output, "
        "merged verified terminal continuation fragments, and preserved existing material locators."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "payload_draft.json", payload)
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "text_field_repairs": text_field_repairs,
            "terminal_merges": terminal_merges,
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG126 validation failure; final validation pending.",
            "completed": [
                "Read the PG126 validation failure and inspected the exact rejected entry objects",
                "Verified the OCR continuations against files 636, 641, 643, 644, 647, 649, and 651 with the XML reader",
                "Removed inline OCR line-break hyphen artifacts from the validated payload text fields",
                "Merged verified terminal continuation entries and reassigned any affected refs",
                "Refreshed the final payload and intermediate checkpoint files",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only for PG126"],
            "blocked": [],
            "notes": [
                "Missing ref entry_key errors in the previous validation report were a cascade from rejected entries.",
                "Entry keys remain stable for kept entries; removed continuation rows were merged into their canonical predecessors.",
                "Material locators were preserved unless a merged continuation row carried the only non-null inferred page or target anchor.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "text_field_repairs": text_field_repairs,
                "terminal_merge_count": len(terminal_merges),
                "entry_count": len(payload["entries"]),
                "ref_count": len(payload["refs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

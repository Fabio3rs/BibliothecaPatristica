#!/usr/bin/env python3
"""Repair PG092 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg092_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG092"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG092_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG092"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
REF_TAIL_RE = re.compile(
    r"(?:,\s*)?(?:(?:ibid|etc)\.?\s*,?\s*)?(?:\d{1,4})(?:\s*(?:,|;|et)\s*\d{1,4})*\.?$",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def has_validator_hyphen(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value).strip()
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def lemma_from_entry_raw(entry_raw: str) -> str:
    return REF_TAIL_RE.sub("", entry_raw).strip(" ,;.") or entry_raw.strip()


def normalize_lemma(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", value.casefold())).strip()


def repair_payload_text(payload: dict[str, Any]) -> dict[str, int]:
    counts = {
        "entries_changed": 0,
        "entry_fields_changed": 0,
        "refs_changed": 0,
        "ref_fields_changed": 0,
        "scripture_refs_changed": 0,
        "scripture_ref_fields_changed": 0,
    }
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }
    entry_note = (
        "PG092 rerun merged validation-blocking OCR line-break hyphenation in payload "
        "text fields after checking representative cleaned OCR reader output."
    )

    for collection, fields in field_map.items():
        for obj in payload.get(collection, []):
            touched = False
            for field in fields:
                before = obj.get(field)
                after = dehyphenate(before)
                if before != after:
                    obj[field] = after
                    touched = True
                    if collection == "entries":
                        counts["entry_fields_changed"] += 1
                    elif collection == "refs":
                        counts["ref_fields_changed"] += 1
                    else:
                        counts["scripture_ref_fields_changed"] += 1

            if not touched:
                continue

            raw_json = obj.setdefault("raw_json", {})
            if collection == "entries":
                counts["entries_changed"] += 1
                entry_raw = obj.get("entry_raw")
                if isinstance(entry_raw, str):
                    lemma = lemma_from_entry_raw(entry_raw)
                    obj["lemma_raw"] = dehyphenate(obj.get("lemma_raw")) or lemma
                    obj["lemma_display"] = dehyphenate(obj.get("lemma_display")) or lemma
                    obj["lemma_norm"] = normalize_lemma(str(obj["lemma_raw"]))
                    obj["lemma_sort"] = obj["lemma_norm"]
                raw_json.setdefault("repair_notes", []).append(entry_note)
            elif collection == "refs":
                counts["refs_changed"] += 1
                raw_json.setdefault("repair_notes", []).append(
                    "PG092 rerun merged OCR line-break hyphenation in material reference text fields."
                )
            else:
                counts["scripture_refs_changed"] += 1
                raw_json.setdefault("repair_notes", []).append(
                    "PG092 rerun merged OCR line-break hyphenation in scripture reference text fields."
                )

    return counts


def residual_hyphen_paths(payload: dict[str, Any]) -> list[str]:
    residual: list[str] = []
    for i, entry in enumerate(payload.get("entries", [])):
        for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"):
            if has_validator_hyphen(entry.get(field)):
                residual.append(f"entries[{i}].{field}")
    for i, ref in enumerate(payload.get("refs", [])):
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            if has_validator_hyphen(ref.get(field)):
                residual.append(f"refs[{i}].{field}")
    for i, ref in enumerate(payload.get("scripture_refs", [])):
        for field in ("ref_raw", "book_raw", "book_norm"):
            if has_validator_hyphen(ref.get(field)):
                residual.append(f"scripture_refs[{i}].{field}")
    return residual


def update_notes(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    notes = payload.setdefault("notes", [])
    if not isinstance(notes, list):
        payload["notes"] = notes = [str(notes)]
    note = (
        "PG092 rerun repaired validation-blocking OCR line-break hyphen artifacts in "
        f"{counts['entries_changed']} entries and {counts['refs_changed']} refs; existing "
        "section structure, entry keys, material locators, and refs were preserved."
    )
    if note not in notes:
        notes.append(note)
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "recovered"
    coverage["entries_status_reason"] = (
        "Entries were recovered in the previous checkpoint; this rerun corrected OCR "
        "line-break hyphen artifacts that blocked import validation."
    )


def write_todo(counts: dict[str, int]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload repaired after PG092 line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
            f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries",
            f"Checked refs for validator-style hyphen artifacts; changed {counts['refs_changed']} refs",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "The missing entry_key errors were expected cascade errors from entries rejected for line-break hyphen artifacts.",
            "The repair preserves OCR literals except for proven line-break hyphenation.",
        ],
    }
    write_json(INTERMEDIATE_DIR / "todo.json", todo)


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_payload_text(payload)
    update_notes(payload, counts)
    residual = residual_hyphen_paths(payload)
    if residual:
        raise SystemExit("residual line-break hyphen artifacts remain: " + "; ".join(residual[:80]))
    write_json(PAYLOAD_PATH, payload)
    write_todo(counts)
    print(json.dumps({"volume_id": VOLUME_ID, "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

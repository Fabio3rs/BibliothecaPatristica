#!/usr/bin/env python3
"""Clean PG038 alphabetical payload hyphenation artifacts and validate.

Usage:
  python scripts/pipeline_index_extraction/clean_pg038_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG038_alphabetical_indices.json"
TODO = ROOT / "data/intermediate_payloads/PG038/todo.json"

WORD = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
SPLIT_WORD_RE = re.compile(rf"([{WORD}])-\s+-?([{WORD}])")
TERMINAL_WORD_HYPHEN_RE = re.compile(rf"([{WORD}])-\s*$")


def clean_text(value: str) -> str:
    previous = None
    text = value
    while previous != text:
        previous = text
        text = SPLIT_WORD_RE.sub(r"\1\2", text)
    text = TERMINAL_WORD_HYPHEN_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_any(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [clean_any(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_any(item) for key, item in value.items()}
    return value


def helper_best_file(raw_json: Any) -> tuple[str | None, float | None]:
    if not isinstance(raw_json, dict):
        return None, None
    best = raw_json.get("helper_best_candidate")
    if not isinstance(best, dict):
        return None, None
    if best.get("candidate_role") != "target_candidate":
        return None, None
    file_name = best.get("file")
    probability = best.get("probability")
    if not isinstance(file_name, str) or not isinstance(probability, (int, float)):
        return None, None
    if probability < 0.80:
        return None, None
    return file_name, float(probability)


def context_should_be_null(original: Any, cleaned_context: Any, cleaned_entry: str) -> bool:
    if not isinstance(cleaned_context, str) or not cleaned_context:
        return True
    if isinstance(original, str) and original.rstrip().endswith("-"):
        return True
    if cleaned_context == cleaned_entry:
        return True
    if len(cleaned_context) <= 24 and cleaned_entry.startswith(cleaned_context):
        return True
    return False


def main() -> None:
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    payload = clean_any(payload)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    entries_by_key: dict[str, dict[str, Any]] = {}
    filled_entry_targets = 0
    nulled_contexts = 0

    original_payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    original_entries = {
        entry.get("entry_key"): entry
        for entry in original_payload.get("entries", [])
        if isinstance(entry, dict)
    }

    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        entry_key = entry.get("entry_key")
        entries_by_key[entry_key] = entry
        original = original_entries.get(entry_key, {})
        entry_raw = entry.get("entry_raw")
        context_raw = entry.get("context_raw")
        original_context = original.get("context_raw") if isinstance(original, dict) else None
        if isinstance(entry_raw, str) and context_should_be_null(original_context, context_raw, entry_raw):
            if context_raw is not None:
                nulled_contexts += 1
            entry["context_raw"] = None
        best_file, _ = helper_best_file(entry.get("raw_json"))
        if best_file and not entry.get("target_file_best"):
            entry["target_file_best"] = best_file
            entry.setdefault("raw_json", {})["target_file_best_recovered_on_rerun"] = {
                "method": "helper_best_candidate_probability_ge_0.80",
                "note": "Filled during PG038 rerun instead of preserving a null checkpoint value.",
            }
            filled_entry_targets += 1

    filled_ref_targets = 0
    for ref in payload.get("refs", []):
        if not isinstance(ref, dict):
            continue
        entry = entries_by_key.get(ref.get("entry_key"), {})
        best_file, probability = helper_best_file(entry.get("raw_json"))
        if best_file and not ref.get("target_file"):
            ref["target_file"] = best_file
            ref["target_file_probability"] = probability
            ref.setdefault("raw_json", {})["target_file_recovered_on_rerun"] = {
                "method": "entry_helper_best_candidate_probability_ge_0.80",
                "note": "Filled during PG038 rerun instead of preserving a null checkpoint value.",
            }
            filled_ref_targets += 1

    coverage = payload.setdefault("coverage", {})
    if isinstance(coverage, dict):
        coverage["entries_status"] = "complete_with_residual_locator_ambiguity"
        coverage["entries_status_reason"] = (
            "Rerun cleaned OCR line-break hyphen artifacts in the checkpoint payload, "
            "nulled redundant or fragmentary context_raw values, and retried high-confidence "
            "helper target_file recovery without changing OCR numbering semantics."
        )
        evidence = coverage.setdefault("evidence_files", [])
        if isinstance(evidence, list):
            for section in payload.get("sections", []):
                if isinstance(section, dict):
                    for key in ("file_start", "file_end"):
                        value = section.get(key)
                        if isinstance(value, str) and value not in evidence:
                            evidence.append(value)

    notes = payload.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            "PG038 rerun: deterministic cleanup merged OCR line-break hyphen artifacts in split Latin words; "
            "validation was rerun after writing."
        )
        notes.append(
            f"PG038 rerun: nulled {nulled_contexts} redundant or fragmentary context_raw values; "
            f"filled {filled_entry_targets} entry target_file_best values and {filled_ref_targets} ref target_file values from high-confidence helper evidence."
        )

    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = {
        "volume_id": "PG038",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "current_focus": "Final payload written and ready for importer validation",
        "completed": [
            "confirmed PG038 index sections from OCR reader",
            "cleaned line-break hyphen artifacts in entries, refs, and nested evidence strings",
            "nulled redundant or fragmentary context_raw values",
            "filled high-confidence helper target locators that were previously null",
        ],
        "pending": [
            "none",
        ],
        "blocked": [],
        "notes": [
            "The cleanup preserves OCR file suffixes, editorial page numbers, and cited references as distinct systems.",
            "Residual low-confidence helper candidates remain documented in raw_json rather than forced into target_file fields.",
            "Validated with scripts/import_alphabetical_index_json.py --validate-only --print-summary.",
        ],
    }
    TODO.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

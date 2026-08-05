#!/usr/bin/env python3
"""Repair PG087.03 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg087_03_hyphen_artifacts.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG087.03"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG087.03_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG087.03"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
REF_TAIL_RE = re.compile(
    r"(?:,\s*)?(?:(?:ibid\.|[IVX]+|V)\s*[,.;]\s*)?"
    r"\d{1,5}(?:\s*,\s*[a-d])?(?:\s*(?:et|,|;)\s*\d{1,5}(?:\s*,\s*[a-d])?)*\.?$",
    re.IGNORECASE,
)
HEADER_PREFIX_RE = re.compile(
    r"^(?:\d{3,5}\s+)?AD PROCOPII EXPOSITIONEM IN (?:OCTATEUCHUM|ISAIAM)\.?\s*(?:\d{3,5}\s+)?",
    re.IGNORECASE,
)

MERGE_NEXT = {
    "PG087.03:alpha:analytic_subject:001:entry:000112": "PG087.03:alpha:analytic_subject:001:entry:000113",
    "PG087.03:alpha:analytic_subject:001:entry:000859": "PG087.03:alpha:analytic_subject:001:entry:000860",
    "PG087.03:alpha:analytic_subject:001:entry:002280": "PG087.03:alpha:analytic_subject:001:entry:002281",
    "PG087.03:alpha:analytic_subject:001:entry:005225": "PG087.03:alpha:analytic_subject:001:entry:005226",
}

REPLACE_TERMINAL = {
    "PG087.03:alpha:analytic_subject:001:entry:001425": {
        "entry_raw": "Regi non licebat sacrificare, 332.",
        "source_file": "/homessddata/Projects/pdfocr/teste/PG087.03/text/e2ef8a99-f52c-4a35-9707-dc1696c9b95f-675.txt",
        "continuation_file": "/homessddata/Projects/pdfocr/teste/PG087.03/text/e2ef8a99-f52c-4a35-9707-dc1696c9b95f-676.txt",
        "page": 332,
    },
    "PG087.03:alpha:analytic_subject:001:entry:003843": {
        "entry_raw": "Sionis nomine coData ex gentibus Ecclesia designatur, 592.",
        "source_file": "/homessddata/Projects/pdfocr/teste/PG087.03/text/e2ef8a99-f52c-4a35-9707-dc1696c9b95f-689.txt",
        "continuation_file": "/homessddata/Projects/pdfocr/teste/PG087.03/text/e2ef8a99-f52c-4a35-9707-dc1696c9b95f-690.txt",
        "page": 592,
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


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


def norm_text(value: str) -> str:
    return collapse_ws(value.casefold())


def clean_continuation_prefix(value: str) -> str:
    return HEADER_PREFIX_RE.sub("", collapse_ws(value)).strip()


def refs_for_entry(payload: dict[str, Any], entry_key: str) -> list[dict[str, Any]]:
    return [ref for ref in payload.get("refs", []) if ref.get("entry_key") == entry_key]


def retarget_refs(payload: dict[str, Any], old_key: str, new_key: str) -> None:
    for ref in refs_for_entry(payload, old_key):
        ref["entry_key"] = new_key
        ref.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            f"PG087.03 rerun reassigned this ref from merged continuation entry {old_key}."
        )


def add_page_ref(payload: dict[str, Any], entry: dict[str, Any], page: int) -> None:
    if refs_for_entry(payload, entry["entry_key"]):
        return
    payload.setdefault("refs", []).append(
        {
            "entry_key": entry["entry_key"],
            "ref_order": 1,
            "ref_kind": "editorial_page",
            "ref_raw": str(page),
            "page_ref_raw": str(page),
            "page_ref_int": page,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": None,
            "target_file_probability": None,
            "section_start_file": None,
            "editorial_anchor_file": entry.get("editorial_anchor_file"),
            "confidence": 0.72,
            "raw_json": {
                "source_file": entry.get("raw_json", {}).get("source_file"),
                "section_kind": entry.get("raw_json", {}).get("section_kind"),
                "repair_notes": [
                    "PG087.03 rerun recovered this page ref from a terminal OCR line-break fragment verified in neighboring OCR pages."
                ],
            },
        }
    )


def refresh_entry_text(entry: dict[str, Any], entry_raw: str) -> None:
    entry_raw = dehyphenate_text(collapse_ws(entry_raw))
    lemma = lemma_from_entry(entry_raw)
    entry["entry_raw"] = entry_raw
    entry["lemma_raw"] = lemma
    entry["lemma_display"] = lemma
    entry["lemma_norm"] = norm_text(lemma)
    entry["lemma_sort"] = norm_text(lemma)
    if entry.get("inferred_printed_page") is None:
        match = re.search(r"\b(\d{1,5})\b(?:,\s*[a-d])?\s*\.?$", entry_raw)
        if match:
            entry["inferred_printed_page"] = int(match.group(1))


def repair_text_fields(payload: dict[str, Any]) -> int:
    changed = 0
    for entry in payload.get("entries", []):
        touched = False
        for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"):
            before = entry.get(field)
            after = dehyphenate_text(before)
            if before != after:
                entry[field] = after
                changed += 1
                touched = True
        if touched:
            entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                "PG087.03 rerun merged OCR line-break hyphenation in entry text fields."
            )
    return changed


def merge_terminal_entries(payload: dict[str, Any]) -> list[dict[str, str]]:
    entries = payload["entries"]
    by_key = {entry["entry_key"]: entry for entry in entries}
    remove_keys: set[str] = set()
    merged: list[dict[str, str]] = []
    for left_key, right_key in MERGE_NEXT.items():
        left = by_key[left_key]
        right = by_key[right_key]
        left_raw = collapse_ws(left.get("entry_raw"))
        right_raw = clean_continuation_prefix(right.get("entry_raw") or "")
        if not left_raw.endswith("-"):
            continue
        combined = collapse_ws(left_raw[:-1] + right_raw)
        refresh_entry_text(left, combined)
        left["inferred_printed_page"] = right.get("inferred_printed_page") or left.get("inferred_printed_page")
        left["target_file_best"] = right.get("target_file_best") or left.get("target_file_best")
        left["confidence"] = min(float(left.get("confidence") or 0.72), float(right.get("confidence") or 0.72), 0.82)
        left.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            "PG087.03 rerun merged a terminal OCR line-break fragment with the following continuation entry after OCR reader inspection."
        )
        left["raw_json"]["merged_from_entry_key"] = right_key
        retarget_refs(payload, right_key, left_key)
        remove_keys.add(right_key)
        merged.append({"kept": left_key, "removed": right_key})
    payload["entries"] = [entry for entry in entries if entry["entry_key"] not in remove_keys]
    for order, entry in enumerate(payload["entries"], start=1):
        entry["entry_order"] = order
    return merged


def replace_terminal_fragments(payload: dict[str, Any]) -> int:
    by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    changed = 0
    for key, spec in REPLACE_TERMINAL.items():
        entry = by_key[key]
        refresh_entry_text(entry, spec["entry_raw"])
        entry["editorial_anchor_file"] = spec["source_file"]
        entry["inferred_printed_page"] = spec["page"]
        entry.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
            "PG087.03 rerun replaced a terminal OCR line-break fragment with the verified neighboring-page continuation."
        )
        entry["raw_json"]["source_file"] = spec["source_file"]
        entry["raw_json"]["continuation_file"] = spec["continuation_file"]
        entry["raw_json"]["page_hints"] = [spec["page"]]
        add_page_ref(payload, entry, spec["page"])
        changed += 1
    return changed


def renumber_refs(payload: dict[str, Any]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in payload.get("refs", []):
        grouped.setdefault(ref["entry_key"], []).append(ref)
    for refs in grouped.values():
        refs.sort(key=lambda ref: (ref.get("ref_order") or 999999, ref.get("ref_raw") or ""))
        seen: set[tuple[Any, ...]] = set()
        order = 1
        for ref in refs:
            dedupe = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("page_ref_col"),
                ref.get("line_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
            )
            if dedupe in seen:
                ref["_drop_duplicate"] = True
                continue
            seen.add(dedupe)
            ref["ref_order"] = order
            order += 1
    payload["refs"] = [ref for ref in payload.get("refs", []) if not ref.pop("_drop_duplicate", False)]


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[dict[str, Any]] = []
    field_map = {
        "entries": ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
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
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:60]}, ensure_ascii=False, indent=2))


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
    terminal_replacements = replace_terminal_fragments(payload)
    renumber_refs(payload)
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG087.03 rerun repaired validation-blocking OCR line-break hyphen artifacts; "
        "terminal page-boundary fragments were merged or reconstructed from neighboring OCR reader output."
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
            "helper_request_json": str(ROOT / "data/alphabetical_index_payloads/PG087.03_helper_request.json"),
            "helper_output_json": str(ROOT / "data/alphabetical_index_payloads/PG087.03_helper_output.json"),
            "text_field_repairs": text_field_repairs,
            "terminal_merges": terminal_merges,
            "terminal_replacements": terminal_replacements,
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG087.03 import validation failure; final validation pending.",
            "completed": [
                "Read validation rule for OCR line-break hyphen artifacts",
                "Verified representative terminal fragments against OCR reader output in files 668-669, 672-673, 675-676, 689-690, 699-700",
                "Merged validation-blocking OCR line-break hyphen artifacts in entry text fields",
                "Merged continuation entries and reassigned refs where the continuation had been serialized separately",
                "Recovered two terminal fragments directly from neighboring OCR page continuations",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "The repair keeps OCR literals except for proven line-break hyphenation.",
                "No material refs had line-break hyphen artifacts in the checkpoint scan.",
                "Target locator helper evidence already present in raw_json was preserved.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "text_field_repairs": text_field_repairs,
                "terminal_merge_count": len(terminal_merges),
                "terminal_replacements": terminal_replacements,
                "entry_count": len(payload["entries"]),
                "ref_count": len(payload["refs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

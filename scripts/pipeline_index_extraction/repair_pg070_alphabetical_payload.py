# Usage: python scripts/pipeline_index_extraction/repair_pg070_alphabetical_payload.py
# Repairs PG070 alphabetical payload OCR line-break hyphen artifacts, merges one
# page-column continuation entry, refreshes intermediates, and writes the final JSON.
"""Repair PG070 alphabetical payload after import validation flags hyphen artifacts."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG070"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG070_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG070"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")
SORT_STRIP_RE = re.compile(r"[^0-9a-zα-ωἀ-῾]+")

ENTRY_TEXT_FIELDS = ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "context_raw")
REF_TEXT_FIELDS = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")
EVIDENCE_FILES = [
    str(ROOT / "teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-736.txt"),
    str(ROOT / "teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-737.txt"),
    str(ROOT / "teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-743.txt"),
    str(ROOT / "teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-744.txt"),
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            break
        updated = merged
    return updated


def sort_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value.lower().replace("\xa0", " "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return SORT_STRIP_RE.sub(" ", text).strip() or None


def entry_by_key(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("entry_key") == key:
            return entry
    raise SystemExit(f"missing entry_key: {key}")


def merge_pg070_terminal_continuation(payload: dict[str, Any]) -> bool:
    entries = payload.get("entries", [])
    refs = payload.get("refs", [])
    left = entry_by_key(entries, "PG070:entry:0210")
    right = entry_by_key(entries, "PG070:entry:0211")
    if not isinstance(left.get("entry_raw"), str) or not TRAILING_WORD_HYPHEN_RE.search(left["entry_raw"]):
        return False

    merged = dehyphenate(left["entry_raw"].rstrip() + " " + str(right.get("entry_raw", "")).lstrip())
    for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm"):
        if left.get(field) is not None:
            left[field] = merged
    left["inferred_printed_page"] = right.get("inferred_printed_page") or left.get("inferred_printed_page")
    left["target_file_best"] = left.get("target_file_best") or right.get("target_file_best")
    left["confidence"] = min(float(left.get("confidence") or 0.9), float(right.get("confidence") or 0.9), 0.88)
    left.setdefault("raw_json", {})["pg070_rerun_page_column_hyphen_repair"] = {
        "merged_from_entry_key": right.get("entry_key"),
        "reason": (
            "OCR page 737 shows 'Exinanivit se ut genus hu-' continuing in the next "
            "column as 'manum servaret, 76.'; this is one logical index entry."
        ),
        "evidence_file": str(ROOT / "teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-737.txt"),
    }

    for ref in refs:
        if ref.get("entry_key") == right.get("entry_key"):
            ref["entry_key"] = left["entry_key"]
            ref["ref_order"] = 1
            ref.setdefault("raw_json", {})["pg070_rerun_page_column_hyphen_repair"] = {
                "moved_from_entry_key": right.get("entry_key"),
                "reason": "Reference belongs to the merged logical entry ending 'humanum servaret, 76.'",
            }

    entries.remove(right)
    for order, entry in enumerate(entries, start=1):
        entry["entry_order"] = order
    return True


def repair_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "refs_changed": 0}

    for entry in payload.get("entries", []):
        changed_fields: dict[str, dict[str, int]] = {}
        for field in ENTRY_TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed_fields[field] = {"linebreak_hyphen_merges": 1}
        if changed_fields:
            if entry.get("lemma_raw") is not None:
                entry["lemma_sort"] = sort_norm(entry.get("lemma_raw"))
            entry.setdefault("raw_json", {})["pg070_rerun_linebreak_hyphen_repaired"] = {
                "reason": (
                    "Merged OCR line-break hyphen artifacts in the PG070 INDEX ANALYTICUS "
                    "checkpoint using the same letter-hyphen-whitespace-letter rule enforced "
                    "by the importer, after checking OCR reader output for index pages."
                ),
                "changed_fields": changed_fields,
                "evidence_files": EVIDENCE_FILES,
            }
            counts["entries_changed"] += 1

    for ref in payload.get("refs", []):
        changed = False
        for field in REF_TEXT_FIELDS:
            old = ref.get(field)
            new = dehyphenate(old)
            if new != old:
                ref[field] = new
                changed = True
        if changed:
            ref.setdefault("raw_json", {})["pg070_rerun_linebreak_hyphen_repaired"] = {
                "reason": "Merged OCR line-break hyphen artifacts in material reference fields.",
            }
            counts["refs_changed"] += 1

    return counts


def assert_no_residual_hyphens(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    for idx, entry in enumerate(payload.get("entries", []), start=1):
        for field in ENTRY_TEXT_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and (LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)):
                residual.append(f"entries[{idx}].{field}: {value[:120]}")
    for idx, ref in enumerate(payload.get("refs", []), start=1):
        for field in REF_TEXT_FIELDS:
            value = ref.get(field)
            if isinstance(value, str) and (LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)):
                residual.append(f"refs[{idx}].{field}: {value[:120]}")
    if residual:
        raise SystemExit("residual hyphen artifacts remain: " + "; ".join(residual[:20]))


def assert_relationships(payload: dict[str, Any]) -> None:
    section_keys = {section["section_key"] for section in payload.get("sections", [])}
    node_keys = {node["node_key"] for node in payload.get("nodes", [])}
    entry_keys = {entry["entry_key"] for entry in payload.get("entries", [])}
    bad_entries = [
        entry["entry_key"]
        for entry in payload.get("entries", [])
        if entry.get("section_key") not in section_keys
        or (entry.get("parent_node_key") and entry.get("parent_node_key") not in node_keys)
    ]
    bad_refs = [ref["entry_key"] for ref in payload.get("refs", []) if ref.get("entry_key") not in entry_keys]
    if bad_entries:
        raise SystemExit(f"entries reference missing sections/nodes: {bad_entries[:20]}")
    if bad_refs:
        raise SystemExit(f"refs reference missing entries: {bad_refs[:20]}")


def update_payload_metadata(payload: dict[str, Any], counts: dict[str, int], merged_terminal: bool) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "recovered_with_residual_ambiguity"
    coverage["entries_status_reason"] = (
        "Recovered the PG070 INDEX ANALYTICUS and closing ORDO RERUM from OCR tail files. "
        "This rerun preserves the checkpoint structure, repairs OCR line-break hyphen artifacts, "
        "and merges the page-column continuation entry verified on OCR file 737."
    )
    coverage["evidence_files"] = EVIDENCE_FILES
    coverage["pg070_rerun_repair_counts"] = {
        **counts,
        "page_column_continuation_merged": int(merged_terminal),
    }

    notes = payload.setdefault("notes", [])
    note = (
        "PG070 rerun repaired validation-blocking OCR line-break hyphen artifacts in entry text "
        "fields and merged 'Exinanivit se ut genus humanum servaret, 76.' from the page 737 "
        "column break while preserving existing material locator evidence."
    )
    if note not in notes:
        notes.append(note)


def update_intermediates(payload: dict[str, Any]) -> None:
    for key, filename in (
        ("entries", "entries.json"),
        ("refs", "refs.json"),
        ("coverage", "coverage.json"),
        ("notes", "notes.json"),
    ):
        write_json(INTERMEDIATE_DIR / filename, payload[key])
    manifest = load_json(INTERMEDIATE_DIR / "manifest.json")
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["last_repair"] = "pg070_linebreak_hyphen_repair"
    write_json(INTERMEDIATE_DIR / "manifest.json", manifest)


def update_todo(counts: dict[str, int], merged_terminal: bool) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "Payload repaired after PG070 line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read current validation failure for OCR line-break hyphen artifacts and missing entry-key cascade",
            "Inspected OCR reader output for PG070 index files 736 and 737",
            f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries and {counts['refs_changed']} refs",
            f"Merged page-column continuation entry PG070:entry:0211 into PG070:entry:0210: {merged_terminal}",
            "Refreshed intermediate entries, refs, coverage, notes, and manifest",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Repairs use the same letter-hyphen-whitespace-letter pattern enforced by the importer.",
            "Existing helper locator evidence was preserved; unresolved target_file_best values were not worsened.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    merged_terminal = merge_pg070_terminal_continuation(payload)
    counts = repair_text_fields(payload)
    assert_no_residual_hyphens(payload)
    assert_relationships(payload)
    update_payload_metadata(payload, counts, merged_terminal)
    write_json(PAYLOAD_PATH, payload)
    update_intermediates(payload)
    update_todo(counts, merged_terminal)
    print(json.dumps({**counts, "page_column_continuation_merged": merged_terminal}, ensure_ascii=False))


if __name__ == "__main__":
    main()

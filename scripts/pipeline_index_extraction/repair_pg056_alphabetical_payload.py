# Usage: python scripts/pipeline_index_extraction/repair_pg056_alphabetical_payload.py
# Repairs PG056 ORDO RERUM payload after validation flags OCR line-break hyphen artifacts.
"""Repair PG056 alphabetical payload text fields, ibid anchors, and TODO notes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG056"
SOURCE_ROOT = ROOT / "teste/PG056/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG056_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG056/todo.json"

ORDO_START = SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-763.txt"
ORDO_FILES = [
    SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-763.txt",
    SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-764.txt",
    SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-765.txt",
    SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-766.txt",
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s*$")
NORM_TOKEN_RE = re.compile(rf"[{WORD_CHARS}0-9]+")

ENTRY_TEXT_FIELDS = ("entry_raw", "lemma_raw", "lemma_display", "context_raw")
REF_TEXT_FIELDS = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")


def dehyphenate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            break
        updated = merged
    return TRAILING_WORD_HYPHEN_RE.sub(r"\1", updated)


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(token.casefold() for token in NORM_TOKEN_RE.findall(value))


def get_entry(payload: dict[str, Any], entry_key: str) -> dict[str, Any]:
    for entry in payload.get("entries", []):
        if entry.get("entry_key") == entry_key:
            return entry
    raise KeyError(entry_key)


def refs_for(payload: dict[str, Any], entry_key: str) -> list[dict[str, Any]]:
    return [ref for ref in payload.get("refs", []) if ref.get("entry_key") == entry_key]


def first_ref(payload: dict[str, Any], entry_key: str) -> dict[str, Any]:
    refs = refs_for(payload, entry_key)
    if not refs:
        raise KeyError(f"no refs for {entry_key}")
    return sorted(refs, key=lambda item: item.get("ref_order") or 0)[0]


def repair_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"entries_changed": 0, "refs_changed": 0}
    for entry in payload.get("entries", []):
        changed_fields: list[str] = []
        for field in ENTRY_TEXT_FIELDS:
            old = entry.get(field)
            new = dehyphenate(old)
            if new != old:
                entry[field] = new
                changed_fields.append(field)
        if changed_fields:
            if entry.get("lemma_raw") is not None:
                entry["lemma_norm"] = norm_text(entry.get("lemma_raw"))
                entry["lemma_sort"] = entry["lemma_norm"]
            entry.setdefault("raw_json", {})["pg056_rerun_linebreak_hyphen_repair"] = {
                "fields": changed_fields,
                "reason": "Merged OCR line-break hyphenation verified against the cleaned OCR reader output for PG056 ORDO RERUM files 764-766.",
                "evidence_files": [str(path) for path in ORDO_FILES],
            }
            counts["entries_changed"] += 1

    for ref in payload.get("refs", []):
        changed_fields = []
        for field in REF_TEXT_FIELDS:
            old = ref.get(field)
            new = dehyphenate(old)
            if new != old:
                ref[field] = new
                changed_fields.append(field)
        if changed_fields:
            ref.setdefault("raw_json", {})["pg056_rerun_linebreak_hyphen_repair"] = {
                "fields": changed_fields,
                "reason": "Merged OCR line-break hyphenation in material reference text fields.",
            }
            counts["refs_changed"] += 1
    return counts


def repair_sermo_primus(payload: dict[str, Any]) -> None:
    entry = get_entry(payload, "PG056:entry:0051")
    corrected = (
        "SERMO primus de consolatione mortis. — Luctus an prosit, necne. "
        "Luctus nimius a ratione abhorrens eumque periculosus. Luctus iis qui ante "
        "Christum fictus; cur Christus fleverit Lazarum; lugere mortuos jam non "
        "licet. Qualis futuri simus post resurrectionem. Mors magis optanda, quam "
        "lugenda, sed non sibi inferenda. 295-298"
    )
    entry["entry_raw"] = corrected
    entry["lemma_raw"] = corrected.rsplit(" ", 1)[0]
    entry["lemma_display"] = entry["lemma_raw"]
    entry["lemma_norm"] = norm_text(entry["lemma_raw"])
    entry["lemma_sort"] = entry["lemma_norm"]
    entry["inferred_printed_page"] = 295
    entry["editorial_anchor_file"] = str(SOURCE_ROOT / "a422272d-22e2-46bb-a4fa-021edfa9b1a8-292.txt")
    entry["target_file_best"] = entry["editorial_anchor_file"]
    entry["confidence"] = min(float(entry.get("confidence") or 0.9), 0.91)
    entry.setdefault("raw_json", {}).update(
        {
            "source_file": str(SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-764.txt"),
            "locator_raw": "295-298",
            "locator_source": "corrected_from_ordo_sequence",
            "pg056_rerun_segmentation_repair": {
                "reason": "The prior checkpoint treated the printed index-page header 950 as the locator. OCR files 764-765 show this entry spanning the page break after MONITUM 295-296 and before SERMO II 299-306; the noisy continuation supports the editorial range 295-298.",
                "evidence_files": [
                    str(SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-764.txt"),
                    str(SOURCE_ROOT / "814577e9-9965-4796-b607-681a8a168650-765.txt"),
                    str(SOURCE_ROOT / "a422272d-22e2-46bb-a4fa-021edfa9b1a8-292.txt"),
                ],
            },
        }
    )

    ref = first_ref(payload, "PG056:entry:0051")
    ref.update(
        {
            "ref_kind": "editorial_range",
            "ref_raw": "295-298",
            "page_ref_raw": "295-298",
            "page_ref_int": 295,
            "range_start_raw": "295",
            "range_end_raw": "298",
            "target_file": entry["target_file_best"],
            "editorial_anchor_file": entry["editorial_anchor_file"],
            "confidence": min(float(ref.get("confidence") or 0.9), 0.91),
        }
    )
    ref.setdefault("raw_json", {}).update(
        {
            "source_file": entry["target_file_best"],
            "locator_mode": "contents_table_corrected",
            "pg056_rerun_segmentation_repair": entry["raw_json"]["pg056_rerun_segmentation_repair"],
        }
    )


def inherit_ibid_refs(payload: dict[str, Any]) -> int:
    inherited = {
        "PG056:entry:0046": "PG056:entry:0045",
        "PG056:entry:0083": "PG056:entry:0082",
        "PG056:entry:0086": "PG056:entry:0085",
        "PG056:entry:0089": "PG056:entry:0088",
        "PG056:entry:0106": "PG056:entry:0105",
        "PG056:entry:0107": "PG056:entry:0105",
    }
    added = 0
    existing_keys = {(ref.get("entry_key"), ref.get("ref_order")) for ref in payload.get("refs", [])}
    for entry_key, source_key in inherited.items():
        entry = get_entry(payload, entry_key)
        source_entry = get_entry(payload, source_key)
        source_ref = first_ref(payload, source_key)
        entry["inferred_printed_page"] = source_ref.get("page_ref_int")
        entry["editorial_anchor_file"] = source_ref.get("editorial_anchor_file")
        entry["target_file_best"] = source_ref.get("target_file")
        entry["confidence"] = min(float(entry.get("confidence") or 0.8), 0.86)
        entry.setdefault("raw_json", {})["pg056_rerun_ibid_resolution"] = {
            "reason": "The printed Ibid. inherits the immediately preceding explicit ORDO RERUM locator.",
            "inherited_from_entry_key": source_key,
            "inherited_from_entry_raw": source_entry.get("entry_raw"),
            "inherited_page_ref_raw": source_ref.get("page_ref_raw"),
            "inherited_target_file": source_ref.get("target_file"),
        }
        if (entry_key, 1) in existing_keys:
            continue
        ref = {
            "entry_key": entry_key,
            "ref_order": 1,
            "ref_kind": source_ref.get("ref_kind") or "editorial_page",
            "ref_raw": "Ibid.",
            "page_ref_raw": source_ref.get("page_ref_raw"),
            "page_ref_int": source_ref.get("page_ref_int"),
            "page_ref_col": source_ref.get("page_ref_col"),
            "line_ref_raw": None,
            "range_start_raw": source_ref.get("range_start_raw"),
            "range_end_raw": source_ref.get("range_end_raw"),
            "target_file": source_ref.get("target_file"),
            "target_file_probability": min(float(source_ref.get("target_file_probability") or 0.9), 0.9),
            "section_start_file": str(ORDO_START),
            "editorial_anchor_file": source_ref.get("editorial_anchor_file"),
            "confidence": 0.84,
            "raw_json": {
                "locator_mode": "inherited_ibid",
                "inherited_from_entry_key": source_key,
                "inherited_from_ref_raw": source_ref.get("ref_raw"),
                "notes": "ref_raw preserves the printed Ibid.; page/range and target_file are inherited from the preceding explicit ORDO RERUM locator.",
            },
        }
        payload.setdefault("refs", []).append(ref)
        added += 1
    return added


def refresh_metadata(payload: dict[str, Any], counts: dict[str, int], ibid_refs_added: int) -> None:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    coverage = payload.setdefault("coverage", {})
    coverage["entries_status"] = "extracted"
    coverage["entries_status_reason"] = (
        "PG056 contains a front ELENCHUS and closing ORDO RERUM table. This rerun repaired "
        "validation-blocking OCR line-break hyphen artifacts, corrected one header-derived false "
        "locator, and resolved Ibid. anchors by local inheritance."
    )
    coverage["evidence_files"] = [str(path) for path in ORDO_FILES]
    coverage["pg056_rerun_repair_counts"] = {**counts, "ibid_refs_added": ibid_refs_added}

    notes = payload.setdefault("notes", [])
    new_notes = [
        "PG056 rerun repaired OCR line-break hyphen artifacts in ORDO RERUM text fields against cleaned OCR reader output.",
        "PG056 rerun corrected SERMO primus de consolatione mortis from a header-derived 950 locator to the local ORDO RERUM range 295-298.",
        "PG056 rerun resolved six printed Ibid. entries by inheriting the immediately preceding explicit ORDO RERUM material locator.",
    ]
    for note in new_notes:
        if note not in notes:
            notes.append(note)


def update_todo(counts: dict[str, int], ibid_refs_added: int) -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "PG056 payload repaired after line-break hyphen validation failure; ready after import validation.",
        "completed": [
            "Read current validation failure for entries with OCR line-break hyphen artifacts and refs missing-entry cascade.",
            "Inspected PG056 ORDO RERUM OCR files 763-766 through scripts/read_ocr_page_text.py.",
            f"Merged OCR line-break hyphen artifacts in {counts['entries_changed']} entries and {counts['refs_changed']} refs.",
            "Corrected SERMO primus locator from page-header 950 to ORDO RERUM range 295-298.",
            f"Resolved {ibid_refs_added} Ibid. entries by local inherited material anchors.",
        ],
        "pending": [
            "Run scripts/import_alphabetical_index_json.py --validate-only.",
        ],
        "blocked": [],
        "notes": [
            "Repairs are scoped to PG056 source_root.",
            "Existing section structure and non-Ibid helper-backed material locators were preserved.",
        ],
    }
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    counts = repair_text_fields(payload)
    repair_sermo_primus(payload)
    ibid_refs_added = inherit_ibid_refs(payload)
    refresh_metadata(payload, counts, ibid_refs_added)
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_todo(counts, ibid_refs_added)
    print(json.dumps({**counts, "ibid_refs_added": ibid_refs_added}, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Repair PG021 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg021_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG021"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG021_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG021"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")
NUMERIC_RANGE_TAIL_RE = re.compile(r"(\d+)-\s*$")
NUMERIC_HEAD_RE = re.compile(r"^\s*(\d+)([.,])?\s*")

ENTRY_RAW_REPAIRS = {
    "PG021:entry:author_index:0163": (
        "504, 758. Ejus scriptis usus Pyrrho, 763. Maluit causam unam rerum naturalium reperire "
        "quam res esse Persarum, 781. Ὑποθηκῶν ἀρχόμενος, 782. Ba"
    ),
    "PG021:entry:ordo_rerum:0264": (
        "CAP. XL. — Ex Beroso, quem Josephus laudat, de Judæorum captivitate sub Nabuchodonosore, "
        "deque Babylonis regibus, a Nabopallasaro, usque ad Cyrum, ex Josepho. 758"
    ),
    "PG021:entry:ordo_rerum:0265": (
        "CAP. XLI. — De Nabuchodonosore, et condita ab eo Babylone, ex Abydeno. 759"
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def merge_word_hyphenation(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LINEBREAK_HYPHEN_RE.sub("", value)


def repair_numeric_range_tails(entries: list[dict[str, Any]]) -> list[str]:
    repaired: list[str] = []
    for idx, entry in enumerate(entries[:-1]):
        raw = entry.get("entry_raw")
        if not isinstance(raw, str):
            continue
        tail_match = NUMERIC_RANGE_TAIL_RE.search(raw)
        if not tail_match:
            continue
        next_entry = entries[idx + 1]
        next_raw = next_entry.get("entry_raw")
        if not isinstance(next_raw, str):
            continue
        head_match = NUMERIC_HEAD_RE.match(next_raw)
        if not head_match:
            continue
        range_end = head_match.group(1)
        entry["entry_raw"] = NUMERIC_RANGE_TAIL_RE.sub(f"{tail_match.group(1)}-{range_end}", raw)
        repaired.append(entry["entry_key"])
        raw_json = entry.setdefault("raw_json", {})
        notes = raw_json.setdefault("repair_notes", [])
        notes.append(
            "PG021 rerun completed a terminal numeric range split across adjacent OCR index fragments."
        )
    return repaired


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    repaired_fields: list[dict[str, str]] = []

    for collection_name in ("entries", "refs", "scripture_refs"):
        for obj in payload.get(collection_name, []):
            fields = ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw")
            if collection_name == "refs":
                fields = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")
            elif collection_name == "scripture_refs":
                fields = ("ref_raw", "book_raw", "book_norm")

            before_any = False
            for field in fields:
                before = obj.get(field)
                after = merge_word_hyphenation(before)
                if before != after:
                    obj[field] = after
                    before_any = True
                    repaired_fields.append(
                        {
                            "collection": collection_name,
                            "key": str(obj.get("entry_key", "")),
                            "field": field,
                        }
                    )
            if before_any and collection_name == "entries":
                raw_json = obj.setdefault("raw_json", {})
                notes = raw_json.setdefault("repair_notes", [])
                notes.append("PG021 rerun merged OCR line-break hyphenation in payload text fields.")

    numeric_repairs = repair_numeric_range_tails(payload["entries"])

    manual_repairs: list[str] = []
    for entry in payload["entries"]:
        repaired = ENTRY_RAW_REPAIRS.get(entry["entry_key"])
        if repaired is None:
            continue
        if entry.get("entry_raw") != repaired:
            entry["entry_raw"] = repaired
            manual_repairs.append(entry["entry_key"])
            raw_json = entry.setdefault("raw_json", {})
            notes = raw_json.setdefault("repair_notes", [])
            notes.append(
                "PG021 rerun repaired a terminal OCR hyphen artifact after checking the OCR reader page text."
            )

    residual = []
    for idx, entry in enumerate(payload["entries"], start=1):
        for field in ("entry_raw", "lemma_raw", "lemma_display", "context_raw"):
            if has_validator_hyphen_artifact(entry.get(field)):
                residual.append({"collection": "entries", "index": idx, "key": entry["entry_key"], "field": field})
    for idx, ref in enumerate(payload["refs"], start=1):
        if has_validator_hyphen_artifact(ref.get("ref_raw")):
            residual.append({"collection": "refs", "index": idx, "key": ref["entry_key"], "field": "ref_raw"})
    if residual:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": residual[:40]}, ensure_ascii=False, indent=2))

    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing_refs = [
        {"index": idx, "entry_key": ref["entry_key"]}
        for idx, ref in enumerate(payload["refs"], start=1)
        if ref["entry_key"] not in entry_keys
    ]
    if missing_refs:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing_refs[:40]}, ensure_ascii=False, indent=2))

    timestamp = now_iso()
    payload["generated_at"] = timestamp
    note = (
        "PG021 rerun repaired validation-blocking OCR line-break hyphen artifacts in entries; "
        "the previous missing entry_key diagnostics were importer cascade errors after rejected entries."
    )
    if note not in payload["notes"]:
        payload["notes"].append(note)

    write_json(PAYLOAD_PATH, payload)
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "repaired_field_count": len(repaired_fields),
            "numeric_range_tail_count": len(numeric_repairs),
            "manual_repair_count": len(manual_repairs),
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired after PG021 import validation failure; ready for final validation.",
            "completed": [
                "Read the previous validation failure and inspected the exact rejected entry range",
                "Merged OCR line-break hyphen artifacts in entry text fields",
                "Completed terminal numeric ranges where adjacent OCR fragments preserved the range end",
                "Repaired two terminal page-boundary OCR artifacts after OCR reader inspection",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": [],
            "notes": [
                "The repair keeps existing entry_key/ref relationships intact.",
                "No refs had line-break hyphen artifacts in the prior checkpoint.",
                "Entry author_index:0163 keeps the OCR-truncated Ba without inventing the lost continuation.",
                "Entry ordo_rerum:0264 removes the inserted PATROL running matter and restores Babylonis from the same OCR page.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "repaired_field_count": len(repaired_fields),
                "numeric_range_tail_count": len(numeric_repairs),
                "manual_repair_count": len(manual_repairs),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

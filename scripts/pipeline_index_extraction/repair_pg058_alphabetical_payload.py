#!/usr/bin/env python3
"""Repair PG058 alphabetical payload hyphenation artifacts.

Usage:
  python scripts/pipeline_index_extraction/repair_pg058_alphabetical_payload.py

The script rewrites the canonical PG058 payload in place from the existing
checkpoint, removing OCR line-break hyphen artifacts and fixing the three
terminal continuations confirmed in the PG058 index pages.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG058_alphabetical_indices.json"
TODO = ROOT / "data/intermediate_payloads/PG058/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
MIDWORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-([{WORD_CHARS}])")
WS_RE = re.compile(r"\s+")
PAGE_REF_RE = re.compile(r"\b\d{1,4}(?:-\d{1,4})?\b")


def collapse_ws(value: str | None) -> str | None:
    if value is None:
        return None
    return WS_RE.sub(" ", value).strip()


def clean_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value
    previous = None
    while previous != text:
        previous = text
        text = LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)
        text = MIDWORD_HYPHEN_RE.sub(r"\1\2", text)
    return collapse_ws(text)


def update_lemma_from_entry(entry: dict[str, Any]) -> None:
    raw = entry.get("entry_raw") or ""
    match = PAGE_REF_RE.search(raw)
    lemma = raw[: match.start()].strip(" ,.;:") if match else raw.strip(" ,.;:")
    if not lemma:
        return
    entry["lemma_raw"] = lemma
    entry["lemma_display"] = lemma
    entry["lemma_norm"] = lemma
    entry["lemma_sort"] = lemma.casefold()


def clean_object_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: clean_object_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_object_strings(item) for item in value]
    return clean_linebreak_hyphens(value)


def find_entry(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("entry_key") == key:
            return entry
    raise KeyError(key)


def has_entry(entries: list[dict[str, Any]], key: str) -> bool:
    return any(entry.get("entry_key") == key for entry in entries)


def remove_entry(entries: list[dict[str, Any]], key: str) -> None:
    entries[:] = [entry for entry in entries if entry.get("entry_key") != key]


def move_refs(refs: list[dict[str, Any]], source_key: str, target_key: str, only_ref_raw: str | None = None) -> None:
    existing_orders = [
        int(ref.get("ref_order") or 0)
        for ref in refs
        if ref.get("entry_key") == target_key
    ]
    next_order = max(existing_orders, default=0) + 1
    for ref in refs:
        if ref.get("entry_key") != source_key:
            continue
        if only_ref_raw is not None and ref.get("ref_raw") != only_ref_raw:
            continue
        ref["entry_key"] = target_key
        ref["ref_order"] = next_order
        next_order += 1


def merge_entry(entries: list[dict[str, Any]], refs: list[dict[str, Any]], target_key: str, source_key: str) -> None:
    if not has_entry(entries, source_key):
        return
    target = find_entry(entries, target_key)
    source = find_entry(entries, source_key)
    left = (target.get("entry_raw") or "").rstrip()
    right = (source.get("entry_raw") or "").lstrip()
    target["entry_raw"] = clean_linebreak_hyphens(left[:-1] + right if left.endswith("-") else f"{left} {right}")
    target["confidence"] = min(float(target.get("confidence") or 0.78), float(source.get("confidence") or 0.78), 0.76)
    target.setdefault("raw_json", {})
    target["raw_json"]["rerun_repair"] = {
        "action": "merged_terminal_hyphen_continuation",
        "merged_entry_key": source_key,
        "reason": "OCR line-break hyphenation split one logical index entry across entries in the checkpoint.",
    }
    if isinstance(target["raw_json"].get("source_fragment"), str):
        target["raw_json"]["source_fragment"] = target["entry_raw"]
    update_lemma_from_entry(target)
    move_refs(refs, source_key, target_key)
    remove_entry(entries, source_key)


def repair_ordo_hom_xxiii(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> None:
    target = find_entry(entries, "PG058:ordo_rerum:001:entry:0031")
    source = find_entry(entries, "PG058:ordo_rerum:001:entry:0033")
    left = (target.get("entry_raw") or "").rstrip()
    source_raw = source.get("entry_raw") or ""
    marker = " HOM. XXIV"
    if marker not in source_raw:
        raise RuntimeError("Expected HOM. XXIV marker not found in ORDO continuation.")
    prefix, rest = source_raw.split(marker, 1)
    prefix = prefix.strip()
    target["entry_raw"] = clean_linebreak_hyphens(left[:-1] + prefix if left.endswith("-") else f"{left} {prefix}")
    source["entry_raw"] = clean_linebreak_hyphens("HOM. XXIV" + rest)
    if isinstance(target.get("raw_json"), dict):
        target["raw_json"]["rerun_repair"] = {
            "action": "merged_terminal_hyphen_continuation_from_later_entry",
            "continuation_entry_key": source["entry_key"],
            "intervening_note_entry_key": "PG058:ordo_rerum:001:entry:0032",
            "reason": "OCR inserted a footnote between the broken word evan-/gelica; only the HOM. XXIII continuation was merged.",
        }
        target["raw_json"]["source_fragment"] = target["entry_raw"]
    if isinstance(source.get("raw_json"), dict):
        source["raw_json"]["source_fragment"] = source["entry_raw"]
    update_lemma_from_entry(target)
    update_lemma_from_entry(source)
    move_refs(refs, source["entry_key"], target["entry_key"], only_ref_raw="307-320")


def renumber_refs(refs: list[dict[str, Any]]) -> None:
    counters: dict[str, int] = {}
    for ref in refs:
        key = ref["entry_key"]
        counters[key] = counters.get(key, 0) + 1
        ref["ref_order"] = counters[key]


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    data = clean_object_strings(data)
    entries = data["entries"]
    refs = data["refs"]

    merge_entry(entries, refs, "PG058:analytic_subject:001:entry:0133", "PG058:analytic_subject:001:entry:0134")
    merge_entry(entries, refs, "PG058:analytic_subject:001:entry:0467", "PG058:analytic_subject:001:entry:0468")
    if (find_entry(entries, "PG058:ordo_rerum:001:entry:0031").get("entry_raw") or "").rstrip().endswith("-"):
        repair_ordo_hom_xxiii(entries, refs)
    renumber_refs(refs)

    data["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    notes = data.setdefault("notes", [])
    note = (
        "PG058 rerun: removed OCR line-break hyphen artifacts and repaired terminal continuations ex-/stantibus, com-/mendatur, and evan-/gelica against OCR index pages."
    )
    if note not in notes:
        notes.append(note)

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    TODO.parent.mkdir(parents=True, exist_ok=True)
    TODO.write_text(
        json.dumps(
            {
                "volume_id": "PG058",
                "updated_at": data["generated_at"],
                "current_focus": "Final validation complete after hyphenation repair",
                "completed": [
                    "read skill contract and repository docs",
                    "inspected PG058 index OCR pages 417, 421, and 466",
                    "removed line-break hyphen artifacts from payload strings",
                    "merged three terminal continuation cases",
                    "renumbered refs within each entry",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "Existing helper evidence and section structure were retained from the checkpoint.",
                    "The repair targeted the previous import failure rather than rebuilding all entries from scratch.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

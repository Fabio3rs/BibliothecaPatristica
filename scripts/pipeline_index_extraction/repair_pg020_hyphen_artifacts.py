# Usage: python scripts/pipeline_index_extraction/repair_pg020_hyphen_artifacts.py
# Repairs PG020 alphabetical payload entries split by OCR line-break hyphenation,
# remaps references from removed continuation entries, and refreshes checkpoints.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG020_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG020"


MERGE_GROUPS = [
    ["PG020:sec:author_index:001:entry:0241", "PG020:sec:author_index:001:entry:0242"],
    ["PG020:sec:analytic_subject:002:entry:0597", "PG020:sec:analytic_subject:002:entry:0598"],
    ["PG020:sec:analytic_subject:002:entry:0618", "PG020:sec:analytic_subject:002:entry:0619"],
    ["PG020:sec:analytic_subject:002:entry:0628", "PG020:sec:analytic_subject:002:entry:0629"],
    ["PG020:sec:analytic_subject:002:entry:0630", "PG020:sec:analytic_subject:002:entry:0631"],
    ["PG020:sec:analytic_subject:002:entry:0633", "PG020:sec:analytic_subject:002:entry:0634"],
    [
        "PG020:sec:analytic_subject:002:entry:0647",
        "PG020:sec:analytic_subject:002:entry:0648",
        "PG020:sec:analytic_subject:002:entry:0649",
    ],
    ["PG020:sec:analytic_subject:002:entry:0654", "PG020:sec:analytic_subject:002:entry:0655"],
    ["PG020:sec:analytic_subject:002:entry:0658", "PG020:sec:analytic_subject:002:entry:0659"],
    ["PG020:sec:analytic_subject:002:entry:0664", "PG020:sec:analytic_subject:002:entry:0665"],
    ["PG020:sec:analytic_subject:002:entry:0668", "PG020:sec:analytic_subject:002:entry:0669"],
    ["PG020:sec:analytic_subject:002:entry:0672", "PG020:sec:analytic_subject:002:entry:0673"],
    ["PG020:sec:analytic_subject:002:entry:0689", "PG020:sec:analytic_subject:002:entry:0690"],
    ["PG020:sec:analytic_subject:002:entry:0694", "PG020:sec:analytic_subject:002:entry:0695"],
    ["PG020:sec:analytic_subject:002:entry:0699", "PG020:sec:analytic_subject:002:entry:0700"],
    ["PG020:sec:analytic_subject:002:entry:0717", "PG020:sec:analytic_subject:002:entry:0718"],
    ["PG020:sec:analytic_subject:002:entry:0724", "PG020:sec:analytic_subject:002:entry:0725"],
    ["PG020:sec:analytic_subject:002:entry:0742", "PG020:sec:analytic_subject:002:entry:0743"],
    ["PG020:sec:analytic_subject:002:entry:0747", "PG020:sec:analytic_subject:002:entry:0748"],
    ["PG020:sec:analytic_subject:002:entry:0755", "PG020:sec:analytic_subject:002:entry:0756"],
    ["PG020:sec:analytic_subject:002:entry:0769", "PG020:sec:analytic_subject:002:entry:0770"],
]

WORD_BREAK_RE = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])-\s+([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF])")
REF_TAIL_RE = re.compile(
    r"\s*(?:[,.;]\s*)?(?:ibid\.?|[0-9]+(?:\s*(?:,|et|seq\.?|seqq\.)\s*[0-9]+|(?:\s+et\s+seqq?\.?)?)?)\s*\.?$",
    re.IGNORECASE,
)


def join_parts(values: list[str]) -> str:
    text = " ".join(v.strip() for v in values if v and v.strip())
    previous = None
    while previous != text:
        previous = text
        text = WORD_BREAK_RE.sub(r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def derive_lemma(entry_raw: str) -> str:
    lemma = REF_TAIL_RE.sub("", entry_raw).strip()
    return lemma.rstrip(" ,.;") or entry_raw


def norm_text(value: str) -> str:
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]+", " ", value.translate(replacements).casefold()).strip()


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    entries = data["entries"]
    refs = data["refs"]
    by_key = {entry["entry_key"]: entry for entry in entries}

    remap: dict[str, str] = {}
    remove_keys: set[str] = set()
    repair_notes: list[dict[str, object]] = []

    for group in MERGE_GROUPS:
        keep_key = group[0]
        keep = by_key[keep_key]
        group_entries = [by_key[key] for key in group]
        entry_raw = join_parts([entry["entry_raw"] for entry in group_entries])
        lemma = derive_lemma(entry_raw)
        source_line = join_parts(
            [
                str(entry.get("raw_json", {}).get("source_line") or entry["entry_raw"])
                for entry in group_entries
            ]
        )
        keep["entry_raw"] = entry_raw
        keep["lemma_raw"] = lemma
        keep["lemma_display"] = lemma
        keep["lemma_norm"] = norm_text(lemma)
        keep["lemma_sort"] = lemma.casefold()
        keep.setdefault("raw_json", {})
        keep["raw_json"]["source_line"] = source_line
        keep["raw_json"]["linebreak_hyphen_repair"] = {
            "merged_entry_keys": group,
            "removed_entry_keys": group[1:],
            "reason": "OCR line-break hyphenation verified on PG020 index pages.",
        }
        keep["confidence"] = min(float(entry.get("confidence") or 0.8) for entry in group_entries)

        for removed in group[1:]:
            remap[removed] = keep_key
            remove_keys.add(removed)
        repair_notes.append({"kept": keep_key, "removed": group[1:], "entry_raw": entry_raw})

    for ref in refs:
        if ref["entry_key"] in remap:
            ref["entry_key"] = remap[ref["entry_key"]]
            ref.setdefault("raw_json", {})
            ref["raw_json"]["entry_key_remapped_from"] = next(
                old for old, new in remap.items() if new == ref["entry_key"]
            )

    refs_by_entry: dict[str, list[dict[str, object]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for entry_refs in refs_by_entry.values():
        entry_refs.sort(key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or "")))
        seen: set[tuple[str, str | None, int | None]] = set()
        order = 1
        for ref in entry_refs:
            key = (str(ref.get("ref_raw")), ref.get("page_ref_raw"), ref.get("page_ref_int"))
            if key in seen:
                continue
            seen.add(key)
            ref["ref_order"] = order
            order += 1

    data["entries"] = [entry for entry in entries if entry["entry_key"] not in remove_keys]
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    notes = data.setdefault("notes", [])
    notes.append(
        {
            "type": "repair",
            "date": datetime.now(timezone.utc).isoformat(),
            "message": "Merged PG020 OCR line-break hyphen artifacts and remapped continuation refs.",
            "details": repair_notes,
        }
    )

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "entries.json").write_text(json.dumps(data["entries"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "refs.json").write_text(json.dumps(data["refs"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    todo = {
        "volume_id": "PG020",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "Validate repaired PG020 alphabetical payload",
        "completed": [
            "Verified line-break hyphen artifacts against OCR pages 785 and 790",
            "Merged affected split entries and remapped continuation refs",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Final payload remains canonical; intermediate entries/refs refreshed from repaired payload."
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

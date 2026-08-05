# Usage: python scripts/pipeline_index_extraction/repair_pg030_hyphen_artifacts.py
# Repairs PG030 alphabetical payload OCR line-break hyphen artifacts verified
# against pages 587-588 and 606-607, then refreshes volume checkpoints.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG030_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG030"


def norm_text(value: str) -> str:
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    return re.sub(
        r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]+",
        " ",
        value.translate(replacements).casefold(),
    ).strip()


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    entries = data["entries"]
    refs = data["refs"]
    by_key = {entry["entry_key"]: entry for entry in entries}

    tabernaculum_key = "PG030:entry:01410"
    tabernaculum = by_key[tabernaculum_key]
    fixed_tabernaculum_entry = (
        "Corpus nostrum tabernaculum nobis est : exitus a tabernaculo, mors est, 114."
    )
    fixed_tabernaculum_lemma = (
        "Corpus nostrum tabernaculum nobis est : exitus a tabernaculo, mors est"
    )
    tabernaculum["entry_raw"] = fixed_tabernaculum_entry
    tabernaculum["lemma_raw"] = fixed_tabernaculum_lemma
    tabernaculum["lemma_display"] = fixed_tabernaculum_lemma
    tabernaculum["lemma_norm"] = norm_text(fixed_tabernaculum_lemma)
    tabernaculum["lemma_sort"] = fixed_tabernaculum_lemma.casefold()
    tabernaculum.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
        "source_files": [
            str(ROOT / "teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-587.txt"),
            str(ROOT / "teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-588.txt"),
        ],
        "reason": "OCR line-break hyphenation at page boundary: taberna- / culum.",
    }

    puella_key = "PG030:entry:03937"
    continuation_key = "PG030:entry:03938"
    puella = by_key[puella_key]
    fixed_puella_entry = (
        "Puellam dici posse mulierem ætate florentem ; virginem vero puellam "
        "non posse contendunt Judæi, 528."
    )
    fixed_puella_lemma = (
        "Puellam dici posse mulierem ætate florentem ; virginem vero puellam "
        "non posse contendunt Judæi"
    )
    continuation = by_key[continuation_key]
    puella["entry_raw"] = fixed_puella_entry
    puella["lemma_raw"] = fixed_puella_lemma
    puella["lemma_display"] = fixed_puella_lemma
    puella["lemma_norm"] = norm_text(fixed_puella_lemma)
    puella["lemma_sort"] = fixed_puella_lemma.casefold()
    puella["inferred_printed_page"] = 528
    puella["target_file_best"] = continuation.get("target_file_best")
    puella["confidence"] = min(float(puella.get("confidence") or 0.8), float(continuation.get("confidence") or 0.8))
    puella.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
        "merged_entry_keys": [puella_key, continuation_key],
        "removed_entry_keys": [continuation_key],
        "source_files": [
            str(ROOT / "teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-606.txt"),
            str(ROOT / "teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-607.txt"),
        ],
        "dropped_false_ref": "1319",
        "reason": "OCR line-break hyphenation at page boundary: virgi- / nem; 1319 is the next index-page header, not an entry locator.",
    }

    repaired_refs = []
    for ref in refs:
        if ref["entry_key"] == puella_key and ref.get("page_ref_raw") == "1319":
            continue
        if ref["entry_key"] == continuation_key:
            ref["entry_key"] = puella_key
            ref["ref_order"] = 1
            ref.setdefault("raw_json", {})["entry_key_remapped_from"] = continuation_key
        repaired_refs.append(ref)
    data["refs"] = repaired_refs
    data["entries"] = [entry for entry in entries if entry["entry_key"] != continuation_key]

    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": datetime.now(timezone.utc).isoformat(),
            "message": "Repaired PG030 OCR line-break hyphen artifacts and removed one header-derived false reference.",
            "details": [
                {"entry_key": tabernaculum_key, "entry_raw": fixed_tabernaculum_entry},
                {"entry_key": puella_key, "removed_entry_key": continuation_key, "entry_raw": fixed_puella_entry},
            ],
        }
    )

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "entries.json").write_text(json.dumps(data["entries"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "refs.json").write_text(json.dumps(data["refs"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    todo = {
        "volume_id": "PG030",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_focus": "Validate repaired PG030 alphabetical payload",
        "completed": [
            "Verified taberna-/culum split across OCR files 587-588",
            "Verified virgi-/nem split across OCR files 606-607",
            "Merged continuation entry PG030:entry:03938 into PG030:entry:03937",
            "Removed false ref 1319 derived from an index-page header",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Existing helper output contains only 24 entries; unresolved locators outside this repair remain checkpoint material.",
            "Final payload remains canonical; refreshed entries and refs checkpoints from the repaired payload.",
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

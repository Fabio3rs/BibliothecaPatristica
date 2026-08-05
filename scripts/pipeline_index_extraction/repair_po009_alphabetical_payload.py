# Usage: python scripts/pipeline_index_extraction/repair_po009_alphabetical_payload.py
# Repairs PO009 line-break hyphen artifacts verified against OCR pages 487, 492, 493, 495, and 678.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PO009_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PO009"


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    text = value.translate(replacements).casefold()
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ']+", " ", text).strip()


def merge_entry(data: dict, keep_key: str, remove_key: str, merged_raw: str, lemma: str) -> None:
    entries = data["entries"]
    by_key = {entry["entry_key"]: entry for entry in entries}
    keep = by_key[keep_key]
    remove = by_key.get(remove_key)

    keep["entry_raw"] = merged_raw
    keep["lemma_raw"] = lemma
    keep["lemma_display"] = lemma
    keep["lemma_norm"] = norm_text(lemma)
    keep["lemma_sort"] = keep["lemma_norm"]
    if remove:
        keep["inferred_printed_page"] = remove.get("inferred_printed_page") or keep.get("inferred_printed_page")
        keep["target_file_best"] = remove.get("target_file_best") or keep.get("target_file_best")
        keep["confidence"] = min(float(keep.get("confidence") or 0.8), float(remove.get("confidence") or 0.8))
    keep.setdefault("raw_json", {})
    keep["raw_json"]["linebreak_hyphen_repair"] = {
        "merged_entry_keys": [keep_key, remove_key],
        "removed_entry_keys": [remove_key],
        "reason": "OCR line-break hyphenation split one logical index entry across adjacent columns/lines; the trailing hyphen was removed and the continuation was merged.",
    }

    for ref in data["refs"]:
        if ref["entry_key"] == remove_key:
            ref["entry_key"] = keep_key
            ref["section_start_file"] = keep.get("section_start_file")
            ref["editorial_anchor_file"] = keep.get("editorial_anchor_file")
            ref.setdefault("raw_json", {})
            ref["raw_json"]["entry_key_remapped_from"] = remove_key

    for sref in data["scripture_refs"]:
        if sref["entry_key"] == remove_key:
            sref["entry_key"] = keep_key
            sref.setdefault("raw_json", {})
            sref["raw_json"]["entry_key_remapped_from"] = remove_key

    if remove:
        data["entries"] = [entry for entry in entries if entry["entry_key"] != remove_key]


def renumber_refs(refs: list[dict]) -> None:
    refs_by_entry: dict[str, list[dict]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for entry_refs in refs_by_entry.values():
        entry_refs.sort(key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or "")))
        seen: set[tuple] = set()
        unique_refs = []
        for ref in entry_refs:
            sig = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
                ref.get("target_file"),
            )
            if sig in seen:
                ref["_drop_duplicate"] = True
                continue
            seen.add(sig)
            unique_refs.append(ref)
        for order, ref in enumerate(unique_refs, 1):
            ref["ref_order"] = order


def entry_by_key(data: dict, entry_key: str) -> dict:
    return {entry["entry_key"]: entry for entry in data["entries"]}[entry_key]


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    merge_entry(
        data,
        "PO009:entry:0157",
        "PO009:entry:0158",
        "Absädi Abba [WaldaAbsädi] (Yohannès Abba), 298.",
        "Absädi Abba [WaldaAbsädi] (Yohannès Abba)",
    )
    entry_by_key(data, "PO009:entry:0157")["raw_json"]["linebreak_hyphen_repair"].update(
        {
            "source_file": str(ROOT / "teste/PO009/text/e408d0d8-83d6-4af6-8456-70f12bcffb93-487.txt"),
            "source_excerpt": "Absädi Abba [Walda- / Absädi] (Yohannès Abba), 298.",
        }
    )

    merge_entry(
        data,
        "PO009:entry:0792",
        "PO009:entry:0793",
        "Jean, martyr, de la ville d'Haraqli, fils de Zakaryôs, préfet, et d'Élisâbét, 4 S, 24, 25, 26, 27, 39.",
        "Jean, martyr, de la ville d'Haraqli",
    )
    entry_by_key(data, "PO009:entry:0792")["raw_json"]["linebreak_hyphen_repair"].update(
        {
            "source_file": str(ROOT / "teste/PO009/text/e408d0d8-83d6-4af6-8456-70f12bcffb93-492.txt"),
            "source_excerpt": "Jean, martyr, de la ville d'Haraqli, fils de Za- / karyôs, préfet, et d'Élisâbét, 4 S, 24, 25, 26, 27, 39.",
        }
    )

    merge_entry(
        data,
        "PO009:entry:0903",
        "PO009:entry:0904",
        "Mâ'qaba-Egzi', premier nom d'Abuna Ewostâ-têwos, fils de KrestosMo'a el de Senna-Hey-wat, 360.",
        "Mâ'qaba-Egzi', premier nom d'Abuna Ewostâ-têwos",
    )
    entry_by_key(data, "PO009:entry:0903")["raw_json"]["linebreak_hyphen_repair"].update(
        {
            "source_file": str(ROOT / "teste/PO009/text/e408d0d8-83d6-4af6-8456-70f12bcffb93-493.txt"),
            "source_excerpt": "Mâ'qaba-Egzi', premier nom d'Abuna Ewostâ-têwos, fils de Krestos- / Mo'a el de Senna-Hey-wat, 360.",
        }
    )

    merge_entry(
        data,
        "PO009:entry:1120",
        "PO009:entry:1121",
        "Qâro, pays, 383. Cf. Pharos.",
        "Qâro",
    )
    entry_by_key(data, "PO009:entry:1120")["raw_json"]["linebreak_hyphen_repair"].update(
        {
            "source_file": str(ROOT / "teste/PO009/text/e408d0d8-83d6-4af6-8456-70f12bcffb93-495.txt"),
            "source_excerpt": "Qâro-, pays, 383. Cf. / Pharos.",
        }
    )

    by_key = {entry["entry_key"]: entry for entry in data["entries"]}
    matt_x_34 = by_key["PO009:entry:1278"]
    matt_x_34["lemma_raw"] = "X, 34"
    matt_x_34["lemma_display"] = "X, 34"
    matt_x_34["lemma_norm"] = "matthieu 10 34"
    matt_x_34["lemma_sort"] = "matthieu 10 34"
    matt_x_34.setdefault("raw_json", {})
    matt_x_34["raw_json"]["lemma_repair"] = {
        "source_file": str(ROOT / "teste/PO009/text/0e03511b-9b6c-4829-bbf9-93e57563b56b-678.txt"),
        "source_excerpt": "MATTH. ... X, 28 ... -- 34 . . . . . . . . 74",
        "reason": "The OCR literal '-- 34' is an inherited dash under MATTH. X, not a word-break hyphen. entry_raw preserves the literal; lemma fields store the inherited scripture locator.",
    }

    renumber_refs(data["refs"])
    data["refs"] = [ref for ref in data["refs"] if not ref.pop("_drop_duplicate", False)]
    renumber_refs(data["scripture_refs"])

    now = datetime.now(timezone.utc).isoformat()
    data["generated_at"] = now
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": now,
            "message": "Repaired PO009 OCR line-break hyphen artifacts and the MATTH. X inherited-dash lemma flagged by validation.",
            "details": [
                "Merged PO009:entry:0158 into PO009:entry:0157: Walda- / Absädi.",
                "Merged PO009:entry:0793 into PO009:entry:0792: Za- / karyôs.",
                "Merged PO009:entry:0904 into PO009:entry:0903: Krestos- / Mo'a.",
                "Merged PO009:entry:1121 into PO009:entry:1120: Qâro-, pays, 383. Cf. / Pharos.",
                "Changed PO009:entry:1278 lemma fields from OCR '--' to inherited 'X, 34' while preserving entry_raw.",
            ],
        }
    )

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    for name in ("sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        (INTERMEDIATE / f"{name}.json").write_text(
            json.dumps(data[name], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    todo = {
        "volume_id": "PO009",
        "updated_at": now,
        "current_focus": "Validate repaired PO009 alphabetical payload",
        "completed": [
            "Verified exact validation failures against OCR reader output",
            "Merged four OCR line-break hyphen continuation entries",
            "Remapped refs from removed continuation entries",
            "Repaired MATTH. X inherited-dash lemma without changing entry_raw literal",
            "Refreshed intermediate checkpoint fragments",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Final payload remains the canonical output; intermediate fragments were refreshed from it.",
            "The existing helper output was kept as evidence already embedded in raw_json where present.",
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

# Usage: python scripts/pipeline_index_extraction/repair_pg022_hyphen_artifacts.py
# Repairs PG022 payload line-break hyphen artifacts verified against OCR pages 656 and 660.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG022_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG022"

WORD_BREAK_RE = re.compile(
    r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])-\s+([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])"
)


def norm_text(value: str) -> str:
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+", " ", value.translate(replacements).casefold()).strip()


def join_word_break(value: str) -> str:
    return WORD_BREAK_RE.sub(r"\1\2", value)


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    entries = data["entries"]
    refs = data["refs"]
    by_key = {entry["entry_key"]: entry for entry in entries}

    analytic = by_key["PG022:entry:000371"]
    for field in ("lemma_raw", "lemma_display", "entry_raw"):
        analytic[field] = join_word_break(analytic[field])
    analytic["lemma_norm"] = norm_text(analytic["lemma_raw"])
    analytic["lemma_sort"] = analytic["lemma_norm"]
    analytic.setdefault("raw_json", {})
    analytic["raw_json"]["linebreak_hyphen_repair"] = {
        "source_file": str(ROOT / "teste/PG022/text/e6bcf7f0-84e7-455e-98e3-dab47110e1d7-656.txt"),
        "source_excerpt": "Legis novæ in Evangelio Christi sanctionem fore testi- / monio prophetico comprobatur, 443.",
        "reason": "OCR line-break hyphenation joins testi- and monio into testimonio.",
    }

    keep = by_key["PG022:ordo:000084"]
    removed_key = "PG022:ordo:000085"
    removed = by_key[removed_key]
    merged_entry_raw = join_word_break(f"{keep['entry_raw']} {removed['entry_raw']}")
    merged_lemma = re.sub(r"\.\s*418$", "", merged_entry_raw).strip()

    keep["entry_raw"] = merged_entry_raw
    keep["lemma_raw"] = merged_lemma
    keep["lemma_display"] = merged_lemma
    keep["lemma_norm"] = norm_text(merged_lemma)
    keep["lemma_sort"] = keep["lemma_norm"]
    keep["inferred_printed_page"] = 418
    keep["target_file_best"] = removed.get("target_file_best") or keep.get("target_file_best")
    keep["confidence"] = min(float(keep.get("confidence") or 0.8), float(removed.get("confidence") or 0.8))
    keep.setdefault("raw_json", {})
    keep["raw_json"]["linebreak_hyphen_repair"] = {
        "merged_entry_keys": ["PG022:ordo:000084", removed_key],
        "removed_entry_keys": [removed_key],
        "source_file": str(ROOT / "teste/PG022/text/e6bcf7f0-84e7-455e-98e3-dab47110e1d7-660.txt"),
        "source_excerpt": "CAP. III. — Quod manifeste ad homines venturus dici- / tur Deus, et omne genus hominum ad seipsum revocaturus. 418",
        "reason": "OCR line-break hyphenation joins dici- and tur into dicitur; continuation entry carried the printed page ref.",
    }
    keep["raw_json"]["pages"] = [418]

    for ref in refs:
        if ref["entry_key"] == removed_key:
            ref["entry_key"] = keep["entry_key"]
            ref.setdefault("raw_json", {})
            ref["raw_json"]["entry_key_remapped_from"] = removed_key
            ref["section_start_file"] = keep.get("section_start_file")
            ref["editorial_anchor_file"] = keep.get("editorial_anchor_file")

    refs_by_entry: dict[str, list[dict[str, object]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for entry_refs in refs_by_entry.values():
        entry_refs.sort(key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or "")))
        for order, ref in enumerate(entry_refs, 1):
            ref["ref_order"] = order

    data["entries"] = [entry for entry in entries if entry["entry_key"] != removed_key]
    now = datetime.now(timezone.utc).isoformat()
    data["generated_at"] = now
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": now,
            "message": "Merged PG022 OCR line-break hyphen artifacts in analytic index and ORDO RERUM.",
            "details": [
                {
                    "entry_key": "PG022:entry:000371",
                    "repair": "testi- monio -> testimonio",
                },
                {
                    "entry_key": "PG022:ordo:000084",
                    "removed_entry_key": removed_key,
                    "repair": "dici- / tur -> dicitur",
                },
            ],
        }
    )

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    for name in ("entries", "refs"):
        (INTERMEDIATE / f"{name}.json").write_text(
            json.dumps(data[name], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    todo = {
        "volume_id": "PG022",
        "updated_at": now,
        "current_focus": "Validate repaired PG022 alphabetical payload",
        "completed": [
            "Verified INDEX ANALYTICUS hyphen artifact against OCR file 656",
            "Verified ORDO RERUM split CAP. III entry against OCR file 660",
            "Merged continuation entry and remapped its material ref",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "Final payload is canonical; intermediate entries and refs were refreshed from it."
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

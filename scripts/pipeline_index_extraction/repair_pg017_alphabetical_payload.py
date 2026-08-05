# Usage: python scripts/pipeline_index_extraction/repair_pg017_alphabetical_payload.py
# Repairs PG017 alphabetical payload OCR line-break hyphen artifacts after import validation.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG017_alphabetical_indices.json"
TODO = ROOT / "data/intermediate_payloads/PG017/todo.json"
SOURCE_FILE_680 = (
    "/homessddata/Projects/pdfocr/teste/PG017/text/"
    "a04dd7ce-7925-462f-a6c8-fc2272af6ed1-680.txt"
)


REPAIRS = {
    "CAP. IV. — Quo ordine, quibus temporibus Origenis libri lucubrati sint exploratur. I. Variis Origenis scriptio- nibus suus ordo, sua tempora ex Eusebio assignantur. II. Quo tempore Tetrapla, Hexapla et Octapla concinnave- rit, investigatur. III. Notantur nonnulla circa ordinem ac tempus exegeticon, ac syntagmatum ipsius quotumdam. IV. Distinguuntur ejusdem homiliæ extemporales, et in otio elaboratæ. 1261": (
        "CAP. IV. — Quo ordine, quibus temporibus Origenis libri lucubrati sint exploratur. I. Variis Origenis scriptionibus suus ordo, sua tempora ex Eusebio assignantur. II. Quo tempore Tetrapla, Hexapla et Octapla concinnaverit, investigatur. III. Notantur nonnulla circa ordinem ac tempus exegeticon, ac syntagmatum ipsius quotumdam. IV. Distinguuntur ejusdem homiliæ extemporales, et in otio elaboratæ. 1261"
    )
}


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    repaired = []
    for entry in data["entries"]:
        old = entry.get("entry_raw")
        if old in REPAIRS:
            entry["entry_raw"] = REPAIRS[old]
            entry.setdefault("raw_json", {})["rerun_repair"] = {
                "reason": "Merged OCR line-break hyphen artifacts verified against cleaned OCR reader output.",
                "source_file": SOURCE_FILE_680,
                "repairs": ["scriptio- nibus -> scriptionibus", "concinnave- rit -> concinnaverit"],
            }
            repaired.append(entry["entry_key"])

    if repaired != ["PG017:entry:083"]:
        raise SystemExit(f"Unexpected repaired entries: {repaired}")

    entry_keys = {entry["entry_key"] for entry in data["entries"]}
    missing_refs = [ref["entry_key"] for ref in data["refs"] if ref["entry_key"] not in entry_keys]
    if missing_refs:
        raise SystemExit(f"refs with missing entry_key remain: {missing_refs}")

    data["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    volume_notes = data.setdefault("volume", {}).setdefault("notes", [])
    repair_note = (
        "PG017 rerun repaired the validation-blocking OCR line-break hyphen artifacts in "
        "entry PG017:entry:083 after checking the cleaned OCR for file 680."
    )
    if repair_note not in volume_notes:
        volume_notes.append(repair_note)

    notes = data.setdefault("notes", [])
    note_obj = {
        "type": "rerun_repair",
        "message": repair_note,
        "evidence_file": SOURCE_FILE_680,
        "affected_entries": repaired,
    }
    if note_obj not in notes:
        notes.append(note_obj)

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    TODO.parent.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": "PG017",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "current_focus": "PG017 payload repaired after line-break hyphen validation failure; import validation completed next.",
        "completed": [
            "Read prior validation failure for entries[83] hyphen artifact and refs[83] cascade",
            "Verified CAP. IV wording against cleaned OCR reader output for file 680",
            "Merged scriptio- nibus and concinnave- rit in PG017:entry:083",
            "Confirmed refs point to existing entry keys before importer validation",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The refs[83] missing-entry error was a cascade from the importer rejecting the preceding entry artifact.",
            "No scripture_refs are present in this Ordo Rerum payload.",
        ],
    }
    TODO.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

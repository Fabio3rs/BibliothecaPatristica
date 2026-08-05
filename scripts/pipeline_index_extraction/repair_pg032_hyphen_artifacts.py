# Usage: python scripts/pipeline_index_extraction/repair_pg032_hyphen_artifacts.py
# Repairs PG032 alphabetical payload OCR line-break hyphen artifacts verified
# against the analytical index and ORDO RERUM OCR pages, then refreshes
# volume-local checkpoints.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data/alphabetical_index_payloads/PG032_alphabetical_indices.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PG032"

ANALYTIC_SOURCE = str(
    ROOT / "teste/PG032/text/71e58586-6bb4-48d8-a4b2-e1da0ff5eb08-734.txt"
)
ORDO_SOURCE = str(
    ROOT / "teste/PG032/text/71e58586-6bb4-48d8-a4b2-e1da0ff5eb08-766.txt"
)
ANALYTIC_BOUNDARY_SOURCES = [
    str(ROOT / "teste/PG032/text/71e58586-6bb4-48d8-a4b2-e1da0ff5eb08-761.txt"),
    str(ROOT / "teste/PG032/text/71e58586-6bb4-48d8-a4b2-e1da0ff5eb08-762.txt"),
]


def norm_text(value: str) -> str:
    replacements = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe"})
    return re.sub(
        r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF]+",
        " ",
        value.translate(replacements).casefold(),
    ).strip()


def set_entry_text(entry: dict, lemma: str, entry_raw: str) -> None:
    entry["lemma_raw"] = lemma
    entry["lemma_display"] = lemma
    entry["lemma_norm"] = norm_text(lemma)
    entry["lemma_sort"] = lemma.casefold()
    entry["entry_raw"] = entry_raw


def add_repair_note(entry: dict, *, source_file: str, reason: str) -> None:
    entry.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
        "source_files": [source_file],
        "reason": reason,
    }


def main() -> None:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    by_key = {entry["entry_key"]: entry for entry in data["entries"]}

    repairs: list[dict[str, str]] = []

    corruptor_key = "PG032:entry:007378"
    continuation_key = "PG032:entry:007379"
    entry = by_key[corruptor_key]
    continuation = by_key[continuation_key]
    lemma = "Virginis corruptor etsi volentis, servus est contumax in herilem torum irruens"
    entry_raw = lemma + ", 138."
    set_entry_text(entry, lemma, entry_raw)
    entry.setdefault("raw_json", {})["linebreak_hyphen_repair"] = {
        "merged_entry_keys": [corruptor_key, continuation_key],
        "removed_entry_keys": [continuation_key],
        "source_files": ANALYTIC_BOUNDARY_SOURCES,
        "reason": "OCR page-boundary line-break hyphenation: sei- at the end of file 761 continues as vus at the start of file 762.",
    }
    entry["target_file_best"] = continuation.get("target_file_best")
    entry["inferred_printed_page"] = 138
    repairs.append({"entry_key": corruptor_key, "entry_raw": entry_raw})

    entry = by_key["PG032:entry:003179"]
    lemma = "Negat Basilius"
    entry_raw = (
        "Negat Basilius, 425. communibus rebus cadentibus, "
        "privatae simol pereunt, 227."
    )
    set_entry_text(entry, lemma, entry_raw)
    add_repair_note(
        entry,
        source_file=ANALYTIC_SOURCE,
        reason="OCR line-break hyphenation in analytical index: Ne- / gnat; repaired to the logical lemma Negat Basilius.",
    )
    repairs.append({"entry_key": entry["entry_key"], "entry_raw": entry_raw})

    entry = by_key["PG032:entry:003206"]
    lemma = "Sub dio precari malunt, quam cum Arianis communicare"
    entry_raw = "Sub dio precari malunt, quam cum Arianis communicare, 235."
    set_entry_text(entry, lemma, entry_raw)
    add_repair_note(
        entry,
        source_file=ANALYTIC_SOURCE,
        reason="OCR line-break hyphenation in analytical index: eam- / mul; local Latin and parallel phrase support malunt.",
    )
    repairs.append({"entry_key": entry["entry_key"], "entry_raw": entry_raw})

    ordo_repairs = {
        "PG032:entry:007763": (
            "Hortatur ut ad Basilium veniat, a quo expediiri melius esse statuit, quam in desertis locis vagari. 601 Epist.",
            "Hortatur ut ad Basilium veniat, a quo expediiri melius esse statuit, quam in desertis locis vagari",
            "OCR line-break hyphenation in ORDO RERUM: vag- before the page locator 601; repaired to vagari from the recurring phrase.",
        ),
        "PG032:entry:007766": (
            "CLIII. - Victori ex-consuli. - Gratiæ agitur quod sui memor sit, nec amorem ob ulitam calumniam imminuat. 610 Epist.",
            "CLIII. - Victori ex-consuli. - Gratiæ agitur quod sui memor sit, nec amorem ob ulitam calumniam imminuat",
            "OCR line-break hyphenation in ORDO RERUM: im- / minuat.",
        ),
        "PG032:entry:007772": (
            "Addit sibi integrum non esse Romanæ materiæ; animam suam ab adversariis querit, nec tamen se quoddam de suo Ecclesiæ defendenda studio remissurum. 611 Epist.",
            "Addit sibi integrum non esse Romanæ materiæ; animam suam ab adversariis querit, nec tamen se quoddam de suo Ecclesiæ defendenda studio remissurum",
            "OCR line-break hyphenation in ORDO RERUM: reme- / rum; repaired to the logical Latin remissurum.",
        ),
        "PG032:entry:007780": (
            "CLXI. - Amphilochio, ordinato episcopo. - Consolatur eum, tum cum fugeret ordinationem, gratiæ rebus intermit.",
            "CLXI. - Amphilochio, ordinato episcopo. - Consolatur eum, tum cum fugeret ordinationem, gratiæ rebus intermit",
            "OCR line-break hyphenation in ORDO RERUM: re- / bus.",
        ),
        "PG032:entry:007782": (
            "Rogat ut si se longo morbo debilitaten inviseret velit, nec tempus nec signum experiment. 621 Epist.",
            "Rogat ut si se longo morbo debilitaten inviseret velit, nec tempus nec signum experiment",
            "OCR line-break hyphenation in ORDO RERUM: experi- / ment.",
        ),
    }
    for entry_key, (entry_raw, lemma, reason) in ordo_repairs.items():
        entry = by_key[entry_key]
        set_entry_text(entry, lemma, entry_raw)
        add_repair_note(entry, source_file=ORDO_SOURCE, reason=reason)
        repairs.append({"entry_key": entry_key, "entry_raw": entry_raw})

    repaired_refs = []
    for ref in data["refs"]:
        if ref["entry_key"] == continuation_key:
            ref["entry_key"] = corruptor_key
            ref["ref_order"] = 1
            ref.setdefault("raw_json", {})["entry_key_remapped_from"] = continuation_key
        repaired_refs.append(ref)
    data["refs"] = repaired_refs
    data["entries"] = [
        entry for entry in data["entries"] if entry["entry_key"] != continuation_key
    ]

    now = datetime.now(timezone.utc).isoformat()
    data["generated_at"] = now
    data.setdefault("notes", []).append(
        {
            "type": "repair",
            "date": now,
            "message": "Repaired PG032 OCR line-break hyphen artifacts that blocked alphabetical import validation.",
            "details": repairs,
        }
    )

    PAYLOAD.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "entries.json").write_text(
        json.dumps(data["entries"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (INTERMEDIATE / "refs.json").write_text(
        json.dumps(data["refs"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    todo = {
        "volume_id": "PG032",
        "updated_at": now,
        "current_focus": "Validate repaired PG032 alphabetical payload",
        "completed": [
            "Verified analytical hyphen artifacts against OCR file 734",
            "Merged analytical page-boundary split PG032:entry:007379 into PG032:entry:007378",
            "Verified ORDO RERUM hyphen artifacts against OCR file 766",
            "Repaired entry_raw, lemma_raw, lemma_display, lemma_norm, and lemma_sort for affected entries",
            "Refreshed entries.json and refs.json from final payload",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "This repair is intentionally scoped to import-blocking OCR line-break hyphen artifacts from the PG032 rerun failure.",
            "Existing unresolved material locators remain checkpoint evidence outside this validation repair.",
        ],
    }
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(todo, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

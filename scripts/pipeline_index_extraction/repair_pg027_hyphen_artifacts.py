# Repair PG027 alphabetical payload after import validation reports OCR
# line-break hyphen artifacts. Run from repo root:
# python scripts/pipeline_index_extraction/repair_pg027_hyphen_artifacts.py

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG027_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PG027/todo.json"


ENTRY_RAW_FIXES = {
    "PG027:alpha:foreign_terms:001:entry:0003": (
        "Ἀγγιθέρος, foribus propinquus, et per metaploram, congriens, accommodatus, col. 540 C."
    ),
    "PG027:alpha:foreign_terms:001:entry:0011": (
        "Ἀποκείμετα τῶν ἐννυπνίων, somniorum prædictiones, col. 444 C."
    ),
    "PG027:alpha:foreign_terms:001:entry:0013": (
        "Δισθολόγιος τόπος, locus ex lapidibus electis constructus, col. 357 C."
    ),
    "PG027:alpha:foreign_terms:001:entry:0016": (
        "Μεταχειρίζεσθαι ἑαυτὸν ἀργόν, qui laqueo se prafoccavit, de Juda dictum, col. 457 A."
    ),
    "PG027:alpha:foreign_terms:001:entry:0017": (
        "Olxoxumla, sape pro Incarnatione usu venit. Vulgarius autem significat totam seriem "
        "actionum Christi ut col. 300 C. Dicitur etiam, οἰκονoμία τοῦ πάθους, œconomia "
        "passionis, οἰκονομία τοῦ σταυροῦ, œconomia crucis, col. 325 D."
    ),
    "PG027:alpha:foreign_terms:001:entry:0020": (
        "Πολυωρία, ibid. Πολυωρία τὴν πολύχρνότητά φησιν, id est, diuturnitatem, malo, "
        "diuturnam sollicitudinem."
    ),
    "PG027:alpha:foreign_terms:001:entry:0021": (
        "Πολυχρονία, id est, πολυχρονότης, temporis diuturnitas, col. 96 B."
    ),
    "PG027:alpha:foreign_terms:001:entry:0024": (
        "Σταχὺ: Ἐστιν ἡ σταχὺ σμύρνης εἶδος λεπτότατον. Ἐκλείθεντος γὰρ τοῦ ἄρωματος, "
        "ὅσον μεν γὰρ αὐτοῦ ῥύθμον εἰς σταχὺ ἀπομερίζεται, τὸ δὲ παχύτερον ἀπομένον "
        "σμύρνα παροξυστίζεται, col. 212 A."
    ),
    "PG027:alpha:foreign_terms:001:entry:0025": (
        "Τόμος, rescriptum compendiosum, sic vocatum quod in eo res συνετετηγμένας "
        "tracterunt. Κεφαλάδα α' Ἐβραίων τὸν τόμον φασιν, col. 192 D."
    ),
    "PG027:alpha:foreign_terms:001:entry:0026": (
        "Ῥινακή. Sic vocantur plurimi psalmi, quia nuncrum ita canebantur, ut diaconus "
        "primam versiculi partem proferret, et populus quasi respondendo, postremam "
        "absolveret, nam ὑπακούοι ἄν ὑπακούοι, id est, respondere, col. 37 B."
    ),
    "PG027:alpha:foreign_terms:001:entry:0027": (
        "Ῥινακή, item q. d. exaudito, ea sententia dicitur, col. 469 D. Τῆς ἀγάπης "
        "μισθὸν ἐδέξατο τὴν ὑπακοήν, id est, in dilectionis præmium exaudivit illum Deus."
    ),
    "PG027:alpha:foreign_terms:001:entry:0028": (
        "Ῥιναλαῖος, idem videtur significare 'quod ὑπακούειν, id est respondendo cantare, "
        "ut fiebat olim cum diaconus priorem versiculum, populus autem posteriorem "
        "partem canebat, col. 256 B."
    ),
}


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))

    fixed = []
    for entry in payload["entries"]:
        replacement = ENTRY_RAW_FIXES.get(entry["entry_key"])
        if not replacement:
            continue
        entry["entry_raw"] = replacement
        raw_json = entry.setdefault("raw_json", {})
        raw_json["hyphenation_repair"] = {
            "source_file": "/homessddata/Projects/pdfocr/teste/PG027/text/d522e5b8-afda-421a-bba0-e241fb78d7b5-713.txt",
            "reason": "Merged OCR line-break split words verified on the INDEX GRÆCITATIS page.",
        }
        fixed.append(entry["entry_key"])

    if len(fixed) != len(ENTRY_RAW_FIXES):
        missing = sorted(set(ENTRY_RAW_FIXES) - set(fixed))
        raise SystemExit(f"Missing expected entries: {missing}")

    payload["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["notes"] = [
        note
        for note in payload.get("notes", [])
        if "OCR line-break hyphen" not in note
    ]
    payload["notes"].append(
        "Rerun repaired OCR line-break hyphen artifacts in INDEX GRÆCITATIS entries against OCR file 713."
    )

    PAYLOAD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    todo = json.loads(TODO_PATH.read_text(encoding="utf-8"))
    todo.update(
        {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "current_focus": "PG027 payload repaired after line-break hyphen validation failure; import validation completed next.",
            "completed": [
                "Read extractor contract and output format",
                "Confirmed INDEX GRÆCITATIS on OCR page 713 and ORDO RERUM on page 714",
                "Reran index_target_locator.py for the existing helper request",
                "Removed OCR line-break hyphen artifacts from validation-blocking entries",
            ],
            "pending": [
                "Run validate-only import check for PG027_alphabetical_indices.json"
            ],
            "blocked": [],
            "notes": [
                "Page 713 is the relevant alphabetical section; 714 is ORDO RERUM / table of contents.",
                "Kept existing material locators where prior direct/editorial decisions already overrode weak helper candidates.",
                "OCR is noisy in the mixed-script lemma rendered as Olxoxumla.",
            ],
        }
    )
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

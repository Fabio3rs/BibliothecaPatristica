#!/usr/bin/env python3
"""Build the PG017 opening-index payload from its validated chunk fragments.

The source fragments are the authoritative transcription state.  This small
assembler only adds OCR-verified work anchors and fixes a single copied
reference (the Appendix line ends in 1271, not 1261).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG017"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def file(name: str) -> str:
    path = TEXT / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return str(path)


FILES = {
    "front": file("cd188115-73f9-4213-9e18-a7f78748f03c-012.txt"),
    "supplement_start": file("cd188115-73f9-4213-9e18-a7f78748f03c-013.txt"),
    "supplement_end": file("82cd72e7-f35c-42aa-9c64-21643288a11a-195.txt"),
    "anonymous_start": file("82cd72e7-f35c-42aa-9c64-21643288a11a-196.txt"),
    "anonymous_end": file("7f780f3a-35ff-484d-96df-c4d51a1eb741-270.txt"),
    "admonitio": file("7f780f3a-35ff-484d-96df-c4d51a1eb741-271.txt"),
    "apologia_start": file("7f780f3a-35ff-484d-96df-c4d51a1eb741-281.txt"),
    "apologia_end": file("ce76c39f-a21d-4349-856e-601738f27014-317.txt"),
    "rufini_start": file("ce76c39f-a21d-4349-856e-601738f27014-318.txt"),
    "rufini_end": file("ce76c39f-a21d-4349-856e-601738f27014-326.txt"),
    "huet_start": file("ce76c39f-a21d-4349-856e-601738f27014-327.txt"),
    "huet_end": file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-652.txt"),
    "bulli_start": file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-653.txt"),
    "bulli_end": file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-675.txt"),
    "ordo_start": file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-676.txt"),
    "ordo_end": file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-680.txt"),
}


WORK_ANCHORS = {
    "Supplementum ad Origenis Exegetica.": {
        "author_raw": "ORIGENES.",
        "start_page": 9,
        "end_page": 370,
        "start_file": FILES["supplement_start"],
        "end_file": FILES["supplement_end"],
        "evidence": [
            "File 013 has the direct title SUPPLEMENTUM AD ORIGENIS EXEGETICA and MONITUM.",
            "File 195 continues Scholia in Joan. under the supplement; file 196 begins the distinct SPURIA / Anonymi in Job work.",
        ],
    },
    "Commentarius Anonymi in Job.": {
        "author_raw": "SPURIA.",
        "start_page": 371,
        "end_page": 520,
        "start_file": FILES["anonymous_start"],
        "end_file": FILES["anonymous_end"],
        "evidence": [
            "File 196 explicitly has SPURIA and ANONYMI IN JOB COMMENTARIUS, with header 371 … 372.",
            "File 270 has header 519 ANONYMUS IN JOB — LIB. III. 520; file 271 starts OPERA AD ORIGENEM SPECTANTIA and its Admonitio.",
        ],
    },
    "Apologia S. Pamphili pro Origene.": {
        "author_raw": "S. PAMPHILI MARTYRIS.",
        "start_page": 541,
        "end_page": 614,
        "start_file": FILES["apologia_start"],
        "end_file": FILES["apologia_end"],
        "evidence": [
            "File 281 has header 541 APOLOGIA PRO ORIGENE and direct title APOLOGIA PAMPHILI MARTYRIS PRO ORIGENE.",
            "File 317 is still APOLOGIA PRO ORIGENE at 613 … 614; file 318 opens Rufini Epilogus.",
        ],
    },
    "Rufini liber de adulteratione librorum Origenis.": {
        "author_raw": "RUFINUS.",
        "start_page": 615,
        "end_page": 632,
        "start_file": FILES["rufini_start"],
        "end_file": FILES["rufini_end"],
        "evidence": [
            "File 318 carries the facing header 615 … 616 and the direct Rufini Epilogus / Liber de adulteratione title; the Elenchus reference 615 is preserved.",
            "File 326 has header 631 … 632 for the Rufini Liber; file 327 begins Huetii Origeniana.",
        ],
    },
    "P. Danielis Huetii Origeniana.": {
        "author_raw": "PETRI DANIELIS HUETII EPISCOPI ABRINCENSIS.",
        "start_page": 633,
        "end_page": 1284,
        "start_file": FILES["huet_start"],
        "end_file": FILES["huet_end"],
        "evidence": [
            "File 327 directly gives PETRI DANIELIS HUETII … ORIGENIANA, OPERIS TOTIUS PROLOGUS ET PARTITIO, and LIBER PRIMUS. Its left header digit is CER-corrupt (673), bounded by 631 … 632 in file 326 and 635 … 636 in file 328, confirming the declared 633/634 pair.",
            "Files 650–652 form the final Huet Appendix range (1279 … 1284, with CER in 651–652); file 653 directly opens the Bull excerpt at the declared 1285 boundary.",
        ],
    },
    "Excerptum ex Georgii Bulli presbyteri Anglicani Defensione Fidei Nicænæ.": {
        "author_raw": "GEORGIUS BULLUS.",
        "start_page": 1285,
        "end_page": 1530,
        "start_file": FILES["bulli_start"],
        "end_file": FILES["bulli_end"],
        "evidence": [
            "File 653 directly has EXCERPTUM EX GEORGII BULLI PRESB. ANGLIC. DEFENSIONE FIDEI NICÆNÆ; its damaged header is followed by the regular 1287 … 1288 header in file 654.",
            "File 675 has header 1529 EX G. BULLI DEF. FIDEI NICÆNÆ 1530; file 676 begins ORDO RERUM 1531 … 1532.",
        ],
    },
}


FRONT_TARGETS = {
    2: FILES["supplement_start"],
    3: FILES["anonymous_start"],
    4: FILES["anonymous_start"],
    5: FILES["admonitio"],
    6: FILES["apologia_start"],
    7: FILES["rufini_start"],
    8: FILES["huet_start"],
    9: FILES["bulli_start"],
}

ORDO_TARGETS = {
    1: FILES["supplement_start"],
    32: FILES["anonymous_start"],
    33: FILES["admonitio"],
    35: FILES["apologia_start"],
    36: FILES["apologia_start"],
    47: FILES["rufini_start"],
    48: FILES["huet_start"],
    49: FILES["huet_start"],
    82: file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-646.txt"),
    83: file("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-641.txt"),
    84: FILES["bulli_start"],
}


def add_target_files(entries: list[dict], targets: dict[int, str]) -> list[dict]:
    result = copy.deepcopy(entries)
    for entry in result:
        target = targets.get(entry["entry_order"])
        if target:
            entry["target_file"] = target
            entry.setdefault("raw_json", {}).setdefault("target_evidence", "direct OCR title/opening evidence")
    return result


def main() -> None:
    assembled = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    data = assembled["data"]
    stable_sections = data["sections"]
    front = copy.deepcopy(stable_sections[0])
    ordo = copy.deepcopy(stable_sections[1])

    front["heading_norm"] = "Elenchus auctorum et operum qui in hoc tomo XVII continentur"
    front["page_start"] = None
    front["page_end"] = None
    front["entries"] = add_target_files(front["entries"], FRONT_TARGETS)
    front["raw_json"]["assembled_fragments_consumed"] = True

    # The physical order is 676–680, but the surviving printed headers include
    # 1531/1532, 1533/1534, 1555/1556, 1557/1558, then 1359/1360.  Do not invent
    # a monotonic editorial span from those conflicting OCR sheets.
    ordo["heading_raw"] = "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR."
    ordo["heading_norm"] = "Ordo rerum quæ in hoc tomo continentur"
    ordo["page_start"] = None
    ordo["page_end"] = None
    ordo["work_key"] = None
    ordo["entries"] = add_target_files(ordo["entries"], ORDO_TARGETS)
    appendix = ordo["entries"][81]
    appendix["page_ref_raw"] = "1271"
    appendix["page_ref_int"] = 1271
    appendix["raw_json"]["reference_correction"] = "Corrected from fragment 1261 to the OCR literal 1271 in the same entry and in file 646."
    ordo["raw_json"]["assembled_fragments_consumed"] = True
    ordo["raw_json"]["editorial_header_note"] = "Non-monotonic and CER-damaged printed headers across the five physical files; physical file anchors are authoritative for this section span."

    works = []
    for stable_work in data["works"]:
        work = copy.deepcopy(stable_work)
        anchor = WORK_ANCHORS[work["title_raw"]]
        work.update(anchor)
        work["raw_json"].update(
            {
                "assembled_fragments_consumed": True,
                "anchor_resolution": "resolved by direct OCR opening and closing/boundary evidence",
                "anchor_evidence": anchor["evidence"],
            }
        )
        works.append(work)

    huet_work_key = next(work["work_key"] for work in works if work["title_raw"] == "P. Danielis Huetii Origeniana.")
    huet_partitio = {
        "section_key": "PG017_sec_huet_partitio",
        "work_key": huet_work_key,
        "scope_kind": "work_front",
        "index_kind": "PARTITIO",
        "heading_raw": "OPERIS TOTIUS PROLOGUS ET PARTITIO.\nLIBRI PRIMI PARTITIO.",
        "heading_norm": "Operis totius prologus et partitio; libri primi partitio",
        "page_start": 633,
        "page_end": 634,
        "file_start": FILES["huet_start"],
        "file_end": FILES["huet_start"],
        "confidence": "high",
        "raw_json": {
            "source_files": [FILES["huet_start"]],
            "evidence": "Direct OCR in file 327; its initial left header digit is CER-corrupt, resolved by 631/632 and 635/636 neighbors.",
        },
        "entries": [
            {
                "entry_order": 1,
                "entry_raw": "LIBER PRIMUS. ORIGENIS VITA.",
                "target_raw": "LIBER PRIMUS. ORIGENIS VITA.",
                "target_file": FILES["huet_start"],
                "page_ref_raw": "633",
                "page_ref_int": 633,
                "page_ref_col": None,
                "note_raw": None,
                "normalized_target": "Liber primus. Origenis vita",
                "confidence": "high",
                "raw_json": {},
            },
            {
                "entry_order": 2,
                "entry_raw": "LIBRI PRIMI PARTITIO. — Quatuor capitibus liber iste absolvitur.",
                "target_raw": "LIBRI PRIMI PARTITIO.",
                "target_file": FILES["huet_start"],
                "page_ref_raw": "633",
                "page_ref_int": 633,
                "page_ref_col": None,
                "note_raw": None,
                "normalized_target": "Libri primi partitio",
                "confidence": "high",
                "raw_json": {},
            },
            {
                "entry_order": 3,
                "entry_raw": "CAPUT PRIMUM. I. Origenis patria, ætas, parentes. II. Nomen. III. Cognomina. IV. Institutio puerilis, indoles. V. Præceptores et studia. VI. Utrum Ammonium audiverit. VII. An plures fuerint Origenes, et plures Adamantii. VIII. Leonidæ martyrium. IX. Origenes grammaticam publice profitetur, catechumenos instituit, martyribus præsto est. X. Utrum hoc tempore Cæsaream Cappadociæ iterit. XI. Grammaticæ docendæ munus abdicat. XII. Piæ ejus exercitationes. Plurimi ex ejus discipulis martyrium obeunt. XIII. Se ipse evirat.",
                "target_raw": "CAPUT PRIMUM.",
                "target_file": FILES["huet_start"],
                "page_ref_raw": "633",
                "page_ref_int": 633,
                "page_ref_col": None,
                "note_raw": None,
                "normalized_target": "Caput primum",
                "confidence": "high",
                "raw_json": {},
            },
        ],
    }

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(TEXT),
            "volume_label": "Patrologiæ Græcæ Tomus XVII",
            "notes": [
                "Opening ELENCHUS in physical OCR file 012 was transcribed line by line.",
                "The closing ORDO RERUM in physical OCR files 676–680 was transcribed line by line from the validated chunk fragments.",
                "Closing alphabetical/analytical index material was not extracted in this opening/general works-index payload.",
            ],
        },
        "works": works,
        "sections": [front, huet_partitio, ordo],
        "notes": [
            "Consumed all six validated work objects, two validated section objects, and all 93 validated entries from assembled_fragments.json.",
            "Corrected the fragment's Appendix reference from 1261 to the OCR literal 1271.",
            "All three previous work-anchor reruns were resolved through direct local OCR evidence; no work_anchor_rerun markers remain.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

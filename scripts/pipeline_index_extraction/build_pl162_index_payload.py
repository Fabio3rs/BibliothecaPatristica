#!/usr/bin/env python3
"""Materialize the PL162 general-index payload from its validated fragments."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL162"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
OLD_PAYLOAD = ROOT / "data" / "index_payloads" / f"{VOLUME_ID}_indices.json"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME_ID}_indices.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_for(sequence: int) -> str:
    matches = list(SOURCE_ROOT.glob(f"*-{sequence:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one PL162 physical file for suffix {sequence:03d}; found {matches}")
    return str(matches[0])


def canonical_entry(entry: dict) -> dict:
    """Keep every stable fragment field while supplying canonical nullable fields."""
    raw_json = dict(entry.get("raw_json") or {})
    raw_json["stable_entry_key"] = entry["entry_key"]
    return {
        "entry_key": entry["entry_key"],
        "entry_order": entry["entry_order"],
        "entry_raw": entry["entry_raw"],
        "target_raw": entry.get("target_raw"),
        "target_file": entry.get("target_file"),
        "page_ref_raw": entry.get("page_ref_raw"),
        "page_ref_int": entry.get("page_ref_int"),
        "page_ref_col": entry.get("page_ref_col"),
        "note_raw": entry.get("note_raw"),
        "normalized_target": entry.get("normalized_target"),
        "confidence": entry.get("confidence", "high"),
        "raw_json": raw_json,
    }


def canonical_section(section: dict) -> dict:
    key = section["section_key"]
    raw_json = dict(section.get("raw_json") or {})
    if key == "PL162:section:volume_front:elenchus:001":
        heading_raw = "ELENCHUS / AUCTORUM ET OPERUM QUI IN HOC TOMO CLXII CONTINENTUR."
        heading_norm = "Elenchus auctorum et operum qui in hoc tomo CLXII continentur"
        index_kind = "ELENCHUS"
        file_start = file_for(12)
        file_end = file_for(12)
    elif key == "PL162:section:volume_end:ordo_rerum_contents":
        heading_raw = "ORDO RERUM / QUÆ IN HOC TOMO CONTINENTUR."
        heading_norm = "Ordo rerum quae in hoc tomo continentur"
        index_kind = "ORDO RERUM"
        file_start = file_for(816)
        file_end = file_for(821)
    elif key == "PL162:section:002:ordo_rerum":
        heading_raw = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
        heading_norm = "Ordo rerum quae in hoc tomo continentur"
        index_kind = "ORDO RERUM"
        file_start = file_for(822)
        file_end = file_for(822)
    else:
        raise ValueError(f"Unexpected stable section {key}")
    return {
        "section_key": key,
        "work_key": None,
        "scope_kind": section["scope_kind"],
        "index_kind": index_kind,
        "heading_raw": heading_raw,
        "heading_norm": heading_norm,
        "page_start": section.get("page_start"),
        "page_end": section.get("page_end"),
        "file_start": file_start,
        "file_end": file_end,
        "confidence": section["confidence"],
        "raw_json": raw_json,
        "entries": [canonical_entry(entry) for entry in section["entries"]],
    }


ANCHORS = {
    "PL162-01-ivo-carnotensis-episcopus": {
        "start_page": 11, "end_page": 616, "start": 14, "end": 316,
        "evidence": [
            "File 014 contains the direct opening heading D. IVONIS EPISTOLÆ and EPISTOLA PRIMA.",
            "File 316 contains the final Chronicon continuation; its lower block begins Lambertus Atrebatensis Notitia, establishing the following work boundary on the same physical file.",
        ],
    },
    "PL162-02-lambertus-atrebatensis-episcopus": {
        "start_page": 627, "end_page": 718, "start": 322, "end": 367,
        "evidence": [
            "File 322 prints 627/628 and the direct incipit INCIPIUNT GESTA, followed by the Atrebatensium reformatur dignitatem title.",
            "File 367 is headed CANONES CONCIL. CLAROMONT. 717/718; file 368 begins FRANCISCUS CAMENUS — NOTITIA.",
        ],
    },
    "PL162-03-franciscus-camenus": {
        "start_page": 721, "end_page": 721, "start": 369, "end": 369,
        "evidence": ["File 369 directly prints HYMNUS IN S. NICOLAUM PEREGRINUM and also begins the adjacent Galo notice."],
    },
    "PL162-04-galo-parisiensis-episcopus": {
        "start_page": 721, "end_page": 730, "start": 369, "end": 374,
        "evidence": [
            "File 369 has the 721/722 GALO PARISIENSIS EPISCOPUS — NOTITIA heading.",
            "File 374 retains the conclusion of Appendix ad Galonem before its Godefridus heading, so the physical boundary is shared.",
        ],
    },
    "PL162-05-godefridus-ambianensis-episcopus": {
        "start_page": 735, "end_page": 748, "start": 376, "end": 382,
        "evidence": ["File 376 prints 735/736 S. GODEFRIDI AMBIANENSIS EPISCOPI; file 382 carries its 747/748 Charta communiæ before Reimbaldus begins in file 383."],
    },
    "PL162-06-reimbaldus-prepositus-s-joannis-leodiensis": {
        "start_page": 751, "end_page": 752, "start": 384, "end": 384,
        "evidence": [
            "File 384 directly prints REIMBALDI PRÆPOSITI S. JOAN. LEOD. EPISTOLÆ, REIMBALDI EPISTOLÆ, and item I.",
            "File 383 is the preceding Notitia at 749/750; file 385 opens the Joannes monachus chronicle material.",
        ],
    },
    "PL162-07-joannes-monachus-besuensis": {
        "start_page": 861, "end_page": 1004, "start": 439, "end": 510,
        "evidence": ["File 439 prints 861/862 JOANNIS MONACHI CHRONICON BESUENSE; file 510 remains Chronicon Besuense, while file 511 begins Petrus Chrysolanus Notitia."],
    },
    "PL162-08-petrus-chrysolanus-mediolanensis-archiepiscopus": {
        "start_page": 1007, "end_page": 1016, "start": 512, "end": 516,
        "evidence": ["File 512 contains the Petrus Chrysolanus Oratio opening; file 516 is headed PETRI CHRYSOL. ... ORAT. DE SPIRITU SANCTO. 1016, followed by Robertus in file 517."],
    },
    "PL162-09-robertus-de-arbrissello": {
        "start_page": 1079, "end_page": 1118, "start": 548, "end": 567,
        "evidence": [
            "File 548 directly begins REGULÆ SANCTIMONIALIUM at the 1079/1080 header; the title's subsequent Præcepta and Diplomata occur in files 549 and 551.",
            "File 567 is headed 1117 ORAT. DE B. ROBERTO DE ARBRISSELLO. 1118; file 568 begins Seherus.",
        ],
    },
    "PL162-10-seherus-abbas-calmosiacensis": {
        "start_page": 1119, "end_page": 1152, "start": 568, "end": 584,
        "evidence": ["File 568 has the direct SEHERUS ABBAS CALMOSIAGENSIS / DE PRIMORDIIS title; file 585 begins Joannis S. Audoeni Sermones."],
    },
    "PL162-11-joannes-monachus-s-audoeni": {
        "start_page": 1153, "end_page": 1168, "start": 585, "end": 592,
        "evidence": ["File 585 directly prints JOANNIS DIACONI S. AUDOENI MONACHI SERMONES QUATUOR; file 592 remains Sermones and file 593 begins Anselmus Notitia."],
    },
    "PL162-12-anselmus-scholasticus-et-canonicalus-laudunensis": {
        "start_page": 1187, "end_page": 1592, "start": 602, "end": 802,
        "evidence": ["File 602 opens ANSELMI LAUDUNENSIS OPERA / ENARRATIONES IN CANTICA; file 802 is the 1591/1592 Epistola ad H. before Richardus begins in file 803."],
    },
    "PL162-13-richardus-cardinalis": {
        "start_page": 1593, "end_page": 1604, "start": 803, "end": 808,
        "evidence": ["The direct Richardus title sequence begins in file 803 and is repeatedly headed RICHARDI CARDINALIS in files 804–808; file 809 begins INDEX IN EPIST., SERM. ET CHRON. IVONIS."],
    },
}


def build() -> dict:
    old = read_json(OLD_PAYLOAD)
    assembled = read_json(ASSEMBLED)
    stable_sections = assembled["data"]["sections"]
    if assembled.get("status") != "complete" or assembled.get("consumed_chunk_count") != assembled.get("expected_chunk_count"):
        raise ValueError("PL162 assembled fragments are not complete")
    sections = [canonical_section(section) for section in stable_sections]
    works = []
    for work in old["works"]:
        work = dict(work)
        anchor = ANCHORS[work["work_key"]]
        work["start_page"] = anchor["start_page"]
        work["end_page"] = anchor["end_page"]
        work["start_file"] = file_for(anchor["start"])
        work["end_file"] = file_for(anchor["end"])
        work["source_section_key"] = "PL162:section:volume_front:elenchus:001"
        raw_json = dict(work.get("raw_json") or {})
        raw_json.pop("work_anchor_rerun", None)
        raw_json.pop("deterministic_anchor_reconciliation", None)
        raw_json["anchor_verification"] = {
            "status": "resolved",
            "method": "direct OCR title/opening and neighboring-work boundary inspection",
            "evidence": anchor["evidence"],
        }
        work["raw_json"] = raw_json
        works.append(work)
    payload = {
        "volume": old["volume"],
        "works": works,
        "sections": sections,
        "notes": [
            "Validated fragment assembly consumed in full: 3 sections and 873 line-by-line entries.",
            "The closing alphabetical index material was not extracted; only the contents-table material from the validated fragments is included.",
            "Physical OCR filenames are retained only as locators; editorial page references come from OCR text and may share a scan boundary.",
            *assembled["data"].get("notes", []),
        ],
    }
    stable_count = sum(len(section["entries"]) for section in stable_sections)
    output_count = sum(len(section["entries"]) for section in payload["sections"])
    if output_count != stable_count:
        raise ValueError(f"Lost validated entries: stable={stable_count}, output={output_count}")
    work_keys = {work["work_key"] for work in works}
    for section in sections:
        if section["work_key"] is not None and section["work_key"] not in work_keys:
            raise ValueError(f"Dangling section work key {section['work_key']}")
    for work in works:
        if work["start_page"] > work["end_page"]:
            raise ValueError(f"Impossible editorial range for {work['work_key']}")
        for field in ("start_file", "end_file"):
            if not Path(work[field]).is_file():
                raise ValueError(f"Missing OCR locator for {work['work_key']}: {work[field]}")
    return payload


if __name__ == "__main__":
    payload = build()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"works": len(payload["works"]), "sections": len(payload["sections"]), "entries": sum(len(s["entries"]) for s in payload["sections"])}, ensure_ascii=False))

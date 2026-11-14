#!/usr/bin/env python3
"""Rebuild PG144 from validated fragments and direct OCR boundary evidence."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG144"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
REPORT = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "fragment_consumption_report.json"

sys.path.insert(0, str(ROOT))
from tools.indexing.index_fragment_assembly import verify_payload_consumes_fragments


def file_for(suffix: int) -> str:
    matches = list(TEXT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"expected one OCR file ending in {suffix:03d}: {matches}")
    return str(matches[0])


def work_by_key(works: list[dict], work_key: str) -> dict:
    return next(work for work in works if work["work_key"] == work_key)


def section_by_key(sections: list[dict], section_key: str) -> dict:
    return next(section for section in sections if section["section_key"] == section_key)


def resolve(work: dict, *, start_page: int | None, end_page: int | None,
            start_file: str | None, end_file: str | None, confidence: str,
            evidence: list[str]) -> None:
    work.update(
        {
            "start_page": start_page,
            "end_page": end_page,
            "start_file": start_file,
            "end_file": end_file,
            "confidence": confidence,
        }
    )
    work.setdefault("raw_json", {})["anchor_resolution"] = {
        "status": "resolved",
        "evidence": evidence,
    }


def set_front_target(section: dict, order: int, target_file: str | None, evidence: str) -> None:
    entry = next(item for item in section["entries"] if item["entry_order"] == order)
    entry["target_file"] = target_file
    entry.setdefault("raw_json", {})["target_file_evidence"] = evidence


def normalize_entry(entry: dict) -> None:
    """Retain every stable field while supplying the canonical entry fields."""
    raw_json = entry.setdefault("raw_json", {})
    if not isinstance(raw_json, dict):
        raw_json = {"validated_raw_json": raw_json}
        entry["raw_json"] = raw_json
    if entry.get("entry_kind") is not None:
        raw_json.setdefault("entry_kind", entry["entry_kind"])
    entry.setdefault("target_raw", None)
    entry.setdefault("target_file", None)
    entry.setdefault("page_ref_raw", None)
    raw_ref = entry.get("page_ref_raw")
    if entry.get("page_ref_int") is None:
        entry["page_ref_int"] = int(raw_ref) if isinstance(raw_ref, str) and re.fullmatch(r"\d+", raw_ref) else None
    entry.setdefault("page_ref_col", None)
    entry.setdefault("note_raw", None)
    entry.setdefault("normalized_target", entry.get("target_raw"))
    entry.setdefault("confidence", "high")


def repair_linebreak_hyphens(value):
    """Remove only terminal OCR wrap hyphens after direct page inspection."""
    if isinstance(value, dict):
        return {key: repair_linebreak_hyphens(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_linebreak_hyphens(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?<=\w)-\s*(?:\n|$)", "", value, flags=re.MULTILINE)
    return value


def main() -> None:
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled_document.get("status") != "complete":
        raise ValueError("PG144 assembled fragments are not complete")
    stable = copy.deepcopy(assembled_document["data"])
    expected_counts = (12, 5, 1192)
    actual_counts = (
        len(stable["works"]),
        len(stable["sections"]),
        sum(len(section["entries"]) for section in stable["sections"]),
    )
    if actual_counts != expected_counts:
        raise ValueError(f"unexpected validated-fragment counts: {actual_counts}")

    f011, f013, f014, f365, f399, f423 = (file_for(n) for n in (11, 13, 14, 365, 399, 423))
    f464, f465, f467, f468, f470 = (file_for(n) for n in (464, 465, 467, 468, 470))
    f471, f480, f481, f482, f483, f485, f486 = (file_for(n) for n in (471, 480, 481, 482, 483, 485, 486))
    f514, f706, f707 = (file_for(n) for n in (514, 706, 707))

    works = stable["works"]
    resolve(
        work_by_key(works, "PG144:work:petri_possini_praefatio_in_tomum_ii"),
        start_page=9, end_page=14, start_file=f011, end_file=f013, confidence="high",
        evidence=[
            "File 011 begins PETRUS POSSINUS LECTORI in the 9/10 opening; file 013 has the explicit closing Vale and date.",
            "File 014 directly begins the separate Georgii Pachymerae Andronicus Palaeologus title.",
        ],
    )
    resolve(
        work_by_key(works, "PG144:work:georgii_pachymerae_andronicus_palaeologus"),
        start_page=15, end_page=714, start_file=f014, end_file=f365, confidence="high",
        evidence=[
            "File 014 prints GEORGII PACHYMERAE / ANDRONICUS PALAELOGUS / SIVE / HISTORIA RERUM and its 15/16 editorial pair.",
            "File 365 has the 713/714 De Andronico Palaeologo lib. VII header and the direct completion formula for the history.",
        ],
    )
    observationes = work_by_key(works, "PG144:work:petri_possini_observationum_pachymerianarum")
    observationes.update({"start_page": 715, "end_page": None, "start_file": None, "end_file": None, "confidence": "medium"})
    observationes.setdefault("raw_json", {})["anchor_resolution"] = {
        "status": "ambiguous",
        "evidence": [
            "The direct title page for the declared 715 opening is absent from the readable OCR files; the next attested body header is 719 P. POSSINI OBSERVATIONUM LIB. I. in file 366.",
            "File 365 directly closes Andronicus, while file 465 directly opens Descriptio Augusteonis; neither substitutes for a missing Observationum title-page anchor.",
        ],
    }
    resolve(
        work_by_key(works, "PG144:work:georgii_pachymerae_descriptio_augusteonis"),
        start_page=917, end_page=922, start_file=f465, end_file=f467, confidence="high",
        evidence=[
            "File 465 directly prints GEORGII PACHYMERAE DESCRIPTIO AUGUSTEONIS at 917/918.",
            "File 467 retains the 921/922 Descriptio header; file 468 begins the different De Spiritu Sancto item.",
        ],
    )
    resolve(
        work_by_key(works, "PG144:work:adversus_eos_qui_dicunt_ideo_dici_spiritum_sanctum"),
        start_page=923, end_page=928, start_file=f468, end_file=f470, confidence="high",
        evidence=[
            "File 468 begins the item with the all-caps IDEO DICI SPIRITUM FILII ... heading and Græcia orthodoxa source note.",
            "File 470 carries the 927/928 GEORGII PACHYMERAE DE SPIRITU SANCTO running header; file 471 opens Theodorus Metochita Notitia.",
        ],
    )
    external = work_by_key(works, "PG144:work:argumenta_declamationum_pachymeres")
    external.update({"start_page": None, "end_page": None, "start_file": None, "end_file": None, "confidence": "high"})
    external.setdefault("raw_json", {})["external_reference"] = {
        "literal": "Vide tomum superiorem, col. 413.",
        "local_target": None,
    }
    resolve(
        work_by_key(works, "PG144:work:theodorus_metochitae_notitia_ex_bibliotheca_fabricii"),
        start_page=929, end_page=None, start_file=f471, end_file=f480, confidence="medium",
        evidence=[
            "File 471 directly prints THEODORUS METOCHITA / NOTITIA after the 929/930 header.",
            "Files 478-479 continue the Notitia running header. File 480 is the final inspected notitia prose page before the 949/950 De lingua Græca Aegypti opening in file 481; its OCR header is corrupt, so no end page is synthesized.",
        ],
    )
    resolve(
        work_by_key(works, "PG144:work:theodorus_metochitae_de_lingua_graeca_aegypti"),
        start_page=949, end_page=952, start_file=f481, end_file=f482, confidence="high",
        evidence=[
            "File 481 supplies the 949/950 DE LINGUA GRAECA AEGYPTI header; file 482 supplies 951/952.",
            "File 483 directly opens the separate Matthaeus Blastares Notitia.",
        ],
    )
    resolve(
        work_by_key(works, "PG144:work:matthaei_blastares_notitia_ex_bibliotheca_fabricii"),
        start_page=953, end_page=958, start_file=f483, end_file=f485, confidence="high",
        evidence=[
            "File 483 directly prints MATTHAEUS BLASTARES / NOTITIA at 953/954; file 485 continues 957/958 NOTITIA.",
            "File 486 directly opens the Syntagma alphabeticum title page.",
        ],
    )
    syntagma = work_by_key(works, "PG144:work:matthaei_blastares_syntagma_alphabeticum")
    resolve(
        syntagma,
        start_page=959, end_page=1400, start_file=f486, end_file=f706, confidence="high",
        evidence=[
            "File 486 directly prints the bilingual Syntagma alphabeticum title at 959/960.",
            "File 706 retains the 1399/1400 Matthaei Blastaris header; file 707 directly begins INDEX IN PACHYMERAE HISTORIAM, which belongs to the alphabetical-index pipeline.",
        ],
    )
    for duplicate_key in (
        "PG144:work:matthaei_blastares_syntagma_alphabeticum:001",
        "PG144:work:matthaeus_blastaris:syntagma_alphabeticum:001",
    ):
        duplicate = work_by_key(works, duplicate_key)
        duplicate.update({"end_page": None, "end_file": None, "confidence": "high"})
        duplicate.setdefault("raw_json", {})["relationship"] = {
            "kind": "validated_duplicate_work_representation",
            "canonical_work_key": syntagma["work_key"],
            "evidence": "File 514 directly prints MATTHAEI MONACHI SIVE BLASTARIS SYNTAGMA ALPHABETICUM and begins PROLOGUS; retained under its validated stable key rather than silently collapsed.",
        }

    front = section_by_key(stable["sections"], "PG144:section:volume_front:elenchus:001")
    front["index_kind"] = "ELENCHUS"
    front.setdefault("raw_json", {})["scope_note"] = "File 010 contains the complete volume-front ELENCHUS; all final literals are editorial references, never OCR file suffixes."
    for order, target, evidence in (
        (1, f011, "direct Petrus Possinus opening in file 011"),
        (2, f014, "direct Andronicus title page in file 014"),
        (5, f399, "direct 785/786 NOTAE header in file 399"),
        (6, f423, "direct 833/834 LIB. III. CHRONOLOGICUS header in file 423"),
        (8, f465, "direct Descriptio Augusteonis opening in file 465"),
        (9, f468, "direct De Spiritu Sancto opening in file 468"),
        (11, f471, "direct Theodorus Metochita Notitia opening in file 471"),
        (12, f481, "direct De lingua Graeca Aegypti opening in file 481"),
        (13, f483, "direct Matthaeus Blastares Notitia opening in file 483"),
        (14, f486, "direct Syntagma alphabeticum title page in file 486"),
        (15, f707, "direct INDEX IN PACHYMERAE HISTORIAM opening in file 707"),
    ):
        set_front_target(front, order, target, evidence)
    for order in (3, 4):
        set_front_target(front, order, None, "No direct physical title-page anchor survives for the declared Observationum 715 opening; numeric mapping alone was rejected.")
    for order in (7, 10):
        set_front_target(front, order, None, "Pure external remission; no local target file is asserted.")

    first_index = section_by_key(stable["sections"], "PG144:section:matthaei_blastares_syntagma_alphabeticum:index_capitum:001")
    second_index = section_by_key(stable["sections"], "PG144:section:matthaei_blastaris:syntagmatis_alphabetici:index_capitum:001")
    first_index.setdefault("raw_json", {})["scope_note"] = "Validated first slice of the bilingual Syntagma INDEX CAPITUM, preserved as its own stable section."
    second_index["index_kind"] = "INDEX CAPITUM"
    second_index.setdefault("raw_json", {})["scope_note"] = "Validated continuation slice of the bilingual Syntagma INDEX CAPITUM, preserved as its own stable section."
    closing = section_by_key(stable["sections"], "PG144:section:volume_end:ordo_rerum:003")
    closing.setdefault("raw_json", {})["scope_note"] = "Closing volume ORDO RERUM; all 653 validated line items are retained."
    continuation = section_by_key(stable["sections"], "PG144:candidate-section:003")
    continuation["scope_kind"] = "volume_end"
    continuation.setdefault("raw_json", {})["scope_note"] = "Validated 1439/1440 continuation of the closing ORDO RERUM; reclassified from the non-taxonomic volume_contents label."

    for work in works:
        work.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    for section in stable["sections"]:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            normalize_entry(entry)
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(TEXT),
            "volume_label": "PATROLOGIAE GRAECAE TOMUS CXLIV",
            "notes": [
                "File 010 contains the complete opening ELENCHUS; files 720-726 contain the closing ORDO RERUM.",
                "Editorial references and OCR physical-file locators are stored separately.",
            ],
            "scan_summary": {
                "front_index_files": [10],
                "work_index_files": [505, 506, 507, 508, 509, 510, 511, 512, 513, 514],
                "volume_end_files": [720, 721, 722, 723, 724, 725, 726],
            },
        },
        "works": works,
        "sections": stable["sections"],
        "notes": [
            *stable.get("notes", []),
            "Consumed every validated object from assembled_fragments.json: 12 works, 5 sections, and 1192 line-by-line entries.",
            "Resolved the prior Syntagma work-anchor rerun: the real title page is physical file 486 at editorial 959/960, not the nonexistent physical suffix 953.",
            "The INDEX IN PACHYMERAE HISTORIAM after editorial 1400 is left to the alphabetical-index pipeline; it is retained only as a volume-front inventory reference.",
        ],
    }

    work_keys = {work["work_key"] for work in works}
    dangling = [section["section_key"] for section in payload["sections"] if section.get("work_key") is not None and section["work_key"] not in work_keys]
    if dangling:
        raise ValueError(f"dangling section work keys: {dangling}")
    section_keys = [section["section_key"] for section in payload["sections"]]
    entry_keys = [entry.get("entry_key") for section in payload["sections"] for entry in section["entries"] if entry.get("entry_key")]
    if len(section_keys) != len(set(section_keys)) or len(entry_keys) != len(set(entry_keys)):
        raise ValueError("duplicate stable section or entry keys")
    if sum(len(section["entries"]) for section in payload["sections"]) != 1192:
        raise ValueError("validated entries were lost")
    payload = repair_linebreak_hyphens(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = verify_payload_consumes_fragments(payload, assembled_document)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report.get("status") != "ok":
        raise ValueError(f"fragment consumption verification failed: {report}")


if __name__ == "__main__":
    main()

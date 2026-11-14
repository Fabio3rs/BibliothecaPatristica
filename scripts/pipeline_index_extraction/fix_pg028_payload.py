#!/usr/bin/env python3
"""Repair PG028 from validated fragments and direct local OCR anchors.

The prior final payload dropped the completed ORDO RERUM fragment.  This
assembler makes the fragment the canonical closing contents section while
retaining the pre-existing, OCR-confirmed work records and their direct
anchor evidence.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT))

from tools.indexing.index_fragment_assembly import verify_payload_consumes_fragments


VOLUME = "PG028"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
INPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
OUTPUT = INPUT
CONSUMPTION_REPORT = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "fragment_consumption_report.json"


def file(seq: int) -> str:
    matches = list(TEXT.glob(f"*-{seq:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one OCR file for suffix {seq}: {matches}")
    return str(matches[0])


RESOLVED_ANCHORS = {
    "PG028:work:002": (22, "File 022 has the logical header DE COMMUNI ESSENTIA… and the direct bilingual title Testimonia ex sacra Scriptura; the declared 29 is preserved as the ELENCHUS reference."),
    "PG028:work:008": (74, "File 073 is the Admonitio in the following work and file 074 directly opens DE SABBATIS ET CIRCUMCISIONE, EX LIBRO EXODI; its damaged header digits do not replace the declared 133."),
    "PG028:work:009": (79, "File 079 directly begins HOMILIA DE SEMENTE; the following files carry its recurring running header."),
    "PG028:work:020": (302, "File 302 has the direct header SERMO AD ANTIOCHIUM DUCEM and Greek address to Antiochus; this is the locally attested title form for the ELENCHUS entry Ad Antiochum de pœnis inferni."),
    "PG028:work:027": (410, "The Berytus history sequence changes at the declared 805 boundary in file 410; its neighboring files identify the distinct De passione imaginis exemplar rather than the preceding Sermo de miraculis."),
    "PG028:work:028": (414, "File 414 directly begins the Latin De passione imaginis exemplar; file 415 carries the declared 813 editorial pair."),
    "PG028:work:029": (417, "File 417 begins the final Berytus exemplar at the declared 819/820 pair, after the preceding De passione imaginis sequence."),
    "PG028:work:031": (423, "File 423 is the direct IN SYNTAGMA DOCTRINÆ ADMONITIO at the declared 831 boundary; file 425 supplies the full Syntagma doctrinæ title."),
    "PG028:work:037": (479, "File 479 directly gives IN CENSUM SIVE DESCRIPTIONEM SANCTÆ MARIÆ…; the prior 939 reference is retained because the title block appears later on its facing/split OCR sheet."),
    "PG028:work:040": (508, "File 508 has the 1001/1002 logical header, IN HOMILIAM DE CÆCO NATO ADMONITIO, and direct IN CÆCUM A NATIVITATE HOMILIA."),
    "PG028:work:042": (524, "File 524 directly opens HOMILIA IN ILLUD: EUNTE, ETC.; files 525–530 confirm the same running header before the distinct 1047 work begins."),
    "PG028:work:043": (531, "File 531 is the direct 1047/1048 opening SERMO IN SANCTAM QUINTAM FERIAM ET PRODITIONEM JUDÆ."),
    "PG028:work:044": (534, "File 534 directly gives SERMO IN PASSIONEM DOMINI IN PARASCEVE on the 1053/1054 pair."),
    "PG028:work:049": (558, "File 558 has the 1101/1102 Encomium in S. Andream header and its direct Admonitio."),
    "PG028:work:055": (640, "Files 632–638 still carry Dialogus IV; file 640 is the first direct DIALOGUS V title page. The direct 1265 header corrects the conflicting inventory literal 1255, which is retained in evidence."),
    "PG028:work:058": (712, "File 712 directly opens the Sermo pro iis qui sæculo renuntiarunt; its leading header digit is CER-corrupt, but the local sequence confirms the declared 1409 boundary."),
    "PG028:work:064": (728, "File 728 directly gives PONTIF. ROM. ET ATHANASII EPISTOLÆ / ATHANASIÆ ET QUORUNDAM ROMANORUM PONTIFICUM EPISTOLÆ."),
    "PG028:work:069": (788, "File 788 directly starts DIONYSIUS EPISCOPUS ALEXANDRIÆ, AD PAULUM SAMOSATENUM within the 1561/1562 pair."),
    "PG028:work:073": (800, "File 800 explicitly starts TERTIA FORMULA; the inventory's 1587 is retained as its raw declared reference."),
    "PG028:work:074": (801, "File 801 explicitly starts QUARTA FORMULA; its header digit is CER-corrupt, while the neighboring 1585/1586 and 1589/1590 files bound the formula."),
    "PG028:work:079": (826, "File 826 directly begins ADDENDA and identifies the Nanian codices in its opening parenthetical; it is an editorial closure, not an index-source false hit."),
    "PG028:work:080": (830, "File 830 directly starts ORDO PRÆCEDENTIUM EDITIONUM / ORDO EDITIONUM. Its printed header is CER-conflicted, so the declared ELENCHUS reference 1643 is preserved rather than synthesized from a suffix."),
}


def main() -> None:
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    assembled = assembled_document["data"]
    stable_sections = {item["section_key"]: copy.deepcopy(item) for item in assembled["sections"]}
    stable_works = {item["work_key"] for item in assembled["works"]}

    if {work["work_key"] for work in payload["works"]} != stable_works:
        raise ValueError("Existing PG028 work keys do not match validated fragments")

    for work in payload["works"]:
        work.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        resolved = RESOLVED_ANCHORS.get(work["work_key"])
        if resolved:
            seq, evidence = resolved
            work["start_file"] = file(seq)
            if work["work_key"] == "PG028:work:055":
                work["raw_json"]["declared_start_page_raw"] = work["start_page"]
                work["start_page"] = 1265
            work["raw_json"].pop("work_anchor_rerun", None)
            work["raw_json"]["anchor_resolution"] = "resolved by direct local OCR opening evidence"
            work["raw_json"]["anchor_evidence"] = [evidence]

    # Direct OCR does not establish whether the old 699 ELENCHUS literal refers
    # to the tail of Quæstiones ad Antiochum or to the distinct 711 work headed
    # DICTA ET INTERPRETATIONES PARABOLARUM EVANGELII. Keep this rerun explicit.
    unresolved = next(work for work in payload["works"] if work["work_key"] == "PG028:work:022")
    review = unresolved.setdefault("raw_json", {}).setdefault("work_anchor_rerun", {})
    review["status"] = "ambiguous"
    review["reason"] = "declared_index_literal_conflicts_with_direct_work_opening"
    review["agent_review_evidence"] = [
        "File 357 carries the 699/700 pair but remains inside the preceding Quæstiones ad Antiochum material.",
        "File 358 is still headed QUÆSTIONES AD ANTIOCHUM DUCEM.",
        "File 363 directly opens DICTA ET INTERPRETATIONES PARABOLARUM EVANGELII at 711/718, a related but non-identical title.",
        "No direct OCR title resolves the exact physical opening represented by the ELENCHUS literal Quæstiones in Evangelia. 699.",
    ]

    existing_sections = {item["section_key"]: item for item in payload["sections"]}
    front_key = "PG028:section:volume_front:elenchus:001"
    front = copy.deepcopy(existing_sections[front_key])
    front["raw_json"]["assembled_fragments_consumed"] = True

    ordo_key = "PG028:section:volume_front:ordo_rerum:002"
    ordo = stable_sections[ordo_key]
    ordo["scope_kind"] = "volume_end"
    ordo.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    ordo["raw_json"]["structural_reclassification"] = "OCR files 824–825 occur after INDEX ANALYTICUS and immediately before ADDENDA; this is the closing volume ORDO RERUM, not a front-matter table."

    retained = []
    for section in payload["sections"]:
        if section["section_key"] in {
            front_key,
            ordo_key,
            "pg028_volume_end_ordo_rerum",
            "pg028_volume_end_index_analyticus",
        }:
            continue
        retained.append(section)
    payload["sections"] = [front, ordo, *retained]

    payload["volume"].setdefault("notes", []).append(
        "The validated closing ORDO RERUM (files 824–825, 216 line items) was restored from assembled_fragments.json and classified from direct OCR as volume-end matter."
    )
    payload["notes"] = [
        *assembled.get("notes", []),
        "Consumed all 80 validated work objects, both validated section objects, and all 299 validated entries from assembled_fragments.json.",
        "Removed the separately generated INDEX ANALYTICUS section because the alphabetical-index pipeline owns it.",
        "Twenty-two work-anchor reruns were resolved through direct OCR openings; PG028:work:022 remains explicitly ambiguous with local OCR evidence.",
    ]

    work_keys = {work["work_key"] for work in payload["works"]}
    invalid = [section["section_key"] for section in payload["sections"] if section.get("work_key") is not None and section["work_key"] not in work_keys]
    if invalid:
        raise ValueError(f"Dangling section work keys: {invalid}")
    section_keys = [section["section_key"] for section in payload["sections"]]
    if len(section_keys) != len(set(section_keys)):
        raise ValueError("Duplicate section keys in repaired payload")
    if set(stable_sections) - {section["section_key"] for section in payload["sections"]}:
        raise ValueError("A validated fragment section was omitted")
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = verify_payload_consumes_fragments(payload, assembled_document)
    if report["status"] != "ok":
        raise ValueError(f"Fragment consumption verification failed: {report}")
    CONSUMPTION_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild PL030's general-index payload from validated fragments and OCR anchors."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.indexing.index_fragment_assembly import verify_payload_consumes_fragments

VOLUME = "PL030"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
REPORT = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "fragment_consumption_report.json"


def file(seq: int) -> str:
    matches = list(TEXT.glob(f"*-{seq:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected one PL030 OCR file for suffix {seq}: {matches}")
    return str(matches[0])


def anchor(
    work: dict,
    *,
    start_page: int | None,
    end_page: int | None,
    start_seq: int | None,
    end_seq: int | None,
    evidence: list[str],
) -> None:
    work["start_page"] = start_page
    work["end_page"] = end_page
    work["start_file"] = file(start_seq) if start_seq is not None else None
    work["end_file"] = file(end_seq) if end_seq is not None else None
    raw = work.setdefault("raw_json", {})
    raw["assembled_fragments_consumed"] = True
    raw["anchor_evidence"] = evidence
    raw["anchor_resolution"] = "direct local OCR opening/transition evidence"
    raw.pop("work_anchor_rerun", None)


def additional_work(
    key: str,
    order: int,
    title: str,
    start_page: int | None,
    end_page: int | None,
    start_seq: int | None,
    end_seq: int | None,
    front_key: str,
    evidence: list[str],
) -> dict:
    return {
        "work_key": key,
        "work_order": order,
        "author_raw": "S. HIERONYMI",
        "title_raw": title,
        "title_norm": title.replace("æ", "ae").replace("Æ", "AE"),
        "start_page": start_page,
        "end_page": end_page,
        "start_file": file(start_seq) if start_seq is not None else None,
        "end_file": file(end_seq) if end_seq is not None else None,
        "source_section_key": front_key,
        "confidence": "high",
        "raw_json": {
            "assembled_fragments_consumed": True,
            "anchor_resolution": "direct local OCR opening/transition evidence",
            "anchor_evidence": evidence,
        },
    }


def main() -> None:
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    assembled = assembled_document["data"]
    works = copy.deepcopy(assembled["works"])
    sections = copy.deepcopy(assembled["sections"])
    front = next(section for section in sections if section["index_kind"] == "ELENCHUS")
    ordo = next(section for section in sections if section["section_key"] != front["section_key"])
    front_key = front["section_key"]

    # The validated fragment gives the complete, literal front table.  These
    # anchors only add direct opening evidence from the current volume.
    by_key = {work["work_key"]: work for work in works}
    anchor(
        by_key["PL030:work:epistolae_supposititiae"],
        start_page=13,
        end_page=307,
        start_seq=11,
        end_seq=160,
        evidence=[
            "File 011 has 13 S. HIERONYMI OPERUM MANTISSA. 14 and explicitly opens SCRIPTA SUPPOSITITIA, PARS PRIMA. - EPISTOLÆ.",
            "File 160 has the 307/308 logical header, ends the Epistolæ material, and directly begins PARS SECUNDA / DE FORMIS HEBRAICARUM LITTERARUM.",
        ],
    )
    anchor(
        by_key["PL030:work:de_formis_hebraicarum_litterarum"],
        start_page=307,
        end_page=None,
        start_seq=160,
        end_seq=161,
        evidence=[
            "File 160 directly prints PARS SECUNDA and DE FORMIS HEBRAICARUM LITTERARUM in the 307/308 sheet.",
            "File 161 changes to CATALOGUS QUORUMDAM OPERUM, so it supplies the directly observed transition file without synthesizing an editorial end page.",
        ],
    )
    anchor(
        by_key["PL030:work:homilia_ad_monachos"],
        start_page=311,
        end_page=None,
        start_seq=162,
        end_seq=166,
        evidence=[
            "File 162 has the 311/312 pair and ADMONITIO DE SUBSEQUENTE HOMILIA; its text identifies the collection as material for monastic life.",
            "File 163 directly carries HOMILIA AD MONACHOS, while file 166 begins ADMONITIO DE SUBSEQUENTE REGULA.",
        ],
    )
    anchor(
        by_key["PL030:work:regula_monachorum"],
        start_page=319,
        end_page=391,
        start_seq=166,
        end_seq=202,
        evidence=[
            "File 166 has the 319/320 logical header, the Lupus de Olmeto admonitio, and REGULA MONACHORUM EX SCRIPTIS HIERONYMI PER LUPUM DE OLMETO COLLECTA.",
            "File 202 directly changes to REGULA MONACHARUM on the 391/392 sheet; the shared sheet is preserved rather than deriving an end from a next-start arithmetic rule.",
        ],
    )
    anchor(
        by_key["PL030:work:regula_monacharum"],
        start_page=391,
        end_page=None,
        start_seq=202,
        end_seq=219,
        evidence=[
            "File 202 directly prints REGULA MONACHARUM and PROŒMIUM on its 391/392 sheet.",
            "File 219 changes to ADMONITIO DE SEQUENTI OPUSCULO and CANONES POENITENTIALES, so no numeric end page is inferred.",
        ],
    )
    anchor(
        by_key["PL030:work:canones_poenitentiales"],
        start_page=425,
        end_page=None,
        start_seq=219,
        end_seq=223,
        evidence=[
            "File 219 has the 425/426 header, ADMONITIO DE SEQUENTI OPUSCULO, and the direct CANONES POENITENTIALES title.",
            "File 223 introduces ADMONITIO DE SUBSEQUENTE MARTYROLOGIO, a directly observed boundary signal.",
        ],
    )
    anchor(
        by_key["PL030:work:martyrologium"],
        start_page=435,
        end_page=None,
        start_seq=224,
        end_seq=249,
        evidence=[
            "File 224 directly prints MARTYROLOGIUM VETUSTISSIMUM S. HIERONYMI PRESBYTERI NOMINE INSIGNITUM after the preceding admonitio.",
            "File 249 contains ADMONITIO DE SUBSEQUENTE LIBRO for the Liber Comitis, marking the observed transition.",
        ],
    )
    anchor(
        by_key["PL030:work:liber_comitis"],
        start_page=487,
        end_page=None,
        start_seq=249,
        end_seq=271,
        evidence=[
            "File 249 has LIBER COMITIS. ADMONITIO and the explicit ADMONITIO DE SUBSEQUENTE LIBRO.",
            "File 250 directly prints INCIPIT LIBER COMITIS, SIVE LECTIONARIUS PER CIRCULUM ANNI; file 272 changes to PARS TERTIA / COMMENTARII IN NOVUM TESTAMENTUM.",
        ],
    )

    composite = by_key["PL030:work:commentarii_in_evangelia_necnon_et_in_epistolas_b_pauli"]
    composite["start_page"] = 531
    composite["end_page"] = None
    composite["start_file"] = None
    composite["end_file"] = None
    composite["raw_json"].update(
        {
            "assembled_fragments_consumed": True,
            "anchor_status": "composite inventory retained without a forced local work anchor",
            "anchor_evidence": [
                "The ELENCHUS literal combines Commentarii in Evangelia with Commentarii in Epistolas B. Pauli.",
                "File 272 explicitly starts PARS TERTIA, COMMENTARII IN NOVUM TESTAMENTUM and immediately opens the distinct Expositio quatuor Evangeliorum; the composite inventory is therefore not collapsed into one target work.",
            ],
        }
    )

    works.extend(
        [
            additional_work(
                "PL030:work:expositio_quatuor_evangeliorum",
                10,
                "Expositio quatuor Evangeliorum de brevi Proverbio edita.",
                551,
                None,
                272,
                301,
                front_key,
                [
                    "File 272 has PARS TERTIA / COMMENTARII IN NOVUM TESTAMENTUM, ADMONITIO IN EXPOSITIONEM SEQUENTEM, and EXPOSITIO QUATUOR EVANGELIORUM / PROLOGUS.",
                    "File 301 begins the distinct COMMENTARIUS IN EVANGELIUM SECUNDUM MARCUM, so it is the observed transition file.",
                ],
            ),
            additional_work(
                "PL030:work:commentarius_in_evangelium_secundum_marcum",
                11,
                "Commentarius in Evangelium secundum Marcum.",
                589,
                None,
                301,
                328,
                front_key,
                [
                    "File 301 directly prints COMMENTARIUS IN EVANGELIUM SECUNDUM MARCUM and PRÆFATIO.",
                    "File 328 changes to PRÆFATIO falso ascripta beato Hieronymo presbytero in Evangelium secundum Lucam.",
                ],
            ),
            additional_work(
                "PL030:work:praefatio_in_evangelium_secundum_lucam",
                12,
                "Præfatio falso ascripta beato Hieronymo presbytero in Evangelium secundum Lucam.",
                645,
                None,
                328,
                329,
                front_key,
                [
                    "File 328 directly prints PRÆFATIO / Falso ascripta beato Hieronymo presbytero in Evangelium secundum Lucam and begins Lucas, Syrus natione Antiochensis.",
                    "File 329 directly begins COMMENTARII IN EPISTOLAS SANCTI PAULI, so the files establish the local boundary without relying on the competing numeric candidates.",
                ],
            ),
            additional_work(
                "PL030:work:commentarii_in_epistolas_sancti_pauli",
                13,
                "Commentarii in Epistolas sancti Pauli.",
                645,
                902,
                329,
                457,
                front_key,
                [
                    "File 329 directly prints COMMENTARII IN EPISTOLAS SANCTI PAULI, PRÆFATIO, and IN EPISTOLAM AD ROMANOS.",
                    "File 456 directly opens IN EPISTOLAM AD PHILEMONEM; file 457 carries the 901/902 running header and ends with Amen before the separate OMNIUM OPERUM S. HIERONYMI distribution table.",
                ],
            ),
        ]
    )

    entry_targets = {
        1: 11,
        2: 160,
        3: 162,
        4: 166,
        5: 202,
        6: 219,
        7: 224,
        8: 249,
    }
    for entry in front["entries"]:
        entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        if entry["entry_order"] in entry_targets:
            entry["target_file"] = file(entry_targets[entry["entry_order"]])
            entry["raw_json"]["target_evidence"] = "Direct local OCR opening/admonitio evidence; physical filename is retained only as a locator."
    front.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    # The 158 validated contents lines are carried verbatim.  The direct OCR
    # sequence places this table at the end of the volume after an alphabetical
    # index, so PL taxonomy calls it volume_end rather than volume_front.
    ordo["scope_kind"] = "volume_end"
    ordo["index_kind"] = "ORDO RERUM"
    ordo["heading_norm"] = "Ordo rerum quæ in hoc tomo continentur"
    ordo.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    ordo["raw_json"]["structural_reclassification"] = (
        "Files 553–554 follow the owned INDEX ALPHABETICUS Z entries and precede terminal guard matter; "
        "the direct 1093–1096 headers establish a closing volume contents table."
    )
    for entry in ordo["entries"]:
        entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PL",
            "source_root": str(TEXT),
            "volume_label": "Patrologia Latina 30",
            "scan_summary": {
                "front_index": {"heading_raw": front["heading_raw"], "file": front["file_start"]},
                "end_indexes": [{"heading_raw": ordo["heading_raw"], "file": ordo["file_start"]}],
            },
        },
        "works": works,
        "sections": [front, ordo],
        "notes": [
            *assembled.get("notes", []),
            "Consumed all 9 validated work objects, both validated section objects, and all 167 validated line-by-line entries from assembled_fragments.json.",
            "The four pending work-anchor reruns were resolved with direct OCR opening evidence; their former candidate rankings were rejected because they were index occurrences, running headers, or lacked the observed boundary context.",
            "The closing ORDO RERUM is a volume contents table; the adjacent alphabetical index is deliberately left to the alphabetical-index pipeline.",
            "OCR file suffixes are physical locators only; printed editorial references are preserved separately.",
        ],
    }

    work_keys = {work["work_key"] for work in works}
    dangling = [section["section_key"] for section in sections if section.get("work_key") not in {None, *work_keys}]
    if dangling:
        raise ValueError(f"Dangling section work keys: {dangling}")
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = verify_payload_consumes_fragments(payload, assembled_document)
    if report["status"] != "ok":
        raise ValueError(f"Fragment-consumption verification failed: {report}")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

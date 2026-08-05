#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG007.02"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
ASSEMBLED = (
    ROOT
    / "data"
    / "index_intermediate_payloads"
    / VOLUME_ID
    / "assembled_fragments.json"
)
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME_ID}_indices.json"


def physical_file(file_seq: int) -> str:
    matches = sorted(SOURCE_ROOT.glob(f"*-{file_seq:03d}.txt"))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one physical OCR file for suffix {file_seq:03d}; got {matches}"
        )
    return str(matches[0])


def entry(
    *,
    section_key: str,
    order: int,
    entry_raw: str,
    target_raw: str,
    target_seq: int | None,
    page_ref_raw: str | None,
    normalized_target: str,
    note_raw: str | None = None,
    raw_json: dict | None = None,
) -> dict:
    page_ref_int = None
    if page_ref_raw:
        match = re.fullmatch(r"\d+", page_ref_raw)
        if match:
            page_ref_int = int(page_ref_raw)
    return {
        "entry_key": f"{section_key}:final:{order}",
        "entry_order": order,
        "entry_raw": entry_raw,
        "target_raw": target_raw,
        "target_file": physical_file(target_seq) if target_seq is not None else None,
        "page_ref_raw": page_ref_raw,
        "page_ref_int": page_ref_int,
        "page_ref_col": None,
        "note_raw": note_raw,
        "normalized_target": normalized_target,
        "confidence": "high",
        "raw_json": raw_json or {},
    }


def work(
    *,
    key: str,
    order: int,
    author: str,
    title: str,
    title_norm: str,
    start_page: int | None,
    end_page: int | None,
    start_seq: int | None,
    end_seq: int | None,
    source_section_key: str,
    confidence: str,
    evidence: dict,
) -> dict:
    return {
        "work_key": key,
        "work_order": order,
        "author_raw": author,
        "title_raw": title,
        "title_norm": title_norm,
        "start_page": start_page,
        "end_page": end_page,
        "start_file": physical_file(start_seq) if start_seq is not None else None,
        "end_file": physical_file(end_seq) if end_seq is not None else None,
        "source_section_key": source_section_key,
        "confidence": confidence,
        "raw_json": evidence,
    }


def main() -> None:
    assembled = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled["status"] != "complete":
        raise RuntimeError("Validated chunk assembly is not complete")
    stable = assembled["data"]
    if assembled["counts"] != {
        "works": 5,
        "sections": 2,
        "notes": 9,
        "entries": 240,
    }:
        raise RuntimeError(f"Unexpected assembled counts: {assembled['counts']}")

    stable_works = copy.deepcopy(stable["works"])
    stable_sections = copy.deepcopy(stable["sections"])
    stable_notes = copy.deepcopy(stable["notes"])

    elenchus_key = "PG007.02:section:volume_front:elenchus:001"
    prolegomena_key = "PG007.02:section:work_front:prolegomena_variorum:002"
    grabe_section_key = "PG007.02:section:work_front:grabe_prolegomena:003"
    ordo_key = "PG007.02:section:volume_end:ordo_rerum:002"

    stable_work_map = {item["work_key"]: item for item in stable_works}

    prolegomena = stable_work_map["PG007.02:work:prolegomena"]
    prolegomena.update(
        {
            "start_file": None,
            "end_file": None,
            "end_page": None,
            "confidence": "medium",
        }
    )
    prolegomena["raw_json"].update(
        {
            "final_anchor_review": {
                "status": "resolved_as_not_local",
                "evidence": [
                    "The ELENCHUS literal is 'Prolegomena. Col. 9'.",
                    "The current source is PARS SECUNDA and its local body begins around column 1117.",
                    "Physical file 009 is a title leaf for CONTRA HÆRESES LIBRI QUINQUE, not the Prolegomena at column 9.",
                ],
                "local_target": None,
            }
        }
    )

    adversus = stable_work_map[
        "PG007.02:work:adversus_haereses_libri_quinque"
    ]
    adversus.update(
        {
            "start_file": physical_file(9),
            "end_file": physical_file(64),
            "end_page": 1223,
            "confidence": "high",
        }
    )
    adversus["raw_json"].update(
        {
            "merged_prior_work_key": "pg00702-irenaeus-adversus-haereses",
            "final_anchor_review": {
                "status": "resolved",
                "start_evidence": [
                    "Physical file 009 is a direct material title leaf: SANCTI IRENÆI ... CONTRA HÆRESES LIBRI QUINQUE.",
                    "Physical file 011 contains ANALYSIS LIBRI QUINTI and file 012 contains LIBER QUINTUS, PRÆFATIO, and CAPUT PRIMUM.",
                    "The editorial work start 433 comes from the full-tome ELENCHUS; the extant part-II body is a continuation, so the title leaf and editorial start are deliberately distinct anchors.",
                ],
                "end_evidence": [
                    "Files 060-063 contain CAPUT XXXIV-XXXVI of book V.",
                    "Physical file 064 shares the end of book V with DE SEQUENTIBUS FRAGMENTIS ADMONITIO.",
                    "The local end therefore shares editorial column 1223 and physical file 064 with the following work.",
                ],
                "rejected_candidates": [
                    {
                        "file": physical_file(464),
                        "reason": "closing ORDO RERUM inventory, not a work target",
                    },
                    {
                        "file": physical_file(137),
                        "reason": "Grabe prolegomena discussion, not the Irenaeus work opening",
                    },
                    {
                        "file": physical_file(271),
                        "reason": "numeric/CER coincidence without title-page evidence",
                    },
                ],
            },
        }
    )
    adversus["raw_json"].pop("work_anchor_rerun", None)

    fragmenta = stable_work_map[
        "PG007.02:work:fragmenta_deperditorum_operum_s_irenaei"
    ]
    fragmenta.update(
        {
            "start_page": 1223,
            "end_page": 1263,
            "start_file": physical_file(64),
            "end_file": physical_file(84),
            "confidence": "high",
        }
    )
    fragmenta["raw_json"].update(
        {
            "final_anchor_review": {
                "status": "resolved",
                "start_evidence": "File 064 directly begins DE SEQUENTIBUS FRAGMENTIS ADMONITIO; file 065 carries the full FRAGMENTA DEPERDITORUM OPERUM title.",
                "end_evidence": "File 084 contains the last numbered fragment lines and the direct APPENDIX title on the same physical sheet.",
                "shared_boundaries": [
                    "start shared with the end of Adversus haereses on file 064 / column 1223",
                    "end shared with the Appendix opening on file 084 / column 1263",
                ],
            }
        }
    )

    appendix = stable_work_map[
        "PG007.02:work:appendix_ad_irenaei_libros_contra_haereses_continens_gnosticorum_quorum_meminit_s_martyr_fragmenta"
    ]
    appendix.update(
        {
            "end_page": 1320,
            "start_file": physical_file(84),
            "end_file": physical_file(120),
            "confidence": "high",
        }
    )
    appendix["raw_json"].update(
        {
            "final_anchor_review": {
                "status": "resolved",
                "start_evidence": "File 084 has the direct APPENDIX AD IRENÆI LIBROS CONTRA HÆRESES title and editorial header 1263-1264.",
                "end_evidence": "File 120 has header 1319-1320 and closes the Heracleon fragment sequence; file 121 starts the next editorial container.",
                "duplicate_scan_note": "Files 092-099 duplicate columns 1263-1278 already present in files 084-091; the first physical occurrence is retained as the work anchor.",
            }
        }
    )

    editorial_container_key = (
        "PG007.02:work:"
        "praefationes_prolegomena_notae_et_observationes_eorum_omnium_"
        "qui_novas_irenaei_editiones_aut_publicaverunt_aut_illustrarunt"
    )
    editorial_container = stable_work_map[editorial_container_key]
    editorial_container.update(
        {
            "start_file": physical_file(121),
            "end_file": physical_file(410),
            "end_page": 1900,
            "confidence": "high",
        }
    )
    editorial_container["raw_json"].update(
        {
            "final_anchor_review": {
                "status": "resolved",
                "start_evidence": "File 121 directly displays PRÆFATIONES, PROLEGOMENA, NOTÆ ET OBSERVATIONES and begins the Erasmus letter.",
                "editorial_start_sequence": [
                    "file 120: 1319-1320",
                    "file 121 OCR literal: 1521-1522 (CER for 1321-1322)",
                    "file 122: 1323-1324",
                ],
                "end_evidence": "File 410 closes GLOSSARIUM LATINUM at columns 1899-1900; file 411 starts the excluded alphabetical INDEX RERUM ET SENTENTIARUM.",
            }
        }
    )

    added_works = [
        work(
            key="pg00702-erasmus-epistola",
            order=6,
            author="DESIDERII ERASMI",
            title="EPISTOLA NUNCUPATORIA",
            title_norm="Epistola Nuncupatoria",
            start_page=1321,
            end_page=1329,
            start_seq=121,
            end_seq=125,
            source_section_key=prolegomena_key,
            confidence="high",
            evidence={
                "final_anchor_review": {
                    "status": "resolved",
                    "direct_opening": "File 121 contains DESIDERII ERASMI / EPISTOLA NUNCUPATORIA and its incipit addressed to Bernardus.",
                    "editorial_sequence": "File 120 has 1319-1320; file 122 has 1323-1324, resolving file 121's OCR literal 1521-1522 as 1321-1322.",
                    "direct_end": "File 125 closes the Erasmus text with 'Bene vale, lector' before NICOLAI GALLASII.",
                    "inspected_competitors": [
                        physical_file(121),
                        physical_file(127),
                        physical_file(140),
                        physical_file(55),
                        physical_file(221),
                    ],
                    "rejections": {
                        physical_file(140): "historical mention inside Grabe's section III",
                        physical_file(127): "continuation of Gallasii material",
                        physical_file(55): "book V body without the title",
                        physical_file(221): "later VARIORUM NOTÆ material",
                    },
                },
                "shared_end": "The transition to Gallasii occurs on physical file 125 under the 1329-1330 header.",
            },
        ),
        work(
            key="PG007.02:work:nicolai_gallasii_epistola",
            order=7,
            author="NICOLAI GALLASII",
            title="Epistola nuncupatoria ad Edmundum Grindallum, episcopum Londinensem, in qua suæ Irenæi editionis consilium exponit.",
            title_norm="Epistola nuncupatoria ad Edmundum Grindallum",
            start_page=1329,
            end_page=1339,
            start_seq=125,
            end_seq=130,
            source_section_key=prolegomena_key,
            confidence="high",
            evidence={
                "direct_opening": "File 125 displays NICOLAI GALLASII and the complete epistola heading.",
                "direct_end": "File 130 closes the letter before JACOBI BILLII.",
                "shared_boundaries": [physical_file(125), physical_file(130)],
            },
        ),
        work(
            key="PG007.02:work:jacobi_billii_admonitio",
            order=8,
            author="JACOBI BILLII",
            title="De sua priorum 18 (21) Irenæi capitum translatione, suisque in eadem scholiis admonitio.",
            title_norm="Admonitio de priorum Irenaei capitum translatione",
            start_page=1339,
            end_page=1339,
            start_seq=130,
            end_seq=130,
            source_section_key=prolegomena_key,
            confidence="high",
            evidence={
                "direct_opening_and_end": "The complete short admonitio is enclosed on file 130 between the Gallasii and Feuardentii headings."
            },
        ),
        work(
            key="PG007.02:work:francisci_feuardentii_commonitio",
            order=9,
            author="FRANCISCI FEUARDENTII",
            title="Commonitio ad lectores de sua quinque librorum D. Irenæi editione.",
            title_norm="Commonitio ad lectores",
            start_page=1339,
            end_page=1351,
            start_seq=130,
            end_seq=136,
            source_section_key=prolegomena_key,
            confidence="high",
            evidence={
                "direct_opening": "File 130 displays FRANCISCI FEUARDENTII and the complete Commonitio heading.",
                "direct_end": "File 136 finishes Feuardentii before the centered Grabe title.",
                "shared_boundaries": [physical_file(130), physical_file(136)],
            },
        ),
        work(
            key="pg00702-grabe-prolegomena",
            order=10,
            author="JOANNIS ERNESTI GRABE",
            title="PROLEGOMENA",
            title_norm="Prolegomena",
            start_page=1351,
            end_page=1362,
            start_seq=136,
            end_seq=141,
            source_section_key=prolegomena_key,
            confidence="high",
            evidence={
                "final_anchor_review": {
                    "status": "resolved",
                    "direct_opening": "File 136 contains the centered title JOANNIS ERNESTI GRABE / PROLEGOMENA followed by SECTIO I.",
                    "direct_structure": [
                        "SECTIO I on file 136",
                        "SECTIO II on file 137",
                        "SECTIO III on file 140",
                    ],
                    "direct_end": "File 141 completes SECTIO III; file 142 opens VARIORUM NOTÆ IN LIBROS S. IRENÆI CONTRA HÆRESES.",
                    "editorial_sequence": "Headers 1351-1352 through 1361-1362 are locally consecutive across files 136-141.",
                    "inspected_competitors": [
                        physical_file(128),
                        physical_file(136),
                        physical_file(138),
                        physical_file(140),
                        physical_file(464),
                    ],
                    "rejections": {
                        physical_file(128): "running header inside earlier Gallasii material",
                        physical_file(138): "continuation of Grabe body",
                        physical_file(140): "SECTIO III, not the work opening",
                        physical_file(464): "closing ORDO RERUM inventory",
                    },
                }
            },
        ),
    ]

    works = stable_works + added_works

    stable_section_map = {item["section_key"]: item for item in stable_sections}
    elenchus = stable_section_map[elenchus_key]
    elenchus_entries = elenchus["entries"]
    elenchus_entries[0].update(
        {
            "target_file": None,
            "confidence": "medium",
        }
    )
    elenchus_entries[0]["raw_json"].update(
        {
            "final_target_review": {
                "status": "not_local_to_part_II",
                "reason": "The declared Prolegomena begins at column 9, while this source part begins around column 1117; file 009 is a Contra haereses title leaf.",
            }
        }
    )
    elenchus_entries[1]["target_file"] = physical_file(9)
    elenchus_entries[1]["raw_json"].update(
        {
            "final_target_review": {
                "status": "resolved",
                "evidence": "Direct material title leaf on file 009; local continuation begins with book V on files 011-012.",
            }
        }
    )
    elenchus_entries[2]["target_file"] = physical_file(65)
    elenchus_entries[2]["raw_json"].update(
        {
            "final_target_review": {
                "status": "resolved",
                "evidence": "File 065 has the direct full FRAGMENTA DEPERDITORUM OPERUM title; the preceding admonitio starts on file 064.",
            }
        }
    )
    elenchus_entries[3]["target_file"] = physical_file(84)
    elenchus_entries[3]["raw_json"].update(
        {
            "final_target_review": {
                "status": "resolved",
                "evidence": "Direct APPENDIX title on file 084.",
            }
        }
    )
    elenchus_entries[4]["target_file"] = physical_file(121)
    elenchus_entries[4]["raw_json"].update(
        {
            "final_target_review": {
                "status": "resolved",
                "evidence": "Direct PRÆFATIONES, PROLEGOMENA, NOTÆ ET OBSERVATIONES heading on file 121.",
            }
        }
    )
    elenchus["raw_json"].update(
        {
            "final_review": {
                "verified": True,
                "entry_count": 5,
                "scope": "whole tome VII inventory printed at the front of pars secunda",
                "numbering": "Col. 9 and the other trailing numbers are editorial column references, not OCR suffixes.",
            }
        }
    )

    prolegomena_entries = [
        entry(
            section_key=prolegomena_key,
            order=1,
            entry_raw="DESIDERII ERASMI EPISTOLA NUNCUPATORIA",
            target_raw="DESIDERII ERASMI EPISTOLA NUNCUPATORIA",
            target_seq=121,
            page_ref_raw=None,
            normalized_target="Desiderii Erasmi Epistola Nuncupatoria",
            raw_json={"editorial_header_pair": [1321, 1322]},
        ),
        entry(
            section_key=prolegomena_key,
            order=2,
            entry_raw="NICOLAI GALLASII Epistola nuncupatoria ad Edmundum Grindallum, episcopum Londinensem, in qua suæ Irenæi editionis consilium exponit.",
            target_raw="NICOLAI GALLASII Epistola nuncupatoria",
            target_seq=125,
            page_ref_raw=None,
            normalized_target="Nicolai Gallasii Epistola nuncupatoria",
            raw_json={"editorial_header_pair": [1329, 1330]},
        ),
        entry(
            section_key=prolegomena_key,
            order=3,
            entry_raw="JACOBI BILLII De sua priorum 18 (21) Irenæi capitum translatione, suisque in eadem scholiis admonitio.",
            target_raw="JACOBI BILLII Admonitio",
            target_seq=130,
            page_ref_raw=None,
            normalized_target="Jacobi Billii Admonitio",
            raw_json={"editorial_header_pair": [1339, 1340]},
        ),
        entry(
            section_key=prolegomena_key,
            order=4,
            entry_raw="FRANCISCI FEUARDENTII Commonitio ad lectores de sua quinque librorum D. Irenæi editione.",
            target_raw="FRANCISCI FEUARDENTII Commonitio ad lectores",
            target_seq=130,
            page_ref_raw=None,
            normalized_target="Francisci Feuardentii Commonitio ad lectores",
            raw_json={"editorial_header_pair": [1339, 1340]},
        ),
        entry(
            section_key=prolegomena_key,
            order=5,
            entry_raw="JOANNIS ERNESTI GRABE PROLEGOMENA",
            target_raw="JOANNIS ERNESTI GRABE PROLEGOMENA",
            target_seq=136,
            page_ref_raw=None,
            normalized_target="Joannis Ernesti Grabe Prolegomena",
            raw_json={"editorial_header_pair": [1351, 1352]},
        ),
    ]
    prolegomena_section = {
        "section_key": prolegomena_key,
        "work_key": editorial_container_key,
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA VARIORUM",
        "heading_raw": "PRÆFATIONES, PROLEGOMENA, NOTÆ ET OBSERVATIONES EORUM OMNIUM QUI NOVAS IRENÆI EDITIONES AUT PUBLICAVERUNT AUT ILLUSTRARUNT. / PROLEGOMENA VARIORUM",
        "heading_norm": "Praefationes, prolegomena, notae et observationes / Prolegomena variorum",
        "page_start": 1321,
        "page_end": 1362,
        "file_start": physical_file(121),
        "file_end": physical_file(141),
        "confidence": "high",
        "raw_json": {
            "classification": "work-front structural sequence of prefatory pieces, not a closing alphabetical index",
            "candidate_file_review": {
                physical_file(121): "direct container and Erasmus opening",
                physical_file(128): "running header PROLEGOMENA VARIORUM inside Gallasii body",
                physical_file(132): "running header with digit CER (4513/1514 for 1343/1344)",
                physical_file(136): "direct Grabe opening after Feuardentii",
                physical_file(140): "running header and direct SECTIO III, not a new work",
                physical_file(142): "transition to VARIORUM NOTÆ, outside this section",
            },
            "entry_semantics": "Entries are the explicit piece headings encountered in physical order.",
        },
        "entries": prolegomena_entries,
    }

    grabe_entries = [
        entry(
            section_key=grabe_section_key,
            order=1,
            entry_raw="SECTIO I. De tempore nativitatis et obitus Irenæi, necnon genere mortis ejus.",
            target_raw="SECTIO I",
            target_seq=136,
            page_ref_raw=None,
            normalized_target="Sectio I: de tempore nativitatis et obitus Irenaei",
            raw_json={"editorial_header_pair": [1351, 1352]},
        ),
        entry(
            section_key=grabe_section_key,
            order=2,
            entry_raw="SECTIO II. De quinque Irenæi contra omnes hæreses libris ac horum versione, aliisque deperditis ejusdem scriptis.",
            target_raw="SECTIO II",
            target_seq=137,
            page_ref_raw=None,
            normalized_target="Sectio II: de quinque Irenaei contra omnes haereses libris",
            raw_json={"editorial_header_pair": [1353, 1354]},
        ),
        entry(
            section_key=grabe_section_key,
            order=3,
            entry_raw="SECTIO III. De variis quinque Irenæi librorum adversus hæreses editionibus, ac hujus novissimæ ratione atque consilio.",
            target_raw="SECTIO III",
            target_seq=140,
            page_ref_raw=None,
            normalized_target="Sectio III: de variis editionibus",
            raw_json={"editorial_header_pair": [1359, 1360]},
        ),
    ]
    grabe_section = {
        "section_key": grabe_section_key,
        "work_key": "pg00702-grabe-prolegomena",
        "scope_kind": "work_front",
        "index_kind": "PROLEGOMENA",
        "heading_raw": "JOANNIS ERNESTI GRABE PROLEGOMENA",
        "heading_norm": "Joannis Ernesti Grabe Prolegomena",
        "page_start": 1351,
        "page_end": 1362,
        "file_start": physical_file(136),
        "file_end": physical_file(141),
        "confidence": "high",
        "raw_json": {
            "classification": "internal three-section structure recovered from direct headings",
            "next_boundary": {
                "file": physical_file(142),
                "heading": "VARIORUM NOTÆ IN LIBROS S. IRENÆI CONTRA HÆRESES.",
            },
        },
        "entries": grabe_entries,
    }

    ordo = stable_section_map[ordo_key]
    if len(ordo["entries"]) != 235:
        raise RuntimeError("Validated ORDO fragment no longer has 235 entries")
    ordo.update(
        {
            "page_end": 2016,
            "file_end": physical_file(468),
            "confidence": "high",
        }
    )

    chapter_targets = {
        199: 11,
        200: 12,
        201: 12,
        202: 12,
        203: 14,
        204: 16,
        205: 18,
        206: 19,
        207: 20,
        208: 22,
        209: 23,
        210: 24,
        211: 26,
        212: 27,
        213: 28,
        214: 30,
        215: 32,
        216: 34,
        217: 36,
        218: 36,
        219: 38,
        220: 40,
        221: 41,
        222: 41,
        223: 43,
        224: 44,
        225: 45,
        226: 46,
        227: 48,
        228: 50,
        229: 51,
        230: 53,
        231: 54,
        232: 56,
        233: 57,
        234: 58,
        235: 60,
    }
    for item in ordo["entries"]:
        order = item["entry_order"]
        if order == 53:
            item["target_file"] = physical_file(9)
            item["raw_json"]["final_target_review"] = {
                "status": "resolved_to_part_title_leaf",
                "evidence": "File 009 directly titles CONTRA HÆRESES LIBRI QUINQUE; the body at editorial column 433 belongs to pars prima.",
            }
        elif order in chapter_targets:
            item["target_file"] = physical_file(chapter_targets[order])
            item["raw_json"]["final_target_review"] = {
                "status": "resolved",
                "evidence": "Direct matching ANALYSIS/LIBER/PRÆFATIO/CAPUT heading in the local book-V continuation.",
            }
        elif order <= 198:
            item["target_file"] = None
            item["raw_json"]["final_target_review"] = {
                "status": "not_local_to_part_II",
                "reason": "The referenced editorial material precedes the local body continuation at column 1117; no physical target was invented from the number.",
            }

    extra_ordo_entries = [
        entry(
            section_key=ordo_key,
            order=236,
            entry_raw="CAP. XXV. — Contendit superiora testimonia non posse per allegoriam de bonis tantum cælestibus intelligi, sed implenda esse post adventum Antichristi. et resurrectionem in terrena Jerusalem; prioribusque prophetiis, ex Isaia, Jeremia et Joannis Apocalypsi alias subjungit. 1218",
            target_raw="CAP. XXV.",
            target_seq=61,
            page_ref_raw="1218",
            normalized_target="Caput XXXV",
            note_raw="OCR literal in the ORDO is CAP. XXV.; the direct work heading on file 061 reads CAPUT XXXV.",
            raw_json={
                "source_file": physical_file(468),
                "source_literal_preserved": True,
                "direct_target_heading": "CAPUT XXXV.",
            },
        ),
        entry(
            section_key=ordo_key,
            order=237,
            entry_raw="CAP. XXXVI. — Homines vere suscitabuntur, mundusque non penitus exterminabitur : erunt autem variæ sanctorum mansiones pro cujusque dignitate, et omnia subjicientur Deo Patri : sicque erit omnia in omnibus. 1221",
            target_raw="CAP. XXXVI.",
            target_seq=63,
            page_ref_raw="1221",
            normalized_target="Caput XXXVI",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=238,
            entry_raw="Admonitio de sequentibus fragmentis. 1223",
            target_raw="Admonitio de sequentibus fragmentis",
            target_seq=64,
            page_ref_raw="1223",
            normalized_target="Admonitio de sequentibus fragmentis",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=239,
            entry_raw="FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI. 1223",
            target_raw="FRAGMENTA DEPERDITORUM OPERUM S. IRENÆI",
            target_seq=65,
            page_ref_raw="1223",
            normalized_target="Fragmenta deperditorum operum S. Irenaei",
            note_raw="The admonitio starts on file 064; the full work title is on file 065.",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=240,
            entry_raw="APPENDIX ad Irenæi libros contra hæreses, continens Gnosticorum quorum meminit S. martyr fragmenta.",
            target_raw="APPENDIX ad Irenæi libros contra hæreses",
            target_seq=84,
            page_ref_raw=None,
            normalized_target="Appendix ad Irenaei libros contra haereses",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=241,
            entry_raw="Fragmenta commentariorum Basilidis. 1263",
            target_raw="Fragmenta commentariorum Basilidis",
            target_seq=84,
            page_ref_raw="1263",
            normalized_target="Fragmenta commentariorum Basilidis",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=242,
            entry_raw="Fragmenta Epiphanii. 1265",
            target_raw="Fragmenta Epiphanii",
            target_seq=85,
            page_ref_raw="1265",
            normalized_target="Fragmenta Epiphanii",
            note_raw="Direct OCR heading reads FRAGMENTA EPIPHANIS.",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=243,
            entry_raw="Fragmentum Isidori. 1269",
            target_raw="Fragmentum Isidori",
            target_seq=87,
            page_ref_raw="1269",
            normalized_target="Fragmenta Isidori",
            note_raw="Direct OCR heading reads FRAGMENTA ISIDORI.",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=244,
            entry_raw="Fragmenta Valentini. 1271",
            target_raw="Fragmenta Valentini",
            target_seq=88,
            page_ref_raw="1271",
            normalized_target="Valentini fragmenta",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=245,
            entry_raw="Fragmentum libri cujusdam Valentiniani. 1277",
            target_raw="Fragmentum libri cujusdam Valentiniani",
            target_seq=91,
            page_ref_raw="1277",
            normalized_target="Fragmentum libri cujusdam Valentiniani",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=246,
            entry_raw="Epistola Ptolemæi ad Floram 1283",
            target_raw="Epistola Ptolemæi ad Floram",
            target_seq=101,
            page_ref_raw="1283",
            normalized_target="Ptolemaei ad Floram epistola",
            note_raw="Header digits on file 101 are CER-damaged, but the direct bilingual PTOLEMÆI AD FLORAM EPISTOLA title resolves the physical target.",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=247,
            entry_raw="Fragmenta Heracleonis. 1291",
            target_raw="Fragmenta Heracleonis",
            target_seq=106,
            page_ref_raw="1291",
            normalized_target="Heracleonis fragmenta",
            raw_json={"source_file": physical_file(468)},
        ),
        entry(
            section_key=ordo_key,
            order=248,
            entry_raw="PRÆFATIONES, PROLEGOMENA, NOTÆ ET OBSERVATIONES eorum omnium qui novas Irenæi editiones aut publicaverunt aut illustrarunt. 1321",
            target_raw="PRÆFATIONES, PROLEGOMENA, NOTÆ ET OBSERVATIONES",
            target_seq=121,
            page_ref_raw="1321",
            normalized_target="Praefationes, prolegomena, notae et observationes",
            raw_json={"source_file": physical_file(468)},
        ),
    ]
    ordo["entries"].extend(extra_ordo_entries)
    ordo["raw_json"].update(
        {
            "owned_entry_count": 248,
            "owned_entry_counts_by_start_file": {
                **ordo["raw_json"]["owned_entry_counts_by_start_file"],
                physical_file(468): 13,
            },
            "editorial_header_evidence": {
                **ordo["raw_json"]["editorial_header_evidence"],
                physical_file(468): "2015 ADDENDA 2016; the ORDO finishes above the ADDENDA heading on the shared sheet",
            },
            "final_extension_review": {
                "status": "verified",
                "reason": "The validated chunk stopped at file 467 ownership, but direct OCR shows thirteen remaining ORDO lines at the top of context file 468.",
                "excluded_after_ordo": [
                    "ADDENDA body beginning lower on file 468",
                    "INDEX SCRIPTORUM on files 469-470",
                ],
            },
            "local_target_policy": {
                "entries_1_198": "Targets before the part-II continuation at editorial column 1117 remain null unless a direct local title leaf exists.",
                "entry_53_exception": "Mapped to the direct part-II material title leaf on file 009.",
                "entries_199_248": "Mapped by direct local ANALYSIS/LIBER/CAPUT/work headings, not by numeric suffix inference.",
            },
        }
    )

    sections = [
        elenchus,
        prolegomena_section,
        grabe_section,
        ordo,
    ]

    notes = [
        f"Validated chunk provenance (before final reconciliation): {note}"
        for note in stable_notes
    ]
    notes.extend(
        [
            "Final reconciliation supersedes provisional fragment locators: direct OCR targets were substituted for ORDO/ELENCHUS self-hits.",
            "All 240 stable assembled entries are retained; thirteen ORDO lines from the top of physical file 468 were added, and eight work-front structure entries were added.",
            "The closing alphabetical INDEX RERUM ET SENTENTIARUM / INDEX RERUM pages (files 411-462) and INDEX SCRIPTORUM pages (files 469-470) are intentionally excluded from this opening/general-index payload.",
            "OCR literals, including ligatures, suspicious digits, CAP. XXV. on file 468, and the 1521-1522 header literal on file 121, are preserved with correction evidence in raw_json.",
            "Physical OCR suffixes are locators only; editorial columns and shared-sheet boundaries are stored separately.",
        ]
    )

    payload = {
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PATROLOGIÆ GRÆCÆ TOMUS VII. PARS SECUNDA.",
            "notes": [
                "ELENCHUS on physical file 010 inventories the whole tome VII, including material in pars prima.",
                "The extant part-II body continues book V around editorial column 1117, then contains Fragmenta, Appendix, and the editorial prolegomena/notes block.",
                "The general ORDO RERUM spans physical files 463-468 and editorial columns/pages 2005-2016; file 468 is shared with ADDENDA.",
            ],
            "scan_summary": {
                "file_count": 478,
                "validated_fragment_counts": assembled["counts"],
                "final_counts": {
                    "works": len(works),
                    "sections": len(sections),
                    "entries": sum(len(section["entries"]) for section in sections),
                },
                "duplicate_scan_range": "Files 092-099 duplicate Appendix columns already represented by files 084-091.",
            },
        },
        "works": works,
        "sections": sections,
        "notes": notes,
    }

    work_keys = [item["work_key"] for item in works]
    if len(work_keys) != len(set(work_keys)):
        raise RuntimeError("Duplicate work_key")
    section_keys = [item["section_key"] for item in sections]
    if len(section_keys) != len(set(section_keys)):
        raise RuntimeError("Duplicate section_key")
    known_work_keys = set(work_keys)
    dangling = [
        section["section_key"]
        for section in sections
        if section["work_key"] is not None
        and section["work_key"] not in known_work_keys
    ]
    if dangling:
        raise RuntimeError(f"Dangling section work_key values: {dangling}")
    if any("entries_summary" in section for section in sections):
        raise RuntimeError("entries_summary is forbidden")
    if any(
        section["index_kind"]
        in {
            "INDEX RERUM",
            "INDEX RERUM ET SENTENTIARUM",
            "INDEX SCRIPTORUM",
        }
        for section in sections
    ):
        raise RuntimeError("Closing alphabetical index leaked into general payload")
    for section in sections:
        orders = [item["entry_order"] for item in section["entries"]]
        if orders != list(range(1, len(orders) + 1)):
            raise RuntimeError(
                f"Non-contiguous entry_order in {section['section_key']}: "
                f"{orders[:4]}...{orders[-4:]}"
            )
        for item in section["entries"]:
            target = item.get("target_file")
            if target and not Path(target).is_file():
                raise RuntimeError(f"Missing target_file: {target}")
            if target and not Path(target).is_relative_to(SOURCE_ROOT):
                raise RuntimeError(f"Out-of-volume target_file: {target}")
    for item in works:
        if (
            item["start_page"] is not None
            and item["end_page"] is not None
            and item["start_page"] > item["end_page"]
        ):
            raise RuntimeError(f"Invalid work range: {item['work_key']}")
        for field in ("start_file", "end_file"):
            value = item[field]
            if value and not Path(value).is_relative_to(SOURCE_ROOT):
                raise RuntimeError(f"Out-of-volume {field}: {value}")
    pending_reruns = [
        item["work_key"]
        for item in works
        if isinstance(item.get("raw_json"), dict)
        and "work_anchor_rerun" in item["raw_json"]
    ]
    if pending_reruns:
        raise RuntimeError(f"Unprocessed work anchor reruns: {pending_reruns}")

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "written": str(OUTPUT),
                "works": len(works),
                "sections": len(sections),
                "entries": sum(len(section["entries"]) for section in sections),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

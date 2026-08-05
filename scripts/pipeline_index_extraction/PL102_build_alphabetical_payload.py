# Usage: python scripts/pipeline_index_extraction/PL102_build_alphabetical_payload.py
# Builds the PL102 alphabetical-index payload and writes it to the runtime output path.

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
OUT = ROOT / "data/alphabetical_index_payloads/PL102_alphabetical_indices.json"

SECTION_START = ROOT / "teste/PL102/text/9a177bbf-0564-4b58-905f-a07f11a198af-006.txt"
SUMMARIUM_FILE = ROOT / "teste/PL102/text/81200c3c-eff6-42f4-bf55-b13a7f128e6e-283.txt"
COMMENTARIA_FILE = ROOT / "teste/PL102/text/c9c41935-8aa8-4f6d-a132-2474162f0fa3-347.txt"
VIA_REGIA_FILE = ROOT / "teste/PL102/text/aa155960-8f1b-443b-a2b1-97db83d924d8-468.txt"
ACTA_FILE = ROOT / "teste/PL102/text/f0f7530f-9ee8-482d-9705-0bef7c667c06-488.txt"
APPENDIX_FILE = ROOT / "teste/PL102/text/f0f7530f-9ee8-482d-9705-0bef7c667c06-490.txt"
LEO_EPISTOLAE_FILE = ROOT / "teste/PL102/text/f0f7530f-9ee8-482d-9705-0bef7c667c06-514.txt"
MAGNUS_FILE = ROOT / "teste/PL102/text/f0f7530f-9ee8-482d-9705-0bef7c667c06-493.txt"
MAGNUS_JURIS_FILE = ROOT / "teste/PL102/text/f0f7530f-9ee8-482d-9705-0bef7c667c06-494.txt"
LEO_PRIVILEGIA_FILE = ROOT / "teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-537.txt"
STEPHANUS_FILE = ROOT / "teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-538.txt"
PASCHALIS_FILE = ROOT / "teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-545.txt"
REMIGIUS_FILE = ROOT / "teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-549.txt"
COLLECTIONES_FILE = ROOT / "teste/PL102/text/9a177bbf-0564-4b58-905f-a07f11a198af-010.txt"


def p(path: Path) -> str:
    return str(path)


def make_entry(
    entry_key: str,
    section_key: str,
    parent_node_key: str | None,
    entry_order: int,
    entry_kind: str,
    lemma_raw: str | None,
    lemma_display: str | None,
    lemma_norm: str | None,
    lemma_sort: str | None,
    entry_raw: str,
    context_raw: str,
    inferred_printed_page: int | None,
    section_start_file: str,
    editorial_anchor_file: str,
    target_file_best: str | None,
    confidence: float,
    raw_json: dict,
) -> dict:
    return {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_display,
        "lemma_norm": lemma_norm,
        "lemma_sort": lemma_sort,
        "entry_raw": entry_raw,
        "context_raw": context_raw,
        "heading_letter": None,
        "inferred_printed_page": inferred_printed_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_file_best,
        "confidence": confidence,
        "raw_json": raw_json,
    }


def make_ref(
    entry_key: str,
    ref_order: int,
    ref_kind: str,
    ref_raw: str,
    page_ref_raw: str,
    page_ref_int: int,
    target_file: str,
    target_file_probability: float,
    section_start_file: str,
    editorial_anchor_file: str,
    confidence: float,
    raw_json: dict,
) -> dict:
    return {
        "entry_key": entry_key,
        "ref_order": ref_order,
        "ref_kind": ref_kind,
        "ref_raw": ref_raw,
        "page_ref_raw": page_ref_raw,
        "page_ref_int": page_ref_int,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": None,
        "range_end_raw": None,
        "target_file": target_file,
        "target_file_probability": target_file_probability,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "confidence": confidence,
        "raw_json": raw_json,
    }


section_key = "PL102:alpha:author_index:001"
section_file = p(SECTION_START)
index_section = {
    "section_key": section_key,
    "volume_id": "PL102",
    "work_key": None,
    "section_order": 1,
    "section_kind": "author_index",
    "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CII CONTINENTUR",
    "heading_norm": "elenchus auctorum et operum qui in hoc tomo cii continentur",
    "heading_letter": None,
    "page_start": None,
    "page_end": None,
    "file_start": section_file,
    "file_end": section_file,
    "confidence": 0.99,
    "raw_json": {
        "source_file": section_file,
        "section_kind_reason": "Front-matter ELENCHUS of authors and works in the tomo; not a subject index or ordo rerum block.",
        "observed_heading_lines": [
            "ELENCHUS",
            "AUCTORUM ET OPERUM QUI IN HOC TOMO CII CONTINENTUR"
        ]
    }
}

nodes = [
    {
        "node_key": "PL102:node:smaragdus",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 1,
        "node_kind": "heading_group",
        "label_raw": "SMARAGDUS ABBAS.",
        "label_norm": "smaragdus abbas",
        "label_sort": "smaragdus abbas",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:appendix",
        "section_key": section_key,
        "parent_node_key": "PL102:node:smaragdus",
        "node_order": 2,
        "node_kind": "heading_group",
        "label_raw": "APPENDIX AD SMARAGDI OPERA.",
        "label_norm": "appendix ad smaragdi opera",
        "label_sort": "appendix ad smaragdi opera",
        "node_level": 2,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:magnus",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 3,
        "node_kind": "heading_group",
        "label_raw": "MAGNUS SENONENSIS ARCHIEPISCOPUS.",
        "label_norm": "magnus senonensis archiepiscopus",
        "label_sort": "magnus senonensis archiepiscopus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:leo",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 4,
        "node_kind": "heading_group",
        "label_raw": "SANCTUS LEO, III PONTIFEX ROMANUS.",
        "label_norm": "sanctus leo iii pontifex romanus",
        "label_sort": "sanctus leo iii pontifex romanus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:stephanus",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 5,
        "node_kind": "heading_group",
        "label_raw": "TEPHANUS IV, PONTIFEX ROMANUS.",
        "label_norm": "stephanus iv pontifex romanus",
        "label_sort": "stephanus iv pontifex romanus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:paschalis",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 6,
        "node_kind": "heading_group",
        "label_raw": "PASCHALIS I, PONTIFEX ROMANUS.",
        "label_norm": "paschalis i pontifex romanus",
        "label_sort": "paschalis i pontifex romanus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
    {
        "node_key": "PL102:node:remigius",
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": 7,
        "node_kind": "heading_group",
        "label_raw": "REMIGIUS CURIENSIS EPISCOPUS.",
        "label_norm": "remigius curiensis episcopus",
        "label_sort": "remigius curiensis episcopus",
        "node_level": 1,
        "confidence": 0.99,
        "raw_json": {"source_file": section_file},
    },
]

entries = []
refs = []

entries.append(
    make_entry(
        "PL102:entry:0001",
        section_key,
        "PL102:node:smaragdus",
        1,
        "lemma",
        "a Collectiones in Epistolas et Evangelia",
        "Collectiones in Epistolas et Evangelia",
        "collectiones in epistolas et evangelia",
        "collectiones in epistolas et evangelia",
        "a Collectiones in Epistolas et Evangelia. Col. 15",
        "a Collectiones in Epistolas et Evangelia. Col. 15",
        15,
        section_file,
        section_file,
        p(COLLECTIONES_FILE),
        0.68,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_collectiones_015",
            "helper_status": "resolved",
            "helper_best_file": section_file,
            "helper_best_probability": 0.516913,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=7; page_hints=[15]; strong=body_name_match,body_context_exact,index_page_penalty",
            "resolution_note": "Kept conservative because the OCR line is a column citation on the index page itself."
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0001",
        1,
        "editorial_column",
        "Col. 15",
        "15",
        15,
        p(COLLECTIONES_FILE),
        0.516913,
        section_file,
        section_file,
        0.68,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_collectiones_015",
            "helper_status": "resolved",
            "helper_best_file": section_file,
            "helper_best_probability": 0.516913,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=7; page_hints=[15]; strong=body_name_match,body_context_exact,index_page_penalty",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0002",
        section_key,
        "PL102:node:smaragdus",
        2,
        "lemma",
        "Summarium in Epistolas et Evangelia Smaragdo additum",
        "Summarium in Epistolas et Evangelia Smaragdo additum",
        "summarium in epistolas et evangelia smaragdo additum",
        "summarium in epistolas et evangelia smaragdo additum",
        "Summarium in Epistolas et Evangelia Smaragdo additum. 553",
        "Summarium in Epistolas et Evangelia Smaragdo additum. 553",
        553,
        section_file,
        section_file,
        p(SUMMARIUM_FILE),
        0.62,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_summarium_553",
            "helper_status": "ambiguous",
            "helper_best_file": p(SUMMARIUM_FILE),
            "helper_best_probability": 0.376943,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=561; page_hints=[553]; strong=header_name_match",
            "resolution_note": "The helper favored a later body anchor; direct OCR on file 279 preserves the exact summarium heading, so the ambiguity is kept explicit."
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0002",
        1,
        "editorial_page",
        "553",
        "553",
        553,
        p(SUMMARIUM_FILE),
        0.376943,
        section_file,
        section_file,
        0.62,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_summarium_553",
            "helper_status": "ambiguous",
            "helper_best_file": p(SUMMARIUM_FILE),
            "helper_best_probability": 0.376943,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=561; page_hints=[553]; strong=header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0003",
        section_key,
        "PL102:node:smaragdus",
        3,
        "lemma",
        "Commentaria in Regulam sancti Benedicti",
        "Commentaria in Regulam sancti Benedicti",
        "commentaria in regulam sancti benedicti",
        "commentaria in regulam sancti benedicti",
        "Commentaria in Regulam sancti Benedicti. 691",
        "Commentaria in Regulam sancti Benedicti. 691",
        691,
        section_file,
        section_file,
        p(COMMENTARIA_FILE),
        0.77,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_commentaria_benedicti_691",
            "helper_status": "resolved",
            "helper_best_file": p(COMMENTARIA_FILE),
            "helper_best_probability": 0.765016,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=246; page_hints=[691]; strong=header_name_match,header_name_match,physical_index_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0003",
        1,
        "editorial_page",
        "691",
        "691",
        691,
        p(COMMENTARIA_FILE),
        0.765016,
        section_file,
        section_file,
        0.77,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_commentaria_benedicti_691",
            "helper_status": "resolved",
            "helper_best_file": p(COMMENTARIA_FILE),
            "helper_best_probability": 0.765016,
            "helper_candidate_role": "index_page_candidate",
            "helper_reason_summary": "index_page_candidate; inferred_page=246; page_hints=[691]; strong=header_name_match,header_name_match,physical_index_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0004",
        section_key,
        "PL102:node:smaragdus",
        4,
        "lemma",
        "Via regia",
        "Via regia",
        "via regia",
        "via regia",
        "Via regia. 931",
        "Via regia. 931",
        931,
        section_file,
        section_file,
        p(VIA_REGIA_FILE),
        0.9999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_via_regia_931",
            "helper_status": "resolved",
            "helper_best_file": p(VIA_REGIA_FILE),
            "helper_best_probability": 0.999883,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=931; page_hints=[931]; strong=header_name_match,body_name_match,header_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0004",
        1,
        "editorial_page",
        "931",
        "931",
        931,
        p(VIA_REGIA_FILE),
        0.999883,
        section_file,
        section_file,
        0.9999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_via_regia_931",
            "helper_status": "resolved",
            "helper_best_file": p(VIA_REGIA_FILE),
            "helper_best_probability": 0.999883,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=931; page_hints=[931]; strong=header_name_match,body_name_match,header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0005",
        section_key,
        "PL102:node:smaragdus",
        5,
        "lemma",
        "Acta collationis Romanæ a Smaragdo descripta",
        "Acta collationis Romanæ a Smaragdo descripta",
        "acta collationis romanae a smaragdo descripta",
        "acta collationis romanae a smaragdo descripta",
        "Acta collationis Romanæ a Smaragdo descripta. 971",
        "Acta collationis Romanæ a Smaragdo descripta. 971",
        971,
        section_file,
        section_file,
        p(ACTA_FILE),
        0.9979,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_acta_collationis_971",
            "helper_status": "resolved",
            "helper_best_file": p(ACTA_FILE),
            "helper_best_probability": 0.9979,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=971; page_hints=[971]; strong=body_name_match,body_context_partial,inferred_page_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0005",
        1,
        "editorial_page",
        "971",
        "971",
        971,
        p(ACTA_FILE),
        0.9979,
        section_file,
        section_file,
        0.9979,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_acta_collationis_971",
            "helper_status": "resolved",
            "helper_best_file": p(ACTA_FILE),
            "helper_best_probability": 0.9979,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=971; page_hints=[971]; strong=body_name_match,body_context_partial,inferred_page_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0006",
        section_key,
        "PL102:node:appendix",
        6,
        "lemma",
        "Chartæ Ludovici Pii et Lotharii filii ejus pro monasterio Sancti Michaelis",
        "Chartæ Ludovici Pii et Lotharii filii ejus pro monasterio Sancti Michaelis",
        "chartae ludovici pii et lotharii filii ejus pro monasterio sancti michaelis",
        "chartae ludovici pii et lotharii filii ejus pro monasterio sancti michaelis",
        "1. Chartæ Ludovici Pii et Lotharii filii ejus pro monasterio Sancti Michaelis. 975",
        "1. Chartæ Ludovici Pii et Lotharii filii ejus pro monasterio Sancti Michaelis. 975",
        975,
        section_file,
        section_file,
        p(APPENDIX_FILE),
        0.805776,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_chartae_975",
            "helper_status": "resolved",
            "helper_best_file": p(APPENDIX_FILE),
            "helper_best_probability": 0.805776,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=975; page_hints=[975]; strong=body_name_match,body_name_match,body_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0006",
        1,
        "editorial_page",
        "975",
        "975",
        975,
        p(APPENDIX_FILE),
        0.805776,
        section_file,
        section_file,
        0.805776,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_chartae_975",
            "helper_status": "resolved",
            "helper_best_file": p(APPENDIX_FILE),
            "helper_best_probability": 0.805776,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=975; page_hints=[975]; strong=body_name_match,body_name_match,body_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0007",
        section_key,
        "PL102:node:appendix",
        7,
        "lemma",
        "Epistola Caroli Magni ad Leonem papam, de processione Spiritus sancti, quam edidit Smaragdus abbas",
        "Epistola Caroli Magni ad Leonem papam, de processione Spiritus sancti, quam edidit Smaragdus abbas",
        "epistola caroli magni ad leonem papam de processione spiritus sancti quam edidit smaragdus abbas",
        "epistola caroli magni ad leonem papam de processione spiritus sancti quam edidit smaragdus abbas",
        "2. Epistola Caroli Magni ad Leonem papam, de processione Spiritus sancti, quam edidit Smaragdus abbas. 979",
        "2. Epistola Caroli Magni ad Leonem papam, de processione Spiritus sancti, quam edidit Smaragdus abbas. 979",
        979,
        section_file,
        section_file,
        p(APPENDIX_FILE),
        1.0,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_epistola_caroli_979",
            "helper_status": "resolved",
            "helper_best_file": p(APPENDIX_FILE),
            "helper_best_probability": 1.0,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=979; page_hints=[979]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0007",
        1,
        "editorial_page",
        "979",
        "979",
        979,
        p(APPENDIX_FILE),
        1.0,
        section_file,
        section_file,
        1.0,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_smaragdi_epistola_caroli_979",
            "helper_status": "resolved",
            "helper_best_file": p(APPENDIX_FILE),
            "helper_best_probability": 1.0,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=979; page_hints=[979]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0008",
        section_key,
        "PL102:node:magnus",
        8,
        "lemma",
        "Libellus de Mysterio baptismatis",
        "Libellus de Mysterio baptismatis",
        "libellus de mysterio baptismatis",
        "libellus de mysterio baptismatis",
        "Libellus de Mysterio baptismatis. 981",
        "Libellus de Mysterio baptismatis. 981",
        981,
        section_file,
        section_file,
        p(MAGNUS_FILE),
        0.993483,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_magnus_libellus_baptismatis_981",
            "helper_status": "resolved",
            "helper_best_file": p(MAGNUS_FILE),
            "helper_best_probability": 0.993483,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=981; page_hints=[981]; strong=body_name_match,body_name_match,inferred_page_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0008",
        1,
        "editorial_page",
        "981",
        "981",
        981,
        p(MAGNUS_FILE),
        0.993483,
        section_file,
        section_file,
        0.993483,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_magnus_libellus_baptismatis_981",
            "helper_status": "resolved",
            "helper_best_file": p(MAGNUS_FILE),
            "helper_best_probability": 0.993483,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=981; page_hints=[981]; strong=body_name_match,body_name_match,inferred_page_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0009",
        section_key,
        "PL102:node:magnus",
        9,
        "lemma",
        "Notæ juris a Magnone collectæ",
        "Notæ juris a Magnone collectæ",
        "notae juris a magnone collectae",
        "notae juris a magnone collectae",
        "Notæ juris a Magnone collectæ. 983",
        "Notæ juris a Magnone collectæ. 983",
        983,
        section_file,
        section_file,
        p(MAGNUS_JURIS_FILE),
        0.99999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_magnus_notae_juris_983",
            "helper_status": "resolved",
            "helper_best_file": p(MAGNUS_JURIS_FILE),
            "helper_best_probability": 0.99999,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=982; page_hints=[983]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0009",
        1,
        "editorial_page",
        "983",
        "983",
        983,
        p(MAGNUS_JURIS_FILE),
        0.99999,
        section_file,
        section_file,
        0.99999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_magnus_notae_juris_983",
            "helper_status": "resolved",
            "helper_best_file": p(MAGNUS_JURIS_FILE),
            "helper_best_probability": 0.99999,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=982; page_hints=[983]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0010",
        section_key,
        "PL102:node:leo",
        10,
        "lemma",
        "Epistolæ",
        "Epistolæ",
        "epistolae",
        "epistolae",
        "Epistolæ. 1023",
        "Epistolæ. 1023",
        1023,
        section_file,
        section_file,
        p(LEO_EPISTOLAE_FILE),
        0.999266,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_leo_epistolae_1023",
            "helper_status": "resolved",
            "helper_best_file": p(LEO_EPISTOLAE_FILE),
            "helper_best_probability": 0.999266,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1023; page_hints=[1023]; strong=header_name_match,header_name_match,inferred_page_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0010",
        1,
        "editorial_page",
        "1023",
        "1023",
        1023,
        p(LEO_EPISTOLAE_FILE),
        0.999266,
        section_file,
        section_file,
        0.999266,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_leo_epistolae_1023",
            "helper_status": "resolved",
            "helper_best_file": p(LEO_EPISTOLAE_FILE),
            "helper_best_probability": 0.999266,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1023; page_hints=[1023]; strong=header_name_match,header_name_match,inferred_page_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0011",
        section_key,
        "PL102:node:leo",
        11,
        "lemma",
        "Privilegia",
        "Privilegia",
        "privilegia",
        "privilegia",
        "Privilegia. 1067",
        "Privilegia. 1067",
        1067,
        section_file,
        section_file,
        p(LEO_PRIVILEGIA_FILE),
        0.721284,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_leo_privilegia_1067",
            "helper_status": "resolved",
            "helper_best_file": p(LEO_PRIVILEGIA_FILE),
            "helper_best_probability": 0.721284,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1069; page_hints=[1067]; strong=header_name_match,physical_index_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0011",
        1,
        "editorial_page",
        "1067",
        "1067",
        1067,
        p(LEO_PRIVILEGIA_FILE),
        0.721284,
        section_file,
        section_file,
        0.721284,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_leo_privilegia_1067",
            "helper_status": "resolved",
            "helper_best_file": p(LEO_PRIVILEGIA_FILE),
            "helper_best_probability": 0.721284,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1069; page_hints=[1067]; strong=header_name_match,physical_index_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0012",
        section_key,
        "PL102:node:stephanus",
        12,
        "lemma",
        "Notitia historica",
        "Notitia historica",
        "notitia historica",
        "notitia historica",
        "Notitia historica. 1071",
        "Notitia historica. 1071",
        1071,
        section_file,
        section_file,
        p(STEPHANUS_FILE),
        1.0,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_stephanus_notitia_historica_1071",
            "helper_status": "resolved",
            "helper_best_file": p(STEPHANUS_FILE),
            "helper_best_probability": 1.0,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1071; page_hints=[1071]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0012",
        1,
        "editorial_page",
        "1071",
        "1071",
        1071,
        p(STEPHANUS_FILE),
        1.0,
        section_file,
        section_file,
        1.0,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_stephanus_notitia_historica_1071",
            "helper_status": "resolved",
            "helper_best_file": p(STEPHANUS_FILE),
            "helper_best_probability": 1.0,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1071; page_hints=[1071]; strong=header_name_match,header_name_match,header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0013",
        section_key,
        "PL102:node:paschalis",
        13,
        "lemma",
        "Epistolæ",
        "Epistolæ",
        "epistolae",
        "epistolae",
        "Epistolæ. 1085",
        "Epistolæ. 1085",
        1085,
        section_file,
        section_file,
        p(PASCHALIS_FILE),
        0.999999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_paschalis_epistolae_1085",
            "helper_status": "resolved",
            "helper_best_file": p(PASCHALIS_FILE),
            "helper_best_probability": 0.999999,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1086; page_hints=[1085]; strong=header_name_match,body_name_match,header_name_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0013",
        1,
        "editorial_page",
        "1085",
        "1085",
        1085,
        p(PASCHALIS_FILE),
        0.999999,
        section_file,
        section_file,
        0.999999,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_paschalis_epistolae_1085",
            "helper_status": "resolved",
            "helper_best_file": p(PASCHALIS_FILE),
            "helper_best_probability": 0.999999,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1086; page_hints=[1085]; strong=header_name_match,body_name_match,header_name_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0014",
        section_key,
        "PL102:node:remigius",
        14,
        "lemma",
        "Canones pro sua diœcesi",
        "Canones pro sua diœcesi",
        "canones pro sua diocesi",
        "canones pro sua diocesi",
        "Canones pro sua diœcesi. 1093",
        "Canones pro sua diœcesi. 1093",
        1093,
        section_file,
        section_file,
        p(REMIGIUS_FILE),
        0.999995,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_remigius_canones_1093",
            "helper_status": "resolved",
            "helper_best_file": p(REMIGIUS_FILE),
            "helper_best_probability": 0.999995,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1093; page_hints=[1093]; strong=header_name_match,header_name_match,inferred_page_match",
        },
    )
)
refs.append(
    make_ref(
        "PL102:entry:0014",
        1,
        "editorial_page",
        "1093",
        "1093",
        1093,
        p(REMIGIUS_FILE),
        0.999995,
        section_file,
        section_file,
        0.999995,
        {
            "source_file": section_file,
            "helper_entry_id": "pl102_remigius_canones_1093",
            "helper_status": "resolved",
            "helper_best_file": p(REMIGIUS_FILE),
            "helper_best_probability": 0.999995,
            "helper_candidate_role": "target_candidate",
            "helper_reason_summary": "target_candidate; inferred_page=1093; page_hints=[1093]; strong=header_name_match,header_name_match,inferred_page_match",
        },
    )
)

entries.append(
    make_entry(
        "PL102:entry:0015",
        section_key,
        None,
        15,
        "editorial_note",
        None,
        None,
        None,
        None,
        "R. PITRA. — NOTÆ IN COMMENTARIUM SMARAGDI.",
        "R. PITRA. — NOTÆ IN COMMENTARIUM SMARAGDI.",
        None,
        section_file,
        section_file,
        section_file,
        0.9,
        {
            "source_file": section_file,
            "note": "No material locator in the OCR line; preserved as an editorial note attached to the ELENCHUS."
        },
    )
)

payload = {
    "schema_version": 1,
    "generated_at": "2026-07-20T00:00:00-03:00",
    "volume": {
        "volume_id": "PL102",
        "collection": "PL",
        "source_root": "/homessddata/Projects/pdfocr/teste/PL102/text",
        "volume_label": "Patrologia Latina 102",
        "notes": [
            "The front-matter ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CII CONTINENTUR. is treated as the alphabetical author/work index.",
            "The tail ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. was inspected but excluded from the alphabetical payload as editorial contents material.",
            "OCR literals are preserved, including Col. 15, Epistolæ, Cæciliæ, and diœcesi."
        ],
    },
    "sections": [index_section],
    "nodes": nodes,
    "entries": entries,
    "refs": refs,
    "scripture_refs": [],
    "coverage": {
        "entries_status": "complete",
        "entries_status_reason": "The ELENCHUS author/work lines were recoverable from OCR and the helper resolved the cited page anchors for all indexed works; one trailing editorial note line was preserved without a material locator.",
        "evidence_files": [
            section_file,
            p(SUMMARIUM_FILE),
            p(COMMENTARIA_FILE),
            p(VIA_REGIA_FILE),
            p(ACTA_FILE),
            p(APPENDIX_FILE),
            p(MAGNUS_FILE),
            p(MAGNUS_JURIS_FILE),
            p(LEO_EPISTOLAE_FILE),
            p(LEO_PRIVILEGIA_FILE),
            p(STEPHANUS_FILE),
            p(PASCHALIS_FILE),
            p(REMIGIUS_FILE)
        ],
    },
    "notes": [
        "The helper was conservative on Collectiones in Epistolas et Evangelia and Summarium in Epistolas et Evangelia Smaragdo additum; both entries preserve the ambiguity in raw_json while keeping the OCR literal.",
        "ORDO RERUM was inspected in the tail OCR window, but it is editorial contents material and was not serialized into sections for this alphabetical payload.",
    ],
}

OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


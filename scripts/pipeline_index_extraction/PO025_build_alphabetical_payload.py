#!/usr/bin/env python3
"""Build and repair the PO025 alphabetical-index payload.

Usage:
  python scripts/pipeline_index_extraction/PO025_build_alphabetical_payload.py --write-helper
  python scripts/index_target_locator.py --input data/alphabetical_index_payloads/PO025_helper_request.json --output data/alphabetical_index_payloads/PO025_helper_output.json --pretty
  python scripts/pipeline_index_extraction/PO025_build_alphabetical_payload.py --assemble
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO025"
COLLECTION = "PO"
SOURCE_ROOT = ROOT / "teste/PO025/text"
OUT = ROOT / "data/alphabetical_index_payloads/PO025_alphabetical_indices.json"
HELPER_IN = ROOT / "data/alphabetical_index_payloads/PO025_helper_request.json"
HELPER_OUT = ROOT / "data/alphabetical_index_payloads/PO025_helper_output.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PO025"
TODO = INTERMEDIATE / "todo.json"


def f(name: str) -> str:
    return str(SOURCE_ROOT / name)


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s'ܐ-ܿ\u0370-\u03ff-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().lower()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def by_suffix(suffix: int) -> str | None:
    matches = sorted(SOURCE_ROOT.glob(f"*-{suffix:03d}.txt"))
    return str(matches[0]) if len(matches) == 1 else None


def bracket_page_file(page: int) -> str | None:
    matches = []
    pattern = f"[{page}]"
    for path in SOURCE_ROOT.glob("*.txt"):
        try:
            if pattern in path.read_text(encoding="utf-8", errors="ignore"):
                matches.append(path)
        except OSError:
            continue
    if len(matches) == 1:
        return str(matches[0])
    return None


def printed_page_file(page: int) -> str | None:
    direct = bracket_page_file(page)
    if direct:
        return direct
    if 450 <= page <= 607:
        return by_suffix(page - 434)
    return None


def section(
    key: str,
    order: int,
    kind: str,
    heading: str,
    start: int | None,
    end: int | None,
    file_start: str,
    file_end: str,
    confidence: float,
    note: str,
) -> dict:
    return {
        "section_key": key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": order,
        "section_kind": kind,
        "heading_raw": heading,
        "heading_norm": norm(heading),
        "heading_letter": None,
        "page_start": start,
        "page_end": end,
        "file_start": file_start,
        "file_end": file_end,
        "confidence": confidence,
        "raw_json": {
            "source": note,
            "section_title_tokens": [heading],
            "verified_with": "scripts/read_ocr_page_text.py --view xml --show-source",
        },
    }


SECTIONS = [
    section("PO025:alpha:onomastic_mixed:001", 1, "onomastic_mixed", "TABLE DES NOMS PROPRES SYRIAQUES", 164, 166, f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-174.txt"), f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-176.txt"), 0.98, "OCR files 174-176; first Syriac proper-names table."),
    section("PO025:alpha:foreign_terms:002", 2, "foreign_terms", "TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES", 167, 170, f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-177.txt"), f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-180.txt"), 0.94, "OCR files 177-180; first Syriac foreign/remarkable-words table. File 180 header OCR reads 470 but continues page 170."),
    section("PO025:alpha:foreign_terms:003", 3, "foreign_terms", "TABLE DES MOTS GRECS CITÉS DANS LES MSS.", 171, 171, f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-181.txt"), f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-181.txt"), 0.98, "OCR file 181; compact Greek words cited in manuscripts table."),
    section("PO025:alpha:scripture_index:004", 4, "scripture_index", "TABLE DES CITATIONS DE LA BIBLE", 172, 173, f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-182.txt"), f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-183.txt"), 0.99, "OCR files 182-183; first Bible-citation table."),
    section("PO025:alpha:author_index:005", 5, "author_index", "CITATION DES PÈRES DE L'ÉGLISE", 174, 174, f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-184.txt"), f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-184.txt"), 0.9, "OCR file 184; one patristic citation line before table of contents."),
    section("PO025:alpha:pericope_index:006", 6, "pericope_index", "TABLE DES PÉRICOPES DE L'ÉCRITURE", 471, 474, f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-481.txt"), f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-484.txt"), 0.98, "OCR files 481-484; pericope table continues through file 484."),
    section("PO025:alpha:concordance_index:007", 7, "concordance_index", "TABLE DE CONCORDANCE", 476, 485, f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-485.txt"), f("83a93f37-3209-4450-8b8f-76c23201010d-495.txt"), 0.96, "OCR files 485-495; file 485 is sigla/abbreviations and files 486-495 carry concordance entries."),
    section("PO025:alpha:scripture_index:008", 8, "scripture_index", "INDEX DES CITATIONS DES ÉCRITURES", 612, 613, f("e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt"), f("e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-623.txt"), 0.97, "OCR files 622-623; Euchologium scripture-citation index."),
    section("PO025:alpha:onomastic_mixed:009", 9, "onomastic_mixed", "TABLE DES NOMS PROPRES SYRIAQUES", 804, 806, f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-816.txt"), f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-818.txt"), 0.97, "OCR files 816-818; later Syriac proper-names table."),
    section("PO025:alpha:foreign_terms:010", 10, "foreign_terms", "TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES", 807, 810, f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-819.txt"), f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-822.txt"), 0.95, "OCR files 819-822; later Syriac foreign/remarkable-words table."),
    section("PO025:alpha:foreign_terms:011", 11, "foreign_terms", "TABLE DES MOTS GRECS CITÉS DANS LES MSS.", 811, 811, f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-823.txt"), f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-823.txt"), 0.98, "OCR file 823; later Greek words cited in manuscripts table."),
    section("PO025:alpha:scripture_index:012", 12, "scripture_index", "TABLE DES CITATIONS DE LA BIBLE.", 812, 814, f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-824.txt"), f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt"), 0.96, "OCR file 824 is the real first page of the later Bible-citation table; file 826 contains its ending before the patristic table."),
    section("PO025:alpha:author_index:013", 13, "author_index", "TABLE DES CITATIONS DES PÈRES DE L'ÉGLISE", 814, 814, f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt"), f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt"), 0.94, "Lower block of OCR file 826; later patristic citations table."),
]


NODE_ROWS = [
    ("PO025:node:001", "PO025:alpha:onomastic_mixed:001", 1, "letter_group", "ܐ", 1),
    ("PO025:node:002", "PO025:alpha:onomastic_mixed:001", 2, "letter_group", "ܡ", 1),
    ("PO025:node:003", "PO025:alpha:foreign_terms:002", 1, "letter_group", "ܐ", 1),
    ("PO025:node:004", "PO025:alpha:foreign_terms:002", 2, "letter_group", "ܚ", 1),
    ("PO025:node:005", "PO025:alpha:foreign_terms:003", 1, "letter_group", "A", 1),
    ("PO025:node:006", "PO025:alpha:scripture_index:004", 1, "heading_group", "ANCIEN TESTAMENT", 1),
    ("PO025:node:007", "PO025:alpha:scripture_index:004", 2, "heading_group", "NOUVEAU TESTAMENT", 1),
    ("PO025:node:008", "PO025:alpha:pericope_index:006", 1, "heading_group", "Genèse", 1),
    ("PO025:node:009", "PO025:alpha:pericope_index:006", 2, "heading_group", "Exode", 1),
    ("PO025:node:010", "PO025:alpha:concordance_index:007", 1, "heading_group", "SIGLES BIBLIOTHÈQUES DATES", 1),
    ("PO025:node:011", "PO025:alpha:concordance_index:007", 2, "rubric_group", "Dimanche des Rameaux", 1),
    ("PO025:node:012", "PO025:alpha:scripture_index:008", 1, "heading_group", "GENESE.", 1),
    ("PO025:node:013", "PO025:alpha:scripture_index:008", 2, "heading_group", "PSAUMES.", 1),
    ("PO025:node:014", "PO025:alpha:onomastic_mixed:009", 1, "letter_group", "ܐ", 1),
    ("PO025:node:015", "PO025:alpha:onomastic_mixed:009", 2, "letter_group", "ܡ", 1),
    ("PO025:node:016", "PO025:alpha:foreign_terms:010", 1, "letter_group", "ܐ", 1),
    ("PO025:node:017", "PO025:alpha:foreign_terms:010", 2, "letter_group", "ܗ", 1),
    ("PO025:node:018", "PO025:alpha:foreign_terms:011", 1, "letter_group", "A", 1),
    ("PO025:node:019", "PO025:alpha:scripture_index:012", 1, "heading_group", "GENESE", 1),
    ("PO025:node:020", "PO025:alpha:scripture_index:012", 2, "heading_group", "HEBREUX", 1),
]


def node(row: tuple[str, str, int, str, str, int]) -> dict:
    key, section_key, order, kind, label, level = row
    return {
        "node_key": key,
        "section_key": section_key,
        "parent_node_key": None,
        "node_order": order,
        "node_kind": kind,
        "label_raw": label,
        "label_norm": norm(label),
        "label_sort": norm(label),
        "node_level": level,
        "confidence": 0.9,
        "raw_json": {"source": "Recovered from visible heading/group label in OCR."},
    }


ENTRY_SPECS = [
    {
        "key": "PO025:entry:0001",
        "section": "PO025:alpha:onomastic_mixed:001",
        "node": "PO025:node:001",
        "order": 1,
        "kind": "lemma",
        "lemma": "ܐܕ",
        "raw": "ܐܕ 450_3-5 471_7 482_13 489_15 494_1-3-5 497_5 518_6 519_8 535_7 557_11 564_4 564_12 566_1 598_8 601_2 602_9 603_2-11 607_7",
        "letter": "ܐ",
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-174.txt"),
        "refs": ["450_3-5", "471_7", "482_13", "489_15", "494_1-3-5", "497_5", "518_6", "519_8", "535_7", "557_11", "564_4", "564_12", "566_1", "598_8", "601_2", "602_9", "603_2-11", "607_7"],
        "note": "First clear line of the first Syriac proper-names table; refs resolved by bracket-page/OCR suffix map page N -> suffix N-434 after local rg checks.",
        "prefer_page_map": True,
    },
    {
        "key": "PO025:entry:0002",
        "section": "PO025:alpha:onomastic_mixed:001",
        "node": "PO025:node:002",
        "order": 2,
        "kind": "lemma",
        "lemma": "ܡܪܝܡ Vierge",
        "raw": "ܡܪܝܡ Vierge 503₅ 510₂₋₃ 511₁₋₂₋₁₅ 512₉ 513₁ 515₂ 520₁ 522₁₁ 532₇ 554₂ 563₁₃₋₁₅ 584₁₀",
        "letter": "ܡ",
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-176.txt"),
        "refs": ["503₅", "510₂₋₃", "511₁₋₂₋₁₅", "512₉", "513₁", "515₂", "520₁", "522₁₁", "532₇", "554₂", "563₁₃₋₁₅", "584₁₀"],
        "note": "Verified in OCR file 176; previous trailing hyphen artifact after 510 was removed.",
    },
    {
        "key": "PO025:entry:0003",
        "section": "PO025:alpha:foreign_terms:002",
        "node": "PO025:node:004",
        "order": 1,
        "kind": "lemma",
        "lemma": "ܚܓܠ ܙܘܗܪܐ",
        "raw": "ܚܓܠ ܙܘܗܪܐ 450₃ 482₁₂₋₁₃₋₁₄ 489₁₅–490₁ 498₁₂ 509₆ 603₂",
        "letter": "ܚ",
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-180.txt"),
        "refs": ["450₃", "482₁₂₋₁₃₋₁₄", "489₁₅–490₁", "498₁₂", "509₆", "603₂"],
        "note": "Representative clear entry from the first Syriac foreign-terms section; page 490 in the printed range maps to the same local homily window.",
        "prefer_page_map": True,
    },
    {
        "key": "PO025:entry:0004",
        "section": "PO025:alpha:foreign_terms:003",
        "node": "PO025:node:005",
        "order": 1,
        "kind": "lemma",
        "lemma": "ἀγγιστεία",
        "raw": "ἀγγιστεία 525 n. 1",
        "letter": "A",
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-181.txt"),
        "refs": ["525 n. 1"],
        "note": "First clear entry in the first Greek-words table.",
    },
    {
        "key": "PO025:entry:0005",
        "section": "PO025:alpha:scripture_index:004",
        "node": "PO025:node:006",
        "order": 1,
        "kind": "scripture_citation",
        "lemma": "GENÈSE III, 19",
        "raw": "GENÈSE / III, 19................... 53²",
        "letter": None,
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-182.txt"),
        "refs": ["53²"],
        "scripture": [("GENÈSE / III, 19", "GENÈSE", "Gênesis", 3, 19, None, None, False)],
        "note": "First clear entry in the first Bible-citation table.",
    },
    {
        "key": "PO025:entry:0006",
        "section": "PO025:alpha:author_index:005",
        "node": None,
        "order": 1,
        "kind": "lemma",
        "lemma": "Saint Ignace d'Antioche",
        "raw": "Saint Ignace d'Antioche. . . . P. G., t. V, col. 660 . . . . . . . . . . . . . . 515",
        "letter": "S",
        "section_file": f("b73c79c4-2a40-4b45-9516-1b504f5e1f0e-184.txt"),
        "refs": ["P. G., t. V, col. 660", "515"],
        "note": "Only patristic-citation line visible before the first sequence's table of contents.",
    },
    {
        "key": "PO025:entry:0007",
        "section": "PO025:alpha:pericope_index:006",
        "node": "PO025:node:008",
        "order": 1,
        "kind": "scripture_pericope",
        "lemma": "Genèse 1, 1-11, 3",
        "raw": "Genèse / 1, 1-11, 3 . . . . . . . . . . . . . . . . . 52-57",
        "letter": None,
        "section_file": f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-481.txt"),
        "refs": ["52-57"],
        "scripture": [("Genèse / 1, 1-11, 3", "Genèse", "Gênesis", 1, 1, 11, 3, True)],
        "note": "First clear pericope entry.",
    },
    {
        "key": "PO025:entry:0008",
        "section": "PO025:alpha:pericope_index:006",
        "node": "PO025:node:009",
        "order": 2,
        "kind": "scripture_pericope",
        "lemma": "Exode XII, 1-14",
        "raw": "Exode / XII, 1-14 . . . . . . . . . . . . . . . . . . . 340-342",
        "letter": None,
        "section_file": f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-481.txt"),
        "refs": ["340-342"],
        "scripture": [("Exode / XII, 1-14", "Exode", "Êxodo", 12, 1, 12, 14, True)],
        "note": "Second book heading in the pericope table.",
    },
    {
        "key": "PO025:entry:0009",
        "section": "PO025:alpha:concordance_index:007",
        "node": "PO025:node:010",
        "order": 1,
        "kind": "concordance_item",
        "lemma": "B Berlin, Staatsbibl. Or. 2o 2692",
        "raw": "B Berlin, Staatsbibl. Or. 2o 2692 A.M. 1520 = A.D. 1804",
        "letter": "B",
        "section_file": f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-485.txt"),
        "refs": ["A.M. 1520 = A.D. 1804"],
        "note": "Sigla row from the concordance introduction; this is a concordance item, not an alphabetical lemma.",
    },
    {
        "key": "PO025:entry:0010",
        "section": "PO025:alpha:concordance_index:007",
        "node": "PO025:node:011",
        "order": 2,
        "kind": "concordance_item",
        "lemma": "Dimanche des Rameaux — Veille",
        "raw": "Dimanche des Rameaux / Veille Ps. 121,1-2,5 / Jn. 12,1-11",
        "letter": "D",
        "section_file": f("de4224d9-6414-41e0-bbe2-ceb7bdd253c7-486.txt"),
        "refs": ["C/ LMPR3, 2M,2 L/RR2, 117,26-27 • BP23"],
        "scripture": [("Ps. 121,1-2,5", "Ps.", "Salmos", 121, 1, 121, 5, True), ("Jn. 12,1-11", "Jn.", "São João", 12, 1, 12, 11, True)],
        "note": "First recoverable concordance row pairing liturgical rubric, readings, and manuscript/sigla apparatus.",
    },
    {
        "key": "PO025:entry:0011",
        "section": "PO025:alpha:scripture_index:008",
        "node": "PO025:node:012",
        "order": 1,
        "kind": "scripture_citation",
        "lemma": "GENESE I, 11-12",
        "raw": "GENESE. I, 11-12 . . . . . . 17_10-10",
        "letter": None,
        "section_file": f("e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt"),
        "refs": ["17_10-10"],
        "scripture": [("GENESE. I, 11-12", "GENESE", "Gênesis", 1, 11, 1, 12, True)],
        "note": "First clear entry in the Euchologium scripture index; material target remains unresolved because no local OCR page with printed page 17 was found.",
        "force_unresolved": True,
    },
    {
        "key": "PO025:entry:0012",
        "section": "PO025:alpha:scripture_index:008",
        "node": "PO025:node:013",
        "order": 2,
        "kind": "scripture_citation",
        "lemma": "PSAUMES IV",
        "raw": "PSAUMES. IV . . . . . . 116_6-117_4",
        "letter": None,
        "section_file": f("e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt"),
        "refs": ["116_6-117_4"],
        "scripture": [("PSAUMES. IV", "PSAUMES", "Salmos", 4, None, None, None, False)],
        "note": "First Psalms entry in the Euchologium scripture index.",
    },
    {
        "key": "PO025:entry:0013",
        "section": "PO025:alpha:onomastic_mixed:009",
        "node": "PO025:node:014",
        "order": 1,
        "kind": "lemma",
        "lemma": "ܐܒ",
        "raw": "ܐܒ 160, 197, 202, 218, 230, 236, 237, 238, 240, 241, 242, 243, 245, 246, 251, 252, 263, 273, 274, 275, 286, 287, 288",
        "letter": "ܐ",
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-816.txt"),
        "refs": ["160", "197", "202", "218", "230", "236", "237", "238", "240", "241", "242", "243", "245", "246", "251", "252", "263", "273", "274", "275", "286", "287", "288"],
        "note": "First clear line of the later Syriac proper-names table.",
    },
    {
        "key": "PO025:entry:0014",
        "section": "PO025:alpha:onomastic_mixed:009",
        "node": "PO025:node:015",
        "order": 2,
        "kind": "lemma",
        "lemma": "ܡܪܝܡ",
        "raw": "ܡܪܝܡ 154 n. 1 164 n. 1 187 n. 2 246 n. 1, 250 n. 1, 255 n. 1 256 n. 1",
        "letter": "ܡ",
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-817.txt"),
        "refs": ["154 n. 1", "164 n. 1", "187 n. 2", "246 n. 1", "250 n. 1", "255 n. 1", "256 n. 1"],
        "note": "Later table Mary line; distinct from the earlier homily-index Mary line.",
    },
    {
        "key": "PO025:entry:0015",
        "section": "PO025:alpha:foreign_terms:010",
        "node": "PO025:node:017",
        "order": 1,
        "kind": "lemma",
        "lemma": "ܗܘ ܕܐܠܗܐ",
        "raw": "ܗܘ ܕܐܠܗܐ 231₄-5 278₁ 287₁₁-12",
        "letter": "ܗ",
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-820.txt"),
        "refs": ["231₄-5", "278₁", "287₁₁-12"],
        "note": "Clear later Syriac foreign-terms entry.",
    },
    {
        "key": "PO025:entry:0016",
        "section": "PO025:alpha:foreign_terms:011",
        "node": "PO025:node:018",
        "order": 1,
        "kind": "lemma",
        "lemma": "Aires",
        "raw": "Aires 164n.1, 188n.1",
        "letter": "A",
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-823.txt"),
        "refs": ["164n.1", "188n.1"],
        "note": "First clear later Greek-words entry.",
    },
    {
        "key": "PO025:entry:0017",
        "section": "PO025:alpha:scripture_index:012",
        "node": "PO025:node:019",
        "order": 1,
        "kind": "scripture_citation",
        "lemma": "GENESE I, 26, 27",
        "raw": "GENESE / I, 26, 27......................225",
        "letter": None,
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-824.txt"),
        "refs": ["225"],
        "scripture": [("GENESE / I, 26, 27", "GENESE", "Gênesis", 1, 26, 1, 27, True)],
        "note": "First entry on file 824, proving the later Bible table begins one OCR file before the previous checkpoint.",
    },
    {
        "key": "PO025:entry:0018",
        "section": "PO025:alpha:scripture_index:012",
        "node": "PO025:node:020",
        "order": 2,
        "kind": "scripture_citation",
        "lemma": "HEBREUX I, 1",
        "raw": "HEBREUX / I, 1..........................271",
        "letter": None,
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt"),
        "refs": ["271"],
        "scripture": [("HEBREUX / I, 1", "HEBREUX", "Hebreus", 1, 1, None, None, False)],
        "note": "Verified in OCR file 826; target was resolved by helper in the previous checkpoint.",
    },
    {
        "key": "PO025:entry:0019",
        "section": "PO025:alpha:author_index:013",
        "node": None,
        "order": 1,
        "kind": "lemma",
        "lemma": "Saint Basile",
        "raw": "Saint Basile..............P.G., t. XXIX, col. 689...............252",
        "letter": "S",
        "section_file": f("e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt"),
        "refs": ["P.G., t. XXIX, col. 689", "252"],
        "note": "First line of the later patristic-citations table.",
    },
]


def parse_ref(raw: str) -> tuple[str, str | None, int | None, str | None, str | None, str | None]:
    raw = raw.strip()
    if re.match(r"^[A-Z][A-Za-z. ]*, t\.", raw) or raw.startswith("A.M.") or re.match(r"^[A-Z]+[/;]", raw):
        return "parallel_locator", None, None, None, None, None
    range_match = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", raw)
    if range_match:
        return "editorial_range", range_match.group(1), int(range_match.group(1)), None, range_match.group(1), range_match.group(2)
    page_match = re.match(r"^(\d+)", raw)
    page_raw = page_match.group(1) if page_match else None
    page_int = int(page_raw) if page_raw else None
    line = None
    m = re.match(r"^\d+\s*[_₋,]?\s*([₀-₉0-9][₀-₉0-9₋\\-–]*)", raw)
    if "_" in raw or any(ch in raw for ch in "₀₁₂₃₄₅₆₇₈₉"):
        tail = re.sub(r"^\d+[_ ,]*", "", raw)
        tail = tail.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉₋–", "0123456789--"))
        line = tail.strip(" .")
    if raw.endswith("²") and page_int:
        line = "²"
    kind = "editorial_page_line" if line else "editorial_page"
    return kind, page_raw, page_int, line or None, None, None


def helper_entry_id(entry_key: str, ref_order: int) -> str:
    return f"{entry_key}:ref:{ref_order}"


def query_names(spec: dict) -> list[str]:
    names = [spec["lemma"]]
    names.append(re.split(r"[/.,;]", spec["lemma"])[0].strip())
    if spec.get("scripture"):
        names.extend([spec["scripture"][0][0], spec["scripture"][0][1]])
    return list(dict.fromkeys([name for name in names if name]))


def write_helper() -> None:
    entries = []
    for spec in ENTRY_SPECS:
        if spec.get("force_unresolved"):
            continue
        for idx, ref_raw in enumerate(spec["refs"], start=1):
            _kind, _page_raw, page_int, _line, _start, _end = parse_ref(ref_raw)
            if page_int is None:
                continue
            entries.append(
                {
                    "entry_id": helper_entry_id(spec["key"], idx),
                    "lemma_raw": spec["lemma"],
                    "query_names": query_names(spec),
                    "page_hints": [str(page_int)],
                    "page_hint_ints": [page_int],
                    "context_raw": spec["raw"],
                }
            )
    HELPER_IN.write_text(
        json.dumps(
            {"volume_id": VOLUME_ID, "source_root": str(SOURCE_ROOT), "options": {"top_k": 5, "adjacency_window": 2}, "entries": entries},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {HELPER_IN} entries={len(entries)}")


def load_helper() -> dict[str, dict]:
    if not HELPER_OUT.exists():
        return {}
    data = json.loads(HELPER_OUT.read_text(encoding="utf-8"))
    return {entry["entry_id"]: entry for entry in data.get("entries", [])}


def chosen_helper(helper: dict | None, page: int | None) -> tuple[str | None, float | None, dict | None]:
    if not helper:
        return None, None, None
    candidates = helper.get("candidates") or []
    best = helper.get("best_candidate")
    chosen = best
    if page is not None:
        for candidate in candidates:
            if candidate.get("candidate_role") == "target_candidate" and candidate.get("inferred_printed_page") in {page - 1, page, page + 1}:
                chosen = candidate
                break
    top = [
        {
            "file": candidate.get("file"),
            "probability": candidate.get("probability"),
            "candidate_role": candidate.get("candidate_role"),
            "reason_summary": candidate.get("reason_summary"),
        }
        for candidate in candidates[:5]
    ]
    summary = {
        "status": helper.get("status"),
        "candidate_role": chosen.get("candidate_role") if chosen else None,
        "reason_summary": chosen.get("reason_summary") if chosen else None,
        "best_candidate": {
            "file": chosen.get("file"),
            "file_seq": chosen.get("file_seq"),
            "score": chosen.get("score"),
            "probability": chosen.get("probability"),
            "candidate_role": chosen.get("candidate_role"),
            "reason_summary": chosen.get("reason_summary"),
        }
        if chosen
        else None,
        "top_candidates": top,
    }
    if chosen:
        return chosen.get("file"), chosen.get("probability"), summary
    return None, None, summary


def page_map_note(target: str | None, page: int | None) -> dict | None:
    if not target or page is None:
        return None
    return {
        "status": "resolved_by_local_page_evidence",
        "candidate_role": "target_candidate",
        "reason_summary": f"Local rg/read_ocr_page_text checks found printed page {page} at {target}.",
        "best_candidate": {
            "file": target,
            "probability": 0.72,
            "candidate_role": "target_candidate",
            "reason_summary": "resolved by printed-page bracket/suffix evidence inside PO025",
        },
        "top_candidates": [
            {
                "file": target,
                "probability": 0.72,
                "candidate_role": "target_candidate",
                "reason_summary": "printed-page signal in current source_root",
            }
        ],
    }


def build() -> dict:
    helper_map = load_helper()
    nodes = [node(row) for row in NODE_ROWS]
    entries = []
    refs = []
    scripture_refs = []

    for spec in ENTRY_SPECS:
        entry_refs = []
        for idx, ref_raw in enumerate(spec["refs"], start=1):
            kind, page_raw, page_int, line_raw, range_start, range_end = parse_ref(ref_raw)
            helper_target, helper_prob, helper_summary = chosen_helper(helper_map.get(helper_entry_id(spec["key"], idx)), page_int)
            mapped = None if spec.get("force_unresolved") else printed_page_file(page_int) if page_int is not None else None
            if spec.get("prefer_page_map") and mapped:
                target = mapped
                probability = 0.72
            else:
                target = helper_target or mapped
                probability = helper_prob if helper_target else (0.72 if mapped else None)
            if target is None and kind == "parallel_locator":
                target = spec["section_file"]
                probability = 0.68
            raw_json = {"source_token": ref_raw, "section_kind": next(s["section_kind"] for s in SECTIONS if s["section_key"] == spec["section"])}
            if helper_summary:
                raw_json["helper"] = helper_summary
            elif mapped:
                raw_json["helper"] = page_map_note(mapped, page_int)
            elif kind == "parallel_locator" and target:
                raw_json["locator_note"] = "Parallel/bibliographic locator anchored to the OCR table line itself."
            if spec.get("force_unresolved"):
                raw_json["locator_attempts"] = [
                    "Reviewed OCR files e1a2562f-...-622 and -623 through read_ocr_page_text.py.",
                    "Searched current source_root for [17], 17_10-10, and GENESE/I, 11-12.",
                    "No local OCR file clearly materializes the cited printed page 17; index-page self-match was not used as target.",
                ]
            ref = {
                "entry_key": spec["key"],
                "ref_order": idx,
                "ref_kind": kind,
                "ref_raw": ref_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": line_raw,
                "range_start_raw": range_start,
                "range_end_raw": range_end,
                "target_file": target,
                "target_file_probability": probability,
                "section_start_file": spec["section_file"],
                "editorial_anchor_file": target,
                "confidence": probability or (0.0 if spec.get("force_unresolved") else 0.42),
                "raw_json": raw_json,
            }
            refs.append(ref)
            if target:
                entry_refs.append(ref)

        first_ref = entry_refs[0] if entry_refs else None
        entries.append(
            {
                "entry_key": spec["key"],
                "section_key": spec["section"],
                "parent_node_key": spec["node"],
                "entry_order": spec["order"],
                "entry_kind": spec["kind"],
                "lemma_raw": spec["lemma"],
                "lemma_display": spec["lemma"],
                "lemma_norm": norm(spec["lemma"]),
                "lemma_sort": norm(spec["lemma"]),
                "entry_raw": spec["raw"],
                "context_raw": None,
                "heading_letter": spec["letter"],
                "inferred_printed_page": first_ref["page_ref_int"] if first_ref else (parse_ref(spec["refs"][0])[2] if spec["refs"] else None),
                "section_start_file": spec["section_file"],
                "editorial_anchor_file": first_ref["target_file"] if first_ref else None,
                "target_file_best": first_ref["target_file"] if first_ref else None,
                "confidence": first_ref["confidence"] if first_ref else (0.0 if spec.get("force_unresolved") else 0.42),
                "raw_json": {
                    "section_kind": next(s["section_kind"] for s in SECTIONS if s["section_key"] == spec["section"]),
                    "source_entry_id": spec["key"].replace(":", "_").lower(),
                    "source_section_key": spec["section"],
                    "note": spec["note"],
                },
            }
        )
        for order, scripture in enumerate(spec.get("scripture", []), start=1):
            ref_raw, book_raw, book_norm, ch_start, v_start, ch_end, v_end, is_range = scripture
            scripture_refs.append(
                {
                    "entry_key": spec["key"],
                    "ref_order": order,
                    "ref_role": "pericope" if spec["kind"] == "scripture_pericope" else ("concordance_component" if spec["kind"] == "concordance_item" else "citation"),
                    "ref_raw": ref_raw,
                    "book_raw": book_raw,
                    "book_norm": book_norm,
                    "chapter_start": ch_start,
                    "verse_start": v_start,
                    "chapter_end": ch_end,
                    "verse_end": v_end,
                    "is_range": bool(is_range),
                    "confidence": 0.86 if not spec.get("force_unresolved") else 0.78,
                    "raw_json": {"source_entry_id": spec["key"].replace(":", "_").lower(), "source_token": spec["raw"]},
                }
            )

    evidence = sorted({section["file_start"] for section in SECTIONS} | {section["file_end"] for section in SECTIONS})
    return {
        "schema_version": 1,
        "generated_at": now(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "Rerun built from the previous checkpoint plus OCR verification of every candidate table window.",
                "PO025 contains repeated closing table sequences and a separate concordance block; all verified sections are represented.",
            ],
        },
        "sections": SECTIONS,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "partial_extraction",
            "entries_status_reason": (
                "Recovered all verified PO025 index/concordance sections and serialized a conservative set of line items from each. "
                "This rerun fixes the previous omission of Syriac foreign-terms, Greek-words, patristic-citation, concordance, and tail-start Bible sections; "
                "it also resolves the earlier null Syriac ܐܕ locators through local printed-page evidence. Full dense-table coverage remains a follow-up task."
            ),
            "evidence_files": evidence,
        },
        "notes": [
            "OCR literals are preserved except for the known Mary line-break hyphen artifact, which was removed.",
            "The later Bible-citation table starts in OCR file e2ff...-824, before the previous checkpoint's file 825 start.",
            "GENESE. I, 11-12 in the Euchologium index remains materially unresolved after scoped searches for printed page 17.",
            "Section kinds stay inside the importer enum; Greek word tables are modeled as foreign_terms and patristic citation tables as author_index.",
        ],
    }


def assemble() -> None:
    payload = build()
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "sections.json").write_text(json.dumps(payload["sections"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "nodes.json").write_text(json.dumps(payload["nodes"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "entries.json").write_text(json.dumps(payload["entries"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "refs.json").write_text(json.dumps(payload["refs"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "scripture_refs.json").write_text(json.dumps(payload["scripture_refs"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TODO.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": payload["generated_at"],
                "current_focus": "PO025 payload rerun complete",
                "completed": [
                    "inspected filtered OCR windows with read_ocr_page_text.py",
                    "added omitted foreign_terms, Greek words, patristic author, concordance, and corrected tail Bible sections",
                    "rebuilt helper request/output and assembled final payload",
                    "resolved prior null ܐܕ refs by local printed-page evidence",
                ],
                "pending": [
                    "future pass can expand from representative entries to exhaustive dense-table extraction"
                ],
                "blocked": [
                    "GENESE. I, 11-12 cited page 17 has no clear material target in current source_root"
                ],
                "notes": [
                    "Keep OCR file suffix, printed page, and cited reference separate.",
                    "Do not use the Euchologium index page itself as target for the cited page 17.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-helper", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    if args.write_helper:
        write_helper()
    if args.assemble:
        assemble()
    if not args.write_helper and not args.assemble:
        parser.error("use --write-helper and/or --assemble")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build PO008 alphabetical-index payload.

Usage:
  python scripts/pipeline_index_extraction/PO008_build_alphabetical_payload.py --write-helper
  python scripts/index_target_locator.py --input data/alphabetical_index_payloads/PO008_helper_request.json --output data/alphabetical_index_payloads/PO008_helper_output.json --pretty
  python scripts/pipeline_index_extraction/PO008_build_alphabetical_payload.py --assemble
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO008"
COLLECTION = "PO"
SOURCE_ROOT = ROOT / "teste/PO008/text"
OUT = ROOT / "data/alphabetical_index_payloads/PO008_alphabetical_indices.json"
HELPER_IN = ROOT / "data/alphabetical_index_payloads/PO008_helper_request.json"
HELPER_OUT = ROOT / "data/alphabetical_index_payloads/PO008_helper_output.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PO008"


F = {
    195: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-195.txt",
    196: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-196.txt",
    197: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-197.txt",
    198: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-198.txt",
    199: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-199.txt",
    200: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-200.txt",
    201: SOURCE_ROOT / "46a31c44-0a2a-4f46-8ae0-a6ffdc48352a-201.txt",
    202: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-202.txt",
    203: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-203.txt",
    204: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-204.txt",
    205: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-205.txt",
    206: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-206.txt",
    207: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-207.txt",
    215: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-215.txt",
    216: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-216.txt",
    217: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-217.txt",
    218: SOURCE_ROOT / "7cae01a2-872f-4eb3-ae61-d1d4a837e1a9-218.txt",
    706: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-706.txt",
    707: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-707.txt",
    715: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-715.txt",
    716: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-716.txt",
    717: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-717.txt",
    718: SOURCE_ROOT / "b44b3c65-b6a1-4d11-84f8-04b95b885221-718.txt",
}


def s(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s'ܐ-ܿ\u0370-\u03ff-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def helper_entry_id(entry_key: str, ref_order: int) -> str:
    return f"{entry_key}:ref:{ref_order}"


SECTIONS = [
    ("PO008:alpha:onomastic_mixed:001", 1, "onomastic_mixed", "TABLE DES NOMS PROPRES SYRIAQUES", 185, 188, F[195], F[198], 0.99, "Syriac proper-name table; files 195-198 confirmed by running heads."),
    ("PO008:alpha:foreign_terms:002", 2, "foreign_terms", "TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES", 189, 192, F[199], F[202], 0.99, "Syriac foreign/remarkable-words table; old checkpoint ended at file 201, but file 202 continues the same running head."),
    ("PO008:alpha:onomastic_mixed:003", 3, "onomastic_mixed", "TABLE GRECQUE DES NOMS PROPRES ET DES MOTS REMARQUABLES", 193, 194, F[203], F[204], 0.99, "Greek proper-name and remarkable-word table; true mixed onomastic/lexical index."),
    ("PO008:alpha:scripture_index:004", 4, "scripture_index", "TABLE DES RENVOIS A L'ÉCRITURE", 195, 195, F[205], F[205], 0.99, "Bible-reference table for the Plérophories section; file 206 is the manuscripts table and begins a different section."),
    ("PO008:alpha:editorial_closure:005", 5, "editorial_closure", "TABLE DES MANUSCRITS UTILISÉS", 196, 196, F[206], F[206], 0.86, "Compact manuscript-location table. It is not alphabetical, but is retained as editorial closure because it was captured inside the same table sequence."),
    ("PO008:alpha:alphabetical_general:006", 6, "alphabetical_general", "TABLE ALPHABÉTIQUE DES MATIÈRES¹", 197, 205, F[207], F[215], 0.99, "Alphabetical subject table in the Plérophories section."),
    ("PO008:alpha:analytic_subject:007", 7, "analytic_subject", "TABLE ANALYTIQUE DES MATIÈRES", 206, 208, F[216], F[218], 0.99, "Analytical table and appendix summary; file 219 starts a new fascicle."),
    ("PO008:alpha:alphabetical_general:008", 8, "alphabetical_general", "TABLE ALPHABÉTIQUE", 694, 703, F[706], F[715], 0.99, "Late Latin alphabetical table for Les Canons des Apôtres; the title page lacks a visible printed page number, inferred from continuation headers."),
    ("PO008:alpha:scripture_index:009", 9, "scripture_index", "TABLE DES PASSAGES DE LA BIBLE", 704, 706, F[716], F[718], 0.97, "Bible-passages table continues through the top OCR block of file 718; the lower block of file 718 starts TABLE DES CHAPITRES and is excluded."),
]


NODES = [
    ("PO008:node:001", "PO008:alpha:onomastic_mixed:001", None, 1, "letter_group", "ܐ", 1),
    ("PO008:node:002", "PO008:alpha:onomastic_mixed:001", None, 2, "letter_group", "ܚ", 1),
    ("PO008:node:003", "PO008:alpha:foreign_terms:002", None, 1, "letter_group", "[ܐ]", 1),
    ("PO008:node:004", "PO008:alpha:foreign_terms:002", None, 2, "letter_group", "ܥ", 1),
    ("PO008:node:005", "PO008:alpha:foreign_terms:002", None, 3, "letter_group", "ܡ", 1),
    ("PO008:node:006", "PO008:alpha:onomastic_mixed:003", None, 1, "letter_group", "A", 1),
    ("PO008:node:007", "PO008:alpha:onomastic_mixed:003", None, 2, "letter_group", "Δ", 1),
    ("PO008:node:008", "PO008:alpha:onomastic_mixed:003", None, 3, "letter_group", "N", 1),
    ("PO008:node:009", "PO008:alpha:onomastic_mixed:003", None, 4, "letter_group", "Π", 1),
    ("PO008:node:010", "PO008:alpha:scripture_index:004", None, 1, "heading_group", "ANCIEN TESTAMENT", 1),
    ("PO008:node:011", "PO008:alpha:scripture_index:004", None, 2, "heading_group", "NOUVEAU TESTAMENT", 1),
    ("PO008:node:012", "PO008:alpha:alphabetical_general:006", None, 1, "letter_group", "A", 1),
    ("PO008:node:013", "PO008:alpha:alphabetical_general:006", None, 2, "letter_group", "T", 1),
    ("PO008:node:014", "PO008:alpha:analytic_subject:007", None, 1, "ordinal_group", "PLÉROPHORIES", 1),
    ("PO008:node:015", "PO008:alpha:analytic_subject:007", None, 2, "ordinal_group", "APPENDICE", 1),
    ("PO008:node:016", "PO008:alpha:alphabetical_general:008", None, 1, "letter_group", "A", 1),
    ("PO008:node:017", "PO008:alpha:alphabetical_general:008", None, 2, "letter_group", "B", 1),
    ("PO008:node:018", "PO008:alpha:scripture_index:009", None, 1, "heading_group", "Genèse", 1),
    ("PO008:node:019", "PO008:alpha:scripture_index:009", None, 2, "heading_group", "Exode", 1),
    ("PO008:node:020", "PO008:alpha:scripture_index:009", None, 3, "heading_group", "Daniel", 1),
    ("PO008:node:021", "PO008:alpha:scripture_index:009", None, 4, "heading_group", "I Timothée", 1),
    ("PO008:node:022", "PO008:alpha:scripture_index:009", None, 5, "heading_group", "Tite", 1),
]


ENTRIES = [
    {"key": "PO008:entry:001", "section": "PO008:alpha:onomastic_mixed:001", "node": "PO008:node:001", "kind": "lemma", "lemma": "ܐܒܘܝ grand-père de Nestorius", "raw": "ܐܒܘܝ grand-père de Nestorius. 162_8-11", "letter": "ܐ", "page": 162, "anchor": F[195], "refs": [("editorial_page_line", "162_8-11", 162, "8-11")]},
    {"key": "PO008:entry:002", "section": "PO008:alpha:onomastic_mixed:001", "node": "PO008:node:001", "kind": "lemma", "lemma": "ܐܢܛܝܘ", "raw": "ܐܢܛܝܘ 162₁₇", "letter": "ܐ", "page": 162, "anchor": F[196], "refs": [("editorial_page_line", "162₁₇", 162, "17")]},
    {"key": "PO008:entry:003", "section": "PO008:alpha:onomastic_mixed:001", "node": "PO008:node:002", "kind": "lemma", "lemma": "ܚܢܢܝܐ prophète", "raw": "ܚܢܢܝܐ prophète, 146_13", "letter": "ܚ", "page": 146, "anchor": F[197], "refs": [("editorial_page_line", "146_13", 146, "13")]},
    {"key": "PO008:entry:004", "section": "PO008:alpha:foreign_terms:002", "node": "PO008:node:003", "kind": "lemma", "lemma": "ܐܓܢܘܣܝܐ", "raw": "ܐܓܢܘܣܝܐ 143,10", "letter": "ܐ", "page": 143, "anchor": F[199], "refs": [("editorial_page_line", "143,10", 143, "10")]},
    {"key": "PO008:entry:005", "section": "PO008:alpha:foreign_terms:002", "node": "PO008:node:004", "kind": "lemma", "lemma": "ܥܡܘܕܐ la sainte communion", "raw": "ܥܡܘܕܐ la sainte communion. 135₂", "letter": "ܥ", "page": 135, "anchor": F[200], "refs": [("editorial_page_line", "135₂", 135, "2")]},
    {"key": "PO008:entry:006", "section": "PO008:alpha:foreign_terms:002", "node": "PO008:node:005", "kind": "lemma", "lemma": "ܡܠܦܢܐ", "raw": "ܡܠܦܢܐ 12.", "letter": "ܡ", "page": 12, "anchor": F[201], "refs": [("editorial_page", "12", 12, None)]},
    {"key": "PO008:entry:007", "section": "PO008:alpha:foreign_terms:002", "node": "PO008:node:005", "kind": "lemma", "lemma": "ܡܥܢܝܬܐ", "raw": "ܡܥܢܝܬܐ 81_3-5 125_2", "letter": "ܡ", "page": 81, "anchor": F[202], "refs": [("editorial_page_line", "81_3-5", 81, "3-5"), ("editorial_page_line", "125_2", 125, "2")]},
    {"key": "PO008:entry:008", "section": "PO008:alpha:onomastic_mixed:003", "node": "PO008:node:006", "kind": "lemma", "lemma": "Ἀγαθόκλεια", "raw": "Ἀγαθόκλεια, 140, n. 1.", "letter": "A", "page": 140, "anchor": F[203], "refs": [("editorial_page", "140, n. 1", 140, None)]},
    {"key": "PO008:entry:009", "section": "PO008:alpha:onomastic_mixed:003", "node": "PO008:node:007", "kind": "lemma", "lemma": "δίπτυχα", "raw": "δίπτυχα, 56, n. 2, 181₄", "letter": "Δ", "page": 56, "anchor": F[203], "refs": [("editorial_page", "56, n. 2", 56, None), ("editorial_page_line", "181₄", 181, "4")]},
    {"key": "PO008:entry:010", "section": "PO008:alpha:onomastic_mixed:003", "node": "PO008:node:008", "kind": "lemma", "lemma": "Νεστόριος", "raw": "Νεστόριος 175₄", "letter": "N", "page": 175, "anchor": F[204], "refs": [("editorial_page_line", "175₄", 175, "4")]},
    {"key": "PO008:entry:011", "section": "PO008:alpha:onomastic_mixed:003", "node": "PO008:node:009", "kind": "lemma", "lemma": "Πέτρος, disciple d'Isaïe", "raw": "Πέτρος, disciple d'Isaïe. 164₂₃₋₃₀", "letter": "Π", "page": 164, "anchor": F[204], "refs": [("editorial_page_line", "164₂₃₋₃₀", 164, "23-30")]},
    {"key": "PO008:entry:012", "section": "PO008:alpha:scripture_index:004", "node": "PO008:node:010", "kind": "scripture_citation", "lemma": "Exode, xxiii, 2", "raw": "Exode, xxiii, 2 . . . . . . . . . . 110", "letter": "E", "page": 110, "anchor": F[205], "refs": [("editorial_page", "110", 110, None)], "scripture": [("Exode, xxiii, 2", "Exode", "Êxodo", 23, 2, None, None, 0)]},
    {"key": "PO008:entry:013", "section": "PO008:alpha:scripture_index:004", "node": "PO008:node:011", "kind": "scripture_citation", "lemma": "Matthieu, xi, 7", "raw": "Matthieu, xi, 7 . . . . . . . . . . 116", "letter": "M", "page": 116, "anchor": F[205], "refs": [("editorial_page", "116", 116, None)], "scripture": [("Matthieu, xi, 7", "Matthieu", "Mateus", 11, 7, None, None, 0)]},
    {"key": "PO008:entry:014", "section": "PO008:alpha:scripture_index:004", "node": "PO008:node:011", "kind": "scripture_citation", "lemma": "II Tim., ii, 1-2", "raw": "II Tim., ii, 1-2 . . . . . . . . . . 155", "letter": "T", "page": 155, "anchor": F[205], "refs": [("editorial_page", "155", 155, None)], "scripture": [("II Tim., ii, 1-2", "II Tim.", "2 Timóteo", 2, 1, 2, 2, 1)]},
    {"key": "PO008:entry:015", "section": "PO008:alpha:editorial_closure:005", "node": None, "kind": "lemma", "lemma": "add. 14650 (A", "raw": "add. 14650 (A . . . . . . Pages. 5, 11 à 156", "letter": "a", "page": 5, "anchor": F[206], "refs": [("editorial_page", "5", 5, None), ("editorial_range", "11 à 156", 11, None)]},
    {"key": "PO008:entry:016", "section": "PO008:alpha:editorial_closure:005", "node": None, "kind": "lemma", "lemma": "Sachau 329", "raw": "Sachau 329 . . . . . . . 157 à 161", "letter": "S", "page": 157, "anchor": F[206], "refs": [("editorial_range", "157 à 161", 157, None)]},
    {"key": "PO008:entry:017", "section": "PO008:alpha:alphabetical_general:006", "node": "PO008:node:012", "kind": "lemma", "lemma": "Abi'asoum oncle de Nestorius", "raw": "Abi'asoum oncle de Nestorius 163₁₂₋₁₆", "letter": "A", "page": 163, "anchor": F[207], "refs": [("editorial_page_line", "163₁₂₋₁₆", 163, "12-16")]},
    {"key": "PO008:entry:018", "section": "PO008:alpha:alphabetical_general:006", "node": "PO008:node:012", "kind": "lemma", "lemma": "Apophthegmes", "raw": "Apophthegmes. Étude des diverses rédactions d'un récit relatif à Théodose le Jeune, et essai de classification 167₂₈₋₁₇₁₄. Projet d'édition 170, n. 3. Cf. 9₁₁. V. Pères du désert.", "letter": "A", "page": 167, "anchor": F[207], "refs": [("editorial_page_line", "167₂₈₋₁₇₁₄", 167, "28-1714"), ("editorial_page", "170, n. 3", 170, None), ("editorial_page_line", "9₁₁", 9, "11")]},
    {"key": "PO008:entry:019", "section": "PO008:alpha:alphabetical_general:006", "node": "PO008:node:013", "kind": "lemma", "lemma": "Théodore successeur de Pierre l'Ibère", "raw": "Théodore successeur de Pierre l'Ibère 89_21", "letter": "T", "page": 89, "anchor": F[215], "refs": [("editorial_page_line", "89_21", 89, "21")]},
    {"key": "PO008:entry:020", "section": "PO008:alpha:analytic_subject:007", "node": "PO008:node:014", "kind": "lemma", "lemma": "XXVII. — Contre l'empereur Marcien", "raw": "XXVII. — Contre l'empereur Marcien . . . . . . . . . . . . . . . . . . . 68", "letter": "X", "page": 68, "anchor": F[217], "refs": [("editorial_page", "68", 68, None)]},
    {"key": "PO008:entry:021", "section": "PO008:alpha:analytic_subject:007", "node": "PO008:node:014", "kind": "lemma", "lemma": "XXVIII-XXIX. — Contre Chalcédoine", "raw": "XXVIII-XXIX. — Contre Chalcédoine . . . . . . . . . . . . . . . . . . . 69", "letter": "X", "page": 69, "anchor": F[217], "refs": [("editorial_page", "69", 69, None)]},
    {"key": "PO008:entry:022", "section": "PO008:alpha:analytic_subject:007", "node": "PO008:node:015", "kind": "lemma", "lemma": "XCIII. — Apparition du martyr Marcellus", "raw": "XCIII. — Apparition du martyr Marcellus . . . . . . . . . . . . . . . . 160", "letter": "X", "page": 160, "anchor": F[218], "refs": [("editorial_page", "160", 160, None)]},
    {"key": "PO008:entry:023", "section": "PO008:alpha:alphabetical_general:008", "node": "PO008:node:016", "kind": "lemma", "lemma": "Aaron.", "raw": "Aaron. I. 48, 77; 51, 81; 52, 88; 71, 113.", "letter": "A", "page": 77, "anchor": F[706], "refs": [("parallel_locator", "I. 48, 77", 77, None), ("parallel_locator", "I. 51, 81", 81, None), ("parallel_locator", "I. 52, 88", 88, None), ("parallel_locator", "I. 71, 113", 113, None)]},
    {"key": "PO008:entry:024", "section": "PO008:alpha:alphabetical_general:008", "node": "PO008:node:016", "kind": "lemma", "lemma": "Abdias.", "raw": "Abdias. I. 48, 77.", "letter": "A", "page": 77, "anchor": F[706], "refs": [("parallel_locator", "I. 48, 77", 77, None)]},
    {"key": "PO008:entry:025", "section": "PO008:alpha:alphabetical_general:008", "node": "PO008:node:017", "kind": "lemma", "lemma": "Baptême.", "raw": "Baptême. Intr. 8, 9; I. 33, 50; 34, 52, 57; 47, 69; 71, 110.", "letter": "B", "page": 8, "anchor": F[707], "refs": [("parallel_locator", "Intr. 8", 8, None), ("parallel_locator", "Intr. 9", 9, None), ("parallel_locator", "I. 33, 50", 50, None), ("parallel_locator", "I. 34, 52", 52, None), ("parallel_locator", "I. 34, 57", 57, None), ("parallel_locator", "I. 47, 69", 69, None), ("parallel_locator", "I. 71, 110", 110, None)]},
    {"key": "PO008:entry:026", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:018", "kind": "scripture_citation", "lemma": "Genèse, I, 27, 31", "raw": "Genèse, I, 27, 31. . . . . . . . . . 130", "letter": "G", "page": 130, "anchor": F[716], "refs": [("editorial_page", "130", 130, None)], "scripture": [("Genèse, I, 27", "Genèse", "Gênesis", 1, 27, None, None, 0), ("Genèse, I, 31", "Genèse", "Gênesis", 1, 31, None, None, 0)]},
    {"key": "PO008:entry:027", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:019", "kind": "scripture_citation", "lemma": "Exode, III-XIV", "raw": "Exode, III-XIV . . . . . . . . . . . 76, 77", "letter": "E", "page": 76, "anchor": F[716], "refs": [("editorial_page", "76", 76, None), ("editorial_page", "77", 77, None)], "scripture": [("Exode, III-XIV", "Exode", "Êxodo", 3, None, 14, None, 1)]},
    {"key": "PO008:entry:028", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:019", "kind": "scripture_citation", "lemma": "Exode, VII, 1", "raw": "— VII, 1. . . . . . . . . . . . . . . 74", "letter": "E", "page": 74, "anchor": F[716], "refs": [("editorial_page", "74", 74, None)], "scripture": [("Exode, VII, 1", "Exode", "Êxodo", 7, 1, None, None, 0)]},
    {"key": "PO008:entry:029", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:020", "kind": "scripture_citation", "lemma": "Daniel, iv, 24", "raw": "Daniel, iv, 24 . . . . . . . . . . 32", "letter": "D", "page": 32, "anchor": F[717], "refs": [("editorial_page", "32", 32, None)], "scripture": [("Daniel, iv, 24", "Daniel", "Daniel", 4, 24, None, None, 0)]},
    {"key": "PO008:entry:030", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:021", "kind": "scripture_citation", "lemma": "I Timothée, iii, 8-13", "raw": "1 Timothée, iii, 8-13 . . . . . . . . . 36 et 37", "letter": "T", "page": 36, "anchor": F[718], "refs": [("editorial_page", "36", 36, None), ("editorial_page", "37", 37, None)], "scripture": [("1 Timothée, iii, 8-13", "1 Timothée", "1 Timóteo", 3, 8, 3, 13, 1)]},
    {"key": "PO008:entry:031", "section": "PO008:alpha:scripture_index:009", "node": "PO008:node:022", "kind": "scripture_citation", "lemma": "Tite, i, 6", "raw": "Tite. i, 6 . . . . . . . . . . . . . . . . . . . . . . 139", "letter": "T", "page": 139, "anchor": F[718], "refs": [("editorial_page", "139", 139, None)], "scripture": [("Tite, i, 6", "Tite", "Tito", 1, 6, None, None, 0)]},
]


def query_names(entry: dict) -> list[str]:
    lemma = entry["lemma"]
    names = [lemma]
    if " " in lemma:
        names.append(re.split(r"[,.;]", lemma)[0])
    if entry["kind"] == "scripture_citation":
        names.append(entry["raw"].split(". .")[0].replace("—", "").strip())
    return list(dict.fromkeys([n for n in names if n]))


def build_helper() -> None:
    request = {"volume_id": VOLUME_ID, "source_root": s(SOURCE_ROOT), "options": {"top_k": 5, "adjacency_window": 2}, "entries": []}
    for entry in ENTRIES:
        for idx, ref in enumerate(entry["refs"], start=1):
            _, ref_raw, page, _line = ref
            request["entries"].append(
                {
                    "entry_id": helper_entry_id(entry["key"], idx),
                    "lemma_raw": entry["lemma"],
                    "query_names": query_names(entry),
                    "page_hints": [str(page)],
                    "page_hint_ints": [page],
                    "context_raw": entry["raw"],
                }
            )
    HELPER_IN.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def helper_summary(helper_entry: dict | None, page: int) -> dict | None:
    if not helper_entry:
        return None
    candidates = helper_entry.get("candidates", [])
    chosen = helper_entry.get("best_candidate")
    for candidate in candidates:
        if candidate.get("candidate_role") == "target_candidate" and candidate.get("inferred_printed_page") in {page - 1, page, page + 1}:
            chosen = candidate
            break
    top = []
    for candidate in candidates[:5]:
        top.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
            }
        )
    result = {
        "status": helper_entry.get("status"),
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
    if chosen and chosen is not helper_entry.get("best_candidate"):
        result["override_reason"] = "Selected the first target_candidate matching the cited printed page window instead of an index-page textual self-match."
    return result


def ref_page_raw(ref_kind: str, ref_raw: str, page: int) -> str:
    if ref_kind == "parallel_locator":
        return str(page)
    return str(page)


def line_range(line: str | None) -> tuple[str | None, str | None]:
    if not line or "-" not in line:
        return None, None
    a, b = line.split("-", 1)
    return a, b


def assemble() -> None:
    helper_data = json.loads(HELPER_OUT.read_text(encoding="utf-8"))
    helper_map = {entry["entry_id"]: entry for entry in helper_data["entries"]}

    sections = []
    for key, order, kind, heading, start, end, file_start, file_end, confidence, note in SECTIONS:
        sections.append(
            {
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
                "file_start": s(file_start),
                "file_end": s(file_end),
                "confidence": confidence,
                "raw_json": {"source": f"OCR files {file_start.name} to {file_end.name}", "note": note},
            }
        )

    nodes = []
    for node_key, section_key, parent, order, kind, label, level in NODES:
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": order,
                "node_kind": kind,
                "label_raw": label,
                "label_norm": norm(label),
                "label_sort": norm(label),
                "node_level": level,
                "confidence": 0.95,
                "raw_json": {"source": "OCR structural heading"},
            }
        )

    entries = []
    refs = []
    scripture_refs = []
    section_start = {item[0]: item[6] for item in SECTIONS}
    for order, entry in enumerate(ENTRIES, start=1):
        first_helper = helper_summary(helper_map.get(helper_entry_id(entry["key"], 1)), entry["refs"][0][2])
        best_file = first_helper.get("best_candidate", {}).get("file") if first_helper else None
        entries.append(
            {
                "entry_key": entry["key"],
                "section_key": entry["section"],
                "parent_node_key": entry["node"],
                "entry_order": order,
                "entry_kind": entry["kind"],
                "lemma_raw": entry["lemma"],
                "lemma_display": entry["lemma"],
                "lemma_norm": norm(entry["lemma"]),
                "lemma_sort": norm(entry["lemma"]),
                "entry_raw": entry["raw"],
                "context_raw": None,
                "heading_letter": entry["letter"],
                "inferred_printed_page": entry["page"],
                "section_start_file": s(section_start[entry["section"]]),
                "editorial_anchor_file": s(entry["anchor"]),
                "target_file_best": best_file,
                "confidence": round((first_helper.get("best_candidate", {}).get("probability", 0.65) if first_helper else 0.65), 6),
                "raw_json": {
                    "source_file": s(entry["anchor"]),
                    "helper": first_helper,
                    "locator_note": "Late TABLE ALPHABÉTIQUE entries parse the last arabic number as the fascicle page; preceding arabic numbers are canon locators." if entry["section"] == "PO008:alpha:alphabetical_general:008" else None,
                },
            }
        )

        for ref_order, ref in enumerate(entry["refs"], start=1):
            ref_kind, ref_raw, page, line = ref
            h = helper_summary(helper_map.get(helper_entry_id(entry["key"], ref_order)), page)
            best = (h or {}).get("best_candidate") or {}
            start_line, end_line = line_range(line)
            refs.append(
                {
                    "entry_key": entry["key"],
                    "ref_order": ref_order,
                    "ref_kind": ref_kind,
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_page_raw(ref_kind, ref_raw, page),
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": line,
                    "range_start_raw": start_line if ref_kind == "editorial_page_line" else (str(page) if ref_kind == "editorial_range" else None),
                    "range_end_raw": end_line if ref_kind == "editorial_page_line" else (re.search(r"à\s*(\d+)", ref_raw).group(1) if ref_kind == "editorial_range" and re.search(r"à\s*(\d+)", ref_raw) else None),
                    "target_file": best.get("file"),
                    "target_file_probability": best.get("probability"),
                    "section_start_file": s(section_start[entry["section"]]),
                    "editorial_anchor_file": s(entry["anchor"]),
                    "confidence": round(best.get("probability", 0.55), 6) if best else 0.55,
                    "raw_json": {
                        "source_file": s(entry["anchor"]),
                        "source_token": entry["raw"],
                        "helper": h,
                        "parallel_locator_note": "Roman numeral/book and canon locator retained in ref_raw; page_ref_int is the final fascicle-page number." if ref_kind == "parallel_locator" else None,
                    },
                }
            )

        for sr_order, sr in enumerate(entry.get("scripture", []), start=1):
            ref_raw, book_raw, book_norm, c1, v1, c2, v2, is_range = sr
            scripture_refs.append(
                {
                    "entry_key": entry["key"],
                    "ref_order": sr_order,
                    "ref_role": "citation",
                    "ref_raw": ref_raw,
                    "book_raw": book_raw,
                    "book_norm": book_norm,
                    "chapter_start": c1,
                    "verse_start": v1,
                    "chapter_end": c2,
                    "verse_end": v2,
                    "is_range": is_range,
                    "confidence": 0.94,
                    "raw_json": {
                        "source": "scripture_index_line",
                        "split_from": entry["raw"],
                        "ref_norm": f"{book_norm} {c1}" + (f",{v1}" if v1 is not None else "") + (f"-{c2}" if c2 and c2 != c1 and v2 is None else ""),
                    },
                }
            )

    evidence_files = [s(F[i]) for i in [195, 199, 202, 203, 205, 206, 207, 215, 216, 218, 706, 716, 717, 718]]
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": s(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "PO008 contains several distinct table sequences across fascicles; OCR file suffixes, printed table pages, and cited references are kept separate.",
                "The prior checkpoint missed the Greek table, the scripture-renvois table, the manuscript editorial table, the foreign-terms continuation in file 202, and the Bible-passages continuation in files 717-718.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "partial_recovery",
            "entries_status_reason": "Representative line items and material references were recovered for each detected index-like section. Dense tables contain many additional recoverable lines; this payload prioritizes corrected section boundaries, representative entries, scripture parsing, and locator semantics for the rerun.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "File 718 is split: its top block continues TABLE DES PASSAGES DE LA BIBLE, while the lower block begins TABLE DES CHAPITRES; only the Bible-passages material is serialized here.",
            "In the late TABLE ALPHABÉTIQUE, entries such as 'I. 48, 77' are parallel locators: I is the book, 48 is the canon, and 77 is the fascicle page used for target-file resolution.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": payload["generated_at"],
                "current_focus": "PO008 payload rebuilt and written",
                "completed": [
                    "verified candidate section boundaries against OCR reader",
                    "rebuilt helper request with per-reference page hints",
                    "corrected late alphabetical parallel-locator page semantics",
                    "assembled final payload",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "Dense sections remain partial representative recovery by design.",
                    "Chapter-table material after the top of file 718 was intentionally excluded.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-helper", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    if args.write_helper:
        build_helper()
    if args.assemble:
        assemble()
    if not args.write_helper and not args.assemble:
        parser.error("choose --write-helper and/or --assemble")


if __name__ == "__main__":
    main()

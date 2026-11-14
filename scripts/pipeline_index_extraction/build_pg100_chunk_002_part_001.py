#!/usr/bin/env python3
"""Build the bounded PG100 Nicephorus ELENCHUS fragment from OCR evidence."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG100/text"
PREFIX = "4ef1b812-c1b4-4fb5-bd3f-fe9565c4d626"
FILES = {number: SOURCE_ROOT / f"{PREFIX}-{number:03d}.txt" for number in range(9, 20)}
OWNED = [str(FILES[number]) for number in (13, 14, 15)]
OUTPUT = ROOT / "data/index_intermediate_payloads/PG100/chunks/section_002_part_001.json"

VOLUME_ID = "PG100"
SECTION_ID = "PG100:candidate-section:002"
CHUNK_ID = "PG100:chunk:002:001"
SECTION_KEY = "PG100:section:work_front:nicephorus_elenchus_operum:001"
FINGERPRINT = "5729d0ba67e26d9a3bb44375688e4bb0ae4a4a68651e04296eff0028663873e8"

NUMBERING = {
    "physical_file_fields": "physical_files_and_explicit_file_locators",
    "entry_number_system": "editorial",
    "numeric_equality_mapping_forbidden": True,
}


def page_body(number: int) -> str:
    root = ET.parse(FILES[number]).getroot()
    blocks = [
        " ".join((block.text or "").split())
        for block in root.findall("bloco")
        if block.attrib.get("tipo") == "texto_principal"
    ]
    return " ".join(block for block in blocks if block)


ROMANS = [
    "LXXXIII", "LXXXII", "LXXXI", "LXXX", "LXXIX", "LXXVIII", "LXXVII", "LXXVI",
    "LXXV", "LXXIV", "LXXIII", "LXXII", "LXXI", "LXX", "LXIX", "LXVIII", "LXVII",
    "LXVI", "LXV", "LXIV", "LXIII", "LXII", "LXI", "LX", "LIX", "LVIII", "LVII",
    "LVI", "LV", "LIV", "LIII", "LII", "LI", "L", "XLIX", "XLVIII", "XLVII",
    "XLVI", "XLV", "XLIV", "XLIII", "XLII", "XLI", "XL", "XXXIX", "XXXVIII",
    "XXXVII", "XXXVI", "XXXV", "XXXIV", "XXXIII", "XXXII", "XXXI", "XXX", "XXIX",
    "XXVIII", "XXVII", "XXVI", "XXV", "XXIV", "XXIII", "XXII", "XXI", "XX", "XIX",
    "XVIII", "XVII", "XVI", "XV", "XIV", "XIII", "XII", "XI", "X", "IX", "VIII",
    "VII", "VI", "V", "IV", "III", "II", "I",
]
ROMAN_START = re.compile(r"(?=(?<![A-Za-z])(?:" + "|".join(ROMANS) + r")\.\s)")


def split_capitula(text: str) -> list[str]:
    return [item.strip() for item in ROMAN_START.split(text) if item.strip()]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


entries: list[dict] = []


def add(text: str, sources: list[int], kind: str = "contents_entry", note: str | None = None) -> None:
    order = len(entries) + 1
    value = norm(text)
    entries.append(
        {
            "entry_key": f"{SECTION_ID}:{CHUNK_ID}:{order:03d}",
            "entry_order": order,
            "entry_raw": value,
            "target_raw": value,
            "target_file": None,
            "page_ref_raw": None,
            "page_ref_int": None,
            "page_ref_col": None,
            "note_raw": note,
            "normalized_target": value,
            "confidence": "medium",
            "raw_json": {
                "source_files": [str(FILES[number]) for number in sources],
                "entry_kind": kind,
            },
        }
    )


# The preamble is visibly separated by long dashes and crosses 013/014 only at
# "Nicepho-ris, / corumque scriptis".  These are separate table items.
add("TOMUS I.", [13], "contents_heading", "tome heading")
add("Præliminaria Interpretis.", [13], "contents_heading", "structural heading")
add("Præfatio generalis.", [13], "contents_entry")
add("Præfatio de iis quæ hoc volumine continentur.", [13], "contents_entry")
add("Dissertatio de Nicephoris, eorumque scriptis.", [13, 14], "contents_entry")
add("Vita (j) sancti Nicephori, patriarchæ Constantinopolitani, descripta ab Ignatio diacono, ejus discipulo.", [14], "contents_entry")
add("Oratio Theophanis presbyteri, habita in translatione ejus reliquiarum.", [14], "contents_entry")
add("Chronica synopsis sancti Nicephori, patriarchæ CP., aucta ac scripta ordine amorum digerens; ex ejusdem Nicephori vita, ex epistolis sancti Theodori Studitæ, aliorumque actorum monumentis.", [14], "contents_entry")
add("Veterum testimonia de sancto Nicephoro, patriarcha CP.", [14], "contents_entry")
add("Sancti Nicephori, patriarchæ Constantinopolitani, pro irreprehensibili et pura Christianorum fide capita LXXIII.", [14], "work_heading", "The subsequent notice says the chapter numeration was restored from Boivin's reconstruction.")
add("Hæc capitum distinctio sumpta est ex codice Colbertino antiquissimo: capitum autem argumenta toto opere perlecto resuluta sunt.", [14], "editorial_note")

body14 = page_body(14)
first_start = body14.index("I. Prooemium.")
second_heading = body14.index("Pars altera hujus Apologetici")
for item in split_capitula(body14[first_start:second_heading]):
    add(item, [14], "capitulum_entry")

add("Pars altera hujus Apologetici, in qua probatur ex sacris Scripturis, omnes gentes ab idolorum cultu virtute Christi liberares esse, et Ecclesiam universam errare non posse.", [14], "contents_heading", "structural division")
third_start = body14.index("XXXIV. An ea")
for item in split_capitula(body14[third_start:]):
    add(item, [14], "capitulum_entry")

body15 = page_body(15)
third_heading = body15.index("Apologeticæ tertia pars.")
for item in split_capitula(body15[:third_heading]):
    if item.startswith("LVIII.") or item.startswith("LIX.") or item.startswith("LX.") or item.startswith("LXI.") or item.startswith("LXII.") or item.startswith("LXIII.") or item.startswith("LXIV."):
        add(item, [15], "capitulum_entry")
add("Apologeticæ tertia pars.", [15], "contents_heading", "structural division")
third_start = body15.index("LXV. Quas rationes")
after_apologetic = body15.index("Præliminaria interpretis.")
for item in split_capitula(body15[third_start:after_apologetic]):
    add(item, [15], "capitulum_entry")

add("Præliminaria interpretis.", [15], "contents_heading", "structural heading")
appendix_start = after_apologetic + len("Præliminaria interpretis.")
dissertation_heading = "DISSERTATIO DE CONSTANTINI COPRONYMI"
dissertation_start = body15.index(dissertation_heading)
add(body15[appendix_start:dissertation_start], [15], "contents_entry")
antirrheticus_heading = "Antirrheticus liber, seu refutatio"
antirrheticus_start = body15.index(antirrheticus_heading)
first_chapter = body15.index("I. Prooemium.", antirrheticus_start)
add(body15[dissertation_start:antirrheticus_start], [15], "contents_heading", "dissertation heading")
add(body15[antirrheticus_start:first_chapter], [15], "work_heading")

# XXII is the only owned entry continued onto 016; the rest of 016 is context.
body16 = page_body(16)
continuation = body16[: body16.index("XXIII. Ad illa")]
antirrheticus_text = body15[first_chapter:] + " " + continuation
for item in split_capitula(antirrheticus_text):
    roman = item.split(".", 1)[0]
    sources = [15, 16] if roman == "XXII" else [15]
    add(item, sources, "capitulum_entry")


payload = {
    "schema_version": 2,
    "volume_id": VOLUME_ID,
    "section_id": SECTION_ID,
    "chunk_id": CHUNK_ID,
    "input_fingerprint": FINGERPRINT,
    "status": "complete",
    "physical_files": OWNED,
    "numbering_semantics": NUMBERING,
    "boundary_decisions": [
        {
            "physical_left_file": str(FILES[13]),
            "physical_right_file": str(FILES[14]),
            "decision": "joined_entry",
            "reason": "The table item 'Dissertatio de Nicepho ris,' at the end of file 013 continues as 'corumque scriptis.' at the beginning of file 014; it is emitted once with both files as source evidence.",
        },
        {
            "physical_left_file": str(FILES[14]),
            "physical_right_file": str(FILES[15]),
            "decision": "not_same_entry",
            "reason": "File 014 ends Capitulum LVII and file 015 begins the distinct Capitulum LVIII; the page seam does not split a single logical entry.",
        },
        {
            "physical_left_file": str(FILES[15]),
            "physical_right_file": str(FILES[16]),
            "decision": "joined_entry",
            "reason": "Capitulum XXII of the Antirrheticus begins on file 015 and continues with 'erunt geniti, sicut Filius.' on file 016; the entry is owned by file 015 and carries both sources.",
        },
    ],
    "works": [],
    "sections": [
        {
            "section_key": SECTION_KEY,
            "work_key": None,
            "scope_kind": "work_front",
            "index_kind": "ELENCHUS OPERUM",
            "heading_raw": "ELENCIUS OPERUM S. NICEPHORI PATRIARCHAE CPOL.",
            "heading_norm": "Elenchi operum S. Nicephori Patriarchae CPol.",
            "page_start": 17,
            "page_end": 22,
            "file_start": str(FILES[13]),
            "file_end": str(FILES[15]),
            "confidence": "medium",
            "raw_json": {
                "source_files": OWNED,
                "adjacent_context_files": [str(FILES[number]) for number in range(9, 20) if number not in (13, 14, 15)],
                "checked_files": [str(FILES[number]) for number in range(9, 20)],
                "searches_checked": [
                    "rg -n -i 'ELENCIUS OPERUM|TOMUS I|Praeliminaria Interpretis|Apologetic' teste/PG100/text"
                ],
                "numbering_evidence": [
                    "File 013 header: 17 NOTITIA 18.",
                    "File 015 header: 21 NOTITIA 22; it states that the following chapter numbers were restored by Cl. Boivin because the Colbertinus manuscript is mutilated here.",
                    "File 014 has only the running header S. NICEPHORUS PATRIARCHA CP.; its editorial pagination is inferred only from the consistent neighboring header sequence, never from the file suffix."
                ],
                "entries_status": "complete",
                "entries_status_reason": "All readable table items beginning on owned files 013-015 are serialized, including the individually numbered capita and the one continuation onto context file 016."
            },
            "entries": entries,
        }
    ],
    "notes": [
        "This bounded fragment records the Bandurius prospectus printed in the Nicephorus NOTITIA; it does not assemble the whole volume or locate the listed materials.",
        "The unnumbered long prose before ELENCIUS OPERUM on file 013 is explanatory prose and is not serialized as a contents entry.",
        "OCR wording, including apparent CER such as 'resuluta', 'aliconomae chijs', and 'capiibus', is retained in the extracted entries."
    ],
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUTPUT} ({len(entries)} entries)")

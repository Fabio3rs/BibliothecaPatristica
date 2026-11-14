#!/usr/bin/env python3
"""Build the canonical PG130 general-index payload from validated fragments."""

from __future__ import annotations

import copy
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME = "PG130"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
HELPER = ROOT / "data" / "index_payloads" / f"{VOLUME}_helper_output.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def file(seq: int) -> str:
    matches = list(TEXT.glob(f"*-{seq:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one PG130 file for physical suffix {seq}: {matches}")
    return str(matches[0])


def normalize(value: str | None) -> str:
    value = (value or "").replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return " ".join(re.findall(r"[A-Za-zÀ-ÿ]+", value.lower()))


def compact_helper(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    return {
        "entry_id": item.get("entry_id"),
        "status": item.get("status"),
        "ambiguity_reason": item.get("ambiguity_reason"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        },
    }


def helper_for_entry(entry: dict[str, Any], helpers: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Locator ids retain the OCR basename but omit the ``.txt`` extension.
    source = Path(entry["raw_json"]["source_files"][0]).stem
    page = entry.get("page_ref_int")
    if not isinstance(page, int):
        return None
    needle = normalize(entry.get("target_raw") or entry.get("entry_raw"))
    choices: list[tuple[float, dict[str, Any]]] = []
    for item in helpers:
        if source not in str(item.get("entry_id")):
            continue
        queries = ((item.get("debug") or {}).get("normalized_queries") or [])
        hints = ((item.get("debug") or {}).get("page_hints_used") or [])
        if page not in hints:
            continue
        score = max((SequenceMatcher(None, needle, normalize(query)).ratio() for query in queries), default=0.0)
        choices.append((score, item))
    if not choices:
        return None
    score, item = max(choices, key=lambda pair: pair[0])
    return item if score >= 0.62 else None


def attach_locator(entry: dict[str, Any], helpers: list[dict[str, Any]]) -> None:
    item = helper_for_entry(entry, helpers)
    if not item:
        return
    entry.setdefault("raw_json", {})["target_locator"] = compact_helper(item)
    best = item.get("best_candidate") or {}
    source = entry["raw_json"]["source_files"][0]
    if (
        item.get("status") == "resolved"
        and best.get("candidate_role") == "target_candidate"
        and best.get("file")
        and best.get("file") != source
    ):
        entry["target_file"] = best["file"]
        entry["confidence"] = "high" if float(best.get("probability") or 0) >= 0.8 else "medium"


WORK_INDEX_ROWS = [
    ("Titulus I. — Ratiocinatio syllogistica, qua Deus unus esse ejusdem naturæ : Verbum, ac Spiritum item, nempe Filium et Spiritum sanctum habere demonstratur. Ex oratione catechetica Gregorii Nyssæ pontificis.", 23),
    ("Titulus II. — De Patre et Filio et Spiritu sancto distincta simul atque conjuncta divinitatis ratio.", 29),
    ("Titulus III. — De Deo seu Divinitate communiter. Qui Deum, aut Divinitatem aut divinum Numen dicit, communi horum appellatione tres unius divinitatis hypostases, sive personas declarare.", 68),
    ("Titulus IV. — Divinam naturam comprehendi non posse.", 84),
    ("Titulus V. — De divina appellatione.", 101),
    ("Titulus VI. — De opificio Dei.", 105),
    ("Titulus VII. — De divina humanatione.", 110),
    ("Titulus VIII. — Contra Hebræos, ex libro, ut fertur, Nysseni, De cognitione Dei.", 135),
    ("Titulus IX. — Contra Simonem Samarensem, et Marcionem Ponticum, et Manetem Persam, et Manichæos.", 159),
    ("Titulus X. — Adversus Sabellium ex S. Cyrillo.", 170),
    ("Titulus XI. — In Arianos, magni Athanasii.", 172),
    ("Titulus XII. — Contra Spiritus sancti impugnatores.", 349),
    ("Titulus XIII. — Photii patriarchæ, contra Latinos, quod ex Patre tantum procedit Spiritus sanctus, non et ex Filio.", 444),
    ("Titulus XIV. — Contra Apollinarium.", 444),
    ("Titulus XV. — Contra Nestorianos.", 472),
    ("Titulus XVI. — Contra Monophysitas, quorum auctor Eutyches archimandrita.", 512),
    ("Titulus XVII. — Contra Aphthartodocetas.", 550),
    ("Titulus XVIII. — Contra eos qui dicunt Deum Verbum impatibiliter esse passum.", 552),
    ("Titulus XIX. — Contra Aquaeas qui aiunt ut Patre minorem; ignorare diem et horam consummationis mundi.", 559),
    ("Titulus XX. — Contra Origenem.", 559),
    ("Titulus XXI. — Contra Sergium, Pyrrhum et Paulum, patriarchas Constantinopolitis, qui dicebant unam in Christo esse voluntatem et energiam; qui et Monothelitae dicuntur.", 559),
    ("Titulus XXII. — Contra Iconoclastas.", 588),
    ("Titulus XXIII. — Contra Armenios.", 593),
    ("Titulus XXIV. — Contra eos qui dicuntur Pauliciani.", 601),
    ("Titulus XXV. — De cruce, de sancto baptismate et de transmutatione Dominici corporis et sanguinis.", 628),
    ("Titulus XXVI. — Contra Massalianos.", 643),
    ("Titulus XXVII. — Contra Bogomilos.", 651),
    ("Titulus XXVIII. — Contra Saracenos.", 672),
    ("Appendix. — Photii patriarchae Constantinopolitani ex epistola ad Michaelem Bulgarie principem de septem synodis acuveniencis.", 686),
]


def work_index_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for order, (literal, target_seq) in enumerate(WORK_INDEX_ROWS, start=1):
        entries.append(
            {
                "entry_order": order,
                "entry_raw": literal,
                "target_raw": literal,
                "target_file": file(target_seq),
                "page_ref_raw": None,
                "page_ref_int": None,
                "page_ref_col": None,
                "note_raw": None,
                "normalized_target": literal,
                "confidence": "high",
                "raw_json": {
                    "source_files": [file(22) if order <= 18 else file(23)],
                    "target_evidence": "Direct local OCR title occurrence; identified from the same Latin TITULUS literal, not from a file suffix.",
                },
            }
        )
    return entries


def main() -> None:
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    assembled = assembled_document["data"]
    helper_entries = json.loads(HELPER.read_text(encoding="utf-8")).get("entries") or []
    stable_sections = {section["section_key"]: copy.deepcopy(section) for section in assembled["sections"]}

    front_key = "PG130:section:volume_front:elenchus:001"
    closing_key = "PG130:section:volume_end:ordo_rerum:002"
    canonical_work_key = "PG130:work:panoplia_dogmatica_ad_alexium_comnenum"

    front = stable_sections[front_key]
    front["work_key"] = None
    front["heading_raw"] = "ELENCHUS / AUCTORUM ET OPERUM QUI IN HOC TOMO CXXX CONTINENTUR"
    front["heading_norm"] = "Elenchus auctorum et operum qui in hoc tomo CXXX continentur"
    front.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    front["raw_json"]["editorial_page_note"] = "No printed editorial page number occurs on the ELENCHUS sheet; its physical file is the anchor."
    front_entry = front["entries"][1]
    front_entry["target_file"] = file(11)
    front_entry["page_ref_col"] = "col."
    front_entry["raw_json"]["target_evidence"] = "File 011 directly opens with the Greek title ΠΑΝΟΠΛΙΑ ΔΟΓΜΑΤΙΚΗ and names Euthymius Zigabenus."

    closing = stable_sections[closing_key]
    closing["work_key"] = canonical_work_key
    closing["index_kind"] = "ORDO RERUM"
    closing["heading_raw"] = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
    closing["heading_norm"] = "Ordo rerum quae in hoc tomo continentur"
    closing.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    closing["raw_json"]["target_locator_policy"] = "Only resolved helper candidates with a target_candidate role and an independent non-index physical file were attached; ambiguous/index-page results remain unanchored."
    for entry in closing["entries"]:
        attach_locator(entry, helper_entries)

    work_index = {
        "section_key": "PG130:section:work_front:index_libri:003",
        "work_key": canonical_work_key,
        "scope_kind": "work_front",
        "index_kind": "INDEX LIBRI",
        "heading_raw": "INDEX LIBRI PANOPLIA DOGMATICA INSCRIPTI.",
        "heading_norm": "Index libri Panoplia dogmatica inscripti",
        "page_start": 31,
        "page_end": 34,
        "file_start": file(22),
        "file_end": file(23),
        "confidence": "high",
        "raw_json": {
            "evidence": [
                "31 EUTHYMII ZIGABENI 32",
                "ΠΙΝΑΞ ΤΗΣ ΒΙΒΛΟΥ ΤΗΣ ΕΠΟΝΟΜΑΖΟΜΕΝΗΣ ΔΟΓΜΑΤΙΚΗΣ ΠΑΝΟΠΛΙΑΣ.",
                "INDEX LIBRI PANOPLIA DOGMATICA INSCRIPTI.",
                "33 PANOPLIA DOGMATICA. — TIT. I. 34",
            ],
            "bilingual_layout_note": "The Greek and Latin title lists are parallel on the OCR sheets; entries preserve the readable Latin line-by-line inventory and retain the original OCR literals.",
        },
        "entries": work_index_entries(),
    }

    work = {
        "work_key": canonical_work_key,
        "work_order": 1,
        "author_raw": "EUTHYMIUS ZIGABENUS.",
        "title_raw": "Panoplia dogmatica ad Alexium Comnenum",
        "title_norm": "Panoplia dogmatica ad Alexium Comnenum",
        "start_page": 1,
        "end_page": 1362,
        "start_file": file(11),
        "end_file": file(687),
        "source_section_key": front_key,
        "confidence": "high",
        "raw_json": {
            "assembled_fragments_consumed": True,
            "merged_validated_work_records": copy.deepcopy(assembled["works"]),
            "anchor_resolution": "resolved by direct local OCR opening and closing evidence",
            "anchor_evidence": [
                "File 011 directly opens ΠΑΝΟΠΛΙΑ ΔΟΓΜΑΤΙΚΗ and names Euthymius Zigabenus; its footer carries the editorial page 1.",
                "Files 013 onward begin the recurring PANOPLIA DOGMATICA running-header body range, while files 022–023 show the internal INDEX LIBRI and the direct TITULUS I opening.",
                "File 687 has the logical header 1361 PANOPLIA DOGMATICA. 1362 and prints the concluding Tergobyst edition verses; file 688 begins the separate ORDO RERUM.",
            ],
            "stable_work_merge_note": "The two validated fragment work records are alternate front/closing representations of this one locally attested work; their full stable objects are retained here rather than treated as two distinct works.",
        },
    }

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(TEXT),
            "volume_label": "Patrologiæ Græcæ Tomus CXXX",
            "notes": [
                "Front ELENCHUS in file 010; internal INDEX LIBRI in files 022–023; closing ORDO RERUM in files 688–692.",
                "The volume contains one principal work: Euthymius Zigabenus, Panoplia dogmatica ad Alexium Comnenum.",
            ],
            "scan_summary": {"front_index_files": [10], "work_index_files": [22, 23], "end_index_files": [688, 689, 690, 691, 692]},
        },
        "works": [work],
        "sections": [front, work_index, closing],
        "notes": [
            "Consumed all validated sections and all 344 validated entries from assembled_fragments.json.",
            "The validated fragment work records were reconciled as alternate metadata for the single Panoplia work and are preserved verbatim in the canonical work raw_json.",
            "The closing ORDO RERUM is a contents table, not an alphabetical or analytical index.",
            "OCR file suffixes remain physical locators; all printed page and column literals remain distinct editorial references.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

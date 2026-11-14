#!/usr/bin/env python3
"""Rebuild PG123 from its validated chunks and direct local OCR anchors."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG123"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
REPORT = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "fragment_consumption_report.json"

sys.path.insert(0, str(ROOT))
from tools.indexing.index_fragment_assembly import verify_payload_consumes_fragments


def file_for(suffix: int) -> str:
    matches = list(TEXT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one PG123 OCR file ending in {suffix:03d}: {matches}")
    return str(matches[0])


def keyed(items: list[dict], key: str) -> dict:
    for item in items:
        if item[key] == key:
            return item
    raise KeyError(key)


def add_target(section: dict, order: int, target_file: str, evidence: str) -> None:
    entry = next(entry for entry in section["entries"] if entry["entry_order"] == order)
    entry["target_file"] = target_file
    entry.setdefault("raw_json", {})["target_file_evidence"] = evidence


def mark_consumed(data: dict) -> None:
    for work in data["works"]:
        work.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
    for section in data["sections"]:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True


def main() -> None:
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    if assembled_document.get("status") != "complete":
        raise ValueError("PG123 assembled fragments are not complete")
    stable = copy.deepcopy(assembled_document["data"])
    if (len(stable["works"]), len(stable["sections"]), sum(len(s["entries"]) for s in stable["sections"])) != (7, 8, 151):
        raise ValueError("unexpected PG123 validated-fragment counts")
    mark_consumed(stable)

    f011, f073, f074, f078, f252 = (file_for(n) for n in (11, 73, 74, 78, 252))
    f349, f350, f587, f588, f715, f716 = (file_for(n) for n in (349, 350, 587, 588, 715, 716))

    works = {work["work_key"]: work for work in stable["works"]}
    dissertation = works["PG123:work:fr_j_f_maria_de_rubeis_dissertatio_de_theophylacti_aetate_gestis_scriptis_ac_doctrina"]
    dissertation.update({"start_page": 9, "end_page": 130, "start_file": f011, "end_file": f073, "confidence": "high"})
    dissertation["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "File 011 directly prints the full De Rubeis dissertation title and begins division I.",
            "File 073 has the 129/130 De Theophylacti gestis et scriptis header; file 074 directly begins Philippi Montani Præfatio in Evangelia.",
            "The earlier claimed physical file 007 is volume preliminaries, not the dissertation opening.",
        ],
    }

    container = works["PG123:work:theophylactus_bulgariae_archiepiscopus_opera"]
    container.update({"start_page": None, "end_page": None, "start_file": None, "end_file": None, "confidence": "medium"})
    container["raw_json"]["structural_note"] = (
        "THEOPHYLACTI OPERA is an ELENCHUS division/author container, not one locally anchored composite work; its four component works are retained separately."
    )

    ordo_dissertation = works["PG123:work:de_rubeis_dissertatio_theophylacti"]
    ordo_dissertation.update({"start_page": 9, "end_page": 130, "start_file": f011, "end_file": f073, "confidence": "high"})
    ordo_dissertation["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "This validated ORDO RERUM representation denotes the same dissertation directly opened in file 011 and ending in the 129/130 range in file 073.",
            "It is retained under its stable key rather than being silently dropped or collapsed into the ELENCHUS representation.",
        ],
    }

    matthew = works["PG123:work:enarratio_matthei"]
    matthew.update({"start_page": 131, "end_page": 488, "start_file": f074, "end_file": f252, "confidence": "high"})
    matthew["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "File 074 directly begins PHILIPPI MONTANI PRÆFATIO IN EVANGELIA at editorial 131/132, the front matter listed for this commentary.",
            "File 078 directly gives the bilingual Matthæi title; files 078 onward establish the body range.",
            "File 252 contains Commentariorum Theophylacti in Evangelium Matthaei finis before the direct Marci title on the same physical file.",
        ],
        "declared_title_page_reference": 143,
    }

    mark = works["PG123:work:enarratio_marci"]
    mark.update({"start_page": 487, "end_page": 682, "start_file": f252, "end_file": f349, "confidence": "high"})
    mark["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "File 252 directly prints ENARRATIO IN EVANGELIUM MARCI and begins Vita B. Marci inside the 487/488 scan.",
            "File 349 has the 681/682 Marci header and explicit Commentariorum Theophylacti in Evangelium Marci finis.",
        ],
    }

    luke = works["PG123:work:enarratio_lucae"]
    luke.update({"start_page": 683, "end_page": 1126, "start_file": f350, "end_file": f587, "confidence": "high"})
    luke["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "File 350 directly combines the 683/684 header, ENARRATIO IN EVANGELIUM LUCÆ, and VITA SANCTI LUCÆ.",
            "File 587 has the 1125/1126 Lucæ header and explicit Theophylacti in Evangelium Lucæ Commentarii finis.",
            "The previously claimed file 421 is a later Lucae body page, not the opening.",
        ],
    }

    john = works["PG123:work:enarratio_joannis"]
    john.update({"start_page": 1127, "end_page": 1346, "start_file": f588, "end_file": f716, "confidence": "medium"})
    john["raw_json"]["anchor_resolution"] = {
        "status": "resolved",
        "evidence": [
            "File 588 directly prints ENARRATIO IN EVANGELIUM JOANNIS and begins Vita Joannis in the 1127/1128 scan.",
            "Files 713-716 retain Joannis commentary body; file 715 has the 1345/1346 running header and file 717 begins the separate ORDO RERUM.",
            "The final physical body file 716 has a contradictory OCR header literal (1247 ... 1241), so the final reliable editorial pair 1345/1346 is preserved from file 715 rather than inferred from a filename.",
        ],
    }

    sections = stable["sections"]
    front = keyed(sections, "section_key") if False else next(s for s in sections if s["section_key"] == "PG123:section:volume_front:elenchus:001")
    front["confidence"] = "high"
    front["raw_json"]["scope_note"] = "Direct OCR in file 010 supplies the complete volume-front ELENCHUS; its references are editorial columns/pages, not OCR-file suffixes."
    add_target(front, 2, f011, "direct dissertation title page in file 011")
    add_target(front, 4, f078, "direct bilingual Matthæi work title in file 078")
    add_target(front, 5, f252, "direct Marci work title in file 252")
    add_target(front, 6, f350, "direct Lucæ work title in file 350")
    add_target(front, 7, f588, "direct Joannis work title in file 588")

    dissertation_section = next(s for s in sections if s["section_key"] == "PG123:section:work_front:de_rubeis_dissertatio:001")
    for entry in dissertation_section["entries"]:
        sources = entry.get("raw_json", {}).get("source_files") or []
        if sources:
            entry["target_file"] = sources[0]
            entry["raw_json"]["target_file_evidence"] = "direct structural heading/prose unit in the cited OCR source file"

    candidate = next(s for s in sections if s["section_key"] == "PG123:candidate-section:001")
    candidate["raw_json"]["retained_reason"] = "Validated continuation-only checkpoint retained under its stable key; it is explicitly not a second ELENCHUS and has no line items."

    for section in sections:
        if section["section_key"].startswith("PG123:section:ordo_rerum:"):
            section["scope_kind"] = "volume_end"
            section["raw_json"]["scope_note"] = (
                "This stable slice belongs to the single closing ORDO RERUM in files 717-720; the work_key preserves its validated thematic grouping."
            )

    payload = {
        "volume": {
            "volume_id": VOLUME,
            "collection": "PG",
            "source_root": str(TEXT),
            "volume_label": "PATROLOGIÆ GRÆCÆ TOMUS CXXIII",
            "notes": [
                "File 010 is the complete volume-front ELENCHUS; files 717-720 are the closing ORDO RERUM.",
                "OCR file suffixes are physical locators only. Editorial page/column literals remain separate fields.",
            ],
        },
        "works": stable["works"],
        "sections": sections,
        "notes": [
            *stable.get("notes", []),
            "Consumed every validated object from assembled_fragments.json: 7 works, 8 sections, and 151 line-by-line entries.",
            "Resolved the four previous work-anchor reruns from direct PG123 OCR opening/closing evidence; no work_anchor_rerun marker remains.",
            "The closing ORDO RERUM is retained as volume-end contents matter; alphabetical and analytical index extraction remains out of scope.",
        ],
    }

    work_keys = {work["work_key"] for work in payload["works"]}
    dangling = [section["section_key"] for section in payload["sections"] if section.get("work_key") is not None and section["work_key"] not in work_keys]
    if dangling:
        raise ValueError(f"dangling section work keys: {dangling}")
    section_keys = [section["section_key"] for section in payload["sections"]]
    entry_keys = [entry.get("entry_key") for section in payload["sections"] for entry in section["entries"] if entry.get("entry_key")]
    if len(section_keys) != len(set(section_keys)) or len(entry_keys) != len(set(entry_keys)):
        raise ValueError("duplicate stable section or entry keys")
    if sum(len(section["entries"]) for section in payload["sections"]) != 151:
        raise ValueError("validated entries were lost")

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = verify_payload_consumes_fragments(payload, assembled_document)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report.get("status") != "ok":
        raise ValueError(f"fragment consumption verification failed: {report}")


if __name__ == "__main__":
    main()

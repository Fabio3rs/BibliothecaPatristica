#!/usr/bin/env python3
"""Consume PG120 validated fragments and resolve directly inspected work anchors."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PG120"
PAYLOAD_PATH = ROOT / "data/index_payloads/PG120_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PG120/assembled_fragments.json"
TEXT_ROOT = ROOT / "teste/PG120/text"


def find_file(suffix: int) -> str:
    matches = sorted(TEXT_ROOT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise ValueError(f"Expected one PG120 OCR file with suffix {suffix}: {matches}")
    return str(matches[0])


def merge_unique(values: list[str], additions: list[str]) -> list[str]:
    for value in additions:
        if value not in values:
            values.append(value)
    return values


def resolve_anchor(
    work: dict,
    *,
    suffix: int,
    evidence: str,
    start_page: int | None = None,
) -> None:
    work["start_file"] = find_file(suffix)
    if start_page is not None:
        work["start_page"] = start_page
    raw = work.setdefault("raw_json", {})
    raw.pop("work_anchor_rerun", None)
    raw["anchor_verification"] = {
        "status": "resolved",
        "source": "direct PG120 OCR inspection",
        "evidence": evidence,
    }


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    fragments = json.loads(FRAGMENTS_PATH.read_text())["data"]

    works = {work["work_key"]: work for work in payload["works"]}
    fragment_works = {work["work_key"]: work for work in fragments["works"]}
    missing_works = set(fragment_works) - set(works)
    if missing_works:
        raise ValueError(f"Validated works missing from payload: {sorted(missing_works)}")
    for work_key in fragment_works:
        works[work_key].setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    sections = {section["section_key"]: section for section in payload["sections"]}
    for fragment_section in fragments["sections"]:
        key = fragment_section["section_key"]
        if key not in sections:
            section = fragment_section
            section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
            if key == "PG120:candidate-section:002:ordo_rerum:001":
                section["raw_json"]["validated_scope_kind_raw"] = section["scope_kind"]
                section["scope_kind"] = "volume_end"
            payload["sections"].append(section)
            sections[key] = section
            continue
        if key in {
            "PG120:candidate-section:001",
            "PG120:candidate-section:002:ordo_rerum:001",
        }:
            if sections[key]["entries"] != fragment_section["entries"]:
                raise ValueError(f"Validated entries drifted from fragment: {key}")
        elif sections[key] != fragment_section:
            raise ValueError(f"Validated section drifted from fragment: {key}")
        sections[key].setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        if key == "PG120:candidate-section:002:ordo_rerum:001":
            sections[key]["raw_json"]["validated_scope_kind_raw"] = sections[key]["scope_kind"]
            sections[key]["scope_kind"] = "volume_end"

    resolve_anchor(
        works["pg120_anonymus_s_nili_discipulus"],
        suffix=11,
        evidence="File 011 has the explicit ANONYMI / VITA S. NILI JUNIORIS title and the opening Commentarius praevius; file 010 is ELENCHUS only.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:001"],
        suffix=11,
        start_page=9,
        evidence="File 011 is the direct Vita S. Nili title/opening; the closing ORDO gives the editorial reference 9.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:002"],
        suffix=89,
        evidence="File 089 begins the Theodorus Iconii material at the 165/166 sheet transition, independently of the ELENCHUS occurrence.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:007"],
        suffix=114,
        evidence="File 114, headed 215/216, has the author block EPIPHANII MONACHI ET PRESBYTERI and the direct bilingual Vita/Acta Andreae opening; 115 is a running-body page.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:011"],
        suffix=150,
        evidence="File 150 contains the SYMEON JUNIOR heading and his Notitia immediately after the Demetrius notice; it is a mixed sheet but a direct local opening.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:014"],
        suffix=260,
        evidence="File 260, headed 507/508, contains EJUSDEM SYMEONIS ... DIVINORUM AMORUM ... LIBER SINGULARIS and CAPUT PRIMUM; 261 is only a running-header body page.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:017"],
        suffix=353,
        evidence="File 353 directly changes from Capita moralia to a Greek title LOGOS PERI PISTEOS KAI DIDASKALIAS, with the following 695/696 sheet continuing it. The inventory literal 695 is retained as the entry reference.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:022"],
        suffix=374,
        start_page=735,
        evidence="File 374 has the 735/736 header and explicit MICHAELIS CERULARII ... EDICTUM SYNODALE title; closing ORDO independently lists EDICTUM SYNODALE at 735. The front ELENCHUS literal 758 is a conflicting index reference.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:027"],
        suffix=432,
        start_page=851,
        evidence="File 431 is the Monitum ad tres Centurias and file 432, headed 851/852, directly opens PRACTICORUM CAPITUM CENTURIA PRIMA. Closing ORDO confirms the three-centuria sequence begins at 851; the ELENCHUS literal 954 points to a later third-centuria sheet.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:028"],
        suffix=511,
        evidence="File 511 directly opens DE SALUTATIONE MANUALI and the bilingual Nicetas text. The ELENCHUS editorial literal 1009 is retained because the estimator pair is 1009/1010 and the sheet does not decide the side.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:032"],
        suffix=532,
        evidence="File 532, headed 1051/1052, has the direct VITA S. DOROTHEI JUNIORIS SIVE IN CHILIOCOMO title and Caput I; 533 is a running-header page.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:033"],
        suffix=544,
        evidence="File 544, headed 1075/1076, directly has JOANNIS EUCHAITARUM ... SERMO IN AUGUSTISSIMAM SANCTISSIMAE DEIPARAE DORMITIONEM.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:034"],
        suffix=655,
        evidence="Before the ORDO heading on the same mixed file 655, the OCR has ADDENDA / JOANNIS EUCHAITENSIS EX VITA SANCTAE EUSEBIAE FRAGMENTUM; this is source text, not the later index occurrence.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:035"],
        suffix=563,
        start_page=1114,
        evidence="File 563, headed 1113/1114, directly opens JOANNIS METROPOLITANI EUCHAITENSIS VERSUS IAMBICI IN PRINCIPALIUM FESTORUM ...; closing ORDO gives 1114. The front inventory literal 1119 describes the later commentary/programma beginning.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:036"],
        suffix=607,
        evidence="File 607 is the direct Joannes Xiphilinus opening: NOTITIA followed by ORATIONES ALIQUOT; adjacent sheets establish its 1201/1202 editorial position.",
    )
    resolve_anchor(
        works["PG120:candidate-section:001:work:038"],
        suffix=651,
        evidence="File 651 has the direct Greek and Latin DOMINICA OMNIUM SANCTORUM heading. Its visible 1389/1390 digits are CER within the 1289/1290 neighborhood established by files 650 and 652, so the inventory reference 1289 is retained.",
    )

    fragment_notes = fragments.get("notes", [])
    payload["notes"] = merge_unique(payload.get("notes", []), fragment_notes)
    payload["notes"] = merge_unique(
        payload["notes"],
        [
            "Consumed all 39 validated works, both validated sections, and all 314 validated entries from assembled_fragments.json; the closing validated ORDO section is retained under its stable candidate key.",
            "Resolved the 16 requested work-anchor reviews by direct inspection of the cited PG120 OCR files and their adjacent sheets; all prior work_anchor_rerun markers were removed only where a local textual opening was directly present.",
        ],
    )

    work_keys = {work["work_key"] for work in payload["works"]}
    for section in payload["sections"]:
        if section["work_key"] is not None and section["work_key"] not in work_keys:
            raise ValueError(f"Dangling section work key: {section['section_key']}")
    pending = [
        work["work_key"]
        for work in payload["works"]
        if work.get("raw_json", {}).get("work_anchor_rerun")
    ]
    if pending:
        raise ValueError(f"Unprocessed work-anchor reruns: {pending}")

    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG005/text"
PAYLOAD_PATH = ROOT / "data/index_payloads/PG005_indices.json"
ASSEMBLED_PATH = (
    ROOT / "data/index_intermediate_payloads/PG005/assembled_fragments.json"
)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def confidence_label(value: object) -> str:
    if isinstance(value, str) and value in {"high", "medium", "low"}:
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "medium"
    if number >= 0.85:
        return "high"
    if number >= 0.6:
        return "medium"
    return "low"


def general_entry(entry: dict) -> dict:
    if "target_raw" in entry:
        raw_json = copy.deepcopy(entry.get("raw_json") or {})
        raw_json.setdefault("validated_fragment_entry_key", entry.get("entry_key"))
        return {
            "entry_order": entry["entry_order"],
            "entry_raw": entry.get("entry_raw", ""),
            "target_raw": entry.get("target_raw"),
            "target_file": entry.get("target_file"),
            "page_ref_raw": entry.get("page_ref_raw"),
            "page_ref_int": entry.get("page_ref_int"),
            "page_ref_col": entry.get("page_ref_col"),
            "note_raw": entry.get("note_raw"),
            "normalized_target": entry.get("normalized_target"),
            "confidence": confidence_label(entry.get("confidence")),
            "raw_json": raw_json,
        }

    raw_json = copy.deepcopy(entry.get("raw_json") or {})
    raw_json["validated_fragment_entry_key"] = entry.get("entry_key")
    raw_json["fragment_entry_kind"] = entry.get("entry_kind")
    raw_json["fragment_context_raw"] = entry.get("context_raw")
    raw_json["fragment_lemma_norm"] = entry.get("lemma_norm")
    raw_json["fragment_lemma_sort"] = entry.get("lemma_sort")
    raw_json["fragment_editorial_anchor_file"] = entry.get("editorial_anchor_file")
    raw_json["fragment_section_start_file"] = entry.get("section_start_file")
    page = entry.get("inferred_printed_page")
    return {
        "entry_order": entry["entry_order"],
        "entry_raw": entry.get("entry_raw", ""),
        "target_raw": entry.get("lemma_raw"),
        "target_file": entry.get("target_file_best"),
        "page_ref_raw": str(page) if page is not None else None,
        "page_ref_int": page,
        "page_ref_col": None,
        "note_raw": None,
        "normalized_target": entry.get("lemma_display"),
        "confidence": confidence_label(entry.get("confidence")),
        "raw_json": raw_json,
    }


def entry_lookup_key(entry: dict) -> tuple[str, str]:
    raw = normalize_text(entry.get("entry_raw"))
    page = str(entry.get("page_ref_int") or "")
    return raw, page


def apply_anchor(
    work: dict,
    *,
    start_page: int,
    end_page: int,
    start_suffix: int,
    end_suffix: int,
    evidence: list[str],
) -> None:
    files = sorted(SOURCE_ROOT.glob(f"*-{start_suffix:03d}.txt"))
    end_files = sorted(SOURCE_ROOT.glob(f"*-{end_suffix:03d}.txt"))
    if len(files) != 1 or len(end_files) != 1:
        raise RuntimeError(
            f"Cannot resolve unique OCR files for {work['work_key']}: "
            f"{start_suffix}, {end_suffix}"
        )
    work["start_page"] = start_page
    work["end_page"] = end_page
    work["start_file"] = str(files[0])
    work["end_file"] = str(end_files[0])
    raw_json = copy.deepcopy(work.get("raw_json") or {})
    raw_json.pop("work_anchor_rerun", None)
    raw_json.pop("deterministic_anchor_reconciliation", None)
    raw_json["agent_anchor_review"] = {
        "status": "resolved",
        "method": "direct_ocr_and_neighbor_inspection",
        "evidence": evidence,
        "shared_boundary_allowed": start_suffix == end_suffix
        or any("shared" in item.casefold() for item in evidence),
    }
    work["raw_json"] = raw_json


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled = json.loads(ASSEMBLED_PATH.read_text(encoding="utf-8"))
    stable = assembled["data"]

    old_sections = copy.deepcopy(payload["sections"])
    old_entries = [
        entry for section in old_sections for entry in section.get("entries", [])
    ]
    old_entry_lookup: dict[tuple[str, str], list[dict]] = {}
    for entry in old_entries:
        old_entry_lookup.setdefault(entry_lookup_key(entry), []).append(entry)

    stable_sections = []
    stable_entry_keys: set[str] = set()
    canonical_ignatius = "PG005-S-IGNATIUS-MARTYR"
    stable_ignatius = stable["works"][0]["work_key"]

    for source_section in stable["sections"]:
        section = copy.deepcopy(source_section)
        section["work_key"] = (
            canonical_ignatius
            if section.get("work_key") == stable_ignatius
            else section.get("work_key")
        )
        if section["section_key"] == "PG005:candidate-section:002":
            section.update(
                {
                    "scope_kind": "volume_end",
                    "index_kind": "ORDO RERUM",
                    "heading_raw": "ORDO RERUM (continuatio et finis)",
                    "heading_norm": "ordo-rerum-continuatio-et-finis",
                    "page_start": 1503,
                    "page_end": 1504,
                    "file_start": str(
                        next(SOURCE_ROOT.glob("*-758.txt"))
                    ),
                    "file_end": str(next(SOURCE_ROOT.glob("*-758.txt"))),
                    "confidence": "high",
                }
            )
            section_raw = copy.deepcopy(section.get("raw_json") or {})
            section_raw["normalization_note"] = (
                "Stable alphabetical-shaped fragment normalized into the general "
                "ORDO RERUM entry schema; it remains a contents table, not an "
                "alphabetical index."
            )
            section["raw_json"] = section_raw

        normalized_entries = []
        for fragment_entry in section.get("entries", []):
            key = fragment_entry.get("entry_key")
            if key:
                stable_entry_keys.add(key)
            entry = general_entry(fragment_entry)
            matches = old_entry_lookup.get(entry_lookup_key(entry), [])
            located = [match for match in matches if match.get("target_file")]
            if len({match["target_file"] for match in located}) == 1:
                entry["target_file"] = located[0]["target_file"]
                entry["raw_json"]["prior_payload_target_match"] = {
                    "target_file": located[0]["target_file"],
                    "evidence": "exact OCR entry literal and editorial reference",
                }
            normalized_entries.append(entry)

        section["entries"] = normalized_entries
        section["confidence"] = confidence_label(section.get("confidence"))
        stable_sections.append(section)

    polycarpus_section = next(
        section
        for section in old_sections
        if section["section_key"] == "PG005:work_front:prolegomena:polycarpus"
    )
    payload["sections"] = [
        stable_sections[0],
        stable_sections[1],
        polycarpus_section,
        stable_sections[2],
        stable_sections[3],
    ]

    works = {work["work_key"]: work for work in payload["works"]}
    stable_work = stable["works"][0]
    ignatius = works[canonical_ignatius]
    ignatius.update(
        {
            "author_raw": stable_work["author_raw"],
            "title_raw": stable_work["title_raw"],
            "title_norm": stable_work["title_norm"],
            "source_section_key": "PG005:section:volume_front:elenchus:001",
        }
    )
    ignatius_raw = copy.deepcopy(ignatius.get("raw_json") or {})
    ignatius_raw["validated_fragment_work"] = {
        "fragment_work_key": stable_ignatius,
        "source_files": stable_work["raw_json"]["source_files"],
        "evidence": stable_work["raw_json"]["evidence"],
        "entry_kind": stable_work["raw_json"]["entry_kind"],
        "canonicalized_to": canonical_ignatius,
    }
    ignatius["raw_json"] = ignatius_raw

    apply_anchor(
        ignatius,
        start_page=9,
        end_page=995,
        start_suffix=9,
        end_suffix=504,
        evidence=[
            "File 009 is the direct Ignatius title page, not an inventory hit.",
            "File 504 finishes Ignatius at column 995 and opens Polycarpus lower on the shared scan.",
        ],
    )
    apply_anchor(
        works["PG005-S-POLYCARPUS-MARTYR-SMYRNAEORUM-EPISCOPUS"],
        start_page=997,
        end_page=1045,
        start_suffix=504,
        end_suffix=529,
        evidence=[
            "File 504 directly opens S. POLYCARPUS MARTYR and DE EPISTOLA S. POLYCARPI.",
            "File 529 closes the Polycarp material and opens Evaristus on the shared boundary at column 1045.",
        ],
    )
    apply_anchor(
        works["PG005-SIXTUS-I-PAPA"],
        start_page=1073,
        end_page=1079,
        start_suffix=543,
        end_suffix=546,
        evidence=[
            "File 543 has the logical header 1073 SIXTI I PAPÆ EPISTOLÆ 1074 and a direct Sixtus opening.",
            "File 546 concludes Decretum Sixti and opens Telesphorus on the shared column 1079.",
        ],
    )
    apply_anchor(
        works["PG005-PIUS-I-PAPA"],
        start_page=1093,
        end_page=1127,
        start_suffix=553,
        end_suffix=570,
        evidence=[
            "File 553 directly opens PIUS I PAPA, NOTITIA and DISSERTATIO DE VITA ET SCRIPTIS S. PII I PAPÆ under columns 1093-1094.",
            "The title occurrence in closing file 756 is an ORDO RERUM source and was rejected as a body anchor.",
        ],
    )
    apply_anchor(
        works["PG005-S-PAPIAS-HIERAPOLITANUS-EPISCOPUS"],
        start_page=1249,
        end_page=1255,
        start_suffix=632,
        end_suffix=636,
        evidence=[
            "File 632 directly opens S. PAPIAS HIERAPOLITANUS EPISCOPUS and NOTITIA.",
            "The generic NOTITIA hit in file 579 belongs to Melito and was rejected.",
        ],
    )
    apply_anchor(
        works["PG005-S-HEGESIPPUS"],
        start_page=1305,
        end_page=1325,
        start_suffix=658,
        end_suffix=669,
        evidence=[
            "File 658 directly opens S. HEGESIPPUS and NOTITIA.",
            "The generic NOTITIA hit in file 579 belongs to Melito and was rejected.",
        ],
    )
    apply_anchor(
        works["PG005-RHODON"],
        start_page=1331,
        end_page=1334,
        start_suffix=672,
        end_suffix=674,
        evidence=[
            "File 672 directly opens RHODON NOTITIA at columns 1331-1332.",
            "Files 673-674 carry RHODON FRAGMENTA at columns 1333-1334; Maximus opens in file 675.",
        ],
    )
    apply_anchor(
        works["PG005-MAXIMUS-HIEROSOLYMORUM-EPISCOPUS"],
        start_page=1337,
        end_page=1354,
        start_suffix=675,
        end_suffix=683,
        evidence=[
            "File 675 directly opens MAXIMUS HIEROSOLYMORUM EPISCOPUS NOTITIA at columns 1337-1338.",
            "The Fragmentum continues through file 683; Polycrates opens in file 684.",
        ],
    )
    apply_anchor(
        works["PG005-POLYCRATES-EPHESIORUM-EPISCOPUS"],
        start_page=1355,
        end_page=1365,
        start_suffix=684,
        end_suffix=689,
        evidence=[
            "File 684 directly opens POLYCRATES NOTITIA at columns 1355-1356.",
            "File 689 concludes ACTA S. TIMOTHEI and opens Theophilus on the shared column 1365.",
        ],
    )
    apply_anchor(
        works["PG005-S-SERAPION-EPISCOPUS-ANTIOCHENUS"],
        start_page=1371,
        end_page=1375,
        start_suffix=692,
        end_suffix=694,
        evidence=[
            "File 692 directly opens S. SERAPION EPISCOPUS ANTIOCHENUS NOTITIA.",
            "File 694 finishes Serapion and opens APOLLONIUS on the shared column 1375.",
        ],
    )
    apply_anchor(
        works["PG005-PLURIUM-ANONYMORUM-RELIQUIAE-A-S-IRENAEO-SERVATAE"],
        start_page=1385,
        end_page=1400,
        start_suffix=699,
        end_suffix=706,
        evidence=[
            "Files 699-706 carry the SENIORES APUD IRENÆUM material through columns 1399-1400.",
            "The Ecclesiarum Viennensis et Lugdunensis work opens in file 707.",
        ],
    )
    apply_anchor(
        works["PG005-ECCLESIARUM-VIENNENSIS-ET-LUGDUNENSIS-EPISTOLA"],
        start_page=1401,
        end_page=1452,
        start_suffix=707,
        end_suffix=732,
        evidence=[
            "File 707 directly opens the Ecclesiarum Viennensis et Lugdunensis epistola at columns 1401-1402.",
            "File 732 is the final body scan before the Appendix opens at column 1453 in file 733; its terminal 1451-1452 pair is inferred from the local header sequence after OCR digit corruption.",
        ],
    )
    apply_anchor(
        works["PG005-APPENDIX-PASSIONES-ET-ACTA"],
        start_page=1453,
        end_page=1474,
        start_suffix=733,
        end_suffix=743,
        evidence=[
            "File 733 directly opens PASSIO SS. EPIPODII ET ALEXANDRI at columns 1453-1454.",
            "The three-part Appendix continues through file 743; Victor opens in file 744.",
        ],
    )
    apply_anchor(
        works["PG005-VICTOR-I-PAPA"],
        start_page=1475,
        end_page=1489,
        start_suffix=744,
        end_suffix=751,
        evidence=[
            "File 744 directly opens S. VICTOR I PAPA and COMMENTARIUS CHRONOLOGICO-HISTORICUS.",
            "File 751 finishes Victor and opens Archæus on the shared column 1489.",
        ],
    )
    apply_anchor(
        works["PG005-ARCHAEUS-EPISCOPUS-AFRICANUS"],
        start_page=1489,
        end_page=1491,
        start_suffix=751,
        end_suffix=751,
        evidence=[
            "File 751 directly contains ARCHÆUS EPISCOPUS AFRICANUS and DE PASCHATE IN DIE DOMINICA CELEBRANDO.",
            "The work shares its opening scan with the conclusion of Victor; the former end_file 748 preceded its start and was rejected.",
        ],
    )

    main_closing_key = "PG005:section:volume_end:ordo_rerum:001"
    tail_closing_key = "PG005:candidate-section:002"
    for work in payload["works"]:
        source_key = work.get("source_section_key")
        if source_key == "PG005:volume_front:elenchus:001":
            work["source_section_key"] = "PG005:section:volume_front:elenchus:001"
        elif source_key and source_key.startswith("PG005:volume_end:ordo_rerum:"):
            work["source_section_key"] = (
                tail_closing_key if work.get("work_order", 0) >= 28 else main_closing_key
            )

    payload["notes"] = list(dict.fromkeys(payload.get("notes", []) + stable["notes"]))
    payload["notes"].extend(
        [
            "All 525 entries from the three validated chunk fragments were consumed; the two Polycarpus prolegomena entries retained from the prior reviewed payload bring the final total to 527.",
            "Six mandatory work-anchor reruns were resolved by direct OCR and neighbor inspection; shared editorial columns are preserved where one scan closes one work and opens the next.",
            "Generic NOTITIA/FRAGMENTA locator collisions were corrected for Papias, Hegesippus, Rhodon and Maximus using author-bearing title pages.",
        ]
    )
    payload["volume"]["notes"] = list(
        dict.fromkeys(
            payload["volume"].get("notes", [])
            + [
                "Validated chunk assembly covers files 010-011 and the complete closing ORDO RERUM through file 758.",
                "Physical scans may contain two editorial columns and shared work boundaries; OCR suffixes were not used as editorial page numbers.",
            ]
        )
    )

    section_keys = {section["section_key"] for section in payload["sections"]}
    work_keys = {work["work_key"] for work in payload["works"]}
    if len(section_keys) != len(payload["sections"]):
        raise AssertionError("Duplicate section keys")
    if len(work_keys) != len(payload["works"]):
        raise AssertionError("Duplicate work keys")
    for section in payload["sections"]:
        if section.get("work_key") is not None and section["work_key"] not in work_keys:
            raise AssertionError(f"Unknown section work_key: {section['work_key']}")
    for work in payload["works"]:
        if work.get("source_section_key") not in section_keys:
            raise AssertionError(
                f"Unknown work source_section_key: {work['work_key']} -> "
                f"{work.get('source_section_key')}"
            )
        if (
            work.get("start_page") is not None
            and work.get("end_page") is not None
            and work["start_page"] > work["end_page"]
        ):
            raise AssertionError(f"Reversed pages: {work['work_key']}")
        for key in ("start_file", "end_file"):
            path = work.get(key)
            if path and (
                not Path(path).is_file()
                or SOURCE_ROOT not in Path(path).parents
            ):
                raise AssertionError(f"Invalid {key}: {work['work_key']} -> {path}")
        if "work_anchor_rerun" in (work.get("raw_json") or {}):
            raise AssertionError(f"Unprocessed rerun: {work['work_key']}")

    represented_fragment_keys = {
        entry.get("raw_json", {}).get("validated_fragment_entry_key")
        for section in payload["sections"]
        for entry in section.get("entries", [])
        if entry.get("raw_json", {}).get("validated_fragment_entry_key")
    }
    if stable_entry_keys != represented_fragment_keys:
        raise AssertionError(
            f"Stable entry loss: expected={len(stable_entry_keys)} "
            f"represented={len(represented_fragment_keys)}"
        )
    total_entries = sum(len(section.get("entries", [])) for section in payload["sections"])
    if total_entries != 527:
        raise AssertionError(f"Unexpected final entry count: {total_entries}")

    PAYLOAD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "works": len(payload["works"]),
                "sections": len(payload["sections"]),
                "entries": total_entries,
                "stable_entries": len(represented_fragment_keys),
            }
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Restore PL077's general-index payload from validated chunk fragments.

The prior final payload mixed closing analytical/lexical/concordance material
into the general index, expanded OCR lines into entries, and discarded the
stable keys emitted by the chunk assembler.  This repair makes the validated
assembly canonical again while retaining reviewed work anchors and only those
physical entry targets that match a stable entry unambiguously.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME = "PL077"
SOURCE_ROOT = (ROOT / "teste" / VOLUME / "text").resolve()
PAYLOAD_PATH = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"
ASSEMBLED_PATH = (
    ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
)
CONSUMPTION_REPORT_PATH = (
    ROOT
    / "data"
    / "index_intermediate_payloads"
    / VOLUME
    / "fragment_consumption_report.json"
)

GENERAL_PRIOR_SECTION_KEYS = {
    "front_elenchus_operum",
    "volume_ordo_rerum_final",
}

EXCLUDED_PRIOR_SECTION_KEYS = {
    "dialogorum_elenchus_vocum_graecobarbararum",
    "epistolarum_variae_tabulae",
    "epistolarum_index_titulorum",
    "epistolarum_geographica_divisio_bipartita",
    "epistolarum_ordo_vetus_novo_comparatus",
    "epistolarum_ordo_restitutus_cum_antea_vulgato_compiratus",
}

ENTRY_SIGNATURE_FIELDS = (
    "entry_raw",
    "target_raw",
    "page_ref_raw",
    "page_ref_int",
    "page_ref_col",
)

WORK_ANCHOR_FIELDS = (
    "start_page",
    "end_page",
    "start_file",
    "end_file",
    "confidence",
)


def entry_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(field) for field in ENTRY_SIGNATURE_FIELDS)


def source_files(entry: dict[str, Any]) -> set[str]:
    raw_json = entry.get("raw_json")
    if not isinstance(raw_json, dict):
        return set()
    paths: set[str] = set()
    one = raw_json.get("source_file")
    if isinstance(one, str) and one:
        paths.add(one)
    many = raw_json.get("source_files")
    if isinstance(many, list):
        paths.update(item for item in many if isinstance(item, str) and item)
    return paths


def choose_locator_candidate(
    stable_entry: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    resolved = [item for item in candidates if item.get("target_file")]
    if len(resolved) == 1:
        return resolved[0]
    if not resolved:
        return None

    stable_sources = source_files(stable_entry)
    overlapping = [item for item in resolved if stable_sources & source_files(item)]
    if len(overlapping) == 1:
        return overlapping[0]

    paths = {str(item["target_file"]) for item in resolved}
    if len(paths) == 1:
        return resolved[0]
    return None


def assert_local_target(target_file: str) -> None:
    target = Path(target_file).resolve()
    if SOURCE_ROOT not in target.parents or not target.is_file():
        raise ValueError(f"Refusing non-local or missing PL077 target: {target_file}")


def transfer_reviewed_targets(
    sections: list[dict[str, Any]],
    prior: dict[str, Any],
    allowed_prior_section_keys: set[str],
) -> tuple[int, int]:
    candidates: defaultdict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for section in prior["sections"]:
        if section.get("section_key") not in allowed_prior_section_keys:
            continue
        for entry in section["entries"]:
            candidates[entry_signature(entry)].append(entry)

    retained = 0
    ambiguous = 0
    for section in sections:
        for entry in section["entries"]:
            matches = candidates.get(entry_signature(entry), [])
            chosen = choose_locator_candidate(entry, matches)
            if chosen is None:
                if any(item.get("target_file") for item in matches):
                    ambiguous += 1
                continue

            target_file = str(chosen["target_file"])
            assert_local_target(target_file)
            entry["target_file"] = target_file
            raw_json = entry.setdefault("raw_json", {})
            evidence = copy.deepcopy(
                (chosen.get("raw_json") or {}).get("physical_target_evidence")
            )
            if isinstance(evidence, dict):
                evidence["entry_key"] = entry["entry_key"]
                sequence = evidence.get("sequence")
                if isinstance(sequence, dict):
                    old_segment = str(sequence.get("segment_key") or "")
                    suffix = old_segment.partition(":structural-segment:")[2]
                    if suffix:
                        sequence["segment_key"] = (
                            f"{section['section_key']}:structural-segment:{suffix}"
                        )
                raw_json["physical_target_evidence"] = evidence
            raw_json["target_file_repair"] = (
                "Retained from the reviewed structural-target pass after an exact "
                "stable-entry signature match within PL077."
            )
            retained += 1
    return retained, ambiguous


def restore_works(
    stable_works: list[dict[str, Any]], prior: dict[str, Any]
) -> list[dict[str, Any]]:
    prior_by_stable_key: dict[str, dict[str, Any]] = {}
    for work in prior["works"]:
        raw_json = work.get("raw_json") or {}
        consumed = raw_json.get("assembled_fragments_consumed") or {}
        stable_key = consumed.get("stable_work_key")
        if stable_key:
            prior_by_stable_key[str(stable_key)] = work

    works = copy.deepcopy(stable_works)
    for work in works:
        prior_work = prior_by_stable_key.get(work["work_key"])
        if prior_work is None:
            raise ValueError(f"Missing reviewed anchor record for {work['work_key']}")
        for field in WORK_ANCHOR_FIELDS:
            work[field] = prior_work.get(field)
        stable_raw = work.setdefault("raw_json", {})
        prior_raw = copy.deepcopy(prior_work.get("raw_json") or {})
        stable_raw.update(prior_raw)
        stable_raw["assembled_fragments_consumed"] = {
            "stable_work_key": work["work_key"],
            "stable_source_section_key": work["source_section_key"],
        }
    return works


def main() -> None:
    prior = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled_document = json.loads(ASSEMBLED_PATH.read_text(encoding="utf-8"))
    assembled = assembled_document["data"]

    stable_section_keys = {section["section_key"] for section in assembled["sections"]}
    prior_section_keys = {section["section_key"] for section in prior["sections"]}
    missing_exclusions = EXCLUDED_PRIOR_SECTION_KEYS - prior_section_keys
    already_repaired = prior_section_keys == stable_section_keys
    if missing_exclusions and not already_repaired:
        raise ValueError(f"Expected contaminated sections are absent: {missing_exclusions}")

    sections = copy.deepcopy(assembled["sections"])
    for section in sections:
        if section.get("scope_kind") == "volume_back":
            section.setdefault("raw_json", {})["assembled_scope_kind"] = "volume_back"
            section["scope_kind"] = "volume_end"

    retained_targets, ambiguous_targets = transfer_reviewed_targets(
        sections,
        prior,
        GENERAL_PRIOR_SECTION_KEYS | stable_section_keys,
    )
    works = restore_works(assembled["works"], prior)

    volume = copy.deepcopy(prior["volume"])
    volume["notes"] = [
        "PL077 contains four works identified by the volume-front ELENCHUS.",
        "The general-index payload owns the volume-front ELENCHUS and the final recapitulating ORDO RERUM only.",
        "Closing analytical, lexical, concordance, and cross-reference structures are excluded from the general-index payload.",
        "Physical OCR file suffixes remain distinct from printed/editorial page or column references.",
    ]

    notes = [
        "Restored all four validated section fragments and all 950 stable entries from assembled_fragments.json.",
        "Removed six cross-pipeline sections introduced by the prior final agent: the Dialogorum lexical glossary, Epistolarum comparative/geographical/concordance tables, and Index Titulorum.",
        "The preceding INDEX RERUM ET SENTENTIARUM remains excluded; the ORDO RERUM begins below its trailing X-Z material in OCR file 804.",
        f"Retained {retained_targets} reviewed target_file mappings by exact stable-entry match; skipped {ambiguous_targets} ambiguous mappings.",
        "Stable work, section, and entry keys from the deterministic assembly were restored.",
    ]

    result = {
        "volume": volume,
        "works": works,
        "sections": sections,
        "notes": notes,
    }

    entry_keys = [
        entry["entry_key"]
        for section in sections
        for entry in section["entries"]
    ]
    if len(entry_keys) != 950 or len(entry_keys) != len(set(entry_keys)):
        raise ValueError("Stable entry count or uniqueness check failed")
    if len(sections) != 4 or len(works) != 4:
        raise ValueError("Expected four stable works and four stable section fragments")

    PAYLOAD_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    import sys

    sys.path.insert(0, str(ROOT))
    from tools.indexing.index_fragment_assembly import (  # noqa: PLC0415
        verify_payload_consumes_fragments,
    )

    report = verify_payload_consumes_fragments(result, assembled_document)
    if report.get("status") != "ok":
        raise ValueError(f"Fragment consumption verification failed: {report}")
    CONSUMPTION_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "volume_id": VOLUME,
                "works": len(works),
                "sections": len(sections),
                "entries": len(entry_keys),
                "retained_targets": retained_targets,
                "ambiguous_targets_skipped": ambiguous_targets,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

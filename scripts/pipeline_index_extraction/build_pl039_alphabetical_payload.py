#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/build_pl039_alphabetical_payload.py --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL039_alphabetical_indices.json

Rebuild the PL039 closing-index payload from validated assembled fragments,
normalizing section boundaries/order and applying the verified helper-backed
locator fixes for the late appendix sermon entries.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL039"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL039/text"
ASSEMBLED_FRAGMENTS = ROOT / "data/intermediate_payloads/PL039/assembled_fragments.json"
CHUNKS_DIR = ROOT / "data/intermediate_payloads/PL039/chunks"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL039_helper_output.json"
TODO_JSON = ROOT / "data/intermediate_payloads/PL039/todo.json"

SECTION_ANALYTIC = "PL039:candidate-section:002"
SECTION_CROSSWALK = "PL039:candidate-section:001"

SECTION_CONFIG = {
    SECTION_ANALYTIC: {
        "section_order": 1,
        "file_start_seq": 439,
        "file_end_seq": 472,
        "page_start": 2553,
        "page_end": 2620,
        "heading_raw": "INDEX RERUM.",
        "heading_norm": "index rerum",
        "section_kind": "analytic_subject",
        "notes": [
            "The section begins below the appendix prose on file 439 and runs continuously through the last INDEX RERUM page on file 472.",
            "Editorial pagination was reconstructed from neighboring headers because OCR often drops the leading 5 or 6 in the 2553-2620 sequence.",
        ],
    },
    SECTION_CROSSWALK: {
        "section_order": 2,
        "file_start_seq": 473,
        "file_end_seq": 482,
        "page_start": 2621,
        "page_end": 2640,
        "heading_raw": "SERMONUM INDICES.",
        "heading_norm": "sermonum indices",
        "section_kind": "crosswalk_index",
        "notes": [
            "This closing section is a sermon-order crosswalk headed SERMONUM INDICES. / SERMONUM ORDO NOVUS CUM ORDINE VETERI COMPARATUS.",
            "The 2621-2640 page span is inferred from the stable 2621/2622 start on file 473 and the corrupted 2639/2640 tail header on file 482.",
        ],
    },
}

HELPER_ENTRY_FIXES = {
    "Serm. CCCXI. De eleemosyna.": {
        "entry_id": "pl039_sermo_cccxi_de_eleemosyna",
        "target_seq": 433,
        "target_probability": 0.93,
        "reason": "File 433 carries SERMO CCCXI at the inferred 2541/2542 opening, which is the best direct appendix witness for the 2542-2545 locator range.",
    },
    "Serm. CCCXII. De eleemosyna.": {
        "entry_id": "pl039_sermo_cccxii_de_eleemosyna",
        "target_seq": 434,
        "target_probability": 0.82,
        "reason": "File 434 contains the head of SERMO CCCXII; the printed locator appears as 2545-2544 in both the body witness and the index, so the direct sermon witness was preferred over the later index page.",
    },
    "Serm. CCCXIV. De erepto energumeno.": {
        "entry_id": "pl039_sermo_cccxiv_de_erepto_energumeno",
        "target_seq": 436,
        "target_probability": 0.94,
        "reason": "File 436 preserves the SERMO CCCXIV head and matches the 2547-2548 locator sequence despite header-digit loss in OCR.",
    },
    "Serm. CCCXVI. De sancto Laurentio.": {
        "entry_id": "pl039_sermo_cccxvi_de_sancto_laurentio",
        "target_seq": 438,
        "target_probability": 0.95,
        "reason": "File 438 preserves SERMO CCCXVI and the paired Massa Candida continuation under the inferred 2551/2552 header.",
    },
    "Serm. CCCXVII. In Natali Martyrum Massæ Candidæ.": {
        "entry_id": None,
        "target_seq": 438,
        "target_probability": 0.92,
        "reason": "The Ibid. locator inherits the preceding 2551-2552 appendix witness on file 438, where the Massa Candida sermon begins immediately after SERMO CCCXVI.",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_seq(path_str: str) -> int:
    match = re.search(r"-(\d+)\.txt$", path_str)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path_str}")
    return int(match.group(1))


def file_by_seq(seq: int) -> str:
    matches = list(SOURCE_ROOT.glob(f"*-{seq}.txt"))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one OCR file for seq={seq}, found {len(matches)}")
    return str(matches[0])


def load_chunk_section_material() -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    notes: list[str] = []
    for chunk_path in sorted(CHUNKS_DIR.glob("section_*.json")):
        payload = read_json(chunk_path)
        notes.extend(payload.get("notes") or [])
        for section in payload.get("sections") or []:
            by_section[section["section_key"]].append(section)
    return by_section, notes


def load_helper_output() -> dict[str, Any]:
    payload = read_json(HELPER_OUTPUT)
    return {entry["entry_id"]: entry for entry in payload.get("entries") or []}


def dedupe_preserve(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the PL039 alphabetical payload from validated fragments.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/alphabetical_index_payloads/PL039_alphabetical_indices.json",
        help="Final payload path",
    )
    args = parser.parse_args()

    assembled = read_json(ASSEMBLED_FRAGMENTS)
    data = deepcopy(assembled["data"])
    chunk_sections, chunk_notes = load_chunk_section_material()
    helper_entries = load_helper_output()

    section_start_files = {
        key: file_by_seq(meta["file_start_seq"])
        for key, meta in SECTION_CONFIG.items()
    }
    section_end_files = {
        key: file_by_seq(meta["file_end_seq"])
        for key, meta in SECTION_CONFIG.items()
    }

    sections_out: list[dict[str, Any]] = []
    for section_key, meta in sorted(SECTION_CONFIG.items(), key=lambda item: item[1]["section_order"]):
        fragments = chunk_sections.get(section_key, [])
        if not fragments:
            raise RuntimeError(f"missing chunk section fragments for {section_key}")
        visible_headings: list[str] = []
        evidence_files: list[str] = []
        context_checked: list[str] = []
        context_not_emitted: list[str] = []
        notes: list[str] = []
        extra_file_evidence: list[dict[str, Any]] = []
        header_anomalies: dict[str, str] = {}
        for fragment in fragments:
            raw = fragment.get("raw_json") or {}
            visible_headings.extend(raw.get("visible_headings") or [])
            evidence_files.extend(raw.get("evidence_files") or [])
            context_checked.extend(raw.get("context_files_checked") or [])
            context_not_emitted.extend(raw.get("context_files_not_emitted") or [])
            notes.extend(raw.get("notes") or [])
            extra_file_evidence.extend(raw.get("extra_file_evidence") or [])
            header_anomalies.update(raw.get("header_anomalies") or {})
        section = {
            "section_key": section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": meta["section_order"],
            "section_kind": meta["section_kind"],
            "heading_raw": meta["heading_raw"],
            "heading_norm": meta["heading_norm"],
            "heading_letter": None,
            "page_start": meta["page_start"],
            "page_end": meta["page_end"],
            "file_start": section_start_files[section_key],
            "file_end": section_end_files[section_key],
            "confidence": max(fragment.get("confidence", 0.0) for fragment in fragments),
            "raw_json": {
                "section_kind_reason": (fragments[-1].get("raw_json") or {}).get("section_kind_reason"),
                "heading_page_range_inference": {
                    "page_start": meta["page_start"],
                    "page_end": meta["page_end"],
                    "reason": "Reconstructed from neighboring printed headers in the current volume after correcting dropped leading digits in OCR.",
                },
                "evidence_files": dedupe_preserve(evidence_files),
                "context_files_checked": dedupe_preserve(context_checked),
                "context_files_not_emitted": dedupe_preserve(context_not_emitted),
                "visible_headings": dedupe_preserve(visible_headings) if visible_headings else None,
                "extra_file_evidence": dedupe_preserve(extra_file_evidence) if extra_file_evidence else None,
                "header_anomalies": header_anomalies or None,
                "notes": dedupe_preserve(meta["notes"] + notes),
            },
        }
        sections_out.append(section)

    # Drop null placeholders from section raw_json.
    for section in sections_out:
        section["raw_json"] = {k: v for k, v in section["raw_json"].items() if v not in (None, [], {})}

    # Nodes only exist in the analytical section. Sort them by physical file.
    nodes_in = deepcopy(data["nodes"])
    node_meta = []
    for idx, node in enumerate(nodes_in):
        source_file = (node.get("raw_json") or {}).get("source_file")
        node_meta.append((page_seq(source_file), idx, node))
    nodes_out: list[dict[str, Any]] = []
    per_section_node_order: dict[str, int] = defaultdict(int)
    for _, _, node in sorted(node_meta):
        section_key = node["section_key"]
        per_section_node_order[section_key] += 1
        node["node_order"] = per_section_node_order[section_key]
        nodes_out.append(node)

    # Sort entries in natural physical order.
    entries_in = deepcopy(data["entries"])
    entry_meta: list[tuple[str, int, int, dict[str, Any]]] = []
    for idx, entry in enumerate(entries_in):
        source_file = (entry.get("raw_json") or {}).get("source_file") or entry["editorial_anchor_file"]
        entry_meta.append((entry["section_key"], page_seq(source_file), idx, entry))

    section_order_rank = {section["section_key"]: section["section_order"] for section in sections_out}
    entries_out: list[dict[str, Any]] = []
    entry_key_to_order: dict[str, int] = {}
    entry_key_to_position: dict[str, int] = {}
    per_section_entry_order: dict[str, int] = defaultdict(int)

    for _, _, _, entry in sorted(
        entry_meta,
        key=lambda item: (section_order_rank[item[0]], item[1], item[2]),
    ):
        section_key = entry["section_key"]
        per_section_entry_order[section_key] += 1
        entry["entry_order"] = per_section_entry_order[section_key]
        entry["section_start_file"] = section_start_files[section_key]
        if entry.get("context_raw") == entry.get("entry_raw"):
            entry["context_raw"] = None
        entry_key_to_order[entry["entry_key"]] = entry["entry_order"]
        entry_key_to_position[entry["entry_key"]] = len(entries_out)
        entries_out.append(entry)

    refs_in = deepcopy(data["refs"])
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs_in:
        refs_by_entry[ref["entry_key"]].append(ref)

    # Helper-backed locator fixes.
    for entry in entries_out:
        fix = HELPER_ENTRY_FIXES.get(entry.get("lemma_raw"))
        if not fix:
            continue
        target_file = file_by_seq(fix["target_seq"])
        entry["target_file_best"] = target_file
        raw_json = entry.setdefault("raw_json", {})
        helper_payload = None
        if fix["entry_id"]:
            helper_entry = helper_entries.get(fix["entry_id"])
            if helper_entry:
                helper_payload = {
                    "entry_id": fix["entry_id"],
                    "status": helper_entry.get("status"),
                    "best_candidate_file": helper_entry.get("best_candidate", {}).get("file"),
                    "best_candidate_probability": helper_entry.get("best_candidate", {}).get("probability"),
                    "candidate_role": helper_entry.get("best_candidate", {}).get("candidate_role"),
                    "reason_summary": helper_entry.get("best_candidate", {}).get("reason_summary"),
                    "top_candidates": [
                        {
                            "file": candidate.get("file"),
                            "probability": candidate.get("probability"),
                            "candidate_role": candidate.get("candidate_role"),
                            "evidence_kinds": [ev.get("kind") for ev in candidate.get("evidence", [])[:4]],
                        }
                        for candidate in helper_entry.get("candidates", [])[:3]
                    ],
                }
        if helper_payload:
            raw_json["helper"] = helper_payload
        raw_json["target_override_reason"] = fix["reason"]
        raw_json["target_override_evidence_files"] = dedupe_preserve(
            raw_json.get("source_files", []) + [target_file]
        )
        for ref in refs_by_entry.get(entry["entry_key"], []):
            ref["target_file"] = target_file
            ref["target_file_probability"] = fix["target_probability"]
            ref_raw_json = ref.setdefault("raw_json", {})
            if helper_payload:
                ref_raw_json["helper"] = helper_payload
            ref_raw_json["target_override_reason"] = fix["reason"]

    refs_out: list[dict[str, Any]] = []
    for ref in refs_in:
        ref["section_start_file"] = section_start_files[next(e["section_key"] for e in entries_out if e["entry_key"] == ref["entry_key"])]
        refs_out.append(ref)
    refs_out.sort(key=lambda ref: (section_order_rank[next(e["section_key"] for e in entries_out if e["entry_key"] == ref["entry_key"])], entry_key_to_order[ref["entry_key"]], ref["ref_order"], entry_key_to_position[ref["entry_key"]]))

    missing_ref_entries = [entry for entry in entries_out if entry["entry_key"] not in refs_by_entry]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_ID,
        "notes": (
            "Closing sermon-index material rebuilt from validated chunk fragments: "
            "INDEX RERUM (analytic sermon summaries) followed by SERMONUM INDICES "
            "(ordo novus / ordo vetus crosswalk tables)."
        ),
    }

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Both closing-index sections were reconstructed from 9 validated chunk fragments. "
            f"{len(missing_ref_entries)} analytic entries remain without refs because the OCR never exposes a stable terminal locator even after neighboring-page and regex checks recorded in raw_json."
        ),
        "evidence_files": [
            file_by_seq(439),
            file_by_seq(472),
            file_by_seq(473),
            file_by_seq(482),
            file_by_seq(433),
            file_by_seq(436),
            file_by_seq(438),
        ],
    }

    notes = dedupe_preserve(
        [
            "The canonical payload was rebuilt from data/intermediate_payloads/PL039/assembled_fragments.json because the chunk last-message checkpoints are empty acknowledgments, not the stable extraction state.",
            "Section pagination was inferred from neighboring headers inside PL039; OCR frequently drops the leading 5/6 in the 2553-2640 closing-index run.",
            "Five late appendix sermons near the file 472 tail were cross-checked against the appendix/body witnesses on files 433, 434, 436, and 438; helper evidence was preserved where it materially affected the chosen locator.",
            f"{len(missing_ref_entries)} entries were intentionally kept without refs when the OCR preserved the lemma but not a recoverable terminal locator.",
        ]
        + chunk_notes
    )

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections_out,
        "nodes": nodes_out,
        "entries": entries_out,
        "refs": refs_out,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(args.output, payload)

    todo_payload = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PL039 payload rebuilt from validated fragments and written to final output",
        "completed": [
            "validated assembled_fragments.json consumed",
            "section boundaries and page spans normalized",
            "entry/node ordering normalized for final payload",
            "helper-backed appendix locator fixes applied",
            "final payload written",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Keep the stable chunk objects; the empty last_message files are not a lossless checkpoint.",
            "Remaining ref-less entries are documented individually in raw_json.locator_missing evidence.",
        ],
    }
    write_json(TODO_JSON, todo_payload)


if __name__ == "__main__":
    main()

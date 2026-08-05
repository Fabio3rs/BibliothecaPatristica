#!/usr/bin/env python3
"""Usage: build the PL115-1 ORDO RERUM payload and helper request.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl115_1_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL115-1/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115-1_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115-1_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL115-1 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115-1_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pipeline_index_extraction import pl115_ordo_rerum_build as base


VOLUME_ID = "PL115-1"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 115-1"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_FILES = [731, 732, 733, 734]

EXTRA_SKIP_EXACT = {
    "AUDRADUS SENONENSIS CHOREPISCOPUS.",
    "SANCTI PRUDENTII TRECENSIS EPISCOPI FLORILEGIUM EX SACRA SCRIPTURA.",
    "FLORILEGIUM EX SACRA SCRIPTURA.",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "QUÆ IN HOC TOMO CONTINENTUR.",
}

PAGE_CORRECTIONS = {
    "CAPUT PRIMUM. — Quadrivio regularum totius philosophiæ quatuor omnem quaestionem solvi.": 1044,
    "CAPUT PRIMUM. — Quadrivio regularum totius philosophiæ quatuor omnem quæstionem solvi.": 1044,
    "CAP. II. — Argumento necessitatis colligitur duas præ-destinationes liceri non posse.": 1045,
    "CAP. III. — De eo quod duas prædestinationes ratione non sunt esse.": 1049,
    "CAP. IV. — De una veraque solaque prædestinatione.": 1053,
    "CAP. XII. — Ex Cassiodoro in psalams et Beda.": 1003,
    "CAP. XIII. — De Gratia et libero Arbitrio et diversi- s.": 1005,
}

ENTRY_ORDER_CORRECTIONS = {
    355: 1421,
    356: 1450,
    357: 1451,
    358: 1455,
}


@dataclass(slots=True)
class ParsedEntry:
    entry_order: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    source_file: str
    query_names: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_file(source_root: Path, seq: int) -> Path:
    matches = sorted(source_root.glob(f"*-{seq}.txt"))
    if not matches:
        raise SystemExit(f"Could not locate OCR file with suffix {seq} in {source_root}")
    return matches[0]


def is_skipped_heading(line: str) -> bool:
    if line in EXTRA_SKIP_EXACT:
        return True
    return base.is_skipped_heading(line)


def parse_entries(source_root: Path) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    order = 0
    for seq in SECTION_FILES:
        path = find_file(source_root, seq)
        lines = base.extract_lines(path)
        buffer: list[str] = []
        for line in lines:
            if base.is_noise_line(line):
                continue
            if is_skipped_heading(line):
                buffer = []
                continue
            if re.fullmatch(r"\d{1,4}", line):
                if buffer:
                    order += 1
                    entry_raw = " ".join(buffer).strip()
                    page_ref_raw = line
                    lemma = re.sub(r"\s+\d{1,4}$", "", entry_raw).strip()
                    entries.append(
                        ParsedEntry(
                            entry_order=order,
                            entry_raw=entry_raw,
                            lemma_raw=lemma,
                            page_ref_raw=page_ref_raw,
                            page_ref_int=int(page_ref_raw),
                            source_file=str(path),
                            query_names=base.make_query_names(lemma),
                        )
                    )
                    buffer = []
                continue
            page_match = re.search(r"(?P<page>\d{1,4})$", line)
            if page_match:
                order += 1
                entry_raw = " ".join(buffer + [line]).strip() if buffer else line
                page_ref_raw = page_match.group("page")
                lemma = re.sub(r"\s+\d{1,4}$", "", entry_raw).strip()
                entries.append(
                    ParsedEntry(
                        entry_order=order,
                        entry_raw=entry_raw,
                        lemma_raw=lemma,
                        page_ref_raw=page_ref_raw,
                        page_ref_int=int(page_ref_raw),
                        source_file=str(path),
                        query_names=base.make_query_names(lemma),
                    )
                )
                buffer = []
                continue
            buffer.append(line)
    return entries


def apply_page_corrections(entries: list[ParsedEntry]) -> list[ParsedEntry]:
    for item in entries:
        if item.entry_order in ENTRY_ORDER_CORRECTIONS:
            corrected = ENTRY_ORDER_CORRECTIONS[item.entry_order]
            item.page_ref_raw = str(corrected)
            item.page_ref_int = corrected
            continue
        for prefix, corrected in PAGE_CORRECTIONS.items():
            if item.lemma_raw.startswith(prefix):
                item.page_ref_raw = str(corrected)
                item.page_ref_int = corrected
                break
    return entries


def build_helper_request(source_root: Path, entries: list[ParsedEntry]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in entries:
        if item.page_ref_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.entry_order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names or [item.lemma_raw],
                "page_hints": [item.page_ref_raw] if item.page_ref_raw else [],
                "page_hint_ints": [item.page_ref_int] if item.page_ref_int is not None else [],
                "context_raw": item.entry_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(source_root: Path, entries: list[ParsedEntry], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_by_id = helper_index(helper_output)
    section_start = find_file(source_root, SECTION_FILES[0])
    section_end = find_file(source_root, SECTION_FILES[-1])
    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1457,
            "page_end": 1463,
            "file_start": str(section_start),
            "file_end": str(section_end),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "final_volume_ordo_rerum_closure",
                "section_evidence_files": [
                    str(find_file(source_root, seq)) for seq in SECTION_FILES
                ],
            },
        }
    ]

    out_entries: list[dict[str, Any]] = []
    out_refs: list[dict[str, Any]] = []

    for item in entries:
        entry_id = f"{VOLUME_ID.lower()}_{item.entry_order:03d}"
        helper = helper_by_id.get(entry_id) or {}
        best = helper.get("best_candidate") or {}
        candidates = helper.get("candidates") or []
        target_file_best = best.get("file")
        target_prob = best.get("probability")
        confidence = 0.95 if item.page_ref_int is not None else 0.72
        if helper.get("status") == "ambiguous":
            confidence = min(confidence, 0.84)
        if item.page_ref_int is None:
            confidence = 0.7

        out_entries.append(
            {
                "entry_key": entry_id,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": item.entry_order,
                "entry_kind": "lemma",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": norm(item.lemma_raw).lower(),
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": str(section_start),
                "editorial_anchor_file": item.source_file or None,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "helper_status": helper.get("status"),
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best,
                    "helper_candidates_top": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in candidates[:3]
                    ],
                    "page_ref_raw_source": item.page_ref_raw,
                },
            }
        )

        if item.page_ref_int is not None:
            out_refs.append(
                {
                    "entry_key": entry_id,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": item.page_ref_raw,
                    "page_ref_raw": item.page_ref_raw,
                    "page_ref_int": item.page_ref_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file_best,
                    "target_file_probability": target_prob,
                    "section_start_file": str(section_start),
                    "editorial_anchor_file": item.source_file or None,
                    "confidence": confidence,
                    "raw_json": {
                        "helper_status": helper.get("status"),
                        "helper_best_candidate": best,
                        "helper_candidates_top": [
                            {
                                "file": cand.get("file"),
                                "probability": cand.get("probability"),
                                "candidate_role": cand.get("candidate_role"),
                                "reason_summary": cand.get("reason_summary"),
                            }
                            for cand in candidates[:3]
                        ],
                    },
                }
            )

    notes = [
        "PL115-1 closes with an Ordo Rerum section rather than a conventional alphabetical index.",
        "OCR page refs in the contents block were cross-checked against body pages for the predestination treatise and corrected where the OCR had dropped digits.",
        "The helper output was used only to choose the most plausible physical OCR file for each page ref.",
    ]

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Ordo rerum lines were recoverable from the OCR tail, with page-ref corrections validated against the body text.",
        "evidence_files": [str(find_file(source_root, seq)) for seq in SECTION_FILES],
    }

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": [],
        "entries": out_entries,
        "refs": out_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL115-1 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve PL115-1 Ordo Rerum and write the final payload",
        "completed": [
            "inspected tail OCR pages 731-734",
            "validated body pages for the predestination treatise around 1003-1054",
        ],
        "pending": [
            "run helper target resolution",
            "assemble final payload",
            "write output JSON",
        ],
        "blocked": [],
        "notes": [
            "Treat the OCR page suffix as separate from the editorial page cited by the contents.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    entries = parse_entries(args.source_root)
    entries = apply_page_corrections(entries)

    helper_request = build_helper_request(args.source_root, entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(args.source_root, entries, helper_output)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

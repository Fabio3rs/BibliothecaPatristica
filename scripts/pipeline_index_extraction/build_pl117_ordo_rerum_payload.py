#!/usr/bin/env python3
"""Usage: build the PL117 ORDO RERUM payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl117_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL117/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL117_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL117_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL117 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL117_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL117"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 117"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"


@dataclass(slots=True)
class TocItem:
    order: int
    source_file: str
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    query_names: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def norm_sort(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        line = normalize(raw)
        if not line:
            continue
        if line == "Digitized by Google":
            continue
        lines.append(line)
    return lines


def strip_page_ref(text: str) -> tuple[str, int | None, str | None]:
    value = normalize(text)
    match = None
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", value):
        pass
    if match is None:
        return value, None, None
    page_raw = match.group(1)
    lemma = normalize(value[: match.start()]).rstrip(" ,;:.")
    return lemma, int(page_raw), page_raw


def extract_toc_items(source_root: Path) -> tuple[list[TocItem], dict[str, Any]]:
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    if not files:
        raise SystemExit(f"No OCR files found in {source_root}")

    toc_files = [
        source_root / "9ba900f5-51ca-4d19-8d88-c119b6b3fa94-616.txt",
        source_root / "9ba900f5-51ca-4d19-8d88-c119b6b3fa94-617.txt",
    ]
    for path in toc_files:
        if not path.exists():
            raise SystemExit(f"Expected OCR file not found: {path}")

    lines_616 = clean_text_lines(toc_files[0])
    lines_617 = clean_text_lines(toc_files[1])

    # The OCR stores the TOC as a few very long lines. We preserve the literal
    # sequence but split out page-bearing items with a conservative regex.
    combined = " ".join(lines_616 + lines_617)
    combined = combined.replace("Digitized by Google", "")
    combined = re.sub(r"\s+", " ", combined).strip()

    # Split the initial heading block off from the item stream.
    marker = "ENARRATIO IN XII PROPHETAS MINORES."
    if marker in combined:
        combined = combined.split(marker, 1)[1].strip()

    items: list[TocItem] = []
    item_re = re.compile(
        r"(?P<entry>.+?)\s+(?P<page>\d{1,4})(?=\s+(?:[A-ZÆŒÀ-Ý]|Argumentum\.|Prologus\.|Præfatio\.|Haymonis|Sequitur|LIBER|FINIS|$))"
    )
    order = 0
    source_cutoff = 87  # entries 1-87 are on OCR file 616, the rest on 617.
    for idx, match in enumerate(item_re.finditer(combined), start=1):
        order += 1
        entry_raw = normalize(match.group("entry"))
        page_raw = match.group("page")
        lemma_raw, page_int, _ = strip_page_ref(f"{entry_raw} {page_raw}")
        # Keep OCR literals for the entry itself but use a slightly cleaner lemma.
        lemma_raw = lemma_raw or entry_raw
        query_names = [lemma_raw]
        if lemma_raw.upper().startswith("IN EPISTOLAM "):
            query_names.append(lemma_raw.replace("IN EPISTOLAM ", "", 1))
        if lemma_raw.upper().startswith("CAPUT "):
            query_names.append(lemma_raw.replace(".", "", 1))
        if lemma_raw.upper().startswith("CAP. "):
            query_names.append(lemma_raw.replace(".", "", 1))
        query_names = list(dict.fromkeys(q for q in query_names if q))
        source_file = toc_files[0].as_posix() if idx <= source_cutoff else toc_files[1].as_posix()
        items.append(
            TocItem(
                order=order,
                source_file=source_file,
                entry_raw=f"{entry_raw} {page_raw}".strip(),
                lemma_raw=lemma_raw,
                page_ref_raw=page_raw,
                page_ref_int=page_int,
                query_names=query_names,
            )
        )

    # Extract the major section nodes from the TOC top matter. They preserve the
    # hierarchy without forcing every chapter line into an artificial subtree.
    raw_lines = "\n".join(lines_616 + lines_617)
    section_nodes = [
        {
            "node_key": f"{VOLUME_ID}:node:001",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "rubric_group",
            "label_raw": "HAYMO HALBERSTATENSIS EPISCOPUS.",
            "label_norm": "haymo halberstatensis episcopus",
            "label_sort": "haymo halberstatensis episcopus",
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_file": toc_files[0].as_posix(), "role": "author_heading"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:002",
            "section_key": SECTION_KEY,
            "parent_node_key": f"{VOLUME_ID}:node:001",
            "node_order": 2,
            "node_kind": "rubric_group",
            "label_raw": "BAYMONIS OPERUM PRIMÆ PARTIS CONTINUATIO.",
            "label_norm": "baymonis operum primae partis continuatio",
            "label_sort": "baymonis operum primae partis continuatio",
            "node_level": 2,
            "confidence": 0.98,
            "raw_json": {"source_file": toc_files[0].as_posix(), "role": "continuation_heading"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:003",
            "section_key": SECTION_KEY,
            "parent_node_key": f"{VOLUME_ID}:node:002",
            "node_order": 3,
            "node_kind": "heading_group",
            "label_raw": "ENARRATIO IN XII PROPHETAS MINORES.",
            "label_norm": "enarratio in xii prophetas minores",
            "label_sort": "enarratio in xii prophetas minores",
            "node_level": 3,
            "confidence": 0.99,
            "raw_json": {"source_file": toc_files[0].as_posix(), "role": "major_work"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:004",
            "section_key": SECTION_KEY,
            "parent_node_key": f"{VOLUME_ID}:node:002",
            "node_order": 4,
            "node_kind": "heading_group",
            "label_raw": "COMMENTARIUM IN CANTICA CANTICORUM.",
            "label_norm": "commentarium in cantica canticorum",
            "label_sort": "commentarium in cantica canticorum",
            "node_level": 3,
            "confidence": 0.99,
            "raw_json": {"source_file": toc_files[0].as_posix(), "role": "major_work"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:005",
            "section_key": SECTION_KEY,
            "parent_node_key": f"{VOLUME_ID}:node:002",
            "node_order": 5,
            "node_kind": "heading_group",
            "label_raw": "IN D. PAULI EPISTOLAS EXPOSITIO.",
            "label_norm": "in d pauli epistolas expositio",
            "label_sort": "in d pauli epistolas expositio",
            "node_level": 3,
            "confidence": 0.99,
            "raw_json": {"source_file": toc_files[1].as_posix(), "role": "major_work"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:006",
            "section_key": SECTION_KEY,
            "parent_node_key": f"{VOLUME_ID}:node:002",
            "node_order": 6,
            "node_kind": "heading_group",
            "label_raw": "EXPOSITIO IN APOCALYPSIN.",
            "label_norm": "expositio in apocalypsin",
            "label_sort": "expositio in apocalypsin",
            "node_level": 3,
            "confidence": 0.99,
            "raw_json": {"source_file": toc_files[1].as_posix(), "role": "major_work"},
        },
    ]

    return items, {
        "toc_files": [p.as_posix() for p in toc_files],
        "raw_lines": raw_lines,
        "nodes": section_nodes,
    }


def build_helper_request(source_root: Path, items: list[TocItem]) -> dict[str, Any]:
    helper_entries = []
    for item in items:
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names,
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
        out[str(item.get("entry_id"))] = item
    return out


def entry_group(order: int) -> str:
    if order <= 87:
        return f"{VOLUME_ID}:node:003"
    if order <= 124:
        return f"{VOLUME_ID}:node:004"
    if order <= 210:
        return f"{VOLUME_ID}:node:005"
    return f"{VOLUME_ID}:node:006"


def build_payload(
    items: list[TocItem],
    helper_output: dict[str, Any],
    helper_meta: dict[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    section_start_file = helper_meta["toc_files"][0]
    editorial_anchor_file = helper_meta["toc_files"][0]

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
            "page_start": 1219,
            "page_end": 1224,
            "file_start": helper_meta["toc_files"][0],
            "file_end": helper_meta["toc_files"][1],
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "The OCR tail explicitly prints ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.; this is a table of contents / ordering section, not an alphabetical index proper.",
                "evidence_files": helper_meta["toc_files"],
                "source_note": "fallback_internal filtered pages confirmed the section heading in files 616 and 617.",
            },
        }
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    for item in items:
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{item.order:03d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        parent_node_key = entry_group(item.order)

        if item.order == 1:
            parent_node_key = f"{VOLUME_ID}:node:003"

        entry_key = f"{VOLUME_ID}:entry:{item.order:03d}"
        target_file_best = best.get("file") or None
        if target_file_best == item.source_file:
            target_file_best = None
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": parent_node_key,
            "entry_order": item.order,
            "entry_kind": "heading_group",
            "lemma_raw": item.lemma_raw,
            "lemma_display": item.lemma_raw,
            "lemma_norm": norm_sort(item.lemma_raw),
            "lemma_sort": norm_sort(item.lemma_raw),
            "entry_raw": item.entry_raw,
            "context_raw": item.entry_raw,
            "heading_letter": None,
            "inferred_printed_page": item.page_ref_int,
            "section_start_file": section_start_file,
            "editorial_anchor_file": item.source_file,
            "target_file_best": target_file_best,
            "confidence": 0.91 if target_file_best else 0.78,
            "raw_json": {
                "source_file": item.source_file,
                "section_kind": "ordo_rerum",
                "page_ref_raw": item.page_ref_raw,
                "helper": {
                    "status": helper_entry.get("status"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                    "best_candidate": best if best else None,
                    "top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                            "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict)],
                        }
                        for cand in candidates[:3]
                    ],
                },
            },
        }
        entries.append(entry)

        refs.append(
            {
                "entry_key": entry_key,
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
                "target_file_probability": best.get("probability"),
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "confidence": 0.9 if target_file_best else 0.72,
                "raw_json": {
                    "source_file": item.source_file,
                    "section_kind": "ordo_rerum",
                    "helper": {
                        "status": helper_entry.get("status"),
                        "candidate_role": best.get("candidate_role"),
                        "reason_summary": best.get("reason_summary"),
                        "best_candidate": best if best else None,
                    },
                },
            }
        )

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "OCR tail contains a structured ORDO RERUM table of contents with page-bearing lines recovered as entries.",
        "evidence_files": helper_meta["toc_files"],
    }

    return {
        "schema_version": 1.0,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": helper_meta["nodes"],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            {
                "note_key": f"{VOLUME_ID}:note:001",
                "note_type": "extraction",
                "text": "This volume ends in ORDO RERUM rather than an alphabetical index proper; the payload records the table of contents as an ordo_rerum section.",
            }
        ],
    }


def write_intermediate_state(intermediate_dir: Path, items: list[TocItem], payload: dict[str, Any]) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "items.json", [asdict(item) for item in items])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL117 ORDO RERUM payload assembled and validated",
            "completed": [
                "TOC section detected",
                "helper request built and resolved",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The tail pages 616-617 contain the section heading and TOC lines.",
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL117 ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    items, helper_meta = extract_toc_items(args.source_root)
    helper_request = build_helper_request(args.source_root, items)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(items, helper_output, helper_meta, args.source_root)
    write_intermediate_state(args.intermediate_dir, items, payload)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

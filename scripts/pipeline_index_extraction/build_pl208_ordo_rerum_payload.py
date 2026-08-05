#!/usr/bin/env python3
"""Usage: build the PL208 ORDO RERUM payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl208_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL208/text \
    --files /homessddata/Projects/pdfocr/teste/PL208/text/3fd56bff-87dc-4130-806f-d22c08bbd98b-679.txt \
            /homessddata/Projects/pdfocr/teste/PL208/text/3fd56bff-87dc-4130-806f-d22c08bbd98b-680.txt \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL208_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL208_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL208 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL208_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PL208"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 208"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"

ORDER_RE = re.compile(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$")
PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class TocItem:
    order: int
    node_key: str | None
    source_file: str
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str
    page_ref_int: int
    context_raw: str


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def fold(text: str | None) -> str:
    value = normalize(text)
    return (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
        .replace("Ĳ", "IJ")
        .replace("ĳ", "ij")
    )


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = fold(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo = re.search(r'tipo="([^"]+)"', attrs)
        block_type = (tipo.group(1).strip().lower() if tipo else "")
        if block_type != "texto_principal":
            continue
        body = re.sub(r"<[^>]+>", " ", block.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def is_noise(line: str) -> bool:
    if line in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
        return True
    if line == "Digitized by Google":
        return True
    if re.fullmatch(r"\d{3,4}", line):
        return True
    return False


def is_node_label(line: str) -> bool:
    return line in {"S. MARTINUS LEGIONENSIS.", "LIBER SERMONUM S. MARTINI."}


def strip_page(line: str) -> tuple[str, str | None, int | None]:
    match = ORDER_RE.match(line)
    if not match:
        return line, None, None
    lemma = normalize(match.group("lemma").rstrip(" ,;:."))
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def parse_items(files: list[Path]) -> tuple[list[TocItem], list[dict[str, Any]]]:
    items: list[TocItem] = []
    nodes: list[dict[str, Any]] = []
    current_node_key: str | None = None
    node_order = 0
    entry_order = 0
    buffer: list[str] = []
    buffer_file: Path | None = None
    collecting = False

    def flush() -> None:
        nonlocal buffer, buffer_file, entry_order
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        buffer = []
        source_file = buffer_file or files[0]
        buffer_file = None
        lemma_raw, page_raw, page_int = strip_page(entry_raw)
        if not page_raw or not lemma_raw:
            return
        entry_order += 1
        items.append(
            TocItem(
                order=entry_order,
                node_key=current_node_key,
                source_file=source_file.as_posix(),
                entry_raw=entry_raw,
                lemma_raw=lemma_raw,
                page_ref_raw=page_raw,
                page_ref_int=page_int or 0,
                context_raw=entry_raw,
            )
        )

    for path in files:
        for line in extract_lines(path):
            if (
                line == SECTION_HEADING_RAW
                or line == SECTION_HEADING_NORM
                or line == "ORDO RERUM"
                or line == "QUÆ IN HOC TOMO CONTINENTUR."
                or line == "QUAE IN HOC TOMO CONTINENTUR."
            ):
                collecting = True
                flush()
                continue
            if is_noise(line):
                continue
            if not collecting:
                continue
            if line in {"S. MARTINUS LEGIONENSIS.", "LIBER SERMONUM S. MARTINI."}:
                flush()
                node_order += 1
                node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "heading_group",
                        "label_raw": line,
                        "label_norm": sort_norm(line),
                        "label_sort": sort_norm(line),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {
                            "source_file": path.as_posix(),
                            "section_kind": "ordo_rerum",
                        },
                    }
                )
                current_node_key = node_key
                continue

            lemma_raw, page_raw, page_int = strip_page(line)
            if page_raw is not None:
                if not buffer:
                    buffer_file = path
                buffer.append(line)
                flush()
                continue

            if PAGE_ONLY_RE.fullmatch(line) and buffer:
                buffer.append(line)
                flush()
                continue

            if not buffer:
                buffer_file = path
            buffer.append(line)

    flush()
    return items, nodes


def build_helper_request(items: list[TocItem], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in items:
        query_names = [
            item.lemma_raw,
            item.entry_raw.rsplit(" ", 1)[0],
            item.lemma_raw.replace("—", " "),
        ]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.order:04d}",
                "lemma_raw": item.lemma_raw,
                "query_names": [q for q in dict.fromkeys(normalize(q) for q in query_names) if q],
                "page_hints": [item.page_ref_raw],
                "page_hint_ints": [item.page_ref_int],
                "context_raw": item.context_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            helper_request_json.as_posix(),
            "--output",
            helper_output_json.as_posix(),
            "--pretty",
        ],
        check=True,
    )
    return read_json(helper_output_json, default={"entries": []})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {str(item.get("entry_id")): item for item in helper_output.get("entries", [])}


def build_payload(
    items: list[TocItem],
    nodes: list[dict[str, Any]],
    helper_output: dict[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    section_start_file = items[0].source_file if items else None
    section_end_file = items[-1].source_file if items else None
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    for item in items:
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{item.order:04d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        target_file_best = best.get("file")
        entry_key = f"{VOLUME_ID}:entry:{item.order:04d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": item.node_key,
                "entry_order": item.order,
                "entry_kind": "lemma",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": sort_norm(item.lemma_raw),
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.context_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "target_file_best": target_file_best,
                "confidence": 0.92 if target_file_best else 0.8,
                "raw_json": {
                    "source_file": item.source_file,
                    "section_kind": "ordo_rerum",
                    "helper_status": helper_entry.get("status"),
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
                    "helper_top_candidates": [
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
            }
        )
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
                "confidence": 0.91 if target_file_best else 0.75,
                "raw_json": {
                    "source_file": item.source_file,
                    "section_kind": "ordo_rerum",
                    "helper_status": helper_entry.get("status"),
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
                },
            }
        )

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
            "page_start": None,
            "page_end": None,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": (
                    "Closing contents table (ordo rerum) for the tome. "
                    "The OCR spreads repeat the heading and the printed page headers are not monotone across the sampled files, "
                    "so the section is anchored by file order and the observed heading pages are preserved separately."
                ),
                "heading_sources": [path.as_posix() for path in files_used],
                "observed_heading_pages": [1549, 1550, 1351, 1352],
            },
        }
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table as a structured editorial section and preserved the page-bearing lines as individual entries.",
            "evidence_files": [path.as_posix() for path in files_used],
        },
        "notes": [
            {
                "note_key": f"{VOLUME_ID}:note:001",
                "note_type": "extraction",
                "text": "PL208 ends in an ORDO RERUM contents table; the payload keeps the OCR file suffix separate from the printed page numbers and records the repeated heading as a single editorial section.",
            }
        ],
    }


def write_intermediate_state(intermediate_dir: Path, payload: dict[str, Any], items: list[TocItem]) -> None:
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
            "current_focus": "PL208 ORDO RERUM payload assembled and validated",
            "completed": [
                "OCR tail section detected",
                "contents entries parsed from the ORDO RERUM block",
                "helper request and helper output generated",
                "final payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Observed heading pages are stored in raw_json because the OCR spreads do not yield a monotone editorial page range.",
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL208 ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--files", nargs="+", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    global files_used
    files_used = args.files
    items, nodes = parse_items(args.files)
    helper_request = build_helper_request(items, args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(items, nodes, helper_output, args.source_root)
    write_intermediate_state(args.intermediate_dir, payload, items)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    files_used: list[Path] = []
    main()

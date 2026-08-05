#!/usr/bin/env python3
"""Usage: build the PG001 closing ORDO RERUM payload and helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg001_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG001/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG001_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG001_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG001 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG001_alphabetical_indices.json
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


VOLUME_ID = "PG001"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 1"
SECTION_KEY = f"{VOLUME_ID}:section:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 1477
SECTION_PAGE_END = 1484
SECTION_FILE_START_SEQ = 739
SECTION_FILE_END_SEQ = 742


@dataclass(slots=True)
class TocItem:
    item_order: int
    kind: str
    text_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    source_file: str
    line_index: int
    parent_node_key: str | None
    node_level: int | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def lemma_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    return value.lower() if value else None


def lemma_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    return value.lower() if value else None


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize_space(raw_line)
            if line:
                lines.append(line)
    return lines


def page_header_line(line: str) -> bool:
    return bool(
        re.fullmatch(r"\d{3,4}", line)
        or re.fullmatch(r"\d{3,4}\s+.*\s+\d{3,4}", line)
        or re.search(r"\bORDO RERUM\b", line)
        or re.search(r"\bQU[ÆAE] IN HOC TOMO CONTINENTUR\.?\b", line)
    )


def strip_trailing_page(line: str) -> tuple[str, str | None, int | None]:
    m = re.search(r"(?P<text>.*?)(?:\s+)(?P<page>\d{1,4})$", line)
    if not m:
        return line, None, None
    return m.group("text").strip(), m.group("page"), int(m.group("page"))


def is_heading_candidate(line: str) -> bool:
    if not line:
        return False
    if re.search(r"\d{1,4}$", line):
        return False
    if line.isdigit():
        return False
    if any(line.startswith(prefix) for prefix in ("S. ", "D. ", "EPISTOLA", "EPIST.", "LIBER", "CAP.", "Articulus", "RECOGNITIONES", "CONSTITUTIONES", "PASSIO", "EXPOSITIO", "VITA", "ADDENDA", "ADNOTATIO", "PROŒMIA", "Proœmia", "Judicium", "Appendix", "Canones apostolici")):
        return True
    if line.isupper() and len(line.split()) <= 12:
        return True
    if line.endswith(".") and len(line.split()) <= 16 and len(line) <= 140:
        return True
    return False


def section_heading_level(line: str) -> int:
    if any(
        line.startswith(prefix)
        for prefix in (
            "S. CLEMENTIS OPERA GENUINA",
            "S. CLEMENTIS OPERA DUBIA",
            "CONSTITUTIONES APOSTOLICÆ",
            "CONSTITUTIONES SANCTORUM APOSTOLORUM",
            "RECOGNITIONES S. CLEMENTIS",
        )
    ):
        return 1
    if line.startswith("EPISTOLA") or line.startswith("LIBER") or line.startswith("CAP.") or line.startswith("Articulus") or line.startswith("PROŒMIA") or line.startswith("Proœmia") or line.startswith("Judicium"):
        return 2
    return 1


def normalize_query_names(text: str) -> list[str]:
    value = normalize_space(text)
    if not value:
        return []
    stripped = re.sub(r"^\s*(?:§\s*[IVXLCDM]+\.\s*—\s*|CAP\.\s*[IVXLCDM]+\.\s*—\s*|Articulus\s+[IVXLCDM]+\.\s*—\s*)", "", value)
    stripped = re.sub(r"^\s*(?:LIBER|EPISTOLA|EPIST\.)\s+[IVXLCDM0-9]+\.\s*(?:—\s*)?", "", stripped)
    stripped = re.sub(r"\s+\d{1,4}$", "", stripped)
    candidates = [value]
    if stripped and stripped != value:
        candidates.append(stripped)
    if "—" in value:
        tail = value.split("—", 1)[1].strip()
        if tail:
            candidates.append(tail)
    return [q for q in dict.fromkeys(normalize_space(q) for q in candidates if normalize_space(q))]


def join_buffer(buffer: list[str]) -> str:
    if not buffer:
        return ""
    text = buffer[0]
    for piece in buffer[1:]:
        if text.endswith("-"):
            text = text[:-1] + piece.lstrip()
        else:
            text += " " + piece
    return normalize_space(text)


def parse_toc_items(source_root: Path) -> tuple[list[TocItem], list[dict[str, Any]]]:
    files = [source_root / f"15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-{seq}.txt" for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)]
    lines_with_source: list[tuple[str, str, int]] = []
    for path in files:
        for idx, line in enumerate(extract_lines(path), 1):
            lines_with_source.append((line, str(path), idx))

    items: list[TocItem] = []
    nodes: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_source: str | None = None
    buffer_line_index: int | None = None
    item_order = 0
    node_order = 0
    current_parent_node_key: str | None = None
    last_node_at_level: dict[int, str] = {}

    def flush_buffer(source_file: str | None = None, line_index: int | None = None) -> None:
        nonlocal item_order, buffer, buffer_source, buffer_line_index, current_parent_node_key
        if not buffer:
            return
        item_order += 1
        text = join_buffer(buffer)
        lemma, page_ref_raw, page_ref_int = strip_trailing_page(text)
        items.append(
            TocItem(
                item_order=item_order,
                kind="entry",
                text_raw=text,
                page_ref_raw=page_ref_raw,
                page_ref_int=page_ref_int,
                source_file=source_file or buffer_source or "",
                line_index=line_index or buffer_line_index or 0,
                parent_node_key=current_parent_node_key,
            )
        )
        buffer = []
        buffer_source = None
        buffer_line_index = None

    def add_node(text: str, source_file: str, line_index: int) -> str:
        nonlocal node_order, current_parent_node_key, last_node_at_level
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        level = section_heading_level(text)
        parent = None
        if level > 1:
            for candidate_level in range(level - 1, 0, -1):
                if candidate_level in last_node_at_level:
                    parent = last_node_at_level[candidate_level]
                    break
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": parent,
                "node_order": node_order,
                "node_kind": "heading_group",
                "label_raw": text,
                "label_norm": lemma_norm(text),
                "label_sort": lemma_sort(text),
                "node_level": level,
                "confidence": 0.96,
                "raw_json": {
                    "source_file": source_file,
                    "line_index": line_index,
                    "node_level_reason": "major_contents_heading" if level == 1 else "subheading_within_contents",
                },
            }
        )
        for candidate_level in list(last_node_at_level):
            if candidate_level >= level:
                del last_node_at_level[candidate_level]
        last_node_at_level[level] = node_key
        current_parent_node_key = node_key
        return node_key

    for line, source_file, line_index in lines_with_source:
        if page_header_line(line):
            continue
        if is_heading_candidate(line) and not re.search(r"\d{1,4}$", line):
            flush_buffer(source_file=source_file, line_index=line_index)
            add_node(line, source_file, line_index)
            continue
        if re.search(r"\d{1,4}$", line):
            flush_buffer(source_file=source_file, line_index=line_index)
            item_order += 1
            lemma, page_ref_raw, page_ref_int = strip_trailing_page(line)
            items.append(
                TocItem(
                    item_order=item_order,
                    kind="entry",
                    text_raw=line,
                    page_ref_raw=page_ref_raw,
                    page_ref_int=page_ref_int,
                    source_file=source_file,
                    line_index=line_index,
                    parent_node_key=current_parent_node_key,
                )
            )
            continue
        buffer.append(line)
        if buffer_source is None:
            buffer_source = source_file
            buffer_line_index = line_index

    flush_buffer()
    return items, nodes


def build_helper_request(source_root: Path, items: list[TocItem]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in items:
        if item.page_ref_int is None:
            continue
        lemma, _, _ = strip_trailing_page(item.text_raw)
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.item_order:04d}",
                "lemma_raw": lemma,
                "query_names": normalize_query_names(lemma),
                "page_hints": [item.page_ref_raw] if item.page_ref_raw else [],
                "page_hint_ints": [item.page_ref_int],
                "context_raw": item.text_raw,
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
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(source_root: Path, items: list[TocItem], nodes: list[dict[str, Any]], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_by_id = helper_index(helper_output)
    section_start_file = str(source_root / f"15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-{SECTION_FILE_START_SEQ}.txt")
    section_end_file = str(source_root / f"15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-{SECTION_FILE_END_SEQ}.txt")

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
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "closing_ordo_rerum_table_of_contents recovered from files 739-742; file 738 is preceding addenda content, not part of the contents table.",
                "evidence_files": [
                    str(source_root / f"15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-{seq}.txt")
                    for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)
                ],
                "heading_evidence": [
                    {
                        "file": str(source_root / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-742.txt"),
                        "text": SECTION_HEADING_RAW,
                    }
                ],
                "ocr_note": "File 739 header OCR reads 4477/4478; context and neighboring headers indicate it is the 1477/1478 contents page.",
            },
        }
    ]

    out_entries: list[dict[str, Any]] = []
    out_refs: list[dict[str, Any]] = []

    for item in items:
        entry_key = f"{VOLUME_ID}:entry:{item.item_order:04d}"
        helper_entry = helper_by_id.get(f"{VOLUME_ID.lower()}_{item.item_order:04d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        helper_status = helper_entry.get("status")
        target_file_best = best.get("file")
        target_prob = best.get("probability")
        if target_file_best is None:
            target_file_best = None
        confidence = 0.93 if item.page_ref_int is not None else 0.88
        if helper_status == "ambiguous":
            confidence = min(confidence, 0.79)
        if helper_status == "unresolved":
            confidence = min(confidence, 0.68)

        lemma, _, _ = strip_trailing_page(item.text_raw)
        out_entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": item.parent_node_key,
                "entry_order": item.item_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": lemma_norm(lemma),
                "lemma_sort": lemma_sort(lemma),
                "entry_raw": item.text_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "source_file": item.source_file,
                    "line_index": item.line_index,
                    "line_class": "entry_line",
                    "section_kind": "ordo_rerum",
                    "page_ref_source": item.page_ref_raw,
                    "helper_status": helper_status,
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
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

        if item.page_ref_raw is None:
            continue

        out_refs.append(
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
                "target_file_probability": target_prob,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item.source_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": item.source_file,
                    "line_index": item.line_index,
                    "helper_status": helper_status,
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best or None,
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

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the closing Ordo Rerum contents table from OCR files 739-742 and preserved the page-linked contents lines as individual entries.",
        "evidence_files": [
            str(source_root / f"15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-{seq}.txt")
            for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)
        ],
    }

    notes = [
        "The visible contents-table heading is repeated across multiple OCR files; the cleanest section heading is the full form on file 742.",
        "File 738 is preceding addenda content and was excluded from the Ordo Rerum section.",
        "The OCR header on file 739 misreads the running page number as 4477/4478; neighboring files show the intended 1477/1478 sequence.",
    ]

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
        "nodes": nodes,
        "entries": out_entries,
        "refs": out_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG001 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve the closing Ordo Rerum table and preserve the OCR page-linked contents lines.",
        "completed": [
            "identified the section as Ordo Rerum",
            "parsed OCR lines from files 739-742",
        ],
        "pending": [
            "run index_target_locator on the page-linked contents lines",
            "write the final payload and validate it",
        ],
        "blocked": [],
        "notes": [
            "Keep the section start distinct from the preceding addenda content on file 738.",
            "Page hints are editorial page numbers, not OCR file suffixes.",
        ],
    }
    write_json(todo_path, todo)

    items, nodes = parse_toc_items(args.source_root)
    write_json(args.intermediate_dir / "items.json", [asdict(item) for item in items])
    write_json(args.intermediate_dir / "nodes.json", nodes)

    helper_request = build_helper_request(args.source_root, items)
    write_json(args.helper_request_json, helper_request)
    write_json(args.intermediate_dir / "helper_request.json", helper_request)

    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    write_json(args.intermediate_dir / "helper_output.json", helper_output)

    payload = build_payload(args.source_root, items, nodes, helper_output)
    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "payload.json", payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

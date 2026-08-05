#!/usr/bin/env python3
"""Build the PG023 ORDO RERUM helper request and final alphabetical payload.

Run:
  python scripts/pipeline_index_extraction/build_pg023_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG023/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG023_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG023_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG023 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG023_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PG023"
COLLECTION = "PG"
SECTION_KEY = f"{VOLUME_ID}:section:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM, QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 1397
SECTION_PAGE_END = 1400
SECTION_FILE_START = "c638fd5a-0c2b-4000-a488-c325499ad130-703.txt"
SECTION_FILE_END = "c638fd5a-0c2b-4000-a488-c325499ad130-704.txt"
ENTRY_FILES = [703, 704]

PAGE_ONLY_RE = re.compile(r"^\d{3,4}$")
PAGE_AT_END_RE = re.compile(r"^(?P<text>.*?)(?:\s+)(?P<page>\d{1,4})$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ascii_norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\n", " ")
    text = text.replace("œ", "oe").replace("Œ", "oe").replace("æ", "ae").replace("Æ", "ae")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text or None


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def extract_block_lines(path: Path) -> tuple[str | None, list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    heading = None
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
            if not line:
                continue
            if tipo == "cabecalho" and heading is None and "ORDO RERUM" in line:
                heading = re.sub(r"^\d{1,4}\s+", "", line)
                heading = re.sub(r"\s+\d{1,4}$", "", heading).strip()
            if tipo == "texto_principal":
                lines.append(line)
    return heading, lines


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


def parse_entries(source_root: Path) -> tuple[str, list[dict[str, Any]]]:
    files = [source_root / f"c638fd5a-0c2b-4000-a488-c325499ad130-{seq}.txt" for seq in ENTRY_FILES]
    all_lines: list[tuple[str, str, int]] = []
    section_heading = None
    for path in files:
        heading, lines = extract_block_lines(path)
        if section_heading is None and heading:
            section_heading = heading
        for idx, line in enumerate(lines, start=1):
            all_lines.append((line, str(path), idx))

    entries: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_source: str | None = None
    buffer_line_index: int | None = None

    def flush() -> None:
        nonlocal buffer, buffer_source, buffer_line_index
        if not buffer:
            return
        text = join_buffer(buffer)
        m = PAGE_AT_END_RE.match(text)
        if not m:
            buffer = []
            buffer_source = None
            buffer_line_index = None
            return
        lemma = m.group("text").strip()
        page = m.group("page")
        entries.append(
            {
                "source_file": buffer_source,
                "source_line_index": buffer_line_index,
                "entry_raw": text,
                "lemma_raw": lemma,
                "page_ref_raw": page,
                "page_ref_int": int(page),
            }
        )
        buffer = []
        buffer_source = None
        buffer_line_index = None

    for line, source_file, line_index in all_lines:
        if PAGE_ONLY_RE.fullmatch(line):
            continue
        if line == "Digitized by Google" or line.startswith("FINIS TOMI VICESIMI TERTII"):
            continue
        if not buffer:
            buffer_source = source_file
            buffer_line_index = line_index
        buffer.append(line)
        if PAGE_AT_END_RE.match(line):
            flush()

    flush()
    if section_heading is None:
        section_heading = SECTION_HEADING_RAW
    return section_heading, entries


def build_helper_request(source_root: Path, parsed_entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for idx, item in enumerate(parsed_entries, start=1):
        lemma_raw = item["lemma_raw"]
        page_ref_raw = item["page_ref_raw"]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_ordo_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw],
                "page_hints": [page_ref_raw],
                "page_hint_ints": [int(page_ref_raw)],
                "context_raw": item["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(source_root: Path, parsed: dict[str, Any], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_by_entry_id: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            helper_by_entry_id[str(entry_id)] = item

    entries = []
    refs = []
    for idx, item in enumerate(parsed["entries"], start=1):
        entry_id = f"{VOLUME_ID.lower()}_ordo_{idx:04d}"
        helper_item = helper_by_entry_id.get(entry_id, {})
        best_candidate = helper_item.get("best_candidate") or {}
        probability = best_candidate.get("probability")
        target_file = best_candidate.get("file")
        status = helper_item.get("status")
        lemma_raw = item["lemma_raw"]
        page_ref_raw = item["page_ref_raw"]
        page_ref_int = item["page_ref_int"]
        confidence = float(probability) if isinstance(probability, (int, float)) else (0.8 if status == "resolved" else 0.55)
        entry_key = f"{VOLUME_ID}:entry:{idx:06d}"
        source_file = item["source_file"]
        helper_trimmed = {
            "status": status,
            "candidate_role": helper_item.get("best_candidate", {}).get("candidate_role"),
            "reason_summary": helper_item.get("best_candidate", {}).get("reason_summary"),
            "best_candidate": best_candidate or None,
            "candidates": helper_item.get("candidates", [])[:3] if isinstance(helper_item.get("candidates"), list) else [],
        }
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": idx,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": ascii_norm(lemma_raw),
                "lemma_sort": ascii_norm(lemma_raw),
                "entry_raw": item["entry_raw"],
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_ref_int,
                "section_start_file": f"{source_root}/{SECTION_FILE_START}",
                "editorial_anchor_file": source_file,
                "target_file_best": target_file,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_file": source_file,
                    "source_line_index": item.get("source_line_index"),
                    "page_hints": [page_ref_raw],
                    "page_hint_ints": [page_ref_int],
                    "helper": helper_trimmed,
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": page_ref_raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": float(probability) if isinstance(probability, (int, float)) else None,
                "section_start_file": f"{source_root}/{SECTION_FILE_START}",
                "editorial_anchor_file": source_file,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_file": source_file,
                    "source_line_index": item.get("source_line_index"),
                    "helper": helper_trimmed,
                },
            }
        )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": "Patrologia Graeca 23",
        },
        "sections": [
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
                "file_start": f"{source_root}/{SECTION_FILE_START}",
                "file_end": f"{source_root}/{SECTION_FILE_END}",
                "confidence": 0.99,
                "raw_json": {
                    "source_files": [
                        f"{source_root}/{SECTION_FILE_START}",
                        f"{source_root}/{SECTION_FILE_END}",
                    ],
                    "section_kind_reason": "Closing table of contents; the printed heading is ORDO RERUM, not an alphabetical index proper.",
                    "page_note": "OCR header on file 704 reads 4399/4400, but the volume sequence and contents pages indicate the closing spread is 1399/1400.",
                },
            }
        ],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents lines from files 703-704 and resolved their cited page targets with the locator helper.",
            "evidence_files": [
                f"{source_root}/{SECTION_FILE_START}",
                f"{source_root}/{SECTION_FILE_END}",
            ],
        },
        "notes": [
            "This volume ends with an ORDO RERUM table of contents rather than an alphabetical subject index.",
            "The 704 header OCR appears to misread the final spread as 4399/4400; the section itself is still the closing 1399/1400 spread.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--helper-request-json", required=True)
    parser.add_argument("--helper-output-json", required=True)
    parser.add_argument("--intermediate-dir", required=True)
    parser.add_argument("--output-file", required=True)
    args = parser.parse_args()

    source_root = Path(args.source_root)
    helper_request_path = Path(args.helper_request_json)
    helper_output_path = Path(args.helper_output_json)
    intermediate_dir = Path(args.intermediate_dir)
    output_file = Path(args.output_file)

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = intermediate_dir / "todo.json"

    section_heading, parsed_entries = parse_entries(source_root)
    parsed_payload = {
        "volume_id": VOLUME_ID,
        "section_heading": section_heading,
        "entries": parsed_entries,
    }
    write_json(intermediate_dir / "parsed_entries.json", parsed_payload)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve PG023 ORDO RERUM contents lines and build the final payload",
        "completed": [
            "tail OCR inspected",
            "contents lines parsed",
        ],
        "pending": [
            "run locator helper",
            "assemble final payload",
        ],
        "blocked": [],
        "notes": [
            "Use the closing spread in files 703-704 only.",
            "Treat the OCR header on file 704 as a likely 1399/1400 misread.",
        ],
    }
    write_json(todo_path, todo)

    helper_request = build_helper_request(source_root, parsed_entries)
    write_json(helper_request_path, helper_request)

    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(helper_request_path),
            "--output",
            str(helper_output_path),
            "--pretty",
        ],
        check=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )

    helper_output = load_json(helper_output_path)
    payload = build_payload(source_root, parsed_payload, helper_output)
    write_json(output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("helper output resolved")
    todo["completed"].append("final payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

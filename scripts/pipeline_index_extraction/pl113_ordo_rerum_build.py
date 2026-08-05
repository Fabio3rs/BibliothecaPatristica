#!/usr/bin/env python3
"""Usage: build the PL113 closing ORDO RERUM payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl113_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL113/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL113_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL113_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL113 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL113_alphabetical_indices.json
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


VOLUME_ID = "PL113"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 113"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SOURCE_FILE_SEQ = 662
SOURCE_FILE_NAME = f"b31bfd62-4a16-447b-a871-d6db62c91853-{SOURCE_FILE_SEQ}.txt"
SECTION_START_PAGE = 4515
SECTION_END_PAGE = 4516


@dataclass(slots=True)
class TocItem:
    entry_order: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    query_names: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_page_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo = re.search(r'tipo="([^"]+)"', attrs)
        if (tipo.group(1).strip().lower() if tipo else "") not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = norm(raw_line)
            if line:
                lines.append(line)
    return lines


def page_ref_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        return 0


def combine_lines(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if left.endswith("-"):
        return f"{left[:-1]}{right}"
    if left.endswith("—"):
        return f"{left} {right}"
    if left.endswith(".") or left.endswith(":"):
        return f"{left} {right}"
    return f"{left} {right}"


def parse_toc(lines: list[str]) -> list[TocItem]:
    toc_start = None
    toc_end = None
    for idx, line in enumerate(lines):
        if line == "ORDO RERUM" and idx + 1 < len(lines) and lines[idx + 1].startswith("QUÆ IN HOC TOMO CONTINENTUR"):
            toc_start = idx
            break
    if toc_start is None:
        raise SystemExit("Could not locate ORDO RERUM heading in PL113 OCR.")
    for idx in range(toc_start, len(lines)):
        if lines[idx].startswith("FINIS TOMI CENTESIMI DECIMI TERTII"):
            toc_end = idx
            break
    if toc_end is None:
        toc_end = len(lines)

    content = lines[toc_start + 2 : toc_end]
    items: list[TocItem] = []
    order = 0
    i = 0
    while i < len(content):
        line = content[i]
        if i == 0 and line.startswith("WALAFRIDUS STRABUS, FULDENSIS") and i + 1 < len(content):
            line = combine_lines(line, content[i + 1])
            i += 2
            order += 1
            items.append(
                TocItem(
                    entry_order=order,
                    entry_raw=line,
                    lemma_raw=line,
                    page_ref_raw=None,
                    page_ref_int=None,
                    query_names=[],
                )
            )
            continue
        if line.startswith("WALAFRIDI OPERUM PARS PRIMA.") and i + 1 < len(content):
            line = combine_lines(line, content[i + 1])
            i += 2
            order += 1
            items.append(
                TocItem(
                    entry_order=order,
                    entry_raw=line,
                    lemma_raw=line,
                    page_ref_raw=None,
                    page_ref_int=None,
                    query_names=[],
                )
            )
            continue

        if line.endswith("-") and i + 1 < len(content) and re.search(r"\d{1,4}$", content[i + 1]):
            line = combine_lines(line, content[i + 1])
            i += 2
        elif i + 1 < len(content) and re.fullmatch(r"\d{1,4}", content[i + 1]):
            line = combine_lines(line, content[i + 1])
            i += 2
        else:
            i += 1

        order += 1
        match = re.search(r"(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$", line)
        lemma_raw = line
        page_raw = None
        page_int = None
        if match:
            lemma_raw = match.group("lemma").strip()
            page_raw = match.group("page")
            page_int = page_ref_int(page_raw)
        query_names = [lemma_raw]
        if lemma_raw.upper().startswith("LIBER "):
            query_names.append(lemma_raw[6:].strip())
        if lemma_raw.upper().startswith("GLOSSA ORDINARIA"):
            query_names.append("GLOSSA ORDINARIA")
        if lemma_raw.upper().startswith("CANTICUM CANTICORUM"):
            query_names.append("Canticum")
        if lemma_raw.upper().startswith("LIBER ECCLE"):
            query_names.append("Ecclesiastes")
        if lemma_raw.upper().startswith("LIBER SAPIENT"):
            query_names.append("Sapientia")
        if lemma_raw.upper().startswith("LIBER ECCLI"):
            query_names.append("Ecclesiasticus")
        if lemma_raw.upper().startswith("PROPHETIA ISAI"):
            query_names.append("Isaias")
        query_names = [q for q in dict.fromkeys(q for q in query_names if q)]
        items.append(
            TocItem(
                entry_order=order,
                entry_raw=line,
                lemma_raw=lemma_raw,
                page_ref_raw=page_raw,
                page_ref_int=page_int,
                query_names=query_names,
            )
        )
    return items


def build_helper_request(source_root: Path, items: list[TocItem]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in items:
        if item.page_ref_raw is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.entry_order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names,
                "page_hints": [item.page_ref_raw],
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


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    ocr_file = source_root / SOURCE_FILE_NAME
    lines = load_page_lines(ocr_file)
    items = parse_toc(lines)
    helper_request = build_helper_request(source_root, items)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_map = helper_index(helper_output)

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": SECTION_START_PAGE,
        "page_end": SECTION_END_PAGE,
        "file_start": str(ocr_file),
        "file_end": str(ocr_file),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Editorial contents table at the end of the tome, listing works and printed-page anchors.",
            "evidence_files": [str(ocr_file)],
            "helper_status": None,
        },
    }

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for item in items:
        entry_key = f"{VOLUME_ID}:entry:{item.entry_order:04d}"
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{item.entry_order:03d}")
        best = helper_entry.get("best_candidate") if helper_entry else None
        candidates = helper_entry.get("candidates") if helper_entry else []
        best_file = best.get("file") if best else None
        best_prob = best.get("probability") if best else None
        helper_raw = helper_entry if helper_entry else None

        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": item.entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": sort_norm(item.lemma_raw),
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": str(ocr_file),
                "editorial_anchor_file": str(ocr_file),
                "target_file_best": best_file if best_file else str(ocr_file),
                "confidence": 0.78 if item.page_ref_raw is not None else 0.72,
                "raw_json": {
                    "source_file": str(ocr_file),
                    "section_kind": "ordo_rerum",
                    "entry_kind_reason": "contents line from the volume's final ORDO RERUM table",
                    "page_hints": [item.page_ref_int] if item.page_ref_int is not None else [],
                    "helper": helper_raw,
                },
            }
        )

        if item.page_ref_raw is None:
            continue
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": item.page_ref_raw,
                "page_ref_raw": item.page_ref_raw,
                "page_ref_int": item.page_ref_int if item.page_ref_int is not None else 0,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": best_file,
                "target_file_probability": best_prob,
                "section_start_file": str(ocr_file),
                "editorial_anchor_file": str(ocr_file),
                "confidence": 0.66 if item.page_ref_int in {0, 90} else 0.9,
                "raw_json": {
                    "source_file": str(ocr_file),
                    "section_kind": "ordo_rerum",
                    "helper": helper_raw,
                },
            }
        )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM contents table from the OCR tail; "
            "the final four page numbers are preserved literally as printed OCR artifacts."
        ),
        "evidence_files": [str(ocr_file)],
    }

    notes = [
        "The only detected section is the closing ORDO RERUM contents table.",
        "OCR page numbers 0000 and 0090 are preserved literally and not normalized away.",
        "Helper resolution was used for the page-linked contents lines; the two top heading lines were kept as heading_group entries without refs.",
    ]

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL113 ORDO RERUM alphabetical payload.")
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
        "current_focus": "Resolve the closing ORDO RERUM table and preserve OCR page-number corruption literally.",
        "completed": [
            "identified the final ORDO RERUM section",
            "parsed the TOC lines from OCR file 662",
        ],
        "pending": [
            "run index_target_locator on page-linked contents lines",
            "assemble and validate the final payload",
        ],
        "blocked": [],
        "notes": [
            "Use the OCR file 662 as the only section evidence file.",
            "Keep 0000 and 0090 as literal OCR references in the payload.",
        ],
    }
    write_json(todo_path, todo)

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()

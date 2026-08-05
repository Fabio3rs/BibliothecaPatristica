#!/usr/bin/env python3
"""Usage: build the PG146 ORDO RERUM helper request and final payload.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pg146_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG146/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG146_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG146_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG146 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG146_alphabetical_indices.json
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

VOLUME_ID = "PG146"
COLLECTION = "PG"
SECTION_KEY = f"{VOLUME_ID}:section:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_KIND_REASON = "Closing table of contents; the printed heading is ORDO RERUM, not an alphabetical index proper."
ENTRY_PREFIX_RE = re.compile(r"(?i)^cap\.\s*(?P<num>[ivxlcdm]+|\d+)\.?\s*(?:[—-]\s*)?(?P<body>.*)$")
LIBER_RE = re.compile(r"(?i)^liber\s+(?P<body>.*)$")
PAGE_END_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})$")


@dataclass(slots=True)
class ParsedLine:
    source_file: str
    source_line_index: int
    text: str


@dataclass(slots=True)
class ParsedNode:
    node_key: str
    label_raw: str
    label_norm: str
    label_sort: str
    source_file: str
    source_line_index: int


@dataclass(slots=True)
class ParsedEntry:
    entry_key: str
    source_file: str
    source_line_index: int
    source_file_end: str
    source_line_index_end: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    chapter_raw: str
    chapter_num: str
    parent_node_key: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def ascii_norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\n", " ")
    text = text.replace("œ", "oe").replace("Œ", "oe").replace("æ", "ae").replace("Æ", "ae")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text or None


def strip_digits_prefix(text: str) -> str:
    return re.sub(r"^\d{1,4}\s+", "", text)


def extract_ocr_lines(path: Path) -> list[ParsedLine]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[ParsedLine] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo != "texto_principal":
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for idx, raw_line in enumerate(content.splitlines(), start=1):
            text = normalize_space(raw_line)
            if not text or text == "Digitized by Google":
                continue
            lines.append(ParsedLine(source_file=path.as_posix(), source_line_index=idx, text=text))
    return lines


def locate_section_file(source_root: Path) -> Path:
    for path in sorted(source_root.glob("*.txt"), key=lambda p: (int(re.search(r"-(\d+)\.txt$", p.name).group(1)), p.name)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "ORDO RERUM" in text and "QUÆ IN HOC TOMO CONTINENTUR." in text:
            return path
        if "ORDO RERUM" in text and "QUAE IN HOC TOMO CONTINENTUR." in text:
            return path
    raise SystemExit(f"Could not locate ORDO RERUM in {source_root}")


def parse_books_and_entries(files: list[Path]) -> tuple[list[ParsedNode], list[ParsedEntry], Path, Path]:
    nodes: list[ParsedNode] = []
    entries: list[ParsedEntry] = []
    current_node_key: str | None = None
    current_node_index = 0
    current_entry_lines: list[ParsedLine] = []
    current_entry_num: str | None = None
    current_entry_parent: str | None = None
    current_entry_index = 0
    section_start_file = files[0]
    section_end_file = files[-1]
    last_structural_file = files[0]

    def flush_entry() -> None:
        nonlocal current_entry_lines, current_entry_num, current_entry_parent, current_entry_index, last_structural_file
        if not current_entry_lines:
            return
        raw_text = normalize_space(" ".join(piece.text for piece in current_entry_lines))
        page_match = PAGE_END_RE.match(raw_text)
        if page_match:
            body = normalize_space(page_match.group("body").rstrip(" ,;:."))
            page_raw = page_match.group("page")
            page_int = int(page_raw)
        else:
            body = raw_text
            page_raw = None
            page_int = None
        chapter_match = ENTRY_PREFIX_RE.match(body)
        if chapter_match:
            chapter_num = chapter_match.group("num")
            lemma_raw = normalize_space(chapter_match.group("body").rstrip(" ,;:."))
        else:
            chapter_num = current_entry_num or ""
            lemma_raw = normalize_space(body.rstrip(" ,;:."))
        current_entry_index += 1
        entry_key = f"{VOLUME_ID}:entry:{current_entry_index:06d}"
        entries.append(
            ParsedEntry(
                entry_key=entry_key,
                source_file=current_entry_lines[0].source_file,
                source_line_index=current_entry_lines[0].source_line_index,
                source_file_end=current_entry_lines[-1].source_file,
                source_line_index_end=current_entry_lines[-1].source_line_index,
                entry_raw=raw_text,
                lemma_raw=lemma_raw,
                page_ref_raw=page_raw,
                page_ref_int=page_int,
                chapter_raw=body,
                chapter_num=chapter_num,
                parent_node_key=current_entry_parent,
            )
        )
        current_entry_lines = []
        current_entry_num = None
        current_entry_parent = None

    for path in files:
        ocr_lines = extract_ocr_lines(path)
        for line in ocr_lines:
            text = line.text
            if text.startswith("Digitized by Google"):
                continue
            liber_match = LIBER_RE.match(text)
            if liber_match:
                flush_entry()
                current_node_index += 1
                node_label = normalize_space(text.rstrip("."))
                node_key = f"{VOLUME_ID}:node:liber:{current_node_index:03d}"
                nodes.append(
                    ParsedNode(
                        node_key=node_key,
                        label_raw=node_label,
                        label_norm=ascii_norm(node_label) or node_label.lower(),
                        label_sort=ascii_norm(node_label) or node_label.lower(),
                        source_file=line.source_file,
                        source_line_index=line.source_line_index,
                    )
                )
                current_node_key = node_key
                last_structural_file = path
                continue
            cap_match = ENTRY_PREFIX_RE.match(text)
            if cap_match:
                flush_entry()
                current_entry_num = cap_match.group("num")
                current_entry_parent = current_node_key
                current_entry_lines = [line]
                last_structural_file = path
                continue
            if current_entry_lines:
                current_entry_lines.append(line)

        # stop after the closing contents spread; later files are guards/ads.
        if current_entry_lines and path.name.endswith("-684.txt"):
            flush_entry()
            last_structural_file = path

    flush_entry()
    return nodes, entries, section_start_file, last_structural_file


def clean_query_name(text: str) -> str:
    value = normalize_space(text)
    value = re.sub(r"(?i)^cap\.\s*[ivxlcdm\d]+\.\s*(?:[—-]\s*)?", "", value)
    value = re.sub(r"\s+\d{1,4}$", "", value)
    value = value.replace("—", " ").replace("–", " ")
    value = normalize_space(value.rstrip(" ,;:."))
    return value


def make_query_names(lemma_raw: str, entry_raw: str) -> list[str]:
    variants: list[str] = []
    primary = clean_query_name(lemma_raw)
    if primary:
        variants.append(primary)
    raw_fold = ascii_norm(primary)
    if raw_fold:
        variants.append(raw_fold)
    entry_body = clean_query_name(entry_raw)
    if entry_body and entry_body not in variants:
        variants.append(entry_body)
    if entry_body:
        entry_fold = ascii_norm(entry_body)
        if entry_fold and entry_fold not in variants:
            variants.append(entry_fold)
    if len(variants) > 4:
        variants = variants[:4]
    return list(dict.fromkeys(variants))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_helper_request(source_root: Path, parsed_entries: list[ParsedEntry]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed_entries, start=1):
        query_names = make_query_names(item.lemma_raw, item.entry_raw)
        page_hint_raw = item.page_ref_raw or (str(item.page_ref_int) if item.page_ref_int is not None else "")
        entry = {
            "entry_id": f"{VOLUME_ID.lower()}_ordo_{idx:04d}",
            "lemma_raw": item.lemma_raw,
            "query_names": query_names,
            "page_hints": [page_hint_raw] if page_hint_raw else [],
            "page_hint_ints": [item.page_ref_int] if item.page_ref_int is not None else [],
            "context_raw": item.entry_raw,
        }
        entries.append(entry)
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": entries,
    }


def helper_entry_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


def choose_best_candidate(helper_item: dict[str, Any]) -> tuple[str | None, float | None, int | None, dict[str, Any]]:
    best = helper_item.get("best_candidate") or {}
    target_file = best.get("file")
    prob = best.get("probability")
    inferred_page = best.get("inferred_printed_page")
    return (str(target_file) if target_file else None, float(prob) if isinstance(prob, (int, float)) else None, int(inferred_page) if isinstance(inferred_page, int) else None, best)


def build_payload(source_root: Path, section_file: Path, section_end_file: Path, nodes: list[ParsedNode], parsed_entries: list[ParsedEntry], helper_output: dict[str, Any]) -> dict[str, Any]:
    helper_map = helper_entry_map(helper_output)
    section_start = section_file.as_posix()
    section_end = section_end_file.as_posix()
    section_page_start = 1275
    section_page_end = 1288

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
            "page_start": section_page_start,
            "page_end": section_page_end,
            "file_start": section_start,
            "file_end": section_end,
            "confidence": 0.99,
            "raw_json": {
                "source_files": [section_start, section_end],
                "section_kind_reason": SECTION_KIND_REASON,
                "volume_note": "The contents spread spans several OCR files and the later files after 684 are guards/advertising matter, not part of ORDO RERUM.",
            },
        }
    ]

    out_nodes: list[dict[str, Any]] = []
    for idx, node in enumerate(nodes, start=1):
        out_nodes.append(
            {
                "node_key": node.node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": idx,
                "node_kind": "heading_group",
                "label_raw": node.label_raw,
                "label_norm": node.label_norm,
                "label_sort": node.label_sort,
                "node_level": 1,
                "confidence": 0.96,
                "raw_json": {
                    "source_file": node.source_file,
                    "source_line_index": node.source_line_index,
                },
            }
        )

    out_entries: list[dict[str, Any]] = []
    out_refs: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed_entries, start=1):
        entry_id = f"{VOLUME_ID.lower()}_ordo_{idx:04d}"
        helper_item = helper_map.get(entry_id, {})
        target_file, target_prob, inferred_page, best_candidate = choose_best_candidate(helper_item)
        confidence = target_prob if target_prob is not None else 0.65
        ref_page = inferred_page if inferred_page is not None else item.page_ref_int
        ref_raw = str(ref_page) if ref_page is not None else item.page_ref_raw
        entry_key = item.entry_key
        out_entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": item.parent_node_key,
                "entry_order": idx,
                "entry_kind": "lemma",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": ascii_norm(item.lemma_raw),
                "lemma_sort": ascii_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": ref_page,
                "section_start_file": section_start,
                "editorial_anchor_file": item.source_file_end,
                "target_file_best": target_file,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_file": item.source_file,
                    "source_line_index": item.source_line_index,
                    "source_file_end": item.source_file_end,
                    "source_line_index_end": item.source_line_index_end,
                    "chapter_raw": item.chapter_raw,
                    "chapter_num": item.chapter_num,
                    "ocr_page_ref_raw": item.page_ref_raw,
                    "ocr_page_ref_int": item.page_ref_int,
                    "helper": {
                        "status": helper_item.get("status"),
                        "candidate_role": helper_item.get("candidate_role"),
                        "reason_summary": helper_item.get("reason_summary"),
                        "best_candidate": best_candidate or None,
                        "candidates": helper_item.get("candidates", [])[:3] if isinstance(helper_item.get("candidates"), list) else [],
                    },
                },
            }
        )
        out_refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": ref_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": target_prob,
                "section_start_file": section_start,
                "editorial_anchor_file": item.source_file_end,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_file": item.source_file,
                    "source_line_index": item.source_line_index,
                    "ocr_page_ref_raw": item.page_ref_raw,
                    "ocr_page_ref_int": item.page_ref_int,
                    "target_candidate": best_candidate or None,
                },
            }
        )

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved chapter targets with the locator helper plus local OCR inspection.",
        "evidence_files": [section_start, section_end],
    }

    notes = [
        "This volume ends with ORDO RERUM rather than a subject alphabetic index.",
        "Later OCR files after 684 are guards or advertising matter and were excluded from the contents extraction.",
        "Entry OCR is preserved in entry_raw; helper-resolved page targets are recorded separately in refs and raw_json.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": "Patrologia Graeca 146",
        },
        "sections": sections,
        "nodes": out_nodes,
        "entries": out_entries,
        "refs": out_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def update_todo(intermediate_dir: Path, status: str) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PG146 ORDO RERUM payload",
        "completed": [],
        "pending": [],
        "blocked": [],
        "notes": [status],
    }
    write_json(intermediate_dir / "todo.json", todo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root
    section_file = locate_section_file(source_root)
    section_seq = int(re.search(r"-(\d+)\.txt$", section_file.name).group(1))
    files = [source_root / f"{section_file.name.rsplit('-', 1)[0]}-{seq}.txt" for seq in range(section_seq, 685)]
    # Filter to files that exist and are part of the closing contents spread.
    files = [path for path in files if path.exists() and re.search(r"-(\d+)\.txt$", path.name) and int(re.search(r"-(\d+)\.txt$", path.name).group(1)) <= 684]
    if not files:
        raise SystemExit("No OCR files found for the closing contents spread.")

    nodes, parsed_entries, section_start_file, section_end_file = parse_books_and_entries(files)
    helper_request = build_helper_request(source_root, parsed_entries)
    args.helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.helper_request_json, helper_request)

    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(args.helper_request_json),
            "--output",
            str(args.helper_output_json),
            "--pretty",
        ],
        check=True,
    )

    helper_output = load_json(args.helper_output_json)
    payload = build_payload(source_root, section_start_file, section_end_file, nodes, parsed_entries, helper_output)

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": now_iso(),
        "section_file": section_start_file.as_posix(),
        "section_end_file": section_end_file.as_posix(),
        "entry_count": len(parsed_entries),
        "node_count": len(nodes),
    })
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    update_todo(args.intermediate_dir, "Parsed ORDO RERUM, resolved helper targets, and assembled the final payload.")
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

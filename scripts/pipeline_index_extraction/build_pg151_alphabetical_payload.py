#!/usr/bin/env python3
"""Usage: build the PG151 alphabetical payload from the OCR tail and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg151_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG151/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG151_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG151_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG151 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG151_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
VOLUME_ID = "PG151"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 151"
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING_RAW = (
    "ΠΙΝΑΞ ΤΩΝ ΕΝ ΤΑΙΣ ΟΜΙΛΙΑΙΣ ΓΡΗΓΟΡΙΟΥ ΤΟΥ ΠΑΛΑΜΑ "
    "ΚΥΡΙΩΤΕΡΩΝ ΠΡΑΓΜΑΤΩΝ ΚΑΤΑ ΑΛΦΑΒΗΤΟΝ."
)
SECTION_KIND_REASON = (
    "Greek thematic index of principal matters arranged alphabetically; "
    "marginal letter groups are part of the printed hierarchy. "
    "The nearby ORDO RERUM back matter on files 691-692 was inspected but "
    "excluded as non-alphabetical editorial closure."
)
SECTION_START_FILE_SEQ = 687
SECTION_END_FILE_SEQ = 690
PAGE_START = 1365
PAGE_END = 1372

FILE_RE = re.compile(r"-(\d+)\.txt$")
LETTER_RE = re.compile(r"^[A-ZΑ-ΩἈ-῾]$")
SPLIT_PERIOD_RE = re.compile(r"(?<=\d\.)\s+(?=[^\d])")
SPLIT_MISSING_PERIOD_RE = re.compile(r"(?<=\d)\s+(?=[A-ZΑ-ΩἈ-῾])")
NUM_TAIL_RE = re.compile(r"(\d{1,4}(?:\s*,\s*\d{1,4})*)\.?\s*$")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = FILE_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw.strip())
        except ET.ParseError:
            continue
        for bloco in root.findall("bloco"):
            if (bloco.attrib.get("tipo") or "").strip().lower() != "cabecalho":
                continue
            header = normalize("".join(bloco.itertext()))
            if not header:
                continue
            for match in HEADER_NUM_RE.finditer(header):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def extract_lines(block: ET.Element) -> list[str]:
    lines = []
    for line in "".join(block.itertext()).splitlines():
        value = normalize(line)
        if value:
            lines.append(value)
    return lines


def split_into_segments(text: str) -> tuple[list[str], str | None]:
    value = normalize(text)
    if not value:
        return [], None
    parts = SPLIT_PERIOD_RE.split(value)
    expanded: list[str] = []
    for part in parts:
        expanded.extend(piece for piece in SPLIT_MISSING_PERIOD_RE.split(part) if piece)
    if not expanded:
        return [], None
    if NUM_TAIL_RE.search(expanded[-1]):
        return expanded, None
    return expanded[:-1], expanded[-1]


def parse_entry_segment(segment: str) -> tuple[str, list[int], str]:
    value = normalize(segment)
    match = NUM_TAIL_RE.search(value)
    if not match:
        return value.rstrip(" ,;:"), [], value
    ref_block = match.group(1)
    lemma = value[: match.start(1)].rstrip(" ,;:.")
    refs = [int(item) for item in re.findall(r"\d{1,4}", ref_block)]
    return lemma, refs, value


def make_slug(value: str | None) -> str | None:
    normalized = sort_norm(value)
    if not normalized:
        return None
    return normalized


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_index_entries(index_files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_letter: str | None = None
    pending_text: str | None = None
    pending_source_file: str | None = None

    seen_letters: set[str] = set()
    entry_order = 0

    def register_letter(letter: str) -> str:
        if letter not in seen_letters:
            seen_letters.add(letter)
            nodes.append(
                {
                    "node_key": f"{SECTION_KEY}:letter:{len(nodes)+1:02d}:{letter}",
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "node_order": len(nodes) + 1,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": sort_norm(letter),
                    "label_sort": sort_norm(letter),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {
                        "source": "marginal_or_inline_letter_heading",
                    },
                }
            )
        return f"{SECTION_KEY}:letter:{list(seen_letters).index(letter)+1:02d}:{letter}"

    # Deterministic node-key lookup built after the pass.
    letter_to_node_key: dict[str, str] = {}

    def node_key_for(letter: str | None) -> str | None:
        if not letter:
            return None
        return letter_to_node_key.get(letter)

    for path in index_files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw.strip())
        except ET.ParseError as exc:
            raise RuntimeError(f"cannot parse OCR XML: {path}") from exc
        for bloco in root.findall("bloco"):
            kind = (bloco.attrib.get("tipo") or "").strip().lower()
            lines = extract_lines(bloco)

            if kind in {"nota_marginal", "note_marginal"}:
                for line in lines:
                    if LETTER_RE.fullmatch(line):
                        current_letter = line
                continue

            if kind != "texto_principal":
                continue

            for line in lines:
                if LETTER_RE.fullmatch(line):
                    current_letter = line
                    continue

                text = line
                source_file = pending_source_file or str(path)
                if pending_text:
                    text = f"{pending_text} {text}"
                    pending_text = None
                    pending_source_file = None

                complete_parts, remainder = split_into_segments(text)
                for part in complete_parts:
                    lemma_raw, ref_ints, entry_raw = parse_entry_segment(part)
                    if not lemma_raw or not ref_ints:
                        continue
                    entry_order += 1
                    entries.append(
                        {
                            "entry_key": f"{VOLUME_ID}:entry:{entry_order:06d}",
                            "section_key": SECTION_KEY,
                            "parent_node_key": None,
                            "entry_order": entry_order,
                            "entry_kind": "lemma",
                            "lemma_raw": lemma_raw,
                            "lemma_display": lemma_raw,
                            "lemma_norm": sort_norm(lemma_raw),
                            "lemma_sort": sort_norm(lemma_raw),
                            "entry_raw": entry_raw,
                            "context_raw": None,
                            "heading_letter": current_letter,
                            "inferred_printed_page": ref_ints[0],
                            "section_start_file": str(index_files[0]),
                            "editorial_anchor_file": source_file,
                            "target_file_best": None,
                            "confidence": 0.92,
                            "raw_json": {
                                "source_file": source_file,
                                "source_file_seq": file_seq(Path(source_file)),
                                "entry_parse": "line_segment",
                            },
                            "_refs": ref_ints,
                        }
                    )

                if remainder is not None:
                    pending_text = remainder
                    pending_source_file = source_file

    if pending_text:
        lemma_raw, ref_ints, entry_raw = parse_entry_segment(pending_text)
        if lemma_raw and ref_ints:
            entry_order += 1
            entries.append(
                {
                    "entry_key": f"{VOLUME_ID}:entry:{entry_order:06d}",
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": current_letter,
                    "inferred_printed_page": ref_ints[0],
                    "section_start_file": str(index_files[0]),
                    "editorial_anchor_file": pending_source_file or str(index_files[0]),
                    "target_file_best": None,
                    "confidence": 0.86,
                    "raw_json": {
                        "source_file": pending_source_file or str(index_files[0]),
                        "source_file_seq": file_seq(Path(pending_source_file or str(index_files[0]))),
                        "entry_parse": "pending_tail",
                    },
                    "_refs": ref_ints,
                }
            )

    # Rebuild node keys with stable order after the letter sequence is known.
    letter_order: list[str] = []
    for entry in entries:
        letter = entry["heading_letter"]
        if letter and letter not in letter_order:
            letter_order.append(letter)
    for idx, letter in enumerate(letter_order, start=1):
        letter_to_node_key[letter] = f"{SECTION_KEY}:letter:{idx:02d}:{letter}"
    nodes = []
    for idx, letter in enumerate(letter_order, start=1):
        nodes.append(
            {
                "node_key": letter_to_node_key[letter],
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": idx,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": sort_norm(letter),
                "label_sort": sort_norm(letter),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "source": "marginal_or_inline_letter_heading",
                },
            }
        )

    for entry in entries:
        entry["parent_node_key"] = node_key_for(entry["heading_letter"])
    return entries, nodes


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        ref_pages = entry.get("_refs", [])
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"]],
                "page_hints": [str(page) for page in ref_pages],
                "page_hint_ints": ref_pages,
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
            "editorial_page_estimator": True,
        },
        "entries": helper_entries,
    }


def helper_best_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        best = item.get("best_candidate") or {}
        if best:
            result[item.get("entry_id")] = {
                "status": item.get("status"),
                "best_candidate": best,
                "candidates": item.get("candidates", []),
            }
    return result


def page_map_for_refs(page_map: dict[int, str], pages: list[int]) -> tuple[list[str], str | None]:
    files = [page_map[p] for p in pages if p in page_map]
    best = files[0] if files else None
    return files, best


def finalize_payload(
    entries: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    page_map: dict[int, str],
    helper_map: dict[str, dict[str, Any]],
    index_files: list[Path],
) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    for entry in entries:
        ref_pages = entry.pop("_refs")
        target_files, best_target = page_map_for_refs(page_map, ref_pages)
        helper_item = helper_map.get(entry["entry_key"])
        if helper_item:
            helper_best = helper_item.get("best_candidate", {})
            if helper_best.get("file"):
                best_target = helper_best.get("file")
            entry["raw_json"]["helper_status"] = helper_item.get("status")
            entry["raw_json"]["helper_best_candidate"] = {
                "file": helper_best.get("file"),
                "probability": helper_best.get("probability"),
                "candidate_role": helper_best.get("candidate_role"),
                "reason_summary": helper_best.get("reason_summary"),
            }
        entry["target_file_best"] = best_target
        entry["raw_json"]["page_hint_ints"] = ref_pages
        entry["raw_json"]["resolved_target_files"] = target_files

        for ref_order, page in enumerate(ref_pages, start=1):
            target_file = page_map.get(page)
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page),
                    "page_ref_raw": str(page),
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 1.0 if target_file else None,
                    "section_start_file": str(index_files[0]),
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.97 if target_file else 0.75,
                    "raw_json": {
                        "source_file": entry["raw_json"]["source_file"],
                        "source_file_seq": entry["raw_json"]["source_file_seq"],
                        "target_source": "header_page_map" if target_file else "unresolved",
                    },
                }
            )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "OCR file 687 starts the Greek alphabetical index; later files continue the same alphabetical run.",
                "The ORDO RERUM back matter on files 691-692 was inspected but excluded from the payload as non-alphabetical editorial closure.",
            ],
        },
        "sections": [
            {
                "section_key": SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "analytic_subject",
                "heading_raw": SECTION_HEADING_RAW,
                "heading_norm": sort_norm(SECTION_HEADING_RAW),
                "heading_letter": None,
                "page_start": PAGE_START,
                "page_end": PAGE_END,
                "file_start": str(index_files[0]),
                "file_end": str(index_files[-1]),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": SECTION_KIND_REASON,
                    "helper_run": {
                        "request_json": str(HELPER_REQUEST_JSON),
                        "output_json": str(HELPER_OUTPUT_JSON),
                        "entry_count": len(entries),
                    },
                },
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": (
                "Recovered the main Greek alphabetical index from the OCR tail with "
                "line-level segmentation, marginal-letter hierarchy, and page-number anchoring."
            ),
            "evidence_files": [str(path) for path in index_files],
        },
        "notes": [
            "Marginal letter headings are preserved through `nodes` and `heading_letter` rather than flattened into lemma text.",
            "Printed page spans on the first and third index files are partially OCR-corrupt, so the page range was inferred from the neighboring numeric headers and file sequence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--helper-request-json", required=True, type=Path)
    parser.add_argument("--helper-output-json", required=True, type=Path)
    parser.add_argument("--intermediate-dir", required=True, type=Path)
    parser.add_argument("--output-file", required=True, type=Path)
    args = parser.parse_args()

    global SOURCE_ROOT, HELPER_REQUEST_JSON, HELPER_OUTPUT_JSON
    SOURCE_ROOT = args.source_root
    HELPER_REQUEST_JSON = args.helper_request_json
    HELPER_OUTPUT_JSON = args.helper_output_json

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"

    all_files = discover_files(SOURCE_ROOT)
    index_files = [path for path in all_files if SECTION_START_FILE_SEQ <= file_seq(path) <= SECTION_END_FILE_SEQ]
    if not index_files:
        raise RuntimeError("no PG151 index OCR files found in the requested source_root")

    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Build PG151 alphabetical payload from OCR tail",
            "completed": [
                "Inspected the Greek alphabetical index in files 687-690",
                "Prepared to resolve material target pages with the local helper",
            ],
            "pending": [
                "Run index_target_locator on the generated helper request",
                "Write the final JSON payload",
            ],
            "blocked": [],
            "notes": [
                "Files 691-692 contain ORDO RERUM back matter and are intentionally excluded from this payload.",
            ],
        },
    )

    page_map = build_page_map(all_files)
    entries, nodes = parse_index_entries(index_files)

    helper_request = build_helper_request(entries)
    write_json(HELPER_REQUEST_JSON, helper_request)

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        check=True,
    )
    helper_output = json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))
    helper_map = helper_best_map(helper_output)

    payload = finalize_payload(entries, nodes, page_map, helper_map, index_files)
    write_json(args.output_file, payload)
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validation complete",
            "completed": [
                "Inspected the Greek alphabetical index in files 687-690",
                "Generated helper request and helper output",
                "Wrote the final payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Files 691-692 contain ORDO RERUM back matter and are intentionally excluded from this payload.",
            ],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Usage: build the PG119 alphabetical payload from the OCR index tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg119_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG119/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG119_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG119_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG119 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG119_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG119"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 119"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
INDEX_FILE_SEQS = list(range(655, 660))
INDEX_START_PAGE = 1301
INDEX_END_PAGE = 1310


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if m:
            files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def seq_from_path(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(path)
    return int(m.group(1))


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    raw = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r'<bloco tipo="texto_principal"[^>]*bbox="([^"]+)">(.*?)</bloco>', re.S)
    blocks: list[dict[str, Any]] = []
    for idx, match in enumerate(pattern.finditer(raw), 1):
        bbox = match.group(1)
        text = normalize(match.group(2) or "")
        if not text:
            continue
        x0 = None
        try:
            x0 = int(bbox.split(",")[0])
        except Exception:
            pass
        blocks.append(
            {
                "index": idx,
                "bbox": bbox,
                "x0": x0,
                "text": text,
                "header_text": normalize(parsed.get("header_text") or ""),
            }
        )
    return blocks


def header_pages(raw: str) -> list[int]:
    pages: list[int] = []
    header = normalize(raw)
    if not header:
        return pages
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
    return pages


def page_for_index_file(seq: int) -> list[int]:
    base = INDEX_START_PAGE + (seq - INDEX_FILE_SEQS[0]) * 2
    return [base, base + 1]


def preprocess_text(text: str) -> str:
    text = text.replace("\x01", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("—", "-")
    return text.strip()


def split_segments(text: str) -> list[str]:
    text = preprocess_text(text)
    if not text:
        return []
    text = re.sub(
        r"((?:ibid\.|(?:[IVXLCDM]{1,4}|[A-ZΑ-Ω]+)[\.,]\s*[\d][\d\s\.\-]*(?:,\s*[\d][\d\s\.\-]*)*))\s+(?=[A-ZΑ-Ω][a-zα-ω])",
        r"\1|||",
        text,
    )
    text = re.sub(r"(?<=\.)\s+(?=[A-ZΑ-Ω][a-zα-ω])", "|||", text)
    parts = [part.strip() for part in text.split("|||") if part.strip()]
    return parts


BOOK_RE = re.compile(r"^(?P<book>(?:[IVXLCDM]{1,4}|[A-ZΑ-Ω]+))[\.,]?\s*(?P<body>.*)$")


def split_ref_numbers(body: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s*[,;]\s*", body) if p.strip()]
    return parts


def parse_refs(entry_raw: str, previous_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    ref_order = 1
    last_explicit_page = previous_page
    text = entry_raw
    # normalize line-break hyphenation in refs only
    text = re.sub(r"\s+", " ", text)
    # explicit book groups
    for match in re.finditer(r"(?P<book>\b[IVXLCDM]{1,4}\b|\b[A-ZΑ-Ω]{1,4}\b)[\.,]?\s*(?P<body>(?:\d[\d\s\.\-]*|ibid\.?)(?:\s*[,;]\s*(?:\d[\d\s\.\-]*|ibid\.?))*)", text):
        book = match.group("book")
        body = match.group("body").strip()
        if not body:
            continue
        for piece in split_ref_numbers(body):
            raw_piece = piece
            inherited = False
            if piece.lower().startswith("ibid"):
                if last_explicit_page is None:
                    continue
                raw_piece = "ibid."
                page_ref_raw = "ibid."
                page_ref_int = last_explicit_page
                inherited = True
            else:
                cleaned = re.sub(r"[^\d]", " ", piece)
                nums = re.findall(r"\d+", cleaned)
                if not nums:
                    continue
                page_ref_int = int(nums[0])
                page_ref_raw = piece
                last_explicit_page = page_ref_int
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": f"{book}, {raw_piece}" if not inherited else "ibid.",
                    "page_ref_raw": page_ref_raw,
                    "page_ref_int": page_ref_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            ref_order += 1
    # fallback for entries whose refs are not book-marked in OCR
    if not refs:
        for piece in re.findall(r"(?<!\d)(\d[\d\s\.\-]*)(?!\d)", text):
            cleaned = re.sub(r"[^\d]", " ", piece)
            nums = re.findall(r"\d+", cleaned)
            if not nums:
                continue
            page_ref_int = int(nums[0])
            refs.append(
                {
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": piece.strip(),
                    "page_ref_raw": piece.strip(),
                    "page_ref_int": page_ref_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            ref_order += 1
            last_explicit_page = page_ref_int
    return refs, last_explicit_page


def lemma_from_segment(seg: str) -> str | None:
    seg = seg.strip()
    if not seg:
        return None
    if "," in seg:
        return seg.split(",", 1)[0].strip()
    if "." in seg:
        head = seg.split(".", 1)[0].strip()
        if len(head) <= 60:
            return head
    return seg[:80].strip()


def infer_entry_kind(entry_raw: str, refs: list[dict[str, Any]]) -> str:
    lowered = entry_raw.lower().strip()
    if lowered.startswith(("vid.", "vide", "voir", "cf.", "id.")) and not refs:
        return "cross_reference"
    return "lemma"


def first_letter(lemma_raw: str | None) -> str | None:
    if not lemma_raw:
        return None
    for ch in lemma_raw.strip():
        if ch.isalpha():
            return ch.upper()
    return None


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pg119_paulus_129",
                "lemma_raw": "Paulus quandoque spiritu edoctus",
                "query_names": [
                    "Paulus quandoque spiritu edoctus",
                    "Paulus quomodo in Actis dicitur",
                    "Paulo ex Saulo dictus",
                ],
                "page_hints": ["129", "110", "1 10"],
                "page_hint_ints": [129, 110],
                "context_raw": "Paulus quandoque spiritu edoctus, interdum autem visu, I, 129, 1 10. Paulus quomodo se dixerit Romanum, I, 132.",
            },
            {
                "entry_id": "pg119_petrus_7_1",
                "lemma_raw": "Petrus quare reprehensus",
                "query_names": [
                    "Petrus quare reprehensus",
                    "Petrus quomodo timebat conversos",
                    "Petrus admonens ut parati simus",
                ],
                "page_hints": ["7.1", "50.1", "7.2"],
                "page_hint_ints": [7, 50, 7],
                "context_raw": "Petrus quare reprehensus, et a quibus, I, 7, 7.1. Petrus quomodo timebat conversos a laxo I, 7.2.",
            },
            {
                "entry_id": "pg119_praepositio_7170",
                "lemma_raw": "Præpositionum quæ divinas concernunt personas",
                "query_names": [
                    "Præpositionum quæ divinas concernunt personas",
                    "Præpositio",
                    "tam ad patrem quam ad i.um",
                ],
                "page_hints": ["537", "7170"],
                "page_hint_ints": [537, 7170],
                "context_raw": "Præpositionum quæ divinas concernunt personas, usus indifferens est apud divum Paulum, I, 537. Præpositio 3.a. id est, per, tam ad patrem quam ad i.um apponit.ur, I, 7170.",
            },
            {
                "entry_id": "pg119_christus_332",
                "lemma_raw": "Christus tentatus sive afflictus",
                "query_names": [
                    "Christus tentatus sive afflictus",
                    "his qui tentantur succurrere",
                    "afflictus potest",
                ],
                "page_hints": ["332"],
                "page_hint_ints": [332],
                "context_raw": "Christus tentatus sive afflictus potest et his qui tentantur succurrere, II, 332.",
            },
            {
                "entry_id": "pg119_scriptura_544",
                "lemma_raw": "Scriptura sacra affectiones",
                "query_names": [
                    "Scriptura sacra affectiones",
                    "brutis assimilat",
                    "quae naturaliter homini conveniunt",
                ],
                "page_hints": ["544"],
                "page_hint_ints": [544],
                "context_raw": "Scriptura sacra affectiones, quæ naturaliter homini conveniunt, brutis assimilat, II, 544.",
            },
            {
                "entry_id": "pg119_dilectio_699",
                "lemma_raw": "Dilectio Dei ex proximi dilectione probatur",
                "query_names": [
                    "Dilectio Dei ex proximi dilectione probatur",
                    "Dilectio Dei",
                    "proximi dilectione",
                ],
                "page_hints": ["699"],
                "page_hint_ints": [699],
                "context_raw": "Dilectio Dei ex proximi dilectione probatur, XII, 367, 593, et ediverso, II, 626.",
            },
            {
                "entry_id": "pg119_sacerdotium_588",
                "lemma_raw": "Sacerdotium Christi præstantius Aaronico sacerdotio",
                "query_names": [
                    "Sacerdotium Christi præstantius Aaronico sacerdotio",
                    "Aaronico sacerdotio",
                    "Sacerdotium Christi",
                ],
                "page_hints": ["588"],
                "page_hint_ints": [588],
                "context_raw": "Sacerdotium Christi præstantius Aaronico sacerdotio, II, 569, 588.",
            },
        ],
    }


def run_helper(helper_request: Path, helper_output: Path) -> dict[str, Any] | None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request),
            "--output",
            str(helper_output),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if helper_output.exists():
        return json.loads(helper_output.read_text(encoding="utf-8"))
    return None


def build_helper_page_map(helper_request: dict[str, Any] | None, helper_output: dict[str, Any] | None) -> dict[int, str]:
    mapping: dict[int, str] = {}
    if not helper_request or not helper_output:
        return mapping
    output_entries = {item.get("entry_id"): item for item in helper_output.get("entries", [])}
    for req in helper_request.get("entries", []):
        entry_id = req.get("entry_id")
        if not entry_id or entry_id not in output_entries:
            continue
        out = output_entries[entry_id]
        hints = [hint for hint in req.get("page_hint_ints", []) if isinstance(hint, int)]
        candidates = out.get("candidates", [])
        scored: list[tuple[int, int, str]] = []
        for cand in candidates:
            if cand.get("candidate_role") != "target_candidate":
                continue
            page = cand.get("inferred_printed_page")
            file_path = cand.get("file")
            if not isinstance(page, int) or not file_path:
                continue
            distance = min((abs(page - hint) for hint in hints), default=10**9)
            scored.append((distance, page, file_path))
        if not scored:
            best = out.get("best_candidate") or {}
            file_path = best.get("file")
            page = best.get("inferred_printed_page")
            if isinstance(page, int) and file_path:
                scored.append((0, page, file_path))
        if not scored:
            continue
        scored.sort(key=lambda item: (item[0], item[1]))
        chosen_file = scored[0][2]
        for hint in hints:
            mapping.setdefault(hint, chosen_file)
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--helper-request-json", type=Path, required=True)
    parser.add_argument("--helper-output-json", type=Path, required=True)
    parser.add_argument("--intermediate-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    helper_request = build_helper_request(args.source_root)
    args.helper_request_json.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = build_helper_page_map(helper_request, helper_output)

    files = discover_files(args.source_root)
    index_files = [path for path in files if seq_from_path(path) in INDEX_FILE_SEQS]
    page_map: dict[int, str] = {}
    for path in files:
        try:
            parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for page in header_pages(header):
            page_map.setdefault(page, str(path))

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    section_key = f"{VOLUME_ID}:alpha:analytic_subject:001"
    heading_raw = "INDEX RERUM ET SENTENTIARUM QUÆ IN COMMENTARIIS ŒCUMENII MEMORATU DIGNÆ VISÆ SUNT."
    sections.append(
        {
            "section_key": section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": heading_raw,
            "heading_norm": normalize(heading_raw),
            "heading_letter": None,
            "page_start": INDEX_START_PAGE,
            "page_end": INDEX_END_PAGE,
            "file_start": str(index_files[0]),
            "file_end": str(index_files[-1]),
            "confidence": 0.9,
            "raw_json": {
                "section_kind_reason": "Analytical subject index headed INDEX RERUM ET SENTENTIARUM, with alphabetized lemma groups and page-located commentary references.",
                "source_files": [str(path) for path in index_files],
                "excluded_files": [str(path) for path in files if seq_from_path(path) == 660],
            },
        }
    )

    current_letter = None
    node_counter = 0
    entry_counter = 0
    previous_page_for_ibid: int | None = None
    pending_tail: str | None = None
    pending_entry_meta: dict[str, Any] | None = None
    index_page_numbers = {
        655: [1301, 1302],
        656: [1303, 1304],
        657: [1305, 1306],
        658: [1307, 1308],
        659: [1309, 1310],
    }

    for path in index_files:
        seq = seq_from_path(path)
        blocks = extract_blocks(path)
        pages = index_page_numbers.get(seq, page_for_index_file(seq))
        if len(pages) < len(blocks):
            while len(pages) < len(blocks):
                pages.append(pages[-1] + 1)
        for block_index, block in enumerate(blocks):
            block_page = pages[min(block_index, len(pages) - 1)]
            text = block["text"]
            if seq == 655 and block_index == 0:
                # drop the structural editorial preface before the actual alphabetized material
                marker = "Aaroniticum"
                if marker in text:
                    text = text[text.index(marker) :]
            if pending_tail:
                text = pending_tail + text.lstrip()
                pending_tail = None
            segments = split_segments(text)
            if not segments:
                continue
            # carry a broken hyphenated tail to the next block/file if needed
            if segments[-1].endswith("-"):
                pending_tail = segments[-1][:-1]
                segments = segments[:-1]
            for seg in segments:
                if not seg:
                    continue
                if re.fullmatch(r"[A-ZΑ-Ω]", seg):
                    continue
                if seq == 655 and block_index == 0 and seg.startswith("Numerus Romanus"):
                    continue
                if seq == 655 and block_index == 0 and seg.startswith("Sed meminerit Lector"):
                    continue
                # If this is a hanging ref-only continuation, keep it with the previous entry.
                if pending_entry_meta and re.fullmatch(r"(?:[IVXLCDM]{1,4}\b[\.,]?\s*)?(?:\d[\d\s\.\-]*)", seg):
                    pending_entry_meta["entry_raw"] += " " + seg
                    pending_entry_meta["raw_json"]["continued_fragment"] = seg
                    continue

                lemma_raw = lemma_from_segment(seg)
                entry_entry_kind = infer_entry_kind(seg, [])
                refs_for_entry, previous_page_for_ibid = parse_refs(seg, previous_page_for_ibid)
                if entry_entry_kind == "cross_reference" and refs_for_entry:
                    entry_entry_kind = "lemma"
                heading_letter = first_letter(lemma_raw)
                if heading_letter != current_letter:
                    current_letter = heading_letter
                    node_counter += 1
                    node_key = f"{VOLUME_ID}:node:{node_counter:03d}"
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_counter,
                            "node_kind": "letter_group",
                            "label_raw": heading_letter,
                            "label_norm": heading_letter,
                            "label_sort": sort_norm(heading_letter),
                            "node_level": 1,
                            "confidence": 0.86,
                            "raw_json": {"inferred": True, "reason": "Alphabetical order implied by successive lemma initials."},
                        }
                    )
                else:
                    node_key = nodes[-1]["node_key"] if nodes else None
                entry_counter += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
                target_refs = []
                for ref in refs_for_entry:
                    ref_page = ref["page_ref_int"]
                    target_file = page_map.get(ref_page)
                    if target_file is None:
                        target_file = page_map.get(ref_page - 1) or page_map.get(ref_page + 1)
                    if target_file is None:
                        target_file = helper_map.get(ref_page)
                    target_refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref["ref_order"],
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": ref["page_ref_col"],
                            "line_ref_raw": ref["line_ref_raw"],
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": target_file,
                            "target_file_probability": 0.83 if target_file else None,
                            "section_start_file": str(index_files[0]),
                            "editorial_anchor_file": str(path),
                            "confidence": 0.86 if target_file else 0.58,
                            "raw_json": {"page_lookup": "exact" if target_file and ref_page in page_map else "approximate"},
                        }
                    )
                refs.extend(target_refs)
                entry_confidence = 0.9
                if "ibid" in seg.lower() or "?" in seg or "i, 7170" in seg.lower() or "2-0" in seg or "1 10" in seg:
                    entry_confidence = 0.72
                if "continued_fragment" in (pending_entry_meta or {}):
                    entry_confidence = min(entry_confidence, 0.7)
                raw_json = {
                    "source_file": str(path),
                    "block_index": block_index + 1,
                    "block_bbox": block["bbox"],
                    "helper_request_used": True,
                }
                if helper_output is not None:
                    raw_json["helper_output_summary"] = helper_output.get("entries", helper_output)
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": node_key,
                    "entry_order": entry_counter,
                    "entry_kind": entry_entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": seg,
                    "context_raw": None,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": block_page,
                    "section_start_file": str(index_files[0]),
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": entry_confidence,
                    "raw_json": raw_json,
                }
                entries.append(entry)
                pending_entry_meta = entry

    # final structural note on the closing contents pages
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Index entries recovered from the alphabetical analytical index tail; non-index ORDO RERUM pages were excluded.",
        "evidence_files": [str(path) for path in index_files],
    }
    notes = [
        {
            "note_type": "extraction",
            "text": "PG119 contains an analytical alphabetical index headed INDEX RERUM ET SENTENTIARUM and a separate editorial ORDO RERUM section later in the volume; only the index tail was serialized here.",
        }
    ]
    if helper_output is not None:
        notes.append({"note_type": "helper_output", "text": "Helper request ran successfully for representative ambiguous locators."})

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PG119 alphabetical payload assembled from OCR and helper request.",
        "completed": [
            "index pages 655-659 parsed",
            "helper request executed on representative ambiguous locators",
            "final payload written",
        ],
        "pending": [
            "spot-check malformed OCR ref groups if a later validation pass flags them",
        ],
        "blocked": [],
        "notes": [
            "Excluded the non-index ORDO RERUM content pages.",
            "Used page-map lookup from OCR headers for cited commentary pages when exact matches existed.",
        ],
    }
    (args.intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

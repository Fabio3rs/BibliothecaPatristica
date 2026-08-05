#!/usr/bin/env python3
"""Usage: build the PG030 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg030_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG030/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG030_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG030_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG030 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG030_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG030"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 30"

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:alphabetical_general:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_START_SEQ = 575
INDEX_END_SEQ = 608
ORDO_SEQ = 609

NOISE_RE = re.compile(
    r"^(?:INDEX RERUM ET VERBORUM\.?|INDEX RERUM AC VERBORUM\.?|QU[ÆAE]\s+ANTE\s+APPENDICEM\s+INVENIUNTUR\.?|QU[ÆAE]\s+IN\s+APPENDICE\s+INVENIUNTUR\.?|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?)$",
    re.IGNORECASE,
)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LETTER_MARKER_RE = re.compile(r"^<<LETTER:([A-ZÆŒ])>>$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ])")
BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.IGNORECASE | re.DOTALL)
TRAILING_LOCATOR_RE = re.compile(
    r"(?P<group>(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)(?:\s*,\s*\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)*(?:\s+et\s+seq\.?)?)\.?\s*$",
    re.IGNORECASE,
)
NUMBER_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
NON_BIBLICAL_REMISSION_RE = re.compile(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    def seq(path: Path) -> int:
        return int(re.search(r"-(\d+)\.txt$", path.name).group(1))

    return sorted(source_root.glob("*.txt"), key=seq)


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_body_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        if kind not in {"cabecalho", "texto_principal", "nota_marginal"}:
            continue
        content = match.group("content") or ""
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in NUMBER_HEADER_RE.finditer(header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def target_for_page(page: int, page_map: dict[int, str]) -> str | None:
    return page_map.get(page)


def extract_locator_group(fragment: str, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[int]]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    match = TRAILING_LOCATOR_RE.search(fragment)
    if not match:
        return refs, page_hints
    group = match.group("group") or ""
    tokens = [token.strip() for token in group.split(",") if token.strip()]
    for token in tokens:
        raw = token
        is_seq = False
        if re.search(r"\bet\s+seq\.?$", token, re.IGNORECASE):
            token = re.sub(r"\bet\s+seq\.?$", "", token, flags=re.IGNORECASE).strip()
            is_seq = True
        range_match = re.fullmatch(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})", token)
        if range_match:
            start_raw, end_raw = range_match.groups()
            page = int(start_raw)
            page_hints.append(page)
            refs.append(
                {
                    "ref_kind": "editorial_range",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": start_raw,
                    "range_end_raw": end_raw,
                    "target_file": target_for_page(page, page_map),
                    "target_file_probability": 0.99 if target_for_page(page, page_map) else None,
                    "confidence": 0.95 if target_for_page(page, page_map) else 0.7,
                    "raw_json": {"locator_kind": "range"},
                }
            )
            continue
        page_match = re.fullmatch(r"(\d{1,4})", token)
        if page_match:
            page = int(page_match.group(1))
            page_hints.append(page)
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_for_page(page, page_map),
                    "target_file_probability": 0.99 if target_for_page(page, page_map) else None,
                    "confidence": 0.95 if target_for_page(page, page_map) else 0.7,
                    "raw_json": {"locator_kind": "page", "et_seq": is_seq},
                }
            )
    return refs, page_hints


def lemma_from_fragment(fragment: str) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    match = TRAILING_LOCATOR_RE.search(text)
    if match:
        text = text[: match.start()].rstrip(" ,;:.")
    if re.search(r"\bvide\b", text, re.IGNORECASE):
        text = re.split(r"\bvide\b", text, maxsplit=1, flags=re.IGNORECASE)[0].rstrip(" ,;:.")
    return text or None


def entry_kind(fragment: str, has_locators: bool) -> str:
    text = normalize(fragment) or ""
    if not has_locators and NON_BIBLICAL_REMISSION_RE.search(text):
        return "cross_reference"
    if not has_locators and re.fullmatch(r"[A-ZÆŒ]\.?", text):
        return "heading_group"
    if not has_locators and len(text) <= 24 and re.search(r"^[A-ZÆŒ]", text) and " " not in text:
        return "heading_group"
    return "lemma"


def split_fragments(buffer_text: str) -> list[str]:
    text = buffer_text.replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [frag.strip() for frag in SENTENCE_SPLIT_RE.split(text) if frag.strip()]


def build_sections(index_files: list[Path], ordo_files: list[Path]) -> list[dict[str, Any]]:
    index_pages = []
    for path in index_files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if header:
            index_pages.extend(int(m.group(1)) for m in NUMBER_HEADER_RE.finditer(header))
    ordo_pages = []
    for path in ordo_files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if header:
            ordo_pages.extend(int(m.group(1)) for m in NUMBER_HEADER_RE.finditer(header))

    return [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX RERUM ET VERBORUM",
            "heading_norm": "index rerum et verborum",
            "heading_letter": None,
            "page_start": min(index_pages) if index_pages else None,
            "page_end": max(index_pages) if index_pages else None,
            "file_start": str(index_files[0]) if index_files else None,
            "file_end": str(index_files[-1]) if index_files else None,
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Main alphabetical index of matters and words; letter dividers and many explicit page locators in the entries.",
                "observed_headings": [
                    "INDEX RERUM ET VERBORUM",
                    "QUAE ANTE APPENDICEM INVENIUNTUR",
                    "QUE IN APPENDICE INVENIUNTUR",
                ],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM",
            "heading_norm": "ordo rerum",
            "heading_letter": None,
            "page_start": min(ordo_pages) if ordo_pages else None,
            "page_end": max(ordo_pages) if ordo_pages else None,
            "file_start": str(ordo_files[0]) if ordo_files else None,
            "file_end": str(ordo_files[-1]) if ordo_files else None,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Editorial contents table / ordo rerum at the end of the tome, separate from the alphabetical index proper.",
            },
        },
    ]


def build_index_payload(index_files: list[Path], page_map: dict[int, str], helper_limit: int = 24) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    buffer_lines: list[str] = []
    buffer_file: str | None = None
    entry_order = 0
    letter_nodes: dict[str, str] = {}

    def flush_buffer() -> None:
        nonlocal entry_order, buffer_lines, buffer_file
        if not buffer_lines:
            return
        fragments = split_fragments("\n".join(buffer_lines))
        buffer_lines = []
        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned:
                continue
            if NOISE_RE.fullmatch(cleaned):
                continue
            if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T"}:
                continue
            raw_refs, page_hints = extract_locator_group(cleaned, page_map)
            kind = entry_kind(cleaned, bool(page_hints))
            lemma_raw = None if kind == "cross_reference" else lemma_from_fragment(cleaned)
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
            target_file_best = target_for_page(page_hints[0], page_map) if page_hints else buffer_file
            entry = {
                "entry_key": entry_key,
                "section_key": INDEX_SECTION_KEY,
                "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": None,
                "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": str(index_files[0]) if index_files else None,
                "editorial_anchor_file": buffer_file,
                "target_file_best": target_file_best,
                "confidence": 0.88 if page_hints else 0.68,
                "raw_json": {
                    "source_file": buffer_file,
                    "section_kind": "alphabetical_general",
                    "split_strategy": "sentence_boundary",
                    "page_hints": page_hints,
                },
            }
            entries.append(entry)
            if page_hints:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or cleaned,
                        "query_names": [name for name in [lemma_raw, cleaned.split(",", 1)[0], cleaned.split(".", 1)[0]] if name],
                        "page_hints": [str(page) for page in page_hints[:3]],
                        "page_hint_ints": page_hints[:3],
                        "context_raw": cleaned,
                    }
                )
            for ref_order, ref in enumerate(raw_refs, start=1):
                ref = dict(ref)
                ref["entry_key"] = entry_key
                ref["ref_order"] = ref_order
                ref["section_start_file"] = str(index_files[0]) if index_files else None
                ref["editorial_anchor_file"] = buffer_file
                refs.append(ref)

    for path in index_files:
        lines = extract_body_lines(path)
        for line in lines:
            if line.startswith("ORDO RERUM"):
                continue
            if line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                continue
            if line.startswith("INDEX RERUM ET VERBORUM") or line.startswith("INDEX RERUM AC VERBORUM"):
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                flush_buffer()
                current_letter = line
                if line not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:{line}"
                    letter_nodes[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": INDEX_SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path), "kind": "letter_divider"},
                        }
                    )
                buffer_file = str(path)
                continue
            if current_letter is None:
                continue
            if not buffer_lines:
                buffer_file = str(path)
            buffer_lines.append(line)
        buffer_lines.append(" ")
    flush_buffer()

    if helper_limit and len(helper_entries) > helper_limit:
        helper_entries = helper_entries[:helper_limit]

    return entries, refs, nodes, helper_entries


def build_ordo_payload(ordo_files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    for path in ordo_files:
        lines = extract_body_lines(path)
        text = " ".join(
            line
            for line in lines
            if not NOISE_RE.fullmatch(line) and not SINGLE_LETTER_RE.fullmatch(line) and not line.startswith("ORDO RERUM")
        )
        text = re.sub(r"(?<=\w)-\s+(?=\w)", "", text)
        for fragment in re.findall(r"(?:Oratio|Homilia|Monitum|Liber|Sermo|Argumenta|Expositio)[^.]*(?:\.\s*[^.]+?)*?(?=(?:\bOratio\b|\bHomilia\b|\bMonitum\b|\bLiber\b|\bSermo\b|\bArgumenta\b|\bExpositio\b|$))", text):
            cleaned = normalize(fragment) or ""
            if not cleaned:
                continue
            raw_refs, page_hints = extract_locator_group(cleaned, page_map)
            if not page_hints:
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:ordo:{entry_order:04d}"
            lemma_raw = lemma_from_fragment(cleaned)
            entry = {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_hints[0],
                "section_start_file": str(ordo_files[0]) if ordo_files else None,
                "editorial_anchor_file": str(path),
                "target_file_best": target_for_page(page_hints[0], page_map),
                "confidence": 0.9,
                "raw_json": {
                    "source_file": str(path),
                    "section_kind": "ordo_rerum",
                    "split_strategy": "toc_fragment",
                    "page_hints": page_hints,
                },
            }
            entries.append(entry)
            for ref_order, ref in enumerate(raw_refs, start=1):
                ref = dict(ref)
                ref["entry_key"] = entry_key
                ref["ref_order"] = ref_order
                ref["section_start_file"] = str(ordo_files[0]) if ordo_files else None
                ref["editorial_anchor_file"] = str(path)
                refs.append(ref)
    return entries, refs


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {str(item.get("entry_id")): item for item in helper_output.get("entries", []) if isinstance(item, dict)}


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG030 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Extract PG030 alphabetical index and keep ORDO RERUM separate",
            "completed": [],
            "pending": [
                "parse OCR index entries",
                "run helper target locator on sample lines",
                "assemble final payload and validate schema",
            ],
            "blocked": [],
            "notes": [
                "File 575 begins with residual pre-index text; the alphabetical run starts at the A divider in the same OCR file.",
                "File 608 continues the S/T tail of the alphabetical index; file 609 starts ORDO RERUM.",
            ],
        },
    )

    files = discover_files(args.source_root)
    page_map = build_page_map(files)
    index_files = [path for path in files if INDEX_START_SEQ <= file_seq(path) <= INDEX_END_SEQ]
    ordo_files = [path for path in files if file_seq(path) == ORDO_SEQ]

    sections = build_sections(index_files, ordo_files)
    entries, refs, nodes, helper_entries = build_index_payload(index_files, page_map, helper_limit=24)
    ordo_entries, ordo_refs = build_ordo_payload(ordo_files, page_map)

    entries.extend(ordo_entries)
    refs.extend(ordo_refs)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)

    helper_output: dict[str, Any] = {"entries": []}
    if args.helper_output_json.exists():
        helper_output = json.loads(args.helper_output_json.read_text(encoding="utf-8"))
    else:
        helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    helper_by_id = helper_map(helper_output)
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        entry["raw_json"]["helper_status"] = helper.get("status")
        entry["raw_json"]["helper_candidate_role"] = helper.get("candidate_role")
        entry["raw_json"]["helper_reason_summary"] = helper.get("reason_summary")
        entry["raw_json"]["helper_top_candidates"] = [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "evidence_kinds": cand.get("evidence_kinds"),
            }
            for cand in (helper.get("top_candidates") or [])[:5]
        ]
        best = helper.get("best_candidate") or {}
        if best:
            entry["raw_json"]["helper_best_candidate"] = {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "inferred_printed_page": best.get("inferred_printed_page"),
                "evidence_kinds": best.get("evidence_kinds"),
            }

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Alphabetical index recovered from the OCR tail with ORDO RERUM serialized as a separate section.",
                "Material page references were kept distinct from OCR file suffixes and were resolved through the local page map when possible.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the alphabetical index and the closing ORDO RERUM table from the OCR tail; letter dividers and page refs were preserved conservatively, and helper evidence was sampled for representative entries.",
            "evidence_files": [str(index_files[0]), str(index_files[-1]), str(ordo_files[0])] if index_files and ordo_files else [str(path) for path in index_files[:3]],
        },
        "notes": [
            "The OCR tail is irregular: file 575 begins with residual pre-index matter before the alphabetical run, and file 608 continues the tail into the T section.",
            "Biblical citations remain embedded in entry_raw unless they could be safely separated from the material reference stream.",
        ],
    }

    write_json(args.output_file, payload)
    todo_path.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": now_iso(),
                "current_focus": "Final payload written",
                "completed": ["parsed OCR tail", "ran helper target locator", "assembled final payload"],
                "pending": [],
                "blocked": [],
                "notes": ["Keep this volume as a reusable template for similar PG appendix indexes."],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

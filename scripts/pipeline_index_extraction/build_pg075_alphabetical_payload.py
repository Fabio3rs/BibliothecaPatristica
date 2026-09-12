#!/usr/bin/env python3
"""Usage: build the PG075 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg075_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG075/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG075_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG075_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG075 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG075_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG075"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 75"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
DEFAULT_TODO = ROOT / "data/intermediate_payloads/PG075/todo.json"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analyticus:001",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS.",
        "heading_norm": "index analyticus",
        "section_kind_reason": (
            "Alphabetical analytical subject index with letter-group headings "
            "and multiple page references per entry."
        ),
        "page_start": 1483,
        "page_end": 1490,
        "file_start_seq": 748,
        "file_end_seq": 751,
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "section_kind_reason": (
            "Final contents table (ordo rerum) listing the volume's internal "
            "works and chapter divisions; editorial closure material rather "
            "than an ordinary alphabetical lemma list."
        ),
        "page_start": 1493,
        "page_end": 1496,
        "file_start_seq": 752,
        "file_end_seq": 754,
    },
]

NOISE_LINES = {
    "Digitized by Google",
}

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
TYPE_RE = re.compile(r'tipo="([^"]+)"')

PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_TOKEN_RE = re.compile(
    r"(?P<page>\d{1,4})(?:\s*[-–—]\s*(?P<end>\d{1,4}))?(?P<m>\s*m\.?)?(?:\s*(?P<note>et seq\.|seq\.|seqq\.|cum adnot\.|cum nota\.|in adnot\.|cum adnot\.)\b)?",
    re.IGNORECASE,
)
TRAILING_REF_BLOCK_RE = re.compile(
    r"(?P<block>"
    r"(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*m\.?)?(?:\s*(?:et seq\.|seq\.|seqq\.|cum adnot\.|cum nota\.|in adnot\.))?)"
    r"(?:\s*,\s*(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*m\.?)?(?:\s*(?:et seq\.|seq\.|seqq\.|cum adnot\.|cum nota\.|in adnot\.))?))*"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)
SPLIT_BOUNDARY_RE = re.compile(r"\.\s+(?=[A-ZÆŒÀ-ÿΑ-ΩἈ-῾])")
PAGE_TO_TITLE_BOUNDARY_RE = re.compile(r"(?<=\d)\s+(?=[A-ZÆŒÀ-ÿΑ-ΩIVXLCDM])")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
STRUCTURAL_RE = re.compile(
    r"^(?:"
    r"INDEX\b|ORDO\b|CAP\.\b|ASSERT\.\b|DIALOGUS\b|LIBER\b|DE\b|PRO[ŒE]MIUM\b|"
    r"PROLOGUS\b|ADMONITIO\b|SCHOLIA\b|Variae lectiones\b|Variæ lectiones\b|"
    r"QUOD\b|QUAE\b|QUÆ\b|II\.\s*—|III\.\s*—|IV\.\s*—|V\.\s*—|VI\.\s*—"
    r")",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = strip_accents(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def discover_files(source_root: Path) -> list[Path]:
    def seq(path: Path) -> int:
        m = re.search(r"-(\d+)\.txt$", path.name)
        return int(m.group(1)) if m else 10**9

    return sorted(source_root.glob("*.txt"), key=seq)


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def extract_page_header_numbers(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(read_text(path))
    header = normalize(parsed.get("header_text") or "")
    out: list[int] = []
    seen: set[int] = set()
    for match in PAGE_HEADER_RE.finditer(header):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in extract_page_header_numbers(path):
            mapping.setdefault(page, path.as_posix())
    return mapping


def extract_body_lines(path: Path) -> list[str]:
    raw = read_text(path)
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_match = TYPE_RE.search(attrs)
        tipo = tipo_match.group(1).strip().lower() if tipo_match else ""
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            if re.fullmatch(r"\d{1,4}", line):
                continue
            lines.append(line)
    return lines


def split_entry_segments(line: str) -> list[str]:
    text = normalize(line)
    if not text:
        return []
    pieces: list[str] = []
    start = 0
    split_regexes = [SPLIT_BOUNDARY_RE, PAGE_TO_TITLE_BOUNDARY_RE]
    changed = True
    while changed:
        changed = False
        for regex in split_regexes:
            tmp: list[str] = []
            for chunk in pieces or [text]:
                last = 0
                made_split = False
                for match in regex.finditer(chunk):
                    prefix = chunk[: match.start() + 1]
                    if regex is SPLIT_BOUNDARY_RE and not (
                        re.search(r"\d", prefix)
                        or re.search(
                            r"\b(?:ibid|seq|seqq|et seq|cum adnot|cum nota|in adnot)\.?\s*$",
                            prefix,
                            re.IGNORECASE,
                        )
                    ):
                        continue
                    piece = chunk[last : match.start() + 1].strip()
                    if piece:
                        tmp.append(piece)
                    last = match.end()
                    made_split = True
                tail = chunk[last:].strip()
                if tail:
                    tmp.append(tail)
                if made_split:
                    changed = True
            pieces = tmp
    if not pieces:
        pieces = [text]
    pieces = [piece for piece in pieces if piece]
    return pieces or [text]


def extract_ref_block(segment: str) -> tuple[str, list[dict[str, Any]]]:
    text = normalize(segment)
    if not text:
        return "", []
    match = TRAILING_REF_BLOCK_RE.search(text)
    if not match:
        return text.rstrip(" .;:"), []

    block = normalize(match.group("block"))
    lemma = normalize(text[: match.start()].rstrip(" ,;:."))
    refs: list[dict[str, Any]] = []
    for ref_order, token in enumerate(re.split(r"\s*,\s*", block), start=1):
        token = normalize(token).rstrip(".")
        if not token:
            continue
        page_match = PAGE_TOKEN_RE.search(token)
        if not page_match:
            continue
        page_ref_int = int(page_match.group("page"))
        range_end = page_match.group("end")
        refs.append(
            {
                "ref_order": ref_order,
                "ref_raw": token,
                "page_ref_raw": page_match.group("page"),
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page_match.group("page"),
                "range_end_raw": range_end,
            }
        )
    return lemma or text.rstrip(" .;:"), refs


def lemma_from_segment(segment: str) -> str:
    lemma, _ = extract_ref_block(segment)
    return lemma


def make_query_names(lemma_raw: str, entry_raw: str) -> list[str]:
    variants = []
    for item in [lemma_raw, entry_raw]:
        cleaned = normalize(item)
        if cleaned and cleaned not in variants:
            variants.append(cleaned)
    if lemma_raw:
        first_clause = re.split(r"\s*[;,]\s*|\s{2,}", lemma_raw, maxsplit=1)[0].strip()
        if first_clause and first_clause not in variants:
            variants.append(first_clause)
    return variants[:4]


def section_for_file(path: Path) -> dict[str, Any] | None:
    seq = file_seq(path)
    for spec in SECTION_SPECS:
        if spec["file_start_seq"] <= seq <= spec["file_end_seq"]:
            return spec
    return None


def section_files(files: list[Path], spec: dict[str, Any]) -> list[Path]:
    return [path for path in files if spec["file_start_seq"] <= file_seq(path) <= spec["file_end_seq"]]


def build_nodes(section_key: str, lines_by_file: dict[Path, list[str]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    order = 0
    for path, lines in lines_by_file.items():
        for line in lines:
            if not LETTER_RE.fullmatch(line):
                continue
            if line in seen:
                continue
            seen.add(line)
            order += 1
            nodes.append(
                {
                    "node_key": f"{VOLUME_ID}:node:{order:03d}",
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": order,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": line.lower(),
                    "label_sort": line.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "node_kind_reason": "Standalone alphabetic heading in the analytical index.",
                    },
                }
            )
    return nodes


def page_map_for_refs(page_map: dict[int, str], page_ref_int: int) -> str | None:
    return page_map.get(page_ref_int)


def is_structural_entry(kind: str, entry_raw: str) -> bool:
    if kind == "ordo_rerum":
        return True
    if STRUCTURAL_RE.match(entry_raw):
        return True
    return False


def build_entries_for_section(
    *,
    spec: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    lines_by_file: dict[Path, list[str]] = {}
    entry_order = 0
    current_letter: str | None = None
    started = False
    section_start_file = files[0].as_posix() if files else None
    section_kind = spec["section_kind"]

    for path in files:
        lines = extract_body_lines(path)
        lines_by_file[path] = lines
        for line in lines:
            if not started:
                if section_kind == "analytic_subject":
                    if not LETTER_RE.fullmatch(line) or line != "A":
                        continue
                    started = True
                    current_letter = line
                    continue
                if spec["heading_norm"].lower() not in line.lower():
                    continue
                started = True
                continue
            if LETTER_RE.fullmatch(line):
                current_letter = line
                continue
            for segment in split_entry_segments(line):
                cleaned = normalize(segment)
                if not cleaned or cleaned in NOISE_LINES:
                    continue
                if cleaned.lower().startswith("pag. "):
                    continue
                if section_kind == "ordo_rerum" and cleaned and cleaned[0].islower():
                    continue
                if section_kind == "analytic_subject" and cleaned == spec["heading_norm"]:
                    continue
                entry_raw, entry_refs = extract_ref_block(cleaned)
                if not entry_raw:
                    continue
                if entry_raw.lower().startswith("pag") and len(entry_raw.split()) <= 2 and entry_refs:
                    continue
                if section_kind == "analytic_subject" and entry_raw.upper() in {"INDEX ANALYTICUS", "INDEX ANALYTICUS."}:
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                inferred_page = entry_refs[0]["page_ref_int"] if entry_refs else None
                target_file_best = page_map_for_refs(page_map, inferred_page) if inferred_page is not None else None
                if target_file_best is None:
                    target_file_best = path.as_posix()

                entry_kind = "heading_group" if section_kind == "ordo_rerum" else "lemma"
                if is_structural_entry(section_kind, entry_raw):
                    entry_kind = "heading_group"

                entry = {
                    "entry_key": entry_key,
                    "section_key": spec["section_key"],
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": entry_raw,
                    "lemma_display": entry_raw,
                    "lemma_norm": sort_norm(entry_raw),
                    "lemma_sort": sort_norm(entry_raw),
                    "entry_raw": cleaned,
                    "context_raw": None,
                    "heading_letter": current_letter if section_kind == "analytic_subject" else None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": path.as_posix(),
                    "target_file_best": target_file_best,
                    "confidence": 0.95 if entry_refs else 0.82,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "section_kind": section_kind,
                        "page_hints": [ref["page_ref_int"] for ref in entry_refs],
                        "split_strategy": "sentence_boundary_after_trailing_page_refs",
                        "source_line": cleaned,
                    },
                }
                if current_letter and section_kind == "analytic_subject":
                    entry["raw_json"]["heading_letter"] = current_letter
                entries.append(entry)
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": entry_raw,
                        "query_names": make_query_names(entry_raw, cleaned),
                        "page_hints": [str(ref["page_ref_int"]) for ref in entry_refs[:3]],
                        "page_hint_ints": [ref["page_ref_int"] for ref in entry_refs[:3]],
                        "context_raw": cleaned,
                    }
                )
                for ref in entry_refs:
                    target_file = page_map_for_refs(page_map, ref["page_ref_int"])
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref["ref_order"],
                            "ref_kind": "editorial_page",
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": target_file,
                            "target_file_probability": 0.99 if target_file else None,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": 0.96 if target_file else 0.66,
                            "raw_json": {
                                "locator_method": "header_page_map" if target_file else "unresolved",
                                "source_file": path.as_posix(),
                            },
                        }
                    )

    return entries, refs, helper_entries, lines_by_file


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
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
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def update_with_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = {str(item.get("entry_id")): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in entries:
        helper = by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        entry.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": best if best else None,
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in (helper.get("candidates") or [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        helper = by_id.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref.setdefault("raw_json", {})["helper_best_file"] = best.get("file")
            ref["raw_json"]["helper_best_probability"] = best.get("probability")


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for order, spec in enumerate(SECTION_SPECS, start=1):
        section_files = [path for path in files if spec["file_start_seq"] <= file_seq(path) <= spec["file_end_seq"]]
        if not section_files:
            continue
        page_starts = [page for path in section_files for page in extract_page_header_numbers(path)]
        sections.append(
            {
                "section_key": spec["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": min(page_starts) if page_starts else spec["page_start"],
                "page_end": max(page_starts) if page_starts else spec["page_end"],
                "file_start": section_files[0].as_posix(),
                "file_end": section_files[-1].as_posix(),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": spec["section_kind_reason"],
                    "evidence_files": [section_files[0].as_posix(), section_files[-1].as_posix()],
                },
            }
        )
    return sections


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root = args.source_root.resolve()
    files = discover_files(source_root)
    page_map = build_page_map(files)
    sections = build_sections(files)

    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    all_nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    lines_by_file_all: dict[str, list[str]] = {}

    for spec in SECTION_SPECS:
        section_files = section_files_in_range = [path for path in files if spec["file_start_seq"] <= file_seq(path) <= spec["file_end_seq"]]
        if not section_files:
            continue
        entries, refs, helper_seed, lines_by_file = build_entries_for_section(spec=spec, files=section_files, page_map=page_map)
        all_entries.extend(entries)
        all_refs.extend(refs)
        helper_entries.extend(helper_seed)
        lines_by_file_all.update({path.as_posix(): lines for path, lines in lines_by_file.items()})
        if spec["section_kind"] == "analytic_subject":
            all_nodes.extend(build_nodes(spec["section_key"], lines_by_file))

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    update_with_helper(all_entries, all_refs, helper_output)

    # Restore stable target files from the page-header map if the helper was less specific.
    for entry in all_entries:
        if entry.get("inferred_printed_page") is not None:
            target = page_map.get(entry["inferred_printed_page"])
            if target:
                entry["target_file_best"] = target
    for ref in all_refs:
        if ref.get("target_file") is None and ref.get("page_ref_int") is not None:
            ref["target_file"] = page_map.get(ref["page_ref_int"])
            ref["target_file_probability"] = 0.98 if ref["target_file"] else None

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": all_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": (
                "Recovered the closing analytical index and the final ORDO RERUM table "
                "from the OCR tail, with page-numbered references resolved against the "
                "volume header map."
            ),
            "evidence_files": [sections[0]["file_start"], sections[-1]["file_end"]] if sections else [],
        },
        "notes": [
            "PG075 ends with an analytical index followed by an ORDO RERUM contents table.",
            "OCR file suffixes were treated separately from printed page numbers; the page-header map controls material target resolution.",
            f"Helper status: {helper_output.get('status', 'unknown')}.",
        ],
    }

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", all_nodes)
    write_json(args.intermediate_dir / "entries.json", all_entries)
    write_json(args.intermediate_dir / "refs.json", all_refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": str(args.helper_request_json),
            "helper_output_json": str(args.helper_output_json),
            "output_file": str(args.output_file),
        },
    )
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Validate the PG075 analytical index payload and ensure helper/page-map alignment.",
            "completed": [
                "identified the final analytical index and ORDO RERUM sections",
                "built a page-header map for the OCR tail",
                "serialized entries, refs, nodes, and helper request data",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Helper evidence is preserved in raw_json for entries where it was useful.",
                "If a page-header lookup is missing, inspect neighboring OCR files before changing the ref.",
            ],
        },
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

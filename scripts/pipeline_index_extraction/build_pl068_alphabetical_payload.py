#!/usr/bin/env python3
"""Usage: build the PL068 alphabetical payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl068_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL068/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL068_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL068_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL068 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL068_alphabetical_indices.json
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

VOLUME_ID = "PL068"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 68"

MAIN_INDEX_HEADING_RE = re.compile(r"INDEX RERUM ET VERBORUM(?:\.|\s|$)", re.IGNORECASE)
MAIN_INDEX_SUBHEADING_RE = re.compile(r"QUÆ IN NOTIS EXPONUNTUR\.?", re.IGNORECASE)
VETERUM_HEADING_RE = re.compile(r"INDEX AUCTORUM VETERUM QUI IN NOTIS LAUDANTUR\.?", re.IGNORECASE)
RECENTIORUM_HEADING_RE = re.compile(r"INDEX AUCTORUM RECENTIORUM QUI IN NOTIS LAUDANTUR\.?", re.IGNORECASE)
ORDO_HEADING_RE = re.compile(r"(?:ORDO RERUM(?: QUÆ IN HOC TOMO CONTINENTUR\.)?|QUÆ IN HOC T(?:O|O)MO CONTINENTUR\.?)", re.IGNORECASE)
SECTION_HEADING_ANY_RE = re.compile(
    r"(INDEX RERUM ET VERBORUM|INDEX AUCTORUM VETERUM QUI IN NOTIS LAUDANTUR|INDEX AUCTORUM RECENTIORUM QUI IN NOTIS LAUDANTUR|ORDO RERUM|QUÆ IN HOC T(?:O|O)MO CONTINENTUR)",
    re.IGNORECASE,
)
BLOCK_RE = re.compile(r"<bloco[^>]*>(?P<content>.*?)</bloco>", re.IGNORECASE | re.DOTALL)
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(bis|ter|quater))?(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)", re.IGNORECASE)
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
VIDE_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?)\b", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|\d{1,4}\s*|\s*)$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒĒĪŌŪÁÀÂÄÃÉÈÊÍÌÎÓÒÔÖÕÚÙÛÇΣΤΥΦΧΨΩΔΘΛΜΝΞΠΡ][\.]?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm_text(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        content = match.group("content") or ""
        for line in content.splitlines():
            value = norm_text(line)
            if not value or NOISE_RE.fullmatch(value):
                continue
            lines.append(value)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for line in extract_lines(path)[:8]:
            for match in PAGE_TOKEN_RE.finditer(line):
                page = int(match.group(1))
                if 1 <= page <= 9999:
                    page_map.setdefault(page, str(path))
    return page_map


def unique_preserve_order(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        value = norm_text(value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def should_continue(buffer: str, line: str) -> bool:
    if not buffer:
        return False
    if buffer.endswith(("-", "—", ":", ";", ",")):
        return True
    if buffer.lower().endswith(("et", "vel", "sed", "quia", "quod", "ibid", "id", "vid")):
        return True
    if "(" in buffer and ")" not in buffer:
        return True
    if line[:1].islower() or line[:1].isdigit() or line.startswith((",", ".", ";", "—", "-", "·")):
        return True
    return False


def is_section_heading(line: str) -> bool:
    return bool(SECTION_HEADING_ANY_RE.search(line))


def is_letter_line(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line.strip()))


def parse_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1

    if IBID_RE.fullmatch(text.strip().rstrip(".")) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": text.strip(),
                "page_ref_raw": text.strip(),
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)
        return refs, page_hints, last_page

    for match in PAGE_TOKEN_RE.finditer(text):
        raw = match.group(0).strip()
        page = int(match.group(1))
        suffix = match.group(2)
        end = match.group(3)
        ref_kind = "editorial_page"
        range_start_raw = None
        range_end_raw = None
        if end is not None:
            ref_kind = "editorial_range"
            range_start_raw = str(page)
            range_end_raw = str(int(end))
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
            }
        )
        page_hints.append(page)
        ref_order += 1
        last_page = page
    if not refs and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "Ibid.",
                "page_ref_raw": "Ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)
    return refs, page_hints, last_page


def choose_entry_kind(section_kind: str, text: str, refs_local: list[dict[str, Any]]) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not refs_local and VIDE_RE.search(text):
        return "cross_reference"
    if not refs_local and IBID_RE.search(text):
        return "cross_reference"
    return "lemma"


def infer_section_key(section_kind: str) -> str:
    mapping = {
        "alphabetical_general": f"{VOLUME_ID}:alpha:alphabetical_general:001",
        "onomastic_veterum": f"{VOLUME_ID}:alpha:onomastic_veterum:002",
        "onomastic_recentiorum": f"{VOLUME_ID}:alpha:onomastic_recentiorum:003",
        "ordo_rerum": f"{VOLUME_ID}:alpha:ordo_rerum:004",
    }
    return mapping[section_kind]


def infer_section_kind_from_heading(line: str) -> str | None:
    if VETERUM_HEADING_RE.search(line):
        return "onomastic_veterum"
    if RECENTIORUM_HEADING_RE.search(line):
        return "onomastic_recentiorum"
    if ORDO_HEADING_RE.search(line):
        return "ordo_rerum"
    if MAIN_INDEX_HEADING_RE.search(line) or MAIN_INDEX_SUBHEADING_RE.search(line):
        return "alphabetical_general"
    return None


def section_metadata(files: list[Path]) -> list[dict[str, Any]]:
    file_by_num = {file_num(path): path for path in files}
    return [
        {
            "section_key": infer_section_key("alphabetical_general"),
            "volume_id": VOLUME_ID,
            "work_key": "aratoris_epistolas_et_libros",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX RERUM ET VERBORUM QUÆ IN NOTIS EXPONUNTUR.",
            "heading_norm": "index rerum et verborum quae in notis exponuntur",
            "heading_letter": None,
            "page_start": 1087,
            "page_end": 1114,
            "file_start": str(file_by_num[559]),
            "file_end": str(file_by_num[567]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Alphabetical vocabulary index with Latin and Greek lemmata and page citations in the notes.",
                "evidence_files": [str(file_by_num[559]), str(file_by_num[567])],
            },
        },
        {
            "section_key": infer_section_key("onomastic_veterum"),
            "volume_id": VOLUME_ID,
            "work_key": "aratoris_epistolas_et_libros",
            "section_order": 2,
            "section_kind": "onomastic_veterum",
            "heading_raw": "INDEX AUCTORUM VETERUM QUI IN NOTIS LAUDANTUR.",
            "heading_norm": "index auctorum veterum qui in notis laudantur",
            "heading_letter": None,
            "page_start": 1115,
            "page_end": 1116,
            "file_start": str(file_by_num[567]),
            "file_end": str(file_by_num[568]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical onomastic index of ancient authors cited in the notes.",
                "evidence_files": [str(file_by_num[567]), str(file_by_num[568])],
            },
        },
        {
            "section_key": infer_section_key("onomastic_recentiorum"),
            "volume_id": VOLUME_ID,
            "work_key": "aratoris_epistolas_et_libros",
            "section_order": 3,
            "section_kind": "onomastic_recentiorum",
            "heading_raw": "INDEX AUCTORUM RECENTIORUM QUI IN NOTIS LAUDANTUR.",
            "heading_norm": "index auctorum recentiorum qui in notis laudantur",
            "heading_letter": None,
            "page_start": 1117,
            "page_end": 1118,
            "file_start": str(file_by_num[569]),
            "file_end": str(file_by_num[569]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical onomastic index of recent authors cited in the notes.",
                "evidence_files": [str(file_by_num[569])],
            },
        },
        {
            "section_key": infer_section_key("ordo_rerum"),
            "volume_id": VOLUME_ID,
            "work_key": "aratoris_epistolas_et_libros",
            "section_order": 4,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1119,
            "page_end": 1124,
            "file_start": str(file_by_num[570]),
            "file_end": str(file_by_num[572]),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial contents/closure block at the end of the volume.",
                "evidence_files": [str(file_by_num[570]), str(file_by_num[572])],
            },
        },
    ]


def parse_volume(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    current_section_kind: str | None = None
    current_section_key: str | None = None
    current_letter: str | None = None
    buffer = ""
    buffer_file: str | None = None
    last_page: int | None = None
    entry_order = 0
    node_order = 0
    section1_started = False

    def flush() -> None:
        nonlocal buffer, buffer_file, last_page, entry_order
        text = norm_text(buffer)
        source_file = buffer_file
        buffer = ""
        buffer_file = None
        if not text or current_section_key is None or source_file is None:
            return
        if is_letter_line(text):
            return
        refs_local, page_hints, last_page_local = parse_refs(text, last_page)
        if last_page_local is not None:
            last_page = last_page_local
        lemma_raw = text
        cut = None
        for ref in refs_local:
            idx = text.find(ref["page_ref_raw"])
            if idx >= 0 and (cut is None or idx < cut):
                cut = idx
        if cut is not None and cut > 0:
            lemma_raw = norm_text(text[:cut].rstrip(" ,;:."))
        lemma_display = lemma_raw
        entry_kind = choose_entry_kind(current_section_kind or "alphabetical_general", text, refs_local)
        inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else source_file
        if target_file_best is None:
            target_file_best = source_file

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": current_section_key,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": current_letter if current_section_kind == "alphabetical_general" else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": section_start_file_for(current_section_kind),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.9 if refs_local else 0.72,
            "raw_json": {
                "source_file": source_file,
                "section_kind": current_section_kind,
                "page_hints": page_hints,
            },
        }
        if not refs_local and (VIDE_RE.search(text) or IBID_RE.search(text) or text.lower().endswith("passim.")):
            entry["raw_json"]["remission_or_passim"] = True
        entries.append(entry)

        if refs_local:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or text,
                    "query_names": unique_preserve_order(
                        [
                            lemma_raw,
                            lemma_display,
                            text,
                            norm_text(lemma_raw.split(",", 1)[0] if lemma_raw else None),
                        ]
                    ),
                    "page_hints": [str(p) for p in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": text,
                }
            )
        for ref in refs_local:
            ref_entry = {
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
                "target_file": page_map.get(ref["page_ref_int"]),
                "target_file_probability": 0.9 if page_map.get(ref["page_ref_int"]) else None,
                "section_start_file": section_start_file_for(current_section_kind),
                "editorial_anchor_file": source_file,
                "confidence": 0.88 if page_map.get(ref["page_ref_int"]) else 0.7,
                "raw_json": {},
            }
            refs.append(ref_entry)

    def section_start_file_for(section_kind: str | None) -> str | None:
        if section_kind is None:
            return None
        if section_kind == "alphabetical_general":
            return str(files_by_num[559])
        if section_kind == "onomastic_veterum":
            return str(files_by_num[567])
        if section_kind == "onomastic_recentiorum":
            return str(files_by_num[569])
        if section_kind == "ordo_rerum":
            return str(files_by_num[570])
        return None

    files_by_num = {file_num(path): path for path in files}

    for path in files:
        num = file_num(path)
        if num < 559 or num > 572:
            continue
        lines = extract_lines(path)
        for line in lines:
            section_kind = infer_section_kind_from_heading(line)
            if section_kind is not None:
                flush()
                current_section_kind = section_kind
                current_section_key = infer_section_key(section_kind)
                current_letter = None
                if section_kind == "alphabetical_general" and MAIN_INDEX_SUBHEADING_RE.search(line):
                    section1_started = True
                continue
            if current_section_kind is None:
                continue
            if current_section_kind == "alphabetical_general":
                if MAIN_INDEX_HEADING_RE.search(line):
                    continue
                if is_letter_line(line):
                    flush()
                    current_letter = line.strip(".")
                    node_order += 1
                    nodes.append(
                        {
                            "node_key": f"{VOLUME_ID}:node:{node_order:03d}",
                            "section_key": infer_section_key("alphabetical_general"),
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter,
                            "label_sort": current_letter,
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source_file": str(path), "section_kind": current_section_kind},
                        }
                    )
                    section1_started = True
                    continue
                if not section1_started:
                    continue
            if current_section_kind == "ordo_rerum":
                # The contents block is treated as a heading group section with continuous lines.
                pass
            if buffer and should_continue(buffer, line):
                buffer = f"{buffer} {line}"
                continue
            if buffer:
                flush()
            buffer = line
            buffer_file = str(path)
    flush()
    return entries, refs, nodes, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_entry = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": helper.get("top_candidates") or helper.get("candidates") or [],
        }
        if helper.get("best_candidate"):
            raw_json["helper"]["best_candidate"] = helper["best_candidate"]
        if helper.get("top_candidates"):
            best = helper["top_candidates"][0]
            if isinstance(best, dict):
                entry["target_file_best"] = best.get("file") or entry.get("target_file_best")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL068 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    page_map = build_page_map(files)
    entries, refs, nodes, helper_entries = parse_volume(files, page_map)

    helper_request_entries = []
    helper_seed_keys = [
        "A et æ, confusa",
        "Christiani",
        "Paulius scribendum, non Paulus",
        "Actus apostolorum",
    ]
    for item in entries:
        lemma = item.get("lemma_raw") or ""
        if any(seed.lower() in lemma.lower() for seed in helper_seed_keys):
            helper_request_entries.append(
                {
                    "entry_id": item["entry_key"],
                    "lemma_raw": item.get("lemma_raw"),
                    "query_names": unique_preserve_order(
                        [
                            item.get("lemma_raw"),
                            item.get("lemma_display"),
                            item.get("entry_raw"),
                        ]
                    ),
                    "page_hints": [str(x) for x in (item.get("raw_json", {}).get("page_hints") or [])],
                    "page_hint_ints": item.get("raw_json", {}).get("page_hints") or [],
                    "context_raw": item.get("context_raw"),
                }
            )
    if not helper_request_entries:
        helper_request_entries = helper_entries[:4]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries[:8],
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    attach_helper(entries, helper_output)
    for ref in refs:
        entry = next((item for item in entries if item["entry_key"] == ref["entry_key"]), None)
        helper = None
        if entry:
            helper = (entry.get("raw_json") or {}).get("helper")
        if helper and helper.get("top_candidates"):
            best = helper["top_candidates"][0]
            if isinstance(best, dict) and best.get("file"):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")

    file_559 = str(next(path for path in files if file_num(path) == 559))
    file_567 = str(next(path for path in files if file_num(path) == 567))
    file_568 = str(next(path for path in files if file_num(path) == 568))
    file_569 = str(next(path for path in files if file_num(path) == 569))
    file_570 = str(next(path for path in files if file_num(path) == 570))
    file_572 = str(next(path for path in files if file_num(path) == 572))

    sections = section_metadata(files)
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The main vocabulary index is followed by two onomastic indexes and a final ORDO RERUM block.",
            "OCR literals, mixed Latin/Greek forms, and non-specific remissions were preserved.",
        ],
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the main alphabetical index, the ancient- and recent-author indexes, and the closing ORDO RERUM block from the OCR tail with conservative line grouping and locator resolution.",
        "evidence_files": [file_559, file_567, file_568, file_569, file_570, file_572],
    }
    notes = [
        "Section 1 is the vocabulary index beginning at the NOTES index heading.",
        "Sections 2 and 3 are onomastic indexes of cited authors.",
        "Section 4 is the editorial ORDO RERUM closure block.",
        "Helper output was used only as locator support for a small calibration subset.",
    ]
    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "helper_entry_count": len(helper_request_entries),
        "entry_count": len(entries),
        "ref_count": len(refs),
    }
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL068 alphabetical payload and keep the four editorial blocks separated",
        "completed": [
            "OCR tail sections identified",
            "helper request generated and resolved",
            "entries, refs, and sections serialized",
        ],
        "pending": [
            "assemble final payload",
            "validate section boundaries and locator evidence",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "Do not collapse distinct numbering systems.",
        ],
    }

    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(args.intermediate_dir / "manifest.json", manifest)
    write_json(args.intermediate_dir / "todo.json", todo)

    assemble_cmd = [
        sys.executable,
        str(Path(__file__).resolve().with_name("assemble_alphabetical_payload.py")),
        "--intermediate-dir",
        str(args.intermediate_dir),
        "--output",
        str(args.output_file),
        "--generated-at",
        manifest["updated_at"],
        "--pretty",
    ]
    proc = subprocess.run(
        assemble_cmd,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"assemble_alphabetical_payload.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")


if __name__ == "__main__":
    main()

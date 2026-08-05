#!/usr/bin/env python3
"""Usage: build the PL071 alphabetical-index payload from OCR tail files.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl071_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL071/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL071_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL071_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL071 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL071_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


VOLUME_ID = "PL071"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 71"
SECTION1_START_FILE = 629
SECTION1_END_FILE = 648
SECTION1_PAGE_START = 1249
SECTION1_PAGE_END = 1288
SECTION2_START_FILE = 649
SECTION2_END_FILE = 656
SECTION2_PAGE_START = 1289
SECTION2_PAGE_END = 1302

BLOCK_RE = re.compile(
    r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>',
    re.IGNORECASE | re.DOTALL,
)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SECTION1_HEADING_RE = re.compile(r"INDEX GENERALIS\.?", re.IGNORECASE)
SECTION2_HEADING_RE = re.compile(r"ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.?)?", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|\d{1,4}\s*)$", re.IGNORECASE)
SPLIT_RE = re.compile(r"(?<=[0-9a-z\)])\.\s*(?:—\s*)?(?=[A-ZÆŒ])")
SEMICOLON_SPLIT_RE = re.compile(r";\s*(?=[A-ZÆŒ])")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
VIDE_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?|v\.)\b", re.IGNORECASE)
CONTINUATION_WORD_RE = re.compile(
    r"^(?:Ibi|Ibid|Ejus|Eam|Ea|Eo|Eos|Hic|Hæc|Hic|Hinc|Item|Unde|Quod|Quæ|Quae|Quam|Quo|Quibus|Quibusdam)\b",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:.")
    return value or None


def norm_sort(text: str | None) -> str | None:
    value = normalize(text)
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


def extract_blocks(path: Path, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        if kinds is not None and kind not in kinds:
            continue
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and not NOISE_RE.fullmatch(line)]
        if lines:
            blocks.append({"kind": kind, "lines": lines, "text": "\n".join(lines)})
    return blocks


def extract_header_pages(path: Path) -> list[int]:
    pages: list[int] = []
    for block in extract_blocks(path, {"cabecalho", "outro"}):
        for line in block["lines"][:3]:
            for match in HEADER_PAGE_RE.finditer(line):
                pages.append(int(match.group(1)))
    return pages


def infer_start_page(files: list[Path]) -> int:
    for path in files[:5]:
        pages = [page for page in extract_header_pages(path) if page >= 10]
        if pages:
            pages = sorted(dict.fromkeys(pages))
            return pages[0]
    return 1


def build_page_map(files: list[Path], start_page: int) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for offset, path in enumerate(files):
        page_a = start_page + offset * 2
        page_b = page_a + 1
        page_map[page_a] = str(path)
        page_map[page_b] = str(path)
    return page_map


def lookup_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, str]:
    if page is None:
        return None, "none"
    if page_map:
        min_page = min(page_map)
        max_page = max(page_map)
        if page < min_page or page > max_page:
            return None, "out_of_range"
    if page in page_map:
        return page_map[page], "exact"
    for delta in (1, -1, 2, -2, 3, -3):
        candidate = page + delta
        if candidate in page_map:
            return page_map[candidate], f"fuzzy_{delta:+d}"
    return None, "missing"


def split_segments(text: str) -> list[str]:
    parts = [text]
    next_parts: list[str] = []
    for part in parts:
        for chunk in SPLIT_RE.split(part):
            chunk = chunk.strip()
            if not chunk:
                continue
            next_parts.extend([p.strip() for p in SEMICOLON_SPLIT_RE.split(chunk) if p.strip()])
    return next_parts


def clean_segment(segment: str) -> str:
    value = normalize(segment) or ""
    value = re.sub(r"^\d{1,4}\s*(?:[.,;:]|\.?\s*)+", "", value)
    value = value.lstrip("—- ")
    return value.strip()


def parse_refs(text: str, previous_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1
    stripped = text.strip()
    if IBID_RE.fullmatch(stripped) and previous_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": stripped,
                "page_ref_raw": stripped,
                "page_ref_int": previous_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(previous_page)
        return refs, page_hints, previous_page

    for match in PAGE_RE.finditer(text):
        raw = match.group(0).strip()
        start = int(match.group(1))
        end = match.group(2)
        ref_kind = "editorial_page"
        range_start_raw = None
        range_end_raw = None
        if end is not None:
            ref_kind = "editorial_range"
            range_start_raw = str(start)
            range_end_raw = str(int(end))
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
            }
        )
        page_hints.append(start)
        ref_order += 1
        previous_page = start

    if not refs and IBID_RE.search(text) and previous_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": previous_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(previous_page)
    return refs, page_hints, previous_page


def split_entry_parts(text: str) -> list[str]:
    cleaned = normalize(text) or ""
    if not cleaned:
        return []
    if SECTION1_HEADING_RE.fullmatch(cleaned) or SECTION2_HEADING_RE.fullmatch(cleaned):
        return [cleaned]
    parts = [part.strip() for part in re.split(r"\s+", cleaned) if part.strip()]
    return [cleaned] if len(parts) == 1 else [cleaned]


def classify_entry(text: str, section_kind: str) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    stripped = text.strip()
    if VIDE_RE.search(stripped) and not PAGE_RE.search(stripped):
        return "cross_reference"
    first_word = stripped.split(" ", 1)[0] if stripped else ""
    if stripped[:1].islower() or CONTINUATION_WORD_RE.match(first_word):
        return "sublemma"
    return "lemma"


def build_entry(
    *,
    text: str,
    source_file: str,
    section_kind: str,
    section_key: str,
    section_start_file: str,
    current_letter: str | None,
    current_node_key: str | None,
    page_map: dict[int, str],
    previous_page: int | None,
    entry_order: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int | None, dict[str, Any] | None]:
    cleaned = clean_segment(text)
    if not cleaned:
        return None, [], previous_page, None
    if LETTER_RE.fullmatch(cleaned):
        return None, [], previous_page, None
    if SECTION1_HEADING_RE.fullmatch(cleaned) or SECTION2_HEADING_RE.fullmatch(cleaned):
        return None, [], previous_page, None
    if cleaned in {"K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
        return None, [], previous_page, None

    refs_local, page_hints, updated_previous_page = parse_refs(cleaned, previous_page)
    entry_kind = classify_entry(cleaned, section_kind)
    lemma_raw: str | None = None
    if refs_local:
        cut = None
        for ref in refs_local:
            idx = cleaned.find(ref["page_ref_raw"])
            if idx >= 0 and (cut is None or idx < cut):
                cut = idx
        if cut is not None and cut > 0:
            lemma_raw = normalize(cleaned[:cut])
        else:
            lemma_raw = normalize(cleaned)
    else:
        lemma_raw = None if entry_kind == "cross_reference" else normalize(cleaned)

    if lemma_raw and entry_kind == "cross_reference" and refs_local:
        entry_kind = "lemma"
    inferred_page = refs_local[0]["page_ref_int"] if refs_local else None
    if inferred_page is None:
        target_file_best, target_mode = source_file, "source"
    else:
        target_file_best, target_mode = lookup_target(inferred_page, page_map)
    confidence = 0.9 if refs_local else 0.72
    if entry_kind == "cross_reference":
        confidence = 0.68
    if entry_kind == "sublemma":
        confidence = 0.74 if refs_local else 0.62
    if section_kind == "ordo_rerum":
        confidence = 0.93 if refs_local else 0.87

    entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
    entry = {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": current_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": norm_sort(lemma_raw),
        "lemma_sort": norm_sort(lemma_raw),
        "entry_raw": cleaned,
        "context_raw": cleaned,
        "heading_letter": current_letter if section_kind == "alphabetical_general" else None,
        "inferred_printed_page": inferred_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": source_file,
        "target_file_best": target_file_best,
        "confidence": confidence,
        "raw_json": {
            "source_file": source_file,
            "section_kind": section_kind,
            "page_hints": page_hints,
            "target_lookup_mode": target_mode,
        },
    }

    refs: list[dict[str, Any]] = []
    for ref in refs_local:
        target_file, target_mode_ref = lookup_target(ref["page_ref_int"], page_map)
        if target_file is None and target_mode_ref == "out_of_range":
            target_file = None
        refs.append(
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
                "target_file_probability": 0.96 if target_mode_ref == "exact" else (0.82 if target_mode_ref.startswith("fuzzy") else None),
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.94 if target_mode_ref == "exact" else 0.8,
                "raw_json": {"target_lookup_mode": target_mode_ref},
            }
        )

    helper_entry: dict[str, Any] | None = None
    if refs_local:
        query_names = []
        for candidate in (
            lemma_raw,
            normalize(cleaned.split(",", 1)[0]) if cleaned else None,
            cleaned,
        ):
            candidate = normalize(candidate)
            if candidate and candidate not in query_names:
                query_names.append(candidate)
        helper_entry = {
            "entry_id": entry_key,
            "lemma_raw": lemma_raw or cleaned,
            "query_names": query_names[:4],
            "page_hints": [str(page) for page in page_hints],
            "page_hint_ints": page_hints,
            "context_raw": cleaned,
        }
    return entry, refs, updated_previous_page, helper_entry


def parse_section(
    *,
    files: list[Path],
    start_file_num: int,
    end_file_num: int,
    section_key: str,
    section_kind: str,
    section_heading_raw: str,
    section_heading_norm: str,
    page_start: int,
    page_end: int,
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    current_node_key: str | None = None
    node_order = 0
    entry_order = 0
    previous_page: int | None = None
    buffered = ""
    buffered_file: str | None = None
    section_start_file = str(next(path for path in files if file_num(path) == start_file_num))
    section_end_file = str(next(path for path in files if file_num(path) == end_file_num))
    section_started = True

    def flush() -> None:
        nonlocal buffered, buffered_file, entry_order, previous_page
        if not buffered or buffered_file is None:
            buffered = ""
            buffered_file = None
            return
        entry_order += 1
        entry, entry_refs, updated_previous_page, helper_entry = build_entry(
            text=buffered,
            source_file=buffered_file,
            section_kind=section_kind,
            section_key=section_key,
            section_start_file=section_start_file,
            current_letter=current_letter,
            current_node_key=current_node_key,
            page_map=page_map,
            previous_page=previous_page,
            entry_order=entry_order,
        )
        buffered = ""
        buffered_file = None
        if entry is None:
            entry_order -= 1
            return
        previous_page = updated_previous_page
        entries.append(entry)
        refs.extend(entry_refs)
        if helper_entry:
            helper_entries.append(helper_entry)

    for path in files:
        num = file_num(path)
        if num < start_file_num or num > end_file_num:
            continue
        for block in extract_blocks(path, {"texto_principal"}):
            for raw_line in block["lines"]:
                line = normalize(raw_line) or ""
                if not line or NOISE_RE.fullmatch(line):
                    continue
                if section_kind == "alphabetical_general":
                    if SECTION1_HEADING_RE.fullmatch(line):
                        section_started = True
                        continue
                    if LETTER_RE.fullmatch(line):
                        flush()
                        current_letter = line
                        node_order += 1
                        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": section_key,
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": line,
                                "label_norm": line.lower(),
                                "label_sort": line.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source_file": str(path), "section_kind": section_kind},
                            }
                        )
                        continue
                    if not section_started:
                        continue
                segments = split_segments(line)
                for segment in segments:
                    segment = clean_segment(segment)
                    if not segment:
                        continue
                    if current_letter and LETTER_RE.fullmatch(segment):
                        flush()
                        current_letter = segment
                        node_order += 1
                        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": section_key,
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": segment,
                                "label_norm": segment.lower(),
                                "label_sort": segment.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source_file": str(path), "section_kind": section_kind},
                            }
                        )
                        continue
                    if buffered and (
                        buffered.endswith(("-", "—", ":", ";", ",")) or
                        segment[:1].islower() or
                        CONTINUATION_WORD_RE.match(segment)
                    ):
                        buffered = f"{buffered} {segment}"
                        continue
                    flush()
                    buffered = segment
                    buffered_file = str(path)
        flush()

    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": "gregorii_turonensis_historica",
        "section_order": 1 if section_kind == "alphabetical_general" else 2,
        "section_kind": section_kind,
        "heading_raw": section_heading_raw,
        "heading_norm": section_heading_norm,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": section_start_file,
        "file_end": section_end_file,
        "confidence": 0.97 if section_kind == "alphabetical_general" else 0.95,
        "raw_json": {
            "section_kind_reason": (
                "Alphabetical general index of the volume, with lemma entries, letter nodes, and page citations."
                if section_kind == "alphabetical_general"
                else "Editorial contents/closure block at the end of the volume."
            ),
            "evidence_files": [section_start_file, section_end_file],
        },
    }
    return [section], nodes, entries, refs, helper_entries


def build_helper_request(volume_id: str, source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    selected = sorted(
        helper_entries,
        key=lambda item: (-len(item.get("page_hint_ints") or []), item.get("entry_id") or ""),
    )
    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": selected[:8],
    }


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
    helper_by_id = {
        item.get("entry_id"): item
        for item in (helper_output.get("entries") or [])
        if isinstance(item, dict)
    }
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        entry.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": helper.get("top_candidates") or helper.get("candidates") or [],
            "best_candidate": helper.get("best_candidate"),
        }
        best = helper.get("best_candidate")
        if not best and helper.get("top_candidates"):
            best = helper["top_candidates"][0]
        if isinstance(best, dict) and best.get("file"):
            entry["target_file_best"] = best["file"]


def assemble_payload(
    *,
    source_root: Path,
    sections: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    coverage: dict[str, Any],
    notes: list[str],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Alphabetical general index and final ORDO RERUM closure recovered from the OCR tail.",
                "OCR literals and abbreviated page citations were preserved; helper was used only as locator support for a calibration subset.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def renumber_refs_in_place(refs: list[dict[str, Any]]) -> None:
    counters: dict[str, int] = defaultdict(int)
    for ref in refs:
        entry_key = ref.get("entry_key")
        if not entry_key:
            continue
        counters[entry_key] += 1
        ref["ref_order"] = counters[entry_key]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL071 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_text_files(args.source_root)
    if not files:
        raise SystemExit("No OCR files found in source_root.")

    start_page = infer_start_page(files)
    page_map = build_page_map(files, start_page)

    section1, nodes1, entries1, refs1, helper1 = parse_section(
        files=files,
        start_file_num=SECTION1_START_FILE,
        end_file_num=SECTION1_END_FILE,
        section_key=f"{VOLUME_ID}:alpha:alphabetical_general:001",
        section_kind="alphabetical_general",
        section_heading_raw="INDEX GENERALIS",
        section_heading_norm="index generalis",
        page_start=SECTION1_PAGE_START,
        page_end=SECTION1_PAGE_END,
        page_map=page_map,
    )
    section2, nodes2, entries2, refs2, helper2 = parse_section(
        files=files,
        start_file_num=SECTION2_START_FILE,
        end_file_num=SECTION2_END_FILE,
        section_key=f"{VOLUME_ID}:alpha:ordo_rerum:002",
        section_kind="ordo_rerum",
        section_heading_raw="ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        section_heading_norm="ordo rerum quae in hoc tomo continentur",
        page_start=SECTION2_PAGE_START,
        page_end=SECTION2_PAGE_END,
        page_map=page_map,
    )

    sections = section1 + section2
    nodes = nodes1 + nodes2
    entries = entries1 + entries2
    refs = refs1 + refs2
    helper_entries = helper1 + helper2

    helper_request = build_helper_request(VOLUME_ID, args.source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    attach_helper(entries, helper_output)
    renumber_refs_in_place(refs)

    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)
    for entry in entries:
        item_refs = refs_by_entry.get(entry["entry_key"], [])
        if item_refs:
            entry["inferred_printed_page"] = item_refs[0]["page_ref_int"]
            if entry.get("target_file_best") is None and item_refs[0].get("target_file"):
                entry["target_file_best"] = item_refs[0]["target_file"]

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the alphabetic index and closing ORDO RERUM block from the OCR tail with conservative segment splitting, "
            "but some OCR fragments remain abbreviated or continuation-like."
        ),
        "evidence_files": [
            str(next(path for path in files if file_num(path) == SECTION1_START_FILE)),
            str(next(path for path in files if file_num(path) == SECTION1_END_FILE)),
            str(next(path for path in files if file_num(path) == SECTION2_START_FILE)),
            str(next(path for path in files if file_num(path) == SECTION2_END_FILE)),
        ],
    }
    notes = [
        "The main index is alphabetical general; letter-group nodes were preserved when the OCR emitted standalone letters.",
        "The final ORDO RERUM block was preserved as a separate section_kind.",
        "Page-to-file resolution uses the observed two-page-per-file sequence, with helper used only for a small calibration subset.",
    ]

    generated_at = now_iso()
    payload = assemble_payload(
        source_root=args.source_root,
        sections=sections,
        nodes=nodes,
        entries=entries,
        refs=refs,
        coverage=coverage,
        notes=notes,
        generated_at=generated_at,
    )

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": generated_at,
        "current_focus": "Finalize PL071 alphabetical payload and keep the index and ORDO RERUM separated",
        "completed": [
            "OCR tail sections identified",
            "helper request generated and resolved",
            "entries, refs, and sections serialized",
        ],
        "pending": [
            "validate section boundaries and target lookups",
            "write the final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "Do not collapse distinct numbering systems.",
        ],
    }

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": generated_at,
        "entry_count": len(entries),
        "ref_count": len(refs),
        "section_count": len(sections),
        "helper_entry_count": len(helper_request.get("entries") or []),
    }

    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(args.intermediate_dir / "manifest.json", manifest)
    write_json(args.intermediate_dir / "todo.json", todo)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

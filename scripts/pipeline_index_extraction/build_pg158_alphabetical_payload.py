#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg158_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG158/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG158_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG158_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG158 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG158_alphabetical_indices.json

Builds the PG158 alphabetical payload by splitting the author index, the main
index rerum et verborum, and the final ordo rerum from the OCR tail.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
sys.path.insert(0, str(ROOT))

VOLUME_ID = "PG158"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 158"

AUTHOR_START = 675
AUTHOR_END = 676
ANALYTIC_START = 677
ANALYTIC_END = 692
ORDO_START = 692
ORDO_END = 692

AUTHOR_HEADING = (
    "INDICES SYLLABUS AUCTORUM SIVE EDITORUM, SIVE NONDUM EDITORUM, "
    "IN ANNALIBUS GLYCÆ LAUDATORUM."
)
ANALYTIC_HEADING = (
    "INDEX RERUM ET VERBORUM IN MICHAELIS GLYCÆ ANNALIBUS PRÆCIPUE MEMORABILIUM."
)
ORDO_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

CONTINUATION_PREFIXES = (
    "ibid.",
    "ibid",
    "id.",
    "id",
    "et ",
    "Item",
    "Ejus",
    "Ejusdem",
    "De ",
    "In ",
    "Ad ",
    "Ab ",
    "A ",
    "Quo ",
    "Quid ",
    "Unde ",
    "Cur ",
    "Cum ",
    "Sub ",
    "Ex ",
    "Inter ",
    "Supra ",
    "Infra ",
)
NOISE_PATTERNS = (
    "Digitized by Google",
    "PATROL. GR.",
    "THIS VOLUME",
    "DOES NOT CIRCULATE",
    "OUTSIDE THE LIBRARY",
)
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADER_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


@dataclass
class Block:
    kind: str
    text: str


@dataclass
class EntrySeed:
    section_slug: str
    section_key: str
    source_file: str
    file_seq: int
    heading_letter: str | None
    parent_node_key: str | None
    entry_kind: str
    entry_raw: str
    lemma_raw: str | None
    page_hints: list[int]
    section_start_file: str
    editorial_anchor_file: str
    inferred_printed_page: int | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def extract_blocks(path: Path) -> tuple[str, list[Block]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    header_parts: list[str] = []
    blocks: list[Block] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        kind = normalize(match.group(1) or "") or ""
        text = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        if not normalize(text):
            continue
        if kind == "cabecalho":
            header_parts.append(normalize(text) or "")
        else:
            blocks.append(Block(kind=kind, text=text))
    return normalize(" ".join(header_parts)) or "", blocks


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        header, _ = extract_blocks(path)
        for token in HEADER_NUMBER_RE.findall(header):
            value = int(token)
            mapping.setdefault(value, str(path))
    return mapping


def split_inline_entries(text: str) -> list[str]:
    pieces = [normalize(part) for part in re.split(r"(?<=\d\.)\s+(?=[A-ZÆŒἈἘἸὉὙὨΜ])", text)]
    result: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        result.append(piece)
    return result or [text]


def should_merge(previous: str, current: str, section_slug: str) -> bool:
    if section_slug == "ordo_rerum":
        return previous.endswith("-")
    if previous.endswith("-"):
        return True
    if current.startswith(CONTINUATION_PREFIXES):
        return True
    if current and current[0].islower():
        return True
    if previous.endswith(",") or previous.endswith(";") or previous.endswith(":"):
        return True
    if not re.search(r"\d", previous):
        return True
    if previous.endswith("et seq.") or previous.endswith("et seq") or previous.endswith("seq.") or previous.endswith("seqq."):
        return True
    return False


def extract_page_hints(text: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for token in PAGE_TOKEN_RE.findall(text):
        value = int(token)
        if value > 1200:
            continue
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def derive_lemma(entry_raw: str, entry_kind: str) -> str | None:
    if entry_kind == "cross_reference":
        left = entry_raw.split(".")[0]
        left = left.split("Vide")[0].strip(" ,.;:")
        return normalize(left) or None
    if "," in entry_raw:
        lemma = normalize(entry_raw.split(",", 1)[0])
        return lemma
    if "." in entry_raw:
        lemma = normalize(entry_raw.split(".", 1)[0])
        return lemma
    return normalize(entry_raw)


def looks_cross_reference(text: str) -> bool:
    lowered = text.lower()
    return ("vide " in lowered or "vid." in lowered) and not extract_page_hints(text)


def read_section_lines(path: Path, section_slug: str) -> tuple[list[str], list[str]]:
    header, blocks = extract_blocks(path)
    lines: list[str] = []
    letters: list[str] = []
    in_ordo = False
    for block in blocks:
        if block.kind == "nota_marginal" and LETTER_RE.fullmatch(block.text):
            letters.append(block.text)
            continue
        if block.kind != "texto_principal":
            continue
        for raw_line in block.text.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if any(noise in line for noise in NOISE_PATTERNS):
                continue
            if section_slug == "author_index":
                if line.startswith("Revocatur Lector"):
                    continue
            if section_slug == "analytic_subject":
                if line == ORDO_HEADING or "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in line:
                    in_ordo = True
                    break
                if line == ANALYTIC_HEADING:
                    continue
            if section_slug == "ordo_rerum":
                if header and "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in header:
                    pass
                if "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in line:
                    continue
                if line == "MICHAEL GLYCAS.":
                    lines.append(line)
                    continue
            if section_slug == "author_index" and line == AUTHOR_HEADING:
                continue
            if LETTER_RE.fullmatch(line):
                letters.append(line)
                continue
            lines.append(line)
        if section_slug == "analytic_subject" and in_ordo:
            break
    if section_slug == "ordo_rerum":
        capture = False
        lines = []
        letters = []
        for block in blocks:
            if block.kind != "texto_principal":
                continue
            for raw_line in block.text.splitlines():
                line = normalize(raw_line)
                if not line:
                    continue
                if "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in line:
                    capture = True
                    continue
                if not capture:
                    continue
                if any(noise in line for noise in NOISE_PATTERNS):
                    continue
                lines.append(line)
    return lines, letters


def parse_entries(
    *,
    source_root: Path,
    all_files: list[Path],
    page_map: dict[int, str],
    section_slug: str,
    section_key: str,
    file_start_seq: int,
    file_end_seq: int,
    section_start_file: str,
) -> tuple[list[EntrySeed], list[dict[str, Any]]]:
    files = [p for p in all_files if file_start_seq <= file_seq(p) <= file_end_seq]
    entries: list[EntrySeed] = []
    nodes: list[dict[str, Any]] = []
    current_letter: str | None = None
    node_order = 0
    entry_buffer: list[str] = []
    entry_source_file: str | None = None
    entry_source_seq: int | None = None

    def flush_buffer() -> None:
        nonlocal entry_buffer, entry_source_file, entry_source_seq
        if not entry_buffer or entry_source_file is None or entry_source_seq is None:
            entry_buffer = []
            entry_source_file = None
            entry_source_seq = None
            return
        merged = normalize(" ".join(entry_buffer))
        entry_buffer = []
        source_file = entry_source_file
        source_seq = entry_source_seq
        entry_source_file = None
        entry_source_seq = None
        if not merged:
            return
        for piece in split_inline_entries(merged):
            page_hints = extract_page_hints(piece)
            entry_kind = "cross_reference" if looks_cross_reference(piece) else "lemma"
            if section_slug == "ordo_rerum" and piece.isupper() and not page_hints:
                entry_kind = "heading_group"
            lemma_raw = None if entry_kind == "heading_group" else derive_lemma(piece, entry_kind)
            inferred_page = page_hints[0] if page_hints else None
            entries.append(
                EntrySeed(
                    section_slug=section_slug,
                    section_key=section_key,
                    source_file=source_file,
                    file_seq=source_seq,
                    heading_letter=current_letter,
                    parent_node_key=(
                        f"{VOLUME_ID}:node:{section_slug}:letter:{sort_norm(current_letter)}"
                        if current_letter
                        else None
                    ),
                    entry_kind=entry_kind,
                    entry_raw=piece,
                    lemma_raw=lemma_raw,
                    page_hints=page_hints,
                    section_start_file=section_start_file,
                    editorial_anchor_file=source_file,
                    inferred_printed_page=inferred_page,
                )
            )

    for path in files:
        seq = file_seq(path)
        lines, letters = read_section_lines(path, section_slug)
        for letter in letters:
            node_key = f"{VOLUME_ID}:node:{section_slug}:letter:{sort_norm(letter)}"
            if not any(node["node_key"] == node_key for node in nodes):
                node_order += 1
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": letter,
                        "label_norm": sort_norm(letter),
                        "label_sort": sort_norm(letter),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {
                            "source_file": str(path),
                            "file_seq": seq,
                        },
                    }
                )
                current_letter = letter
        for line in lines:
            if LETTER_RE.fullmatch(line):
                flush_buffer()
                current_letter = line
                node_key = f"{VOLUME_ID}:node:{section_slug}:letter:{sort_norm(line)}"
                if not any(node["node_key"] == node_key for node in nodes):
                    node_order += 1
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": sort_norm(line),
                            "label_sort": sort_norm(line),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source_file": str(path), "file_seq": seq},
                        }
                    )
                continue
            if not entry_buffer:
                entry_buffer = [line]
                entry_source_file = str(path)
                entry_source_seq = seq
            elif should_merge(entry_buffer[-1], line, section_slug):
                entry_buffer.append(line)
            else:
                flush_buffer()
                entry_buffer = [line]
                entry_source_file = str(path)
                entry_source_seq = seq
    flush_buffer()
    return entries, nodes


def build_helper_request(entries: list[EntrySeed], page_map: dict[int, str], output_path: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        if not entry.page_hints:
            continue
        needs_helper = (
            any(page not in page_map for page in entry.page_hints)
            or "ibid" in entry.entry_raw.lower()
            or "seq." in entry.entry_raw.lower()
            or "seqq." in entry.entry_raw.lower()
        )
        if not needs_helper:
            continue
        query_names = [q for q in [entry.lemma_raw, entry.entry_raw.split(",", 1)[0]] if q]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID}:{entry.section_slug}:{idx:04d}",
                "lemma_raw": entry.lemma_raw or entry.entry_raw[:80],
                "query_names": query_names,
                "page_hints": [str(p) for p in entry.page_hints],
                "page_hint_ints": entry.page_hints,
                "context_raw": entry.entry_raw,
            }
        )
    payload = {
        "volume_id": VOLUME_ID,
        "source_root": str(output_path.parents[2] / "teste" / VOLUME_ID / "text") if False else None,
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    payload["source_root"] = str(ROOT / "teste" / VOLUME_ID / "text")
    write_json(output_path, payload)
    return payload


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
    request = json.loads(helper_request_json.read_text(encoding="utf-8"))
    if not request.get("entries"):
        write_json(helper_output_json, {"volume_id": VOLUME_ID, "entries": []})
        return
    cmd = [
        sys.executable,
        str(ROOT / "scripts/index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def helper_map(helper_request: dict[str, Any], helper_output_path: Path) -> dict[str, dict[str, Any]]:
    output = json.loads(helper_output_path.read_text(encoding="utf-8")) if helper_output_path.exists() else {}
    request_by_id = {item["entry_id"]: item for item in helper_request.get("entries", [])}
    result: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_by_id:
            continue
        req = request_by_id[entry_id]
        result[entry_id] = {
            "request": req,
            "status": item.get("status"),
            "best_candidate": item.get("best_candidate"),
            "candidates": item.get("candidates", [])[:5],
        }
    return result


def make_refs(
    entry_key: str,
    entry: EntrySeed,
    page_map: dict[int, str],
    helper_info: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    helper_candidates = helper_info.get("candidates", []) if helper_info else []
    candidate_by_page: dict[int, dict[str, Any]] = {}
    for candidate in helper_candidates:
        inferred = candidate.get("inferred_printed_page")
        if isinstance(inferred, int) and inferred not in candidate_by_page:
            candidate_by_page[inferred] = candidate
    for order, page in enumerate(entry.page_hints, start=1):
        candidate = candidate_by_page.get(page)
        target_file = None
        probability = None
        if candidate:
            target_file = candidate.get("file")
            probability = candidate.get("probability")
        if target_file is None:
            target_file = page_map.get(page)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": order,
                "ref_kind": "editorial_page",
                "ref_raw": str(page),
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": entry.section_start_file,
                "editorial_anchor_file": entry.editorial_anchor_file,
                "confidence": 0.84 if target_file else 0.6,
                "raw_json": {
                    "source_file": entry.source_file,
                    "section_kind": entry.section_slug,
                    "helper_status": helper_info.get("status") if helper_info else None,
                },
            }
        )
    return refs


def build_payload(
    *,
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    all_files = discover_files(source_root)
    page_map = build_page_map(all_files)

    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:author_index:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": AUTHOR_HEADING,
            "heading_norm": sort_norm(AUTHOR_HEADING),
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(next(p for p in all_files if file_seq(p) == AUTHOR_START)),
            "file_end": str(next(p for p in all_files if file_seq(p) == AUTHOR_END)),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Author index of cited writers and sources praised in Michael Glyca's Annales.",
                "source_files": [str(p) for p in all_files if AUTHOR_START <= file_seq(p) <= AUTHOR_END],
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": ANALYTIC_HEADING,
            "heading_norm": sort_norm(ANALYTIC_HEADING),
            "heading_letter": None,
            "page_start": 1085,
            "page_end": 1115,
            "file_start": str(next(p for p in all_files if file_seq(p) == ANALYTIC_START)),
            "file_end": str(next(p for p in all_files if file_seq(p) == ANALYTIC_END)),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Main INDEX RERUM ET VERBORUM section for Michael Glyca's Annales; the final Z entries continue at the top of file 692 before the ORDO RERUM title.",
                "source_files": [str(p) for p in all_files if ANALYTIC_START <= file_seq(p) <= ANALYTIC_END],
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING,
            "heading_norm": sort_norm(ORDO_HEADING),
            "heading_letter": None,
            "page_start": 1115,
            "page_end": 1116,
            "file_start": str(next(p for p in all_files if file_seq(p) == ORDO_START)),
            "file_end": str(next(p for p in all_files if file_seq(p) == ORDO_END)),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial contents table for the volume, distinct from the alphabetical author and subject indices.",
                "source_files": [str(next(p for p in all_files if file_seq(p) == ORDO_START))],
            },
        },
    ]

    entry_seeds: list[EntrySeed] = []
    nodes: list[dict[str, Any]] = []
    for section in sections:
        slug = section["section_kind"]
        if slug == "analytic_subject":
            section_slug = "analytic_subject"
            start_seq, end_seq = ANALYTIC_START, ANALYTIC_END
        elif slug == "author_index":
            section_slug = "author_index"
            start_seq, end_seq = AUTHOR_START, AUTHOR_END
        else:
            section_slug = "ordo_rerum"
            start_seq, end_seq = ORDO_START, ORDO_END
        seeds, section_nodes = parse_entries(
            source_root=source_root,
            all_files=all_files,
            page_map=page_map,
            section_slug=section_slug,
            section_key=section["section_key"],
            file_start_seq=start_seq,
            file_end_seq=end_seq,
            section_start_file=section["file_start"],
        )
        entry_seeds.extend(seeds)
        nodes.extend(section_nodes)

    helper_request = build_helper_request(entry_seeds, page_map, helper_request_json)
    run_helper(helper_request_json, helper_output_json)
    helper_by_id = helper_map(helper_request, helper_output_json)

    request_index_by_context: dict[tuple[str, str], str] = {}
    for item in helper_request["entries"]:
        key = (item["lemma_raw"], item["context_raw"])
        request_index_by_context[key] = item["entry_id"]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    section_counts = defaultdict(int)

    for seed in entry_seeds:
        section_counts[seed.section_slug] += 1
        entry_key = f"{VOLUME_ID}:entry:{seed.section_slug}:{section_counts[seed.section_slug]:04d}"
        helper_entry_id = request_index_by_context.get((seed.lemma_raw or seed.entry_raw[:80], seed.entry_raw))
        helper_info = helper_by_id.get(helper_entry_id) if helper_entry_id else None
        target_file_best = None
        if helper_info and helper_info.get("best_candidate"):
            target_file_best = helper_info["best_candidate"].get("file")
        elif seed.page_hints:
            target_file_best = page_map.get(seed.page_hints[0])
        entry = {
            "entry_key": entry_key,
            "section_key": seed.section_key,
            "parent_node_key": seed.parent_node_key,
            "entry_order": len(entries) + 1,
            "entry_kind": seed.entry_kind,
            "lemma_raw": seed.lemma_raw,
            "lemma_display": seed.lemma_raw,
            "lemma_norm": sort_norm(seed.lemma_raw) if seed.lemma_raw else None,
            "lemma_sort": sort_norm(seed.lemma_raw) if seed.lemma_raw else None,
            "entry_raw": seed.entry_raw,
            "context_raw": None,
            "heading_letter": seed.heading_letter,
            "inferred_printed_page": seed.inferred_printed_page,
            "section_start_file": seed.section_start_file,
            "editorial_anchor_file": seed.editorial_anchor_file,
            "target_file_best": target_file_best,
            "confidence": 0.86 if seed.page_hints else 0.78,
            "raw_json": {
                "source_file": seed.source_file,
                "file_seq": seed.file_seq,
                "section_kind": seed.section_slug,
                "page_hints": seed.page_hints,
                "helper_entry_id": helper_entry_id,
                "helper_status": helper_info.get("status") if helper_info else None,
                "helper_best_candidate": helper_info.get("best_candidate") if helper_info else None,
                "helper_candidates": helper_info.get("candidates") if helper_info else [],
            },
        }
        entries.append(entry)
        refs.extend(make_refs(entry_key, seed, page_map, helper_info))

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Recovered author, analytical, and ordo lines from the OCR tail with conservative line-based segmentation and helper-supported target lookup.",
        "evidence_files": [section["file_start"] for section in sections],
    }
    notes = [
        "PG158 contains three relevant tail sections: an author index (675-676), INDEX RERUM ET VERBORUM (677-692), and a final ORDO RERUM starting mid-file 692.",
        "Entries were segmented conservatively from OCR lines; when one OCR line clearly bundled several lemmata, the line was split at sentence boundaries before a new capitalized lemma.",
        "Target resolution used page hints plus local helper output scoped to PG158 only.",
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "PG158 tail indices extracted from OCR files 675-692.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Completed PG158 payload assembly and helper-assisted target resolution.",
            "completed": [
                "confirmed author index, analytic subject index, and ordo rerum sections",
                "generated helper request and helper output",
                "assembled canonical payload",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Segmentation is conservative and line-based when OCR layout is ambiguous.",
                "File 692 contains both the tail of the analytical index and the opening of the ordo rerum.",
            ],
        },
    )
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "entry_count": len(entries),
            "ref_count": len(refs),
            "node_count": len(nodes),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(output_file),
        },
    )
    write_json(output_file, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--helper-request-json", type=Path, required=True)
    parser.add_argument("--helper-output-json", type=Path, required=True)
    parser.add_argument("--intermediate-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
    )


if __name__ == "__main__":
    main()

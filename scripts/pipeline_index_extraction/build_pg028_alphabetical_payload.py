#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg028_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG028/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG028_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG028_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG028 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG028_alphabetical_indices.json

Build the PG028 analytical alphabetical payload from the OCR tail, write the
helper request, run target resolution, and serialize the final JSON payload.
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG028"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 28"

SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING_RAW = "INDEX ANALYTICUS."
SECTION_HEADING_NORM = "index analyticus"
SECTION_KIND_REASON = (
    "Analytical alphabetical subject index headed INDEX ANALYTICUS.; the OCR tail "
    "continues through the A-Z subject lemmata and stops before the separate "
    "ADDENDA / ORDO RERUM / INDEX OPERUM S. ATHANASII material."
)

SOURCE_START_SEQ = 819
SOURCE_END_SEQ = 823
SECTION_START_FILE_SEQ = 819
SECTION_END_FILE_SEQ = 823

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG028/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG028_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG028_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG028_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG028"

BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<tipo>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.S)
FILE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
PAGE_TOKEN_RE = re.compile(
    r"(?<!\w)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?:\s*(et\s+seqq?\.?|seq\.?|seqq\.?|passim))?",
    re.IGNORECASE,
)
INLINE_PAGE_PAIR_RE = re.compile(r"^(\d{1,4})[,.](\d{1,2})\.$")
OCR_NOISE = {"Digitized by Google"}
LETTER_RE = re.compile(r"^[A-Z]$")

STOPWORDS_AFTER_HEADWORD = {
    "a",
    "ad",
    "ab",
    "ac",
    "at",
    "au",
    "cum",
    "de",
    "e",
    "et",
    "ex",
    "in",
    "id",
    "ibid",
    "non",
    "per",
    "pro",
    "quae",
    "qui",
    "quid",
    "quod",
    "quare",
    "sive",
    "seu",
    "ut",
    "vide",
    "vid",
    "voir",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = FILE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_text_lines(path: Path) -> list[tuple[str, str, int]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[tuple[str, str, int]] = []
    for block in BLOCK_RE.finditer(raw):
        tipo = (block.group("tipo") or "").strip().lower()
        if tipo != "texto_principal":
            continue
        content = block.group("content") or ""
        for line_no, raw_line in enumerate(content.splitlines(), start=1):
            line = normalize(raw_line)
            if not line or line in OCR_NOISE:
                continue
            if line == "Digitized by Google":
                continue
            if re.fullmatch(r"\d{1,4}", line):
                continue
            lines.append((str(path), line, line_no))
    return lines


def is_letter_heading(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def is_section_heading(line: str) -> bool:
    return normalize(line) in {
        SECTION_HEADING_RAW,
        "INDEX ANALYTICUS",
        "ORDO RERUM",
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        "ADDENDA",
        "INDEX OPERUM S. ATHANASII",
    }


def should_continue(previous_line: str, current_line: str) -> bool:
    prev = previous_line.rstrip()
    if not prev:
        return False
    if current_line[:1].islower() or current_line[:1] in "-—,;:.)](":
        return True
    if current_line and current_line.split(" ", 1)[0].lower() in {"ibid", "id", "vide", "vid", "de", "ad", "in", "ex", "et"}:
        return True
    if prev.endswith(("-", "—", ",", ";", ":")):
        return True
    if prev[-1].isdigit() or prev[-1].isalpha():
        return not prev.endswith((".", "?", "!"))
    return False


def leading_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw)
    if not text:
        return None
    text = re.split(r"(?<!\d)\.(?=\s+[A-ZΑ-Ω])", text, maxsplit=1)[0]
    page_match = PAGE_TOKEN_RE.search(text)
    if page_match:
        text = text[: page_match.start()].rstrip(" ,;:.")
    if "," in text:
        text = text.split(",", 1)[0].strip()
    words = text.split()
    if len(words) >= 2 and words[1].lower().rstrip(".") in STOPWORDS_AFTER_HEADWORD:
        return words[0].strip(" ,;:.")
    return text.strip(" ,;:.") or None


def extract_ref_tokens(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None, str | None]] = set()
    masked = list(entry_raw)

    def mask_span(start: int, end: int) -> None:
        for idx in range(start, end):
            masked[idx] = " "

    for match in re.finditer(r"(?<!\d)(\d{1,4})[,.](\d{1,2})\.(?!\d)", entry_raw):
        raw = match.group(0)
        page_int = int(match.group(1))
        line_raw = match.group(2)
        key = (raw, page_int, None, line_raw)
        if key not in seen:
            seen.add(key)
            refs.append(
                {
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": line_raw,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "ref_kind": "editorial_page_line",
                    "page_token_source": "ocr_index_entry",
                }
            )
        mask_span(match.start(), match.end())

    for match in re.finditer(r"(?<!\d)(\d{1,4}):(\d)(?!\d)", entry_raw):
        raw = match.group(0)
        page_int = int(f"{match.group(1)}5{match.group(2)}")
        key = (raw, page_int, None, None)
        if key not in seen:
            seen.add(key)
            refs.append(
                {
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "ref_kind": "editorial_page",
                    "page_token_source": "ocr_corrected_colon_zero",
                }
            )
        mask_span(match.start(), match.end())

    masked_text = "".join(masked)

    for match in PAGE_TOKEN_RE.finditer(masked_text):
        raw = match.group(0).strip()
        if not raw:
            continue
        page_raw = match.group(1)
        page_int = int(page_raw)
        end_raw = match.group(2)
        suffix = (match.group(3) or "").strip()
        key = (raw, page_int, end_raw, suffix or None)
        if key in seen:
            continue
        seen.add(key)
        if end_raw:
            ref_kind = "editorial_range"
        elif suffix:
            ref_kind = "editorial_range"
        else:
            ref_kind = "editorial_page"
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page_raw if (end_raw or suffix) else None,
                "range_end_raw": end_raw,
                "ref_kind": ref_kind,
                "page_token_source": "ocr_index_entry",
            }
        )

    unique: list[dict[str, Any]] = []
    seen_key: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref["ref_raw"],
            ref["page_ref_int"],
            ref["page_ref_col"],
            ref["line_ref_raw"],
            ref["range_start_raw"],
            ref["range_end_raw"],
        )
        if key in seen_key:
            continue
        seen_key.add(key)
        unique.append(ref)
    return unique


def helper_summary(helper_item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_item:
        return None
    candidates = []
    for cand in helper_item.get("candidates", [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in cand.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ][:6],
            }
        )
    best = helper_item.get("best_candidate") or {}
    return {
        "status": helper_item.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary") or helper_item.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "inferred_printed_page": best.get("inferred_printed_page"),
        },
        "top_candidates": candidates,
    }


def build_provisional_entries(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    files = [p for p in discover_files(source_root) if SOURCE_START_SEQ <= file_seq(p) <= SOURCE_END_SEQ]
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    evidence_files = [str(p) for p in files]
    letter_nodes: dict[str, str] = {}
    current_letter: str | None = None
    current_entry: dict[str, Any] | None = None
    entry_order = 0
    node_order = 0

    def ensure_letter_node(letter: str, source_file: str, line_no: int, inferred: bool = False) -> str:
        nonlocal node_order
        if letter in letter_nodes:
            return letter_nodes[letter]
        node_order += 1
        node_key = f"{SECTION_KEY}:node:letter:{node_order:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98 if not inferred else 0.90,
                "raw_json": {
                    "source_file": source_file,
                    "line_no": line_no,
                    "inferred": inferred,
                },
            }
        )
        letter_nodes[letter] = node_key
        return node_key

    def flush_current() -> None:
        nonlocal current_entry, entry_order, current_letter
        if not current_entry:
            return
        entry_order += 1
        entry_raw = normalize(" ".join(current_entry["lines"]))
        lemma = leading_lemma(entry_raw)
        page_refs = extract_ref_tokens(entry_raw)
        if current_letter is None:
            inferred_letter = (lemma or entry_raw[:1] or "A")[:1].upper()
            if inferred_letter and inferred_letter.isalpha():
                current_letter = inferred_letter
                ensure_letter_node(current_letter, current_entry["source_file"], current_entry["line_start"], inferred=True)
        node_key = letter_nodes.get(current_letter or "") if current_letter else None
        entry_key = f"{SECTION_KEY}:entry:{entry_order:04d}"
        entry_kind = "lemma"
        if lemma and (lemma.lower().startswith(("vide", "vid.")) or (not page_refs and "vide" in entry_raw.lower())):
            entry_kind = "cross_reference"
        inferred_page = page_refs[0]["page_ref_int"] if page_refs else None
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": sort_norm(lemma),
                "lemma_sort": sort_norm(lemma),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_page,
                "section_start_file": current_entry["section_start_file"],
                "editorial_anchor_file": current_entry["source_file"],
                "target_file_best": current_entry["source_file"],
                "confidence": 0.88 if page_refs else 0.74,
                "raw_json": {
                    "source_file": current_entry["source_file"],
                    "line_start": current_entry["line_start"],
                    "line_end": current_entry["line_end"],
                    "line_count": len(current_entry["lines"]),
                    "page_tokens": [ref["page_ref_raw"] for ref in page_refs],
                    "page_hints": [ref["page_ref_int"] for ref in page_refs],
                    "entry_kind_reason": "cross_reference" if entry_kind == "cross_reference" else "lemma_or_subentry_cluster",
                    "ref_count": len(page_refs),
                },
            }
        )
        current_entry = None

    for path in files:
        for source_file, line, line_no in extract_text_lines(path):
            if is_section_heading(line):
                flush_current()
                continue
            if is_letter_heading(line):
                flush_current()
                current_letter = line
                ensure_letter_node(line, source_file, line_no, inferred=False)
                continue
            if current_entry and should_continue(current_entry["lines"][-1], line):
                current_entry["lines"].append(line)
                current_entry["line_end"] = line_no
                continue
            flush_current()
            current_entry = {
                "source_file": source_file,
                "section_start_file": str(files[0]) if files else source_file,
                "line_start": line_no,
                "line_end": line_no,
                "lines": [line],
            }
            if current_letter is None:
                current_letter = "A"
                ensure_letter_node("A", source_file, line_no, inferred=True)

    flush_current()
    return entries, nodes, evidence_files


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        if not page_hints:
            continue
        query_names = []
        for candidate in [entry.get("lemma_raw"), entry.get("lemma_display"), entry.get("lemma_norm")]:
            if candidate and candidate not in query_names:
                query_names.append(candidate)
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:80],
                "query_names": query_names[:4],
                "page_hints": [str(v) for v in page_hints[:6]],
                "page_hint_ints": page_hints[:6],
                "context_raw": entry["entry_raw"][:240],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


def candidate_inferred_page(candidate: dict[str, Any]) -> int | None:
    if candidate.get("inferred_printed_page") is not None:
        try:
            return int(candidate["inferred_printed_page"])
        except Exception:
            return None
    reason = candidate.get("reason_summary") or ""
    match = re.search(r"inferred_page=(\d+)", reason)
    if match:
        return int(match.group(1))
    return None


def pick_target_file_for_page(helper_item: dict[str, Any] | None, page_int: int | None) -> tuple[str | None, float | None]:
    if not helper_item:
        return None, None
    candidates = helper_item.get("candidates", []) or []
    if not candidates:
        best = helper_item.get("best_candidate") or {}
        return best.get("file"), best.get("probability")
    if page_int is not None:
        exact = [
            cand for cand in candidates
            if candidate_inferred_page(cand) == page_int
        ]
        if exact:
            exact.sort(key=lambda c: (c.get("probability") or 0.0, c.get("score") or 0.0), reverse=True)
            return exact[0].get("file"), exact[0].get("probability")
        candidates = sorted(
            candidates,
            key=lambda cand: (
                abs((candidate_inferred_page(cand) or page_int) - page_int) if candidate_inferred_page(cand) is not None else 9999,
                -(cand.get("probability") or 0.0),
            ),
        )
        best = candidates[0]
        if best.get("file") is not None:
            return best.get("file"), best.get("probability")
    best = helper_item.get("best_candidate") or {}
    return best.get("file"), best.get("probability")


def build_refs(entry: dict[str, Any], helper_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    refs = []
    page_hints = entry["raw_json"].get("page_hints") or []
    page_tokens = entry["raw_json"].get("page_tokens") or []
    if not page_hints:
        return refs
    seen: set[tuple[Any, ...]] = set()
    for idx, ref in enumerate(extract_ref_tokens(entry["entry_raw"]), start=1):
        key = (
            ref["ref_raw"],
            ref["page_ref_int"],
            ref["line_ref_raw"],
            ref["range_start_raw"],
            ref["range_end_raw"],
        )
        if key in seen:
            continue
        seen.add(key)
        target_file, probability = pick_target_file_for_page(helper_item, ref["page_ref_int"])
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": len(refs) + 1,
                "ref_kind": ref["ref_kind"],
                "ref_raw": ref["ref_raw"],
                "page_ref_raw": ref["page_ref_raw"],
                "page_ref_int": ref["page_ref_int"],
                "page_ref_col": ref["page_ref_col"],
                "line_ref_raw": ref["line_ref_raw"],
                "range_start_raw": ref["range_start_raw"],
                "range_end_raw": ref["range_end_raw"],
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": 0.88 if target_file else 0.58,
                "raw_json": {
                    "helper_entry_id": entry["entry_key"],
                    "helper_page_hints": page_hints,
                    "page_token_source": ref["page_token_source"],
                    "selected_target_file_reason": (
                        "helper exact page match" if target_file and ref["page_ref_int"] in page_hints else "helper best available candidate"
                    ),
                    "raw_page_token": page_tokens[idx - 1] if idx - 1 < len(page_tokens) else None,
                },
            }
        )
    return refs


def build_payload(source_root: Path, helper_output: dict[str, Any], provisional_entries: list[dict[str, Any]], evidence_files: list[str]) -> dict[str, Any]:
    helper_items = helper_map(helper_output)
    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(Path(evidence_files[0])) if evidence_files else str(source_root / "4ebe6371-f4ac-47d6-b2af-164f337b1b6f-819.txt"),
            "file_end": str(Path(evidence_files[-1])) if evidence_files else str(source_root / "4ebe6371-f4ac-47d6-b2af-164f337b1b6f-823.txt"),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "source_files": evidence_files,
                "notes": [
                    "OCR pagination in the headers is irregular; file anchors are more reliable than the printed page sequence in this tail.",
                    "ADDENDA, ORDO RERUM and INDEX OPERUM S. ATHANASII were inspected but excluded from the alphabetical payload.",
                ],
            },
        }
    ]

    nodes = []
    entries = []
    refs = []

    for provisional in provisional_entries:
        helper_item = helper_items.get(provisional["entry_key"])
        entry = dict(provisional)
        entry["raw_json"] = dict(provisional["raw_json"])
        entry["raw_json"]["helper"] = helper_summary(helper_item)
        if helper_item and helper_item.get("best_candidate"):
            entry["raw_json"]["helper_best_candidate"] = helper_item["best_candidate"]
        if entry["entry_kind"] == "cross_reference":
            entry["confidence"] = 0.78 if entry["inferred_printed_page"] is None else 0.82
        if not entry["lemma_raw"]:
            entry["entry_kind"] = "editorial_note"
        entries.append(entry)
        refs.extend(build_refs(entry, helper_item))

    # Rebuild nodes in the order they were first seen.
    node_order = {}
    for entry in entries:
        letter = entry.get("heading_letter")
        if not letter:
            continue
        if letter not in node_order:
            node_order[letter] = len(node_order) + 1
            nodes.append(
                {
                    "node_key": f"{SECTION_KEY}:node:letter:{node_order[letter]:03d}",
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "node_order": node_order[letter],
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.96 if letter == "A" else 0.98,
                    "raw_json": {
                        "source": "inferred_from_ocr_index_lines" if letter == "A" else "explicit_letter_heading_or_grouping",
                    },
                }
            )

    # Map nodes back to entries.
    node_key_map = {node["label_raw"]: node["node_key"] for node in nodes}
    for entry in entries:
        letter = entry.get("heading_letter")
        if letter in node_key_map:
            entry["parent_node_key"] = node_key_map[letter]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the analytical index entries from OCR files 819-823 with conservative line-cluster segmentation; "
            "the section boundary before ADDENDA/ORDO RERUM was checked and excluded."
        ),
        "evidence_files": evidence_files + ([str(source_root / "4ebe6371-f4ac-47d6-b2af-164f337b1b6f-824.txt")] if evidence_files else []),
    }

    notes = [
        "The A heading is inferred from the first index cluster because the OCR page opens directly on the first A-entry without a standalone letter line.",
        "Reference pages remain literal OCR values in the entry text; obvious OCR noise such as 2:0 was corrected only for target lookup and recorded in raw_json.",
        "Editorial closure material after the alphabetical index was inspected separately and excluded from sections.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Analytical alphabetical index tail from files 819-823.",
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    provisional_entries, provisional_nodes, evidence_files = build_provisional_entries(args.source_root)
    # Keep a short resumption note in the runtime workspace.
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PG028 analytical index references and write final payload.",
            "completed": [
                "Section boundary checked in OCR files 819-823",
                "Provisional entry clusters extracted from OCR",
            ],
            "pending": [
                "Run helper on page-hint entries",
                "Assemble final payload",
            ],
            "blocked": [],
            "notes": [
                "Use OCR file anchors for the index section itself and helper-driven target files for the cited pages.",
            ],
        },
    )

    build_helper_request(provisional_entries, args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(args.source_root, helper_output, provisional_entries, evidence_files)

    write_json(args.output_file, payload)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Final payload written.",
            "completed": [
                "Section boundary checked in OCR files 819-823",
                "Provisional entry clusters extracted from OCR",
                "Helper request generated and resolved",
                "Final payload written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Index-only OCR files were used as editorial anchors; cited-page targets were resolved through the helper.",
            ],
        },
    )


if __name__ == "__main__":
    main()

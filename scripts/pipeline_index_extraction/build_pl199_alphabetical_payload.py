#!/usr/bin/env python3
"""Usage: build the PL199 alphabetical-index payload from OCR tail material.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl199_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL199/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL199_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL199_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL199 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL199_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml

VOLUME_ID = "PL199"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 199"

SCRIPT_TARGET_LOCATOR = Path(__file__).resolve().parents[1] / "index_target_locator.py"

INDEX_START_SEQ = 604
ORDO_START_SEQ = 607
ORDO_END_SEQ = 616

TITLE_RE = re.compile(r"INDEX ALPHABETICUS\s+IN EP\.?\s*J\.?\s*SARESB\.?", re.IGNORECASE)
ORDO_RE = re.compile(r"^ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LOCATOR_RE = re.compile(r"(?<!\w)((?:[A-ZÆŒ]\s*)+\d{1,4}(?:\s*,\s*\d{1,4})*(?:[*?])?)")
ROMAN_PAGE_RE = re.compile(r"(?<![A-Za-z])([IVXLCDM]{1,8}\.?)")
PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
WS_RE = re.compile(r"\s+")


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
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = value.lower()
    return value


def page_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse sequence from {path}")
    return int(match.group(1))


def header_pages(path: Path) -> tuple[int | None, int | None]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text", ""))
    nums = [int(n) for n in re.findall(r"\b\d{1,4}\b", header)]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return nums[0], nums[-1]


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        left, right = header_pages(path)
        for num in {left, right}:
            if isinstance(num, int) and num not in page_map:
                page_map[num] = path.as_posix()
    return page_map


def file_lines(path: Path) -> list[str]:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", raw_text, re.S)
    lines: list[str] = []
    for match in blocks:
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        block_type = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if block_type not in {"cabecalho", "texto_principal", "nota", "nota_marginal", "rodape"}:
            continue
        body = match.group("body") or ""
        for line in body.splitlines():
            line = normalize(line)
            if line and line.lower() != "digitized by google":
                lines.append(line)
    return lines


def is_single_letter(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def extract_locator_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in LOCATOR_RE.finditer(text):
        token = normalize(match.group(1)).strip(" ,.;:")
        if token:
            tokens.append(token)
    return tokens


def first_locator_int(token: str) -> int | None:
    nums = [int(n) for n in re.findall(r"\d{1,4}", token)]
    return nums[-1] if nums else None


def line_terminates_entry(line: str) -> bool:
    return bool(re.search(r"\b\d{1,4}[*?]?\s*$", line) or line.endswith("."))


def strip_locator_tokens(text: str) -> str:
    stripped = text
    for token in extract_locator_tokens(text):
        stripped = stripped.replace(token, " ")
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped.strip(" ,.;:-")


def derive_lemma(entry_raw: str) -> str:
    text = strip_locator_tokens(entry_raw)
    for marker in (" Vide ", " Vid. ", " vide ", " vid. ", " voir ", " cf. ", " id. "):
        if marker in text:
            text = text.split(marker, 1)[0]
    return normalize(text).strip(" ,;:.—–-")


def entry_kind_from_text(text: str, locator_tokens: list[str]) -> str:
    if not locator_tokens and re.search(r"\b(?:vid\.|vide|voir|cf\.|id\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def make_query_names(lemma_raw: str, entry_raw: str) -> list[str]:
    names: list[str] = []
    for candidate in (
        lemma_raw,
        lemma_raw.replace(".", "").replace(",", ""),
        derive_lemma(entry_raw),
        entry_raw,
    ):
        candidate = normalize(candidate)
        if candidate and candidate not in names:
            names.append(candidate)
    return names[:4]


def build_index_section(source_root: Path, page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    files = [source_root / f"45eaab3d-5e85-425e-8c63-ea8cd309235f-{seq}.txt" for seq in range(INDEX_START_SEQ, ORDO_START_SEQ)]
    section_key = f"{VOLUME_ID}:alpha:alphabetical_general:001"
    section_start_file = files[0].as_posix()
    evidence_files = [path.as_posix() for path in files]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    started = False
    current_letter: str | None = None
    current_node_key: str | None = None
    entry_order = 0
    node_order = 0
    buffer: list[str] = []
    buffer_file: str | None = None

    def flush_buffer() -> None:
        nonlocal buffer, buffer_file, entry_order
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        buffer = []
        source_file = buffer_file or section_start_file
        buffer_file = None
        if not entry_raw:
            return
        locator_tokens = extract_locator_tokens(entry_raw)
        lemma_raw = derive_lemma(entry_raw)
        if not lemma_raw:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:001:{entry_order:04d}"
        inferred_page = first_locator_int(locator_tokens[0]) if locator_tokens else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else source_file
        if inferred_page is not None and target_file_best is None:
            target_file_best = source_file
        entry_kind = entry_kind_from_text(entry_raw, locator_tokens)
        confidence = 0.88 if locator_tokens else 0.73
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize(lemma_raw).lower(),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter,
            "inferred_printed_page": inferred_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": confidence,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "alphabetical_general",
                "page_hint_count": len(locator_tokens),
                "locator_tokens": locator_tokens,
            },
        }
        entries.append(entry)
        if locator_tokens:
            for ref_order, token in enumerate(locator_tokens, start=1):
                page_int = first_locator_int(token)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": token,
                        "page_ref_raw": token,
                        "page_ref_int": page_int,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": page_map.get(page_int) if page_int is not None else None,
                        "target_file_probability": 0.98 if page_int in page_map else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.86 if page_int in page_map else 0.6,
                        "raw_json": {
                            "source_file": source_file,
                            "source_token": token,
                            "section_kind": "alphabetical_general",
                        },
                    }
                )
        if locator_tokens and len(locator_tokens) >= 2:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": make_query_names(lemma_raw, entry_raw),
                    "page_hints": [str(first_locator_int(token)) for token in locator_tokens if first_locator_int(token) is not None],
                    "page_hint_ints": [first_locator_int(token) for token in locator_tokens if first_locator_int(token) is not None],
                    "context_raw": entry_raw,
                }
            )
        entry["raw_json"]["source_entry_order"] = entry_order

    for path in files:
        lines = file_lines(path)
        for line in lines:
            if not started:
                if TITLE_RE.search(line):
                    started = True
                continue
            if line.startswith("A designat ordinem"):
                continue
            if line.startswith("ms. Paris") or line.startswith("qui in duas partes"):
                continue
            if is_single_letter(line):
                flush_buffer()
                if current_letter != line:
                    current_letter = line
                    node_order += 1
                    current_node_key = f"{VOLUME_ID}:node:001:{node_order:03d}"
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
                            "confidence": 0.95,
                            "raw_json": {"source_file": path.as_posix()},
                        }
                    )
                continue
            if line.startswith("D ") and current_letter != "D" and node_order == 0:
                current_letter = "D"
            if buffer and line_terminates_entry(normalize(" ".join(buffer))) and not line[0].isdigit():
                flush_buffer()
            if not buffer:
                buffer_file = path.as_posix()
            buffer.append(line)
            if line_terminates_entry(line):
                # Some lines spill over into the next line; only flush here when
                # the next line is clearly a new entry or a letter divider.
                flush_buffer()
    flush_buffer()

    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": "Epistolae Joannis Saresberiensis",
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX ALPHABETICUS IN EPISTOLAS JOANNIS SARESBERIENSIS",
        "heading_norm": "index alphabeticus in epistolas joannis saresberiensis",
        "heading_letter": None,
        "page_start": 1173,
        "page_end": 1176,
        "file_start": section_start_file,
        "file_end": files[-1].as_posix(),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Alphabetical epistles index arranged by incipits with witness-page locators; it is not an author-name index.",
            "source_root": source_root.as_posix(),
            "evidence_files": evidence_files,
        },
    }
    return section, nodes, entries, refs, helper_entries


def build_ordo_section(source_root: Path, page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    files = [source_root / f"45eaab3d-5e85-425e-8c63-ea8cd309235f-{seq}.txt" for seq in range(ORDO_START_SEQ, ORDO_END_SEQ + 1)]
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"
    section_start_file = files[0].as_posix()
    evidence_files = [path.as_posix() for path in files]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    buffer: list[str] = []
    buffer_file: str | None = None

    def flush_buffer() -> None:
        nonlocal buffer, buffer_file, entry_order
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        buffer = []
        source_file = buffer_file or section_start_file
        buffer_file = None
        if not entry_raw:
            return
        locator_tokens = extract_locator_tokens(entry_raw)
        lemma_raw = derive_lemma(entry_raw)
        if not lemma_raw:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:002:{entry_order:04d}"
        inferred_page = first_locator_int(locator_tokens[0]) if locator_tokens else None
        target_file_best = page_map.get(inferred_page) if inferred_page is not None else source_file
        if inferred_page is not None and target_file_best is None:
            target_file_best = source_file
        entry_kind = entry_kind_from_text(entry_raw, locator_tokens)
        confidence = 0.91 if locator_tokens else 0.74
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize(lemma_raw).lower(),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": inferred_page,
                "section_start_file": section_start_file,
                "editorial_anchor_file": source_file,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": "ordo_rerum",
                    "page_hint_count": len(locator_tokens),
                    "locator_tokens": locator_tokens,
                },
            }
        )
        if locator_tokens:
            for ref_order, token in enumerate(locator_tokens, start=1):
                page_int = first_locator_int(token)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": token,
                        "page_ref_raw": token,
                        "page_ref_int": page_int,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": page_map.get(page_int) if page_int is not None else None,
                        "target_file_probability": 0.98 if page_int in page_map else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.9 if page_int in page_map else 0.6,
                        "raw_json": {
                            "source_file": source_file,
                            "source_token": token,
                            "section_kind": "ordo_rerum",
                        },
                    }
                )
        if locator_tokens and len(locator_tokens) >= 2:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": make_query_names(lemma_raw, entry_raw),
                    "page_hints": [str(first_locator_int(token)) for token in locator_tokens if first_locator_int(token) is not None],
                    "page_hint_ints": [first_locator_int(token) for token in locator_tokens if first_locator_int(token) is not None],
                    "context_raw": entry_raw,
                }
            )

    for path in files:
        lines = file_lines(path)
        started = False
        seen_entry = False
        for line in lines:
            if not started:
                if ORDO_RE.search(line):
                    started = True
                continue
            if line.startswith("Digitized by Google"):
                continue
            if ORDO_RE.search(line):
                continue
            if line == "QUÆ IN HOC TOMO CONTINENTUR.":
                continue
            if is_single_letter(line):
                flush_buffer()
                continue
            if not seen_entry and not re.search(r"\d", line):
                continue
            seen_entry = True
            if not buffer:
                buffer_file = path.as_posix()
            buffer.append(line)
            if line_terminates_entry(line):
                flush_buffer()
    flush_buffer()

    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quæ in hoc tomo continentur.",
        "heading_letter": None,
        "page_start": 1177,
        "page_end": 1196,
        "file_start": section_start_file,
        "file_end": files[-1].as_posix(),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Closing contents table separate from the alphabetical epistles index.",
            "source_root": source_root.as_posix(),
            "evidence_files": evidence_files,
        },
    }
    return section, [], entries, refs, helper_entries


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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def build_helper_request(volume_id: str, source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": volume_id,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def write_todo(intermediate_dir: Path, helper_status: str, completed: list[str], pending: list[str]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL199 alphabetical payload and preserve the OCR-tail editorial structures separately.",
        "completed": completed,
        "pending": pending,
        "blocked": [],
        "notes": [
            "Keep OCR file suffixes separate from printed page numbers and cited references.",
            f"Helper status: {helper_status}.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL199 alphabetical-index payload.")
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--helper-request-json", required=True, type=Path)
    ap.add_argument("--helper-output-json", required=True, type=Path)
    ap.add_argument("--intermediate-dir", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    args = ap.parse_args()

    source_root = args.source_root.resolve()
    page_map = build_page_map(source_root)

    index_section, index_nodes, index_entries, index_refs, index_helper_entries = build_index_section(source_root, page_map)
    ordo_section, ordo_nodes, ordo_entries, ordo_refs, ordo_helper_entries = build_ordo_section(source_root, page_map)

    helper_entries = index_helper_entries + ordo_helper_entries
    helper_request = build_helper_request(VOLUME_ID, source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json) if helper_entries else {"volume_id": VOLUME_ID, "status": "empty", "entries": []}

    helper_lookup = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }
    helper_status = helper_output.get("status")
    if not helper_status:
        statuses = [item.get("status") for item in helper_output.get("entries", []) if isinstance(item, dict)]
        if not statuses:
            helper_status = "empty"
        elif all(status == "resolved" for status in statuses):
            helper_status = "resolved"
        elif all(status == "unresolved" for status in statuses):
            helper_status = "unresolved"
        else:
            helper_status = "mixed"

    def merge_helper(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            hit = helper_lookup.get(entry["entry_key"])
            if not hit:
                continue
            entry["raw_json"]["helper"] = {
                "status": helper_status,
                "candidate_role": hit.get("candidate_role"),
                "reason_summary": hit.get("reason_summary"),
                "best_candidate": hit.get("best_candidate"),
                "candidates": hit.get("candidates", [])[:5],
            }
            best = hit.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]

    merge_helper(index_entries)
    merge_helper(ordo_entries)

    sections = [index_section, ordo_section]
    nodes = index_nodes + ordo_nodes
    entries = index_entries + ordo_entries
    refs = index_refs + ordo_refs

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the alphabetical epistles index and the closing ORDO RERUM contents table from the OCR tail, preserving literal locators and keeping the editorial structures separate.",
        "evidence_files": [
            (source_root / f"45eaab3d-5e85-425e-8c63-ea8cd309235f-{INDEX_START_SEQ}.txt").as_posix(),
            (source_root / f"45eaab3d-5e85-425e-8c63-ea8cd309235f-{ORDO_START_SEQ}.txt").as_posix(),
            (source_root / f"45eaab3d-5e85-425e-8c63-ea8cd309235f-{ORDO_END_SEQ}.txt").as_posix(),
        ],
    }

    notes = [
        "The alphabetical section is an index of epistle incipits and witness/page locators, not an author-name index.",
        "The closing ORDO RERUM section is serialized separately as editorial closure material.",
        f"Helper status: {helper_status}.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": args.helper_request_json.as_posix(),
            "helper_output_json": args.helper_output_json.as_posix(),
            "output_file": args.output_file.as_posix(),
        },
    )
    write_todo(
        args.intermediate_dir,
        helper_status,
        [
            "inspected the OCR tail and identified the alphabetical index plus the closing ORDO RERUM section",
            "built the helper request and ran index_target_locator",
            "wrote intermediate fragments",
        ],
        [
            "review helper evidence on ambiguous lines if any remain",
            "validate the final payload structure",
        ],
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PG159 analytical index and ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg159_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG159/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG159_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG159_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG159 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG159_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG159"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 159"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_FILES = list(range(698, 717))
ORDO_FILES = list(range(716, 720))

INDEX_HEADING_RAW = "INDEX ANALYTICUS IN LAONICUM CHALCOCONDYLAM."
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

ANALYTIC_START_RE = re.compile(r"(?<!\w)([A-ZÆŒ][^,.;:]{0,120}?,\s*(?:ibid\.|\d{1,4})(?:\s*,\s*\d{1,4})*\.)")
ANALYTIC_ENTRY_END_RE = re.compile(r"(?:ibid\.|\d{1,4})(?:\s*,\s*\d{1,4})*\.$")
PAGE_TOKEN_RE = re.compile(r"(?<!\d)(ibid\.|\d{1,4})(?!\d)", re.IGNORECASE)
ORDO_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3}(?:-\d{1,3})?)\.\s+")
ORDO_ENTRY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:-\d{1,3})?)\.\s+(.*?)(?=(?<!\d)\d{1,3}(?:-\d{1,3})?\.\s+|$)", re.DOTALL)
LIB_ENTRY_RE = re.compile(r"(Lib\.\s+[IVXLCDM]+\.\s+—\s+.*?\s+\d{1,4})(?=\s+Lib\.|\s+[A-Z][a-z].*?\d{1,4}\s+\d+\.\s+|$)", re.DOTALL)
UPPER_ENTRY_RE = re.compile(r"([A-ZÆŒ][A-ZÆŒ\s,\-\.]+?\.\s+\d{1,4})(?=\s+[A-ZÆŒ][A-ZÆŒ]|$)")
HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_ws(text: str | None) -> str:
    value = text or ""
    value = value.replace("\xa0", " ")
    value = re.sub(r"(\w)-\s+(\w)", r"\1\2", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_ws(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def file_path(source_root: Path, seq: int) -> Path:
    for path in sorted(source_root.glob("*.txt")):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match and int(match.group(1)) == seq:
            return path
    raise FileNotFoundError(f"missing OCR file for seq {seq}")


def discover_sequences(source_root: Path) -> list[int]:
    seqs: list[int] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            seqs.append(int(match.group(1)))
    return sorted(set(seqs))


def parse_file(source_root: Path, seq: int) -> dict[str, Any]:
    path = file_path(source_root, seq)
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "seq": seq,
        "path": path,
        "header": normalize_ws(parsed.get("header_text") or ""),
        "body": normalize_ws(parsed.get("body_text") or ""),
        "footer": normalize_ws(parsed.get("footer_text") or ""),
    }


def build_page_map(files: list[dict[str, Any]]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for item in files:
        for match in HEADER_PAGE_RE.finditer(item["header"]):
            page_map.setdefault(int(match.group(1)), str(item["path"]))
    return page_map


def analytic_text(files: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in files:
        body = item["body"]
        if item["seq"] == 716 and "LAONICUS CHALCOCONDYLA" in body:
            body = body.split("LAONICUS CHALCOCONDYLA", 1)[0]
        parts.append(body)
    text = " ".join(part for part in parts if part)
    text = text.replace(" C Caba ", " Caba ")
    text = re.sub(r"\b1434\s+(?=Ziences\b)", "", text)
    return normalize_ws(text)


def split_analytic_entries(text: str) -> list[str]:
    text = normalize_ws(text)
    text = re.sub(r"\b([A-ZÆŒ])\s+(?=[A-ZÆŒ][a-zæœ])", "", text)
    text = re.sub(
        r"((?:ibid\.|\d{1,4})\.)\s+(?=[A-ZÆŒΑ-Ωa-z][A-Za-zÆŒæœα-ωΑ-Ω])",
        r"\1\n",
        text,
    )
    chunks = [part.strip() for part in text.splitlines() if part.strip()]
    entries: list[str] = []
    for chunk in chunks:
        if ANALYTIC_ENTRY_END_RE.search(chunk):
            entries.append(chunk)
            continue
        cursor = 0
        while cursor < len(chunk):
            match = ANALYTIC_START_RE.search(chunk, cursor)
            if not match:
                tail = normalize_ws(chunk[cursor:])
                if tail:
                    if entries:
                        entries[-1] = normalize_ws(entries[-1] + " " + tail)
                    else:
                        entries.append(tail)
                break
            if match.start() > cursor:
                prefix = normalize_ws(chunk[cursor:match.start()])
                if prefix:
                    if entries:
                        entries[-1] = normalize_ws(entries[-1] + " " + prefix)
                    else:
                        entries.append(prefix)
            entries.append(normalize_ws(match.group(1)))
            cursor = match.end()
    cleaned: list[str] = []
    for entry in entries:
        value = normalize_ws(entry)
        if not value:
            continue
        if re.fullmatch(r"[A-ZÆŒ]", value):
            continue
        cleaned.append(value)
    return cleaned


def infer_letter(text: str) -> str | None:
    match = re.match(r"([A-ZÆŒ])", text)
    return match.group(1) if match else None


def page_hints_from_ref(ref_raw: str) -> list[int]:
    hints: list[int] = []
    for token in [part.strip() for part in ref_raw.split(",")]:
        if token.isdigit():
            hints.append(int(token))
    return hints


def needs_helper(ref_raw: str | None, hints: list[int], target_file_best: str | None) -> bool:
    if not ref_raw:
        return False
    lowered = ref_raw.lower()
    if "ibid." in lowered:
        return True
    if target_file_best is None:
        return True
    return len(hints) > 1


def lemma_from_analytic(entry_raw: str) -> str | None:
    match = re.match(r"(.+?),\s*(?:ibid\.|\d{1,4})(?:\s*,\s*\d{1,4})*\.$", entry_raw, re.IGNORECASE)
    if match:
        return normalize_ws(match.group(1))
    return normalize_ws(entry_raw.split(".", 1)[0]) or None


def make_ref(
    entry_key: str,
    ref_order: int,
    ref_raw: str,
    page_map: dict[int, str],
    section_start_file: str,
    editorial_anchor_file: str,
    helper_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hints = page_hints_from_ref(ref_raw)
    page_ref_int = hints[0] if hints else None
    target_file = page_map.get(page_ref_int) if page_ref_int is not None else None
    raw_json: dict[str, Any] = {
        "page_hints": hints,
        "source_request_entry_id": entry_key,
    }
    if helper_hint:
        raw_json["helper"] = helper_hint
        best = helper_hint.get("best_candidate") or {}
        if best.get("file"):
            target_file = best.get("file")
    return {
        "entry_key": entry_key,
        "ref_order": ref_order,
        "ref_kind": "editorial_page" if len(hints) <= 1 else "editorial_range",
        "ref_raw": ref_raw,
        "page_ref_raw": ref_raw,
        "page_ref_int": page_ref_int,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": str(hints[0]) if len(hints) > 1 else None,
        "range_end_raw": str(hints[-1]) if len(hints) > 1 else None,
        "target_file": target_file,
        "target_file_probability": 0.95 if target_file else None,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "confidence": 0.9 if target_file else 0.72,
        "raw_json": raw_json,
    }


def build_analytic(
    source_root: Path,
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = [parse_file(source_root, seq) for seq in INDEX_FILES]
    text = analytic_text(files)
    raw_entries = split_analytic_entries(text)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    letter_nodes: dict[str, str] = {}
    for idx, entry_raw in enumerate(raw_entries, start=1):
        letter = infer_letter(entry_raw)
        parent_node_key = None
        if letter:
            if letter not in letter_nodes:
                node_key = f"{VOLUME_ID}:node:analytic:{letter}"
                letter_nodes[letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": INDEX_SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": letter,
                        "label_norm": letter.lower(),
                        "label_sort": letter.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"section_kind": "analytic_subject"},
                    }
                )
            parent_node_key = letter_nodes[letter]
        entry_key = f"{VOLUME_ID}:entry:analytic:{idx:04d}"
        entry_raw = normalize_ws(entry_raw)
        ref_match = re.search(r"((?:ibid\.|\d{1,4})(?:\s*,\s*\d{1,4})*)\.$", entry_raw, re.IGNORECASE)
        ref_raw = ref_match.group(1) if ref_match else None
        hints = page_hints_from_ref(ref_raw or "")
        target_file_best = page_map.get(hints[0]) if hints else None
        lemma_raw = lemma_from_analytic(entry_raw)
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": INDEX_SECTION_KEY,
                "parent_node_key": parent_node_key,
                "entry_order": idx,
                "entry_kind": "lemma",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize_sort(lemma_raw),
                "lemma_sort": normalize_sort(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": letter,
                "inferred_printed_page": hints[0] if hints else None,
                "section_start_file": str(files[0]["path"]),
                "editorial_anchor_file": str(files[0]["path"]),
                "target_file_best": target_file_best,
                "confidence": 0.9 if ref_raw else 0.65,
                "raw_json": {
                    "page_hints": hints,
                    "source_files": [str(file["path"]) for file in files],
                },
            }
        )
        if ref_raw:
            refs.append(
                make_ref(
                    entry_key=entry_key,
                    ref_order=1,
                    ref_raw=ref_raw,
                    page_map=page_map,
                    section_start_file=str(files[0]["path"]),
                    editorial_anchor_file=str(files[0]["path"]),
                )
            )
            if needs_helper(ref_raw, hints, target_file_best):
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or entry_raw[:120],
                        "query_names": [lemma_raw or entry_raw[:120]],
                        "page_hints": [str(value) for value in hints[:4]],
                        "page_hint_ints": hints[:4],
                        "context_raw": entry_raw,
                    }
                )
    return entries, refs, nodes, helper_entries


def ordo_text(files: list[dict[str, Any]]) -> str:
    seq716 = files[0]["body"]
    start = seq716.find("LAONICUS CHALCOCONDYLA")
    base = seq716[start:] if start >= 0 else seq716
    text = " ".join([base] + [item["body"] for item in files[1:]])
    text = normalize_ws(text)
    text = text.split("LEONARDUS CHIENSIS MITTYLÆUS ARCHIEPISCOPUS.", 1)[0]
    return text


def split_ordo_entries(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    prefatory: list[str] = []
    numbered: list[tuple[str, str]] = []
    marker = re.search(r"(?<!\d)1\.\s+", text)
    prefix = text[: marker.start()] if marker else text
    rest = text[marker.start():] if marker else ""
    prefix = normalize_ws(prefix)
    for match in LIB_ENTRY_RE.finditer(prefix):
        prefatory.append(normalize_ws(match.group(1)))
    consumed = LIB_ENTRY_RE.sub("", prefix)
    for match in UPPER_ENTRY_RE.finditer(consumed):
        token = normalize_ws(match.group(1))
        if token and not token.startswith("Lib. "):
            prefatory.append(token)
    for number, body in ORDO_ENTRY_RE.findall(rest):
        numbered.append((number, normalize_ws(body)))
    return prefatory, numbered


def lemma_from_ordo_prefatory(entry_raw: str) -> str | None:
    if "—" in entry_raw:
        return normalize_ws(entry_raw.split("—", 1)[0])
    if entry_raw.isupper():
        return entry_raw
    return normalize_ws(entry_raw.split(".", 1)[0]) or entry_raw


def build_ordo(
    source_root: Path,
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = [parse_file(source_root, seq) for seq in ORDO_FILES]
    text = ordo_text(files)
    prefatory, numbered = split_ordo_entries(text)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    main_node = f"{VOLUME_ID}:node:ordo:001"
    nodes.append(
        {
            "node_key": main_node,
            "section_key": ORDO_SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": ORDO_HEADING_RAW,
            "label_norm": normalize_sort(ORDO_HEADING_RAW),
            "label_sort": normalize_sort(ORDO_HEADING_RAW),
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {"section_kind": "ordo_rerum"},
        }
    )
    entry_order = 0
    for raw in prefatory:
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:ordo:{entry_order:04d}"
        page_match = re.search(r"(\d{1,4})$", raw)
        page = int(page_match.group(1)) if page_match else None
        lemma = lemma_from_ordo_prefatory(raw)
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": main_node,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": normalize_sort(lemma),
                "lemma_sort": normalize_sort(lemma),
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page,
                "section_start_file": str(files[0]["path"]),
                "editorial_anchor_file": str(files[0]["path"]),
                "target_file_best": page_map.get(page) if page else None,
                "confidence": 0.91 if page else 0.8,
                "raw_json": {"prefatory": True},
            }
        )
        if page:
            refs.append(
                make_ref(
                    entry_key=entry_key,
                    ref_order=1,
                    ref_raw=str(page),
                    page_map=page_map,
                    section_start_file=str(files[0]["path"]),
                    editorial_anchor_file=str(files[0]["path"]),
                )
            )
            if page_map.get(page) is None:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma or raw[:120],
                        "query_names": [lemma or raw[:120]],
                        "page_hints": [str(page)],
                        "page_hint_ints": [page],
                        "context_raw": raw,
                    }
                )
    for number, body in numbered:
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:ordo:{entry_order:04d}"
        page_match = re.search(r"(\d{1,4})$", body)
        page = int(page_match.group(1)) if page_match else None
        entry_raw = f"{number}. {body}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": main_node,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": number,
                "lemma_display": number,
                "lemma_norm": number,
                "lemma_sort": number.zfill(4),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page,
                "section_start_file": str(files[0]["path"]),
                "editorial_anchor_file": str(files[0]["path"]),
                "target_file_best": page_map.get(page) if page else None,
                "confidence": 0.94 if page else 0.72,
                "raw_json": {"printed_item_number": number},
            }
        )
        if page:
            refs.append(
                make_ref(
                    entry_key=entry_key,
                    ref_order=1,
                    ref_raw=str(page),
                    page_map=page_map,
                    section_start_file=str(files[0]["path"]),
                    editorial_anchor_file=str(files[0]["path"]),
                )
            )
            if page_map.get(page) is None or "-" in number:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": number,
                        "query_names": [number, entry_raw[:100]],
                        "page_hints": [str(page)],
                        "page_hint_ints": [page],
                        "context_raw": entry_raw,
                    }
                )
    return entries, refs, nodes, helper_entries


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
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        if not isinstance(item, dict) or not item.get("entry_id"):
            continue
        mapped[item["entry_id"]] = {
            "status": item.get("status"),
            "candidate_role": item.get("candidate_role"),
            "reason_summary": item.get("reason_summary"),
            "best_candidate": item.get("best_candidate"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in item.get("candidates", [])[:5]
                if isinstance(cand, dict)
            ],
        }
    return mapped


def attach_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_entry = helper_map(helper_output)
    for entry in entries:
        helper_hint = by_entry.get(entry["entry_key"])
        if not helper_hint:
            continue
        entry.setdefault("raw_json", {})["helper"] = helper_hint
        best = helper_hint.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
    for ref in refs:
        helper_hint = by_entry.get(ref["entry_key"])
        if not helper_hint:
            continue
        ref.setdefault("raw_json", {})["helper"] = helper_hint
        best = helper_hint.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")


def section_record(
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    page_start: int,
    page_end: int,
    file_start: str,
    file_end: str,
    section_kind_reason: str,
) -> dict[str, Any]:
    return {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": normalize_sort(heading_raw),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": file_start,
        "file_end": file_end,
        "confidence": 0.98,
        "raw_json": {"section_kind_reason": section_kind_reason},
    }


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> dict[str, Any]:
    all_files = [parse_file(source_root, seq) for seq in discover_sequences(source_root)]
    page_map = build_page_map(all_files)
    analytic_entries, analytic_refs, analytic_nodes, analytic_helper = build_analytic(source_root, page_map)
    ordo_entries, ordo_refs, ordo_nodes, ordo_helper = build_ordo(source_root, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": analytic_helper + ordo_helper,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs
    nodes = analytic_nodes + ordo_nodes
    attach_helper(entries, refs, helper_output)

    sections = [
        section_record(
            section_key=INDEX_SECTION_KEY,
            section_order=1,
            section_kind="analytic_subject",
            heading_raw=INDEX_HEADING_RAW,
            page_start=1397,
            page_end=1434,
            file_start=str(file_path(source_root, 698)),
            file_end=str(file_path(source_root, 716)),
            section_kind_reason="Alphabetical analytical index for Laonicus Chalcocondyles occupying the OCR tail until the closing ORDO RERUM begins.",
        ),
        section_record(
            section_key=ORDO_SECTION_KEY,
            section_order=2,
            section_kind="ordo_rerum",
            heading_raw=ORDO_HEADING_RAW,
            page_start=1434,
            page_end=1440,
            file_start=str(file_path(source_root, 716)),
            file_end=str(file_path(source_root, 719)),
            section_kind_reason="Closing contents table for the tome, beginning midway through file 716 and continuing through file 719 before the next work starts.",
        ),
    ]

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Recovered the analytical index and the closing ORDO RERUM from the OCR tail with helper-backed target candidates.",
        "evidence_files": [str(file_path(source_root, seq)) for seq in range(698, 720)],
    }
    notes = [
        "File 716 contains both the end of the INDEX ANALYTICUS and the start of the ORDO RERUM; the split was taken at the first 'LAONICUS CHALCOCONDYLA' heading.",
        "The ORDO RERUM was truncated before 'LEONARDUS CHIENSIS MITTYLÆUS ARCHIEPISCOPUS.' because that heading starts the next editorial unit beyond the contents table.",
        "The analytical index body is OCR-collapsed into long lines; entries were conservatively segmented by explicit trailing page locators.",
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Built from the OCR tail files 698-719 only.",
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
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "entry_count": len(entries),
            "ref_count": len(refs),
            "section_count": len(sections),
        },
    )
    write_json(output_file, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PG159 alphabetical-index payload.")
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

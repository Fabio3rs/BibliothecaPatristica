#!/usr/bin/env python3
"""Usage: build the PG160 ORDO RERUM alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg160_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG160/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG160_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG160_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG160 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG160_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG160"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Vol. CLX"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM. QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_KIND_REASON = (
    "Closing ORDO RERUM contents table for the volume tail; the material is an editorial "
    "contents list with hierarchical rubrics and page-bearing line items, not an alphabetical lemma index."
)
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
HEADER_LINE_RE = re.compile(
    r"^(?:\d{3,4}\s+)?ORDO RERUM(?:\s+QU(?:AE|Æ|E)\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?(?:\s+\d{3,4})?$",
    re.IGNORECASE,
)
PAGE_RE = re.compile(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})(?:[)\].,'’]*)?$")
WS_RE = re.compile(r"\s+")
MANUAL_TARGET_OVERRIDES = {
    f"{VOLUME_ID}:entry:001:0040": {
        "target_file": "/homessddata/Projects/pdfocr/teste/PG160/text/40ed618c-5a08-452b-a67e-bec91ad34799-753.txt",
        "target_file_probability": 0.9999,
        "status": "resolved_manual",
        "reason": (
            "Direct OCR inspection of neighboring files 751-754 confirms that file 753 carries the printed header "
            "`1129 DE PRAEDESTINATIONE LIBER III. 1130` and therefore matches the cited page 1129 for "
            "`De divina providentia liber tertius`."
        ),
        "verification": {
            "searched_phrase": "De divina providentia liber tertius",
            "neighbor_files_checked": [
                "/homessddata/Projects/pdfocr/teste/PG160/text/40ed618c-5a08-452b-a67e-bec91ad34799-751.txt",
                "/homessddata/Projects/pdfocr/teste/PG160/text/40ed618c-5a08-452b-a67e-bec91ad34799-752.txt",
                "/homessddata/Projects/pdfocr/teste/PG160/text/40ed618c-5a08-452b-a67e-bec91ad34799-753.txt",
                "/homessddata/Projects/pdfocr/teste/PG160/text/40ed618c-5a08-452b-a67e-bec91ad34799-754.txt",
            ],
        },
    }
}

NODE_SPECS = [
    {"key": "greg_mamma", "label": "GREGORIUS MAMMA CPOLITANUS PATRIARCHA.", "file_seq": 831, "start": 3, "end": 3, "parent": None, "kind": "heading_group", "level": 1},
    {"key": "greg_mamma_scripta", "label": "GREGORII MAMMÆ SCRIPTA.", "file_seq": 831, "start": 6, "end": 6, "parent": "greg_mamma", "kind": "rubric_group", "level": 2},
    {"key": "gennadius", "label": "GENNADIUS SEU GEORGIUS SCHOLARIUS CPOLITANUS PATRIARCHA.", "file_seq": 831, "start": 14, "end": 14, "parent": None, "kind": "heading_group", "level": 1},
    {"key": "gennadii_opera", "label": "GENNADII OPERA.", "file_seq": 831, "start": 17, "end": 17, "parent": "gennadius", "kind": "rubric_group", "level": 2},
    {"key": "gennadii_addenda", "label": "ADDENDA.", "file_seq": 831, "start": 108, "end": 108, "parent": "gennadii_opera", "kind": "rubric_group", "level": 3},
    {"key": "pletho", "label": "GEORGIUS GEMISTUS PLETHO.", "file_seq": 832, "start": 4, "end": 4, "parent": None, "kind": "heading_group", "level": 1},
    {"key": "plethonis_scripta", "label": "PLETHONIS SCRIPTA.", "file_seq": 832, "start": 9, "end": 9, "parent": "pletho", "kind": "rubric_group", "level": 2},
    {"key": "camariota", "label": "MATTHÆUS CAMARIOTA.", "file_seq": 832, "start": 27, "end": 27, "parent": None, "kind": "heading_group", "level": 1},
    {"key": "marcus", "label": "MARCUS EUGENICUS, EPHESIUS METROPOLITA.", "file_seq": 832, "start": 30, "end": 30, "parent": None, "kind": "heading_group", "level": 1},
    {"key": "marcus_scripta", "label": "SCRIPTA.", "file_seq": 832, "start": 32, "end": 32, "parent": "marcus", "kind": "rubric_group", "level": 2},
    {"key": "marcus_addenda", "label": "ADDENDA.", "file_seq": 832, "start": 44, "end": 44, "parent": "marcus_scripta", "kind": "rubric_group", "level": 3},
]

ENTRY_SPECS = [
    {"key": "0001", "file_seq": 831, "start": 4, "end": 5, "node": "greg_mamma", "page": 9},
    {"key": "0002", "file_seq": 831, "start": 7, "end": 9, "node": "greg_mamma_scripta", "page": 15},
    {"key": "0003", "file_seq": 831, "start": 10, "end": 11, "node": "greg_mamma_scripta", "page": 111},
    {"key": "0004", "file_seq": 831, "start": 12, "end": 13, "node": "greg_mamma_scripta", "page": 205},
    {"key": "0005", "file_seq": 831, "start": 15, "end": 15, "node": "gennadius", "page": 249},
    {"key": "0006", "file_seq": 831, "start": 16, "end": 16, "node": "gennadius", "page": 311},
    {"key": "0007", "file_seq": 831, "start": 18, "end": 21, "node": "gennadii_opera", "page": 319},
    {"key": "0008", "file_seq": 831, "start": 22, "end": 25, "node": "gennadii_opera", "page": 353},
    {"key": "0009", "file_seq": 831, "start": 26, "end": 27, "node": "gennadii_opera", "page": 351},
    {"key": "0010", "file_seq": 831, "start": 28, "end": 30, "node": "gennadii_opera", "page": 375},
    {"key": "0011", "file_seq": 831, "start": 37, "end": 37, "node": "gennadii_opera", "page": 381, "context": {"file_seq": 831, "start": 31, "end": 36}},
    {"key": "0012", "file_seq": 831, "start": 38, "end": 39, "node": "gennadii_opera", "page": 381},
    {"key": "0013", "file_seq": 831, "start": 40, "end": 41, "node": "gennadii_opera", "page": 385},
    {"key": "0014", "file_seq": 831, "start": 42, "end": 43, "node": "gennadii_opera", "page": 405},
    {"key": "0015", "file_seq": 831, "start": 44, "end": 44, "node": "gennadii_opera", "page": 439},
    {"key": "0016", "file_seq": 831, "start": 45, "end": 46, "node": "gennadii_opera", "page": 475},
    {"key": "0017", "file_seq": 831, "start": 47, "end": 49, "node": "gennadii_opera", "page": 523},
    {"key": "0018", "file_seq": 831, "start": 50, "end": 50, "node": "gennadii_opera", "page": 529},
    {"key": "0019", "file_seq": 831, "start": 51, "end": 51, "node": "gennadii_opera", "page": 529},
    {"key": "0020", "file_seq": 831, "start": 52, "end": 52, "node": "gennadii_opera", "page": 533},
    {"key": "0021", "file_seq": 831, "start": 53, "end": 53, "node": "gennadii_opera", "page": 535},
    {"key": "0022", "file_seq": 831, "start": 54, "end": 56, "node": "gennadii_opera", "page": 537},
    {"key": "0023", "file_seq": 831, "start": 57, "end": 59, "node": "gennadii_opera", "page": 539},
    {"key": "0024", "file_seq": 831, "start": 60, "end": 61, "node": "gennadii_opera", "page": 567},
    {"key": "0025", "file_seq": 831, "start": 62, "end": 63, "node": "gennadii_opera", "page": 597},
    {"key": "0026", "file_seq": 831, "start": 64, "end": 66, "node": "gennadii_opera", "page": 631},
    {"key": "0027", "file_seq": 831, "start": 67, "end": 69, "node": "gennadii_opera", "page": 649},
    {"key": "0028", "file_seq": 831, "start": 70, "end": 72, "node": "gennadii_opera", "page": 649},
    {"key": "0029", "file_seq": 831, "start": 73, "end": 76, "node": "gennadii_opera", "page": 663},
    {"key": "0030", "file_seq": 831, "start": 77, "end": 77, "node": "gennadii_opera", "page": 713},
    {"key": "0031", "file_seq": 831, "start": 78, "end": 78, "node": "gennadii_opera", "page": 731},
    {"key": "0032", "file_seq": 831, "start": 79, "end": 81, "node": "gennadii_opera", "page": 737},
    {"key": "0033", "file_seq": 831, "start": 82, "end": 84, "node": "gennadii_opera", "page": 743},
    {"key": "0034", "file_seq": 831, "start": 85, "end": 85, "node": "gennadii_opera", "page": 745},
    {"key": "0035", "file_seq": 831, "start": 103, "end": 105, "node": "gennadii_opera", "page": 747, "context": {"file_seq": 831, "start": 86, "end": 102}},
    {"key": "0036", "file_seq": 831, "start": 106, "end": 107, "node": "gennadii_opera", "page": 767},
    {"key": "0037", "file_seq": 831, "start": 109, "end": 111, "node": "gennadii_addenda", "page": 1105},
    {"key": "0038", "file_seq": 831, "start": 112, "end": 112, "node": "gennadii_addenda", "page": 1105},
    {"key": "0039", "file_seq": 831, "start": 113, "end": 114, "node": "gennadii_addenda", "page": 1123},
    {"key": "0040", "file_seq": 831, "start": 115, "end": 116, "node": "gennadii_addenda", "page": 1129},
    {"key": "0041", "file_seq": 831, "start": 117, "end": 117, "node": "gennadii_addenda", "page": 1137},
    {"key": "0042", "file_seq": 831, "start": 118, "end": 118, "node": "gennadii_addenda", "page": 1149},
    {"key": "0043", "file_seq": 831, "start": 119, "end": 119, "node": "gennadii_addenda", "page": 1157, "context": {"file_seq": 831, "start": 120, "end": 130}},
    {"key": "0044", "file_seq": 832, "start": 3, "end": 3, "node": "gennadii_addenda", "page": 1211, "context": {"file_seq": 832, "start": 2, "end": 2}},
    {"key": "0045", "file_seq": 832, "start": 5, "end": 5, "node": "pletho", "page": 775},
    {"key": "0046", "file_seq": 832, "start": 6, "end": 6, "node": "pletho", "page": 795},
    {"key": "0047", "file_seq": 832, "start": 7, "end": 7, "node": "pletho", "page": 805},
    {"key": "0048", "file_seq": 832, "start": 8, "end": 8, "node": "pletho", "page": 813},
    {"key": "0049", "file_seq": 832, "start": 10, "end": 10, "node": "plethonis_scripta", "page": 821},
    {"key": "0050", "file_seq": 832, "start": 11, "end": 11, "node": "plethonis_scripta", "page": 841},
    {"key": "0051", "file_seq": 832, "start": 12, "end": 12, "node": "plethonis_scripta", "page": 865},
    {"key": "0052", "file_seq": 832, "start": 13, "end": 13, "node": "plethonis_scripta", "page": 885},
    {"key": "0053", "file_seq": 832, "start": 14, "end": 14, "node": "plethonis_scripta", "page": 889},
    {"key": "0054", "file_seq": 832, "start": 15, "end": 15, "node": "plethonis_scripta", "page": 939},
    {"key": "0055", "file_seq": 832, "start": 16, "end": 16, "node": "plethonis_scripta", "page": 951},
    {"key": "0056", "file_seq": 832, "start": 17, "end": 17, "node": "plethonis_scripta", "page": 955},
    {"key": "0057", "file_seq": 832, "start": 18, "end": 18, "node": "plethonis_scripta", "page": 955},
    {"key": "0058", "file_seq": 832, "start": 19, "end": 19, "node": "plethonis_scripta", "page": 959},
    {"key": "0059", "file_seq": 832, "start": 20, "end": 20, "node": "plethonis_scripta", "page": 961},
    {"key": "0060", "file_seq": 832, "start": 21, "end": 21, "node": "plethonis_scripta", "page": 965},
    {"key": "0061", "file_seq": 832, "start": 22, "end": 22, "node": "plethonis_scripta", "page": 965},
    {"key": "0062", "file_seq": 832, "start": 23, "end": 23, "node": "plethonis_scripta", "page": 967},
    {"key": "0063", "file_seq": 832, "start": 24, "end": 24, "node": "plethonis_scripta", "page": 975},
    {"key": "0064", "file_seq": 832, "start": 25, "end": 25, "node": "plethonis_scripta", "page": 975},
    {"key": "0065", "file_seq": 832, "start": 26, "end": 26, "node": "plethonis_scripta", "page": 979},
    {"key": "0066", "file_seq": 832, "start": 28, "end": 28, "node": "camariota", "page": 1019},
    {"key": "0067", "file_seq": 832, "start": 29, "end": 29, "node": "camariota", "page": 1059},
    {"key": "0068", "file_seq": 832, "start": 31, "end": 31, "node": "marcus", "page": 1071},
    {"key": "0069", "file_seq": 832, "start": 33, "end": 33, "node": "marcus_scripta", "page": 1079},
    {"key": "0070", "file_seq": 832, "start": 34, "end": 34, "node": "marcus_scripta", "page": 1091},
    {"key": "0071", "file_seq": 832, "start": 35, "end": 35, "node": "marcus_scripta", "page": 1091},
    {"key": "0072", "file_seq": 832, "start": 36, "end": 36, "node": "marcus_scripta", "page": 1091},
    {"key": "0073", "file_seq": 832, "start": 37, "end": 37, "node": "marcus_scripta", "page": 1091},
    {"key": "0074", "file_seq": 832, "start": 38, "end": 38, "node": "marcus_scripta", "page": 1096},
    {"key": "0075", "file_seq": 832, "start": 39, "end": 39, "node": "marcus_scripta", "page": 1099},
    {"key": "0076", "file_seq": 832, "start": 40, "end": 40, "node": "marcus_scripta", "page": 1099},
    {"key": "0077", "file_seq": 832, "start": 41, "end": 41, "node": "marcus_scripta", "page": 1105},
    {"key": "0078", "file_seq": 832, "start": 42, "end": 42, "node": "marcus_scripta", "page": 1165},
    {"key": "0079", "file_seq": 832, "start": 43, "end": 43, "node": "marcus_scripta", "page": 1195},
    {"key": "0080", "file_seq": 832, "start": 45, "end": 45, "node": "marcus_addenda", "page": 1201},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def fold_for_sort(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
        .replace("Τ", "T")
        .replace("ό", "o")
    )
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", raw, flags=re.S | re.I):
        attrs = block.group("attrs") or ""
        kind_match = re.search(r'tipo="([^"]+)"', attrs, flags=re.I)
        kind = (kind_match.group(1).strip().lower() if kind_match else "")
        if kind not in {"cabecalho", "texto_principal", "outro"}:
            continue
        body = re.sub(r"<[^>]+>", " ", block.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def load_volume_lines(source_root: Path) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for seq in (831, 832):
        path = source_root / f"cea716bc-fa0e-4cdf-bbc4-ccf0ddfdf444-{seq}.txt"
        result[seq] = extract_lines(path)
    return result


def join_line_range(lines_by_seq: dict[int, list[str]], file_seq: int, start: int, end: int) -> str:
    source = lines_by_seq[file_seq]
    parts = source[start - 1 : end]
    merged = ""
    for part in parts:
        part = normalize(part)
        if not part:
            continue
        if not merged:
            merged = part
            continue
        if merged.endswith("-"):
            merged = merged[:-1] + part
        else:
            merged = f"{merged} {part}"
    return normalize(merged)


def strip_page(entry_text: str) -> tuple[str, str | None, int | None]:
    match = PAGE_RE.match(normalize(entry_text))
    if not match:
        return normalize(entry_text), None, None
    lemma = normalize(match.group("lemma")).rstrip(" ,;:.")
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def build_query_names(lemma_raw: str) -> list[str]:
    base = normalize(lemma_raw)
    candidates = [base]
    first_clause = normalize(re.split(r"[.;(]", base, maxsplit=1)[0])
    if first_clause and first_clause != base:
        candidates.append(first_clause)
    if base.startswith("— "):
        stripped = normalize(base[2:])
        if stripped:
            candidates.append(stripped)
    short = normalize(" ".join(base.split()[:8]))
    if short and short not in candidates:
        candidates.append(short)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = normalize(item)
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:5]


def helper_compact(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    compact_candidates: list[dict[str, Any]] = []
    for candidate in (helper_entry.get("candidates") or [])[:3]:
        compact_candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in candidate.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            }
        )
    return {
        "status": helper_entry.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary") or helper_entry.get("debug"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper_entry.get("candidates") or []),
        "candidates": compact_candidates,
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    lines_by_seq = load_volume_lines(source_root)
    files = [
        (source_root / "cea716bc-fa0e-4cdf-bbc4-ccf0ddfdf444-831.txt").as_posix(),
        (source_root / "cea716bc-fa0e-4cdf-bbc4-ccf0ddfdf444-832.txt").as_posix(),
    ]
    section_start_file = files[0]
    section_end_file = files[-1]

    node_key_map = {spec["key"]: f"{VOLUME_ID}:node:{i:03d}" for i, spec in enumerate(NODE_SPECS, start=1)}
    nodes: list[dict[str, Any]] = []
    for idx, spec in enumerate(NODE_SPECS, start=1):
        label_raw = join_line_range(lines_by_seq, spec["file_seq"], spec["start"], spec["end"])
        nodes.append(
            {
                "node_key": node_key_map[spec["key"]],
                "section_key": SECTION_KEY,
                "parent_node_key": node_key_map[spec["parent"]] if spec["parent"] else None,
                "node_order": idx,
                "node_kind": spec["kind"],
                "label_raw": label_raw,
                "label_norm": fold_for_sort(label_raw),
                "label_sort": fold_for_sort(label_raw),
                "node_level": spec["level"],
                "confidence": 0.98,
                "raw_json": {
                    "source_file": (source_root / f"cea716bc-fa0e-4cdf-bbc4-ccf0ddfdf444-{spec['file_seq']}.txt").as_posix(),
                    "source_lines": [spec["start"], spec["end"]],
                    "section_kind": "ordo_rerum",
                },
            }
        )

    helper_entries: list[dict[str, Any]] = []
    entry_work: list[dict[str, Any]] = []
    for order, spec in enumerate(ENTRY_SPECS, start=1):
        entry_key = f"{VOLUME_ID}:entry:001:{spec['key']}"
        source_file = (source_root / f"cea716bc-fa0e-4cdf-bbc4-ccf0ddfdf444-{spec['file_seq']}.txt").as_posix()
        entry_raw = join_line_range(lines_by_seq, spec["file_seq"], spec["start"], spec["end"])
        lemma_raw, page_raw, page_int = strip_page(entry_raw)
        context_raw = None
        if spec.get("context"):
            ctx = spec["context"]
            context_raw = join_line_range(lines_by_seq, ctx["file_seq"], ctx["start"], ctx["end"])
        helper_entries.append(
            {
                "entry_id": entry_key,
                "lemma_raw": lemma_raw,
                "query_names": build_query_names(lemma_raw),
                "page_hints": [page_raw] if page_raw else [],
                "page_hint_ints": [page_int] if page_int is not None else [],
                "context_raw": context_raw,
            }
        )
        entry_work.append(
            {
                "entry_key": entry_key,
                "parent_node_key": node_key_map[spec["node"]],
                "entry_order": order,
                "entry_raw": entry_raw,
                "lemma_raw": lemma_raw,
                "page_raw": page_raw,
                "page_int": page_int,
                "source_file": source_file,
                "source_lines": list(range(spec["start"], spec["end"] + 1)),
                "context_raw": context_raw,
            }
        )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    subprocess.run(
        [
            "python",
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
        cwd=ROOT,
    )
    helper_output = json.loads(helper_output_json.read_text(encoding="utf-8"))
    helper_map = {item["entry_id"]: item for item in helper_output.get("entries", [])}

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_status_counts: dict[str, int] = {}
    for item in entry_work:
        helper_entry = helper_map.get(item["entry_key"])
        helper_status = (helper_entry or {}).get("status", "missing")
        best = (helper_entry or {}).get("best_candidate") or {}
        best_file = best.get("file")
        best_prob = best.get("probability")
        manual_override = MANUAL_TARGET_OVERRIDES.get(item["entry_key"])
        if manual_override:
            helper_status = manual_override["status"]
            best_file = manual_override["target_file"]
            best_prob = manual_override["target_file_probability"]
        helper_status_counts[helper_status] = helper_status_counts.get(helper_status, 0) + 1
        entry_confidence = 0.93 if helper_status == "resolved" else 0.82
        if helper_status == "ambiguous":
            entry_confidence = 0.76
        if helper_status == "resolved_manual":
            entry_confidence = 0.95
        compact = helper_compact(helper_entry)

        entries.append(
            {
                "entry_key": item["entry_key"],
                "section_key": SECTION_KEY,
                "parent_node_key": item["parent_node_key"],
                "entry_order": item["entry_order"],
                "entry_kind": "heading_group",
                "lemma_raw": item["lemma_raw"],
                "lemma_display": item["lemma_raw"],
                "lemma_norm": fold_for_sort(item["lemma_raw"]),
                "lemma_sort": fold_for_sort(item["lemma_raw"]),
                "entry_raw": item["entry_raw"],
                "context_raw": item["context_raw"],
                "heading_letter": None,
                "inferred_printed_page": item["page_int"],
                "section_start_file": section_start_file,
                "editorial_anchor_file": item["source_file"],
                "target_file_best": best_file,
                "confidence": entry_confidence,
                "raw_json": {
                    "source_file": item["source_file"],
                    "source_lines": item["source_lines"],
                    "line_count": len(item["source_lines"]),
                    "section_kind": "ordo_rerum",
                    "helper": compact,
                    "manual_target_override": manual_override,
                },
            }
        )

        refs.append(
            {
                "entry_key": item["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": item["page_raw"],
                "page_ref_raw": item["page_raw"],
                "page_ref_int": item["page_int"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": best_file,
                "target_file_probability": best_prob,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item["source_file"],
                "confidence": 0.95 if helper_status == "resolved_manual" else (0.91 if helper_status == "resolved" else 0.74),
                "raw_json": {
                    "locator_status": helper_status,
                    "helper": compact,
                    "manual_target_override": manual_override,
                },
            }
        )

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1215,
            "page_end": 1216,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "source_root": source_root.as_posix(),
                "evidence_files": files,
                "ignored_lines": [
                    {
                        "file": files[1],
                        "line": 2,
                        "text": join_line_range(lines_by_seq, 832, 2, 2),
                        "reason": "Continuation of the parenthetical editorial note from the previous page, not a standalone ORDO entry.",
                    }
                ],
            },
        }
    ]
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from OCR files 831-832 and serialized all page-bearing line items with helper-backed target candidates.",
        "evidence_files": files,
    }
    notes = [
        "The section is editorial contents material (`ordo_rerum`), not an alphabetical subject index.",
        "OCR file 832 line 2 was excluded because it only continues the long parenthetical note that begins on file 831 line 120.",
        f"Helper status summary: {helper_status_counts}.",
    ]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
            "notes": "Closing ORDO RERUM contents table for late Byzantine authors and addenda in PG160.",
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
    write_json(intermediate_dir / "volume.json", payload["volume"])
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
            "section_count": len(sections),
            "node_count": len(nodes),
            "entry_count": len(entries),
            "ref_count": len(refs),
            "helper_request_json": helper_request_json.as_posix(),
            "helper_output_json": helper_output_json.as_posix(),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Completed payload assembly and helper-backed locator pass for PG160.",
            "completed": [
                "Confirmed the useful tail section is the two-page ORDO RERUM in files 831-832.",
                "Segmented all page-bearing ORDO entries and structural headings.",
                "Wrote helper request, helper output, intermediates, and final payload.",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "File 832 line 2 is note continuation only and was intentionally excluded from entries.",
                "Long parenthetical notes were preserved as short context snippets only where they materially disambiguate adjacent entries.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG160 alphabetical-index payload for the closing ORDO RERUM section.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

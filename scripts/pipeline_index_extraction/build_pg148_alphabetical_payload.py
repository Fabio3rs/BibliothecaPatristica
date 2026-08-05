#!/usr/bin/env python3
"""Usage: build the PG148 alphabetical/ordo payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg148_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG148/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG148_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG148_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG148 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG148_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


ROOT = Path("/homessddata/Projects/pdfocr")
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
VOLUME_ID = "PG148"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 148"
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2

ANALYTIC_SEQ_START = 733
ANALYTIC_SEQ_END = 756
ORDO_SEQ_START = 757
ORDO_SEQ_END = 770

ANALYTIC_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

TEXT_BLOCK_RE = re.compile(r'<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>', re.S)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
PAGE_REF_RE = re.compile(r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\b")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
GREEK_LETTER_RE = re.compile(r"^[Α-Ω]$")
MACRO_HEADING_RE = re.compile(r"^[A-ZÆŒΑ-Ω][A-ZÆŒΑ-Ω\s\.\-()']{0,40}:?$")
STRUCTURAL_ORDO_RE = re.compile(r"^LIBER\s+[A-Z]+\.?$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def parse_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, list[str]]] = []
    try:
        root = ET.fromstring(raw.strip())
        for bloco in root.findall("bloco"):
            kind = (bloco.attrib.get("tipo") or "").strip().lower()
            content = normalize("".join(bloco.itertext()))
            lines = [normalize(line) for line in content.splitlines() if normalize(line)]
            if lines:
                blocks.append((kind, lines))
        return blocks
    except ET.ParseError:
        for match in TEXT_BLOCK_RE.finditer(raw):
            kind = (match.group(1) or "").strip().lower()
            body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
            content = normalize(body)
            lines = [normalize(line) for line in content.splitlines() if normalize(line)]
            if lines:
                blocks.append((kind, lines))
        return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw.strip())
        except ET.ParseError:
            continue
        for bloco in root.findall("bloco"):
            if (bloco.attrib.get("tipo") or "").strip().lower() != "cabecalho":
                continue
            header = normalize("".join(bloco.itertext()))
            if not header:
                continue
            nums = [int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header)]
            for num in nums:
                page_map.setdefault(num, str(path))
    return page_map


def has_page_ref(text: str) -> bool:
    return bool(PAGE_REF_RE.search(text))


def extract_page_hints(entry_raw: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if not raw:
            continue
        token = re.split(r"\s*[-–—]\s*", raw, maxsplit=1)[0].strip()
        if not token.isdigit():
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def extract_lemma_raw(entry_raw: str) -> str | None:
    value = normalize(entry_raw)
    if not value:
        return None
    cut = len(value)
    page_match = PAGE_REF_RE.search(value)
    if page_match:
        cut = min(cut, page_match.start())
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bvoir\b", r"\bv\.\b", r"\bcf\.\b", r"\bid\.\b"]:
        m = re.search(pattern, value, flags=re.IGNORECASE)
        if m:
            cut = min(cut, m.start())
    lemma = value[:cut].strip(" ,;:.")
    return lemma or None


def entry_kind_for(entry_raw: str) -> str:
    value = normalize(entry_raw)
    if not value:
        return "lemma"
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", value, flags=re.IGNORECASE):
        return "cross_reference"
    if STRUCTURAL_ORDO_RE.fullmatch(value):
        return "heading_group"
    return "lemma"


def first_letter(text: str | None) -> str | None:
    if not text:
        return None
    for ch in normalize(text):
        if ch.isalpha():
            return ch.upper()
    return None


def helper_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    candidates: list[str] = []
    for value in [lemma_raw, entry_raw]:
        if value:
            value = normalize(value)
            if value and value not in candidates:
                candidates.append(value)
    if lemma_raw:
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", normalize(lemma_raw)).strip()
        if stripped and stripped not in candidates:
            candidates.append(stripped)
    if entry_raw:
        first_clause = re.split(r"\s*[;,]\s*|\s{2,}", normalize(entry_raw), maxsplit=1)[0].strip()
        if first_clause and first_clause not in candidates:
            candidates.append(first_clause)
    return candidates[:4] or [lemma_raw or entry_raw]


def compact_helper_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    candidates = []
    for cand in (item.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in (cand.get("evidence") or [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            }
        )
    return {
        "status": item.get("status"),
        "candidate_role": item.get("candidate_role"),
        "reason_summary": item.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidates": candidates,
    }


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = extract_page_hints(entry["entry_raw"])
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:120],
                "query_names": helper_query_names(entry["lemma_raw"], entry["entry_raw"]),
                "page_hints": [str(v) for v in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry["context_raw"] or entry["entry_raw"][:240],
            }
        )
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
        },
        "entries": helper_entries,
    }
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_TARGET_LOCATOR),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(
            f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_best_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("results") or helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        best = item.get("best_candidate") or {}
        mapping[str(entry_id)] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": best,
            "candidates": item.get("candidates") or [],
        }
    return mapping


def classify_line(line: str) -> str:
    value = normalize(line)
    if not value:
        return "skip"
    if LETTER_RE.fullmatch(value) or GREEK_LETTER_RE.fullmatch(value):
        return "letter"
    if STRUCTURAL_ORDO_RE.fullmatch(value):
        return "structural"
    if value.endswith(":") and MACRO_HEADING_RE.fullmatch(value):
        return "macro"
    if value in {"Digitized by Google"}:
        return "skip"
    return "entry"


def build_section_entries(
    source_root: Path,
    files: list[Path],
    page_map: dict[int, str],
    seq_start: int,
    seq_end: int,
    section_key: str,
    section_kind: str,
    section_order: int,
    heading_raw: str,
    heading_norm: str,
    page_start: int,
    page_end: int,
    helper_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_files = [path for path in files if seq_start <= file_seq(path) <= seq_end]
    section_start_file = str(section_files[0]) if section_files else None
    section_end_file = str(section_files[-1]) if section_files else None

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_letter = None
    current_letter_node = None
    current_macro_node = None
    letter_order: dict[str, int] = {}
    macro_order = 0
    entry_counter = 0

    for path in section_files:
        pending_hyphen: str | None = None
        for block_kind, lines in parse_blocks(path):
            if block_kind not in {"texto_principal", "nota_marginal"}:
                continue
            for raw_line in lines:
                line = normalize(raw_line)
                if not line:
                    continue

                # Preserve only safe hyphenated wraps; otherwise keep conservative line boundaries.
                if pending_hyphen is not None:
                    line = f"{pending_hyphen}{line.lstrip()}"
                    pending_hyphen = None

                if line.endswith("-") and len(line) > 1:
                    pending_hyphen = line[:-1]
                    continue

                cls = classify_line(line)
                if cls == "skip":
                    continue
                if cls == "letter":
                    current_letter = line
                    current_macro_node = None
                    if current_letter not in letter_order:
                        letter_order[current_letter] = len(letter_order) + 1
                        current_letter_node = f"{section_key}:letter:{current_letter}"
                        nodes.append(
                            {
                                "node_key": current_letter_node,
                                "section_key": section_key,
                                "parent_node_key": None,
                                "node_order": letter_order[current_letter],
                                "node_kind": "letter_group",
                                "label_raw": current_letter,
                                "label_norm": current_letter.lower(),
                                "label_sort": current_letter.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {
                                    "source": "standalone_letter_heading",
                                    "file": str(path),
                                },
                            }
                        )
                    continue
                if cls == "structural":
                    if section_kind == "ordo_rerum" and not any(
                        node.get("label_raw") == line for node in nodes
                    ):
                        macro_order += 1
                        current_macro_node = f"{section_key}:heading:{macro_order:02d}"
                        nodes.append(
                            {
                                "node_key": current_macro_node,
                                "section_key": section_key,
                                "parent_node_key": current_letter_node,
                                "node_order": macro_order,
                                "node_kind": "heading_group",
                                "label_raw": line,
                                "label_norm": sort_norm(line),
                                "label_sort": sort_norm(line),
                                "node_level": 2 if current_letter_node else 1,
                                "confidence": 0.9,
                                "raw_json": {
                                    "source": "structural_heading",
                                    "file": str(path),
                                },
                            }
                        )
                    continue
                if cls == "macro":
                    macro_order += 1
                    current_macro_node = f"{section_key}:heading:{macro_order:02d}"
                    nodes.append(
                        {
                            "node_key": current_macro_node,
                            "section_key": section_key,
                            "parent_node_key": current_letter_node,
                            "node_order": macro_order,
                            "node_kind": "heading_group",
                            "label_raw": line,
                            "label_norm": sort_norm(line),
                            "label_sort": sort_norm(line),
                            "node_level": 2 if current_letter_node else 1,
                            "confidence": 0.95,
                            "raw_json": {
                                "source": "macro_heading",
                                "file": str(path),
                            },
                        }
                    )
                    continue

                entry_counter += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
                lemma_raw = extract_lemma_raw(line)
                entry_kind = entry_kind_for(line)
                page_hints = extract_page_hints(line)
                helper_info = helper_map.get(entry_key, {})
                target_file_best = None
                if helper_info.get("best_candidate", {}).get("file"):
                    target_file_best = helper_info["best_candidate"].get("file")
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": current_macro_node or current_letter_node,
                    "entry_order": entry_counter,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line if len(line) <= 220 else line[:220],
                    "heading_letter": current_letter if section_kind == "analytic_subject" else None,
                    "inferred_printed_page": page_hints[0] if page_hints else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_file_best,
                    "confidence": 0.76 if page_hints else 0.62,
                    "raw_json": {
                        "source_file": str(path),
                        "line_kind": block_kind,
                        "helper": compact_helper_item(helper_info),
                    },
                }
                if not entry["lemma_raw"]:
                    entry["lemma_raw"] = line
                    entry["lemma_display"] = line
                    entry["lemma_norm"] = sort_norm(line)
                    entry["lemma_sort"] = sort_norm(line)
                if not target_file_best and page_hints:
                    target_file_best = None
                entries.append(entry)

                seen_pages: set[int] = set()
                ref_order = 0
                for match in PAGE_REF_RE.finditer(line):
                    raw = match.group(0).strip()
                    if not raw:
                        continue
                    token = re.split(r"\s*[-–—]\s*", raw, maxsplit=1)[0].strip()
                    if not token.isdigit():
                        continue
                    page_int = int(token)
                    if page_int in seen_pages:
                        continue
                    seen_pages.add(page_int)
                    ref_order += 1
                    target_file = page_map.get(page_int)
                    if not target_file and helper_info.get("best_candidate", {}).get("file"):
                        target_file = helper_info["best_candidate"].get("file")
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": raw,
                            "page_ref_raw": raw,
                            "page_ref_int": page_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": 0.99 if target_file else None,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": str(path),
                            "confidence": 0.88 if target_file else 0.57,
                            "raw_json": {
                                "page_token_kind": "page",
                            },
                        }
                    )

    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": heading_norm,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": section_start_file,
        "file_end": section_end_file,
        "confidence": 0.96 if section_kind == "analytic_subject" else 0.9,
        "raw_json": {
            "section_kind_reason": (
                "Analytical alphabetical index block at the volume tail."
                if section_kind == "analytic_subject"
                else "Editorial ordo rerum / contents block distinct from the alphabetical index."
            ),
            "section_file_seq_start": seq_start,
            "section_file_seq_end": seq_end,
        },
    }
    return section, nodes, entries, refs, scripture_refs


def build_payload(source_root: Path, helper_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    analytic_section, analytic_nodes, analytic_entries, analytic_refs, analytic_scripture = build_section_entries(
        source_root,
        files,
        page_map,
        ANALYTIC_SEQ_START,
        ANALYTIC_SEQ_END,
        ANALYTIC_SECTION_KEY,
        "analytic_subject",
        1,
        "INDEX ANALYTICUS.",
        "index analyticus",
        1454,
        1500,
        helper_map,
    )
    ordo_section, ordo_nodes, ordo_entries, ordo_refs, ordo_scripture = build_section_entries(
        source_root,
        files,
        page_map,
        ORDO_SEQ_START,
        ORDO_SEQ_END,
        ORDO_SECTION_KEY,
        "ordo_rerum",
        2,
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "ordo rerum quae in hoc tomo continentur",
        1501,
        1528,
        helper_map,
    )

    # Attach the page map to the line entries in a second pass so manual inspection can see the raw OCR file.
    def attach_target_best(entries: list[dict[str, Any]], refs: list[dict[str, Any]]) -> None:
        ref_map: dict[str, str | None] = {}
        for ref in refs:
            if ref.get("entry_key") not in ref_map:
                ref_map[ref["entry_key"]] = ref.get("target_file")
        for entry in entries:
            if not entry.get("target_file_best"):
                entry["target_file_best"] = ref_map.get(entry["entry_key"])

    attach_target_best(analytic_entries, analytic_refs)
    attach_target_best(ordo_entries, ordo_refs)

    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs
    scripture_refs = analytic_scripture + ordo_scripture
    nodes = analytic_nodes + ordo_nodes

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the PG148 analytical index from OCR files 733-756 and the trailing ORDO RERUM block from files 757-770, using conservative line-level segmentation and page-number anchoring."
        ),
        "evidence_files": [
            str(source_root / "79272189-68c8-4eb5-8750-68e3fa413902-733.txt"),
            str(source_root / "79272189-68c8-4eb5-8750-68e3fa413902-740.txt"),
            str(source_root / "79272189-68c8-4eb5-8750-68e3fa413902-756.txt"),
            str(source_root / "79272189-68c8-4eb5-8750-68e3fa413902-757.txt"),
            str(source_root / "79272189-68c8-4eb5-8750-68e3fa413902-770.txt"),
        ],
    }

    notes = [
        "Used conservative one-line-per-entry segmentation; only hyphenated wraps were merged.",
        "The alphabetical tail ends at file 756; the trailing ORDO RERUM content is stored as a separate ordo_rerum section.",
        "OCR literals were preserved; the helper was used only to support target-file selection where possible.",
    ]

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [analytic_section, ordo_section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PG148 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract PG148 analytical and ordo tail payload",
        "completed": [
            "confirmed index boundary at files 733-756",
            "confirmed ORDO RERUM block at files 757-770",
        ],
        "pending": [
            "build helper request from extracted page-hint entries",
            "run index_target_locator",
            "write final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR line fragments conservative; do not merge unrelated lines into oversized synthetic entries.",
        ],
    }
    (args.intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    provisional = build_payload(args.source_root, {})
    build_helper_request(provisional["entries"], args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_best_map(helper_output)
    payload = build_payload(args.source_root, helper_map)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

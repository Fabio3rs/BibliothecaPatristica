#!/usr/bin/env python3
"""Usage: build the PG140 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG140_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG140/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG140_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG140_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG140 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG140_alphabetical_indices.json
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

from tools.ocr_xml_utils import read_ocr_page


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG140"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 140"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:index_i_nicetam:001",
        "heading_raw": "INDEX I IN NICETAM CHONIATEM.",
        "heading_norm": "index i in nicetam choniatem",
        "section_kind": "analytic_subject",
        "section_kind_reason": (
            "Alphabetical index of subjects, persons, and remissions for Nicetas Choniates, "
            "preserving the printed 'INDEX I' heading."
        ),
        "start_marker": "INDEX I IN NICETAM CHONIATEM.",
        "end_marker": "INDEX SCRIPTORUM",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:index_scriptorum:001",
        "heading_raw": "INDEX SCRIPTORUM ET ALIORUM QUORUMDAM QUI IN NICETÆ CHONIATÆ HISTORIA BYZANTINA MEMORANTUR.",
        "heading_norm": "index scriptorum et aliorum quorumdam qui in nicetae choniatae historia byzantina memorantur",
        "section_kind": "author_index",
        "section_kind_reason": (
            "Index of writers and named persons explicitly labelled 'INDEX SCRIPTORUM ET ALIORUM QUORUMDAM'."
        ),
        "start_marker": "INDEX SCRIPTORUM",
        "end_marker": "INDEX ANALYTICUS",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:index_ii_nicetam:001",
        "heading_raw": "INDEX ANALYTICUS AD NICETÆ CHONIATÆ HISTORIAM BYZANTINAM.",
        "heading_norm": "index analyticus ad nicetae choniatae historiam byzantinam",
        "section_kind": "analytic_subject",
        "section_kind_reason": (
            "Analytical alphabetical index beginning at the printed 'INDEX ANALYTICUS' heading "
            "and continuing through the end of the Nicetas Choniates material."
        ),
        "start_marker": "INDEX ANALYTICUS",
        "end_marker": "INDEX IN GEORGIUM ACROPOLITAM",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:index_georgius:001",
        "heading_raw": "INDEX IN GEORGIUM ACROPOLITAM.",
        "heading_norm": "index in georgium acropolitam",
        "section_kind": "analytic_subject",
        "section_kind_reason": (
            "Alphabetical index for Georgius Acropolita, organised by letter groups and internal rubrics."
        ),
        "start_marker": "INDEX IN GEORGIUM ACROPOLITAM",
        "end_marker": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "section_kind": "ordo_rerum",
        "section_kind_reason": "Closing editorial contents table for the tome, distinct from the alphabetical indexes.",
        "start_marker": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "end_marker": None,
    },
]

HEADER_RE = re.compile(r"^\d{3,4}(?:\s+.*)?$")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADING_RE = re.compile(r"^[A-ZÆŒ][A-ZÆŒ0-9\s\.\(\)'\-/:,;]+:?$|^COMMENI\s*:$|^CARASILÆ\s*:$|^GABALÆ FRATRES\s*:$|^DUCÆ\s*:$|^LASCARES\s*:$|^MESOPOTAMIÆ\s*:$|^MUZALONES\s*:$", re.IGNORECASE)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?")
SPLIT_RE = re.compile(r"(?:(?<=\d\.)|(?<=ibid\.))\s+(?=[A-ZÆŒΑ-Ω(])", re.IGNORECASE)
TRAILING_HEADER_RE = re.compile(r"^\d{3,4}\s+(?:INDEX|ORDO)\b", re.IGNORECASE)
STRIP_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str:
    return STRIP_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def text_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def sort_norm(text: str | None) -> str | None:
    return text_norm(text)


def discover_files(source_root: Path) -> list[Path]:
    files: dict[int, list[Path]] = defaultdict(list)
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            continue
        files[int(m.group(1))].append(path)
    selected: list[Path] = []
    for seq in sorted(files):
        candidates = sorted(files[seq], key=lambda p: p.name)
        selected.append(candidates[0])
    return selected


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(m.group(1))


def extract_blocks(path: Path) -> list[dict[str, str]]:
    page = read_ocr_page(path)
    items: list[dict[str, str]] = []

    def maybe_merge_continuation(kind: str, line: str) -> bool:
        if kind != "body_text" or not items:
            return False
        prev = items[-1]
        if prev["kind"] != "body_text" or prev["file"] != str(path):
            return False
        if not re.search(r"(?<=[^\W_\d])-$", prev["text"], flags=re.UNICODE):
            return False
        if not re.match(r"^[a-zæœα-ω]", line):
            return False
        prev["text"] = prev["text"][:-1] + line
        return True

    for block in page.blocks:
        raw = normalize(block.content_clean)
        if not raw or raw == "Digitized by Google":
            continue
        kind = "body_text"
        if block.tag_name == "cabecalho" or block.tipo == "cabecalho":
            kind = "header_text"
        elif block.tag_name == "rodape" or block.tipo == "rodape":
            kind = "footer_text"
        if kind == "body_text":
            for line in raw.splitlines():
                line = normalize(line)
                if not line or line == "Digitized by Google":
                    continue
                if maybe_merge_continuation(kind, line):
                    continue
                items.append({"kind": kind, "text": line, "file": str(path)})
        else:
            items.append({"kind": kind, "text": raw, "file": str(path)})
    return items


def header_pages(path: Path) -> list[int]:
    header = normalize(read_ocr_page(path).header_text or "")
    pages: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", header):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            pages.append(value)
    return pages


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for page in header_pages(path):
            page_map.setdefault(page, str(path))
    return page_map


def split_fragments(text: str) -> list[str]:
    text = normalize(text)
    if not text:
        return []
    text = re.sub(r"(?<=\d\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", text)
    text = re.sub(r"(?<=ibid\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", text, flags=re.IGNORECASE)
    return [part.strip() for part in text.splitlines() if part.strip()]


def is_heading_fragment(fragment: str) -> bool:
    return bool(LETTER_RE.fullmatch(fragment) or HEADING_RE.fullmatch(fragment))


def is_section_marker(fragment: str, markers: list[str]) -> bool:
    cleaned = normalize(fragment).upper().rstrip()
    return any(cleaned.startswith(marker.upper()) for marker in markers)


def section_for_fragment(fragment: str, current_section: dict[str, Any] | None) -> dict[str, Any] | None:
    if current_section is None:
        return None
    for section in SECTION_DEFS:
        if is_section_marker(fragment, [section["start_marker"]]):
            return section
    return current_section


def extract_refs(fragment: str, page_map: dict[int, str], section_key: str, source_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None, str]] = set()
    for match in PAGE_RE.finditer(fragment):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        ref_raw = normalize(match.group(0))
        key = (start, end, ref_raw)
        if key in seen:
            continue
        seen.add(key)
        target_file = page_map.get(start)
        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
                "target_file": target_file,
                "target_file_probability": 0.95 if target_file else None,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.93 if target_file else 0.65,
                "raw_json": {
                    "page_token_kind": "range" if end is not None else "page",
                    "section_key": section_key,
                },
            }
        )
    return refs


def extract_lemma(fragment: str) -> str | None:
    text = normalize(fragment)
    if not text:
        return None
    cut = len(text)
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bvoir\b", r"\bv\.\b", r"\bcf\.\b", r"\bid\.\b"]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            cut = min(cut, m.start())
    m = PAGE_RE.search(text)
    if m:
        cut = min(cut, m.start())
    lemma = text[:cut].strip(" ,;:.")
    return lemma or None


def entry_kind_for(fragment: str) -> str:
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", fragment, flags=re.IGNORECASE):
        return "cross_reference"
    lemma = extract_lemma(fragment)
    if not lemma:
        return "editorial_note"
    if lemma.endswith(":") or lemma.endswith("."):
        return "heading_group"
    return "lemma"


def compact_helper_entry(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    candidates: list[dict[str, Any]] = []
    for cand in (item.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict) and ev.get("kind")
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
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_result_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("results") or helper_output.get("entries") or []:
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        mapping[str(entry_id)] = {
            "status": item.get("status"),
            "candidate_role": item.get("candidate_role"),
            "reason_summary": item.get("reason_summary"),
            "best_candidate": item.get("best_candidate") or {},
            "candidates": item.get("candidates") or [],
        }
    return mapping


def find_heading(lines: list[dict[str, str]], start_marker: str) -> tuple[int, dict[str, str]] | None:
    for idx, item in enumerate(lines):
        if start_marker.upper() in normalize(item["text"]).upper():
            return idx, item
    return None


def section_window(lines: list[dict[str, str]], start_marker: str, end_marker: str | None) -> tuple[int, int]:
    start = None
    end = len(lines)
    for idx, item in enumerate(lines):
        text = normalize(item["text"])
        if start is None and start_marker.upper() in text.upper():
            start = idx
            continue
        if start is not None and end_marker and end_marker.upper() in text.upper():
            end = idx
            break
    if start is None:
        raise SystemExit(f"Could not find section marker: {start_marker}")
    return start, end


def build_payload(source_root: Path, helper_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = discover_files(source_root)
    lines: list[dict[str, str]] = []
    for path in files:
        lines.extend(extract_blocks(path))

    page_map = build_page_map(files)

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    for order, section_def in enumerate(SECTION_DEFS, start=1):
        start_idx, end_idx = section_window(lines, section_def["start_marker"], section_def["end_marker"])
        section_lines = lines[start_idx:end_idx]
        section_start_file = section_lines[0]["file"] if section_lines else str(files[0])
        section_end_file = section_lines[-1]["file"] if section_lines else str(files[-1])
        pages = []
        for item in section_lines:
            for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", item["text"]):
                pages.append(int(match.group(1)))
        sections.append(
            {
                "section_key": section_def["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": section_def["section_kind"],
                "heading_raw": section_def["heading_raw"],
                "heading_norm": section_def["heading_norm"],
                "heading_letter": None,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "file_start": section_start_file,
                "file_end": section_end_file,
                "confidence": 0.92 if order < 5 else 0.98,
                "raw_json": {
                    "section_kind_reason": section_def["section_kind_reason"],
                    "start_marker": section_def["start_marker"],
                    "end_marker": section_def["end_marker"],
                },
            }
        )

        current_letter_node: str | None = None
        current_heading_node: str | None = None
        entry_order = 0
        letter_order = 0
        heading_order = 0
        for item in section_lines:
            text = normalize(item["text"])
            if not text:
                continue
            if TRAILING_HEADER_RE.match(text) or text == "Digitized by Google":
                continue
            if section_def["section_kind"] != "ordo_rerum" and is_section_marker(text, [section_def["start_marker"]]):
                continue
            if section_def["section_kind"] != "ordo_rerum" and section_def["end_marker"] and is_section_marker(text, [section_def["end_marker"]]):
                continue
            for fragment in split_fragments(text):
                fragment = normalize(fragment)
                if not fragment or TRAILING_HEADER_RE.match(fragment) or fragment == "Digitized by Google":
                    continue

                if section_def["section_kind"] == "analytic_subject" and LETTER_RE.fullmatch(fragment):
                    letter_order += 1
                    current_letter_node = f"{section_def['section_key']}:letter:{fragment}:{letter_order:03d}"
                    current_heading_node = None
                    nodes.append(
                        {
                            "node_key": current_letter_node,
                            "section_key": section_def["section_key"],
                            "parent_node_key": None,
                            "node_order": letter_order,
                            "node_kind": "letter_group",
                            "label_raw": fragment,
                            "label_norm": fragment.lower(),
                            "label_sort": fragment.lower(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source": "letter_marker"},
                        }
                    )
                    continue

                if section_def["section_kind"] == "analytic_subject" and HEADING_RE.fullmatch(fragment) and not PAGE_RE.search(fragment):
                    heading_order += 1
                    label = fragment.rstrip(":").rstrip(".")
                    current_heading_node = f"{section_def['section_key']}:heading:{heading_order:03d}"
                    nodes.append(
                        {
                            "node_key": current_heading_node,
                            "section_key": section_def["section_key"],
                            "parent_node_key": current_letter_node,
                            "node_order": heading_order,
                            "node_kind": "heading_group",
                            "label_raw": label,
                            "label_norm": text_norm(label),
                            "label_sort": sort_norm(label),
                            "node_level": 2 if current_letter_node else 1,
                            "confidence": 0.93,
                            "raw_json": {"source": "rubric_heading"},
                        }
                    )
                    continue

                if section_def["section_kind"] == "ordo_rerum":
                    if fragment in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "R", "S", "T", "U", "V", "X", "Z"}:
                        letter_order += 1
                        current_letter_node = f"{section_def['section_key']}:letter:{fragment}:{letter_order:03d}"
                        current_heading_node = None
                        nodes.append(
                            {
                                "node_key": current_letter_node,
                                "section_key": section_def["section_key"],
                                "parent_node_key": None,
                                "node_order": letter_order,
                                "node_kind": "letter_group",
                                "label_raw": fragment,
                                "label_norm": fragment.lower(),
                                "label_sort": fragment.lower(),
                                "node_level": 1,
                                "confidence": 0.96,
                                "raw_json": {"source": "ordo_letter_marker"},
                            }
                        )
                        continue

                    if HEADING_RE.fullmatch(fragment) and not PAGE_RE.search(fragment) and len(fragment) < 48:
                        heading_order += 1
                        current_heading_node = f"{section_def['section_key']}:heading:{heading_order:03d}"
                        nodes.append(
                            {
                                "node_key": current_heading_node,
                                "section_key": section_def["section_key"],
                                "parent_node_key": current_letter_node,
                                "node_order": heading_order,
                                "node_kind": "heading_group",
                                "label_raw": fragment.rstrip(":").rstrip("."),
                                "label_norm": text_norm(fragment.rstrip(":").rstrip(".")),
                                "label_sort": sort_norm(fragment.rstrip(":").rstrip(".")),
                                "node_level": 2 if current_letter_node else 1,
                                "confidence": 0.91,
                                "raw_json": {"source": "ordo_heading"},
                            }
                        )
                        continue

                if entry_kind_for(fragment) == "editorial_note" and PAGE_RE.search(fragment) is None:
                    continue

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{order:02d}:{entry_order:04d}"
                refs_for_entry = extract_refs(fragment, page_map, section_def["section_key"], item["file"])
                target_best = None
                if refs_for_entry:
                    target_best = refs_for_entry[0].get("target_file")
                helper_entry = {
                    "entry_id": entry_key,
                    "lemma_raw": extract_lemma(fragment) or fragment[:120],
                    "query_names": [q for q in [extract_lemma(fragment), normalize(fragment), text_norm(fragment)] if q][:4],
                    "page_hints": [str(ref["page_ref_int"]) for ref in refs_for_entry[:4]],
                    "page_hint_ints": [ref["page_ref_int"] for ref in refs_for_entry[:4]],
                    "context_raw": fragment[:240],
                }
                helper_info = helper_map.get(entry_key, {})
                if helper_info.get("best_candidate", {}).get("file"):
                    target_best = helper_info["best_candidate"]["file"]
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_def["section_key"],
                    "parent_node_key": current_heading_node or current_letter_node,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind_for(fragment),
                    "lemma_raw": extract_lemma(fragment),
                    "lemma_display": extract_lemma(fragment),
                    "lemma_norm": text_norm(extract_lemma(fragment)),
                    "lemma_sort": sort_norm(extract_lemma(fragment)),
                    "entry_raw": fragment,
                    "context_raw": fragment if len(fragment) <= 220 else fragment[:220],
                    "heading_letter": (extract_lemma(fragment) or fragment[:1] or "").strip()[:1].upper() or None,
                    "inferred_printed_page": refs_for_entry[0]["page_ref_int"] if refs_for_entry else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": item["file"],
                    "target_file_best": target_best,
                    "confidence": 0.84 if refs_for_entry else 0.68,
                    "raw_json": {
                        "source_file": item["file"],
                        "section_kind": section_def["section_kind"],
                        "helper": compact_helper_entry(helper_info),
                        "entry_kind_reason": (
                            "cross-reference remission"
                            if entry_kind_for(fragment) == "cross_reference"
                            else "heading or lemma fragment"
                        ),
                    },
                }
                entries.append(entry)
                for ref in refs_for_entry:
                    ref["entry_key"] = entry_key
                    refs.append(ref)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the final index material for PG140 from the OCR tail, including the Nicetas Choniates indexes, "
            "the Georgius Acropolita index, and the closing ORDO RERUM table."
        ),
        "evidence_files": [
            str(files[0]),
            str(files[len(files) // 2]),
            str(files[-1]),
        ],
    }

    notes = [
        "OCR file suffixes were kept separate from printed page references.",
        "The PG140 tail contains several consecutive index sections; the payload keeps them as distinct sections.",
        "Letter groups and rubric headings were promoted to nodes where the OCR clearly marked them.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def build_helper_request(entries: list[dict[str, Any]], source_root: Path, helper_request_json: Path) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        if not entry.get("refs"):
            continue
        pages: list[int] = []
        seen: set[int] = set()
        for ref in entry["refs"]:
            page = ref.get("page_ref_int")
            if isinstance(page, int) and page not in seen:
                seen.add(page)
                pages.append(page)
        if not pages:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:120],
                "query_names": [
                    q
                    for q in [
                        entry.get("lemma_raw"),
                        entry.get("lemma_display"),
                        entry.get("lemma_norm"),
                    ]
                    if q
                ][:4],
                "page_hints": [str(v) for v in pages[:4]],
                "page_hint_ints": pages[:4],
                "context_raw": entry.get("context_raw") or entry["entry_raw"][:240],
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG140 alphabetical payload.")
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
        "current_focus": "Extract PG140 tail indexes and validate target locators",
        "completed": [
            "OCR tail inspected",
            "section boundaries identified",
        ],
        "pending": [
            "build helper request",
            "run index_target_locator",
            "assemble final payload",
            "write output JSON",
        ],
        "blocked": [],
        "notes": [
            "Use printed page numbers as material locators, not OCR file suffixes.",
            "Keep section headings distinct from regular entries.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    files = discover_files(args.source_root)
    provisional_payload = build_payload(args.source_root, {})
    write_json(args.intermediate_dir / "sections.json", provisional_payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", provisional_payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", provisional_payload["entries"])
    write_json(args.intermediate_dir / "refs.json", provisional_payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", provisional_payload["scripture_refs"])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "source_root": str(args.source_root),
            "file_count": len(files),
            "first_file": str(files[0]) if files else None,
            "last_file": str(files[-1]) if files else None,
            "generated_at": now_iso(),
        },
    )

    helper_request = build_helper_request(provisional_payload["entries"], args.source_root, args.helper_request_json)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_result_map(helper_output)

    final_payload = build_payload(args.source_root, helper_map)
    write_json(args.intermediate_dir / "volume.json", final_payload["volume"])
    write_json(args.intermediate_dir / "sections.json", final_payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", final_payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", final_payload["entries"])
    write_json(args.intermediate_dir / "refs.json", final_payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", final_payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", final_payload["coverage"])
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Final payload written",
            "completed": [
                "OCR tail inspected",
                "section boundaries identified",
                "helper request built",
                "helper run completed",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Helper output preserved in the runtime output path.",
                "OCR literals retained throughout.",
            ],
        },
    )

    write_json(args.output_file, final_payload)


if __name__ == "__main__":
    main()

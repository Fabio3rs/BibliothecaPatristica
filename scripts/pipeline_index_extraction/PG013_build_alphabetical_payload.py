#!/usr/bin/env python3
"""Usage: build the PG013 alphabetical payload from the OCR index tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG013_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG013/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG013_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG013_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG013 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG013_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG013"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 13"
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING_RAW = "INDEX ANALYTICUS. IN TOMUM III."
SECTION_HEADING_NORM = "index analyticus in tomum iii"
SECTION_KIND_REASON = (
    "Analytical alphabetical index beginning with the printed heading 'INDEX ANALYTICUS. IN TOMUM III.' "
    "and continuing through the A-P lemma sequence before the editorial closure 'ORDO RERUM'."
)

INDEX_START_SEQ = 987
INDEX_END_SEQ = 1006
ORDO_SEQ = 1007

SECTION_PAGE_START = 1947
SECTION_PAGE_END = 1984
SECTION_START_SEQ = 987
SECTION_END_SEQ = 1006

SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
INDEX_MARKER_RE = re.compile(r"^INDEX(?:\s+ANALYTICUS(?:\.)?(?:\s+IN\s+TOMUM\s+III\.)?)?$", re.IGNORECASE)
ORDO_MARKER_RE = re.compile(r"^ORDO RERUM(?:\s+QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?$", re.IGNORECASE)
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:[-–—]|à)\s*(\d{1,4}))?(?:\s*(?:et\s+seqq?\.?|et\s+seq\.?|not\.?|ibid\.?))?", re.IGNORECASE)
ENTRY_SPLIT_RE = re.compile(
    r"(?:(?<=\d\.)|(?<=\bnot\.))\s+(?=[A-ZÆŒ])", re.IGNORECASE
)
CONTINUATION_RE = re.compile(r"^[,\.;:)\]\-—]\s*")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def text_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    return text_norm(value)


def discover_files(source_root: Path) -> list[Path]:
    files: dict[int, list[Path]] = defaultdict(list)
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            continue
        files[int(m.group(1))].append(path)

    preferred: list[Path] = []
    for seq in sorted(files):
        candidates = files[seq]
        candidates.sort(key=lambda p: (0 if p.name.startswith("PG013-") else 1, p.name))
        preferred.append(candidates[0])
    return preferred


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(m.group(1))


def extract_header_pages(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(read_text(path))
    header = normalize(parsed.get("header_text") or "")
    pages: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            pages.append(value)
    return pages


def page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in extract_header_pages(path):
            mapping.setdefault(page, str(path))
    return mapping


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw in body.splitlines():
        line = normalize(raw)
        if not line or line == "Digitized by Google":
            continue
        lines.append(line)
    return lines


def split_fragments(line: str) -> list[str]:
    text = normalize(line)
    if not text:
        return []
    text = re.sub(r"^\d{1,4}\.?\s*", "", text)
    text = re.sub(r"(?<=\d\.)\s+(?=[A-ZÆŒ])", "\n", text)
    text = re.sub(r"(?i)\b(?:et\s+seqq?\.?|ibid\.?|not\.)\s+(?=[A-ZÆŒ])", lambda m: m.group(0).rstrip() + "\n", text)
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    fragments: list[str] = []
    for part in parts:
        cleaned = normalize(part)
        if not cleaned:
            continue
        fragments.append(cleaned)
    return fragments


def is_heading_line(fragment: str) -> bool:
    return bool(LETTER_RE.fullmatch(fragment) or INDEX_MARKER_RE.fullmatch(fragment) or ORDO_MARKER_RE.fullmatch(fragment))


def extract_refs(entry_raw: str, target_pages: dict[int, str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None, str]] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        page_int = int(match.group(1))
        end_int = int(match.group(2)) if match.group(2) else None
        ref_raw = normalize(match.group(0))
        key = (page_int, end_int, ref_raw)
        if key in seen:
            continue
        seen.add(key)
        ref_kind = "editorial_range" if end_int is not None else "editorial_page"
        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page_int) if end_int is not None else None,
                "range_end_raw": str(end_int) if end_int is not None else None,
                "target_file": target_pages.get(page_int),
                "target_file_probability": 0.95 if target_pages.get(page_int) else None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.88 if target_pages.get(page_int) else 0.58,
                "raw_json": {
                    "page_token_kind": "range" if end_int is not None else "page",
                },
            }
        )
    return refs


def extract_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw)
    if not text:
        return None
    cut = len(text)
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bvoir\b", r"\bv\.\b", r"\bcf\.\b", r"\bid\.\b"]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            cut = min(cut, m.start())
    m = PAGE_REF_RE.search(text)
    if m:
        cut = min(cut, m.start())
    lemma = text[:cut].strip(" ,;:.")
    return lemma or None


def extract_entry_kind(entry_raw: str) -> str:
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", entry_raw, flags=re.IGNORECASE):
        return "cross_reference"
    if extract_lemma(entry_raw) is None:
        return "editorial_note"
    return "lemma"


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.get("refs"):
            continue
        page_hints = []
        seen: set[int] = set()
        for ref in entry["refs"]:
            page_int = ref.get("page_ref_int")
            if isinstance(page_int, int) and page_int not in seen:
                seen.add(page_int)
                page_hints.append(page_int)
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
                "query_names": [q for q in [entry["lemma_raw"], entry["lemma_display"], entry["lemma_norm"]] if q][:4],
                "page_hints": [str(v) for v in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry["context_raw"] or entry["entry_raw"][:240],
            }
        )
        if len(helper_entries) >= 24:
            break
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    try:
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
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return {}
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
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


def merge_fragments(prev_entry: dict[str, Any] | None, fragment: str) -> dict[str, Any] | None:
    if prev_entry is None:
        return None
    prev_entry["entry_raw"] = normalize(f"{prev_entry['entry_raw']} {fragment}")
    if prev_entry.get("context_raw") is None and len(prev_entry["entry_raw"]) > 180:
        prev_entry["context_raw"] = prev_entry["entry_raw"][:240]
    return prev_entry


def build_payload(source_root: Path, helper_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = discover_files(source_root)
    header_map = page_map(files)

    section_files = [path for path in files if INDEX_START_SEQ <= file_seq(path) <= INDEX_END_SEQ]
    if not section_files:
        raise SystemExit("No index files found for PG013")

    section_start_file = str(section_files[0])
    section_end_file = str(section_files[-1])

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    letter_nodes: dict[str, str] = {}
    letter_order = 0
    entry_order = 0
    current_letter_node: str | None = None
    pending: dict[str, Any] | None = None
    capture_started = False

    for path in section_files:
        for raw_line in extract_body_lines(path):
            fragments = split_fragments(raw_line)
            for fragment in fragments:
                if is_heading_line(fragment):
                    if LETTER_RE.fullmatch(fragment):
                        if fragment == "A":
                            capture_started = True
                        if not capture_started:
                            continue
                        current_letter_node = letter_nodes.get(fragment)
                        if current_letter_node is None:
                            letter_order += 1
                            current_letter_node = f"{SECTION_KEY}:letter:{fragment}"
                            letter_nodes[fragment] = current_letter_node
                            nodes.append(
                                {
                                    "node_key": current_letter_node,
                                    "section_key": SECTION_KEY,
                                    "parent_node_key": None,
                                    "node_order": letter_order,
                                    "node_kind": "letter_group",
                                    "label_raw": fragment,
                                    "label_norm": fragment.lower(),
                                    "label_sort": fragment.lower(),
                                    "node_level": 1,
                                    "confidence": 0.99,
                                    "raw_json": {"source": "standalone_letter_heading"},
                                }
                            )
                    pending = None
                    continue

                if not capture_started:
                    continue

                if CONTINUATION_RE.match(fragment) and pending is not None:
                    pending["entry_raw"] = normalize(f"{pending['entry_raw']} {fragment}")
                    pending["context_raw"] = pending["entry_raw"][:240] if len(pending["entry_raw"]) > 180 else None
                    continue

                if pending is not None:
                    entries.append(pending)
                    pending = None

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                lemma = extract_lemma(fragment)
                entry_kind = extract_entry_kind(fragment)
                ref_list = extract_refs(fragment, header_map)
                helper_info = helper_map.get(entry_key, {})

                target_best = None
                if ref_list:
                    target_best = ref_list[0].get("target_file")
                if helper_info.get("best_candidate", {}).get("file"):
                    target_best = helper_info["best_candidate"]["file"]

                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": current_letter_node,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": text_norm(lemma),
                    "lemma_sort": sort_norm(lemma),
                    "entry_raw": fragment,
                    "context_raw": fragment if len(fragment) <= 180 else fragment[:180],
                    "heading_letter": (lemma or fragment[:1] or "").strip()[:1].upper() or None,
                    "inferred_printed_page": ref_list[0]["page_ref_int"] if ref_list else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_best,
                    "confidence": 0.9 if ref_list else 0.7,
                    "raw_json": {
                        "source_file": str(path),
                        "helper": helper_info or None,
                    },
                }
                if entry_kind == "editorial_note" and entry["lemma_raw"] is None:
                    entry["context_raw"] = None

                entry_letter = (entry["lemma_raw"] or fragment[:1] or "").strip()[:1].upper() or None
                if entry_letter and LETTER_RE.fullmatch(entry_letter) and entry_letter not in letter_nodes:
                    letter_order += 1
                    current_letter_node = f"{SECTION_KEY}:letter:{entry_letter}"
                    letter_nodes[entry_letter] = current_letter_node
                    nodes.append(
                        {
                            "node_key": current_letter_node,
                            "section_key": SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": letter_order,
                            "node_kind": "letter_group",
                            "label_raw": entry_letter,
                            "label_norm": entry_letter.lower(),
                            "label_sort": entry_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.95,
                            "raw_json": {"source": "inferred_from_entry_initial"},
                        }
                    )
                    entry["parent_node_key"] = current_letter_node
                elif entry_letter and LETTER_RE.fullmatch(entry_letter):
                    current_letter_node = letter_nodes[entry_letter]
                    entry["parent_node_key"] = current_letter_node

                pending = entry

                for ref in ref_list:
                    ref["entry_key"] = entry_key
                    ref["section_start_file"] = section_start_file
                    ref["editorial_anchor_file"] = str(path)
                    refs.append(ref)

        if pending is not None:
            entries.append(pending)
            pending = None

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the analytical alphabetical index from the OCR tail spanning printed pages 1947-1984 "
            "and preserved material page references separately from OCR file suffixes."
        ),
        "evidence_files": [str(section_files[0]), str(section_files[len(section_files)//2]), str(section_files[-1])],
    }

    node_sort_order = {letter: idx for idx, letter in enumerate(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["Æ", "Œ"], start=1)}
    nodes.sort(key=lambda node: (node_sort_order.get(node["label_raw"], 999), node["label_raw"]))
    for idx, node in enumerate(nodes, start=1):
        node["node_order"] = idx

    notes = [
        "The volume uses paired OCR variants for some pages; the payload prefers the PG013-named file when both variants exist.",
        "The editorial closure 'ORDO RERUM' appears after the alphabetical index and was not promoted to a lemma section.",
        "OCR literals were preserved, including odd punctuation, spacing, and apparent print/OCR noise.",
    ]

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
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "section_file_seq_start": SECTION_START_SEQ,
                "section_file_seq_end": SECTION_END_SEQ,
                "editorial_closure": {
                    "kind": "ordo_rerum",
                    "file_seq": ORDO_SEQ,
                },
            },
        }
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG013 alphabetical payload.")
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
        "current_focus": "Build and validate the PG013 alphabetical index payload",
        "completed": [
            "OCR window identified",
            "section split confirmed",
        ],
        "pending": [
            "run helper",
            "assemble payload",
            "write final JSON",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR and material page numbering separate.",
            "Prefer conservative entry splits when an OCR line contains multiple lemmas.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    files = discover_files(args.source_root)
    section_files = [path for path in files if INDEX_START_SEQ <= file_seq(path) <= INDEX_END_SEQ]
    header_map = page_map(files)

    # A light pre-pass so the helper request has real entries to validate.
    provisional_entries: list[dict[str, Any]] = []
    entry_order = 0
    current_letter = None
    capture_started = False
    for path in section_files[:]:
        for raw_line in extract_body_lines(path):
            for fragment in split_fragments(raw_line):
                if is_heading_line(fragment):
                    if LETTER_RE.fullmatch(fragment):
                        if fragment == "A":
                            capture_started = True
                        if not capture_started:
                            continue
                        current_letter = fragment
                    continue
                if not capture_started:
                    continue
                if CONTINUATION_RE.match(fragment):
                    continue
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                ref_list = extract_refs(fragment, header_map)
                provisional_entries.append(
                    {
                        "entry_key": entry_key,
                        "lemma_raw": extract_lemma(fragment),
                        "lemma_display": extract_lemma(fragment),
                        "lemma_norm": text_norm(extract_lemma(fragment)),
                        "entry_raw": fragment,
                        "context_raw": fragment if len(fragment) <= 180 else fragment[:180],
                        "refs": ref_list,
                        "parent_node_key": current_letter,
                    }
                )
                provisional_letter = (extract_lemma(fragment) or fragment[:1] or "").strip()[:1].upper() or None
                if provisional_letter and LETTER_RE.fullmatch(provisional_letter):
                    current_letter = provisional_letter

    helper_request = build_helper_request(provisional_entries, args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_best_map(helper_output)

    payload = build_payload(args.source_root, helper_map)

    # Persist intermediate fragments for reruns.
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
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
            "helper_entry_count": len(helper_request["entries"]),
        },
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

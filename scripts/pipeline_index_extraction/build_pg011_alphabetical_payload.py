#!/usr/bin/env python3
"""Usage: build the PG011 alphabetical index payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg011_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG011/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG011_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG011_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG011 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG011_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PG011"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 11"
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING_RAW = "INDEX ANALYTICUS."
SECTION_HEADING_NORM = "index analyticus"
SECTION_KIND_REASON = (
    "Analytical alphabetical index opening with the heading 'INDEX ANALYTICUS.' and "
    "continuing through the A-Z lemma sequence; the closing 'Ordinem rerum vide...' "
    "is editorial closure rather than a separate alphabetical section."
)

SECTION_START_SEQ = 947
SECTION_END_SEQ = 978
SECTION_PAGE_START = 1885
SECTION_PAGE_END = 1932

ROOT = Path("/homessddata/Projects/pdfocr")
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
GREEK_LETTER_RE = re.compile(r"^[Α-Ω]$")
PAGE_REF_RE = re.compile(r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\b")
SPLIT_AFTER_REFS_RE = re.compile(
    r"(?:(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)|(?:ibid\.?)|(?:not\.?)|(?:monit\.?))\.\s+(?=[A-ZÆŒΑ-Ω])",
    re.IGNORECASE,
)
BLOCK_RE = re.compile(
    r'<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>',
    flags=re.DOTALL | re.IGNORECASE,
)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def lemma_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_header_page(path: Path) -> int | None:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text") or "")
    nums = [int(match.group(0)) for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header) if not match.group(1).startswith("0")]
    return nums[0] if nums else None


def build_page_header_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        page = extract_header_page(path)
        if page is None:
            continue
        mapping.setdefault(page, str(path))
    return mapping


def extract_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, list[str]]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = (attrs.get("tipo") or "").strip().lower()
        if block_type not in {"texto_principal", "nota_marginal"}:
            continue
        content = match.group("content") or ""
        lines = []
        for raw_line in content.splitlines():
            cleaned = normalize(raw_line)
            if cleaned:
                lines.append(cleaned)
        if lines:
            blocks.append((block_type, lines))
    return blocks


def split_fragments(text: str) -> list[str]:
    if not text:
        return []
    fragments: list[str] = []
    for raw_part in text.splitlines():
        part = raw_part.replace("\xa0", " ")
        part = re.sub(r"\s+", " ", part).strip()
        if not part:
            continue
        part = re.sub(r"(?<=\d\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", part)
        part = re.sub(r"(?<=ibid\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", part, flags=re.IGNORECASE)
        part = re.sub(r"(?<=not\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", part, flags=re.IGNORECASE)
        part = re.sub(r"(?<=monit\.)\s+(?=[A-ZÆŒΑ-Ω])", "\n", part, flags=re.IGNORECASE)
        for item in part.splitlines():
            cleaned = normalize(item)
            if cleaned:
                fragments.append(cleaned)
    return fragments


def is_letter_heading(fragment: str) -> bool:
    return bool(LETTER_RE.fullmatch(fragment) or GREEK_LETTER_RE.fullmatch(fragment))


def looks_like_continuation(fragment: str) -> bool:
    if not fragment:
        return False
    if fragment[:1].islower():
        return True
    if fragment.startswith(("—", "-", "·", ":", ";", ",")):
        return True
    return False


def extract_page_hints(entry_raw: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_REF_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if not raw:
            continue
        token = raw.split("—", 1)[0].split("-", 1)[0].split("–", 1)[0].strip()
        if not token.isdigit():
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def extract_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw)
    if not text:
        return None
    cut = len(text)
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bnot\.\b", r"\bmonit\.\b"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            cut = min(cut, match.start())
    num_match = PAGE_REF_RE.search(text)
    if num_match:
        cut = min(cut, num_match.start())
    lemma = text[:cut].strip(" ,;:.")
    return lemma or None


def extract_refs(entry_raw: str, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last: int | None = None
    first_page: int | None = None
    for match in PAGE_REF_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if not raw:
            continue
        if "—" in raw or "–" in raw or "-" in raw:
            start_raw, end_raw = re.split(r"\s*[-–—]\s*", raw, maxsplit=1)
            if not start_raw.isdigit():
                continue
            page_int = int(start_raw)
            range_start = start_raw
            range_end = end_raw if end_raw.isdigit() else None
            ref_kind = "editorial_range"
        else:
            if not raw.isdigit():
                continue
            page_int = int(raw)
            range_start = None
            range_end = None
            ref_kind = "editorial_page"
        current_last = page_int
        if first_page is None:
            first_page = page_int
        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start,
                "range_end_raw": range_end,
                "target_file": page_map.get(page_int),
                "target_file_probability": 0.99 if page_map.get(page_int) else None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.85 if page_map.get(page_int) else 0.55,
                "raw_json": {
                    "page_token_kind": "range" if ref_kind == "editorial_range" else "page",
                },
            }
        )
    return refs, first_page


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = extract_page_hints(entry["entry_raw"])
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
        str(ROOT / "scripts" / "index_target_locator.py"),
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


def build_payload(source_root: Path, helper_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_header_map(files)

    section_files = [path for path in files if SECTION_START_SEQ <= file_seq(path) <= SECTION_END_SEQ]
    section_start_file = str(section_files[0]) if section_files else None
    section_end_file = str(section_files[-1]) if section_files else None

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_node_key = None
    entry_counter = 0
    node_counter = 0

    letter_order: dict[str, int] = {}

    for path in section_files:
        pending: dict[str, Any] | None = None
        for block_type, block_lines in extract_blocks(path):
            for frag in block_lines:
                if frag in {"Digitized by Google", "PATROL. GR. XI."}:
                    continue
                if block_type == "nota_marginal" and is_letter_heading(frag):
                    current_node_key = f"{SECTION_KEY}:letter:{frag}"
                    if frag not in letter_order:
                        node_counter += 1
                        letter_order[frag] = node_counter
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": SECTION_KEY,
                                "parent_node_key": None,
                                "node_order": node_counter,
                                "node_kind": "letter_group",
                                "label_raw": frag,
                                "label_norm": frag.lower(),
                                "label_sort": frag.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source": "marginal_letter_heading"},
                            }
                        )
                    pending = None
                    continue

                if is_letter_heading(frag):
                    current_node_key = f"{SECTION_KEY}:letter:{frag}"
                    if frag not in letter_order:
                        node_counter += 1
                        letter_order[frag] = node_counter
                        nodes.append(
                            {
                                "node_key": current_node_key,
                                "section_key": SECTION_KEY,
                                "parent_node_key": None,
                                "node_order": node_counter,
                                "node_kind": "letter_group",
                                "label_raw": frag,
                                "label_norm": frag.lower(),
                                "label_sort": frag.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source": "standalone_letter_heading"},
                            }
                        )
                    pending = None
                    continue

                if looks_like_continuation(frag) and pending is not None:
                    pending["entry_raw"] = f"{pending['entry_raw']} {frag}".strip()
                    continue

                if pending is not None:
                    entries.append(pending)

                entry_counter += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
                lemma_raw = extract_lemma(frag)
                entry_kind = "lemma"
                if re.search(r"\bVide\b|\bvid\.\b", frag, flags=re.IGNORECASE):
                    entry_kind = "cross_reference"
                refs_for_entry, inferred_page = extract_refs(frag, page_map)
                helper_info = helper_map.get(entry_key, {})
                target_file_best = None
                if refs_for_entry:
                    target_file_best = refs_for_entry[0].get("target_file")
                if helper_info.get("best_candidate", {}).get("file"):
                    target_file_best = helper_info["best_candidate"].get("file")

                entry = {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": current_node_key,
                    "entry_order": entry_counter,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": lemma_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": frag,
                    "context_raw": frag if len(frag) < 180 else frag[:180],
                    "heading_letter": (lemma_raw or frag[:1] or "").strip()[:1].upper() or None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_file_best,
                    "confidence": 0.82 if refs_for_entry else 0.68,
                    "raw_json": {
                        "source_file": str(path),
                        "helper": helper_info or None,
                    },
                }
                if entry["lemma_raw"] is None and entry_kind == "lemma":
                    entry["entry_kind"] = "editorial_note"
                pending = entry

                for ref in refs_for_entry:
                    ref["entry_key"] = entry_key
                    ref["section_start_file"] = section_start_file
                    ref["editorial_anchor_file"] = str(path)
                    refs.append(ref)

            if pending is not None and block_type == "nota_marginal":
                # Letter-only marginal blocks are not entries.
                pass

        if pending is not None:
            entries.append(pending)
            pending = None

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered analytical alphabetical index entries from the OCR tail of PG011 and attached material references where the printed page numbers were recoverable.",
        "evidence_files": [str(path) for path in section_files[:4]] + [str(section_files[-1])] if section_files else [],
    }

    notes = [
        "Section detected from the analytical index tail beginning at OCR file 947.",
        "Page 946 is an APPENDIX and was excluded from the alphabetical section.",
        "OCR noise and pagination drift were preserved; no silent digit normalization was applied.",
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
        "sections": [
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
                },
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
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
        "current_focus": "Extract PG011 alphabetical analytical index and resolve cited page targets",
        "completed": [
            "Confirmed APPENDIX on file 946 and index start on file 947",
            "Read the index tail through file 978",
        ],
        "pending": [
            "Run helper on extracted page-hint entries",
            "Write final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals; do not normalize dubious digits or abbreviations.",
        ],
    }
    (args.intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Build a provisional payload structure first so we can derive helper entries.
    provisional = build_payload(args.source_root, {})
    helper_request = build_helper_request(provisional["entries"], args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_best_map(helper_output)
    payload = build_payload(args.source_root, helper_map)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

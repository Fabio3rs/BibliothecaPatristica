#!/usr/bin/env python3
"""Usage: build the PL183 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl183_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL183/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL183_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL183_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL183 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL183_alphabetical_indices.json
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
VOLUME_ID = "PL183"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 183"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL183/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL183_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL183_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL183_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL183"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_PAGE_START = 608
INDEX_PAGE_END = 659
FOREIGN_TERMS_PAGE = 660
ORDO_PAGE_START = 660
ORDO_PAGE_END = 664

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
FOREIGN_SECTION_KEY = f"{VOLUME_ID}:alpha:foreign_terms:002"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:003"

INDEX_HEADING_RAW = "INDEX RERUM ET VERBORUM"
FOREIGN_HEADING_RAW = "NOMENCLATOR VOCUM EXOTICARUM IN S. BERNARDO."
ORDO_HEADING_RAW = "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."

NOISE_PATTERNS = {
    "Digitized by Google",
    INDEX_HEADING_RAW,
    "INDEX RERUM ET VERBORUM",
    "ORDO RERUM",
    "ORDO SERMONUM",
    "QVAE IN HOC TOMO CONTINENTVR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
}
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LETTER_PREFIX_RE = re.compile(r"^[A-ZÆŒ]\s+")
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ])")
NO_LOCATOR_REF_RE = re.compile(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in PAGE_HEADER_RE.finditer(header):
            token = match.group(1)
            if token.startswith("0"):
                continue
            page_map.setdefault(int(token), str(path))
    return page_map


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line or line in NOISE_PATTERNS:
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        lines.append(line)
    return lines


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in PAGE_HEADER_RE.finditer(text):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_fragment(fragment: str, has_refs: bool) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if has_refs:
        m = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
        if m:
            text = text[: m.start()].rstrip(" ,;:.")
    return text.strip(" .;:") or None


def entry_kind(section_kind: str, fragment: str, has_refs: bool) -> str:
    text = normalize(fragment) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not has_refs and NO_LOCATOR_REF_RE.search(text):
        return "cross_reference"
    if not has_refs and len(text) <= 30 and not re.search(r"[.,]", text):
        return "heading_group"
    return "lemma"


def target_for_page(page: int | None, page_map: dict[int, str]) -> str | None:
    if page is None:
        return None
    return page_map.get(page)


def make_helper_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
    if not page_hints:
        return None
    lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:80]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma_raw,
        "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0]],
        "page_hints": [str(page) for page in page_hints[:3]],
        "page_hint_ints": page_hints[:3],
        "context_raw": entry["entry_raw"],
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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map_by_id(helper_output: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        if isinstance(item, dict):
            mapping[item.get("entry_id")] = item
    return mapping


def add_letter_node(
    nodes: list[dict[str, Any]],
    section_key: str,
    letter: str,
    source_file: str,
) -> str:
    node_key = f"{VOLUME_ID}:node:{section_key.split(':')[-1]}:{letter}"
    nodes.append(
        {
            "node_key": node_key,
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": len(nodes) + 1,
            "node_kind": "letter_group",
            "label_raw": letter,
            "label_norm": letter.lower(),
            "label_sort": letter.lower(),
            "node_level": 1,
            "confidence": 0.99,
            "raw_json": {"source_file": source_file, "kind": "alphabetic divider"},
        }
    )
    return node_key


def parse_fragment_entries(
    *,
    files: list[Path],
    section_key: str,
    section_kind: str,
    page_map: dict[int, str],
    source_root: Path,
    start_order: int = 1,
    line_overrides: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_letter: str | None = None
    letter_nodes: dict[str, str] = {}
    entry_order = start_order - 1
    helper_seed: list[dict[str, Any]] = []
    started = section_kind != "analytic_subject"

    for path in files:
        lines = line_overrides.get(str(path)) if line_overrides else None
        if lines is None:
            lines = extract_body_lines(path)
        if section_kind == "ordo_rerum":
            fragments = lines
        else:
            page_parts: list[str] = []
            for line in lines:
                if SINGLE_LETTER_RE.fullmatch(line):
                    started = True
                    current_letter = line
                    if current_letter not in letter_nodes:
                        letter_nodes[current_letter] = add_letter_node(nodes, section_key, current_letter, str(path))
                    continue
                if LETTER_PREFIX_RE.match(line):
                    started = True
                    current_letter = line[0]
                    if current_letter not in letter_nodes:
                        letter_nodes[current_letter] = add_letter_node(nodes, section_key, current_letter, str(path))
                    line = line[2:].lstrip()
                if section_kind == "analytic_subject" and not started:
                    continue
                page_parts.append(line)
            page_text = " ".join(page_parts)
            page_text = re.sub(r"(?<=\w)-\s+", "", page_text)
            page_text = re.sub(r"\s+", " ", page_text).strip()
            fragments = [frag.strip() for frag in SENTENCE_SPLIT_RE.split(page_text) if frag.strip()]

        for fragment in fragments:
            cleaned = normalize(fragment) or ""
            if not cleaned or cleaned in NOISE_PATTERNS:
                continue
            if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                continue
            if cleaned.startswith("NOMENCLATOR VOCUM EXOTICARUM") or cleaned.startswith("ORDO RERUM"):
                continue
            page_hints = extract_page_hints(cleaned)
            if section_kind != "ordo_rerum":
                kind = entry_kind(section_kind, cleaned, bool(page_hints))
                if kind == "heading_group" and len(cleaned) == 1 and cleaned.isalpha():
                    continue
            else:
                kind = "heading_group"
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
            lemma_raw = lemma_from_fragment(cleaned, bool(page_hints)) if kind != "cross_reference" else None
            target_file_best = target_for_page(page_hints[0], page_map) if page_hints else str(path)
            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
                "inferred_printed_page": page_hints[0] if page_hints else None,
                "section_start_file": str(files[0]) if files else None,
                "editorial_anchor_file": str(path),
                "target_file_best": target_file_best,
                "confidence": 0.86 if page_hints else 0.62,
                "raw_json": {
                    "source_file": str(path),
                    "page_hints": page_hints,
                    "section_kind": section_kind,
                    "split_strategy": "sentence_boundary" if section_kind != "ordo_rerum" else "line",
                },
            }
            entries.append(entry)
            if page_hints:
                helper_item = make_helper_entry(entry)
                if helper_item:
                    helper_seed.append(helper_item)
                for ref_order, page in enumerate(page_hints, start=1):
                    target_file = target_for_page(page, page_map)
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": str(page),
                            "page_ref_raw": str(page),
                            "page_ref_int": page,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": 0.99 if target_file else None,
                            "section_start_file": str(files[0]) if files else None,
                            "editorial_anchor_file": str(path),
                            "confidence": 0.95 if target_file else 0.68,
                            "raw_json": {
                                "source_file": str(path),
                                "locator_method": "header_page_map" if target_file else "unresolved",
                            },
                        }
                    )

    return entries, refs, nodes, helper_seed


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_by_id = helper_map_by_id(helper_output)
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": best if best else None,
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in helper.get("candidates", [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    index_files = [path for path in files if INDEX_PAGE_START <= file_seq(path) <= INDEX_PAGE_END]
    closure_files = [path for path in files if ORDO_PAGE_START <= file_seq(path) <= ORDO_PAGE_END]

    index_entries, index_refs, index_nodes, helper_seed = parse_fragment_entries(
        files=index_files,
        section_key=INDEX_SECTION_KEY,
        section_kind="analytic_subject",
        page_map=page_map,
        source_root=source_root,
    )

    foreign_entries: list[dict[str, Any]] = []
    foreign_refs: list[dict[str, Any]] = []
    foreign_nodes: list[dict[str, Any]] = []
    closure_lines_map: dict[str, list[str]] = {}
    if closure_files:
        page_660 = next((p for p in closure_files if file_seq(p) == FOREIGN_TERMS_PAGE), closure_files[0])
        page_660_lines = extract_body_lines(page_660)
        pre_ordo_lines: list[str] = []
        post_ordo_lines: list[str] = []
        seen_ordo = False
        for line in page_660_lines:
            if line.startswith("ORDO RERUM"):
                seen_ordo = True
                continue
            if seen_ordo:
                post_ordo_lines.append(line)
            else:
                pre_ordo_lines.append(line)
        closure_lines_map[str(page_660)] = pre_ordo_lines
        foreign_entries, foreign_refs, foreign_nodes, foreign_seed = parse_fragment_entries(
            files=[page_660],
            section_key=FOREIGN_SECTION_KEY,
            section_kind="foreign_terms",
            page_map=page_map,
            source_root=source_root,
            line_overrides=closure_lines_map,
        )
        helper_seed.extend(foreign_seed)

    ordo_entries: list[dict[str, Any]] = []
    ordo_refs: list[dict[str, Any]] = []
    ordo_nodes: list[dict[str, Any]] = []
    if closure_files:
        if str(page_660) in closure_lines_map:
            closure_lines_map[str(page_660)] = post_ordo_lines
        ordo_entries, ordo_refs, ordo_nodes, ordo_seed = parse_fragment_entries(
            files=closure_files,
            section_key=ORDO_SECTION_KEY,
            section_kind="ordo_rerum",
            page_map=page_map,
            source_root=source_root,
            line_overrides=closure_lines_map,
        )
        helper_seed.extend(ordo_seed)

    helper_request_entries: list[dict[str, Any]] = []
    for item in helper_seed:
        if item and item not in helper_request_entries:
            helper_request_entries.append(item)
        if len(helper_request_entries) >= 18:
            break

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_request_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request_entries else {"status": "empty", "entries": []}

    entries = index_entries + foreign_entries + ordo_entries
    refs = index_refs + foreign_refs + ordo_refs
    nodes = index_nodes + foreign_nodes + ordo_nodes
    attach_helper(entries, helper_output)

    entry_map = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        entry = entry_map.get(ref["entry_key"])
        if not entry:
            continue
        helper = (entry.get("raw_json") or {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    sections = [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": INDEX_HEADING_RAW,
            "heading_norm": sort_norm(INDEX_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1203,
            "page_end": 1306,
            "file_start": str(index_files[0]) if index_files else None,
            "file_end": str(index_files[-1]) if index_files else None,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index headed INDEX RERUM ET VERBORUM, with letter-group dividers and multi-reference entries.",
                "evidence_files": [
                    str(index_files[0]) if index_files else None,
                    str(index_files[-1]) if index_files else None,
                ],
            },
        },
        {
            "section_key": FOREIGN_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "foreign_terms",
            "heading_raw": FOREIGN_HEADING_RAW,
            "heading_norm": sort_norm(FOREIGN_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1507,
            "page_end": 1508,
            "file_start": str(closure_files[0]) if closure_files else None,
            "file_end": str(closure_files[0]) if closure_files else None,
            "confidence": 0.88,
            "raw_json": {
                "section_kind_reason": "Nomenclator vocum exoticarum in S. Bernardo, a glossary-like alphabetical foreign-terms section preceding the editorial closure.",
                "evidence_files": [
                    str(closure_files[0]) if closure_files else None,
                ],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": sort_norm(ORDO_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1507,
            "page_end": 1516,
            "file_start": str(closure_files[0]) if closure_files else None,
            "file_end": str(closure_files[-1]) if closure_files else None,
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Editorial closure and contents tables headed ORDO RERUM / QUAE IN HOC TOMO CONTINENTUR.",
                "evidence_files": [
                    str(path) for path in closure_files[:4]
                ],
            },
        },
    ]

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the alphabetical index and the closing glossary / contents tables from the OCR tail. The major structures were serialized separately, and page-number drift was preserved literally.",
        "evidence_files": [
            str(index_files[0]) if index_files else None,
            str(index_files[-1]) if index_files else None,
            str(closure_files[0]) if closure_files else None,
            str(closure_files[-1]) if closure_files else None,
        ],
    }
    notes = [
        "The main index is ordered by alphabetic letter dividers, with substantial multi-reference lemmata and remissive cross-references.",
        "The closing files contain a foreign-terms nomenclator and contents/ordo material; they were kept separate from the subject index.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
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
            "generated_at": generated_at,
            "updated_at": generated_at,
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Finalize PL183 alphabetical payload and verify section boundaries.",
            "completed": [
                "identified the main index tail",
                "identified the nomenclator / contents closure",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from editorial page numbers.",
                "Preserve helper evidence only where it affects target selection.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL183 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

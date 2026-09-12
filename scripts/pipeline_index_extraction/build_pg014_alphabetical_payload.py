#!/usr/bin/env python3
"""Usage: build the PG014 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg014_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG014/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG014_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG014_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG014 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG014_alphabetical_indices.json
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG014"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus XIV"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG014/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG014_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG014_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG014_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG014"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"

NOISE_LINES = {
    "Digitized by Google",
    "INDEX ANALYTICUS",
    "INDEX ANALYTICUS.",
}

PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?:\s*(?:et\s+seq\.?|seqq\.?|seq\.?|not\.?|alibi\s+passim|passim))?", re.IGNORECASE)
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒΑ-Ω])")
LEADING_CONTINUATION_RE = re.compile(r"^(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?(?:\s*(?:et\s+seq\.?|seqq\.?|seq\.?|not\.?|alibi\s+passim|passim))?[\s,.;:]*)+$", re.IGNORECASE)
ROMAN_LETTER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]$")
PROTECTED_ABBREVIATIONS = {
    "Opp.": "Opp§",
    "Tom.": "Tom§",
    "t.": "t§",
    "col.": "col§",
    "lin.": "lin§",
    "ibid.": "ibid§",
    "Id.": "Id§",
    "Vid.": "Vid§",
    "vide.": "vide§",
    "not.": "not§",
    "seq.": "seq§",
    "seqq.": "seqq§",
    "passim.": "passim§",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
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


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def parse_page_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    header = normalize(text) or ""
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        token = match.group(1)
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            numbers.append(value)
    return numbers


def extract_clean_text(path: Path) -> str:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line:
            continue
        if line in NOISE_LINES:
            continue
        if len(line) == 1 and line.isalpha():
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        lines.append(line)
    return " ".join(lines)


def extract_text_blocks(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="texto_principal"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        text = re.sub(r"<[^>]+>", " ", match.group(1) or "")
        text = normalize(text) or ""
        if not text:
            continue
        if text in NOISE_LINES:
            continue
        if len(text) == 1 and text.isalpha():
            continue
        if re.fullmatch(r"\d{1,4}", text):
            continue
        blocks.append(text)
    return blocks


def split_fragments(text: str) -> list[str]:
    text = normalize(text) or ""
    if not text:
        return []
    protected = text
    protected = re.sub(r"\bOpp\.\s*t\.", "Opp§ t§", protected)
    for src, dst in PROTECTED_ABBREVIATIONS.items():
        protected = protected.replace(src, dst)
    parts = [part.replace("§", ".").strip() for part in SPLIT_RE.split(protected) if part.strip()]
    return parts or [text]


def should_continue(fragment: str, current_entry: dict[str, Any] | None) -> bool:
    if not current_entry:
        return False
    text = normalize(fragment) or ""
    if not text:
        return False
    if LEADING_CONTINUATION_RE.fullmatch(text):
        return True
    if text[0].islower():
        return True
    if text[0].isdigit():
        return True
    if text.startswith(("et ", "seq.", "seqq.", "ibid.", "id.", "not.", "alibi", "passim")):
        return True
    if text.startswith((")", "]", ",", ";", ":")):
        return True
    return False


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None]] = set()
    for match in PAGE_RE.finditer(text):
        raw = match.group(0)
        page = int(match.group(1))
        page_end = match.group(2)
        suffix = (raw[len(match.group(1)) :].strip() or "").lower()
        key = (raw, page, page_end)
        if key in seen:
            continue
        seen.add(key)
        if page_end:
            refs.append(
                {
                    "ref_kind": "editorial_range",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(page),
                    "range_end_raw": page_end,
                }
            )
            continue
        if any(token in suffix for token in ("seq", "not", "passim", "alibi")):
            ref_kind = "editorial_range"
        else:
            ref_kind = "editorial_page"
        refs.append(
            {
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    text = re.sub(
        r"^(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?(?:\s*(?:et\s+seq\.?|seqq\.?|seq\.?|not\.?|alibi\s+passim|passim))?[\s,.;:]*)+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    first_ref = PAGE_RE.search(text)
    if first_ref:
        text = text[: first_ref.start()].rstrip(" ,.;:")
    text = text.strip(" ,.;:")
    return text or None


def entry_kind(entry_raw: str, refs: list[dict[str, Any]]) -> str:
    text = normalize(entry_raw) or ""
    if not refs and re.fullmatch(r"(?:Vid\.?|Vide|Voir|V\.|cf\.?|id\.?)", text, re.IGNORECASE):
        return "cross_reference"
    if not refs and len(text) <= 24 and ROMAN_LETTER_RE.fullmatch(text):
        return "heading_group"
    return "lemma"


def first_letter(text: str | None) -> str | None:
    value = normalize(text) or ""
    for ch in value:
        if ch.isalpha():
            return ch.upper()
    return None


def discover_index_window(files: list[Path]) -> list[Path]:
    return [path for path in files if 662 <= file_seq(path) <= 696]


def build_entries(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    files = discover_index_window(discover_files(source_root))
    if not files:
        raise RuntimeError("no PG014 index files found in the expected OCR window")

    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    current_entry: dict[str, Any] | None = None
    current_letter: str | None = None
    node_lookup: dict[str, str] = {}
    entry_counter = 0

    for path in files:
        for block in extract_text_blocks(path):
            text = normalize(block) or ""
            if current_entry is not None and should_continue(text, current_entry):
                current_entry["entry_raw"] = f"{current_entry['entry_raw']} {text}".strip()
                current_entry["context_raw"] = current_entry["entry_raw"]
                current_entry["raw_json"].setdefault("continuation_fragments", []).append(text)
                continue
            refs_local = extract_page_refs(text)
            lemma_raw = lemma_from_entry(text)
            kind = entry_kind(text, refs_local)
            if kind == "cross_reference":
                lemma_raw = None
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            letter = first_letter(lemma_raw or text)
            parent_node_key = None
            if letter:
                node_key = node_lookup.get(letter)
                if node_key is None:
                    node_key = f"{VOLUME_ID}:node:letter:{letter}"
                    node_lookup[letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": letter,
                            "label_norm": letter,
                            "label_sort": sort_norm(letter),
                            "node_level": 1,
                            "confidence": 0.94,
                            "raw_json": {"source_file": str(path)},
                        }
                    )
                parent_node_key = node_lookup.get(letter)

            entry = {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": parent_node_key,
                "entry_order": len(entries) + 1,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize(lemma_raw) if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": text,
                "heading_letter": letter,
                "inferred_printed_page": refs_local[0]["page_ref_int"] if refs_local else None,
                "section_start_file": str(path),
                "editorial_anchor_file": str(path),
                "target_file_best": None,
                "confidence": 0.78 if refs_local else 0.6,
                "raw_json": {
                    "source_file": str(path),
                    "page_hints": [ref["page_ref_int"] for ref in refs_local],
                    "section_kind": "analytic_subject",
                    "fragment_source": "ocr_body",
                },
            }
            entries.append(entry)
            current_entry = entry

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS.",
        "heading_norm": "index analyticus",
        "heading_letter": None,
        "page_start": 1315,
        "page_end": 1384,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.93,
        "raw_json": {
            "section_kind_reason": "Analytic index headed INDEX ANALYTICUS; the OCR window 662-696 contains the alphabetical A-Z body and the final page 696 closes the index.",
            "observed_headings": [
                "INDEX ANALYTICUS.",
                "A",
                "B",
                "C",
                "D",
                "E",
                "F",
                "G",
                "H",
                "I",
                "J",
                "L",
                "M",
                "O",
                "P",
                "Q",
                "R",
                "S",
                "T",
                "U",
                "V",
                "X",
                "Z",
            ],
        },
    }
    helper_request = build_helper_request(entries, source_root)
    return [section], nodes, entries, helper_request


def build_helper_request(entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        if not page_hints:
            continue
        lemma_raw = entry["lemma_raw"] or entry["entry_raw"][:96]
        query_names = []
        for candidate in [lemma_raw, normalize(entry["entry_raw"].split(",", 1)[0]), normalize(entry["entry_raw"][:80])]:
            if candidate and candidate not in query_names:
                query_names.append(candidate)
        page = page_hints[0]
        helper_entries.append(
            {
                "entry_id": f"{entry['entry_key']}__r01",
                "lemma_raw": lemma_raw,
                "query_names": query_names,
                "page_hints": [str(page)],
                "page_hint_ints": [page],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in helper_output.get("entries", []) or []:
        lookup[str(item.get("entry_id"))] = item
    return lookup


def apply_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = helper_map(helper_output)
    refs: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        if not page_hints:
            continue
        helper_entries: list[str] = []
        best_target: str | None = None
        best_probability: float | None = None
        best_summary: dict[str, Any] | None = None
        helper_item = lookup.get(f"{entry['entry_key']}__r01") or {}
        best = helper_item.get("best_candidate") or {}
        if best.get("file"):
            best_target = best.get("file")
            best_probability = best.get("probability")
            best_summary = best
        for idx, ref in enumerate(page_hints, start=1):
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(ref),
                    "page_ref_raw": str(ref),
                    "page_ref_int": int(ref),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": best_target,
                    "target_file_probability": best_probability,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.89 if best_target else 0.64,
                    "raw_json": {
                        "helper_entry_id": f"{entry['entry_key']}__r01",
                        "helper_result": helper_item,
                    },
                }
            )
        helper_entries.append(f"{entry['entry_key']}__r01")
        entry["target_file_best"] = best_target
        entry["confidence"] = 0.91 if best_target else 0.68
        entry["raw_json"]["helper_entry_ids"] = helper_entries
        entry["raw_json"]["helper_best_candidate"] = best_summary
    return entries, refs


def build_payload(section: dict[str, Any], nodes: list[dict[str, Any]], entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any], source_root: Path) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the analytic index entries from files 662-696; files 659-661 are introductory matter and were excluded from the final entry list. Page-break continuations were merged conservatively and the helper was used to resolve target files.",
            "evidence_files": [
                str(source_root / "85bfbc12-d097-4c32-b619-363e2b585462-662.txt"),
                str(source_root / "85bfbc12-d097-4c32-b619-363e2b585462-668.txt"),
                str(source_root / "85bfbc12-d097-4c32-b619-363e2b585462-675.txt"),
                str(source_root / "85bfbc12-d097-4c32-b619-363e2b585462-690.txt"),
                str(source_root / "85bfbc12-d097-4c32-b619-363e2b585462-696.txt"),
            ],
        },
        "notes": [
            {
                "note_type": "section_scope",
                "text": "PG014 has a false-positive INDEX ANALYTICUS header in the 659-661 run-up, but the actual index body begins at 662.",
            },
            {
                "note_type": "helper_output",
                "source": str(DEFAULT_HELPER_OUTPUT_JSON),
                "status": helper_output.get("status"),
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PG014 alphabetical payload.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    intermediate_dir: Path = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    sections, nodes, entries, helper_request = build_entries(args.source_root)
    write_json(args.helper_request_json, helper_request)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries_draft.json", entries)
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PG014 analytic index targets and finalize the payload.",
            "completed": [
                "section boundaries recovered",
                "draft entries and helper request prepared",
            ],
            "pending": [
                "run index_target_locator helper",
                "attach helper evidence to entries and refs",
                "write final payload and validate",
            ],
            "blocked": [],
            "notes": [
                "The OCR tail from 659-661 is introductory material and was excluded from the entry list.",
                "Entries are split conservatively on sentence boundaries and page-break continuations are merged when the fragment begins with digits or lowercase text.",
            ],
        },
    )

    helper_output = run_helper(args.helper_request_json, args.helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}
    write_json(intermediate_dir / "helper_output.json", helper_output)
    entries, refs = apply_helper(entries, helper_output)

    payload = build_payload(sections[0], nodes, entries, refs, helper_output, args.source_root)
    write_json(args.output_file, payload)
    write_json(intermediate_dir / "final_payload.json", payload)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload written and ready for validation/import.",
            "completed": [
                "section boundaries recovered",
                "draft entries and helper request prepared",
                "helper-assisted refs assembled",
                "final payload written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Target files are attached per ref from the helper output.",
                "The section is treated as a single analytic_subject block headed INDEX ANALYTICUS.",
            ],
        },
    )


if __name__ == "__main__":
    main()

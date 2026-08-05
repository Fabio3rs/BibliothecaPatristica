#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG042_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG042/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG042_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG042_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG042 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG042_alphabetical_indices.json

Build the PG042 alphabetical-index payload from the OCR tail that contains the
analytical index and the closing ORDO RERUM table. The script also writes the
helper request/output checkpoints used during extraction.
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG042"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 42"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG042/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG042_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG042_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG042_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG042"
DEFAULT_TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

ANALYTICAL_START_SEQ = 774
ANALYTICAL_END_SEQ = 787
ORDO_START_SEQ = 788
ORDO_END_SEQ = 788

HEADER_RE = re.compile(
    r"^(?:\d{1,4}\s+)?(?:INDEX ANALYTICUS(?:\.|$)|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)"
    r"(?:\s+\d{1,4})?$",
    re.IGNORECASE,
)
SECTION_2_RE = re.compile(r"^(?:ORDO RERUM|QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.|ORDO RERUM QU[ÆAE] IN HOC TOMO CONTINENTUR\.)$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒΑ-Ω])")
WEAK_LINE_RE = re.compile(
    r"^(?:INDEX ANALYTICUS|IN DUOS PRIORES TOMOS|Paginæ notantur editionis Dionysii Petavii quas typis grandioribus in nostra expressimus\.|Digitized by Google|ORDO RERUM|QU[ÆAE] IN HOC TOMO CONTINENTUR\.|FINIS TOMI QUADRAGESIMI SECUNDI\.|Parisiis\. — Ex Typis J\.-P\. MIGNE\.)$",
    re.IGNORECASE,
)
WS_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = value.replace("\u00ad", "")
    value = WS_RE.sub(" ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = WS_RE.sub(" ", value).strip().lower()
    return value or None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if not match:
            continue
        files.append((int(match.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(match.group(1))


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", raw, flags=re.S | re.I):
        attrs = match.group("attrs") or ""
        kind_match = re.search(r'tipo="([^"]+)"', attrs, flags=re.I)
        kind = kind_match.group(1).strip().lower() if kind_match else ""
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        if kind:
            blocks.append((kind, body))
    return blocks


def header_numbers(path: Path) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for kind, body in extract_blocks(path):
        if kind != "cabecalho":
            continue
        line = normalize(body) or ""
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", line):
            num = int(match.group(1))
            if num not in seen:
                seen.add(num)
                numbers.append(num)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for number in header_numbers(path):
            page_map.setdefault(number, str(path))
    return page_map


def split_sentences(text: str) -> list[str]:
    text = normalize(text) or ""
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part and part.strip()]
    if not parts:
        return [text]
    merged: list[str] = []
    for part in parts:
        if merged and not PAGE_REF_RE.search(part) and not re.match(r"^[A-ZÆŒ][a-zæœ]", part):
            merged[-1] = f"{merged[-1]} {part}".strip()
            continue
        merged.append(part)
    return merged


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"start": start, "end": end, "raw": match.group(0).strip()})
    return refs


def lemma_from_entry(text: str) -> str | None:
    working = normalize(text) or ""
    if not working:
        return None
    if "," in working:
        working = working.split(",", 1)[0].strip()
    elif "." in working and len(working.split(".", 1)[0].split()) <= 4:
        working = working.split(".", 1)[0].strip()
    working = working.rstrip(" .;:")
    return working or None


def is_boilerplate(text: str) -> bool:
    return bool(WEAK_LINE_RE.fullmatch(text.strip()))


def make_query_names(lemma_raw: str) -> list[str]:
    variants = [
        lemma_raw,
        lemma_raw.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe"),
        lemma_raw.replace("—", " "),
        lemma_raw.replace("–", " "),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        value = normalize(item)
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped[:4]


def build_helper_request(entries: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, item in enumerate(entries, start=1):
        page_refs = item["page_refs"]
        if not page_refs:
            continue
        should_include = (
            idx <= 60
            or any(ref["end"] is not None for ref in page_refs)
            or idx % 250 == 0
        )
        if not should_include:
            continue
        page_hints = []
        page_hint_ints = []
        for ref in page_refs[:2]:
            if ref["start"] not in page_hint_ints:
                page_hints.append(str(ref["start"]))
                page_hint_ints.append(ref["start"])
        lemma_raw = item.get("lemma_raw") or item["entry_raw"][:120]
        helper_entries.append(
            {
                "entry_id": f"pg042_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": make_query_names(lemma_raw),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": item["entry_raw"][:240],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
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
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")


def load_helper_summary(helper_request_json: Path, helper_output_json: Path) -> dict[str, dict[str, Any]]:
    request = read_json(helper_request_json, {})
    output = read_json(helper_output_json, {})
    request_entries = {item.get("entry_id"): item for item in request.get("entries", []) if item.get("entry_id")}
    summary: dict[str, dict[str, Any]] = {}
    for item in output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        candidates = item.get("candidates") or []
        best = candidates[0] if candidates else (item.get("best_candidate") or {})
        top_candidates = []
        for candidate in candidates[:3]:
            top_candidates.append(
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
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "inferred_printed_page": best.get("inferred_printed_page"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in best.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            },
            "top_candidates": top_candidates,
        }
    return summary


def update_todo(intermediate_dir: Path, current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": current_focus,
            "completed": completed,
            "pending": pending,
            "blocked": blocked,
            "notes": notes,
        },
    )


def collect_items(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    files = discover_files(source_root)
    selected = [path for path in files if ANALYTICAL_START_SEQ <= file_seq(path) <= ORDO_END_SEQ]
    if not selected:
        raise SystemExit("No OCR files found in the requested extraction window.")
    page_map = build_page_map(files)

    items: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    node_by_letter: dict[str, str] = {}
    current_letter: str | None = None
    entry_order = 0

    for path in selected:
        for kind, body in extract_blocks(path):
            lines = [normalize(line) for line in body.splitlines()]
            lines = [line for line in lines if line]
            for line in lines:
                if kind == "nota_marginal" and LETTER_RE.fullmatch(line.rstrip(".")):
                    letter = line.rstrip(".")
                    if letter not in node_by_letter:
                        node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:node:{len(node_by_letter)+1:04d}"
                        node_by_letter[letter] = node_key
                        nodes.append(
                            {
                                "node_key": node_key,
                                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                                "parent_node_key": None,
                                "node_order": len(node_by_letter),
                                "node_kind": "letter_group",
                                "label_raw": letter,
                                "label_norm": letter.lower(),
                                "label_sort": letter.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source": "standalone letter heading", "source_file": str(path)},
                            }
                        )
                    current_letter = letter
                    continue
                if kind == "cabecalho" and HEADER_RE.fullmatch(line):
                    continue
                if kind == "texto_principal" and is_boilerplate(line):
                    continue
                if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Z"}:
                    if line not in node_by_letter:
                        node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:node:{len(node_by_letter)+1:04d}"
                        node_by_letter[line] = node_key
                        nodes.append(
                            {
                                "node_key": node_key,
                                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                                "parent_node_key": None,
                                "node_order": len(node_by_letter),
                                "node_kind": "letter_group",
                                "label_raw": line,
                                "label_norm": line.lower(),
                                "label_sort": line.lower(),
                                "node_level": 1,
                                "confidence": 0.99,
                                "raw_json": {"source": "standalone letter heading", "source_file": str(path)},
                            }
                        )
                    current_letter = line
                    continue
                for fragment in split_sentences(line):
                    if not fragment or is_boilerplate(fragment):
                        continue
                    fragment_norm = normalize(fragment) or ""
                    if fragment_norm.upper() in {
                        "INDEX ANALYTICUS",
                        "INDEX ANALYTICUS.",
                        "ORDO RERUM",
                        "ORDO RERUM.",
                        "QUÆ IN HOC TOMO CONTINENTUR.",
                        "QUAE IN HOC TOMO CONTINENTUR.",
                    }:
                        continue
                    if fragment_norm.lower().startswith("index analyticus"):
                        continue
                    page_refs = extract_page_refs(fragment)
                    if not page_refs and not re.match(r"^(?:Vide|Vid\.|voir|cf\.|id\.)", fragment, flags=re.IGNORECASE):
                        continue
                    entry_order += 1
                    lemma_raw = lemma_from_entry(fragment)
                    entry_id = f"pg042_{entry_order:04d}"
                    items.append(
                        {
                            "entry_id": entry_id,
                            "entry_key": f"{VOLUME_ID}:alpha:analytic_subject:001:entry:{entry_order:04d}",
                            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                            "parent_node_key": node_by_letter.get(current_letter or ""),
                            "entry_order": entry_order,
                            "lemma_raw": lemma_raw,
                            "entry_raw": fragment,
                            "page_refs": page_refs,
                            "heading_letter": current_letter,
                            "source_file": str(path),
                        }
                    )
    meta = {
        "selected_files": [str(path) for path in selected],
        "section_start_file": str(selected[0]),
        "section_end_file": str(selected[-1]),
        "page_map": page_map,
    }
    return items, nodes, meta


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    items, nodes, meta = collect_items(source_root)
    helper_summary = load_helper_summary(helper_request_json, helper_output_json)
    page_map: dict[int, str] = meta["page_map"]

    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS.",
            "heading_norm": "index analyticus",
            "heading_letter": None,
            "page_start": 1103,
            "page_end": 1126,
            "file_start": meta["section_start_file"],
            "file_end": meta["selected_files"][-2] if len(meta["selected_files"]) > 1 else meta["section_end_file"],
            "confidence": 0.9,
            "raw_json": {
                "section_kind_reason": "Main analytical index of Epiphanius material, running through the A-Z entries in the selected OCR tail before the contents table begins.",
                "source_window": [meta["section_start_file"], meta["section_end_file"]],
                "boundary_notes": [
                    "The index begins on file 774 after the prefatory note and closes before the ORDO RERUM table on file 788.",
                ],
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1127,
            "page_end": 1128,
            "file_start": meta["section_end_file"],
            "file_end": meta["section_end_file"],
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial contents table / closure section, distinct from the alphabetical index entries.",
                "source_window": [meta["section_end_file"]],
            },
        },
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for item in items:
        page_refs = item["page_refs"]
        helper_id = item["entry_id"] if item["entry_id"] in helper_summary else None
        helper_note = helper_summary.get(item["entry_id"])
        first_ref = page_refs[0]["start"] if page_refs else None
        target_file_best = page_map.get(first_ref) if first_ref is not None else None
        if target_file_best is None and helper_note and helper_note.get("best_candidate", {}).get("file"):
            target_file_best = helper_note["best_candidate"]["file"]
        if target_file_best is None:
            target_file_best = item["source_file"]

        lemma_raw = item["lemma_raw"] or (item["entry_raw"].split(",", 1)[0].strip() if "," in item["entry_raw"] else item["entry_raw"])
        entry_kind = "lemma"
        if not page_refs and re.match(r"^(?:Vide|Vid\.|voir|cf\.|id\.)", item["entry_raw"], flags=re.IGNORECASE):
            entry_kind = "cross_reference"
            lemma_raw = None

        entry = {
            "entry_key": item["entry_key"],
            "section_key": item["section_key"],
            "parent_node_key": item["parent_node_key"],
            "entry_order": item["entry_order"],
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": item["entry_raw"],
            "context_raw": None,
            "heading_letter": item["heading_letter"],
            "inferred_printed_page": first_ref,
            "section_start_file": meta["section_start_file"],
            "editorial_anchor_file": item["source_file"],
            "target_file_best": target_file_best,
            "confidence": 0.9 if page_refs else 0.72,
            "raw_json": {
                "source_file": item["source_file"],
                "section_kind": "analytic_subject",
                "entry_kind_reason": "Sentence-like OCR fragment split conservatively from the analytical index tail.",
                "page_refs": page_refs,
                "helper_entry_id": helper_id,
                "helper_summary": helper_note,
            },
        }
        entries.append(entry)

        for ref_order, ref in enumerate(page_refs, start=1):
            target_file = page_map.get(ref["start"]) or target_file_best
            refs.append(
                {
                    "entry_key": item["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": "editorial_range" if ref["end"] is not None else "editorial_page",
                    "ref_raw": ref["raw"],
                    "page_ref_raw": ref["raw"],
                    "page_ref_int": ref["start"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(ref["start"]) if ref["end"] is not None else None,
                    "range_end_raw": str(ref["end"]) if ref["end"] is not None else None,
                    "target_file": target_file,
                    "target_file_probability": 0.98 if target_file != item["source_file"] else 0.55,
                    "section_start_file": meta["section_start_file"],
                    "editorial_anchor_file": item["source_file"],
                    "confidence": 0.94 if target_file != item["source_file"] else 0.72,
                    "raw_json": {
                        "source_file": item["source_file"],
                        "helper_entry_id": helper_id,
                        "helper_summary": helper_note,
                    },
                }
            )

    notes = [
        {
            "kind": "section_boundary",
            "message": "PG042 analytical index runs through files 774-787; file 788 contains the editorial ORDO RERUM closure.",
        },
        {
            "kind": "extraction_method",
            "message": "Entries were segmented conservatively from OCR sentence-like fragments, with standalone letter headings preserved as nodes.",
        },
    ]

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the analytical index tail and separated the ORDO RERUM closure, but the ORDO table itself was not serialized as ordinary lemma entries.",
        "evidence_files": meta["selected_files"],
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "PG042 ends with an `INDEX ANALYTICUS` block followed by `ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR`.",
            "OCR file suffixes and printed pages were kept separate; the index uses printed pages 1103-1128 while the OCR tail lives in files 774-788.",
        ],
    }

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "generated_at": now_iso(),
        "current_focus": "PG042 alphabetical-index extraction",
        "completed": ["OCR tail inspected", "helper request generated", "helper output resolved", "final payload assembled"],
        "pending": [],
        "blocked": [],
        "notes": [
            "Helper evidence is kept in raw_json; exact target files are derived from the printed-page map whenever available.",
        ],
    }

    write_json(intermediate_dir / "manifest.json", manifest)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    update_todo(
        intermediate_dir,
        "Finalize PG042 alphabetical payload and validate generated refs",
        ["OCR tail inspected", "helper request generated and resolved", "intermediate fragments written"],
        [],
        [],
        ["Keep OCR literals intact; page 788 is editorial closure only.", "Use exact printed-page map rather than OCR file suffixes for refs."],
    )

    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PG042 alphabetical-index payload.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    parser.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    parser.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    args = parser.parse_args()

    items, _, _ = collect_items(args.source_root)
    helper_request = build_helper_request(items, args.source_root)
    write_json(args.helper_request_json, helper_request)
    run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

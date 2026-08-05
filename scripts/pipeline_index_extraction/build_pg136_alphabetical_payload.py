#!/usr/bin/env python3
"""Usage: build the PG136 alphabetical payload from OCR and helper output.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg136_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG136/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG136_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG136_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG136 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG136_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG136"
COLLECTION = "PG"
VOLUME_LABEL = "PG136"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG136/text"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG136_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG136_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG136"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG136_alphabetical_indices.json"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION1_FILE_SEQ = 11
SECTION2_FILE_SEQS = [708, 709, 710, 711]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def strip_accents(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse suffix from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def find_file_by_seq(source_root: Path, seq: int) -> Path:
    for path in discover_files(source_root):
        if file_seq(path) == seq:
            return path
    raise FileNotFoundError(f"could not find file seq {seq} in {source_root}")


def parse_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = read_text(path)
    for match in re.finditer(r"<bloco[^>]*>(.*?)</bloco>", raw, flags=re.S):
        block = match.group(1)
        for raw_line in block.splitlines():
            line = normalize(raw_line)
            if line:
                lines.append(line)
    return lines


def is_heading_noise(line: str) -> bool:
    upper = line.upper()
    if upper.startswith("SÆCULUM XII, ANNI"):
        return True
    return upper in {
        "TRADITIO CATHOLICA.",
        "SÆCULUM XII, ANNI 1190-1200.",
        "-",
        "-----",
        "-----◊◊-----",
        "FINIS TOMI CENTESIMI TRICESIMI SEXTI.",
        "PARISIIS. — EX TYPIS J.-P. MIGNE.",
    }


def is_section1_heading(line: str) -> bool:
    upper = line.upper()
    return upper in {
        "ELENCHUS",
        "AUCTORUM ET OPERUM QUI IN HOC TOMO CXXXVI CONTINENTUR.",
        "EUSTATHIUS THESSALONICENSIS METROPOLITA.",
        "ANTONIUS MELISSA.",
    }


def is_section2_noise(line: str) -> bool:
    upper = line.upper()
    return upper in {
        "ORDO RERUM",
        "QUÆ IN HOC TOMO CONTINENTUR.",
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    } or re.fullmatch(r"\d{4}\s+ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?\s+\d{4}", upper) is not None


def is_letter_heading(line: str) -> bool:
    return bool(re.fullmatch(r"[RSTV]", line))


def is_part_heading(line: str) -> bool:
    return bool(re.fullmatch(r"PARS\s+[IVXLC]+\.?", line.upper()))


def extract_ref_tokens(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in re.finditer(r"(?P<col>[ab])?\s*(?P<num>\d{1,4})", text, flags=re.IGNORECASE):
        raw = match.group(0).strip()
        num = int(match.group("num"))
        col = match.group("col")
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": num,
                "page_ref_col": col.lower() if col else None,
            }
        )
    return refs


def infer_lemma(line: str) -> str | None:
    value = normalize(line) or ""
    if not value:
        return None
    if value.upper().startswith("PARS "):
        return None
    if "Vide" in value:
        value = value.split("Vide", 1)[0]
    ref_match = re.search(r"(?<!\d)(?:[ab]\s*)?\d{1,4}(?!\d)", value, flags=re.IGNORECASE)
    if ref_match:
        value = value[: ref_match.start()]
    value = value.rstrip(" ,;:.—-")
    if not value:
        return None
    return value


@dataclass
class ParsedEntry:
    entry_id: str
    section_key: str
    parent_node_key: str | None
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str | None
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str
    editorial_anchor_file: str
    target_file_best: str
    confidence: float
    raw_json: dict[str, Any]


def make_entry(
    *,
    entry_id: str,
    section_key: str,
    parent_node_key: str | None,
    entry_order: int,
    entry_kind: str,
    line: str,
    heading_letter: str | None,
    source_file: Path,
    section_start_file: Path,
    helper_best: dict[str, Any] | None,
    line_number: int,
    node_label: str | None = None,
) -> ParsedEntry:
    lemma_raw = infer_lemma(line)
    lemma_display = lemma_raw
    lemma_norm = sort_norm(lemma_raw)
    lemma_sort = lemma_norm
    refs = extract_ref_tokens(line)
    helper_status = (helper_best or {}).get("status")
    best_candidate = (helper_best or {}).get("best_candidate") or {}
    candidate_summaries = [
        {
            "file": cand.get("file"),
            "probability": cand.get("probability"),
            "candidate_role": cand.get("candidate_role"),
            "evidence_kinds": [e.get("kind") for e in cand.get("evidence", [])[:6]],
        }
        for cand in (helper_best or {}).get("candidates", [])[:5]
    ]
    raw_json = {
        "source_file": str(source_file),
        "line_number": line_number,
        "section_kind": "author_index" if section_key.endswith("author_index:001") else "ordo_rerum",
        "page_ref_tokens": [ref["page_ref_raw"] for ref in refs],
        "helper_status": helper_status,
        "helper_best_candidate": {
            "file": best_candidate.get("file"),
            "probability": best_candidate.get("probability"),
            "candidate_role": best_candidate.get("candidate_role"),
            "reason_summary": best_candidate.get("reason_summary"),
            "evidence_kinds": [e.get("kind") for e in best_candidate.get("evidence", [])[:6]],
        }
        if best_candidate
        else None,
        "helper_top_candidates": candidate_summaries or None,
        "node_label": node_label,
    }
    inferred_printed_page = None
    if refs:
        inferred_printed_page = refs[0]["page_ref_int"]
    return ParsedEntry(
        entry_id=entry_id,
        section_key=section_key,
        parent_node_key=parent_node_key,
        entry_order=entry_order,
        entry_kind=entry_kind,
        lemma_raw=lemma_raw,
        lemma_display=lemma_display,
        lemma_norm=lemma_norm,
        lemma_sort=lemma_sort,
        entry_raw=line,
        context_raw=None,
        heading_letter=heading_letter,
        inferred_printed_page=inferred_printed_page,
        section_start_file=str(section_start_file),
        editorial_anchor_file=str(source_file),
        target_file_best=str(source_file),
        confidence=0.82 if refs else 0.72,
        raw_json=raw_json,
    )


def build_section1(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    file_011 = find_file_by_seq(source_root, SECTION1_FILE_SEQ)
    lines = parse_lines(file_011)
    section_key = f"{VOLUME_ID}:alpha:author_index:001"
    nodes: list[dict[str, Any]] = []
    entries: list[ParsedEntry] = []
    helper_seed: list[dict[str, Any]] = []
    current_node_key: str | None = None
    current_node_label: str | None = None
    node_order = 0
    entry_order = 0
    # Section 1 is structurally simple: one opening heading group for Eustathius
    # and one for Antonius Melissa.
    for idx, line in enumerate(lines, start=1):
        if is_heading_noise(line):
            continue
        if line in {"ELENCHUS", "AUCTORUM ET OPERUM QUI IN HOC TOMO CXXXVI CONTINENTUR."}:
            continue
        if line in {"EUSTATHIUS THESSALONICENSIS METROPOLITA.", "ANTONIUS MELISSA."}:
            node_order += 1
            current_node_key = f"{VOLUME_ID}:node:author_index:{node_order:03d}"
            current_node_label = line
            nodes.append(
                {
                    "node_key": current_node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "heading_group",
                    "label_raw": line,
                    "label_norm": sort_norm(line),
                    "label_sort": sort_norm(line),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {
                        "source_file": str(file_011),
                        "note": "Explicit macro-heading in the ELENCHUS.",
                    },
                }
            )
            continue
        if not extract_ref_tokens(line):
            continue
        entry_order += 1
        entry_id = f"pg136_author_{entry_order:03d}"
        entry = make_entry(
            entry_id=entry_id,
            section_key=section_key,
            parent_node_key=current_node_key,
            entry_order=entry_order,
            entry_kind="lemma" if "Vide" not in line else "cross_reference",
            line=line,
            heading_letter=None,
            source_file=file_011,
            section_start_file=file_011,
            helper_best=None,
            line_number=idx,
            node_label=current_node_label,
        )
        entries.append(entry)
        helper_seed.append(
            {
                "entry_id": entry_id,
                "lemma_raw": entry.lemma_raw or line,
                "query_names": [
                    entry.lemma_raw or line,
                    sort_norm(entry.lemma_raw or line) or entry.lemma_raw or line,
                ],
                "page_hints": [str(ref["page_ref_int"]) for ref in extract_ref_tokens(line)],
                "page_hint_ints": [ref["page_ref_int"] for ref in extract_ref_tokens(line)],
                "context_raw": line,
            }
        )
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "author_index",
        "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CXXXVI CONTINENTUR.",
        "heading_norm": "elenchus auctorum et operum qui in hoc tomo cxxxvi continentur",
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(file_011),
        "file_end": str(file_011),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Opening ELENCHUS of authors and works; editorially an author index even though the heading is not INDEX AUCTORUM.",
            "evidence_files": [str(file_011)],
        },
    }
    return [
        section
    ], nodes, [
        {
            "entry_key": e.entry_id,
            **e.__dict__,
        }
        for e in entries
    ], helper_seed, [file_011]


def build_section2(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    files = [find_file_by_seq(source_root, seq) for seq in SECTION2_FILE_SEQS]
    section_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"
    nodes: list[dict[str, Any]] = []
    entries: list[ParsedEntry] = []
    helper_seed: list[dict[str, Any]] = []
    node_order = 0
    entry_order = 0
    current_node_key: str | None = None
    current_node_label: str | None = None
    current_letter: str | None = None
    current_parent_node_key: str | None = None
    have_seen_letter = False
    for file_index, path in enumerate(files):
        lines = parse_lines(path)
        for line_number, line in enumerate(lines, start=1):
            if is_heading_noise(line) or is_section2_noise(line):
                continue
            if line in {"S", "T", "V"}:
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:ordo_rerum:{node_order:03d}"
                current_node_label = line
                current_letter = line
                current_parent_node_key = current_node_key
                have_seen_letter = True
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
                        "confidence": 0.99,
                        "raw_json": {
                            "source_file": str(path),
                            "note": "Explicit single-letter grouping in the ORDO RERUM index.",
                        },
                    }
                )
                continue
            if is_part_heading(line):
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:ordo_rerum:{node_order:03d}"
                current_node_label = line
                current_parent_node_key = current_node_key
                current_letter = None
                have_seen_letter = True
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "ordinal_group",
                        "label_raw": line,
                        "label_norm": sort_norm(line),
                        "label_sort": sort_norm(line),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {
                            "source_file": str(path),
                            "note": "Explicit PARS division in the Antonii Melissa contents table.",
                        },
                    }
                )
                continue
            if line == "ORDO RERUM":
                # Structural repetition between the topical index and the contents table.
                continue
            if not have_seen_letter and re.match(r"^[A-Z][a-z]", line):
                # The first run in this section starts with R-entries before the explicit S divider.
                node_order += 1
                current_node_key = f"{VOLUME_ID}:node:ordo_rerum:{node_order:03d}"
                current_node_label = "R"
                current_letter = "R"
                current_parent_node_key = current_node_key
                have_seen_letter = True
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": "R",
                        "label_norm": "r",
                        "label_sort": "r",
                        "node_level": 1,
                        "confidence": 0.85,
                        "raw_json": {
                            "source_file": str(path),
                            "note": "Inferred initial alphabetical group from the first run of R-entries before the explicit S divider.",
                        },
                    }
                )
            if not extract_ref_tokens(line) and "Vide" not in line:
                continue
            entry_order += 1
            entry_id = f"pg136_ordo_{entry_order:03d}"
            entry_kind = "cross_reference" if "Vide" in line and not extract_ref_tokens(line) else "lemma"
            entry = make_entry(
                entry_id=entry_id,
                section_key=section_key,
                parent_node_key=current_parent_node_key,
                entry_order=entry_order,
                entry_kind=entry_kind,
                line=line,
                heading_letter=current_letter,
                source_file=path,
                section_start_file=files[0],
                helper_best=None,
                line_number=line_number,
                node_label=current_node_label,
            )
            entries.append(entry)
            page_refs = extract_ref_tokens(line)
            helper_seed.append(
                {
                    "entry_id": entry_id,
                    "lemma_raw": entry.lemma_raw or line,
                    "query_names": [
                        entry.lemma_raw or line,
                        sort_norm(entry.lemma_raw or line) or entry.lemma_raw or line,
                    ],
                    "page_hints": [str(ref["page_ref_int"]) for ref in page_refs],
                    "page_hint_ints": [ref["page_ref_int"] for ref in page_refs],
                    "context_raw": line,
                }
            )
    section = {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1337,
        "page_end": 1344,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Closing ORDO RERUM contents table for the tomus, separate from the opening ELENCHUS.",
            "evidence_files": [str(files[0]), str(files[-1])],
        },
    }
    return [
        section
    ], nodes, [
        {
            "entry_key": e.entry_id,
            **e.__dict__,
        }
        for e in entries
    ], helper_seed, files


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
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {"status": "empty", "entries": []})


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = {item["entry_id"]: item for item in helper_output.get("entries", [])}
    for entry in entries:
        helper = by_id.get(entry["entry_key"].replace(":entry:", "_") if False else None)
        # The helper request uses its own entry_id namespace. Map via raw_json when possible.
    # Use the helper output entries in the same order as the helper request seed.


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    sections1, nodes1, entries1, helper1, files1 = build_section1(source_root)
    sections2, nodes2, entries2, helper2, files2 = build_section2(source_root)

    entries = entries1 + entries2
    helper_seed = helper1 + helper2
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_seed,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_seed else {"status": "empty", "entries": []}

    helper_map = {item.get("entry_id"): item for item in helper_output.get("entries", [])}
    helper_output_entries = helper_output.get("entries", [])
    for entry, helper_item in zip(entries, helper_output_entries):
        entry["raw_json"]["helper"] = helper_item
        best = helper_item.get("best_candidate") or {}
        if best.get("file"):
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")
    # Material references inherit the helper-best target file when present.
    refs: list[dict[str, Any]] = []
    entry_ref_index: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        page_refs = extract_ref_tokens(entry["entry_raw"])
        if not page_refs:
            continue
        helper_item = entry["raw_json"].get("helper") or {}
        best = helper_item.get("best_candidate") or {}
        for ref_order, ref_token in enumerate(page_refs, start=1):
            ref_kind = "editorial_page_column" if ref_token["page_ref_col"] else "editorial_page"
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": ref_kind,
                    "ref_raw": ref_token["ref_raw"],
                    "page_ref_raw": ref_token["page_ref_raw"],
                    "page_ref_int": ref_token["page_ref_int"],
                    "page_ref_col": ref_token["page_ref_col"],
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": best.get("file"),
                    "target_file_probability": best.get("probability"),
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.72 if best.get("file") else 0.45,
                    "raw_json": {
                        "helper_status": helper_item.get("status"),
                        "helper_best_candidate": best,
                        "helper_top_candidates": helper_item.get("candidates", [])[:5],
                    },
                }
            )
    # Re-key entries into the final structure expected by the importer.
    final_entries = []
    for entry in entries:
        final_entries.append(
            {
                "entry_key": entry["entry_key"],
                "section_key": entry["section_key"],
                "parent_node_key": entry["parent_node_key"],
                "entry_order": entry["entry_order"],
                "entry_kind": entry["entry_kind"],
                "lemma_raw": entry["lemma_raw"],
                "lemma_display": entry["lemma_display"],
                "lemma_norm": entry["lemma_norm"],
                "lemma_sort": entry["lemma_sort"],
                "entry_raw": entry["entry_raw"],
                "context_raw": entry["context_raw"],
                "heading_letter": entry["heading_letter"],
                "inferred_printed_page": entry["inferred_printed_page"],
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "target_file_best": entry["target_file_best"],
                "confidence": entry["confidence"],
                "raw_json": entry["raw_json"],
            }
        )

    sections = sections1 + sections2
    nodes = nodes1 + nodes2
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "PG136 contains an opening ELENCHUS of authors/works and a closing ORDO RERUM contents table for Antonii Melissa.",
            "OCR file suffixes, printed pages, and cited pages were kept separate.",
        ],
    }
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the ELENCHUS and the closing ORDO RERUM from OCR and anchored the cited pages with the local helper/page map.",
        "evidence_files": [str(files1[0]), str(files2[0]), str(files2[-1])],
    }
    notes = [
        "The front matter ELENCHUS is editorially an author/work index even though it is not headed INDEX AUCTORUM.",
        "The closing ORDO RERUM is a contents table for the tome and was serialized separately from the opening ELENCHUS.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": final_entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", final_entries)
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
            "current_focus": "Finalize PG136 alphabetical payload and verify helper anchors against OCR.",
            "completed": [
                "identified ELENCHUS and ORDO RERUM sections",
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
    ap = argparse.ArgumentParser(description="Build the PG136 alphabetical payload.")
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

#!/usr/bin/env python3
"""Usage: build the PG009 alphabetical payload from OCR index pages.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG009_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG009/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG009_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG009_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG009 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG009_alphabetical_indices.json
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

from patristica_pipeline.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG009"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 9"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG009/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG009_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG009_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG009_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG009"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:foreign_terms:001",
        "section_order": 1,
        "section_kind": "foreign_terms",
        "heading_raw": "INDEX GRÆCITATIS.",
        "heading_norm": "index graecitatis",
        "heading_letter": None,
        "page_start": 1545,
        "page_end": 1548,
        "file_start_seq": 777,
        "file_end_seq": 778,
        "section_kind_reason": "Greek alphabetical index of terms and locutions.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:002",
        "section_order": 2,
        "section_kind": "author_index",
        "heading_raw": "INDEX AUCTORUM A CLEMENTE ALEX. CITATORUM.",
        "heading_norm": "index auctorum a clemente alex citatorum",
        "heading_letter": None,
        "page_start": 1349,
        "page_end": 1556,
        "file_start_seq": 779,
        "file_end_seq": 782,
        "section_kind_reason": "Author index of writers cited by Clement of Alexandria, with mixed Latin and Greek entries.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:003",
        "section_order": 3,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM MEMORABILIUM.",
        "heading_norm": "index rerum memorabilium",
        "heading_letter": None,
        "page_start": 1561,
        "page_end": 1564,
        "file_start_seq": 783,
        "file_end_seq": 785,
        "section_kind_reason": "Analytical subject index of memorable things and topics.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:004",
        "section_order": 4,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1681,
        "page_end": 1696,
        "file_start_seq": 845,
        "file_end_seq": 852,
        "section_kind_reason": "Editorial table of contents at the end of the volume.",
    },
]

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à|,|;)\s*(\d{1,4}))?")
NOISE_EXACT = {
    "Digitized by Google",
    "INDEX GRÆCITATIS.",
    "INDEX AUCTORUM",
    "INDEX RERUM",
    "INDEX RERUM MEMORABILIUM.",
    "ORDO RERUM",
    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
}
NOISE_PREFIXES = (
    "A CLEMENTE ALEX.",
    "A CLEMENTE ALEXANDRINO CITATORUM.",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
)
DIVIDER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]$")
LATIN_LEMMA_RE = re.compile(r"^[A-ZÆŒΑ-Ω][^0-9]*")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            continue
        files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file seq from {path}")
    return int(m.group(1))


def page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for token in HEADER_PAGE_RE.findall(header):
            page = int(token)
            if 0 < page < 10000:
                mapping.setdefault(page, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            mapping.setdefault(page, target)
    return mapping


def clean_lines(text: str | None) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = normalize(raw)
        if not line:
            continue
        if line in NOISE_EXACT:
            continue
        if any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        lines.append(line)
    return lines


def select_files(files: list[Path], seqs: set[int]) -> list[Path]:
    return [path for path in files if file_seq(path) in seqs]


def should_merge(prev: str, current: str) -> bool:
    if not prev:
        return False
    if current.startswith(("—", "-", ";", ",", ".", ")", "]")):
        return True
    if current and current[0].islower():
        return True
    if current.startswith(("ἐ", "ὑ", "ὦ", "ὅ", "ὁ", "ὡ", "ἀ", "ἁ", "ἄ", "ἔ", "ἦ", "ἰ", "ο", "ω", "υ", "φ", "ψ", "χ", "τ", "ρ", "σ", "π", "κ", "μ", "ν", "λ")):
        return True
    if prev.endswith(("-", "—")):
        return True
    return False


def collect_fragments(files: list[Path]) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    buffer: dict[str, Any] | None = None
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        body = parsed.get("body_text") or ""
        for line_no, raw_line in enumerate(clean_lines(body), start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line in NOISE_EXACT or any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
                continue
            if DIVIDER_RE.fullmatch(line):
                if buffer:
                    fragments.append(buffer)
                    buffer = None
                fragments.append({"kind": "divider", "file": str(path), "line_no": line_no, "text": line})
                continue
            if buffer and should_merge(buffer["text"], line):
                buffer["text"] = f"{buffer['text']} {line}".strip()
                buffer["end_line_no"] = line_no
                buffer["end_file"] = str(path)
                continue
            if buffer:
                fragments.append(buffer)
            buffer = {
                "kind": "entry",
                "file": str(path),
                "line_no": line_no,
                "end_line_no": line_no,
                "end_file": str(path),
                "text": line,
            }
    if buffer:
        fragments.append(buffer)
    return fragments


def infer_lemma(entry_raw: str, section_kind: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if section_kind == "ordo_rerum":
        head = text.split(".", 1)[0].strip()
        return head or None
    if text.lower().startswith(("vide ", "vid. ", "voir ", "v. ", "cf. ", "id. ")):
        return text.split(",", 1)[0].strip(" .;:") or None
    if "," in text:
        return text.split(",", 1)[0].strip(" .;:")
    m = re.search(r"\d", text)
    if m:
        return text[: m.start()].strip(" .;:")
    return text.split(";", 1)[0].strip(" .;:")


def entry_kind_for(section_kind: str, entry_raw: str) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    lowered = entry_raw.lower()
    if lowered.startswith(("vide ", "vid. ", "voir ", "v. ", "cf. ", "id. ")):
        return "cross_reference"
    if lowered.startswith(("cap.", "cap ", "articulus", "liber ", "admonitio", "ordo rerum", "index ")):
        return "heading_group"
    return "lemma"


def page_hints_from(entry_raw: str) -> list[int]:
    hints: list[int] = []
    for match in PAGE_REF_RE.finditer(entry_raw):
        first = int(match.group(1))
        if first not in hints:
            hints.append(first)
        if match.group(2):
            second = int(match.group(2))
            if second not in hints:
                hints.append(second)
    return hints


def build_refs(entry_key: str, entry_raw: str, file_path: str, page_map_lookup: dict[int, str], section_start_file: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for ref_order, match in enumerate(PAGE_REF_RE.finditer(entry_raw), start=1):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": len(refs) + 1,
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
                "target_file": page_map_lookup.get(start),
                "target_file_probability": 0.99 if start in page_map_lookup else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": file_path,
                "confidence": 0.96 if start in page_map_lookup else 0.74,
                "raw_json": {
                    "source_file": file_path,
                    "locator_method": "header_page_map" if start in page_map_lookup else "unresolved",
                },
            }
        )
    return refs


def build_helper_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
    if not page_hints and entry.get("inferred_printed_page"):
        page_hints = [entry["inferred_printed_page"]]
    if not page_hints:
        return None
    lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:120]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma_raw,
        "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0], entry["entry_raw"][:120]],
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


def section_for_seq(seqs: set[int]) -> dict[str, Any]:
    for section in SECTION_DEFS:
        if section["file_start_seq"] in seqs:
            return section
    raise KeyError("No matching section definition for provided file sequences")


def build_section_entries(
    section: dict[str, Any],
    fragments: list[dict[str, Any]],
    page_map_lookup: dict[int, str],
    helper_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    letter_nodes: dict[str, str] = {}
    current_letter: str | None = None
    entry_order = 0
    section_start_file = str(fragments[0]["file"]) if fragments else None

    for frag in fragments:
        text = normalize(frag["text"]) or ""
        if not text:
            continue
        if text in NOISE_EXACT or any(text.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        if section["section_kind"] == "analytic_subject" and text.startswith("INDEX RERUM"):
            continue
        if section["section_kind"] == "foreign_terms" and text.startswith("INDEX GRÆCITATIS"):
            continue
        if section["section_kind"] == "author_index" and text.startswith("INDEX AUCTORUM"):
            continue
        if section["section_kind"] == "ordo_rerum" and text.startswith("ORDO RERUM"):
            continue

        if DIVIDER_RE.fullmatch(text):
            current_letter = text
            if current_letter not in letter_nodes and section["section_kind"] in {"author_index", "foreign_terms", "analytic_subject"}:
                node_key = f"{VOLUME_ID}:node:letter:{current_letter}"
                letter_nodes[current_letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter.lower(),
                        "label_sort": current_letter.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": frag["file"], "kind": "alphabetic divider"},
                    }
                )
            continue

        entry_raw = text
        entry_kind = entry_kind_for(section["section_kind"], entry_raw)
        page_hints = page_hints_from(entry_raw)
        lemma_raw = infer_lemma(entry_raw, section["section_kind"])
        if section["section_kind"] == "ordo_rerum":
            lemma_raw = entry_raw.split("—", 1)[0].strip(" .;:")
        heading_letter = current_letter or (lemma_raw[:1].upper() if lemma_raw else None)
        if heading_letter and heading_letter not in letter_nodes and section["section_kind"] in {"author_index", "foreign_terms", "analytic_subject"}:
            node_key = f"{VOLUME_ID}:node:letter:{heading_letter}"
            letter_nodes[heading_letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section["section_key"],
                    "parent_node_key": None,
                    "node_order": len(nodes) + 1,
                    "node_kind": "letter_group",
                    "label_raw": heading_letter,
                    "label_norm": heading_letter.lower(),
                    "label_sort": heading_letter.lower(),
                    "node_level": 1,
                    "confidence": 0.92,
                    "raw_json": {"source_file": frag["file"], "kind": "inferred letter"},
                }
            )

        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
        entry = {
            "entry_key": entry_key,
            "section_key": section["section_key"],
            "parent_node_key": letter_nodes.get(heading_letter),
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw if page_hints else None,
            "heading_letter": heading_letter,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": frag["file"],
            "target_file_best": page_map_lookup.get(page_hints[0]) if page_hints else None,
            "confidence": 0.91 if page_hints else 0.68,
            "raw_json": {
                "source_file": frag["file"],
                "source_line_no": frag["line_no"],
                "page_hints": page_hints,
                "section_kind": section["section_kind"],
                "section_kind_reason": section["section_kind_reason"],
            },
        }
        entries.append(entry)
        helper_entry = build_helper_entry(entry)
        if helper_entry is not None:
            helper_candidates.append(helper_entry)

        refs.extend(build_refs(entry_key, entry_raw, frag["file"], page_map_lookup, section_start_file or frag["file"]))

    return entries, refs, nodes, helper_candidates


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map_lookup = page_map(files)
    helper_candidates: list[dict[str, Any]] = []

    file_groups = {
        SECTION_DEFS[0]["section_key"]: select_files(files, {777, 778}),
        SECTION_DEFS[1]["section_key"]: select_files(files, {779, 780, 781, 782}),
        SECTION_DEFS[2]["section_key"]: select_files(files, {783, 784, 785}),
        SECTION_DEFS[3]["section_key"]: select_files(files, {845, 846, 847, 848, 849, 850, 851, 852}),
    }

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    for section in SECTION_DEFS:
        sec_files = file_groups[section["section_key"]]
        if not sec_files:
            continue
        fragments = collect_fragments(sec_files)
        sec_entries, sec_refs, sec_nodes, helper_candidates = build_section_entries(section, fragments, page_map_lookup, helper_candidates)
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": section["heading_norm"],
                "heading_letter": section["heading_letter"],
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": str(sec_files[0]),
                "file_end": str(sec_files[-1]),
                "confidence": 0.95,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "evidence_files": [str(sec_files[0]), str(sec_files[-1])],
                },
            }
        )
        nodes.extend(sec_nodes)
        entries.extend(sec_entries)
        refs.extend(sec_refs)

    helper_candidates = helper_candidates[:24]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_candidates,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_candidates else {"status": "empty", "entries": []}
    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}

    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        raw_json = entry.setdefault("raw_json", {})
        raw_json["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": helper.get("best_candidate"),
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
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")

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

    coverage_evidence = [sections[0]["file_start"], sections[-1]["file_end"]] if sections else [str(files[0]), str(files[-1])]
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the visible alphabetical and editorial index sections from the OCR tail, including Greek terms, author citations, memorable things, and the closing table of contents.",
        "evidence_files": coverage_evidence,
    }
    notes = [
        "OCR file suffixes and printed page numbers are independent; printed page numbers are preserved in refs and raw_json.",
        "The author index and index rerum are split conservatively by visible headings, while the closing Ordo Rerum is kept in a separate section.",
        "Helper evidence is recorded only for entries with page locators and is advisory only.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
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
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(DEFAULT_OUTPUT_FILE),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PG009 alphabetical payload and preserve printed-page locators separately from OCR suffixes.",
            "completed": [
                "identified foreign_terms, author_index, analytic_subject, and ordo_rerum sections",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page locators.",
                "Prefer conservative segmentation when OCR lines wrap across columns.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG009 alphabetical payload.")
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

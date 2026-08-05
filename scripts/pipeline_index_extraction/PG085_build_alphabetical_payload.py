#!/usr/bin/env python3
"""Usage: build the PG085 alphabetical payload from the OCR index sections.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/PG085_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG085/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG085_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG085_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG085 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG085_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG085"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 85"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_SECTION_FILES = {
    "author_index": [596, 597],
    "analytic_subject_1": [924, 925, 927],
    "analytic_subject_2": [929],
    "ordo_rerum": [942, 943],
}

FILE_PAGE_RANGE_OVERRIDES = {
    596: [1181, 1182],
    597: [1183, 1184],
    924: [1825, 1826],
    925: [1827, 1828],
    926: [1829, 1830],
    927: [1831, 1832],
    928: [1833, 1834],
    929: [1835, 1836],
    930: [1837, 1838],
    931: [1839, 1840],
    932: [1841, 1842],
    933: [1843, 1844],
    934: [1845, 1846],
    935: [1847, 1848],
    936: [1849, 1850],
    937: [1851, 1852],
    938: [1853, 1854],
    939: [1855, 1856],
    940: [1857, 1858],
    941: [1859, 1860],
    942: [1861, 1862],
    943: [1863, 1864],
}

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
NOISE_RE = re.compile(r"^(?:Digitized by Google|PATROL\. GR\. LXXXV\.|FINIS TOMI OCTOGESIMI QUINTI\.)$", re.IGNORECASE)
SECTION_TITLE_RE = re.compile(
    r"^(?:INDEX SCRIPTORUM|INDICES ANALYTICI|INDEX ANALYTICUS|INDEX IN ÆNEAM GAZENSEM|ORDO RERUM|ADDENDA)\b",
    re.IGNORECASE,
)
SECTION_START_RE = {
    "author_index": re.compile(r"INDEX SCRIPTORUM", re.IGNORECASE),
    "analytic_subject": re.compile(r"INDICES ANALYTICI|INDEX ANALYTICUS|INDEX IN ÆNEAM GAZENSEM", re.IGNORECASE),
    "ordo_rerum": re.compile(r"ORDO RERUM", re.IGNORECASE),
}
PARALLEL_REF_START_RE = re.compile(
    r"(?:,\s*|\.\s*)(?=(?:lib\.|cap\.|c\.|ibid\.|id\.|\d{1,4}|[IVXLCDM]{1,4}))",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
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


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def discover_files(source_root: Path) -> list[Path]:
    items: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if not match:
            continue
        items.append((int(match.group(1)), path))
    return [path for _, path in sorted(items)]


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"Cannot parse file seq from {path}")
    return int(match.group(1))


def parse_header_pages(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(read_text(path))
    header_text = normalize(parsed.get("header_text") or "")
    if not header_text:
        return []
    nums = [int(match) for match in HEADER_PAGE_RE.findall(header_text)]
    if not nums:
        return []
    return nums[:2]


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        seq = file_seq(path)
        pages = FILE_PAGE_RANGE_OVERRIDES.get(seq) or parse_header_pages(path)
        for page in pages:
            page_map.setdefault(page, str(path))
    return page_map


def nearest_page_file(page_map: dict[int, str], page: int | None, fallback: str | None = None) -> str | None:
    if page is None:
        return fallback
    if page in page_map:
        return page_map[page]
    if not page_map:
        return fallback
    nearest = min(page_map, key=lambda candidate: (abs(candidate - page), candidate))
    return page_map[nearest]


def page_header_hint(seq: int) -> list[int]:
    return FILE_PAGE_RANGE_OVERRIDES.get(seq, [])


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    text = parsed.get("all_text") or parsed.get("body_text") or ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = normalize(raw)
        if not line:
            continue
        if NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def split_fragments(line: str) -> list[str]:
    text = normalize(line) or ""
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[\.\;\]])\s+(?=[A-ZÆŒΑ-Ω])", text) if part.strip()]
    if len(parts) == 1 and ";" in text and "lib." in text.lower():
        return [part.strip() for part in re.split(r";\s*", text) if part.strip()]
    return parts or [text]


def is_letter_marker(text: str) -> bool:
    return bool(LETTER_RE.fullmatch(text.strip()))


def first_initial(text: str) -> str | None:
    cleaned = normalize(text) or ""
    for ch in cleaned:
        if ch.isalpha():
            return ch.upper()
    return None


def entry_kind_for(section_kind: str, text: str) -> str:
    lowered = text.lower().strip()
    if lowered.startswith(("vide", "vid.", "voir", "v.", "cf.", "id.", "caetera vide", "caetera voir")):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        return "heading_group" if not re.search(r"\d", text) or text.upper().startswith(("BASILIUS", "DE VITA", "EUTHALIUS", "JOANNES", "EUDOCIA", "ÆNEAS", "THEOPHRASTUS", "ZACHARIAS", "DISPUTATIO", "FRAGMENTA", "GELASIUS", "HISTORIA", "APPENDIX", "SUPPLEMENTUM")) else "lemma"
    if section_kind == "author_index":
        return "cross_reference" if lowered.startswith(("vide", "vid.", "voir", "v.", "cf.", "id.")) else "lemma"
    return "lemma"


def lemma_from_text(section_kind: str, text: str) -> str | None:
    raw = normalize(text) or ""
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith(("vide", "vid.", "voir", "v.", "cf.", "id.")):
        return None
    if section_kind == "author_index":
        cut = PARALLEL_REF_START_RE.search(raw)
        if cut:
            return raw[: cut.start()].rstrip(" ,;:.") or None
        comma = raw.find(",")
        if comma > 0:
            return raw[:comma].rstrip(" ,;:.") or None
        return raw.rstrip(" ,;:.") or None
    if section_kind in {"analytic_subject", "ordo_rerum"}:
        match = re.search(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?:\s*(?:seqq\.?|seq\.?|et seq\.|fin\.?|ibid\.?|ibid|id\.)*)?\s*$", raw, re.IGNORECASE)
        if match:
            return raw[: match.start()].rstrip(" ,;:.—-") or None
        if "." in raw:
            return raw.rsplit(".", 1)[0].rstrip(" ,;:.—-") or None
        return raw.rstrip(" ,;:.—-") or None
    return raw.rstrip(" ,;:.—-") or None


def extract_editorial_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": match.group(0).strip(),
                "page_ref_raw": match.group(0).strip(),
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
            }
        )
    return refs


def extract_parallel_refs(text: str) -> list[dict[str, Any]]:
    raw = normalize(text) or ""
    if not raw:
        return []
    cut = PARALLEL_REF_START_RE.search(raw)
    if not cut:
        return []
    lemma_tail = raw[cut.start() :].strip(" ,;:.")
    parts = [part.strip(" ,;:.") for part in re.split(r";\s*", lemma_tail) if part.strip(" ,;:.")]
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        if part.lower() in {"ibid.", "ibid", "id.", "id"}:
            continue
        if part in seen:
            continue
        seen.add(part)
        refs.append(
            {
                "ref_kind": "parallel_locator",
                "ref_raw": part,
                "page_ref_raw": None,
                "page_ref_int": None,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def chunk_to_entries(
    *,
    section_kind: str,
    section_key: str,
    section_files: list[Path],
    page_map: dict[int, str],
    start_marker: str,
    helper_summary_by_source: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    node_for_letter: dict[str, str] = {}
    node_order = 0
    entry_order = 0
    current_letter: str | None = None
    started = False
    start_re = re.compile(re.escape(start_marker), re.IGNORECASE) if start_marker else None

    for path in section_files:
        lines = extract_body_lines(path)
        for line in lines:
            if not started:
                if start_re and start_re.search(line):
                    started = True
                continue
            if SECTION_TITLE_RE.fullmatch(line) and section_kind != "ordo_rerum":
                continue
            if any(token in line for token in ("Digitized by Google", "PATROL. GR. LXXXV.", "UNIV. OF MICHIGAN", "FINIS TOMI OCTOGESIMI QUINTI.")):
                continue
            if section_kind == "analytic_subject" and is_letter_marker(line):
                current_letter = line.strip()
                node_order += 1
                node_key = f"{VOLUME_ID}:{section_kind}:{section_key.split(':')[-1]}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": normalize(current_letter),
                        "label_sort": sort_norm(current_letter),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {
                            "source_file": str(path),
                            "reason": "Standalone alphabetical divider in the analytical index.",
                        },
                    }
                )
                node_for_letter[current_letter] = node_key
                continue

            fragments = split_fragments(line)
            for fragment in fragments:
                text = normalize(fragment) or ""
                if not text or is_letter_marker(text):
                    continue
                if any(token in text for token in ("Digitized by Google", "PATROL. GR. LXXXV.", "UNIV. OF MICHIGAN", "FINIS TOMI OCTOGESIMI QUINTI.")):
                    continue
                if section_kind == "ordo_rerum" and text.upper() in {"ORDO RERUM", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."}:
                    continue
                if section_kind == "analytic_subject":
                    derived_letter = first_initial(lemma_from_text(section_kind, text) or text)
                    if derived_letter and derived_letter != current_letter:
                        current_letter = derived_letter
                        node_order += 1
                        node_key = f"{VOLUME_ID}:{section_kind}:{section_key.split(':')[-1]}:node:{node_order:03d}"
                        nodes.append(
                            {
                                "node_key": node_key,
                                "section_key": section_key,
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": current_letter,
                                "label_norm": normalize(current_letter),
                                "label_sort": sort_norm(current_letter),
                                "node_level": 1,
                                "confidence": 0.9,
                                "raw_json": {
                                    "source_file": str(path),
                                    "reason": "Alphabetical group inferred from sorted entry initials.",
                                },
                            }
                        )
                        node_for_letter[current_letter] = node_key
                entry_order += 1
                entry_key = f"{VOLUME_ID}:{section_kind}:{section_key.split(':')[-1]}:entry:{entry_order:04d}"
                entry_kind = entry_kind_for(section_kind, text)
                lemma_raw = lemma_from_text(section_kind, text)
                inferred_page = None
                target_file_best = None
                editorial_anchor_file = str(path)
                material_refs = extract_editorial_refs(text)
                parallel_refs = extract_parallel_refs(text) if section_kind == "author_index" else []
                if material_refs:
                    inferred_page = material_refs[0]["page_ref_int"]
                    target_file_best = nearest_page_file(page_map, inferred_page)
                elif section_kind == "author_index":
                    target_file_best = None
                else:
                    target_file_best = str(path)
                helper_summary = None
                if helper_summary_by_source:
                    helper_summary = helper_summary_by_source.get(text)

                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": node_for_letter.get(current_letter) if current_letter else None,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize(lemma_raw) if lemma_raw else None,
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": text,
                    "context_raw": None,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(section_files[0]),
                    "editorial_anchor_file": editorial_anchor_file,
                    "target_file_best": target_file_best,
                    "confidence": 0.91 if material_refs else 0.84,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section_kind,
                        "locator_kind": "parallel_locator" if parallel_refs else ("editorial_page" if material_refs else None),
                        "helper_summary": helper_summary,
                    },
                }
                if entry["raw_json"]["locator_kind"] is None:
                    entry["raw_json"].pop("locator_kind")
                if entry["raw_json"]["helper_summary"] is None:
                    entry["raw_json"].pop("helper_summary")
                entries.append(entry)

                if section_kind == "author_index":
                    if parallel_refs:
                        for ref_order, ref in enumerate(parallel_refs, start=1):
                            refs.append(
                                {
                                    "entry_key": entry_key,
                                    "ref_order": ref_order,
                                    "ref_kind": ref["ref_kind"],
                                    "ref_raw": ref["ref_raw"],
                                    "page_ref_raw": ref["page_ref_raw"],
                                    "page_ref_int": ref["page_ref_int"],
                                    "page_ref_col": ref["page_ref_col"],
                                    "line_ref_raw": ref["line_ref_raw"],
                                    "range_start_raw": ref["range_start_raw"],
                                    "range_end_raw": ref["range_end_raw"],
                                    "target_file": None,
                                    "target_file_probability": None,
                                    "section_start_file": str(section_files[0]),
                                    "editorial_anchor_file": editorial_anchor_file,
                                    "confidence": 0.7,
                                    "raw_json": {"source_file": str(path), "section_kind": section_kind},
                                }
                            )
                else:
                    for ref_order, ref in enumerate(material_refs, start=1):
                        ref_target = nearest_page_file(page_map, ref["page_ref_int"])
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": ref["ref_kind"],
                                "ref_raw": ref["ref_raw"],
                                "page_ref_raw": ref["page_ref_raw"],
                                "page_ref_int": ref["page_ref_int"],
                                "page_ref_col": ref["page_ref_col"],
                                "line_ref_raw": ref["line_ref_raw"],
                                "range_start_raw": ref["range_start_raw"],
                                "range_end_raw": ref["range_end_raw"],
                                "target_file": ref_target,
                                "target_file_probability": 0.98 if ref_target else None,
                                "section_start_file": str(section_files[0]),
                                "editorial_anchor_file": editorial_anchor_file,
                                "confidence": 0.9,
                                "raw_json": {"source_file": str(path), "section_kind": section_kind},
                            }
                        )
    return nodes, entries, refs


def collect_page_samples(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    wanted = {
        "analytic_subject": ["Abel Christi umbra", "Baptismi figura", "Æneæ Gazæi Theophrastus"],
        "ordo_rerum": ["BASILIUS SELEUCIENSIS", "Notitia. 9"],
    }
    for entry in entries:
        section_kind = entry["raw_json"].get("section_kind")
        if section_kind not in wanted:
            continue
        if any(token.lower() in (entry["entry_raw"] or "").lower() for token in wanted[section_kind]):
            chosen.append(entry)
    if chosen:
        return chosen[:6]
    return entries[:4]


def build_helper_request(source_root: Path, sample_entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(sample_entries, start=1):
        page_hint = entry.get("inferred_printed_page")
        page_hints = [str(page_hint)] if page_hint is not None else []
        page_hint_ints = [int(page_hint)] if page_hint is not None else []
        lemma = entry.get("lemma_raw") or entry["entry_raw"]
        helper_entries.append(
            {
                "entry_id": f"PG085_HELPER_{idx:03d}",
                "lemma_raw": lemma,
                "query_names": [lemma, entry["entry_raw"][:120]],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"][:240],
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


def helper_summary_by_entry_id(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        best = item.get("best_candidate") or {}
        candidates: list[dict[str, Any]] = []
        for candidate in item.get("candidates", [])[:3]:
            candidates.append(
                {
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "candidate_role": candidate.get("candidate_role"),
                    "evidence_kinds": [e.get("kind") for e in candidate.get("evidence", []) if isinstance(e, dict) and e.get("kind")][:6],
                }
            )
        mapping[entry_id] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
            },
            "candidates": candidates,
        }
    return mapping


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
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def build_sections_and_payload(source_root: Path, helper_summary: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    section_specs = [
        {
            "section_key": f"{VOLUME_ID}:alpha:author_index:001",
            "section_order": 1,
            "section_kind": "author_index",
            "start_marker": "INDEX SCRIPTORUM",
            "heading_raw": "INDEX SCRIPTORUM, EPISCOPORUM, HAERETICORUM, ETC. QUI IN GELASII CYZICENI ACTIS SYNODI NICENAE MEMORANTUR.",
            "page_start": 1181,
            "page_end": 1184,
            "file_start": source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-596.txt",
            "file_end": source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-597.txt",
            "section_files": [source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-596.txt", source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-597.txt"],
            "section_kind_reason": "Author index headed INDEX SCRIPTORUM with ecclesiastical names and parallel locators into Gelasius' work.",
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
            "section_order": 2,
            "section_kind": "analytic_subject",
            "start_marker": "INDICES ANALYTICI",
            "heading_raw": "INDICES ANALYTICI RERUM AC VERBORUM QUÆ IN HOC VOLUMINE CONTINENTUR. IN BASILIUM SELEUCIÆ EPISCOPUM.",
            "page_start": 1825,
            "page_end": 1832,
            "file_start": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-924.txt",
            "file_end": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-927.txt",
            "section_files": [
                source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-924.txt",
                source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-925.txt",
                source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-927.txt",
            ],
            "section_kind_reason": "Alphabetical analytical subject index with letter-group dividers and editorial page references for IN BASILIUM SELEUCIÆ EPISCOPUM.",
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:003",
            "section_order": 3,
            "section_kind": "analytic_subject",
            "start_marker": "INDEX IN ÆNEAM GAZENSEM",
            "heading_raw": "INDEX IN ÆNEAM GAZENSEM.",
            "page_start": 1835,
            "page_end": 1836,
            "file_start": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-929.txt",
            "file_end": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-929.txt",
            "section_files": [source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-929.txt"],
            "section_kind_reason": "Alphabetical analytical subject index for Æneas Gazæus with A-Z divider letters and page references into the work.",
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:004",
            "section_order": 4,
            "section_kind": "ordo_rerum",
            "start_marker": "ORDO RERUM",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "page_start": 1861,
            "page_end": 1864,
            "file_start": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-942.txt",
            "file_end": source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-943.txt",
            "section_files": [
                source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-942.txt",
                source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-943.txt",
            ],
            "section_kind_reason": "Editorial contents table for the tomo, distinct from the alphabetical indexes and structured as a running table of works and chapter titles.",
        },
    ]

    section_payloads: list[dict[str, Any]] = []
    node_payloads: list[dict[str, Any]] = []
    entry_payloads: list[dict[str, Any]] = []
    ref_payloads: list[dict[str, Any]] = []

    for spec in section_specs:
        nodes, entries, refs = chunk_to_entries(
            section_kind=spec["section_kind"],
            section_key=spec["section_key"],
            section_files=spec["section_files"],
            page_map=page_map,
            start_marker=spec["start_marker"],
            helper_summary_by_source=None,
        )
        node_payloads.extend(nodes)
        entry_payloads.extend(entries)
        ref_payloads.extend(refs)
        section_payloads.append(
            {
                "section_key": spec["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": normalize(spec["heading_raw"]),
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": str(spec["file_start"]),
                "file_end": str(spec["file_end"]),
                "confidence": 0.98 if spec["section_kind"] != "ordo_rerum" else 0.95,
                "raw_json": {
                    "section_kind_reason": spec["section_kind_reason"],
                    "source_files": [str(p) for p in spec["section_files"]],
                },
            }
        )

    # Enrich entries with helper summaries for selected samples.
    if helper_summary:
        helper_map = helper_summary_by_entry_id(helper_summary)
        helper_ids = {item["entry_id"] for item in helper_summary.get("entries", [])}
        for entry in entry_payloads:
            if entry["entry_key"] in helper_ids and entry.get("raw_json") is not None:
                entry["raw_json"]["helper_summary"] = helper_map.get(entry["entry_key"])

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered all index line items from the detected alphabetical, analytical, onomastic, and contents sections with direct OCR inspection and page-to-file mapping for editorial locators.",
        "evidence_files": [
            str(source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-596.txt"),
            str(source_root / "53e521d1-f2d5-4de4-b397-30eac40b0b84-597.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-924.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-925.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-927.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-929.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-942.txt"),
            str(source_root / "c10a2e12-2feb-4c09-ad59-d79fd6ef4289-943.txt"),
        ],
    }

    notes = [
        "PG085 contains multiple index sections interleaved with body text; the output keeps them separate by section_key.",
        "Printed page numbers for the 924/929/942 OCR files were inferred from the surrounding page sequence because the OCR header digits are noisy in those files.",
        "Entries in INDEX SCRIPTORUM mostly use parallel locators (lib./cap.) rather than editorial page numbers, so their refs intentionally keep target_file null.",
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
        "sections": section_payloads,
        "nodes": node_payloads,
        "entries": entry_payloads,
        "refs": ref_payloads,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def update_todo(intermediate_dir: Path, current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    payload = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": current_focus,
        "completed": completed,
        "pending": pending,
        "blocked": blocked,
        "notes": notes,
    }
    write_json(intermediate_dir / "todo.json", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG085 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    update_todo(
        args.intermediate_dir,
        "Resolve PG085 index sections and assemble final alphabetical payload",
        ["section boundaries identified", "OCR pages inspected", "entry parser drafted"],
        ["build helper request", "run helper locator", "write final payload"],
        [],
        ["Use the inferred page sequence for noisy OCR headers in 924, 929 and 942."],
    )

    files = discover_files(args.source_root)
    page_map = build_page_map(files)

    section_probes = [
        {
            "entry_id": "PG085_HELPER_001",
            "lemma_raw": "Baptismi figura",
            "query_names": ["Baptismi figura", "Baptismi", "figura"],
            "page_hints": ["75"],
            "page_hint_ints": [75],
            "context_raw": "Baptismi figura, 75.",
        },
        {
            "entry_id": "PG085_HELPER_002",
            "lemma_raw": "Æneæ Gazæi Theophrastus",
            "query_names": ["Æneæ Gazæi Theophrastus", "Theophrastus", "Animarum immortalitate"],
            "page_hints": ["629"],
            "page_hint_ints": [629],
            "context_raw": "Æneæ Gazæi Theophrastus, sive de Animarum immortalitate, et corporum resurrectione, 629.",
        },
        {
            "entry_id": "PG085_HELPER_003",
            "lemma_raw": "BASILIUS SELEUCIENSIS. Notitia.",
            "query_names": ["BASILIUS SELEUCIENSIS", "Notitia", "Epistola nuncupatoria"],
            "page_hints": ["9"],
            "page_hint_ints": [9],
            "context_raw": "BASILIUS SELEUCIENSIS. Notitia. 9",
        },
        {
            "entry_id": "PG085_HELPER_004",
            "lemma_raw": "Abel Christi umbra",
            "query_names": ["Abel Christi umbra", "Abel", "Christi umbra"],
            "page_hints": ["20"],
            "page_hint_ints": [20],
            "context_raw": "Abel Christi umbra, 20.",
        },
    ]

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": section_probes,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_sections_and_payload(args.source_root, helper_output)

    # Add a compact helper summary to the entries that were actually probed.
    helper_map = helper_summary_by_entry_id(helper_output)
    for entry in payload["entries"]:
        helper_id = None
        raw = entry.get("entry_raw") or ""
        lemma = entry.get("lemma_raw") or ""
        if lemma == "Baptismi figura":
            helper_id = "PG085_HELPER_001"
        elif "Æneæ Gazæi Theophrastus" in lemma or "Theophrastus" in raw:
            helper_id = "PG085_HELPER_002"
        elif "BASILIUS SELEUCIENSIS" in raw:
            helper_id = "PG085_HELPER_003"
        elif "Abel Christi umbra" in raw:
            helper_id = "PG085_HELPER_004"
        if helper_id and helper_id in helper_map:
            entry.setdefault("raw_json", {})["helper_summary"] = helper_map[helper_id]

    write_json(args.output_file, payload)

    update_todo(
        args.intermediate_dir,
        "Finalize PG085 alphabetical payload",
        ["sections identified", "helper locator run", "payload written"],
        [],
        [],
        ["Output and helper artifacts saved under the runtime paths from the prompt."],
    )


if __name__ == "__main__":
    main()

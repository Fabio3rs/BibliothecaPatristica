#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg066_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG066/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG066_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG066_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG066 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG066_alphabetical_indices.json

Build the PG066 alphabetical-index payload from the OCR tail.
The volume contains a Synesius addressee index, subject indexes, and a closing Ordo Rerum table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG066"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 66"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
TODO_FILENAME = "todo.json"

SECTION_KEYS = {
    "addressee": f"{VOLUME_ID}:alpha:addressee:001",
    "analytic_1": f"{VOLUME_ID}:alpha:analytic:001",
    "rerum": f"{VOLUME_ID}:alpha:analytic:002",
    "ordo": f"{VOLUME_ID}:alpha:ordo_rerum:001",
}

SECTION_META = {
    "addressee": {
        "section_key": SECTION_KEYS["addressee"],
        "section_order": 1,
        "section_kind": "onomastic_person",
        "heading_raw": "INDICULUS EORUM AD QUOS SCRIPTÆ SUNT SYNESII EPISTOLÆ.",
        "heading_norm": "indiculus eorum ad quos scriptae sunt synesii epistolae",
        "page_start": 1735,
        "page_end": 1736,
        "raw_reason": "Index of letter addressees (person names) under the explicit INDICULUS heading.",
    },
    "analytic_1": {
        "section_key": SECTION_KEYS["analytic_1"],
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS.",
        "heading_norm": "index analyticus",
        "page_start": 1735,
        "page_end": 1744,
        "raw_reason": "Analytical subject index headed INDEX ANALYTICUS / INDEX ANALYTICUS IN SYNESIUM.",
    },
    "rerum": {
        "section_key": SECTION_KEYS["rerum"],
        "section_order": 3,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM.",
        "heading_norm": "index rerum",
        "page_start": 1745,
        "page_end": 1748,
        "raw_reason": "Analytical subject index headed INDEX RERUM, kept separate from the preceding INDEX ANALYTICUS block.",
    },
    "ordo": {
        "section_key": SECTION_KEYS["ordo"],
        "section_order": 4,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "page_start": 1753,
        "page_end": 1756,
        "raw_reason": "Closing contents / order-of-matter table headed ORDO RERUM.",
    },
}

TITLE_MARKERS = {
    "INDICULUS EORUM AD QUOS SCRIPTÆ SUNT SYNESII EPISTOLÆ.": "addressee",
    "INDEX ANALYTICUS IN SYNESIUM.": "analytic_1",
    "INDEX ANALYTICUS.": "analytic_1",
    "INDEX ANALATICUS.": "analytic_1",
    "INDEX ANALYTICUS": "analytic_1",
    "INDEX RERUM.": "rerum",
    "INDEX RERUM": "rerum",
    "ORDO RERUM.": "ordo",
    "ORDO RERUM": "ordo",
}

LETTER_RE = re.compile(r"^[A-ZΑ-ΩÆŒ](?:\.)?$")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
NOISE_RE = re.compile(r"^(?:Digitized by Google|[-—·]+)$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            files.append((int(match.group(1)), path))
    return [path for _, path in sorted(files)]


def iter_blocks(text: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    for match in re.finditer(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, flags=re.S):
        block_type = match.group(1)
        content = match.group(2)
        lines = [normalize_space(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        blocks.append((block_type, lines))
    return blocks


def header_pages(path: Path) -> list[int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    pages: list[int] = []
    for block_type, lines in iter_blocks(raw):
        if block_type != "cabecalho":
            continue
        for line in lines:
            for match in PAGE_RE.finditer(line):
                value = int(match.group(1))
                if value not in pages:
                    pages.append(value)
    return pages


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in header_pages(path):
            mapping.setdefault(page, path.as_posix())
    return mapping


def normalize_line(line: str) -> str:
    return normalize_space(line).strip()


def is_letter_marker(line: str) -> bool:
    cleaned = normalize_line(line)
    return bool(LETTER_RE.fullmatch(cleaned) or LETTER_RE.fullmatch(cleaned.rstrip("·")))


def is_title(line: str) -> bool:
    return normalize_line(line).upper() in TITLE_MARKERS


def section_for_title(line: str) -> str | None:
    return TITLE_MARKERS.get(normalize_line(line).upper())


def extract_page_refs(text: str) -> list[int]:
    pages: list[int] = []
    for match in PAGE_RE.finditer(text):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
    return pages


def lemma_from_entry(text: str) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    if " Vide " in f" {value} ":
        value = value.split(" Vide ", 1)[0].rstrip(" ,;:.")
    first_page = PAGE_RE.search(value)
    if first_page:
        prefix = value[: first_page.start()].rstrip(" ,;:.")
        if prefix:
            value = prefix
    if len(value) > 160:
        dot_split = re.match(r"^(.+?)(?:\.\s+[A-ZΑ-ΩÆŒ].+)$", value)
        if dot_split and dot_split.group(1).strip():
            value = dot_split.group(1).rstrip(" ,;:.")
    return value or None


def entry_kind_for_text(text: str, page_refs: list[int]) -> str:
    stripped = normalize_space(text)
    if not page_refs and re.search(r"\bVide\b|\bvid\.\b|\bcf\.\b|\bid\.\b", stripped, flags=re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def make_node_key(section_key: str, order: int) -> str:
    return f"{section_key}:node:{order:04d}"


def make_entry_key(section_key: str, order: int) -> str:
    return f"{section_key}:entry:{order:04d}"


def build_sections(source_root: Path, files: list[Path], file_assignments: dict[str, set[str]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key in ["addressee", "analytic_1", "rerum", "ordo"]:
        meta = SECTION_META[key]
        assigned = [Path(p) for p in sorted(file_assignments.get(key, set()))]
        if assigned:
            file_start = assigned[0].as_posix()
            file_end = assigned[-1].as_posix()
        else:
            file_start = None
            file_end = None
        sections.append(
            {
                "section_key": meta["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": meta["section_order"],
                "section_kind": meta["section_kind"],
                "heading_raw": meta["heading_raw"],
                "heading_norm": meta["heading_norm"],
                "heading_letter": None,
                "page_start": meta["page_start"],
                "page_end": meta["page_end"],
                "file_start": file_start,
                "file_end": file_end,
                "confidence": 0.94 if assigned else 0.82,
                "raw_json": {
                    "section_kind_reason": meta["raw_reason"],
                    "file_assignments": [p.as_posix() for p in assigned],
                },
            }
        )
    return sections


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_refs = entry.get("page_refs") or []
        if not page_refs:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": [q for q in dict.fromkeys([
                    entry["lemma_raw"] or entry["entry_raw"],
                    normalize_space((entry["lemma_raw"] or entry["entry_raw"]).split(",")[0]) if entry.get("lemma_raw") else None,
                ]) if q],
                "page_hints": [str(page_refs[0])],
                "page_hint_ints": [page_refs[0]],
                "context_raw": entry["entry_raw"][:240],
            }
        )
        if len(helper_entries) >= 8:
            break
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(Path("/homessddata/Projects/pdfocr/teste/PG066/text")),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    if not helper_request_json.exists():
        return {"status": "missing_request", "entries": []}
    cmd = [
        sys.executable,
        str(SCRIPT_TARGET_LOCATOR),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if helper_output_json.exists():
        return json.loads(helper_output_json.read_text(encoding="utf-8"))
    return {"status": "empty", "entries": []}


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        key = item.get("entry_id") or item.get("entry_key")
        if key:
            lookup[key] = item
    return lookup


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    working_files = [path for path in files if 896 <= int(re.search(r"-(\d+)\.txt$", path.name).group(1)) <= 906]
    if not working_files:
        working_files = files

    # State keyed by section id.
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    section_files: dict[str, set[str]] = defaultdict(set)
    current_section = "analytic_1"
    current_node_key: str | None = None
    entry_order_by_section: dict[str, int] = defaultdict(int)
    node_order_by_section: dict[str, int] = defaultdict(int)
    open_entry: dict[str, Any] | None = None

    def flush_entry() -> None:
        nonlocal open_entry
        if open_entry is None:
            return
        text = normalize_space(open_entry["entry_raw"])
        if not text:
            open_entry = None
            return
        lemma_raw = lemma_from_entry(text)
        page_refs = extract_page_refs(text)
        entry_kind = entry_kind_for_text(text, page_refs)
        entry_order_by_section[open_entry["section_key"]] += 1
        order = entry_order_by_section[open_entry["section_key"]]
        entry_key = make_entry_key(open_entry["section_key"], order)
        source_file = open_entry["editorial_anchor_file"]
        target_file_best = page_map.get(page_refs[0]) if page_refs else source_file
        entry = {
            "entry_key": entry_key,
            "section_key": open_entry["section_key"],
            "parent_node_key": open_entry["parent_node_key"],
            "entry_order": order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw) if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
            "entry_raw": text,
            "context_raw": None,
            "heading_letter": open_entry["heading_letter"],
            "inferred_printed_page": page_refs[0] if page_refs else None,
            "section_start_file": open_entry["section_start_file"],
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.89 if page_refs else 0.8,
            "raw_json": {
                "source": "ocr_line_group",
                "section_kind": SECTION_META[open_entry["section_name"]]["section_kind"],
                "page_refs_detected": page_refs,
            },
        }
        entries.append(entry)
        for ref_order, page in enumerate(page_refs, start=1):
            target_file = page_map.get(page)
            if target_file is None and page_map:
                nearest = min(page_map, key=lambda candidate: abs(candidate - page))
                target_file = page_map[nearest]
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
                    "target_file_probability": 0.98 if target_file else None,
                    "section_start_file": open_entry["section_start_file"],
                    "editorial_anchor_file": source_file,
                    "confidence": 0.93 if target_file else 0.72,
                    "raw_json": {
                        "source": "page_map",
                        "page_refs_detected": page_refs,
                    },
                }
            )
        open_entry = None

    def switch_section(new_section: str) -> None:
        nonlocal current_section, current_node_key
        flush_entry()
        current_section = new_section
        current_node_key = None

    for path in working_files:
        seq = int(re.search(r"-(\d+)\.txt$", path.name).group(1))
        assigned_section = "analytic_1"
        if seq == 896:
            assigned_section = "analytic_1"
        elif seq in {900, 901, 902, 904}:
            assigned_section = "rerum"
        elif seq in {905, 906}:
            assigned_section = "ordo"

        section_files[assigned_section].add(path.as_posix())

        raw = path.read_text(encoding="utf-8", errors="replace")
        blocks = iter_blocks(raw)
        for block_type, lines in blocks:
            if block_type not in {"texto_principal", "nota_marginal"}:
                continue
            for line in lines:
                compact = normalize_line(line)
                if not compact or NOISE_RE.fullmatch(compact):
                    continue
                if re.fullmatch(r"\d{1,4}", compact):
                    continue
                if re.fullmatch(r"[A-Z]\d+[A-Za-z]*\.?", compact):
                    continue
                marker_section = section_for_title(compact)
                if marker_section is not None and compact.upper() in TITLE_MARKERS:
                    switch_section(marker_section)
                    continue
                if compact == "INDICULUS":
                    continue
                if compact.startswith("EORUM AD QUOS SCRIPTÆ SUNT SYNESII EPISTOLÆ."):
                    switch_section("addressee")
                    continue
                if compact == "INDEX" or compact.startswith("Rerum insigniorum"):
                    # Structural title fragment within the rerum section.
                    continue
                if is_letter_marker(compact):
                    flush_entry()
                    node_order_by_section[current_section] += 1
                    node_order = node_order_by_section[current_section]
                    current_node_key = make_node_key(SECTION_KEYS[current_section], node_order)
                    nodes.append(
                        {
                            "node_key": current_node_key,
                            "section_key": SECTION_KEYS[current_section],
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": compact.rstrip("."),
                            "label_norm": sort_norm(compact.rstrip(".")),
                            "label_sort": sort_norm(compact.rstrip(".")),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source": "ocr_letter_marker", "file": path.as_posix()},
                        }
                    )
                    continue
                if compact.startswith("-") and len(compact) <= 3:
                    continue
                if open_entry is None:
                    open_entry = {
                        "section_name": current_section,
                        "section_key": SECTION_KEYS[current_section],
                        "section_start_file": next(iter(SECTION_META[current_section].get("file_start") or [None]), None) if False else path.as_posix(),
                        "editorial_anchor_file": path.as_posix(),
                        "parent_node_key": current_node_key,
                        "heading_letter": compact[:1] if current_node_key else None,
                        "entry_raw": compact,
                    }
                else:
                    prev_text = normalize_space(open_entry["entry_raw"])
                    continue_prev = (
                        compact[:1].islower()
                        or compact[:1] in "([;,:-"
                        or prev_text.endswith(("-", ",", ";", ":"))
                        or not re.search(r"[.!?]\s*$", prev_text)
                    )
                    if continue_prev:
                        open_entry["entry_raw"] += " " + compact
                    else:
                        flush_entry()
                        open_entry = {
                            "section_name": current_section,
                            "section_key": SECTION_KEYS[current_section],
                            "section_start_file": path.as_posix(),
                            "editorial_anchor_file": path.as_posix(),
                            "parent_node_key": current_node_key,
                            "heading_letter": compact[:1] if current_node_key else None,
                            "entry_raw": compact,
                        }
        flush_entry()

    helper_request = build_helper_request(entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {"status": "empty", "entries": []}
    lookup = helper_lookup(helper_output)

    # Merge helper evidence into the sampled entries only.
    for entry in entries:
        helper_item = lookup.get(entry["entry_key"])
        if not helper_item:
            continue
        entry["raw_json"]["helper"] = helper_item
        if helper_item.get("best_candidate", {}).get("file"):
            entry["target_file_best"] = helper_item["best_candidate"]["file"]
            entry["confidence"] = max(entry["confidence"], float(helper_item["best_candidate"].get("probability") or 0.8))

    sections = build_sections(source_root, files, section_files)

    # Final metadata helpers.
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PG066 alphabetical payload assembled from OCR tail with helper-backed samples.",
        "completed": [
            "read OCR tail pages",
            "built page map from headers",
            "parsed section markers and line entries",
        ],
        "pending": [
            "validate payload structure",
            "inspect any rejected OCR line splits if needed",
        ],
        "blocked": [],
        "notes": [
            "OCR file suffixes are separate from printed page numbers.",
            "Helper was run only on a small sample of representative entries.",
        ],
    }
    write_json(intermediate_dir / TODO_FILENAME, todo)
    write_json(intermediate_dir / "page_map.json", page_map)
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(intermediate_dir / "helper_output.json", helper_output)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Final OCR-tail payload for Synesius addressees, subject indexes, and closing Ordo Rerum.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the addressee index, subject indexes, and closing Ordo Rerum from the OCR tail, preserving literal page locators and section markers.",
            "evidence_files": [path.as_posix() for path in working_files],
        },
        "notes": [
            "The OCR tail mixes an addressee index, two subject-index rubrics, and a closing Ordo Rerum table.",
            "OCR file suffixes were not treated as editorial page numbers; page refs were resolved from the printed headers.",
        ],
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--helper-request-json", type=Path, required=True)
    parser.add_argument("--helper-output-json", type=Path, required=True)
    parser.add_argument("--intermediate-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

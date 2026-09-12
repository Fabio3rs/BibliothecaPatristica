#!/usr/bin/env python3
"""Usage: build the PL179 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl179_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL179/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL179_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL179_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL179 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL179_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL179"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 179"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL179/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL179_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL179_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL179_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL179"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_FILES = list(range(915, 935))
ORDO_FILES = list(range(935, 951))

INDEX_SECTION_KEY = f"{VOLUME_ID}:section:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:section:002"

INDEX_HEADING_RAW = (
    "INDEX ANALYTICUS IN WILLELMI MALMESBURIENSIS GESTA REGUM ANGLORUM ET HISTORIAM NOVELLAM."
)
ORDO_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

SECTION_PAGE_RANGES = {
    INDEX_SECTION_KEY: (1819, 1856),
    ORDO_SECTION_KEY: (1859, 4888),
}

CONTINUATION_PREFIXES = {
    "Ejus",
    "Ejusdem",
    "Eorum",
    "Ipsa",
    "Ipsum",
    "Idem",
    "Item",
    "Hic",
    "Hæc",
    "Haec",
    "Hoc",
    "Quod",
    "Quae",
    "Quæ",
    "Quibus",
    "Qui",
    "Cui",
    "Unde",
    "Sic",
    "Similiter",
    "Tunc",
    "Tum",
    "Ut",
    "Ne",
    "Sed",
}

NOISE_LINES = {
    "Digitized by Google",
}


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
    return sorted(source_root.glob("*.txt"), key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)))


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, re.S):
        blocks.append((match.group(1).strip().lower(), match.group(2)))
    return blocks


def block_lines(path: Path) -> tuple[list[str], list[str]]:
    body_lines: list[str] = []
    notes: list[str] = []
    for block_type, content in extract_blocks(path):
        lines = [line.strip() for line in content.splitlines()]
        if block_type == "texto_principal":
            for line in lines:
                cleaned = normalize(line)
                if cleaned:
                    body_lines.append(cleaned)
        elif block_type == "nota_marginal":
            for line in lines:
                cleaned = normalize(line)
                if cleaned:
                    notes.append(cleaned)
    return body_lines, notes


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_ocr_page_xml(text)
        for header in (parsed.get("header_text") or "").splitlines():
            for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
                page = int(match.group(1))
                if page > 0:
                    page_map.setdefault(page, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def target_for_page(page: int | None, page_map: dict[int, str], source_root: Path) -> str | None:
    if page is None:
        return None
    if page in page_map:
        return page_map[page]
    for path in discover_files(source_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_ocr_page_xml(text)
        header = parsed.get("header_text") or ""
        if re.search(rf"(?<!\d){page}(?!\d)", header):
            return str(path)
    return None


def split_fragments(line: str) -> list[str]:
    line = normalize(line) or ""
    if not line:
        return []
    line = line.replace("…", ".")
    parts = re.split(r"(?<=[.;])\s+(?=[A-ZÆŒ])", line)
    return [part.strip() for part in parts if part.strip()]


def is_continuation(fragment: str) -> bool:
    if not fragment:
        return True
    if fragment[0].islower():
        return True
    token = fragment.split(None, 1)[0].strip(",:;.")
    if token in CONTINUATION_PREFIXES:
        return True
    if fragment.startswith("et ") or fragment.startswith("et,"):
        return True
    return False


def merge_fragments(fragments: Iterable[str]) -> list[str]:
    merged: list[str] = []
    current = ""
    for fragment in fragments:
        fragment = normalize(fragment) or ""
        if not fragment:
            continue
        if current and is_continuation(fragment):
            current = f"{current} {fragment}".strip()
            continue
        if current:
            merged.append(current)
        current = fragment
    if current:
        merged.append(current)
    return merged


def split_entry_line(line: str) -> list[str]:
    return merge_fragments(split_fragments(line))


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def first_alpha_letter(text: str | None) -> str | None:
    value = normalize(text) or ""
    for ch in value:
        if ch.isalpha():
            return ch.upper()
    return None


def derive_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    page_match = re.search(r"(?<!\d)(\d{1,4})(?!\d)", text)
    if page_match:
        text = text[: page_match.start()].rstrip(" ,;:.")
    text = text.rstrip(" .;:")
    return text or None


def entry_kind(section_key: str, entry_raw: str, has_refs: bool) -> str:
    text = normalize(entry_raw) or ""
    if section_key == ORDO_SECTION_KEY:
        return "heading_group"
    if not has_refs and re.search(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", text, re.IGNORECASE):
        return "cross_reference"
    if not has_refs and len(text) <= 28 and not re.search(r"[.,;]", text):
        return "heading_group"
    return "lemma"


def build_sections() -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for order, (section_key, heading_raw, start_file, end_file) in enumerate(
        [
            (
                INDEX_SECTION_KEY,
                INDEX_HEADING_RAW,
                str(DEFAULT_SOURCE_ROOT / "df80a67a-4cf3-41db-bcd2-86027b5427b2-915.txt"),
                str(DEFAULT_SOURCE_ROOT / "df80a67a-4cf3-41db-bcd2-86027b5427b2-934.txt"),
            ),
            (
                ORDO_SECTION_KEY,
                ORDO_HEADING_RAW,
                str(DEFAULT_SOURCE_ROOT / "df80a67a-4cf3-41db-bcd2-86027b5427b2-935.txt"),
                str(DEFAULT_SOURCE_ROOT / "df80a67a-4cf3-41db-bcd2-86027b5427b2-950.txt"),
            ),
        ],
        start=1,
    ):
        page_start, page_end = SECTION_PAGE_RANGES[section_key]
        sections.append(
            {
                "section_key": section_key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": "analytic_subject" if section_key == INDEX_SECTION_KEY else "ordo_rerum",
                "heading_raw": heading_raw,
                "heading_norm": sort_norm(heading_raw),
                "heading_letter": None,
                "page_start": page_start,
                "page_end": page_end,
                "file_start": start_file,
                "file_end": end_file,
                "confidence": 0.95 if section_key == INDEX_SECTION_KEY else 0.92,
                "raw_json": {
                    "section_kind_reason": (
                        "Alphabetical analytical index headed INDEX ANALYTICUS."
                        if section_key == INDEX_SECTION_KEY
                        else "Closing ORDO RERUM contents table for the tome; editorially distinct from the alphabetical index."
                    ),
                    "evidence_files": [start_file, end_file],
                },
            }
        )
    return sections


def build_nodes(index_notes: list[tuple[int, str]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node_order, (page_seq, letter) in enumerate(index_notes, start=1):
        if letter in seen:
            continue
        seen.add(letter)
        nodes.append(
            {
                "node_key": f"{VOLUME_ID}:node:letter:{letter}",
                "section_key": INDEX_SECTION_KEY,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.9,
                "raw_json": {
                    "source_page_seq": page_seq,
                    "kind": "marginal letter group",
                },
            }
        )
    return nodes


def parse_sections(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = discover_files(source_root)
    page_map = build_page_map(files)

    index_entries: list[dict[str, Any]] = []
    ordo_entries: list[dict[str, Any]] = []
    index_notes: list[tuple[int, str]] = []

    for path in files:
        seq = file_seq(path)
        if seq not in set(INDEX_FILES) | set(ORDO_FILES):
            continue
        body_lines, notes = block_lines(path)
        if seq in INDEX_FILES:
            for note in notes:
                if len(note) == 1 and note.isalpha():
                    index_notes.append((seq, note))
            lines = body_lines
            # Drop obvious heading residue if present in OCR body.
            cleaned_lines = [
                line
                for line in lines
                if line not in NOISE_LINES and not line.startswith("INDEX ANALYTICUS")
                and not line.startswith("IN WILLELMI MALMESBURIENSIS")
                and not line.startswith("(Revocatur lector")
            ]
            for line in cleaned_lines:
                for entry_raw in split_entry_line(line):
                    page_hints = extract_page_hints(entry_raw)
                    if not page_hints and len(entry_raw) < 4:
                        continue
                    lemma_raw = derive_lemma(entry_raw) if not re.search(r"\b(?:vide|vid\.?|voir|cf\.?|id\.?)\b", entry_raw, re.I) else None
                    entry_key = f"{VOLUME_ID}:entry:{len(index_entries) + 1:04d}"
                    entry = {
                        "entry_key": entry_key,
                        "section_key": INDEX_SECTION_KEY,
                        "parent_node_key": None,
                        "entry_order": len(index_entries) + 1,
                        "entry_kind": entry_kind(INDEX_SECTION_KEY, entry_raw, bool(page_hints)),
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": entry_raw,
                        "context_raw": entry_raw,
                        "heading_letter": first_alpha_letter(lemma_raw or entry_raw),
                        "inferred_printed_page": page_hints[0] if page_hints else None,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "target_file_best": target_for_page(page_hints[0], page_map, source_root) if page_hints else str(path),
                        "confidence": 0.74 if page_hints else 0.58,
                        "raw_json": {
                            "source_file": str(path),
                            "page_hints": page_hints,
                            "section_kind": "analytic_subject",
                            "split_strategy": "line_plus_sentence",
                        },
                    }
                    index_entries.append(entry)
        else:
            for line in body_lines:
                if line in NOISE_LINES:
                    continue
                if line.startswith("ORDO RERUM"):
                    continue
                if line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HIOC TOMO CONTINENTUR"):
                    continue
                for entry_raw in split_entry_line(line):
                    page_hints = extract_page_hints(entry_raw)
                    if not page_hints and len(entry_raw) < 4:
                        continue
                    lemma_raw = derive_lemma(entry_raw)
                    entry_key = f"{VOLUME_ID}:entry:{len(index_entries) + len(ordo_entries) + 1:04d}"
                    entry = {
                        "entry_key": entry_key,
                        "section_key": ORDO_SECTION_KEY,
                        "parent_node_key": None,
                        "entry_order": len(ordo_entries) + 1,
                        "entry_kind": entry_kind(ORDO_SECTION_KEY, entry_raw, bool(page_hints)),
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": entry_raw,
                        "context_raw": entry_raw,
                        "heading_letter": first_alpha_letter(lemma_raw or entry_raw),
                        "inferred_printed_page": page_hints[0] if page_hints else None,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "target_file_best": target_for_page(page_hints[0], page_map, source_root) if page_hints else str(path),
                        "confidence": 0.82 if page_hints else 0.6,
                        "raw_json": {
                            "source_file": str(path),
                            "page_hints": page_hints,
                            "section_kind": "ordo_rerum",
                            "split_strategy": "line_plus_sentence",
                        },
                    }
                    ordo_entries.append(entry)

    nodes = build_nodes(index_notes)
    entries = index_entries + ordo_entries
    refs: list[dict[str, Any]] = []
    helper_request_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = (entry.get("raw_json") or {}).get("page_hints") or []
        if not page_hints:
            continue
        helper_request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:80],
                "query_names": [q for q in dict.fromkeys(
                    [
                        entry.get("lemma_raw") or "",
                        (entry.get("entry_raw") or "").split(",", 1)[0],
                        (entry.get("entry_raw") or "").split(".", 1)[0],
                    ]
                ) if q],
                "page_hints": [str(page) for page in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry["entry_raw"],
            }
        )

    # Keep the helper request bounded so the locator can finish quickly on this large tail.
    helper_request_entries = helper_request_entries[:24]
    return entries, refs, nodes, helper_request_entries


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
        raise SystemExit(
            f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def attach_helper(entries: list[dict[str, Any]], helper_output: dict[str, Any], page_map: dict[int, str], source_root: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    helper_by_id = {
        item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)
    }
    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"]) or {}
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
                for cand in (helper.get("candidates") or [])[:5]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            raw_json["helper_best_file"] = best.get("file")
            raw_json["helper_best_probability"] = best.get("probability")
        page_hints = raw_json.get("page_hints") or []
        for ref_order, page in enumerate(page_hints, start=1):
            target_file = best.get("file") if best.get("file") else target_for_page(page, page_map, source_root)
            refs.append(
                {
                    "entry_key": entry["entry_key"],
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
                    "target_file_probability": best.get("probability") if best.get("file") else None,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.93 if target_file else 0.68,
                    "raw_json": {
                        "source_file": entry["raw_json"]["source_file"],
                        "locator_method": "helper_best_candidate" if best.get("file") else "header_page_map",
                        "helper": helper.get("best_candidate") if helper else None,
                    },
                }
            )
    return refs


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    entries, _, nodes, helper_request_entries = parse_sections(source_root)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request_entries else {"status": "empty", "entries": []}
    refs = attach_helper(entries, helper_output, page_map, source_root)

    sections = build_sections()
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the analytic index and the closing ORDO RERUM contents table from the OCR tail.",
        "evidence_files": [
            str(source_root / "df80a67a-4cf3-41db-bcd2-86027b5427b2-915.txt"),
            str(source_root / "df80a67a-4cf3-41db-bcd2-86027b5427b2-934.txt"),
            str(source_root / "df80a67a-4cf3-41db-bcd2-86027b5427b2-935.txt"),
            str(source_root / "df80a67a-4cf3-41db-bcd2-86027b5427b2-950.txt"),
        ],
    }
    notes = [
        "The OCR tail interleaves the analytical index and the ORDO RERUM contents table; the editorial order is separate from the OCR suffix order.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
        "OCR file suffixes, printed pages, and cited pages were kept distinct in refs and entry metadata.",
    ]

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
            "current_focus": "Finalize PL179 alphabetical payload and keep the analytical index separate from ORDO RERUM.",
            "completed": [
                "identified the PL179 tail sections",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Preserve OCR literals and keep printed-page references separate from OCR suffixes.",
                "Recheck helper evidence only if a ref target looks inconsistent with the OCR header map.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL179 alphabetical payload.")
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

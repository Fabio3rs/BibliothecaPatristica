#!/usr/bin/env python3
"""Usage: build the PG132 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG132_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG132/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG132_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG132_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG132 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG132_alphabetical_indices.json
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
VOLUME_ID = "PG132"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 132"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG132/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG132_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG132_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG132_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG132"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING = "INDEX RERUM NOTABILIUM QUÆ IN HOMILIIS THEOPHANIS CERAMEI, ITEMQUE IN PROŒMIIS AC NOTIS CONTINENTUR."
SECTION_REASON = (
    "Alphabetical analytical index headed INDEX RERUM NOTABILIUM; "
    "the OCR tail shows letter-group dividers and page-locator entries "
    "before the closing ORDO RERUM table begins."
)

SECTION_START_SEQ = 692
SECTION_END_SEQ = 702

PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–]\s*(\d{1,4})")
PAGE_COL_RE = re.compile(r"(?<!\d)(\d{1,4})\s*([ABCDabcd])\b")
IBID_COL_RE = re.compile(r"\bibid\.?\s*,?\s*([ABCDabcd])\b", re.IGNORECASE)
IBID_RE = re.compile(r"\bibid\.?\b", re.IGNORECASE)
LEADING_NOISE_RE = re.compile(r"^\d{3,4}\s+INDEX IN THEOPHANEM\.?\s+\d{3,4}\s*")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒ]$")
SINGLE_INITIAL_PROTECT_RE = re.compile(r"\b([A-Z]{1,3})\.(?=\s+[A-ZÆŒ])")
BOUNDARY_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ])")
LEADING_LEMMA_SPLIT_RE = re.compile(r"^([A-ZÆŒ][^,;:.]{1,120}?)(?:,|\s{2,}|$)")
ENTRY_START_RE = re.compile(r"^[A-ZÆŒ]")
REMISSION_RE = re.compile(r"\b(?:vide|vid\.?|voir|cf\.?|id\.)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = unicodedata.normalize("NFKD", value)
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def discover_files(source_root: Path) -> list[Path]:
    selected: list[Path] = []
    for seq in range(SECTION_START_SEQ, SECTION_END_SEQ + 1):
        path = source_root / f"644767fd-0f61-4d34-92c5-4c3c5dd31095-{seq}.txt"
        if path.exists():
            selected.append(path)
    return selected


def extract_page_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed.get("all_text", "").splitlines():
        text = normalize(raw)
        if not text or text == "Digitized by Google" or text.isdigit():
            continue
        if LEADING_NOISE_RE.fullmatch(text):
            continue
        if LETTER_ONLY_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in sorted(files, key=lambda p: int(p.stem.rsplit("-", 1)[-1])):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        blobs = [parsed.get("header_text") or "", parsed.get("body_text") or "", (parsed.get("all_text") or "")[:240]]
        for blob in blobs:
            for match in PAGE_RE.finditer(blob):
                page = int(match.group(1))
                if 1 <= page <= 2000 and page not in mapping:
                    mapping[page] = str(path)
    return mapping


def protect_initial_abbreviations(text: str) -> str:
    return SINGLE_INITIAL_PROTECT_RE.sub(lambda m: f"{m.group(1)}§", text)


def split_fragments(text: str) -> list[str]:
    protected = protect_initial_abbreviations(normalize(text) or "")
    parts = [part.replace("§", ".").strip() for part in BOUNDARY_RE.split(protected) if part.strip()]
    return parts or [normalize(text) or ""]


def is_continuation(fragment: str) -> bool:
    stripped = fragment.lstrip()
    if not stripped:
        return True
    first = stripped[0]
    if first.islower() or first.isdigit():
        return True
    if re.match(r"^[A-Z](?:,\s*[A-Z])+\.\s*", stripped):
        return True
    if REMISSION_RE.match(stripped):
        return True
    if stripped.startswith(("ibid.", "ibid", "et ", "n.", "In ", "Quo ", "Quid ", "Cur ")):
        return False
    return False


def lemma_from_fragment(fragment: str) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    first_loc = re.search(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?:\s*([ABCDabcd]))?", text)
    if first_loc:
        lemma = text[: first_loc.start()].strip(" ,;:.")
        if lemma:
            return lemma
    leading = LEADING_LEMMA_SPLIT_RE.match(text)
    if leading:
        return leading.group(1).strip(" ,;:.")
    return text.strip(" ,;:.") or None


def infer_entry_kind(fragment: str) -> str:
    text = normalize(fragment) or ""
    if REMISSION_RE.search(text) and not re.search(r"\d", text):
        return "cross_reference"
    if text.isupper() or re.fullmatch(r"[A-ZÆŒ]", text):
        return "heading_group"
    return "lemma"


def extract_refs(fragment: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    working = normalize(fragment) or ""

    for match in PAGE_RANGE_RE.finditer(working):
        start = int(match.group(1))
        end = int(match.group(2))
        ref_raw = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end),
            }
        )
        last_page = end
        working = working.replace(match.group(0), " ", 1)

    for match in PAGE_COL_RE.finditer(working):
        page = int(match.group(1))
        col = match.group(2).upper()
        ref_raw = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_page_column",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page
        working = working.replace(match.group(0), " ", 1)

    for match in IBID_COL_RE.finditer(working):
        if last_page is None:
            continue
        col = match.group(1).upper()
        ref_raw = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_page_column",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": last_page,
                "page_ref_col": col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        working = working.replace(match.group(0), " ", 1)

    for match in IBID_RE.finditer(working):
        if last_page is None:
            continue
        ref_raw = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        working = working.replace(match.group(0), " ", 1)

    for match in PAGE_RE.finditer(working):
        page = int(match.group(1))
        if page < 1 or page > 2000:
            continue
        ref_raw = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        last_page = page

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref["ref_kind"],
            ref["page_ref_int"],
            ref["page_ref_col"],
            ref["range_start_raw"],
            ref["range_end_raw"],
            ref["ref_raw"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped, last_page


def build_fragments(files: list[Path]) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    current_file: str | None = None
    pending = ""
    for path in files:
        current_file = str(path)
        for line in extract_page_lines(path):
            if not line:
                continue
            if line in {SECTION_HEADING, "QUÆ", "IN HOMILIIS THEOPHANIS CERAMEI, ITEMQUE IN PROŒMIIS", "AC NOTIS CONTINENTUR."}:
                continue
            if LETTER_ONLY_RE.fullmatch(line):
                continue
            if line.startswith("Digitized by Google"):
                continue
            for piece in split_fragments(line):
                if not piece:
                    continue
                if piece.startswith(
                    (
                        "INDEX RERUM NOTABILIUM",
                        "QUÆ IN HOMILIIS THEOPHANIS CERAMEI",
                        "IN HOMILIIS THEOPHANIS CERAMEI",
                        "AC NOTIS CONTINENTUR",
                        "INDEX IN THEOPHANEM",
                        "ORDO RERUM",
                    )
                ):
                    continue
                if pending and is_continuation(piece):
                    pending = f"{pending} {piece}".strip()
                    continue
                if pending:
                    fragments.append({"text": pending, "source_file": current_file})
                pending = piece
    if pending:
        fragments.append({"text": pending, "source_file": current_file})
    return fragments


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_HEADING,
            "heading_norm": sort_norm(SECTION_HEADING),
            "heading_letter": None,
            "page_start": 1269,
            "page_end": 1288,
            "file_start": str(files[0]) if files else None,
            "file_end": str(files[-1]) if files else None,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": SECTION_REASON,
                "evidence_files": [str(files[0]) if files else None, str(files[-1]) if files else None],
                "source": "OCR tail pages 692-702",
            },
        }
    ]


def compact_helper_entry(helper: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper:
        return None
    best = helper.get("best_candidate") or {}
    candidates: list[dict[str, Any]] = []
    for candidate in (helper.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in candidate.get("evidence", []) if isinstance(ev, dict) and ev.get("kind")],
            }
        )
    return {
        "status": helper.get("status"),
        "candidate_role": helper.get("candidate_role"),
        "reason_summary": helper.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper.get("candidates") or []),
        "candidates": candidates,
    }


def query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    candidates = [lemma_raw, normalize(entry_raw).split(",", 1)[0], normalize(entry_raw).split(";", 1)[0]]
    seen: set[str] = set()
    names: list[str] = []
    for item in candidates:
        value = normalize(item)
        if value and value not in seen:
            names.append(value)
            seen.add(value)
    return names[:5]


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit(f"No OCR files found under {source_root}")

    page_map = build_page_map(list(source_root.glob("*.txt")))
    sections = build_sections(files)
    fragments = build_fragments(files)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_by_id: dict[str, dict[str, Any]] = {}
    helper_by_id: dict[str, Any] = {}
    helper_limit = 120

    seen_letters: list[str] = []
    entry_counter = 0
    last_page_seen: int | None = None
    section_start_file = str(files[0]) if files else None

    for frag in fragments:
        fragment_text = normalize(frag["text"]) or ""
        if not fragment_text:
            continue
        if fragment_text in {SECTION_HEADING, "QUÆ", "IN HOMILIIS THEOPHANIS CERAMEI, ITEMQUE IN PROŒMIIS", "AC NOTIS CONTINENTUR."}:
            continue

        entry_counter += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
        lemma_raw = lemma_from_fragment(fragment_text)
        entry_kind = infer_entry_kind(fragment_text)
        refs_for_entry, last_page_seen = extract_refs(fragment_text, last_page_seen)
        page_hints = [ref["page_ref_int"] for ref in refs_for_entry if ref.get("page_ref_int") is not None]
        page_hints = list(dict.fromkeys(page_hints))
        target_file_best = None
        for hint in page_hints:
            candidate = page_map.get(hint)
            if candidate:
                target_file_best = candidate
                break
        if target_file_best is None:
            target_file_best = frag["source_file"]

        heading_letter = None
        if lemma_raw:
            for ch in lemma_raw:
                if ch.isalpha():
                    heading_letter = ch.upper()
                    break
        if heading_letter and heading_letter not in seen_letters:
            seen_letters.append(heading_letter)

        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_counter,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw) if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
            "entry_raw": fragment_text,
            "context_raw": None,
            "heading_letter": heading_letter,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": frag["source_file"],
            "target_file_best": target_file_best,
            "confidence": 0.77 if page_hints else 0.58,
            "raw_json": {
                "source_file": frag["source_file"],
                "page_hints": page_hints,
                "entry_kind_reason": "Conservative OCR fragment from the alphabetical index tail.",
                "fragment_length": len(fragment_text),
            },
        }
        entries.append(entry)
        entry_by_id[entry_key] = entry

        if (
            len(helper_entries) < helper_limit
            and page_hints
            and (
                len(page_hints) > 1
                and (
                    "ibid" in fragment_text.lower()
                    or "vid." in fragment_text.lower()
                    or "voir" in fragment_text.lower()
                    or "cf." in fragment_text.lower()
                    or entry_kind == "cross_reference"
                )
                or target_file_best == frag["source_file"]
            )
        ):
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or fragment_text[:120],
                    "query_names": query_names(lemma_raw, fragment_text),
                    "page_hints": [str(page) for page in page_hints[:3]],
                    "page_hint_ints": page_hints[:3],
                    "context_raw": fragment_text[:220],
                }
            )

        for ref_order, ref in enumerate(refs_for_entry, start=1):
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
                    "target_file": page_map.get(ref["page_ref_int"]),
                    "target_file_probability": 0.98 if page_map.get(ref["page_ref_int"]) else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": frag["source_file"],
                    "confidence": 0.88 if page_map.get(ref["page_ref_int"]) else 0.65,
                    "raw_json": {
                        "source_file": frag["source_file"],
                        "locator_method": "page_map" if page_map.get(ref["page_ref_int"]) else "unresolved",
                    },
                }
            )

    for order, letter in enumerate(seen_letters, start=1):
        nodes.append(
            {
                "node_key": f"{VOLUME_ID}:node:letter:{order:02d}",
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.97,
                "raw_json": {"source": "first-letter grouping from OCR fragments"},
            }
        )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)

    if helper_entries:
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
        helper_output = json.loads(helper_output_json.read_text(encoding="utf-8"))
    else:
        helper_output = {"status": "empty", "entries": []}
        write_json(helper_output_json, helper_output)

    for item in helper_output.get("entries", []):
        if isinstance(item, dict) and item.get("entry_id"):
            helper_by_id[item["entry_id"]] = item

    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        compact = compact_helper_entry(helper)
        entry.setdefault("raw_json", {})["helper"] = compact
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        helper = helper_by_id.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file") and not ref.get("target_file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Alphabetical index tail recovered from OCR pages 692-702 with conservative line-fragment segmentation.",
        "evidence_files": [str(path) for path in files],
    }

    notes = [
        "PG132 final alphabetical index extracted from INDEX RERUM NOTABILIUM through the closing entries before ORDO RERUM.",
        "Helper evidence was attached only to ambiguous fragments with multiple page hints or remissive wording.",
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
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG132 alphabetical payload assembled and ready for validation.",
            "completed": [
                "OCR tail inspected",
                "fragment segmentation implemented",
                "helper request generated",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The OCR tail is dense and some sentence boundaries are conservative.",
                "Page-map based targets were retained when helper evidence was unavailable.",
            ],
        },
    )
    write_json(intermediate_dir / "manifest.json", {"updated_at": now_iso(), "generated_at": payload["generated_at"], "volume_id": VOLUME_ID})
    write_json(intermediate_dir / "volume_payload.json", payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG132 alphabetical payload from OCR fragments.")
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

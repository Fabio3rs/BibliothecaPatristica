#!/usr/bin/env python3
"""Usage: build the PG004 alphabetical payload from the OCR index tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG004_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG004/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG004_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG004_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG004 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG004_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG004"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 4"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG004/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG004_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG004_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG004_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG004"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_FILE_SEQS = [551, 552, 553, 554]
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_HEADING_RAW = "INDEX RERUM MEMORABILIUM QUÆ IN HOC TOMO CONTINENTUR."

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LINE_NOISE_RE = re.compile(
    r"^(?:INDEX RERUM MEMORABILIUM|INDEX RERUM ET VERBORUM|ORDO RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)$",
    re.IGNORECASE,
)
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à|,|;)\s*(\d{1,4}))?")
SPLIT_RE = re.compile(r"(?<!\b[A-ZÆŒ])(?<=[\d\w\)])(?:[.;])\s+(?=[A-ZÆŒΑ-Ω])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


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


def discover_files(source_root: Path) -> list[Path]:
    files = []
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


def page_num_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in PAGE_HEADER_RE.finditer(header):
            token = int(match.group(1))
            if token < 1:
                continue
            page_map.setdefault(token, str(path))
    return page_map


def extract_body_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw in body.splitlines():
        line = normalize(raw)
        if not line:
            continue
        if LINE_NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_section_text(files: list[Path]) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for path in files:
        lines = extract_body_lines(path)
        for line_no, line in enumerate(lines, start=1):
            if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                continue
            fragments.append({"file": str(path), "line_no": line_no, "text": line})
    return fragments


def stitch_fragments(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stitched: list[dict[str, Any]] = []
    for frag in fragments:
        text = frag["text"]
        if not stitched:
            stitched.append(dict(frag))
            continue
        prev = stitched[-1]
        if re.fullmatch(r"[A-ZÆŒ]\.", text):
            prev["text"] = f"{prev['text']} {text}"
            continue
        if re.fullmatch(r"[a-zæœ].*", text):
            prev["text"] = f"{prev['text']} {text}"
            continue
        if prev["text"].endswith(("S.", "B.", "P.", "D.", "R.", "L.", "M.")):
            prev["text"] = f"{prev['text']} {text}"
            continue
        stitched.append(dict(frag))
    return stitched


def split_entries(text: str) -> list[str]:
    text = re.sub(r"(?<=\w)-\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    raw = [part.strip() for part in SPLIT_RE.split(text) if part and part.strip()]
    merged: list[str] = []
    i = 0
    while i < len(raw):
        part = raw[i].strip()
        if part in {"QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
            i += 1
            continue
        if part in {"INDEX RERUM MEMORABILIUM", "INDEX RERUM ET VERBORUM"}:
            i += 1
            continue
        if re.fullmatch(r"[A-ZÆŒ]", part):
            i += 1
            continue
        if re.fullmatch(r"[A-ZÆŒ]\s+.*", part):
            merged.append(part)
            i += 1
            continue
        if part.endswith(("S.", "B.", "P.", "D.", "R.", "L.", "M.")) and i + 1 < len(raw):
            merged.append(f"{part} {raw[i + 1].strip()}".strip())
            i += 2
            continue
        merged.append(part)
        i += 1
    return merged


def strip_divider_letter(fragment: str) -> tuple[str | None, str]:
    cleaned = fragment.strip()
    m = re.match(r"^([A-ZÆŒ])\s+(.*)$", cleaned)
    if m:
        return m.group(1), m.group(2).strip()
    return None, cleaned


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", text, re.IGNORECASE):
        return None
    if "," in text:
        candidate = text.split(",", 1)[0].strip()
    else:
        candidate = text
    candidate = re.sub(r"\s+\d.*$", "", candidate).strip(" .;:")
    return candidate or None


def page_refs_from_text(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        ref_raw = match.group(0).strip()
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(end) if end is not None else None,
            }
        )
    return refs


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
        "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0]],
        "page_hints": [str(page) for page in page_hints],
        "page_hint_ints": page_hints,
        "context_raw": entry["entry_raw"],
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = [path for path in discover_files(source_root) if file_seq(path) in INDEX_FILE_SEQS]
    if not files:
        raise SystemExit("No PG004 index files found in the requested source_root.")

    page_map = page_num_map(discover_files(source_root))
    fragments = stitch_fragments(build_section_text(files))

    letter_nodes: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    entry_order = 0
    for frag in fragments:
        raw = normalize(frag["text"]) or ""
        if not raw:
            continue
        if raw in {"QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
            continue
        if raw.startswith("INDEX RERUM"):
            continue
        if raw in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
            continue

        letter, entry_text = strip_divider_letter(raw)
        if entry_text in {"INDEX RERUM MEMORABILIUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
            continue
        if not entry_text:
            continue

        split_parts = split_entries(entry_text)
        if not split_parts:
            continue

        for part in split_parts:
            part = normalize(part) or ""
            if not part or part in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                continue
            if part in {"QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."} or part.startswith("INDEX RERUM"):
                continue

            heading_letter = letter or (part[:1].upper() if part[:1].isalpha() else None)
            if heading_letter and heading_letter not in letter_nodes:
                node_key = f"{VOLUME_ID}:node:letter:{heading_letter}"
                letter_nodes[heading_letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": heading_letter,
                        "label_norm": heading_letter.lower(),
                        "label_sort": heading_letter.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": frag["file"], "kind": "alphabetic divider"},
                    }
                )

            page_hints = [m.group(1) for m in PAGE_REF_RE.finditer(part)]
            page_hint_ints = [int(v) for v in page_hints]
            if re.search(r"\b(?:ibid\.?|id\.?|vid\.?|vide|voir|cf\.?)\b", part, re.IGNORECASE):
                entry_kind = "cross_reference" if not page_hints else "lemma"
            else:
                entry_kind = "lemma"

            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            lemma_raw = lemma_from_entry(part) if entry_kind != "cross_reference" else None
            target_file_best = page_map.get(page_hint_ints[0]) if page_hint_ints else str(frag["file"])
            entry = {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": letter_nodes.get(heading_letter) if heading_letter else None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": part,
                "context_raw": part if page_hints else None,
                "heading_letter": heading_letter,
                "inferred_printed_page": page_hint_ints[0] if page_hint_ints else None,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": str(frag["file"]),
                "target_file_best": target_file_best,
                "confidence": 0.88 if page_hints else 0.64,
                "raw_json": {
                    "source_file": frag["file"],
                    "source_line_no": frag["line_no"],
                    "page_hints": page_hint_ints,
                    "section_kind": "analytic_subject",
                    "section_kind_reason": "Alphabetical subject index headed INDEX RERUM MEMORABILIUM with letter-group dividers and page-locator entries.",
                },
            }
            entries.append(entry)
            helper_entry = build_helper_entry(entry)
            if helper_entry is not None:
                helper_entries.append(helper_entry)

            for ref_order, page in enumerate(page_hint_ints, start=1):
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
                        "target_file": page_map.get(page),
                        "target_file_probability": 0.99 if page in page_map else None,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": str(frag["file"]),
                        "confidence": 0.96 if page in page_map else 0.7,
                        "raw_json": {
                            "source_file": frag["file"],
                            "locator_method": "header_page_map" if page in page_map else "unresolved",
                        },
                    }
                )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_entries else {"status": "empty", "entries": []}

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

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": sort_norm(SECTION_HEADING_RAW),
        "heading_letter": None,
        "page_start": 1039,
        "page_end": 1096,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.99,
        "raw_json": {
            "section_kind_reason": "Alphabetical subject index headed INDEX RERUM MEMORABILIUM with page-locator entries and letter-group dividers.",
            "evidence_files": [str(files[0]), str(files[-1])],
        },
    }

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the alphabetical subject index from the final OCR pages. The ORDO RERUM contents table begins after the index and was excluded from extraction.",
        "evidence_files": [str(files[0]), str(files[-1])],
    }
    notes = [
        "The index runs across four OCR files and uses printed-page locators that differ from the file suffixes.",
        "Page locators were resolved from the OCR header map; helper evidence is preserved only where it changed target selection.",
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
        "sections": [section],
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
    write_json(intermediate_dir / "scripture_refs.json", [])
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
            "current_focus": "Finalize PG004 alphabetical payload and keep the index separate from ORDO RERUM.",
            "completed": [
                "identified the alphabetical index files",
                "built helper request and ran index_target_locator",
                "wrote intermediate payload fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed-page locators.",
                "Preserve literal OCR and treat helper evidence as advisory.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG004 alphabetical payload.")
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

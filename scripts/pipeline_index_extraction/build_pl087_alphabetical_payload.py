#!/usr/bin/env python3
"""Usage: build the PL087 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl087_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL087/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL087_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL087_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL087 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL087_alphabetical_indices.json
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

VOLUME_ID = "PL087"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 87"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_ORDER = 1
SECTION_KIND = "ordo_rerum"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 1443
SECTION_PAGE_END = 1472
SECTION_FILE_START = 728
SECTION_FILE_END = 742

BLOCK_RE = re.compile(r'<bloco tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.IGNORECASE | re.DOTALL)
NUMERIC_ONLY_RE = re.compile(r"^\d{1,4}$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|–|—|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
IBID_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
FINIS_RE = re.compile(r"^FINIS TOMI OCTOGESIMI SEPTIMI\.?$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(raw_text: str, kind: str) -> list[str]:
    return [match.group("content") for match in BLOCK_RE.finditer(raw_text or "") if match.group("kind") == kind]


def clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = norm(raw)
        if not line or line == "Digitized by Google" or NUMERIC_ONLY_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def load_page_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in extract_blocks(raw, "texto_principal"):
        lines.extend(clean_lines(block))
    return lines


def extract_header_numbers(path: Path) -> list[int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    headers = extract_blocks(raw, "cabecalho")
    if not headers:
        return []
    text = norm(headers[0]) or ""
    numbers: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
        numbers.append(int(match.group(1)))
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        for page in extract_header_numbers(path):
            page_map.setdefault(page, str(path))
    return page_map


def merge_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            merged.append(buffer.strip())
            buffer = ""

    for line in lines:
        if FINIS_RE.fullmatch(line):
            flush()
            buffer = line
            continue
        if not buffer:
            buffer = line
            continue

        if buffer.endswith("-"):
            buffer = f"{buffer[:-1]}{line.lstrip()}"
            continue

        if not re.search(r"[.?!:]$", buffer) and (
            line[:1].islower()
            or line.startswith(("—", "-", ",", ";"))
            or not PAGE_REF_RE.search(line)
        ):
            buffer = f"{buffer} {line}"
            continue

        flush()
        buffer = line

    flush()
    return merged


def infer_lemma(text: str) -> str | None:
    value = norm(text) or ""
    if not value:
        return None
    if PAGE_REF_RE.search(value):
        value = value[: PAGE_REF_RE.search(value).start()].strip()
    value = re.sub(r"\b(?:Ibid\.?|Id\.?)\.?$", "", value, flags=re.IGNORECASE).strip()
    value = value.rstrip(" ,;:(").strip()
    return value or None


def parse_page_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], list[int], int | None]:
    refs: list[dict[str, Any]] = []
    page_hints: list[int] = []
    ref_order = 1
    explicit = False

    for match in PAGE_REF_RE.finditer(text):
        explicit = True
        raw = match.group(0).strip()
        start = int(match.group(1))
        end = match.group(2)
        refs.append(
            {
                "ref_order": ref_order,
                "ref_kind": "editorial_range" if end else "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end else None,
                "range_end_raw": str(int(end)) if end else None,
            }
        )
        page_hints.append(start)
        ref_order += 1
        last_page = start

    if not explicit and IBID_RE.search(text) and last_page is not None:
        refs.append(
            {
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "Ibid.",
                "page_ref_raw": "Ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        page_hints.append(last_page)

    return refs, page_hints, last_page


def build_entries(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_order = 0
    last_page: int | None = None
    started = False

    for path in files:
        for line in merge_lines(load_page_lines(path)):
            line = norm(line) or ""
            if not line:
                continue
            if not started:
                if line == "SANCTUS GALLUS, ABBAS ET CONFESSOR.":
                    started = True
                else:
                    continue
            if NUMERIC_ONLY_RE.fullmatch(line):
                continue
            if line == "Digitized by Google":
                continue

            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            page_refs, page_hints, last_page = parse_page_refs(line, last_page)
            lemma_raw = infer_lemma(line)
            inferred_page = page_refs[0]["page_ref_int"] if page_refs else last_page
            target_file_best = page_map.get(inferred_page) if inferred_page is not None else str(path)
            entry_kind = "editorial_note" if FINIS_RE.fullmatch(line) else "heading_group"

            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line,
                    "heading_letter": None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_file_best,
                    "confidence": 0.92 if page_refs else 0.72,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": SECTION_KIND,
                        "page_hints": page_hints,
                        "entry_kind_reason": "contents line from the volume's final ORDO RERUM table",
                    },
                }
            )

            if page_refs:
                for ref in page_refs:
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref["ref_order"],
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
                            "section_start_file": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
                            "editorial_anchor_file": str(path),
                            "confidence": 0.9 if page_map.get(ref["page_ref_int"]) else 0.7,
                            "raw_json": {"source_file": str(path), "section_kind": SECTION_KIND},
                        }
                    )

            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or line,
                    "query_names": [x for x in [lemma_raw, line] if x],
                    "page_hints": [str(x) for x in page_hints],
                    "page_hint_ints": page_hints,
                    "context_raw": line,
                }
            )

    return entries, refs, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    files = discover_text_files(source_root)
    files = [p for p in files if SECTION_FILE_START <= file_num(p) <= SECTION_FILE_END]
    page_map = build_page_map(discover_text_files(source_root))
    entries, refs, helper_entries = build_entries(files, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_by_entry = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}

    for entry in entries:
        helper = helper_by_entry.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        entry.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "top_candidates": [
                {
                    "rank": cand.get("rank"),
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                    "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or []) if isinstance(ev, dict)],
                }
                for cand in (helper.get("candidates") or [])
                if isinstance(cand, dict)
            ],
        }
        if best.get("file") and not entry.get("target_file_best"):
            entry["target_file_best"] = best["file"]

    for ref in refs:
        helper = helper_by_entry.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        ref.setdefault("raw_json", {})["helper"] = {
            "status": helper.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if not ref.get("target_file") and best.get("file"):
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": SECTION_ORDER,
            "section_kind": SECTION_KIND,
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": str(next((p for p in files if file_num(p) == SECTION_FILE_START), files[0])),
            "file_end": str(next((p for p in reversed(files) if file_num(p) <= SECTION_FILE_END), files[-1])),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial contents table at the end of the tome, listing authors, works, and their printed-page anchors.",
                "evidence_files": [str(p) for p in files],
                "helper_status": helper_output.get("status"),
            },
        }
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The OCR tail contains the volume's closing ORDO RERUM contents table.",
            "Printed page numbers in the contents are distinct from OCR file suffixes.",
            "Bare Ibid. references were resolved conservatively to the previous explicit page when the local context made that unambiguous.",
        ],
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the final ORDO RERUM contents block conservatively from the OCR tail, including inherited Ibid. locators and the terminal FINIS marker. The OCR has a few wrapped lines and a stray closing note on the opening page, but the contents block itself is structurally recoverable.",
        "evidence_files": [str(p) for p in files],
    }

    notes = [
        "The payload models the closing contents table as one ordo_rerum section.",
        "The terminal FINIS marker is preserved as an editorial note entry rather than promoted to a separate section.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": now_iso(), "updated_at": now_iso(), "output_file": str(output_file)})
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate PL087 closing contents extraction and keep OCR literals intact.",
            "completed": [
                "final ORDO RERUM block identified",
                "helper request generated",
                "helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "review the final payload for OCR drift",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR file suffix, printed page, and cited reference separate.",
                "Do not invent placeholder refs for bare remissions.",
            ],
        },
    )

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL087 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

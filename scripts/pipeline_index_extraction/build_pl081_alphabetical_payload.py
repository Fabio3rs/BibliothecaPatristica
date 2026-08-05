#!/usr/bin/env python3
"""Usage: build the PL081 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl081_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL081/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL081_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL081_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL081 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL081_alphabetical_indices.json
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

VOLUME_ID = "PL081"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 81"

SECTION_HEADING = "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."
SECTION_HEADING_RE = re.compile(r"^ORDO RERUM QU[AEÆ] IN HOC TOMO CONTINENTUR\.$", re.IGNORECASE)
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"

CONTENT_FILE_NUMBERS = [490, 491, 492]

ENTRY_START_RE = re.compile(r"^(?:ISIDORIANORUM\b|CAP\.|CAPUT\b)")
NUMERIC_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)(?!.*\d)")
TERMINAL_IBID_RE = re.compile(r"\bIbid\.?$", re.IGNORECASE)
HEADING_NUM_RE = re.compile(r"^\d{3,4}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


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


def first_nonempty_path(files: list[Path]) -> Path:
    if not files:
        raise ValueError("No OCR files found for the requested section.")
    return files[0]


def extract_block_texts(raw_text: str, block_type: str) -> list[str]:
    pattern = re.compile(rf'<bloco tipo="{re.escape(block_type)}"[^>]*>(.*?)</bloco>', re.DOTALL)
    return [match.group(1) for match in pattern.finditer(raw_text or "")]


def extract_first_block_text(raw_text: str, block_type: str) -> str:
    blocks = extract_block_texts(raw_text, block_type)
    return blocks[0] if blocks else ""


def clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        text = norm(raw)
        if not text:
            continue
        if text.startswith("<"):
            continue
        if text == "Digitized by Google":
            continue
        lines.append(text)
    return lines


def is_footer_line(text: str) -> bool:
    return text in {
        "FINIS TOMI OCTOGESIMI PRIMI.",
        "Parisiis. — Ex Typis J.-P. MIGNET.",
        "3565 012",
    }


def extract_content_lines(files: list[Path]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen_heading = False
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(clean_lines(raw), start=1):
            if SECTION_HEADING_RE.fullmatch(line):
                seen_heading = True
                continue
            if not seen_heading:
                continue
            if "CONTINENTUR" in line and "ORDO RERUM" in line and not SECTION_HEADING_RE.fullmatch(line):
                continue
            if HEADING_NUM_RE.fullmatch(line):
                continue
            if line.startswith("973 ") and "CONTINENTUR" in line:
                continue
            if line in {"575", "576"}:
                # OCR page headers on the 492 file are missing the leading 9.
                continue
            if is_footer_line(line):
                continue
            collected.append({"file": path, "line_no": idx, "text": line})
    return collected


def merge_entry_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        merged_text = " ".join(item["text"] for item in buffer).strip()
        entries.append(
            {
                "entry_raw": merged_text,
                "source_files": [str(item["file"]) for item in buffer],
                "source_line_nos": [item["line_no"] for item in buffer],
                "anchor_file": str(buffer[-1]["file"]),
            }
        )
        buffer = []

    for item in lines:
        text = item["text"]
        if ENTRY_START_RE.match(text) and buffer:
            flush()
        buffer.append(item)
    flush()
    return entries


def strip_terminal_page(text: str) -> tuple[str, int | None, bool]:
    stripped = text.strip()
    inherited = False
    if TERMINAL_IBID_RE.search(stripped):
        stripped = TERMINAL_IBID_RE.sub("", stripped).rstrip(" .;:,")
        inherited = True
    page_match = NUMERIC_PAGE_RE.search(stripped)
    page = int(page_match.group(1)) if page_match else None
    if page_match and page_match.end() == len(stripped):
        stripped = stripped[: page_match.start()].rstrip(" .;:,")
    return stripped, page, inherited


def infer_lemma(entry_text: str) -> str | None:
    text, _, _ = strip_terminal_page(entry_text)
    if " — " in text:
        text = text.split(" — ", 1)[1].strip()
    elif "—" in text:
        text = text.split("—", 1)[1].strip()
    text = text.strip(" .;:")
    return text or None


def extract_last_page(entry_text: str) -> int | None:
    text = entry_text.strip()
    page_match = NUMERIC_PAGE_RE.search(text)
    if page_match and page_match.end() == len(text):
        return int(page_match.group(1))
    return None


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        lemma = infer_lemma(entry["entry_raw"]) or entry["entry_raw"]
        page = entry["inferred_printed_page"]
        page_hints = [str(page)] if page is not None else []
        page_hint_ints = [page] if page is not None else []
        helper_entries.append(
            {
                "entry_id": f"pl081_ordo_{idx:04d}",
                "lemma_raw": lemma,
                "query_names": [lemma, entry["entry_raw"].split("—", 1)[-1].strip()],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any] | None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json)


def page_to_file_map(source_root: Path) -> dict[int, Path]:
    files = discover_text_files(source_root)
    mapping: dict[int, Path] = {}
    header_re = re.compile(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', re.DOTALL)
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        m = header_re.search(raw)
        if not m:
            continue
        header_text = " ".join(line.strip() for line in m.group(1).splitlines())
        nums = [int(x) for x in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header_text)]
        for num in nums[:2]:
            mapping.setdefault(num, path)
    # The contents pages at the end of the volume are OCR-noisy; preserve the best local mapping.
    mapping.setdefault(974, source_root / "394c4f42-76ed-46a8-9127-17182fb18873-491.txt")
    mapping.setdefault(975, source_root / "394c4f42-76ed-46a8-9127-17182fb18873-492.txt")
    mapping.setdefault(976, source_root / "394c4f42-76ed-46a8-9127-17182fb18873-492.txt")
    return mapping


def choose_target_file(entry: dict[str, Any], helper_lookup: dict[str, Any], page_map: dict[int, Path]) -> tuple[str | None, float | None, dict[str, Any]]:
    page = entry["inferred_printed_page"]
    helper = helper_lookup.get(entry["helper_entry_id"])
    helper_best = None
    if isinstance(helper, dict):
        helper_best = helper.get("best_candidate")
    helper_file = helper_best.get("file") if isinstance(helper_best, dict) else None
    helper_prob = helper_best.get("probability") if isinstance(helper_best, dict) else None
    direct_file = str(page_map[page]) if page is not None and page in page_map else None
    target_file = helper_file or direct_file
    target_prob = float(helper_prob) if helper_prob is not None else (1.0 if direct_file else None)
    raw = {
        "source_file": entry["editorial_anchor_file"],
        "helper": helper,
        "page_map_file": direct_file,
    }
    return target_file, target_prob, raw


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = [path for path in discover_text_files(source_root) if file_num(path) in CONTENT_FILE_NUMBERS]
    if len(files) != len(CONTENT_FILE_NUMBERS):
        raise SystemExit("Expected PL081 OCR tail files 490, 491, and 492 to be present.")

    content_lines = extract_content_lines(files)
    merged_entries = merge_entry_lines(content_lines)

    page_map = page_to_file_map(source_root)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    previous_page: int | None = None

    helper_request_stub_entries: list[dict[str, Any]] = []
    temp_entries: list[dict[str, Any]] = []
    for idx, merged in enumerate(merged_entries, start=1):
        entry_raw, page, inherited = strip_terminal_page(merged["entry_raw"])
        inferred_page = page if page is not None else previous_page if inherited else previous_page
        helper_entry_id = f"pl081_ordo_{idx:04d}"
        lemma_raw = infer_lemma(entry_raw)
        temp_entry = {
            "entry_key": f"{VOLUME_ID}:entry:{idx:06d}",
            "helper_entry_id": helper_entry_id,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": idx,
            "entry_kind": "heading_group",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_raw.lower() if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": None,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": merged["anchor_file"],
            "target_file_best": None,
            "confidence": 0.88 if inferred_page is not None else 0.8,
            "raw_json": {
                "source_files": merged["source_files"],
                "source_line_nos": merged["source_line_nos"],
                "section_kind": "ordo_rerum",
                "helper_entry_id": helper_entry_id,
                "page_inference": {
                    "printed_page": inferred_page,
                    "derived_from": "explicit_page" if page is not None else ("inherited_ibid" if inherited else "unresolved"),
                    "has_terminal_ibid": inherited,
                },
            },
        }
        temp_entries.append(temp_entry)
        previous_page = inferred_page if inferred_page is not None else previous_page

    helper_request = build_helper_request(source_root, temp_entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_lookup = {item.get("entry_id"): item for item in (helper_output.get("entries") or [])} if isinstance(helper_output, dict) else {}

    for entry in temp_entries:
        target_file, target_prob, helper_raw = choose_target_file(entry, helper_lookup, page_map)
        helper_data = helper_raw.get("helper") if isinstance(helper_raw, dict) else None
        helper_best = helper_data.get("best_candidate") if isinstance(helper_data, dict) else None
        helper_file = helper_best.get("file") if isinstance(helper_best, dict) else None
        entry["target_file_best"] = target_file
        entry["raw_json"]["helper"] = helper_data
        entry["raw_json"]["target_file_choice"] = {
            "helper_file": helper_file,
            "page_map_file": helper_raw.get("page_map_file") if isinstance(helper_raw, dict) else None,
            "selection": "helper" if helper_file else "page_map",
        }
        if target_file and target_prob is not None:
            entry["raw_json"]["target_file_probability"] = target_prob

        if entry["inferred_printed_page"] is not None:
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(entry["inferred_printed_page"]),
                    "page_ref_raw": str(entry["inferred_printed_page"]),
                    "page_ref_int": entry["inferred_printed_page"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": str(files[0]),
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.82 if target_file else 0.7,
                    "raw_json": {
                        "source_files": entry["raw_json"]["source_files"],
                        "helper": helper_raw.get("helper"),
                        "page_map_file": helper_raw.get("page_map_file"),
                    },
                }
            )

        entries.append(entry)

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING,
            "heading_norm": SECTION_HEADING.lower().replace("æ", "ae").replace("æ", "ae").replace("quae", "quae").replace(".", "").lower(),
            "heading_letter": None,
            "page_start": 971,
            "page_end": 976,
            "file_start": str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.94,
            "raw_json": {
                "section_kind_reason": "Editorial contents table at the end of the volume; OCR headers on the 492 file are noisy, but the page-number sequence and entry order show the table continues through the final tail file.",
                "source_files": [str(path) for path in files],
                "ocr_header_notes": [
                    "490 header reads 971 ORDO RERUM 972",
                    "491 header reads 973 QUAe IN HOC TOMO CONTINENTUR. 971",
                    "492 file contains the contents table continuation and its body page numbers are OCR-noisy (575/576), which fit the 975/976 continuation of the same contents block",
                ],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
            },
        }
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "This tail volume exposes a single ORDO RERUM contents block rather than a separate alphabetical subject index.",
            "OCR page headers in the final tail file are noisy; the printed-page sequence was reconstructed from the contents lines and neighboring headers.",
            "Bare inherited remissions do not occur as standalone refs in this section.",
        ],
    }

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the full ORDO RERUM contents table from the OCR tail and resolved the page references conservatively from the printed page numbers in the table.",
        "evidence_files": [str(path) for path in files],
    }

    notes = [
        "Only the closing ORDO RERUM block was present in the filtered tail for this volume.",
        "Target files were chosen from the helper when available, otherwise from the local printed-page map.",
        "The 492 OCR file lacks a clean header block, so the page numbers 575/576 were interpreted as noisy 975/976 continuations of the contents table.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL081_alphabetical_indices.json")),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate the PL081 ORDO RERUM payload and keep OCR literals intact.",
            "completed": [
                "contents table identified",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "validate the final JSON payload",
            ],
            "blocked": [],
            "notes": [
                "Keep `OCR file`, `printed page`, and `cited reference` separate.",
                "Use `Ibid.` only as inherited page context, not as a standalone ref.",
            ],
        },
    )

    return {
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL081 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True, help="OCR source root for PL081")
    ap.add_argument("--helper-request-json", type=Path, required=True, help="Path to the helper request JSON")
    ap.add_argument("--helper-output-json", type=Path, required=True, help="Path to the helper output JSON")
    ap.add_argument("--intermediate-dir", type=Path, required=True, help="Directory for per-volume intermediate JSON fragments")
    ap.add_argument("--output-file", type=Path, required=True, help="Final payload JSON path")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

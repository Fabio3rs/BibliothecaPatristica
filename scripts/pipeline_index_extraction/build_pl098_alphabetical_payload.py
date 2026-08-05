#!/usr/bin/env python3
"""Usage: build the PL098 ORDO RERUM payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl098_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL098/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL098_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL098_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL098 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL098_alphabetical_indices.json
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


BLOCK_RE = re.compile(
    r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>',
    re.IGNORECASE | re.DOTALL,
)
NOISE_LINES = {"Digitized by Google", "||", "."}
PAGE_TOKEN_RE = re.compile(
    r"(?P<prefix>.*?)(?P<token>(?:col\.?\s*)?(?:Ibid\.|\d{1,4}(?:\s*[-–—]\s*\d{1,4})?))(?:\s*[.)])?\s*$",
    re.IGNORECASE,
)
ITEM_RE = re.compile(
    r"(?P<body>.+?)(?P<token>Ibid\.|\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)(?=(?:\s+[A-ZIVXLCDM§]|$))",
    re.IGNORECASE | re.DOTALL,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    return value or None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and line not in NOISE_LINES]
        if lines:
            blocks.append({"kind": kind, "lines": lines})
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        blocks = extract_blocks(path)
        head_lines: list[str] = []
        for block in blocks[:3]:
            if block["kind"] in {"cabecalho", "outro"}:
                head_lines.extend(block["lines"][:3])
        if not head_lines:
            head_lines = [line for block in blocks[:2] for line in block["lines"][:3]]
        for line in head_lines:
            for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", line):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def lookup_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, float | None]:
    if page is None:
        return None, None
    if page in page_map:
        return page_map[page], 0.99
    candidates: list[tuple[int, int, str]] = []
    for candidate_page, candidate_path in page_map.items():
        distance = abs(candidate_page - page)
        if distance <= 3:
            candidates.append((distance, candidate_page, candidate_path))
        elif str(candidate_page)[-3:] == str(page)[-3:]:
            candidates.append((100 + distance, candidate_page, candidate_path))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2], 0.72


def is_page_header_line(text: str) -> bool:
    cleaned = normalize(text) or ""
    upper = cleaned.upper()
    if "ORDO RERUM" in upper and "CONTINENTUR" in upper and re.search(r"\d{1,4}", cleaned):
        return True
    if re.match(r"^\d{1,4}\s+(?:ORDO RERUM|QU[AEÆ] IN HOC TOMO CONTINENTUR)", cleaned, re.IGNORECASE):
        return True
    return False


def parse_entry_line(text: str) -> tuple[str, int | None, str | None, str | None]:
    cleaned = normalize(text) or ""
    match = PAGE_TOKEN_RE.match(cleaned)
    if not match:
        return cleaned, None, None, None
    prefix = normalize(match.group("prefix")) or ""
    token = normalize(match.group("token")) or ""
    if token.lower().startswith("ibid"):
        return prefix, None, token, "ibid"
    page_part = re.search(r"\d{1,4}", token)
    page = int(page_part.group(0)) if page_part else None
    if prefix:
        entry_text = prefix
    else:
        entry_text = cleaned[: match.start("token")].rstrip(" .")
    return entry_text, page, token, "explicit"


def is_heading_text(text: str) -> bool:
    cleaned = normalize(text) or ""
    if not cleaned:
        return False
    letters = [ch for ch in cleaned if ch.isalpha()]
    if not letters:
        return False
    if cleaned.upper() == cleaned and len(cleaned) <= 160:
        return True
    if cleaned.startswith(("SECTIO", "LIBER", "CAPUT", "CAP.", "APPENDIX", "CARM.", "CARMEN", "ADDENDA", "In ", "De ")):
        return True
    if re.match(r"^[IVXLCDM]+\.", cleaned):
        return True
    if cleaned.startswith("§"):
        return True
    return False


def lemma_norm(text: str | None) -> str | None:
    cleaned = normalize(text)
    if not cleaned:
        return None
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae")
    cleaned = cleaned.replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[.,;:()\"“”]", "", cleaned)
    cleaned = re.sub(r"[\-–—]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def block_text(block: dict[str, Any]) -> str:
    text = " ".join(block["lines"])
    return re.sub(r"\s+", " ", text).strip()


def iter_toc_items(section_files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_page_int: int | None = None
    for path in section_files:
        for block in extract_blocks(path):
            if block["kind"] not in {"cabecalho", "texto_principal", "outro"}:
                continue
            text = block_text(block)
            if not text:
                continue
            if "ORDO RERUM" in text.upper() and "CONTINENTUR" in text.upper() and re.match(r"^\d{1,4}\s+ORDO RERUM", text):
                # Skip page headers; the section metadata carries the heading.
                continue
            for match in ITEM_RE.finditer(text):
                body = normalize(match.group("body")) or ""
                token = normalize(match.group("token")) or ""
                if not body:
                    continue
                if token.lower().startswith("ibid"):
                    page_int = current_page_int
                    page_ref_source = "ibid"
                else:
                    page_match = re.search(r"\d{1,4}", token)
                    page_int = int(page_match.group(0)) if page_match else None
                    page_ref_source = "explicit"
                if page_int is not None:
                    current_page_int = page_int
                items.append(
                    {
                        "source_file": str(path),
                        "lemma_raw": body,
                        "page_token_raw": token,
                        "page_int": page_int,
                        "page_ref_source": page_ref_source,
                        "entry_raw": f"{body} {token}".strip(),
                    }
                )
    return items


def build_helper_request(
    *,
    volume_id: str,
    source_root: Path,
    entries: list[dict[str, Any]],
    helper_request_json: Path,
) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_int = entry.get("inferred_printed_page")
        if page_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"]],
                "page_hints": [str(page_int)],
                "page_hint_ints": [page_int],
                "context_raw": entry["entry_raw"],
            }
        )
    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")


def build_payload(
    *,
    volume_id: str,
    source_root: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    if not files:
        raise SystemExit("No OCR files found for PL098.")

    section_files = [path for path in files if 728 <= file_seq(path) <= 738]
    if not section_files:
        raise SystemExit("No OCR files found for the PL098 ORDO RERUM window.")

    page_map = build_page_map(files)
    helper_output = {"entries": []}
    if helper_output_json.exists():
        helper_output = json.loads(helper_output_json.read_text(encoding="utf-8"))

    section_key = f"{volume_id}:section:001"
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entry_counter = 0
    current_source_file = str(section_files[0])

    helper_map: dict[str, Any] = {str(item.get("entry_id")): item for item in helper_output.get("entries", [])}

    for item in iter_toc_items(section_files):
        current_source_file = item["source_file"]
        entry_counter += 1
        entry_key = f"{volume_id}:entry:{entry_counter:04d}"
        page_int = item["page_int"]
        page_token_raw = item["page_token_raw"]
        page_ref_source = item["page_ref_source"]
        target_file, target_prob = lookup_target(page_int, page_map)
        helper_item = helper_map.get(entry_key)
        confidence = 0.93 if target_file else 0.72
        if page_ref_source == "ibid":
            confidence = 0.91
        if helper_item and helper_item.get("status") == "resolved":
            confidence = max(confidence, 0.94)
        entry_raw = item["entry_raw"]
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": entry_counter,
                "entry_kind": "heading_group",
                "lemma_raw": item["lemma_raw"],
                "lemma_display": item["lemma_raw"],
                "lemma_norm": lemma_norm(item["lemma_raw"]),
                "lemma_sort": lemma_norm(item["lemma_raw"]),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": str(section_files[0]),
                "editorial_anchor_file": current_source_file,
                "target_file_best": target_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": current_source_file,
                    "section_kind": "ordo_rerum",
                    "page_token_raw": page_token_raw,
                    "page_ref_source": page_ref_source,
                    "helper_status": helper_item.get("status") if helper_item else None,
                    "helper_best_candidate": helper_item.get("best_candidate") if helper_item else None,
                    "helper_candidates": helper_item.get("candidates") if helper_item else None,
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": page_token_raw or (str(page_int) if page_int is not None else None),
                "page_ref_raw": page_token_raw or (str(page_int) if page_int is not None else None),
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": target_prob,
                "section_start_file": str(section_files[0]),
                "editorial_anchor_file": current_source_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": current_source_file,
                    "section_kind": "ordo_rerum",
                    "page_token_raw": page_token_raw,
                    "page_ref_source": page_ref_source,
                    "helper_status": helper_item.get("status") if helper_item else None,
                    "helper_best_candidate": helper_item.get("best_candidate") if helper_item else None,
                    "helper_candidates": helper_item.get("candidates") if helper_item else None,
                },
            }
        )

    section_page_start = 1451
    section_page_end = 4172
    section = {
        "section_key": section_key,
        "volume_id": volume_id,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": section_page_start,
        "page_end": section_page_end,
        "file_start": str(section_files[0]),
        "file_end": str(section_files[-1]),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Final contents table printed as ORDO RERUM / ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.; not alphabetical, but an editorial closure/contents section allowed by the contract.",
            "source_files": [str(path) for path in section_files],
            "header_pages": [1451, 1452, 1453, 1454, 1455, 1456, 1457, 1458, 1463, 1464, 1465, 1466, 1469, 1470, 4171, 4172],
            "heading_variants": [
                "ORDO RERUM",
                "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
            ],
        },
    }
    volume = {
        "volume_id": volume_id,
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina 98",
        "notes": "Final contents list (ordo rerum) recovered from the OCR tail.",
    }
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM contents table from the OCR tail. "
            "The section is editorial closure rather than a true alphabetical index, "
            "but the individual contents lines and their material page locators were preserved conservatively."
        ),
        "evidence_files": [str(path) for path in section_files],
    }
    notes = [
        {
            "note_key": f"{volume_id}:note:1",
            "note_kind": "extraction_note",
            "note_raw": "PL098 ends with an ORDO RERUM contents table; OCR file suffixes are not the editorial pagination.",
            "confidence": 0.96,
            "raw_json": {
                "helper_output_path": str(helper_output_json),
                "section_window": [file_seq(section_files[0]), file_seq(section_files[-1])],
                "page_map_size": len(page_map),
            },
        }
    ]
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    for name, fragment in {
        "volume.json": volume,
        "sections.json": [section],
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": [],
        "coverage.json": coverage,
        "notes.json": notes,
        "manifest.json": {"volume_id": volume_id, "generated_at": generated_at, "updated_at": generated_at},
    }.items():
        (intermediate_dir / name).write_text(json.dumps(fragment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL098 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = discover_text_files(args.source_root)
    section_files = [path for path in files if 728 <= file_seq(path) <= 738]
    if not section_files:
        raise SystemExit("No OCR files found for the PL098 ORDO RERUM window.")

    helper_output_exists = args.helper_output_json.exists()

    provisional_entries: list[dict[str, Any]] = []
    for idx, item in enumerate(iter_toc_items(section_files), start=1):
        if item["page_int"] is None:
            continue
        provisional_entries.append(
            {
                "entry_key": f"PL098:entry:{idx:04d}",
                "lemma_raw": item["lemma_raw"],
                "inferred_printed_page": item["page_int"],
                "entry_raw": item["entry_raw"],
            }
        )
    request = build_helper_request(
        volume_id="PL098",
        source_root=args.source_root,
        entries=provisional_entries,
        helper_request_json=args.helper_request_json,
    )

    if not helper_output_exists:
        print(
            f"Wrote helper request to {args.helper_request_json}. "
            f"Run index_target_locator.py to generate {args.helper_output_json} and rerun this script.",
            file=sys.stderr,
        )
        return

    payload = build_payload(
        volume_id="PL098",
        source_root=args.source_root,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
    )
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

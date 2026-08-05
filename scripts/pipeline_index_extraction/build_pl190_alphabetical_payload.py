#!/usr/bin/env python3
"""Usage: build the PL190 alphabetical index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl190_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL190/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL190_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL190_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL190 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL190_alphabetical_indices.json

The script parses the alphabetical index of incipits in the tail of PL190,
builds a helper request for page-bearing entries, runs index_target_locator.py,
and assembles the canonical alphabetical payload plus per-volume checkpoints.
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL190"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 190"
SECTION_KEY = f"{VOLUME_ID}:alpha:alphabetical_general:001"

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
LETTER_RE = re.compile(r"^[A-V]$")
INTRO_PREFIXES = (
    "TABULA EPISTOLARUM S. THOMÆ CANTUAR.",
    "Secundum litteram initialem ordinata",
    "Epistolarum sancti Thomæ Cantuariensis editio a Lupo vulgata",
    "A cod.",
    "B cod.",
    "C cod.",
    "D cod.",
    "E indicat",
    "F epistolas",
    "G cod.",
    "H cod.",
    "I cod.",
    "K cod.",
    "L cod.",
    "M cod.",
    "Q Bouquet",
    "W cod.",
    "X cod.",
    "AA ",
    "EE ",
    "FF ",
)
WITNESS_CONT_RE = re.compile(
    r"^(?:[A-Z](?:²|¹)?|AA|BB|CC|DD|EE|FF|GF|Q|W|X|Y|Z)\s+(?:\d|[IVXLCDM]+|uol\.)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = text.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def block_lines(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo not in {"cabecalho", "texto_principal", "nota_marginal", "nota"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google" or line == "-":
                continue
            if line.startswith(("PATROL.", "Ex typis", "Ex typis MIGNE", "Finis tomi", "FINIS TOMI", "Leitura efetuada")):
                continue
            out.append((tipo, line))
    return out


def header_numbers(path: Path) -> list[int]:
    nums: list[int] = []
    for block_type, line in block_lines(path):
        if block_type != "cabecalho":
            continue
        for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", line):
            value = int(num)
            if 0 <= value <= 5000:
                nums.append(value)
    return nums


def build_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        for num in header_numbers(path):
            mapping.setdefault(num, path.as_posix())
    return mapping


def section_files(candidate_files: list[Path]) -> list[Path]:
    ordered = sorted(candidate_files, key=page_seq)
    start_idx: int | None = None
    end_idx: int | None = None
    for idx, path in enumerate(ordered):
        text = "\n".join(line for _, line in block_lines(path))
        if start_idx is None and "TABULA EPISTOLARUM S. THOMÆ CANTUAR." in text:
            start_idx = idx
            continue
        if start_idx is not None and ("ORDO CHRONOLOGICUS" in text or "QUÆ IN HOC TOMO CONTINENTUR" in text):
            end_idx = idx
            break
    if start_idx is None:
        raise SystemExit("could not locate the start of the alphabetical index in the provided tail window")
    files = ordered[start_idx:end_idx] if end_idx is not None else ordered[start_idx:]
    if not files:
        raise SystemExit("no alphabetical index files detected in the provided tail window")
    return files


def is_intro_line(line: str) -> bool:
    return any(line.startswith(prefix) for prefix in INTRO_PREFIXES)


def is_entry_start(line: str) -> bool:
    if not line:
        return False
    if LETTER_RE.fullmatch(line):
        return False
    if line.startswith("—"):
        return False
    if is_intro_line(line):
        return False
    if line[0].isupper() and not re.fullmatch(r"[A-V]", line):
        return True
    return False


def is_continuation(line: str) -> bool:
    if not line:
        return False
    if line[0].islower() or line[0].isdigit() or line.startswith("—"):
        return True
    return bool(WITNESS_CONT_RE.match(line))


def parse_index_entries(section_paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    notes: list[str] = []
    current: dict[str, Any] | None = None
    current_letter: str | None = None
    entry_order = 0
    seen_letters: set[str] = set()

    for path in section_paths:
        for block_type, line in block_lines(path):
            if is_intro_line(line):
                continue
            if line.startswith("INDICES IN EPISTOLAS GILBERTI FOLIOT."):
                continue
            if LETTER_RE.fullmatch(line):
                if current:
                    entries.append(current)
                    current = None
                current_letter = line
                if line not in seen_letters:
                    seen_letters.add(line)
                    nodes.append(
                        {
                            "node_key": f"{VOLUME_ID}:node:letter:{line.lower()}:001",
                            "section_key": SECTION_KEY,
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": path.as_posix()},
                        }
                    )
                continue

            if not current_letter:
                continue

            if current and is_continuation(line):
                current["raw"] += " " + line
                continue

            if current:
                entries.append(current)
                current = None

            current = {
                "letter": current_letter,
                "raw": line,
                "source_file": path.as_posix(),
            }

    if current:
        entries.append(current)

    parsed: list[dict[str, Any]] = []
    for item in entries:
        raw = normalize(item["raw"])
        letter = item["letter"]
        entry_order += 1

        cross_ref = "Vide" in raw and not re.search(r"\d", raw)
        if cross_ref:
            lemma_raw = normalize(re.split(r"\.\s*Vide\b", raw, maxsplit=1)[0].rstrip(" .;:,"))
            entry_kind = "cross_reference"
            page_numbers: list[int] = []
            inferred_printed_page = None
        else:
            first_digit = re.search(r"\d", raw)
            if not first_digit:
                lemma_raw = normalize(raw.rstrip(" .;:,"))
                entry_kind = "editorial_note"
                page_numbers = []
                inferred_printed_page = None
            else:
                lemma_raw = normalize(raw[: first_digit.start()].rstrip(" ,;:."))
                tail = normalize(raw[first_digit.start() :])
                prefix = re.match(r"^(?P<pages>.+?)(?=\s+[A-Z(])", tail)
                pages = prefix.group("pages") if prefix else tail
                page_numbers = [int(num) for num in re.findall(r"\d+", pages)]
                inferred_printed_page = page_numbers[0] if page_numbers else None
                entry_kind = "lemma"

        parsed.append(
            {
                "entry_key": f"{VOLUME_ID}:entry:{entry_order:04d}",
                "section_key": SECTION_KEY,
                "parent_node_key": f"{VOLUME_ID}:node:letter:{letter.lower()}:001",
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw or None,
                "lemma_display": lemma_raw or None,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": raw,
                "context_raw": raw,
                "heading_letter": letter,
                "inferred_printed_page": inferred_printed_page,
                "page_numbers": page_numbers,
                "section_start_file": section_paths[0].as_posix(),
                "editorial_anchor_file": path.as_posix(),
                "target_file_best": None,
                "confidence": 0.0,
                "raw_json": {
                    "source_file": item["source_file"],
                    "section_letter": letter,
                    "entry_kind_reason": "alphabetical index line with printed-page anchors" if page_numbers else "cross-reference or editorial note without printed-page anchor",
                },
            }
        )

    if not parsed:
        notes.append("No index entries could be parsed from the alphabetical window.")
    return parsed, nodes, notes


def helper_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    page_numbers = item.get("page_numbers") or []
    if not page_numbers:
        return None
    lemma = normalize(item.get("lemma_raw") or item.get("entry_raw"))
    query_names = [lemma]
    stripped = lemma.rstrip(" .;:,")
    if stripped and stripped not in query_names:
        query_names.append(stripped)
    if item.get("entry_raw") and item["entry_raw"] not in query_names:
        query_names.append(item["entry_raw"])
    return {
        "entry_id": item["entry_key"].replace(":", "_").lower(),
        "lemma_raw": item.get("lemma_raw") or item.get("entry_raw") or "",
        "query_names": [q for q in query_names if q],
        "page_hints": [str(page_numbers[0])],
        "page_hint_ints": [page_numbers[0]],
        "context_raw": item.get("entry_raw") or item.get("context_raw") or "",
    }


def build_helper_request(source_root: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [entry for item in items if (entry := helper_entry(item)) is not None]
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
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
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            out[entry_id] = item
    return out


def build_payload(
    section_paths: list[Path],
    items: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    helper_output: dict[str, Any],
    page_lookup: dict[int, str],
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    section_start_file = section_paths[0].as_posix()
    section_end_file = section_paths[-1].as_posix()

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    for item in items:
        entry_key = item["entry_key"]
        helper_entry_id = entry_key.replace(":", "_").lower()
        helper_row = helper_map.get(helper_entry_id, {})
        best = helper_row.get("best_candidate") or {}
        candidates = helper_row.get("candidates") or []
        status = helper_row.get("status")

        page_numbers = item.get("page_numbers") or []
        target_file = page_lookup.get(page_numbers[0]) if page_numbers else None
        if target_file is None and best.get("file"):
            target_file = best.get("file")
        target_prob = best.get("probability") if best else None
        editorial_anchor_file = target_file or item["editorial_anchor_file"] or section_start_file
        confidence = 0.9 if target_file else (0.74 if page_numbers else 0.82)

        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": item["parent_node_key"],
                "entry_order": item["entry_order"],
                "entry_kind": item["entry_kind"],
                "lemma_raw": item["lemma_raw"],
                "lemma_display": item["lemma_display"],
                "lemma_norm": item["lemma_norm"],
                "lemma_sort": item["lemma_sort"],
                "entry_raw": item["entry_raw"],
                "context_raw": item["context_raw"],
                "heading_letter": item["heading_letter"],
                "inferred_printed_page": item["inferred_printed_page"],
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "target_file_best": target_file,
                "confidence": confidence,
                "raw_json": {
                    **(item.get("raw_json") or {}),
                    "helper_entry_id": helper_entry_id if page_numbers else None,
                    "helper_status": status,
                    "helper_best_candidate": best or None,
                    "helper_top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in candidates[:3]
                    ]
                    if candidates
                    else [],
                    "page_lookup_resolved": bool(target_file),
                },
            }
        )

        for idx, page_int in enumerate(page_numbers, start=1):
            ref_target = page_lookup.get(page_int) or target_file
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_int),
                    "page_ref_raw": str(page_int),
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": ref_target,
                    "target_file_probability": target_prob,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": editorial_anchor_file,
                    "confidence": confidence - 0.04 if confidence > 0.04 else confidence,
                    "raw_json": {
                        "helper_entry_id": helper_entry_id,
                        "helper_status": status,
                        "source_file": item.get("raw_json", {}).get("source_file"),
                    },
                }
            )

    section_page_start = 1501
    section_page_end = 1506
    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDICES IN EPISTOLAS GILBERTI FOLIOT.",
        "heading_norm": "indices in epistolas gilberti foliot",
        "heading_letter": None,
        "page_start": section_page_start,
        "page_end": section_page_end,
        "file_start": section_start_file,
        "file_end": section_end_file,
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": (
                "Alphabetical incipit index for the Gilbert Foliot letters; the first OCR spread carries the sigla introduction "
                "and letters A-B, and the remaining index spreads run through V before the ORDO CHRONOLOGICUS section begins."
            ),
            "evidence_files": [p.as_posix() for p in section_paths],
        },
    }

    page_refs_total = len(refs)
    unresolved_page_refs = sum(1 for ref in refs if ref.get("target_file") is None)
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            f"Recovered {len(entries)} alphabetical index entries across {len(section_paths)} OCR files; "
            f"{unresolved_page_refs} of {page_refs_total} printed-page refs still need manual or helper-backed target confirmation."
        ),
        "evidence_files": [p.as_posix() for p in section_paths],
    }

    notes = [
        {
            "note_type": "extraction",
            "source": "build_pl190_alphabetical_payload.py",
            "status": "completed",
            "parsed_entries": len(entries),
            "parsed_refs": len(refs),
        },
        {
            "note_type": "helper",
            "source": "PL190_helper_output.json",
            "status": helper_output.get("status") if isinstance(helper_output, dict) else None,
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": section_paths[0].parent.as_posix(),
        "volume_label": VOLUME_LABEL,
    }

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL190 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"

    candidate_files = sorted(args.source_root.glob("*.txt"), key=page_seq)
    section_paths = section_files(candidate_files)
    items, nodes, parse_notes = parse_index_entries(section_paths)
    page_lookup = build_page_map(args.source_root)

    helper_request = build_helper_request(args.source_root, items)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(section_paths, items, nodes, helper_output, page_lookup)

    # Persist checkpoints for reruns.
    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"] + [{"note_type": "parse", "status": "completed", "messages": parse_notes}])
    write_json(
        args.intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": args.helper_request_json.as_posix(),
            "helper_output_json": args.helper_output_json.as_posix(),
            "output_file": args.output_file.as_posix(),
        },
    )
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PL190 alphabetical index payload",
            "completed": [
                "Alphabetical index lines parsed from OCR",
                "Helper request generated",
                "Helper output incorporated",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Section files stop before ORDO CHRONOLOGICUS.",
                "Printed-page refs keep the OCR literal values from the index lines.",
            ],
        },
    )

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

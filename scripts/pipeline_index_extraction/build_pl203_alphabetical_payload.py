#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl203_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL203/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL203_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL203_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL203 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL203_alphabetical_indices.json

Build the PL203 alphabetical-index payload from the OCR tail. The script
separates the `INDEX RERUM ANALYTICUS` subject index from the closing
`ORDO RERUM` contents table, writes helper input/output checkpoints, and
emits the canonical JSON payload expected by the alphabetical index importer.
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
VOLUME_ID = "PL203"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 203"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

ANALYTIC_FILES = [712, 713, 714, 715]
ORDO_FILES = [716, 717]

ANALYTIC_SECTION_KEY = f"{VOLUME_ID}:section:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:section:ordo_rerum:002"

ANALYTIC_HEADING_RAW = "INDEX RERUM ANALYTICUS."
ORDO_HEADING_RAW = "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?(?:\s*(?:et\s+seqq\.?|seqq\.?|seq\.?|etc\.?))?", re.IGNORECASE)
SPLIT_RE = re.compile(r"(?<!\b[A-ZÆŒ])\.\s+(?=[A-ZÆŒ])")
NOISE_PREFIXES = (
    "Digitized by Google",
    "FINIS TOMI",
    "Ex typis MIGNE",
    "Página de índice",
    "Página identificada",
    "Transcrição",
)
HELPER_HINT_RE = re.compile(
    r"(cerpus|aerum|inae-|clibres|apellatur|etc\.|seqq\.|ibid\.|ullus|digniorem|malbodiensi)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value).lower()
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    def seq(path: Path) -> int:
        m = re.search(r"-(\d+)\.txt$", path.name)
        return int(m.group(1)) if m else 10**9

    return sorted(source_root.glob("*.txt"), key=seq)


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    blocks: list[tuple[str, str]] = []
    for key in ("header_text", "body_text"):
        text = parsed.get(key) or ""
        for raw_line in text.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            if any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
                continue
            blocks.append((key, line))
    return blocks


def build_page_lookup(files: list[Path]) -> dict[int, str]:
    lookup: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header):
            value = int(num)
            lookup.setdefault(value, path.as_posix())
    return lookup


def resolve_target(page: int | None, page_lookup: dict[int, str], files: list[Path]) -> str | None:
    if page is None:
        return None
    if page in page_lookup:
        return page_lookup[page]
    needle = re.compile(rf"(?<!\d){page}(?!\d)")
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        body = parsed.get("body_text") or ""
        if needle.search(header) or needle.search(body):
            return path.as_posix()
    return None


def split_fragments(text: str) -> list[str]:
    clean = re.sub(r"(?<=\w)-\s+", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return []
    parts = [part.strip() for part in SPLIT_RE.split(clean) if part.strip()]
    return parts or [clean]


def extract_page_tokens(text: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for match in PAGE_RE.finditer(text):
        raw = normalize(match.group(0))
        if not raw:
            continue
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        tokens.append(
            {
                "raw": raw,
                "page_int": start,
                "range_end": end,
                "kind": "editorial_range" if end is not None else "editorial_page",
            }
        )
    return tokens


def line_entries(
    path: Path,
    *,
    section_kind: str,
    entry_counter: int,
    section_start_file: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    letter_node_keys: dict[str, str] = {}
    section_key = ANALYTIC_SECTION_KEY if section_kind == "analytic_subject" else ORDO_SECTION_KEY

    buffers: list[str] = []
    for block_type, line in extract_blocks(path):
        if LETTER_RE.fullmatch(line):
            current_letter = line
            if section_kind == "analytic_subject" and line not in letter_node_keys:
                node_key = f"{VOLUME_ID}:node:letter:{line.lower()}:001"
                letter_node_keys[line] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": line,
                        "label_norm": line.lower(),
                        "label_sort": line.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": path.as_posix(), "block_type": block_type},
                    }
                )
            continue
        if line.startswith("ORDO RERUM") or line.startswith("QUAE IN HOC TOMO CONTINENTUR") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR"):
            continue
        buffers.append(line)

    joined = re.sub(r"\s+", " ", re.sub(r"(?<=\w)-\s+", "", " ".join(buffers))).strip()
    fragments = split_fragments(joined)

    for fragment in fragments:
        cleaned = normalize(fragment)
        if not cleaned:
            continue
        if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"}:
            continue
        tokens = extract_page_tokens(cleaned)
        first_digit = re.search(r"\d", cleaned)
        if first_digit:
            lemma_raw = cleaned[: first_digit.start()].rstrip(" ,;:.")
        else:
            lemma_raw = cleaned.rstrip(" ,;:.")
        if section_kind == "analytic_subject" and not first_digit and len(cleaned) <= 3:
            entry_kind = "heading_group"
        elif section_kind == "ordo_rerum" and not first_digit:
            entry_kind = "heading_group"
        elif section_kind == "ordo_rerum":
            entry_kind = "heading_group"
        else:
            entry_kind = "lemma"

        entry_counter += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
        inferred_printed_page = tokens[0]["page_int"] if tokens else None
        target_best = None
        if tokens:
            target_best = resolve_target(tokens[0]["page_int"], page_lookup, all_files)

        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": letter_node_keys.get(current_letter) if section_kind == "analytic_subject" else None,
            "entry_order": entry_counter,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw or None,
            "lemma_display": lemma_raw or None,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": cleaned,
            "context_raw": cleaned,
            "heading_letter": current_letter if section_kind == "analytic_subject" else None,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": path.as_posix(),
            "target_file_best": target_best,
            "confidence": 0.88 if tokens else 0.8,
            "raw_json": {
                "source_file": path.as_posix(),
                "section_kind": section_kind,
                "page_tokens": tokens,
                "current_letter": current_letter,
                "split_strategy": "sentence_boundary",
            },
        }
        entries.append(entry)

        needs_helper = False
        if tokens:
            needs_helper = target_best is None or bool(HELPER_HINT_RE.search(cleaned)) or len(tokens) >= 4
        if needs_helper:
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw or cleaned,
                    "query_names": [q for q in [lemma_raw, cleaned.split(",", 1)[0], cleaned] if q],
                    "page_hints": [str(tok["page_int"]) for tok in tokens[:3]],
                    "page_hint_ints": [tok["page_int"] for tok in tokens[:3]],
                    "context_raw": cleaned,
                }
            )

        for idx, token in enumerate(tokens, start=1):
            target_file = resolve_target(token["page_int"], page_lookup, all_files)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": token["kind"],
                    "ref_raw": token["raw"],
                    "page_ref_raw": token["raw"],
                    "page_ref_int": token["page_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": str(token["range_end"]) if token["range_end"] is not None else None,
                    "target_file": target_file,
                    "target_file_probability": 0.99 if target_file else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": path.as_posix(),
                    "confidence": 0.95 if target_file else 0.72,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "section_kind": section_kind,
                        "page_token_raw": token["raw"],
                    },
                }
            )

    return entries, refs, nodes, helper_entries, entry_counter


def helper_entry_payload(entry: dict[str, Any]) -> dict[str, Any] | None:
    page_hints = entry.get("raw_json", {}).get("page_tokens") or []
    if not page_hints:
        return None
    lemma = entry.get("lemma_raw") or entry.get("entry_raw") or ""
    query_names = [name for name in [lemma, entry.get("entry_raw"), lemma.split(",", 1)[0]] if name]
    return {
        "entry_id": entry["entry_key"],
        "lemma_raw": lemma,
        "query_names": query_names,
        "page_hints": [str(token["page_int"]) for token in page_hints],
        "page_hint_ints": [token["page_int"] for token in page_hints],
        "context_raw": entry.get("entry_raw") or "",
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
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, {"status": "missing", "entries": []})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []) or []:
        if isinstance(item, dict) and item.get("entry_id"):
            out[str(item["entry_id"])] = item
    return out


def attach_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = helper_index(helper_output)
    ref_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        ref_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        helper = by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        candidates = helper.get("candidates") or []
        entry.setdefault("raw_json", {})
        entry["raw_json"]["helper_locator"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in candidates[:3]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
            entry["raw_json"]["helper_best_file"] = best.get("file")
            entry["raw_json"]["helper_best_probability"] = best.get("probability")

        for ref in ref_by_entry.get(entry["entry_key"], []):
            ref.setdefault("raw_json", {})
            ref["raw_json"]["helper_locator"] = {
                "status": helper.get("status"),
                "candidate_role": helper.get("candidate_role"),
                "reason_summary": helper.get("reason_summary"),
                "top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                    }
                    for cand in candidates[:3]
                ],
            }
            if best.get("file"):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")
                ref["raw_json"]["helper_best_file"] = best.get("file")
                ref["raw_json"]["helper_best_probability"] = best.get("probability")


def build_section_entries(
    section_kind: str,
    files: list[Path],
    page_lookup: dict[int, str],
    *,
    starting_entry_counter: int,
    section_start_file: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_counter = starting_entry_counter
    for path in files:
        e, r, n, h, entry_counter = line_entries(
            path,
            section_kind=section_kind,
            entry_counter=entry_counter,
            section_start_file=section_start_file,
        )
        entries.extend(e)
        refs.extend(r)
        nodes.extend(n)
        helper_entries.extend(h)
    return entries, refs, nodes, helper_entries, entry_counter


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    global all_files, page_lookup, section_start_file_by_kind
    all_files = discover_files(source_root)
    page_lookup = build_page_lookup(all_files)

    analytic_paths = [p for p in all_files if file_seq(p) in ANALYTIC_FILES]
    ordo_paths = [p for p in all_files if file_seq(p) in ORDO_FILES]
    section_start_file_by_kind = {
        "analytic_subject": analytic_paths[0].as_posix() if analytic_paths else None,
        "ordo_rerum": ordo_paths[0].as_posix() if ordo_paths else None,
    }

    analytic_entries, analytic_refs, analytic_nodes, analytic_helper_entries, next_counter = build_section_entries(
        "analytic_subject",
        analytic_paths,
        page_lookup,
        starting_entry_counter=0,
        section_start_file=analytic_paths[0].as_posix() if analytic_paths else None,
    )
    ordo_entries, ordo_refs, _, ordo_helper_entries, next_counter = build_section_entries(
        "ordo_rerum",
        ordo_paths,
        page_lookup,
        starting_entry_counter=next_counter,
        section_start_file=ordo_paths[0].as_posix() if ordo_paths else None,
    )

    helper_entries = [item for item in (analytic_helper_entries + ordo_helper_entries) if item]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    entries = analytic_entries + ordo_entries
    refs = analytic_refs + ordo_refs
    attach_helper(entries, refs, helper_output)

    sections = [
        {
            "section_key": ANALYTIC_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": ANALYTIC_HEADING_RAW,
            "heading_norm": sort_norm(ANALYTIC_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1397,
            "page_end": 1406,
            "file_start": analytic_paths[0].as_posix() if analytic_paths else None,
            "file_end": analytic_paths[-1].as_posix() if analytic_paths else None,
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytical index headed INDEX RERUM ANALYTICUS, with letter-group dividers and page-locator entries.",
                "evidence_files": [p.as_posix() for p in analytic_paths],
                "source_hint": "candidate_sections",
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": ORDO_HEADING_RAW,
            "heading_norm": sort_norm(ORDO_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1405,
            "page_end": 1408,
            "file_start": ordo_paths[0].as_posix() if ordo_paths else None,
            "file_end": ordo_paths[-1].as_posix() if ordo_paths else None,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table printed after the analytical index.",
                "evidence_files": [p.as_posix() for p in ordo_paths],
                "source_hint": "candidate_sections",
            },
        },
    ]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytical index and the closing ORDO RERUM contents table from the OCR tail. Printed-page locators were preserved and target files were resolved conservatively from the volume page map and helper output.",
        "evidence_files": [analytic_paths[0].as_posix() if analytic_paths else None, analytic_paths[-1].as_posix() if analytic_paths else None, ordo_paths[0].as_posix() if ordo_paths else None, ordo_paths[-1].as_posix() if ordo_paths else None],
    }

    notes = [
        "The OCR tail contains two editorial structures: the analytical index and the ORDO RERUM contents table.",
        "The first analytical file begins with a malformed `INDEX AERUM ANALYTICUS` header in one OCR slice; the canonical heading is preserved from the clearer candidate file.",
        "The index body includes a few OCR corruptions and wrapped lines. Literal OCR is preserved in entry_raw and ref_raw.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": analytic_nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", analytic_nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "helper_request_json": helper_request_json.as_posix(),
            "helper_output_json": helper_output_json.as_posix(),
            "output_file": output_file.as_posix(),
            "entry_count": len(entries),
            "ref_count": len(refs),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PL203 alphabetical payload",
            "completed": [
                "OCR tail inspected",
                "analytical index parsed",
                "ORDO RERUM parsed",
                "helper request generated and helper run",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffix, printed page, and cited reference separate.",
                "Do not collapse comma-separated page locators into a single invented range.",
            ],
        },
    )
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)


if __name__ == "__main__":
    main()

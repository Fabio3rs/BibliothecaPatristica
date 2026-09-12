#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG086_02_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG086.02/text \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG086.02 \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG086.02_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG086.02_helper_output.json \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG086.02_alphabetical_indices.json

Builds the PG086.02 alphabetical payload from the OCR tail, including helper
request generation, helper execution, intermediate JSON fragments, and final
payload assembly.
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

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG086.02"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 86, pars 2"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_1 = {
    "section_key": f"{VOLUME_ID}:alpha:author_index:001",
    "section_order": 1,
    "section_kind": "author_index",
    "heading_raw": "INDEX SCRIPTORUM ET HÆRETICORUM QUORUM MENTIO IN LEONTII BYZANTINI LIBRO DE SECTIS.",
    "heading_norm": "index scriptorum et haereticorum quorum mentio in leontii byzantini libro de sectis",
    "page_start": 3341,
    "page_end": 3342,
    "file_start_seq": 818,
    "file_end_seq": 819,
    "section_kind_reason": "Alphabetical author/heretic index headed by INDEX SCRIPTORUM ET HÆRETICORUM.",
}

SECTION_2 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
    "section_order": 2,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX RERUM MEMORABILIUM QUÆ IN HISTORIA ECCLESIASTICA EVAGRII SCHOLASTICI NOTANTUR.",
    "heading_norm": "index rerum memorabilium quae in historia ecclesiastica evagrii scholastici notantur",
    "page_start": 3343,
    "page_end": 3350,
    "file_start_seq": 819,
    "file_end_seq": 823,
    "section_kind_reason": "Analytical subject index with alphabetical letter groups A-Z.",
}

FILE_RE = re.compile(r"-(\d+)\.txt$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?(?!\d)")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.S | re.I)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
LETTER_RE = re.compile(r"^[A-Z]$")
ROMAN_RE = re.compile(r"^(?:IV|V|VI|VII|VIII|IX|X)$")
START_RE = re.compile(r"^[A-ZΑ-ΩÆŒ]|^[0-9]")
LOWER_CONT_RE = re.compile(r"^[a-zà-ÿ]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_space(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text.replace("\xa0", " "))).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    m = FILE_RE.search(path.name)
    if not m:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = normalize_space(attrs.get("tipo") or "").lower()
        content = match.group("content") or ""
        lines = [normalize_space(line) for line in content.splitlines()]
        lines = [line for line in lines if line and not FOOTER_RE.fullmatch(line)]
        if lines:
            blocks.append({"tipo": block_type, "lines": lines})
    return blocks


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for raw in parsed["all_text"].splitlines():
        line = normalize_space(raw)
        if not line or FOOTER_RE.fullmatch(line) or re.fullmatch(r"\d{4}", line):
            continue
        out.append(line)
    return out


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for block in extract_blocks(path):
            if block["tipo"] != "cabecalho":
                continue
            header_text = normalize_space(" ".join(block["lines"]))
            for match in PAGE_RE.finditer(header_text):
                mapping.setdefault(int(match.group(1)), str(path))
    return mapping


def normalize_query_variants(lemma_raw: str) -> list[str]:
    base = normalize_space(lemma_raw)
    variants = [base]
    stripped = re.sub(r"^[\s«\"'’᾽]*[ivxlcdmIVXLCDM0-9]+[.'’]?\s*", "", base)
    stripped = normalize_space(stripped)
    if stripped and stripped not in variants:
        variants.append(stripped)
    folded = normalize_space(strip_accents(base))
    if folded and folded not in variants:
        variants.append(folded)
    return [item for item in dict.fromkeys(variants) if item]


def is_letter_heading(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line) or ROMAN_RE.fullmatch(line))


def extract_entry_start(line: str) -> bool:
    if not line:
        return False
    if is_letter_heading(line):
        return False
    if line.startswith("INDEX ") or line.startswith("Revocatur lector") or line.startswith("QUÆ IN HOC TOMO") or line.startswith("QUAE IN HOC TOMO"):
        return False
    return bool(START_RE.match(line))


def parse_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int | None]] = set()
    for match in PAGE_RE.finditer(text):
        raw = normalize_space(match.group(0))
        page_int = int(match.group(1))
        page_end_int = int(match.group(2)) if match.group(2) else None
        sig = (raw, page_int, page_end_int)
        if sig in seen:
            continue
        seen.add(sig)
        refs.append(
            {
                "raw": raw,
                "page_ref_int": page_int,
                "range_end_int": page_end_int,
            }
        )
    return refs


def infer_lemma(entry_raw: str) -> str | None:
    match = PAGE_RE.search(entry_raw)
    if match:
        lemma = normalize_space(entry_raw[: match.start()]).rstrip(" ,;:.")
        if lemma:
            return lemma
    return normalize_space(entry_raw).rstrip(" ,;:.") or None


def collect_section_lines(section: dict[str, Any], files: list[Path]) -> list[str]:
    start_seq = section["file_start_seq"]
    end_seq = section["file_end_seq"]
    use = [p for p in files if start_seq <= file_seq(p) <= end_seq]
    lines: list[str] = []
    section_started = False
    if section["section_key"] == SECTION_1["section_key"]:
        heading_marker = "INDEX SCRIPTORUM ET HÆRETICORUM"
        stop_marker = "IV."
    else:
        heading_marker = "INDEX RERUM MEMORABILIUM"
        stop_marker = None
    for path in use:
        for block in extract_blocks(path):
            if block["tipo"] not in {"cabecalho", "texto_principal", "nota_marginal"}:
                continue
            for line in block["lines"]:
                if not section_started:
                    if heading_marker in line:
                        section_started = True
                    continue
                if stop_marker and line == stop_marker:
                    return lines
                if line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                    return lines
                lines.append(line)
    return lines


def group_lines_to_entries(lines: list[str]) -> tuple[list[str], list[str]]:
    entries: list[str] = []
    nodes: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            entries.append(normalize_space(" ".join(buffer)))
            buffer.clear()

    for line in lines:
        clean = normalize_space(line)
        if not clean:
            continue
        if is_letter_heading(clean):
            flush()
            nodes.append(clean)
            continue
        if clean.startswith("Digitized by Google"):
            continue
        if clean.startswith("Revocatur lector"):
            continue
        if not buffer:
            buffer.append(clean)
            continue
        if LOWER_CONT_RE.match(clean):
            buffer.append(clean)
            continue
        if buffer[-1].endswith(("-", ",", ";", ":")):
            buffer.append(clean)
            continue
        flush()
        buffer.append(clean)
    flush()
    return entries, nodes


def build_helper_request(extracted_entries: list[dict[str, Any]], volume_id: str, source_root: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in extracted_entries:
        hints = [ref["page_ref_int"] for ref in entry["page_refs"] if ref.get("page_ref_int") is not None]
        if not hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:120],
                "query_names": normalize_query_variants(entry["lemma_raw"] or entry["entry_raw"]),
                "page_hints": [str(hints[0])],
                "page_hint_ints": [hints[0]],
                "context_raw": entry["entry_raw"][:240],
            }
        )
    return {
        "volume_id": volume_id,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


def extract_sections(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    page_map = build_page_map(files)
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    extracted_for_helper: list[dict[str, Any]] = []

    section_specs = [SECTION_1, SECTION_2]

    for section in section_specs:
        section_lines = collect_section_lines(section, files)
        section_entries_raw, section_nodes_raw = group_lines_to_entries(section_lines)
        section_key = section["section_key"]
        node_lookup: dict[str, str] = {}
        node_order = 0
        entry_order = 0
        current_letter: str | None = None
        section_start_file = str(next(p for p in files if file_seq(p) == section["file_start_seq"]))
        section_end_file = str(next(p for p in files if file_seq(p) == section["file_end_seq"]))

        for letter in section_nodes_raw:
            node_order += 1
            node_key = f"{VOLUME_ID}:alpha:{section['section_kind']}:{section['section_order']:03d}:node:{node_order:03d}:{letter}"
            node_lookup[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {"section_kind": section["section_kind"]},
                }
            )
            current_letter = letter

        for raw_entry in section_entries_raw:
            entry_raw = normalize_space(raw_entry)
            if not entry_raw or entry_raw in {"IV.", "V.", "VI.", "VII.", "VIII.", "IX.", "X."}:
                continue
            if section == SECTION_2 and is_letter_heading(entry_raw):
                current_letter = entry_raw
                continue
            page_refs = parse_page_refs(entry_raw)
            if not page_refs:
                continue
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
            lemma_raw = infer_lemma(entry_raw)
            first_page = page_refs[0]["page_ref_int"]
            target_best = page_map.get(first_page)
            extracted_for_helper.append(
                {
                    "entry_key": entry_key,
                    "lemma_raw": lemma_raw,
                    "entry_raw": entry_raw,
                    "page_refs": page_refs,
                }
            )
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": node_lookup.get(current_letter),
                    "entry_order": entry_order,
                    "entry_kind": "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": sort_norm(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": current_letter,
                    "inferred_printed_page": first_page,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": section_start_file,
                    "target_file_best": target_best,
                    "confidence": 0.83 if target_best else 0.68,
                    "raw_json": {
                        "section_kind": section["section_kind"],
                        "source_files": [str(next(p for p in files if file_seq(p) == i)) for i in range(section["file_start_seq"], section["file_end_seq"] + 1)],
                        "page_refs": page_refs,
                    },
                }
            )
            for ref_order, ref in enumerate(page_refs, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref["raw"],
                        "page_ref_raw": ref["raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": ref["raw"] if ref.get("range_end_int") else None,
                        "range_end_raw": str(ref["range_end_int"]) if ref.get("range_end_int") else None,
                        "target_file": page_map.get(ref["page_ref_int"]),
                        "target_file_probability": 0.98 if page_map.get(ref["page_ref_int"]) else 0.55,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": section_start_file,
                        "confidence": 0.9 if page_map.get(ref["page_ref_int"]) else 0.6,
                        "raw_json": {
                            "section_kind": section["section_kind"],
                            "source_entry": entry_raw,
                        },
                    }
                )

        sections.append(
            {
                "section_key": section_key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": section["heading_norm"],
                "heading_letter": None,
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": section_start_file,
                "file_end": section_end_file,
                "confidence": 0.97,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "source_files": [str(next(p for p in files if file_seq(p) == i)) for i in range(section["file_start_seq"], section["file_end_seq"] + 1)],
                },
            }
        )

    return sections, nodes, entries, refs, extracted_for_helper


def finalize_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    sections, nodes, entries, refs, helper_entries = extract_sections(files)
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": item["entry_key"],
                "lemma_raw": item["lemma_raw"] or item["entry_raw"][:120],
                "query_names": normalize_query_variants(item["lemma_raw"] or item["entry_raw"]),
                "page_hints": [str(item["page_refs"][0]["page_ref_int"])],
                "page_hint_ints": [item["page_refs"][0]["page_ref_int"]],
                "context_raw": item["entry_raw"][:240],
            }
            for item in helper_entries
            if item["page_refs"]
        ],
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_lookup = helper_map(helper_output)

    for entry in entries:
        helper_item = helper_lookup.get(entry["entry_key"])
        if helper_item:
            entry["raw_json"]["helper_status"] = helper_item.get("status")
            entry["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
            entry["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
            entry["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
            entry["raw_json"]["helper_top_candidates"] = helper_item.get("top_candidates") or helper_item.get("candidates")
            best = helper_item.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                entry["editorial_anchor_file"] = best["file"]

    for ref in refs:
        helper_item = helper_lookup.get(ref["entry_key"])
        if helper_item:
            best = helper_item.get("best_candidate") or {}
            if best.get("file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability") or ref["target_file_probability"]
                ref["raw_json"]["helper_best_candidate"] = best
                ref["raw_json"]["helper_status"] = helper_item.get("status")

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the author index and analytical subject index from the OCR tail; editorial closure material (ORDO RERUM and contents pages) was intentionally excluded.",
        "evidence_files": [
            str(next(p for p in files if file_seq(p) == 818)),
            str(next(p for p in files if file_seq(p) == 819)),
            str(next(p for p in files if file_seq(p) == 820)),
            str(next(p for p in files if file_seq(p) == 821)),
            str(next(p for p in files if file_seq(p) == 822)),
            str(next(p for p in files if file_seq(p) == 823)),
        ],
    }
    notes = [
        "Section III is an author/heretic index keyed by alphabetic order and page references.",
        "Section IV is an analytical subject index with letter-group nodes A-Z.",
        "The final ORDO RERUM / contents matter in files 824-828 is editorial closure and was not treated as an alphabetical index section.",
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
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG086.02 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    finalize_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)


if __name__ == "__main__":
    main()

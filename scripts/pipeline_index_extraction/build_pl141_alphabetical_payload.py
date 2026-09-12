#!/usr/bin/env python3
"""Usage: build the PL141 alphabetical-index payload from the OCR front matter and tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl141_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL141/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL141_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL141_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL141 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL141_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL141"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 141"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

sys.path.insert(0, str(ROOT))

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from tools.indexing.index_target_locator import parse_ocr_page_xml

SECTION_DEFS = [
    {
        "section_key": "PL141:alpha:elenchus:001",
        "section_order": 1,
        "section_kind": "author_index",
        "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CXLI CONTINENTUR.",
        "section_kind_reason": "Front-matter author/work table of contents listing the contents of the tome under author headings and work titles.",
        "file_start_seq": 8,
        "file_end_seq": 8,
    },
    {
        "section_key": "PL141:ordo:001",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "section_kind_reason": "Closing contents table of the volume; editorial closure rather than alphabetical subject material.",
        "file_start_seq": 733,
        "file_end_seq": 738,
    },
]

BLOCK_NOISE_RE = re.compile(
    r"^(?:Digitized by Google|FINIS TOMI CENTESIMI QUADRAGESIMI PRIMI\.|Petit-Montrouge\..*|Imprimerie de M\. L\. MICNE\.)$",
    re.I,
)
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
TRAILING_REF_RE = re.compile(r"(?i)(?:(col\.?)\s*)?(?P<num>\d{1,4})\s*[.;]?$")
ENTRY_SPLIT_RE = re.compile(r"(?i)((?:(?:col\.?)\s*)?\d{1,4}\.?)\s+(?=[A-ZÆŒ])")
IBID_RE = re.compile(r"(?i)\bibid\.?\b")
SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class ParsedEntry:
    entry_key: str
    section_key: str
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str
    editorial_anchor_file: str
    target_file_best: str | None
    confidence: float
    raw_json: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = SPACE_RE.sub(" ", value).strip(" .,:;")
    return value.lower() if value else None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def iter_ocr_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        line = SPACE_RE.sub(" ", raw.replace("\xa0", " ")).strip()
        if not line or BLOCK_NOISE_RE.fullmatch(line):
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        lines.append(line)
    return lines


def is_section_heading(line: str) -> bool:
    upper = line.upper()
    return (
        upper == "ELENCHUS"
        or "AUCTORUM ET OPERUM QUI IN HOC TOMO CXLI CONTINENTUR" in upper
        or upper == "ORDO RERUM"
        or ("ORDO RERUM" in upper and ("CONTINENTUR" in upper or re.search(r"\d", line)))
        or "QUÆ IN HOC TOMO CONTINENTUR" in upper
    )


def is_heading_like(line: str) -> bool:
    if is_section_heading(line):
        return True
    if re.search(r"[a-zà-ÿ]", line):
        return False
    return bool(re.search(r"[A-ZÆŒ]", line))


def has_terminal_page_ref(line: str) -> bool:
    return bool(TRAILING_REF_RE.search(line))


def merge_logical_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line and not is_section_heading(line)]


def split_entry_chunks(line: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    for match in ENTRY_SPLIT_RE.finditer(line):
        chunk = line[start:match.end(1)].strip()
        if chunk:
            chunks.append(chunk)
        start = match.end(1)
    tail = line[start:].strip()
    if tail:
        chunks.append(tail)
    return chunks or [line]


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        for block in (parsed.get("header_text") or "", parsed.get("footer_text") or ""):
            for match in PAGE_HEADER_RE.finditer(block):
                number = int(match.group(1))
                if number > 0:
                    page_map.setdefault(number, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def extract_refs(segment: str, previous_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    page_hint = previous_page
    for part in [p.strip() for p in re.split(r"\s*—\s*", segment) if p.strip()]:
        m = TRAILING_REF_RE.search(part)
        if m:
            page_ref_int = int(m.group("num"))
            page_ref_raw = m.group(0).strip().rstrip(".;")
            page_ref_col = "col." if m.group(1) else None
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": page_ref_raw,
                    "page_ref_raw": page_ref_raw,
                    "page_ref_int": page_ref_int,
                    "page_ref_col": page_ref_col,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "inherited": False,
                }
            )
            page_hint = page_ref_int
            continue
        if IBID_RE.search(part) and page_hint is not None:
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": "ibid.",
                    "page_ref_raw": "ibid.",
                    "page_ref_int": page_hint,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "inherited": True,
                }
            )
    return refs, page_hint


def entry_lemma(line: str) -> str | None:
    parts = [part.strip() for part in re.split(r"\s*—\s*", line) if part.strip()]
    if not parts:
        return None
    first = parts[0]
    m = TRAILING_REF_RE.search(first)
    if m:
        first = first[: m.start()].rstrip(" ,;:.")
    return first or None


def build_section_entries(
    section: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    section_files = [path for path in files if section["file_start_seq"] <= file_seq(path) <= section["file_end_seq"]]
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    evidence_files = [str(path) for path in section_files]

    order = 0
    for path in section_files:
        logical_lines = merge_logical_lines(clean_lines(path))
        for line in logical_lines:
            for chunk in split_entry_chunks(line):
                if is_section_heading(chunk):
                    continue
                order += 1
                entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{order:04d}"
                lemma = entry_lemma(chunk)
                entry_kind = "heading_group" if is_heading_like(chunk) and not has_terminal_page_ref(chunk) else "lemma"
                line_refs, inferred_page = extract_refs(chunk, None)
                first_target: str | None = None
                first_prob = 0.0
                for ref_order, ref in enumerate(line_refs, start=1):
                    target_file = page_map.get(ref["page_ref_int"])
                    if not target_file:
                        target_file = str(path)
                    if first_target is None:
                        first_target = target_file
                        first_prob = 0.99 if ref["page_ref_int"] in page_map else 0.55
                    ref_entry = {
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
                        "target_file": target_file,
                        "target_file_probability": 0.99 if target_file in page_map.values() else 0.55,
                        "section_start_file": str(section_files[0]),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.95 if not ref.get("inherited") else 0.8,
                        "raw_json": {
                            "source_file": str(path),
                            "segment_raw": chunk,
                            "inherited": ref.get("inherited", False),
                        },
                    }
                    refs.append(ref_entry)

                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "entry_order": order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma,
                        "lemma_display": lemma,
                        "lemma_norm": normalize(lemma),
                        "lemma_sort": normalize(lemma),
                        "entry_raw": chunk,
                        "context_raw": chunk,
                        "heading_letter": None,
                        "inferred_printed_page": inferred_page if inferred_page is not None else (line_refs[0]["page_ref_int"] if line_refs else None),
                        "section_start_file": str(section_files[0]),
                        "editorial_anchor_file": str(path),
                        "target_file_best": first_target or str(path),
                        "confidence": 0.96 if line_refs else 0.84,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section["section_kind"],
                            "section_kind_reason": section["section_kind_reason"],
                            "line_refs_count": len(line_refs),
                            "contains_inherited_ibid": any(ref.get("inherited") for ref in line_refs),
                        },
                    }
                )

    return entries, refs, evidence_files


def build_helper_request(entries: list[dict[str, Any]], refs: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        page_hint_ints = [ref["page_ref_int"] for ref in entry_refs if isinstance(ref.get("page_ref_int"), int)]
        if not page_hint_ints:
            continue
        page_hints = []
        for ref in entry_refs:
            raw = str(ref.get("page_ref_raw") or "").strip()
            if raw and raw not in page_hints:
                page_hints.append(raw)
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry.get("entry_raw"),
                "query_names": [entry.get("lemma_raw") or entry.get("entry_raw")],
                "page_hints": page_hints[:3],
                "page_hint_ints": page_hint_ints[:3],
                "context_raw": entry.get("entry_raw"),
            }
        )

    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "python",
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
    return read_json(helper_output_json)


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = helper_map(helper_output)
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        helper_item = by_id.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or helper_item.get("best") or {}
        entry["target_file_best"] = best.get("file") or entry.get("target_file_best")
        entry["confidence"] = max(float(entry.get("confidence") or 0.0), float(best.get("probability") or 0.0))
        entry["raw_json"].update(
            {
                "helper_status": helper_item.get("status"),
                "helper_best_file": best.get("file"),
                "helper_best_probability": best.get("probability"),
                "helper_candidate_role": best.get("candidate_role"),
                "helper_reason_summary": best.get("reason_summary"),
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                    }
                    for cand in (helper_item.get("candidates") or [])[:5]
                ],
            }
        )
        for ref in refs_by_entry.get(entry["entry_key"], []):
            ref["raw_json"].update(
                {
                    "helper_status": helper_item.get("status"),
                    "helper_best_file": best.get("file"),
                    "helper_best_probability": best.get("probability"),
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": best.get("reason_summary"),
                }
            )
            if best.get("file"):
                ref["target_file"] = best.get("file")
                if best.get("probability") is not None:
                    ref["target_file_probability"] = float(best.get("probability"))


def build_sections(section_files: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        file_start, file_end = section_files[section["section_key"]]
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": normalize(section["heading_raw"]),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": file_start,
                "file_end": file_end,
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "file_start_seq": section["file_start_seq"],
                    "file_end_seq": section["file_end_seq"],
                },
            }
        )
    return sections


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = iter_ocr_files(source_root)
    page_map = build_page_map(files)

    section_file_paths: dict[str, tuple[str, str]] = {}
    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for section in SECTION_DEFS:
        section_files = [path for path in files if section["file_start_seq"] <= file_seq(path) <= section["file_end_seq"]]
        if not section_files:
            raise SystemExit(f"Could not locate OCR files for section {section['section_key']}")
        section_file_paths[section["section_key"]] = (str(section_files[0]), str(section_files[-1]))
        entries, refs, section_evidence = build_section_entries(section, files, page_map)
        all_entries.extend(entries)
        all_refs.extend(refs)
        evidence_files.extend(section_evidence)

    evidence_files = list(dict.fromkeys(evidence_files))

    helper_request = build_helper_request(all_entries, all_refs, source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    apply_helper(all_entries, all_refs, helper_output)

    sections = build_sections(section_file_paths)

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": [],
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the front-matter ELENCHUS and the closing ORDO RERUM block directly from OCR, with helper-assisted target resolution for page-linked fragments.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "The volume contains a front-matter ELENCHUS of authors and works plus a closing ORDO RERUM contents table.",
            "Bare ibid. locators were preserved as inherited page references only when a prior explicit page reference existed in the same entry.",
            "Helper lookups were used to confirm the physical target file for page-linked fragments; section anchors remain distinct from target files.",
        ],
    }

    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "section_count": len(payload["sections"]),
            "evidence_files": evidence_files,
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Final payload written and helper-checked.",
            "completed": [
                "Confirmed the front ELENCHUS section and the closing ORDO RERUM section in OCR.",
                "Generated helper request and resolved page-linked fragments.",
                "Assembled the canonical payload and checkpoint fragments.",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "ELENCHUS is modeled as author_index; the tail contents block is modeled as ordo_rerum.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL141 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

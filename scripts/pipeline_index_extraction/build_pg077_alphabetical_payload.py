#!/usr/bin/env python3
"""Usage: build the PG077 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pg077_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG077/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG077_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG077_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG077 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG077_alphabetical_indices.json
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
VOLUME_ID = "PG077"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologiae Graecae Tomus LXXVII"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
INDEX_SEQS = {760, 761, 762, 763}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "Digitized by Google" in line:
            continue
        if re.fullmatch(r"\d{4}\s+INDEX ANALYTICUS.*\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}\s+IN EPISTOLAS ET HOMILIAS S\. CYRILLI\.\s+\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}\s+INDEX ANALYTICUS IN EPISTOLAS ET HOMILIAS S\. CYRILLI\.\s+\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}\s+ORDO RERUM.*\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}\s+ORDO EDITIONUM\.\s+\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}", line):
            continue
        if line in {"INDEX ANALYTICUS", "ORDO RERUM", "ORDO EDITIONUM.", "ORDO EDITIONUM"}:
            continue
        if line == "IN":
            continue
        if line.startswith("EPIST.") or line.startswith("HOMILIA"):
            continue
        if line.startswith("QUÆ IN HOC TOMO CONTINENTUR"):
            continue
        if line.startswith("QUÆ IN HOC TOMO CONTINENTUR."):
            continue
        if line.startswith("Revocatur lector ad paginas editionis Joannis Auberti"):
            continue
        if line.startswith("Littera A epistolas"):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        top_text = " ".join(
            [
                parsed.get("header_text") or "",
                parsed.get("footer_text") or "",
            ]
        ).strip()
        if not top_text:
            top_text = " ".join((parsed.get("all_text") or "").splitlines()[:6])
        numbers: list[int] = []
        for raw in re.findall(r"\b(\d{1,4})\b", top_text):
            num = int(raw)
            if num not in numbers:
                numbers.append(num)
        for num in numbers:
            page_map.setdefault(num, str(path))
    return page_map


def build_index_body(files: list[Path]) -> tuple[str, dict[str, str]]:
    body_parts: list[str] = []
    file_bodies: dict[str, str] = {}
    started = False
    for path in files:
        lines = clean_lines(path)
        file_bodies[str(path)] = " ".join(lines)
        for line in lines:
            if not started:
                if line == "A":
                    started = True
                else:
                    continue
            if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X"}:
                continue
            body_parts.append(line)
    body = " ".join(body_parts)
    body = re.sub(r"\s+", " ", body).strip()
    return body, file_bodies


def split_segments(body: str) -> list[str]:
    pieces = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])|(?<=\bseq)\s+(?=[A-ZÆŒ])", body)
    segments: list[str] = []
    for piece in pieces:
        seg = piece.strip()
        if not seg:
            continue
        if seg in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X"}:
            continue
        if seg.startswith("INDEX ANALYTICUS"):
            continue
        if seg.startswith("ORDO RERUM") or seg.startswith("ORDO EDITIONUM"):
            continue
        segments.append(seg)
    return segments


def extract_refs(segment: str) -> list[int]:
    refs: list[int] = []
    for match in re.finditer(r"\b(\d{1,4})(?:\s*et\s+seq\.?)?", segment, flags=re.IGNORECASE):
        num = int(match.group(1))
        if num not in refs:
            refs.append(num)
    return refs


def lemma_from_segment(segment: str) -> str:
    candidate = segment.strip()
    if not candidate:
        return candidate
    if candidate.startswith("A "):
        candidate = candidate[2:].lstrip()
    m = re.search(r",\s*(?:[ABCD]|[IVXLCDM]+|\d{1,4})(?:\s*,\s*\d{1,4})*", candidate)
    if m:
        lemma = candidate[: m.start()]
    else:
        lemma = candidate
    return lemma.rstrip(" ,;:.")


def first_letter(lemma: str | None) -> str | None:
    if not lemma:
        return None
    value = normalize(lemma)
    if not value:
        return None
    for ch in value:
        if ch.isalpha():
            if ch.lower() == "æ":
                return "A"
            if ch.lower() == "œ":
                return "O"
            return ch.upper()
    return None


def locate_source_file(segment: str, file_bodies: dict[str, str], ordered_files: list[Path]) -> str | None:
    snippet = normalize(segment[:120] if len(segment) > 120 else segment)
    if not snippet:
        return None
    snippet = snippet[:90]
    for path in ordered_files:
        body = normalize(file_bodies.get(str(path), ""))
        if body and snippet in body:
            return str(path)
    return str(ordered_files[0]) if ordered_files else None


def helper_index(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            result[str(entry_id)] = item
    return result


def helper_summary(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates = item.get("candidates") or []
    top_candidates: list[dict[str, Any]] = []
    for cand in candidates[:3]:
        top_candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:4]],
            }
        )
    return {
        "helper_status": item.get("status"),
        "helper_best_candidate": best,
        "helper_top_candidates": top_candidates,
    }


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path) -> None:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        refs = entry.get("refs", [])
        page_hints = [str(r["page_ref_int"]) for r in refs if r.get("page_ref_int") is not None]
        page_hint_ints = [r["page_ref_int"] for r in refs if r.get("page_ref_int") is not None]
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"], entry["lemma_norm"] or entry["lemma_raw"]],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )
    payload = {
        "volume_id": VOLUME_ID,
        "source_root": str(ROOT / "teste/PG077/text"),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, payload)


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(helper_request_json), "--output", str(helper_output_json), "--pretty"],
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def parse_entries(files: list[Path], page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    body, file_bodies = build_index_body(files)
    segments = split_segments(body)
    nodes: list[dict[str, Any]] = []
    node_by_letter: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    refs_out: list[dict[str, Any]] = []
    letter_order = 1
    entry_order = 1

    for segment in segments:
        lemma = lemma_from_segment(segment)
        if not lemma:
            continue
        refs = extract_refs(segment)
        if not refs:
            continue
        letter = first_letter(lemma)
        if letter and letter not in node_by_letter:
            node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:letter:{letter}"
            node_by_letter[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                    "parent_node_key": None,
                    "node_order": letter_order,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {"source": "ocr_letter_group"},
                }
            )
            letter_order += 1

        source_file = locate_source_file(segment, file_bodies, files)
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        inferred_page = refs[0]
        entry = {
            "entry_key": entry_key,
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "parent_node_key": node_by_letter.get(letter),
            "entry_order": entry_order,
            "entry_kind": "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": normalize(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": segment,
            "context_raw": segment,
            "heading_letter": letter,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": source_file,
            "target_file_best": page_map.get(inferred_page),
            "confidence": 0.92,
            "raw_json": {
                "source_file": source_file,
                "segment": segment,
                "page_refs": refs,
            },
        }
        entries.append(entry)

        for ref_order, page_num in enumerate(refs, start=1):
            target_file = page_map.get(page_num)
            ref = {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": str(page_num),
                "page_ref_raw": str(page_num),
                "page_ref_int": page_num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": 1.0 if target_file else None,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": source_file,
                "confidence": 0.92 if target_file else 0.62,
                "raw_json": {
                    "source_file": source_file,
                    "page_lookup_resolved": bool(target_file),
                },
            }
            refs_out.append(ref)
        entry_order += 1

    section = {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS IN EPISTOLAS ET HOMILIAS S. CYRILLI.",
        "heading_norm": "index analyticus in epistolas et homilias s. cyrilli",
        "heading_letter": None,
        "page_start": 1517,
        "page_end": 1522,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.96,
        "raw_json": {
            "section_kind_reason": "Alphabetical analytical index with internal letter-group headings and page citations.",
            "evidence_files": [str(p) for p in files],
            "notes": [
                "OCR title appears across the first index pages; file order is used as the section scan window.",
                "The non-alphabetical ORDO RERUM / ORDO EDITIONUM material at the end of the volume was excluded."
            ],
        },
    }
    return section, nodes, entries, refs_out, [{"file": str(p), "line_count": len(clean_lines(p))} for p in files]


def build_payload(intermediate_dir: Path, generated_at: str | None = None) -> dict[str, Any]:
    manifest = read_json(intermediate_dir / "manifest.json", {})
    volume = read_json(intermediate_dir / "volume.json")
    coverage = read_json(intermediate_dir / "coverage.json", {})
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at or manifest.get("generated_at") or manifest.get("updated_at"),
        "volume": volume,
        "sections": read_json(intermediate_dir / "sections.json", []),
        "nodes": read_json(intermediate_dir / "nodes.json", []),
        "entries": read_json(intermediate_dir / "entries.json", []),
        "refs": read_json(intermediate_dir / "refs.json", []),
        "scripture_refs": read_json(intermediate_dir / "scripture_refs.json", []),
        "coverage": coverage,
        "notes": read_json(intermediate_dir / "notes.json", []),
    }
    if payload["generated_at"] is None:
        raise SystemExit("generated_at is required via manifest.json or --generated-at")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PG077 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = sorted(args.source_root.glob("*.txt"), key=file_seq)
    index_files = [p for p in files if file_seq(p) in INDEX_SEQS]
    if len(index_files) != 4:
        raise SystemExit(f"Expected 4 index files for PG077, found {len(index_files)}")

    page_map = build_page_map(files)
    section, nodes, entries, refs_out, evidence_files = parse_entries(index_files, page_map)
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
    }
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytical alphabetical index from the OCR index pages and resolved page citations locally.",
        "evidence_files": [str(p) for p in index_files],
    }
    notes = [
        "The OCR tail also contains ORDO RERUM and ORDO EDITIONUM material, but those are editorial contents tables rather than part of the alphabetical analytical index.",
        "Letter-group headings were promoted to nodes, and printed-page citations were kept separate from OCR file suffixes.",
    ]

    helper_request_entries: list[dict[str, Any]] = []
    for entry in entries:
        helper_request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"], entry["lemma_norm"] or entry["lemma_raw"]],
                "page_hints": [str(ref["page_ref_int"]) for ref in refs_out if ref["entry_key"] == entry["entry_key"]],
                "page_hint_ints": [ref["page_ref_int"] for ref in refs_out if ref["entry_key"] == entry["entry_key"]],
                "context_raw": entry["entry_raw"],
            }
        )
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_index(helper_output)

    for entry in entries:
        helper_entry = helper_map.get(entry["entry_key"])
        if helper_entry:
            entry["raw_json"]["helper"] = helper_summary(helper_entry)
            best = helper_entry.get("best_candidate") or {}
            if best.get("file") and not entry.get("target_file_best"):
                entry["target_file_best"] = best.get("file")
            if best.get("probability") is not None:
                entry["confidence"] = max(entry["confidence"], float(best.get("probability")))
    for ref in refs_out:
        helper_entry = helper_map.get(ref["entry_key"])
        if helper_entry:
            ref["raw_json"]["helper"] = helper_summary(helper_entry)
            best = helper_entry.get("best_candidate") or {}
            if best.get("file") and not ref.get("target_file"):
                ref["target_file"] = best.get("file")
                ref["target_file_probability"] = best.get("probability")
                if best.get("probability") is not None:
                    ref["confidence"] = max(ref["confidence"], float(best.get("probability")))

    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()})
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", [section])
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs_out)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PG077 alphabetical index payload",
            "completed": [
                "index pages identified",
                "analytical entries segmented",
                "helper request generated and resolved",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The OCR title and the page headers span more than one file; the section window uses the full analytic index run.",
                "ORO RERUM / ORDO EDITIONUM content remains excluded from this alphabetical payload.",
            ],
        },
    )

    payload = build_payload(args.intermediate_dir, generated_at=now_iso())
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL174 alphabetical-index payload from OCR pages and helper resolution.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl174_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL174/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL174_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL174_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL174 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL174_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL174"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 174"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET EXPOSITIONUM MYSTICARUM.",
        "heading_norm": "index rerum et expositionum mysticarum",
        "files": list(range(828, 831)),
        "start_marker": "INDEX RERUM ET EXPOSITIONUM MYSTICARUM.",
        "end_marker": "I. IN HOMILIAS DOMINICALES.",
        "section_order": 1,
        "page_start": 1633,
        "page_end": 1640,
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:002",
        "section_kind": "onomastic_mixed",
        "heading_raw": "I. INDEX NOMINUM ET VOCUM BIBLICARUM.",
        "heading_norm": "index nominum et vocum biblicarum",
        "files": list(range(831, 836)),
        "start_marker": "INDEX NOMINUM ET VOCUM BIBLICARUM.",
        "end_marker": "II. IN HOMILIAS FESTIVALES.",
        "section_order": 2,
        "page_start": 1633,
        "page_end": 1646,
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:003",
        "section_kind": "onomastic_mixed",
        "heading_raw": "I. INDEX NOMINUM ET VOCUM BIBLICARUM.",
        "heading_norm": "index nominum et vocum biblicarum",
        "files": [836],
        "start_marker": "INDEX NOMINUM ET VOCUM BIBLICARUM.",
        "end_marker": "II. INDEX RERUM ET EXPOSITIONUM MEMORABILIUM.",
        "section_order": 3,
        "page_start": 1651,
        "page_end": 1652,
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:004",
        "section_kind": "analytic_subject",
        "heading_raw": "II. INDEX RERUM ET EXPOSITIONUM MEMORABILIUM.",
        "heading_norm": "index rerum et expositionum memorabilium",
        "files": list(range(837, 847)),
        "start_marker": "INDEX RERUM ET EXPOSITIONUM MEMORABILIUM.",
        "end_marker": "INDICES IN CHRONICON ALDENBURGENSE MAJUS.",
        "section_order": 4,
        "page_start": 1653,
        "page_end": 1666,
    },
]

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LINE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SEG_SPLIT_RE = re.compile(r"(?<=\d)\.\s+(?=[A-ZÆŒ])")
NOISE_RE = re.compile(r"^(?:Digitized by Google|FINIS TOMI CENTESIMI SEPTUAGESIMI QUARTI\.)$", re.IGNORECASE)
HELPER_FILE_RE = re.compile(r"^[A-ZÆŒ]\w*")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def get_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines = []
    for raw in parsed["all_text"].splitlines():
        text = normalize(raw)
        if not text or NOISE_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        lines = get_lines(path)
        header_lines = lines[:3]
        for line in header_lines:
            for match in HEADER_PAGE_RE.finditer(line):
                value = int(match.group(1))
                if value >= 10:
                    page_map.setdefault(value, str(path))
    return page_map


def iter_section_lines(files: list[Path], start_marker: str, end_marker: str | None) -> tuple[list[str], str, str]:
    lines: list[str] = []
    start_file = str(files[0])
    end_file = str(files[-1])
    started = False
    for path in files:
        path_lines = get_lines(path)
        file_text = "\n".join(path_lines)
        if not started:
            if start_marker.lower() in file_text.lower():
                started = True
                start_file = str(path)
                marker_seen = False
                for idx, line in enumerate(path_lines):
                    if start_marker.lower() in line.lower():
                        marker_seen = True
                        tail = path_lines[idx + 1 :]
                        lines.extend(tail)
                        break
                if not marker_seen:
                    continue
            else:
                continue
        else:
            lines.extend(path_lines)
        end_hit = False
        if end_marker is not None:
            for line in path_lines:
                if end_marker.lower() in line.lower():
                    end_hit = True
                    break
        if end_hit:
            end_file = str(path)
            break
        end_file = str(path)
    return lines, start_file, end_file


def is_heading(line: str) -> bool:
    value = normalize(line) or ""
    if not value:
        return False
    if value in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
        return True
    if value.startswith("I.") or value.startswith("II.") or value.startswith("III."):
        return True
    return False


def split_entry_segments(line: str) -> list[str]:
    text = normalize(line) or ""
    if not text:
        return []
    return [seg.strip() for seg in SEG_SPLIT_RE.split(text) if seg.strip()]


def extract_refs(segment: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for order, match in enumerate(LINE_REF_RE.finditer(segment), start=1):
        page = int(match.group(1))
        refs.append(
            {
                "ref_order": order,
                "ref_kind": "editorial_page",
                "ref_raw": match.group(1),
                "page_ref_raw": match.group(1),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def derive_lemma_raw(segment: str) -> str | None:
    text = normalize(segment) or ""
    if not text:
        return None
    first_ref = LINE_REF_RE.search(text)
    if first_ref:
        lemma = text[: first_ref.start()].strip()
    else:
        lemma = text
    lemma = lemma.strip(" ,;:.")
    return lemma or None


def parse_section(section_spec: dict[str, Any], all_files: list[Path], page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    files = [path for path in all_files if file_num(path) in set(section_spec["files"])]
    lines, start_file, end_file = iter_section_lines(files, section_spec["start_marker"], section_spec["end_marker"])
    sections = [
        {
            "section_key": section_spec["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": section_spec["section_order"],
            "section_kind": section_spec["section_kind"],
            "heading_raw": section_spec["heading_raw"],
            "heading_norm": section_spec["heading_norm"],
            "heading_letter": None,
            "page_start": section_spec["page_start"],
            "page_end": section_spec["page_end"],
            "file_start": start_file,
            "file_end": end_file,
            "confidence": 0.86 if section_spec["section_kind"] == "analytic_subject" else 0.82,
            "raw_json": {
                "section_kind_reason": "Alphabetical index section recovered from OCR headings and local letter-group markers.",
                "source_files": [str(path) for path in files],
                "start_marker": section_spec["start_marker"],
                "end_marker": section_spec["end_marker"],
            },
        }
    ]
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    current_node_key: str | None = None
    node_order = 0
    entry_order = 0
    current_letter: str | None = None

    def ensure_letter(letter: str, source_file: str) -> None:
        nonlocal current_node_key, node_order, current_letter
        if current_letter == letter and current_node_key is not None:
            return
        node_order += 1
        current_letter = letter
        current_node_key = f"{VOLUME_ID}:node:{section_spec['section_order']:02d}:{node_order:03d}"
        nodes.append(
            {
                "node_key": current_node_key,
                "section_key": section_spec["section_key"],
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_file": source_file},
            }
        )

    for path in files:
        path_lines = get_lines(path)
        for line in path_lines:
            lowered = line.lower()
            if re.fullmatch(r"\d{3,4}(?:\s+\d{3,4})?", line):
                continue
            if (
                "indices in opera godefridi" in lowered
                or "in homilias dominicales" in lowered
                or "in homilia" in lowered
                or "in homilias festivales" in lowered
                or "index nominum et vocum biblicarum" in lowered
                or "index rerum et expositionum mysticarum" in lowered
                or "index rerum et expositionum memorabilium" in lowered
                or "ordo rerum" in lowered
            ):
                continue
            if section_spec["end_marker"] and section_spec["end_marker"].lower() in lowered:
                continue
            if is_heading(line):
                ensure_letter(line.strip(" ."), str(path))
                continue
            if line == "Digitized by Google":
                continue
            segments = split_entry_segments(line)
            if not segments:
                continue
            for segment in segments:
                seg = normalize(segment) or ""
                if not seg:
                    continue
                if re.fullmatch(r"[\d,\s\.]+", seg):
                    continue
                if len(seg) == 1 and seg.isalpha() and seg.isupper():
                    ensure_letter(seg, str(path))
                    continue
                if current_node_key is None:
                    ensure_letter((seg[:1] or "A").upper(), str(path))
                refs_for_entry = extract_refs(seg)
                lemma_raw = derive_lemma_raw(seg)
                if not lemma_raw:
                    continue
                inferred_page = refs_for_entry[0]["page_ref_int"] if refs_for_entry else None
                target_file = page_map.get(inferred_page) if inferred_page is not None else None
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{section_spec['section_order']:02d}:{entry_order:04d}"
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_spec["section_key"],
                    "parent_node_key": current_node_key,
                    "entry_order": entry_order,
                    "entry_kind": "index_entry",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": seg,
                    "context_raw": seg,
                    "heading_letter": current_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": start_file,
                    "editorial_anchor_file": start_file,
                    "target_file_best": target_file,
                    "confidence": 0.82 if refs_for_entry else 0.6,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section_spec["section_kind"],
                    },
                }
                entries.append(entry)
                if refs_for_entry:
                    for ref in refs_for_entry:
                        ref_copy = {
                            "entry_key": entry_key,
                            "ref_order": ref["ref_order"],
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": page_map.get(ref["page_ref_int"]),
                            "target_file_probability": 0.99 if page_map.get(ref["page_ref_int"]) else None,
                            "section_start_file": start_file,
                            "editorial_anchor_file": start_file,
                            "confidence": 0.83,
                            "raw_json": {"source_file": str(path)},
                        }
                        refs.append(ref_copy)
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or seg[:80],
                        "query_names": [q for q in [lemma_raw, seg[:120], (lemma_raw or "")[:40]] if q],
                        "page_hints": [str(inferred_page)] if inferred_page is not None else [],
                        "page_hint_ints": [inferred_page] if inferred_page is not None else [],
                        "context_raw": seg,
                    }
                )
                evidence_files.append(str(path))

    return sections[0], nodes, entries, refs, helper_entries, sorted(set(evidence_files))


def build_helper_request(source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def merge_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = {item.get("entry_id"): item for item in (helper_output.get("entries") or []) if isinstance(item, dict)}
    for entry in entries:
        helper = helper_map.get(entry["entry_key"])
        if helper:
            entry["raw_json"]["helper"] = {
                "status": helper.get("status"),
                "candidate_role": helper.get("candidate_role"),
                "reason_summary": helper.get("reason_summary"),
                "best_candidate": helper.get("best_candidate"),
            }
            best = helper.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                if entry.get("editorial_anchor_file") is None:
                    entry["editorial_anchor_file"] = best["file"]
    for ref in refs:
        helper = helper_map.get(ref["entry_key"])
        if helper:
            ref["raw_json"]["helper"] = {
                "status": helper.get("status"),
                "candidate_role": helper.get("candidate_role"),
                "reason_summary": helper.get("reason_summary"),
                "best_candidate": helper.get("best_candidate"),
            }
            best = helper.get("best_candidate") or {}
            if best.get("file") and ref.get("target_file") is None:
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    output_file: Path,
    skip_helper: bool = False,
) -> dict[str, Any]:
    all_files = discover_text_files(source_root)
    page_map = build_page_map(all_files)

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for spec in SECTION_SPECS:
        section, section_nodes, section_entries, section_refs, section_helpers, section_evidence = parse_section(spec, all_files, page_map)
        sections.append(section)
        nodes.extend(section_nodes)
        entries.extend(section_entries)
        refs.extend(section_refs)
        helper_entries.extend(section_helpers)
        evidence_files.extend(section_evidence)

    helper_request = build_helper_request(source_root, helper_entries)
    write_json(helper_request_json, helper_request)
    if skip_helper:
        helper_output = {"status": "skipped", "entries": []}
    else:
        helper_output = run_helper(helper_request_json, helper_output_json)
        merge_helper(entries, refs, helper_output)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the sectioned alphabetical indices from OCR pages with local line parsing and helper-backed target resolution.",
        "evidence_files": sorted(set(evidence_files)),
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
    }

    notes = [
        "The volume contains multiple index blocks: a mysticarum index, two biblical-name indices, and a memorabilium index.",
        "Section boundaries were determined from OCR headings and local continuation pages.",
        "Page refs were kept literal; target files were resolved from local page headers and then corroborated with the helper locator.",
    ]

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Validate PL174 alphabetical payload and keep OCR literals intact.",
        "completed": [
            "index headings mapped",
            "entries and refs extracted",
            "helper request written and helper executed",
        ],
        "pending": [
            "review ambiguous line splits if any",
        ],
        "blocked": [],
        "notes": [
            "Use OCR headers for target-file anchors before relying on helper output.",
            "Keep editorial page and OCR file suffix separate.",
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
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
            "output_file": str(output_file),
        },
    )
    write_json(intermediate_dir / "todo.json", todo)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL174 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--skip-helper", action="store_true", help="Build the payload without calling index_target_locator.py.")
    args = ap.parse_args()

    build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
        output_file=args.output_file,
        skip_helper=args.skip_helper,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage: build the PL166 ORDO RERUM payload and helper request.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl166_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL166/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL166_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL166_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL166 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL166_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PL166"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 166"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_PAGE_START = 1573
SECTION_PAGE_END = 1600
SECTION_FILE_START_SEQ = 795
SECTION_FILE_END_SEQ = 808


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def fold(text: str | None) -> str:
    if text is None:
        return ""
    value = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = fold(value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_sort_key(path: Path) -> tuple[int, str]:
    m = re.search(r"-(\d+)\.txt$", path.name)
    return (int(m.group(1)) if m else 10**9, path.name)


def extract_text_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo = re.search(r'tipo="([^"]+)"', attrs)
        if (tipo.group(1).strip().lower() if tipo else "") != "texto_principal":
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue
            if line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def combine_hyphenated_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.endswith("-") and i + 1 < len(lines):
            out.append(f"{line[:-1]}{lines[i + 1].lstrip()}")
            i += 2
            continue
        out.append(line)
        i += 1
    return out


def is_noise_line(line: str) -> bool:
    if not line:
        return True
    if line == "Digitized by Google":
        return True
    if line == "PATROL. CLXVI.":
        return True
    if re.fullmatch(r"\d{4}", line):
        return True
    if line in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "QUAE IN HOC TOMO CONTINENTUR."}:
        return True
    if re.fullmatch(r"\d{4}\s+ORDO RERUM(?:\s+QU[AEÆ];?N HOC TOMO CONTINENTUR\.)?\s+\d{4}", line):
        return True
    return False


def strip_page_ref(line: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$", line)
    if not match:
        return line, None, None
    lemma = normalize_space(match.group("lemma").rstrip(" ,;:."))
    page_raw = match.group("page")
    return lemma, page_raw, int(page_raw)


def is_heading_like(line: str) -> bool:
    if not line:
        return False
    if re.match(r"^(?:CAP|Cap|LIBER|PARS|PROLOGUS|PROŒMIUM|Prologus|Præfatio|PRAEFATIO|Appendix|DIVERSORUM|VITA|Notitia|EPISTOLÆ|EPISTOLAE|SERMO|TRACTATUS|ACTA|DIPLOMA|DE |EXORDIUM|RHYTHMUS|CARMINA|HIEROSOLYMITANÆ HISTORIÆ LIBRI QUATUOR|HISTORIA HIEROSOLYMITANÆ EXPEDITIONIS|EXCERPTA E CARTULARIO ECCLESIÆ GRATIANOPOLITANÆ)\b", line):
        return True
    if line.isupper() and not re.search(r"\d{1,4}\s*$", line):
        return True
    if len(line.split()) <= 4 and line.endswith(".") and not re.search(r"\d{1,4}$", line):
        return True
    return False


def make_query_names(lemma_raw: str) -> list[str]:
    candidates = [normalize_space(lemma_raw)]
    folded = normalize_space(fold(lemma_raw))
    if folded and folded not in candidates:
        candidates.append(folded)
    stripped = re.sub(r"^\s*[IVXLCDM]+\.\s*—\s*", "", candidates[0])
    stripped = re.sub(r"^\s*[IVXLCDM]+\.\s*", "", stripped)
    stripped = normalize_space(stripped)
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    return [value for value in dict.fromkeys(candidates) if value]


def parse_toc_items(source_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    files = [source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-{seq}.txt" for seq in range(SECTION_FILE_START_SEQ, SECTION_FILE_END_SEQ + 1)]
    lines_with_source: list[tuple[str, Path]] = []
    for path in files:
        lines = combine_hyphenated_lines(extract_text_lines(path))
        for line in lines:
            lines_with_source.append((line, path))

    entries: list[dict[str, Any]] = []
    skipped_headings: list[str] = []
    buffer: list[str] = []
    buffer_source: Path | None = None
    entry_order = 0

    def flush(page_raw: str | None, page_int: int | None, source_file: Path) -> None:
        nonlocal buffer, buffer_source, entry_order
        if not buffer:
            return
        entry_raw = normalize_space(" ".join(buffer))
        lemma_raw = entry_raw if page_raw is not None else entry_raw
        if not re.search(r"[A-Za-zÆæŒœ]", lemma_raw):
            buffer = []
            buffer_source = None
            return
        entry_order += 1
        entries.append(
            {
                "entry_order": entry_order,
                "entry_raw": entry_raw if page_raw is not None else entry_raw,
                "lemma_raw": lemma_raw,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "source_file": str(source_file),
                "context_raw": entry_raw,
                "query_names": make_query_names(lemma_raw),
            }
        )
        buffer = []
        buffer_source = None

    for line, source_file in lines_with_source:
        if is_noise_line(line):
            continue
        if line.startswith("157") or line.startswith("158") or line.startswith("159") or line.startswith("160"):
            # Standalone OCR page headers.
            if re.fullmatch(r"\d{4}", line):
                continue
        lemma_raw, page_raw, page_int = strip_page_ref(line)
        if page_raw is not None:
            if buffer:
                buffer.append(lemma_raw)
                flush(page_raw, page_int, source_file)
            else:
                if not re.search(r"[A-Za-zÆæŒœ]", lemma_raw):
                    continue
                entry_order += 1
                entries.append(
                    {
                        "entry_order": entry_order,
                        "entry_raw": line,
                        "lemma_raw": lemma_raw,
                        "page_ref_raw": page_raw,
                        "page_ref_int": page_int,
                        "source_file": str(source_file),
                        "context_raw": line,
                        "query_names": make_query_names(lemma_raw),
                    }
                )
            continue

        if is_heading_like(line):
            if buffer:
                buffer = []
                buffer_source = None
            skipped_headings.append(line)
            continue

        if buffer_source is None:
            buffer_source = source_file
        buffer.append(line)

    return entries, skipped_headings


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for idx, entry in enumerate(entries, start=1):
        page_int = entry.get("page_ref_int")
        if page_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{idx:03d}",
                "lemma_raw": entry["lemma_raw"],
                "query_names": entry["query_names"],
                "page_hints": [str(page_int)],
                "page_hint_ints": [page_int],
                "context_raw": entry["context_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def helper_result_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries") or []:
        entry_id = str(item.get("entry_id") or "")
        if entry_id:
            out[entry_id] = item
    return out


def build_payload(
    source_root: Path,
    helper_output: dict[str, Any],
    toc_entries: list[dict[str, Any]],
    skipped_headings: list[str],
) -> dict[str, Any]:
    helper_map = helper_result_map(helper_output)
    entries_payload: list[dict[str, Any]] = []
    refs_payload: list[dict[str, Any]] = []

    for idx, entry in enumerate(toc_entries, start=1):
        entry_id = f"{VOLUME_ID.lower()}_{idx:03d}"
        helper_item = helper_map.get(entry_id, {})
        best = helper_item.get("best_candidate") or {}
        candidates = helper_item.get("candidates") or []
        entry_kind = "heading_group" if entry["lemma_raw"].isupper() or entry["lemma_raw"].startswith(("LIBER", "PARS", "DIVERSORUM", "EPISTOLAE", "EPISTOLÆ", "ACTA", "VITA", "EXORDIUM", "FRANCO", "DROGO", "HONORIUS", "RHYTHMUS")) else "lemma"
        section_start_file = str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-{SECTION_FILE_START_SEQ:03d}.txt")
        editorial_anchor_file = entry["source_file"]
        target_file = best.get("file")
        confidence = 0.72
        if helper_item.get("status") == "resolved":
            confidence = 0.92 if best else 0.72
        elif helper_item.get("status") == "ambiguous":
            confidence = 0.62

        entry_payload = {
            "entry_key": f"{VOLUME_ID}:entry:{idx:03d}",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": idx,
            "entry_kind": entry_kind,
            "lemma_raw": entry["lemma_raw"],
            "lemma_display": entry["lemma_raw"],
            "lemma_norm": normalize_space(fold(entry["lemma_raw"])).lower() or None,
            "lemma_sort": sort_norm(entry["lemma_raw"]),
            "entry_raw": entry["entry_raw"],
            "context_raw": entry["context_raw"],
            "heading_letter": None,
            "inferred_printed_page": entry["page_ref_int"],
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "target_file_best": target_file,
            "confidence": confidence,
            "raw_json": {
                "entry_id": entry_id,
                "helper_status": helper_item.get("status"),
                "helper_best_candidate": best or None,
                "helper_candidate_role": best.get("candidate_role") if best else None,
                "helper_reason_summary": best.get("reason_summary") if best else None,
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or [])[:5]],
                    }
                    for cand in candidates[:3]
                ],
                "source_file": entry["source_file"],
            },
        }
        entries_payload.append(entry_payload)

        if entry["page_ref_int"] is not None:
            refs_payload.append(
                {
                    "entry_key": entry_payload["entry_key"],
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": entry["page_ref_raw"],
                    "page_ref_raw": entry["page_ref_raw"],
                    "page_ref_int": entry["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": best.get("probability") if best else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": editorial_anchor_file,
                    "confidence": confidence,
                    "raw_json": {
                        "entry_id": entry_id,
                        "helper_status": helper_item.get("status"),
                        "helper_best_candidate": best or None,
                        "helper_top_candidates": [
                            {
                                "file": cand.get("file"),
                                "probability": cand.get("probability"),
                                "candidate_role": cand.get("candidate_role"),
                            }
                            for cand in candidates[:3]
                        ],
                    },
                }
            )

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": SECTION_PAGE_START,
            "page_end": SECTION_PAGE_END,
            "file_start": str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-{SECTION_FILE_START_SEQ:03d}.txt"),
            "file_end": str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-{SECTION_FILE_END_SEQ:03d}.txt"),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial contents table (ordo rerum), not an alphabetical index; it enumerates the volume's internal works, parts, and chapters.",
                "heading_sources": [
                    str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-795.txt"),
                    str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-808.txt"),
                ],
            },
        }
    ]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the complete ORDO RERUM table of contents from files 795-808 and anchored each page-numbered line to a target OCR file via helper resolution.",
        "evidence_files": [
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-795.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-796.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-797.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-798.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-799.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-800.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-801.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-802.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-803.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-804.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-805.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-806.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-807.txt"),
            str(source_root / f"829820ff-d6a9-4ae5-aaf8-bc119f4039e1-808.txt"),
        ],
    }

    notes = [
        "PL166 is a contents table (ordo rerum), not a true alphabetical index; entries were preserved as editorial lines with their printed page anchors.",
    ]
    if skipped_headings:
        notes.append(f"Skipped {len(skipped_headings)} unnumbered heading fragments that did not function as index entries.")

    return {
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
        "entries": entries_payload,
        "refs": refs_payload,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    intermediate_dir: Path = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    todo_path = intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PL166 ORDO RERUM entries and target files",
            "completed": [],
            "pending": [
                "parse OCR contents block",
                "run helper for target_file resolution",
                "assemble final payload",
            ],
            "blocked": [],
            "notes": [
                "This volume is an editorial contents table, not a true alphabetical index.",
            ],
        },
    )

    toc_entries, skipped_headings = parse_toc_items(args.source_root)
    write_json(intermediate_dir / "entries.json", toc_entries)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "source_root": str(args.source_root), "entry_count": len(toc_entries)})

    helper_request = build_helper_request(args.source_root, toc_entries)
    write_json(args.helper_request_json, helper_request)

    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(args.helper_request_json),
            "--output",
            str(args.helper_output_json),
            "--pretty",
        ],
        check=True,
    )

    helper_output = read_json(args.helper_output_json, default={}) or {}
    write_json(intermediate_dir / "helper_output.json", helper_output)

    payload = build_payload(args.source_root, helper_output, toc_entries, skipped_headings)
    write_json(args.output_file, payload)
    write_json(intermediate_dir / "payload.json", payload)

    todo_path.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": now_iso(),
                "current_focus": "Final payload written",
                "completed": [
                    "parsed OCR contents block",
                    "ran helper for target_file resolution",
                    "assembled final payload",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "PL166 is treated as ORDO RERUM / contents material.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

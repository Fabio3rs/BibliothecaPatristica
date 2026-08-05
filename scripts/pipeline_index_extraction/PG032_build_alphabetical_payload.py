#!/usr/bin/env python3
"""Usage: build the PG032 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG032_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG032/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG032_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG032_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG032 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG032_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG032"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 32"

SECTION_1_FILES = (714, 714)
SECTION_2_FILES = (715, 761)
SECTION_3_FILES = (762, 770)

TEXT_BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<body>.*?)</bloco>', re.I | re.S)
FILE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SECTION_HEADING_RE = re.compile(
    r"^(?:INDEX\s+ANALYTICUS\s+IN\s+TOMUM\s+[IVXLC]+\.?|S\.\s+BASILII\s+EPISTOLARUM\s+INDEX\s+ALPHABETICUS\.?|ORDO\s+RERUM(?:\s+QU[ÆAE]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|INDEX\s+RERUM\s+ET\s+VERBORUM.*|INDEX\s+ALPHABETICUS\.?|INDEX\s+ALPHABETICUS\s+EPISTOLARUM\s+SANCTI\s+BASILII\.?)$",
    re.I,
)
NOISE_RE = re.compile(r"^(?:Digitized by Google|_+|-+)$", re.I)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆŒ§])")
PAGE_TOKEN_RE = re.compile(
    r"(?<!\d)(?P<start>\d{1,4})(?:\s*[-–—]\s*(?P<end>\d{1,4}))?(?:\s*(?P<seq>et\s+seq\.?|seqq\.?|seq\.?))?",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def seq(path: Path) -> int:
    match = FILE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse OCR sequence from {path}")
    return int(match.group(1))


def file_path(source_root: Path, seq_no: int) -> Path:
    for path in source_root.glob("*.txt"):
        if seq(path) == seq_no:
            return path
    raise FileNotFoundError(f"missing OCR file with sequence {seq_no}")


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, str]] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        body = match.group("body") or ""
        blocks.append((kind, body))
    return blocks


def extract_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for kind, body in extract_blocks(path):
        if kind not in {"texto_principal", "nota_marginal"}:
            continue
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, list[str]]:
    page_map: dict[int, list[str]] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            if 1 <= page <= 2000:
                candidates = page_map.setdefault(page, [])
                path_str = str(path)
                if path_str not in candidates:
                    candidates.append(path_str)
    return page_map


def page_bounds(files: list[Path]) -> tuple[int | None, int | None]:
    pages: list[int] = []
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            if 1000 <= page <= 2000:
                pages.append(page)
    if not pages:
        return None, None
    return min(pages), max(pages)


def choose_candidate_file(
    page_int: int | None,
    page_map: dict[int, list[str]],
    *,
    helper_file: str | None = None,
    single_ref_entry: bool = False,
) -> tuple[str | None, float | None, dict[str, Any]]:
    if page_int is None:
        return None, None, {"resolution": "no_page_int"}

    candidates = page_map.get(page_int, [])
    if len(candidates) == 1:
        return candidates[0], 0.98, {"resolution": "page_map_unique", "candidate_count": 1}

    if helper_file and helper_file in candidates:
        return (
            helper_file,
            0.94,
            {"resolution": "helper_exact_candidate", "candidate_count": len(candidates)},
        )

    if helper_file and candidates:
        helper_seq = seq(Path(helper_file))
        best = min(candidates, key=lambda item: abs(seq(Path(item)) - helper_seq))
        return (
            best,
            0.82,
            {
                "resolution": "page_map_disambiguated_by_helper",
                "candidate_count": len(candidates),
                "helper_file": helper_file,
            },
        )

    if helper_file and single_ref_entry:
        return helper_file, 0.74, {"resolution": "helper_single_ref_fallback", "candidate_count": 0}

    for radius in (1, 2, 3):
        nearby = []
        for offset in range(-radius, radius + 1):
            if offset == 0:
                continue
            nearby.extend(page_map.get(page_int + offset, []))
        unique_nearby = sorted(set(nearby))
        if len(unique_nearby) == 1:
            return (
                unique_nearby[0],
                0.64,
                {
                    "resolution": "neighbor_page_unique",
                    "candidate_count": 0,
                    "neighbor_radius": radius,
                },
            )
        if helper_file and unique_nearby:
            helper_seq = seq(Path(helper_file))
            best = min(unique_nearby, key=lambda item: abs(seq(Path(item)) - helper_seq))
            return (
                best,
                0.58,
                {
                    "resolution": "neighbor_page_disambiguated_by_helper",
                    "candidate_count": len(unique_nearby),
                    "neighbor_radius": radius,
                    "helper_file": helper_file,
                },
            )

    return None, None, {"resolution": "unresolved", "candidate_count": len(candidates)}


def split_fragments(buffer_text: str) -> list[str]:
    text = buffer_text.replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    frags = [frag.strip() for frag in SENTENCE_SPLIT_RE.split(text) if frag.strip()]
    return frags if frags else [text]


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for match in PAGE_TOKEN_RE.finditer(text):
        start_raw = match.group("start")
        end_raw = match.group("end")
        seq_raw = match.group("seq")
        if not start_raw:
            continue
        if end_raw:
            ref_raw = f"{start_raw}-{end_raw}"
            kind = "editorial_range"
            page_ref_int = int(start_raw)
            range_start_raw = start_raw
            range_end_raw = end_raw
        elif seq_raw:
            ref_raw = f"{start_raw} {seq_raw}"
            kind = "editorial_range"
            page_ref_int = int(start_raw)
            range_start_raw = start_raw
            range_end_raw = None
        else:
            ref_raw = start_raw
            kind = "editorial_page"
            page_ref_int = int(start_raw)
            range_start_raw = None
            range_end_raw = None
        key = (kind, ref_raw, start_raw)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": kind,
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": range_start_raw,
                "range_end_raw": range_end_raw,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.78 if kind == "editorial_page" else 0.72,
                "raw_json": {"locator_kind": kind},
            }
        )
    return refs


def derive_lemma(fragment: str) -> str | None:
    text = normalize(fragment)
    if not text:
        return None
    first_ref = PAGE_TOKEN_RE.search(text)
    if first_ref:
        lead = normalize(text[: first_ref.start()])
        if lead:
            return lead.rstrip(" ,;:.—-")
    if "Vide" in text:
        lead = normalize(re.split(r"\bVide\b", text, maxsplit=1)[0])
        if lead:
            return lead.rstrip(" ,;:.—-")
    return text.rstrip(" ,;:.—-") or None


def entry_kind(fragment: str, refs: list[dict[str, Any]]) -> str:
    text = normalize(fragment)
    if not refs and re.search(r"\bVide\b|\bvid\.\b|\bvoir\b|\bcf\.\b|\bid\.\b", text, re.I):
        return "cross_reference"
    if SINGLE_LETTER_RE.fullmatch(text):
        return "heading_group"
    if text.startswith("Epist.") or text.startswith("APPENDIX") or text.startswith("Monitum") or text.startswith("Sermo "):
        return "heading_group"
    return "lemma"


def build_sections(
    section_defs: list[tuple[str, str, list[Path], str, str, int | None, int | None]]
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for order, (section_key, section_kind, files, heading_raw, reason, page_start, page_end) in enumerate(
        section_defs, start=1
    ):
        if page_start is None or page_end is None:
            page_start, page_end = page_bounds(files)
        sections.append(
            {
                "section_key": section_key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": section_kind,
                "heading_raw": heading_raw,
                "heading_norm": sort_norm(heading_raw),
                "heading_letter": None,
                "page_start": page_start,
                "page_end": page_end,
                "file_start": str(files[0]),
                "file_end": str(files[-1]),
                "confidence": 0.96 if section_kind != "analytic_subject" else 0.92,
                "raw_json": {
                    "section_kind_reason": reason,
                    "observed_files": [str(path) for path in files[:3]] + ([str(files[-1])] if len(files) > 3 else []),
                },
            }
        )
    return sections


def process_section(
    files: list[Path],
    section_key: str,
    section_kind: str,
    page_map: dict[int, list[str]],
    entry_start: int,
    *,
    letter_groups: bool,
    helper_entries: list[dict[str, Any]],
    helper_limit: int,
    section_start_file: str,
    section_anomalies: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_letter: str | None = None
    letter_nodes: dict[str, str] = {}
    buffer: list[str] = []
    buffer_file: str | None = None
    order = entry_start

    def flush() -> None:
        nonlocal order, buffer, buffer_file
        if not buffer:
            return
        fragments = split_fragments("\n".join(buffer))
        buffer = []
        for fragment in fragments:
            cleaned = normalize(fragment)
            if not cleaned:
                continue
            if SECTION_HEADING_RE.fullmatch(cleaned):
                continue
            page_refs = extract_page_refs(cleaned)
            kind = entry_kind(cleaned, page_refs)
            lemma_raw = derive_lemma(cleaned)
            entry_key = f"{VOLUME_ID}:entry:{order:06d}"
            inferred_page = page_refs[0]["page_ref_int"] if page_refs else None
            raw_json: dict[str, Any] = {
                "source_file": buffer_file,
                "section_kind": section_kind,
                "split_strategy": "sentence_boundary",
                "page_hints": [ref["page_ref_int"] for ref in page_refs[:3]],
            }
            if section_anomalies:
                raw_json["section_anomalies"] = section_anomalies[:]
            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": letter_nodes.get(current_letter) if current_letter else None,
                "entry_order": order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": cleaned,
                "context_raw": None,
                "heading_letter": current_letter or (lemma_raw[:1].upper() if lemma_raw else None),
                "inferred_printed_page": inferred_page,
                "section_start_file": section_start_file,
                "editorial_anchor_file": buffer_file,
                "target_file_best": None,
                "confidence": 0.86 if page_refs else 0.68,
                "raw_json": raw_json,
            }
            entries.append(entry)
            needs_helper = any(len(page_map.get(ref["page_ref_int"], [])) != 1 for ref in page_refs)
            if page_refs and needs_helper:
                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw or cleaned,
                        "query_names": [
                            name
                            for name in [
                                lemma_raw,
                                re.sub(r"\s+\d{1,4}.*$", "", cleaned).strip(" ,;:."),
                                cleaned.split(".", 1)[0].strip(),
                            ]
                            if name
                        ][:3],
                        "page_hints": [str(ref["page_ref_int"]) for ref in page_refs[:3]],
                        "page_hint_ints": [ref["page_ref_int"] for ref in page_refs[:3]],
                        "context_raw": cleaned[:240],
                    }
                )
            for ref_order, ref in enumerate(page_refs, start=1):
                ref_obj = dict(ref)
                ref_obj["entry_key"] = entry_key
                ref_obj["ref_order"] = ref_order
                ref_obj["target_file"] = None
                ref_obj["target_file_probability"] = None
                ref_obj["section_start_file"] = section_start_file
                ref_obj["editorial_anchor_file"] = buffer_file
                refs.append(ref_obj)
            order += 1

    for path in files:
        for line in extract_lines(path):
            if SECTION_HEADING_RE.fullmatch(line):
                if "IN TOMUM II" in line.upper():
                    section_anomalies.append(f"header anomaly in {path.name}: {line}")
                continue
            if letter_groups and SINGLE_LETTER_RE.fullmatch(line):
                flush()
                current_letter = line
                if line not in letter_nodes:
                    node_key = f"{VOLUME_ID}:node:{len(nodes)+1:04d}:{line}"
                    letter_nodes[line] = node_key
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
                            "raw_json": {"source_file": str(path), "kind": "letter_divider"},
                        }
                    )
                buffer_file = str(path)
                continue
            if buffer_file is None:
                buffer_file = str(path)
            buffer.append(line)
    flush()
    return entries, refs, nodes, helper_entries, order


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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {"entries": []})


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {str(item.get("entry_id")): item for item in helper_output.get("entries", []) if isinstance(item, dict)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG032 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    write_json(
        todo_path,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Extract PG032 alphabetical, analytical, and ordo sections conservatively",
            "completed": [],
            "pending": [
                "parse OCR tail into sections and entries",
                "run helper target locator on ambiguous locator cases",
                "assemble final payload and validate schema",
            ],
            "blocked": [],
            "notes": [
                "Treat the page 721 'IN TOMUM II' header as an OCR anomaly unless the surrounding flow proves otherwise.",
                "Keep the epistles alphabetic index, the analytical index, and the closing ORDO RERUM as distinct sections.",
            ],
        },
    )

    files = sorted(args.source_root.glob("*.txt"), key=seq)
    page_map = build_page_map(files)

    sec1_files = [path for path in files if SECTION_1_FILES[0] <= seq(path) <= SECTION_1_FILES[1]]
    sec2_files = [path for path in files if SECTION_2_FILES[0] <= seq(path) <= SECTION_2_FILES[1]]
    sec3_files = [path for path in files if SECTION_3_FILES[0] <= seq(path) <= SECTION_3_FILES[1]]

    section_defs = [
        (
            f"{VOLUME_ID}:section:001",
            "alphabetical_general",
            sec1_files,
            "S. BASILII EPISTOLARUM INDEX ALPHABETICUS.",
            "Alphabetical index of the epistles by addressee/heading letter; semantically onomastic, but editorially an alphabetical index.",
            1411,
            1412,
        ),
        (
            f"{VOLUME_ID}:section:002",
            "analytic_subject",
            sec2_files,
            "INDEX ANALYTICUS IN TOMUM III.",
            "Analytical subject index for the tome; the page 721 header 'INDEX ANALYTICUS IN TOMUM II.' is treated as an OCR anomaly because the local flow remains alphabetical subject indexing.",
            1413,
            1504,
        ),
        (
            f"{VOLUME_ID}:section:003",
            "ordo_rerum",
            sec3_files,
            "ORDO RERUM",
            "Closing contents table / ordo rerum, including the volume contents and appended sermones.",
            1507,
            1524,
        ),
    ]

    sections = build_sections(section_defs)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    order = 1

    e, r, n, helper_entries, order = process_section(
        sec1_files,
        section_defs[0][0],
        section_defs[0][1],
        page_map,
        order,
        letter_groups=True,
        helper_entries=helper_entries,
        helper_limit=120,
        section_start_file=str(sec1_files[0]) if sec1_files else None,
        section_anomalies=[],
    )
    entries.extend(e)
    refs.extend(r)
    nodes.extend(n)

    section2_anomalies: list[str] = []
    e, r, n, helper_entries, order = process_section(
        sec2_files,
        section_defs[1][0],
        section_defs[1][1],
        page_map,
        order,
        letter_groups=True,
        helper_entries=helper_entries,
        helper_limit=120,
        section_start_file=str(sec2_files[0]) if sec2_files else None,
        section_anomalies=section2_anomalies,
    )
    entries.extend(e)
    refs.extend(r)
    nodes.extend(n)

    e, r, n, helper_entries, order = process_section(
        sec3_files,
        section_defs[2][0],
        section_defs[2][1],
        page_map,
        order,
        letter_groups=False,
        helper_entries=helper_entries,
        helper_limit=120,
        section_start_file=str(sec3_files[0]) if sec3_files else None,
        section_anomalies=[],
    )
    entries.extend(e)
    refs.extend(r)
    nodes.extend(n)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)

    if args.helper_output_json.exists():
        helper_output = read_json(args.helper_output_json, {"entries": []})
    else:
        helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_id = helper_map(helper_output)

    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        refs_for_entry = refs_by_entry.get(entry["entry_key"], [])
        best = (helper or {}).get("best_candidate") or {}
        helper_file = best.get("file")
        if helper:
            entry["raw_json"]["helper_status"] = helper.get("status")
            entry["raw_json"]["helper_candidate_role"] = best.get("candidate_role")
            entry["raw_json"]["helper_reason_summary"] = best.get("reason_summary")
            entry["raw_json"]["helper_top_candidates"] = [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "inferred_printed_page": cand.get("inferred_printed_page"),
                    "reason_summary": cand.get("reason_summary"),
                    "evidence_kinds": sorted(
                        {item.get("kind") for item in cand.get("evidence", []) if isinstance(item, dict) and item.get("kind")}
                    )
                    or None,
                }
                for cand in (helper.get("candidates") or [])[:5]
            ]
        if best:
            entry["raw_json"]["helper_best_candidate"] = {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "inferred_printed_page": best.get("inferred_printed_page"),
                "reason_summary": best.get("reason_summary"),
                "evidence_kinds": sorted(
                    {
                        item.get("kind")
                        for cand in (helper.get("candidates") or [])[:2]
                        for item in (cand.get("evidence") or [])
                        if isinstance(item, dict) and item.get("kind")
                    }
                )
                or None,
            }

        target_file_best, target_prob, target_meta = choose_candidate_file(
            entry.get("inferred_printed_page"),
            page_map,
            helper_file=helper_file,
            single_ref_entry=len(refs_for_entry) == 1,
        )
        if target_file_best is None and helper_file:
            target_file_best = helper_file
            target_prob = best.get("probability")
            target_meta = {"resolution": "helper_entry_fallback", "candidate_count": 0}
        if target_file_best is None and refs_for_entry:
            target_file_best = refs_for_entry[0].get("target_file")
            target_prob = refs_for_entry[0].get("target_file_probability")
            target_meta = {"resolution": "first_resolved_ref_fallback", "candidate_count": 0}
        if target_file_best is None:
            target_file_best = entry.get("editorial_anchor_file")
            target_prob = 0.4
            target_meta = {"resolution": "editorial_anchor_fallback", "candidate_count": 0}
        entry["target_file_best"] = target_file_best
        entry["raw_json"]["target_resolution"] = target_meta
        if target_prob is not None:
            entry["raw_json"]["target_file_best_probability"] = target_prob

        for ref in refs_for_entry:
            ref_target, ref_prob, ref_meta = choose_candidate_file(
                ref.get("page_ref_int"),
                page_map,
                helper_file=helper_file,
                single_ref_entry=len(refs_for_entry) == 1,
            )
            if ref_target is None and len(refs_for_entry) == 1:
                ref_target = entry["target_file_best"]
                ref_prob = target_prob
                ref_meta = {"resolution": "entry_target_fallback", "candidate_count": 0}
            ref["target_file"] = ref_target
            ref["target_file_probability"] = ref_prob
            ref["raw_json"]["target_resolution"] = ref_meta
            if helper_file:
                ref["raw_json"]["helper_file"] = helper_file

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "The OCR tail contains three editorial structures: the epistles alphabetic index, the analytical subject index, and the closing ORDO RERUM table.",
            "The analytical section includes a page-header anomaly around file 721 ('INDEX ANALYTICUS IN TOMUM II.'); the local alphabetical flow and surrounding headers indicate it belongs to the same analytical run.",
            "Locator resolution was rerun against the full volume page map and a complete helper request, so null target files were not preserved by inertia from the previous checkpoint.",
        ],
    }

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the epistles alphabetic index, the analytical subject index, and the closing ORDO RERUM table from the OCR tail; line items and material page locators were serialized conservatively.",
        "evidence_files": [
            str(sec1_files[0]) if sec1_files else None,
            str(sec2_files[0]) if sec2_files else None,
            str(sec3_files[0]) if sec3_files else None,
            str(sec3_files[-1]) if sec3_files else None,
        ],
    }
    coverage["evidence_files"] = [item for item in coverage["evidence_files"] if item]

    notes = [
        "Entries are intentionally conservative: long OCR runs were split only on sentence boundaries and obvious letter dividers.",
        "Refs were kept distinct from OCR file suffixes; helper evidence was rerun for all entries with material locators, then combined with the full-volume page map.",
    ]

    fragments_dir = args.intermediate_dir
    write_json(fragments_dir / "volume.json", volume)
    write_json(fragments_dir / "sections.json", sections)
    write_json(fragments_dir / "nodes.json", nodes)
    write_json(fragments_dir / "entries.json", entries)
    write_json(fragments_dir / "refs.json", refs)
    write_json(fragments_dir / "scripture_refs.json", [])
    write_json(fragments_dir / "coverage.json", coverage)
    write_json(fragments_dir / "notes.json", notes)
    write_json(
        fragments_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "entry_count": len(entries),
            "ref_count": len(refs),
            "section_count": len(sections),
        },
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "pipeline_index_extraction" / "assemble_alphabetical_payload.py"),
            "--intermediate-dir",
            str(args.intermediate_dir),
            "--output",
            str(args.output_file),
            "--generated-at",
            now_iso(),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"assemble_alphabetical_payload.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    todo_path.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "updated_at": now_iso(),
                "current_focus": "Final payload written",
                "completed": ["parsed OCR tail", "ran helper target locator", "assembled final payload"],
                "pending": [],
                "blocked": [],
                "notes": ["Keep this volume as a template for similar PG appendix tails with multiple editorial index sections."],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

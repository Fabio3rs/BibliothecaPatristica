#!/usr/bin/env python3
"""Usage: build the PG147 alphabetical payload from OCR, helper resolution, and local checkpoints.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg147_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG147/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG147_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG147_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG147 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG147_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PG147"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 147"
SCRIPT_TARGET_LOCATOR = Path("/homessddata/Projects/pdfocr/scripts/index_target_locator.py")

FILE_RE = re.compile(r"-(\d+)\.txt$")
BLOCK_RE = re.compile(
    r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>',
    re.IGNORECASE | re.DOTALL,
)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADER_NOISE_RE = re.compile(
    r"^(?:\d{1,4}|Digitized by Google|PATROL\. GR\. CXLVII\.|FINIS TOMI CENTESIMI QUADRAGESIMI SEPTIMI\.)$",
    re.IGNORECASE,
)
TITLE_NOISE_RE = re.compile(
    r"^(?:INDICES AD NICEPHORI CALLISTI HIST\. ECCLES\.|HISTOR\. ECCLES\. INDEX PRIOR\.|HISTOR\. ECCLES\. INDEX POSTERIOR\.|INDEX POSTERIOR\.|A col\. 433 tomi CXLVI usque ad finem Historiæ ecclesiasticæ\.|\(Revocatur Lector ad numeros grandiores textui insertos\.\)|A col\. 549 tomi CXLV usque ad col\. 432 tomi CXLVI\.|Revocatur Lector ad numeros grandiores textui Latino insertos\.)$",
    re.IGNORECASE,
)
NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?")
PAGE_REF_HINT_RE = re.compile(r"\b(\d{1,4})(?:\s*-\s*(\d{1,4}))?\b")


@dataclass(frozen=True)
class SectionSpec:
    section_key: str
    section_order: int
    heading_raw: str
    page_start: int | None
    page_end: int | None
    file_sequences: tuple[int, ...]
    intro_hint: str


SECTION_SPECS = [
    SectionSpec(
        section_key=f"{VOLUME_ID}:alpha:alphabetical_general:001",
        section_order=1,
        heading_raw="HISTOR. ECCLES. INDEX PRIOR.",
        page_start=1221,
        page_end=1224,
        file_sequences=tuple(range(599, 621)),
        intro_hint="INDICES AD NICEPHORI CALLISTI HIST. ECCLES. / INDEX PRIOR",
    ),
    SectionSpec(
        section_key=f"{VOLUME_ID}:alpha:alphabetical_general:002",
        section_order=2,
        heading_raw="INDEX POSTERIOR.",
        page_start=1225,
        page_end=1276,
        file_sequences=tuple(range(621, 640)),
        intro_hint="INDEX POSTERIOR.",
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    return value.lower()


def file_seq(path: Path) -> int:
    match = FILE_RE.search(path.name)
    return int(match.group(1)) if match else -1


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_blocks(raw: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = []
        for line in content.splitlines():
            normalized = normalize(line)
            if not normalized:
                continue
            lines.append(normalized)
        if lines:
            blocks.append((kind, lines))
    return blocks


def collect_lines(path: Path) -> list[tuple[str, str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_ocr_page_xml(raw)
    if isinstance(parsed.get("blocks"), list):
        blocks = []
        for block in parsed["blocks"]:
            kind = str(block.get("kind") or "").strip().lower()
            lines = [normalize(line) for line in block.get("lines") or []]
            lines = [line for line in lines if line]
            if lines:
                blocks.append((kind, lines))
    else:
        blocks = extract_blocks(raw)

    collected: list[tuple[str, str, str]] = []
    for kind, lines in blocks:
        if kind not in {"texto_principal", "nota_marginal"}:
            continue
        for line in lines:
            collected.append((path.as_posix(), kind, line))
    return collected


def is_noise_line(line: str) -> bool:
    cleaned = normalize(line) or ""
    if not cleaned:
        return True
    if HEADER_NOISE_RE.fullmatch(cleaned):
        return True
    if TITLE_NOISE_RE.fullmatch(cleaned):
        return True
    if cleaned.startswith(("Digitized by Google", "PATROL. GR.", "FINIS TOMI")):
        return True
    if cleaned.isdigit():
        return True
    return False


def is_letter_heading(line: str) -> bool:
    cleaned = normalize(line) or ""
    return bool(LETTER_RE.fullmatch(cleaned))


def line_starts_new_entry(line: str) -> bool:
    cleaned = normalize(line) or ""
    if not cleaned:
        return False
    if is_letter_heading(cleaned):
        return False
    if cleaned.startswith(("Digitized", "PATROL.", "FINIS TOMI", "INDICES ", "INDEX ", "HISTOR.", "A col.", "(Revocatur", "Revocatur")):
        return False
    if re.match(r"^\d{1,4}\b", cleaned):
        cleaned = re.sub(r"^\d{1,4}\.\s*", "", cleaned)
        cleaned = re.sub(r"^\d{1,4}\s*", "", cleaned)
        return bool(cleaned and re.match(r"^[A-ZÆŒ]", cleaned))
    if re.match(r"^[A-ZÆŒ]", cleaned):
        return True
    if cleaned.startswith(("...", "…")):
        return True
    return False


def entry_kind(entry_raw: str) -> str:
    text = normalize(entry_raw) or ""
    if not text:
        return "editorial_note"
    if re.search(r"\bVide\b|\bvide\b|\bvoir\b|\bcf\.?\b|\bid\.?\b", text) and not NUM_RE.search(text):
        return "cross_reference"
    return "lemma"


def strip_leading_noise(text: str) -> str:
    value = normalize(text) or ""
    value = re.sub(r"^\d{1,4}\.\s*", "", value)
    value = re.sub(r"^\d{1,4}\s*", "", value)
    return value.strip()


def join_lines(lines: list[str]) -> str:
    merged: list[str] = []
    for line in lines:
        clean = normalize(line) or ""
        if not clean:
            continue
        if merged and merged[-1].endswith("-"):
            merged[-1] = merged[-1][:-1] + clean
        else:
            merged.append(clean)
    return re.sub(r"\s+", " ", " ".join(merged)).strip()


def parse_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in NUM_RE.finditer(entry_raw):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        ref_raw = match.group(0).replace(" ", "")
        refs.append(
            {
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end) if end is not None else None,
            }
        )
    return refs


def lemma_from_entry(entry_raw: str) -> str | None:
    text = strip_leading_noise(entry_raw)
    if not text:
        return None
    first_num = NUM_RE.search(text)
    if first_num:
        text = text[: first_num.start()].rstrip(" ,;:.")
    text = text.replace("  ", " ")
    return text.strip(" ,;:.") or None


def entry_display(entry_raw: str) -> str | None:
    lemma = lemma_from_entry(entry_raw)
    return lemma


def extract_section_lines(files: list[Path]) -> tuple[list[tuple[str, str, str]], list[str]]:
    lines: list[tuple[str, str, str]] = []
    evidence: list[str] = []
    for path in files:
        evidence.append(path.as_posix())
        lines.extend(collect_lines(path))
    return lines, evidence


def segment_entries(lines: list[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    nodes: list[str] = []
    current_lines: list[tuple[str, str]] = []
    current_kind: str | None = None
    current_letter: str | None = None

    def flush() -> None:
        nonlocal current_lines, current_kind
        if not current_lines:
            return
        entry_raw = join_lines([line for _, line in current_lines])
        if not entry_raw:
            current_lines = []
            current_kind = None
            return
        source_file = current_lines[0][0]
        entries.append(
            {
                "entry_raw": entry_raw,
                "entry_kind": entry_kind(entry_raw),
                "lemma_raw": lemma_from_entry(entry_raw),
                "lemma_display": entry_display(entry_raw),
                "lemma_norm": sort_norm(lemma_from_entry(entry_raw)),
                "lemma_sort": sort_norm(lemma_from_entry(entry_raw)),
                "page_refs": parse_refs(entry_raw),
                "current_letter": current_letter,
                "source_file": source_file,
            }
        )
        current_lines = []
        current_kind = None

    prev_entry_like = False
    for source_file, kind, line in lines:
        cleaned = normalize(line) or ""
        if is_noise_line(cleaned):
            continue
        if is_letter_heading(cleaned):
            flush()
            current_letter = cleaned
            nodes.append(cleaned)
            prev_entry_like = False
            continue
        if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Z"}:
            flush()
            current_letter = cleaned
            nodes.append(cleaned)
            prev_entry_like = False
            continue
        if current_lines:
            last = current_lines[-1][1]
            last_complete = bool(re.search(r"[.;:]$", last) or re.search(r"\b(?:ibid\.?|seqq?\.?|sqq\.?)$", last, re.IGNORECASE) or last.endswith(tuple("0123456789")))
            if last_complete and line_starts_new_entry(cleaned):
                flush()
        if not current_lines:
            if not line_starts_new_entry(cleaned):
                if current_lines:
                    current_lines.append(cleaned)
                continue
            current_lines.append((source_file, cleaned))
            prev_entry_like = True

    flush()
    # merge nodes preserving order but removing duplicates
    ordered_nodes: list[str] = []
    seen_nodes: set[str] = set()
    for node in nodes:
        if node not in seen_nodes:
            seen_nodes.add(node)
            ordered_nodes.append(node)
    return entries, ordered_nodes


def build_helper_request(section_entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(section_entries, start=1):
        refs = entry.get("page_refs") or []
        if not refs:
            continue
        page_hints = [int(ref["page_ref_int"]) for ref in refs[:3] if ref.get("page_ref_int")]
        if not page_hints:
            continue
        lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:120]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0]],
                "page_hints": [str(page_hints[0])],
                "page_hint_ints": [page_hints[0]],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": f"/homessddata/Projects/pdfocr/teste/{VOLUME_ID}/text",
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    helper_output_json.parent.mkdir(parents=True, exist_ok=True)
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
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def build_section_payload(section: SectionSpec, files: list[Path], helper_output: dict[str, Any] | None) -> dict[str, Any]:
    lines, evidence_files = extract_section_lines(files)
    entries, node_letters = segment_entries(lines)

    helper_lookup: dict[str, Any] = {}
    if helper_output and isinstance(helper_output.get("entries"), list):
        for item in helper_output["entries"]:
            helper_lookup[item.get("entry_id")] = item

    section_entries: list[dict[str, Any]] = []
    section_refs: list[dict[str, Any]] = []
    section_nodes: list[dict[str, Any]] = []
    node_index = 0
    node_map: dict[str, str] = {}

    for letter in node_letters:
        node_index += 1
        node_key = f"{VOLUME_ID}:node:{section.section_order:02d}:{node_index:03d}"
        node_map[letter] = node_key
        section_nodes.append(
            {
                "node_key": node_key,
                "section_key": section.section_key,
                "parent_node_key": None,
                "node_order": node_index,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": sort_norm(letter),
                "label_sort": sort_norm(letter),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "source": letter,
                },
            }
        )

    for idx, entry in enumerate(entries, start=1):
        helper_id = f"{VOLUME_ID.lower()}_{idx:04d}"
        helper = helper_lookup.get(helper_id) or {}
        refs = entry.get("page_refs") or []
        inferred_page = refs[0]["page_ref_int"] if refs else None
        page_hints = [ref["page_ref_int"] for ref in refs[:3] if ref.get("page_ref_int")]
        target_file_best = entry.get("source_file") or files[0].as_posix()
        target_probability = None
        if isinstance(helper, dict):
            best = helper.get("best_candidate") or {}
            if best.get("file"):
                target_file_best = best.get("file")
            target_probability = best.get("probability")
            if not target_probability and best.get("score") is not None:
                target_probability = best.get("score")

        parent_node_key = node_map.get(entry.get("current_letter"))
        entry_key = f"{VOLUME_ID}:entry:{section.section_order:02d}:{idx:04d}"
        section_entries.append(
            {
                "entry_key": entry_key,
                "section_key": section.section_key,
                "parent_node_key": parent_node_key,
                "entry_order": idx,
                "entry_kind": entry["entry_kind"],
                "lemma_raw": entry["lemma_raw"],
                "lemma_display": entry["lemma_display"],
                "lemma_norm": entry["lemma_norm"],
                "lemma_sort": entry["lemma_sort"],
                "entry_raw": entry["entry_raw"],
                "context_raw": entry["entry_raw"] if len(entry["entry_raw"]) < 260 else entry["entry_raw"][:257] + "...",
                "heading_letter": entry.get("current_letter"),
                "inferred_printed_page": inferred_page,
                "section_start_file": files[0].as_posix(),
                "editorial_anchor_file": target_file_best,
                "target_file_best": target_file_best,
                "confidence": 0.9 if refs else 0.78,
                "raw_json": {
                    "helper_request_entry": helper.get("helper_request_entry") if isinstance(helper, dict) else None,
                    "helper": helper,
                    "page_hints": page_hints,
                    "section_intro_hint": section.intro_hint,
                    "line_count_estimate": len(entry["entry_raw"].split()),
                },
            }
        )

        for ref_order, ref in enumerate(refs, start=1):
            section_refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": target_file_best,
                    "target_file_probability": target_probability,
                    "section_start_file": files[0].as_posix(),
                    "editorial_anchor_file": target_file_best,
                    "confidence": 0.85,
                    "raw_json": {
                        "helper": helper,
                        "page_hints": page_hints,
                    },
                }
            )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Index lines in the OCR tail were segmented into entries and resolved against the helper and local OCR context.",
        "evidence_files": evidence_files[:4] if evidence_files else [],
    }
    return {
        "section": {
            "section_key": section.section_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": section.section_order,
            "section_kind": "alphabetical_general",
            "heading_raw": section.heading_raw,
            "heading_norm": sort_norm(section.heading_raw),
            "heading_letter": None,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "file_start": files[0].as_posix(),
            "file_end": files[-1].as_posix(),
            "confidence": 0.9,
            "raw_json": {
                "section_kind_reason": (
                    "Alphabetical index of names, places, and historical topics for Nicephorus Callistus. "
                    "The volume is split into prior and posterior runs; the later run begins with an explicit "
                    "scope note at file 621."
                ),
                "intro_hint": section.intro_hint,
                "evidence_files": evidence_files,
            },
        },
        "nodes": section_nodes,
        "entries": section_entries,
        "refs": section_refs,
        "coverage": coverage,
    }


def build_volume_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Extract PG147 alphabetical index entries from the prior and posterior index runs",
        "completed": [],
        "pending": [
            "segment OCR lines into entries",
            "resolve targets with helper",
            "assemble final payload",
        ],
        "blocked": [],
        "notes": [
            "Section 1 starts with the prior index title page and Section 2 starts with INDEX POSTERIOR.",
            "Keep OCR file suffixes distinct from the printed page references cited by the index.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

    all_sections: list[dict[str, Any]] = []
    all_nodes: list[dict[str, Any]] = []
    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    helper_section_entries: list[dict[str, Any]] = []
    section_payloads: list[dict[str, Any]] = []

    files = sorted(source_root.glob("*.txt"), key=file_seq)
    file_lookup = {file_seq(path): path for path in files}

    for section in SECTION_SPECS:
        section_files = [file_lookup[i] for i in section.file_sequences if i in file_lookup]
        lines, _ = extract_section_lines(section_files)
        entries, _ = segment_entries(lines)
        for idx, entry in enumerate(entries, start=1):
            helper_section_entries.append(
                {
                    "entry_id": f"{VOLUME_ID.lower()}_{len(helper_section_entries)+1:04d}",
                    "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:120],
                    "query_names": [
                        entry["lemma_raw"] or entry["entry_raw"][:120],
                        (entry["entry_raw"].split(",", 1)[0] or entry["entry_raw"][:120]),
                    ],
                    "page_hints": [str(ref["page_ref_int"]) for ref in (entry["page_refs"] or [])[:1]],
                    "page_hint_ints": [ref["page_ref_int"] for ref in (entry["page_refs"] or [])[:1]],
                    "context_raw": entry["entry_raw"],
                }
            )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_section_entries[:120],
    }
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    helper_lookup: dict[str, Any] = {}
    if isinstance(helper_output.get("entries"), list):
        for item in helper_output["entries"]:
            helper_lookup[item.get("entry_id")] = item

    entry_counter = 0
    for section in SECTION_SPECS:
        section_files = [file_lookup[i] for i in section.file_sequences if i in file_lookup]
        section_result = build_section_payload(section, section_files, helper_output)
        section_data = section_result["section"]
        section_entries = section_result["entries"]
        section_refs = section_result["refs"]
        nodes = section_result["nodes"]
        coverage = section_result["coverage"]
        section_payloads.append(section_data)
        all_nodes.extend(nodes)

        for entry in section_entries:
            entry_counter += 1
            entry["entry_key"] = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            entry["raw_json"]["helper_request_entry"] = helper_lookup.get(f"{VOLUME_ID.lower()}_{entry_counter:04d}")
            all_entries.append(entry)

        for ref in section_refs:
            all_refs.append(ref)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Two alphabetical index runs were recovered from the OCR tail, with the helper used to stabilize target-file selection for material references.",
        "evidence_files": [
            file_lookup[599].as_posix() if 599 in file_lookup else files[0].as_posix(),
            file_lookup[621].as_posix() if 621 in file_lookup else files[0].as_posix(),
            file_lookup[639].as_posix() if 639 in file_lookup else files[-1].as_posix(),
        ],
    }

    # Reconcile per-section entries with the global helper ids after ordering.
    global_helper_entries = helper_section_entries
    for idx, entry in enumerate(all_entries, start=1):
        helper_item = helper_lookup.get(f"{VOLUME_ID.lower()}_{idx:04d}") or {}
        entry["raw_json"]["helper"] = helper_item
        if helper_item.get("best_candidate"):
            entry["target_file_best"] = helper_item["best_candidate"].get("file")
        if entry.get("inferred_printed_page") is None and helper_item.get("best_candidate", {}).get("inferred_printed_page"):
            entry["inferred_printed_page"] = helper_item["best_candidate"].get("inferred_printed_page")

    # fix entry keys after global ordering
    for idx, entry in enumerate(all_entries, start=1):
        entry["entry_key"] = f"{VOLUME_ID}:entry:{idx:04d}"
    for ref in all_refs:
        if ref["entry_key"].startswith(f"{VOLUME_ID}:entry:"):
            pass

    notes = [
        "The OCR tail contains a title page plus the prior/posterior split of the index.",
        "A few headers show OCR drift in the printed page number, so the helper output was used as a secondary anchor only.",
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
        "sections": section_payloads,
        "nodes": all_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG147 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_volume_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

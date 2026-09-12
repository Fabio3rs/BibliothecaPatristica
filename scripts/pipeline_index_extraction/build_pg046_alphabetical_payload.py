#!/usr/bin/env python3
"""Usage: build the PG046 alphabetical/analytical index payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg046_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG046/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG046_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG046_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG046 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG046_alphabetical_indices.json
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


VOLUME_ID = "PG046"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 46"

BLOCK_RE = re.compile(
    r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>',
    re.IGNORECASE | re.DOTALL,
)
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
TOKEN_RE = re.compile(
    r"\bibid\.?|\d{1,4}(?:,\s*[A-D]){0,3}(?:\s*(?:et\s+seqq?\.?|et\s+seq\.?|seqq\.|sqq\.))?",
    re.IGNORECASE,
)
NOISE_LINES = {
    "Digitized by Google",
}


@dataclass(frozen=True)
class SectionSpec:
    section_key: str
    section_kind: str
    heading_raw: str
    page_start: int | None
    page_end: int | None
    file_sequences: tuple[int, ...]
    section_order: int
    section_kind_reason: str


SECTION_SPECS = [
    SectionSpec(
        section_key="PG046:alpha:analytic_subject:001",
        section_kind="analytic_subject",
        heading_raw="INDEX ANALYTICUS / RERUM ET VERBORUM NOTABILIUM",
        page_start=1240,
        page_end=1268,
        file_sequences=tuple(range(628, 640)),
        section_order=1,
        section_kind_reason=(
            "Analytical subject index for Gregory of Nyssa's works, with alphabetical letter-group headings "
            "and dense page/column locators. The nearby ORDO RERUM and ORDO NOVUS tables are separate editorial material."
        ),
    ),
    SectionSpec(
        section_key="PG046:alpha:crosswalk_index:002",
        section_kind="crosswalk_index",
        heading_raw="ORDO NOVUS CUM VETERI COLLATUS",
        page_start=1269,
        page_end=1272,
        file_sequences=(641, 642),
        section_order=2,
        section_kind_reason=(
            "Parallel comparison table between the newer edition and the Morellian order. "
            "It is a crosswalk/parallel-locator block rather than an alphabetical subject index."
        ),
    ),
    SectionSpec(
        section_key="PG046:alpha:ordo_rerum:003",
        section_kind="ordo_rerum",
        heading_raw="ORDO RERUM",
        page_start=1273,
        page_end=1276,
        file_sequences=(640,),
        section_order=3,
        section_kind_reason=(
            "Editorial contents table (ordo rerum) at the end of the volume; it is not an alphabetical subject index."
        ),
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def leading_letter(text: str | None) -> str | None:
    value = normalize(text) or ""
    if not value:
        return None
    match = re.match(r"^([A-ZÆŒΑ-Ω])", value)
    if match:
        return match.group(1)
    return None


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
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def collect_lines(section_files: list[Path]) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for path in section_files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        blocks = parsed["blocks"] if isinstance(parsed.get("blocks"), list) else extract_blocks(path)
        for block in blocks:
            if block["kind"] not in {"cabecalho", "texto_principal", "outro"}:
                continue
            for line in block["lines"]:
                collected.append((str(path), line))
    return collected


def is_section_title(line: str) -> bool:
    cleaned = normalize(line) or ""
    upper = cleaned.upper()
    return bool(
        upper.startswith("INDEX ANALYTICUS")
        or upper.startswith("RERUM ET VERBORUM NOTABILIUM")
        or upper.startswith("ORDO RERUM")
        or upper.startswith("ORDO NOVUS CUM VETERI COLLATUS")
        or upper.startswith("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR")
        or upper.startswith("ORDO RERUM QUAE IN HOC TOMO CONTINENTUR")
        or upper.startswith("EDITIONIS NOVÆ")
        or upper.startswith("EDITIONIS NOVAE")
        or upper.startswith("EDITIONIS MORELLIANÆ")
        or upper.startswith("EDITIONIS MORELLIANAE")
        or upper.startswith("FINIS TOMI")
        or upper.startswith("PARISIIS.")
        or upper.startswith("TOM. I. PAG.")
        or upper.startswith("TOM. II.")
        or upper.startswith("TOM. III.")
    )


def is_heading_letter(line: str) -> bool:
    cleaned = normalize(line) or ""
    return bool(LETTER_RE.fullmatch(cleaned))


def looks_like_continuation(line: str) -> bool:
    cleaned = normalize(line) or ""
    if not cleaned:
        return False
    if cleaned.startswith(("—", "-", "·")):
        return True
    if cleaned[:1].islower():
        return True
    if re.match(r"^[A-Z]\.\s+[A-ZÆŒ]", cleaned):
        return True
    if re.match(r"^[IVXLCDM]{1,4},\s*\d", cleaned):
        return True
    if cleaned.startswith(("ibid.", "ibid,", "ibid;")):
        return True
    if re.match(r"^\d", cleaned):
        return True
    return False


def entry_kind_for(section_kind: str, entry_raw: str) -> str:
    raw = normalize(entry_raw) or ""
    if not raw:
        return "editorial_note"
    if section_kind in {"crosswalk_index", "ordo_rerum"}:
        if raw.startswith(("CAP.", "I.", "II.", "III.", "IV.", "V.", "VI.", "VII.", "VIII.", "IX.", "X.", "XI.", "XII.", "XIII.", "XIV.", "XV.", "XVI.", "XVII.", "XVIII.", "XIX.", "XX.", "XXI.", "XXII.")):
            return "heading_group"
        if raw.upper().startswith(("TOM.", "EDITIONIS", "ORDO RERUM", "ORDO NOVUS")):
            return "editorial_note"
    if re.search(r"\bVide\b", raw, re.IGNORECASE) and not re.search(r"\d{1,4}", raw):
        return "cross_reference"
    return "lemma"


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    first_match = TOKEN_RE.search(text)
    if first_match:
        text = text[: first_match.start()].strip()
    text = re.sub(r",\s*[IVXLCDM]{1,4},\s*$", "", text)
    text = re.sub(r",\s*[IVXLCDM]{1,4}\s*$", "", text)
    text = text.strip(" .;:")
    text = re.sub(r"\s+", " ", text)
    return text or None


def parse_refs(entry_raw: str, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last: int | None = None
    inferred_page: int | None = None
    for match in TOKEN_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if raw.lower().startswith("ibid"):
            if current_last is None:
                continue
            page_int = current_last
            page_ref_raw = "ibid."
            page_ref_col = None
            ref_kind = "editorial_page"
            page_hint = current_last
        else:
            page_match = re.search(r"\d{1,4}", raw)
            if not page_match:
                continue
            page_int = int(page_match.group(0))
            current_last = page_int
            page_hint = page_int
            if inferred_page is None:
                inferred_page = page_int
            cols = re.findall(r"\b([A-D])\b", raw)
            page_ref_col = ", ".join(cols) if cols else None
            page_ref_raw = page_match.group(0)
            ref_kind = "editorial_page_column" if page_ref_col else "editorial_page"
        target_file = page_map.get(page_int)
        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": page_ref_col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": 0.99 if target_file else None,
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.91 if page_int is not None else 0.68,
                "raw_json": {
                    "page_token_kind": "ibid" if raw.lower().startswith("ibid") else "page",
                },
            }
        )
    return refs, inferred_page


def helper_entry_id(volume_id: str, section_order: int, entry_order: int) -> str:
    return f"{volume_id.lower()}_{section_order:02d}_{entry_order:05d}"


def section_files_for(volume_root: Path, seqs: tuple[int, ...]) -> list[Path]:
    files = discover_text_files(volume_root)
    wanted = set(seqs)
    return [path for path in files if file_seq(path) in wanted]


def build_todo(volume_id: str, intermediate_dir: Path, focus: str) -> None:
    todo = {
        "volume_id": volume_id,
        "updated_at": now_iso(),
        "current_focus": focus,
        "completed": [],
        "pending": [],
        "blocked": [],
        "notes": [],
    }
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    (intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_section_payload(
    spec: SectionSpec,
    section_files: list[Path],
    page_map: dict[int, str],
    source_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_node_key: str | None = None
    current_letter_label: str | None = None
    current_source_file: str | None = None
    current_buffer: list[str] = []
    node_order = 0
    entry_order = 0
    started = spec.section_kind != "analytic_subject"

    def ensure_letter_node(letter: str, source_file: str | None, synthetic: bool = False) -> None:
        nonlocal node_order, current_node_key, current_letter_label
        if current_letter_label == letter:
            return
        node_order += 1
        current_node_key = f"{VOLUME_ID}:node:{spec.section_order:02d}:{node_order:03d}"
        current_letter_label = letter
        nodes.append(
            {
                "node_key": current_node_key,
                "section_key": spec.section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.95 if not synthetic else 0.86,
                "raw_json": {
                    "source_file": source_file,
                    "synthetic": synthetic,
                },
            }
        )

    def flush_buffer() -> None:
        nonlocal current_buffer, entry_order
        if not current_buffer:
            return
        entry_raw = normalize(" ".join(current_buffer)) or ""
        current_buffer = []
        if not entry_raw:
            return
        if not re.search(r"\d{1,4}|\bibid\.?\b|\bVide\b", entry_raw, re.IGNORECASE):
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{spec.section_order:02d}:{entry_order:06d}"
        entry_refs, inferred_page = parse_refs(entry_raw, page_map)
        lemma_raw = lemma_from_entry(entry_raw)
        entry_payload = {
            "entry_key": entry_key,
            "section_key": spec.section_key,
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind_for(spec.section_kind, entry_raw),
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter_label,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(section_files[0]) if section_files else None,
            "editorial_anchor_file": current_source_file,
            "target_file_best": current_source_file,
            "confidence": 0.92 if entry_refs else 0.74,
            "raw_json": {
                "source_file": current_source_file,
                "page_hints": [ref["page_ref_int"] for ref in entry_refs if ref.get("page_ref_int") is not None],
                "fragmentary_start": False,
            },
        }
        if lemma_raw is None:
            entry_payload["raw_json"]["lemma_recovery"] = "not_recovered"
        entries.append(entry_payload)
        for ref in entry_refs:
            ref = dict(ref)
            ref["entry_key"] = entry_key
            ref["section_start_file"] = str(section_files[0]) if section_files else None
            ref["editorial_anchor_file"] = current_source_file
            refs.append(ref)
        helper_entries.append(
            {
                "entry_id": helper_entry_id(VOLUME_ID, spec.section_order, entry_order),
                "lemma_raw": lemma_raw or entry_raw,
                "query_names": [q for q in {lemma_raw or entry_raw, entry_raw} if q],
                "page_hints": [str(p) for p in entry_payload["raw_json"]["page_hints"]],
                "page_hint_ints": [p for p in entry_payload["raw_json"]["page_hints"]],
                "context_raw": entry_raw,
            }
        )

    for source_file, line in collect_lines(section_files):
        current_source_file = source_file
        cleaned = normalize(line) or ""
        if not cleaned or cleaned in NOISE_LINES:
            continue
        if is_section_title(cleaned):
            flush_buffer()
            continue
        if spec.section_kind == "analytic_subject" and is_heading_letter(cleaned):
            flush_buffer()
            ensure_letter_node(cleaned, source_file, synthetic=False)
            started = True
            continue
        if spec.section_kind == "analytic_subject" and not started:
            if re.match(r"^[A-ZÆŒ]", cleaned):
                started = True
                if not current_node_key:
                    ensure_letter_node(cleaned[:1], source_file, synthetic=True)
            else:
                continue
        if current_buffer and looks_like_continuation(cleaned):
            current_buffer.append(cleaned)
            continue
        if current_buffer:
            flush_buffer()
        if re.search(r"\d{1,4}|\bibid\.?\b|\bVide\b", cleaned, re.IGNORECASE):
            if spec.section_kind == "analytic_subject" and not current_node_key and re.match(r"^[A-ZÆŒ]", cleaned):
                ensure_letter_node(cleaned[:1], source_file, synthetic=True)
            current_buffer = [cleaned]
    flush_buffer()

    if spec.section_kind == "analytic_subject" and not nodes and entries:
        # Ensure the alphabetical index has a stable synthetic opening letter if the OCR omits the first divider.
        first_letter = (entries[0]["lemma_raw"] or entries[0]["entry_raw"] or "A")[:1].upper()
        ensure_letter_node(first_letter, entries[0]["editorial_anchor_file"], synthetic=True)
        for entry in entries:
            entry["parent_node_key"] = current_node_key
            entry["heading_letter"] = current_letter_label
    if spec.section_kind == "analytic_subject" and nodes:
        rebuilt_nodes: list[dict[str, Any]] = []
        letter_nodes: dict[str, str] = {}
        node_order = 0
        for entry in entries:
            letter = leading_letter(entry.get("lemma_raw") or entry.get("entry_raw"))
            if letter:
                entry["heading_letter"] = letter
                if letter not in letter_nodes:
                    node_order += 1
                    node_key = f"{VOLUME_ID}:node:{spec.section_order:02d}:{node_order:03d}"
                    letter_nodes[letter] = node_key
                    rebuilt_nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": spec.section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": letter,
                            "label_norm": letter.lower(),
                            "label_sort": letter.lower(),
                            "node_level": 1,
                            "confidence": 0.95,
                            "raw_json": {
                                "source_file": entry.get("editorial_anchor_file"),
                                "synthetic": False,
                            },
                        }
                    )
                entry["parent_node_key"] = letter_nodes[letter]
        if rebuilt_nodes:
            nodes = rebuilt_nodes

    section = {
        "section_key": spec.section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": spec.section_order,
        "section_kind": spec.section_kind,
        "heading_raw": spec.heading_raw,
        "heading_norm": sort_norm(spec.heading_raw),
        "heading_letter": None,
        "page_start": spec.page_start,
        "page_end": spec.page_end,
        "file_start": str(section_files[0]) if section_files else None,
        "file_end": str(section_files[-1]) if section_files else None,
        "confidence": 0.97 if spec.section_kind == "analytic_subject" else 0.92,
        "raw_json": {
            "section_kind_reason": spec.section_kind_reason,
            "evidence_files": [str(path) for path in section_files],
        },
    }
    return nodes, entries, refs, helper_entries, section


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
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG046 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    build_todo(VOLUME_ID, args.intermediate_dir, "Extract PG046 analytical index plus editorial closures")
    all_files = discover_text_files(args.source_root)
    page_map = build_page_map(all_files)

    sections: list[dict[str, Any]] = []
    all_nodes: list[dict[str, Any]] = []
    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    helper_request_entries: list[dict[str, Any]] = []

    for spec in SECTION_SPECS:
        section_files = section_files_for(args.source_root, spec.file_sequences)
        if not section_files:
            continue
        nodes, entries, refs, helper_entries, section = build_section_payload(spec, section_files, page_map, args.source_root)
        sections.append(section)
        all_nodes.extend(nodes)
        all_entries.extend(entries)
        all_refs.extend(refs)
        helper_request_entries.extend(helper_entries)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    args.helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    args.helper_request_json.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_lookup = {str(item.get("entry_id")): item for item in (helper_output.get("entries") or [])}

    for entry in all_entries:
        entry_id = helper_entry_id(VOLUME_ID, int(entry["section_key"].rsplit(":", 1)[-1]), int(entry["entry_order"]))
        helper_item = helper_lookup.get(entry_id)
        entry["raw_json"]["helper_entry_id"] = entry_id
        entry["raw_json"]["helper_request_entry"] = next(
            (item for item in helper_request_entries if item["entry_id"] == entry_id),
            None,
        )
        if helper_item:
            entry["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }
            best = (helper_item.get("top_candidates") or [{}])[0]
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                entry["raw_json"]["helper_best_file"] = best["file"]
                entry["raw_json"]["helper_best_probability"] = best.get("probability")
        else:
            entry["raw_json"]["helper_locator"] = {
                "status": "unavailable",
                "candidate_role": None,
                "reason_summary": "helper did not return a matching entry_id",
                "top_candidates": [],
            }

    for ref in all_refs:
        if not ref.get("entry_key"):
            continue
        entry_order = int(ref["entry_key"].rsplit(":", 1)[-1])
        section_order = int(ref["entry_key"].split(":")[2])
        entry_id = helper_entry_id(VOLUME_ID, section_order, entry_order)
        helper_item = helper_lookup.get(entry_id)
        if helper_item:
            ref["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the PG046 analytical index from the tail window and retained the adjacent "
            "crosswalk/ordo editorial blocks as separate sections."
        ),
        "evidence_files": [str(path) for path in all_files if 628 <= file_seq(path) <= 642],
    }
    notes = [
        {
            "note_key": "pg046_tail_order",
            "note_raw": (
                "Printed-page order does not match OCR suffix order in the tail. The analytical index ends before the "
                "ORDO NOVUS comparison table, which in turn precedes the ORDO RERUM contents block."
            ),
            "confidence": 0.95,
        },
        {
            "note_key": "pg046_helper",
            "note_raw": "Helper request and output were generated for the page-bearing entries; direct OCR reading remained primary.",
            "confidence": 0.9,
        },
    ]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": (
            "Tail volume containing the analytical index, a parallel comparison table between new and Morellian order, "
            "and the final ordo rerum contents block."
        ),
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": all_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "sections_count": len(sections),
        "nodes_count": len(all_nodes),
        "entries_count": len(all_entries),
        "refs_count": len(all_refs),
    }
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    (args.intermediate_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "volume.json").write_text(json.dumps(volume, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "nodes.json").write_text(json.dumps(all_nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "entries.json").write_text(json.dumps(all_entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "refs.json").write_text(json.dumps(all_refs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "scripture_refs.json").write_text("[]\n", encoding="utf-8")
    (args.intermediate_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_todo(VOLUME_ID, args.intermediate_dir, "Completed PG046 alphabetical index extraction")


if __name__ == "__main__":
    main()

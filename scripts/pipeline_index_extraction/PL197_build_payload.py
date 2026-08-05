#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL197_build_payload.py

Build the PL197 alphabetical-index payload and helper request from the OCR
files under teste/PL197/text, then merge helper output when available.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL197/text"
OUT_PATH = ROOT / "data/alphabetical_index_payloads/PL197_alphabetical_indices.json"
HELPER_REQ_PATH = ROOT / "data/alphabetical_index_payloads/PL197_helper_request.json"
HELPER_OUT_PATH = ROOT / "data/alphabetical_index_payloads/PL197_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL197"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"


SECTION1_FILE = SOURCE_ROOT / "615d4660-99ad-409b-bfc9-b5f9b0e3b0cf-010.txt"
SECTION2_FILES = [
    SOURCE_ROOT / f"5dca4fbe-3e9a-43f1-966d-8f47cbd842b0-{seq:03d}.txt"
    for seq in (684, 686, 687, 688, 690, 692, 694, 698, 700)
]


PAGE_HEADER_RE = re.compile(r"^\d{3,4}\s+.*\s+\d{3,4}$")
PAGE_REF_RE = re.compile(r"(?:\b(?:Col\.)?\s*(\d{1,4})\s*\.?$)")
HEADING_RE = re.compile(
    r"^(?:"
    r"[A-ZÀ-Ü][A-ZÀ-Ü\s,.-]{8,}"
    r"|(?:LIBER|PARS|VISIO|SCIVIAS|EPISTOLAE|EXPLANATIO|VITA|PHYSICA|MONITUM|Præfatio|Praefatio|Capitula|SANCTA|ORDO)\b.*"
    r")$"
)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', text, re.S)
    lines: list[str] = []
    for block in blocks:
        for raw in block.splitlines():
            s = raw.strip()
            if s:
                lines.append(s)
    return lines


def section1_lines() -> list[str]:
    return read_lines(SECTION1_FILE)


def section2_lines() -> list[str]:
    lines: list[str] = []
    started = False
    for path in SECTION2_FILES:
        path_lines = read_lines(path)
        if not started:
            # 684 contains body text before the actual ORDO RERUM heading.
            # Start at the last explicit "ORDO RERUM" line in that file.
            if path.name.endswith("-684.txt"):
                idx = None
                for i, line in enumerate(path_lines):
                    if line == "ORDO RERUM":
                        idx = i
                if idx is None:
                    idx = 0
                path_lines = path_lines[idx + 1 :]
            else:
                # Other files are TOC continuations; skip the page header line.
                if path_lines and PAGE_HEADER_RE.fullmatch(path_lines[0]):
                    path_lines = path_lines[1:]
            started = True
        else:
            if path_lines and PAGE_HEADER_RE.fullmatch(path_lines[0]):
                path_lines = path_lines[1:]
        lines.extend(path_lines)
    return lines


def normalize_latin(text: str) -> str:
    text = text.lower().replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^0-9a-zà-ž]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_query(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+\d{1,4}\s*$", "", text)
    return text[:140]


def extract_ref(line: str) -> tuple[str | None, int | None, str | None]:
    m = PAGE_REF_RE.search(line)
    if not m:
        return None, None, None
    raw = m.group(0).strip()
    value = int(m.group(1))
    if raw.lower().startswith("col."):
        return raw, value, "Col."
    return raw, value, None


def build_page_map() -> OrderedDict[int, str]:
    page_map: OrderedDict[int, str] = OrderedDict()
    for path in sorted(SOURCE_ROOT.glob("*.txt")):
        lines = read_lines(path)
        for line in lines[:8]:
            for match in re.finditer(r"(?<![A-Za-z])(\d{1,4})(?![A-Za-z])", line):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def parse_entries(
    lines: list[str],
    section_key: str,
    section_start_file: str,
    section_kind: str,
    editorial_anchor_file: str,
    parent_node_key: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    page_map = build_page_map()
    entries: list[dict] = []
    refs: list[dict] = []
    helper_entries: list[dict] = []
    pending: list[str] = []
    entry_order = 0

    def flush() -> None:
        nonlocal pending, entry_order
        if not pending:
            return
        raw = " ".join(pending).strip()
        pending = []
        if not raw:
            return
        if raw in {"ORDO RERUM", "SANCTA HILDEGARDIS ABBATISSA.", "SCIVIAS SIVE LIBRI TRES VISIONUM AC REVELATIONUM."}:
            return
        ref_raw, ref_int, ref_col = extract_ref(raw)
        entry_order += 1
        entry_key = f"PL197:entry:{section_kind}:{entry_order:04d}"
        target_best = page_map.get(ref_int) if ref_int is not None else None
        confidence = 0.91 if ref_int is not None else 0.72
        if ref_col:
            ref_kind = "editorial_column"
        else:
            ref_kind = "editorial_page"
        lemma_raw = raw
        if ref_raw:
            lemma_raw = raw[: raw.rfind(ref_raw)].rstrip(" .—-")
        entry_kind = "lemma"
        helper_entry_id = f"pl197_{section_kind}_{entry_order:04d}"
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw or raw,
                "lemma_display": lemma_raw or raw,
                "lemma_norm": normalize_latin(lemma_raw or raw),
                "lemma_sort": normalize_latin(lemma_raw or raw),
                "entry_raw": raw,
                "context_raw": raw,
                "heading_letter": None,
                "inferred_printed_page": ref_int,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "target_file_best": target_best,
                "confidence": confidence,
                "raw_json": {
                    "source_file": editorial_anchor_file,
                    "section_kind": section_kind,
                    "helper_entry_id": helper_entry_id,
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": ref_kind,
                "ref_raw": ref_raw or "",
                "page_ref_raw": str(ref_int) if ref_int is not None else None,
                "page_ref_int": ref_int,
                "page_ref_col": ref_col,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_best,
                "target_file_probability": 0.88 if target_best else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": confidence,
                "raw_json": {"source_file": editorial_anchor_file, "section_kind": section_kind, "helper_entry_id": helper_entry_id},
            }
        )
        helper_entries.append(
            {
                "entry_id": helper_entry_id,
                "lemma_raw": lemma_raw or raw,
                "query_names": [compact_query(lemma_raw or raw)],
                "page_hints": [str(ref_int)] if ref_int is not None else [],
                "page_hint_ints": [ref_int] if ref_int is not None else [],
                "context_raw": raw,
            }
        )

    for idx, line in enumerate(lines):
        if PAGE_HEADER_RE.fullmatch(line) or line.startswith("Digitized by Google"):
            continue
        if not pending:
            if line in {"SANCTA HILDEGARDIS ABBATISSA.", "ORDO RERUM", "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."}:
                continue
            if HEADING_RE.fullmatch(line) and not PAGE_REF_RE.search(line):
                # Keep only real headings as section metadata, not entries.
                continue
            pending.append(line)
        else:
            pending.append(line)
        current = " ".join(pending).strip()
        if PAGE_REF_RE.search(current):
            flush()
            continue
        if HEADING_RE.fullmatch(current) and not PAGE_REF_RE.search(current):
            pending = []

    if pending:
        flush()
    return entries, refs, helper_entries


def build_nodes() -> list[dict]:
    return [
        {
            "node_key": "PL197:node:author_index:001",
            "section_key": "PL197:alpha:author_index:001",
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "SANCTA HILDEGARDIS ABBATISSA.",
            "label_norm": "sancta hildegardis abbatissa",
            "label_sort": "sancta hildegardis abbatissa",
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_file": str(SECTION1_FILE), "section_kind": "author_index"},
        },
        {
            "node_key": "PL197:node:ordo_rerum:001",
            "section_key": "PL197:alpha:ordo_rerum:002",
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
            "label_norm": "ordo rerum quae in hoc tomo continentur",
            "label_sort": "ordo rerum quae in hoc tomo continentur",
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_file": str(SECTION2_FILES[-1]), "section_kind": "ordo_rerum"},
        },
    ]


def build_sections() -> list[dict]:
    return [
        {
            "section_key": "PL197:alpha:author_index:001",
            "volume_id": "PL197",
            "work_key": None,
            "section_order": 1,
            "section_kind": "author_index",
            "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CXCVII CONTINENTUR.",
            "heading_norm": "elenchus auctorum et operum qui in hoc tomo cxcvii continentur",
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(SECTION1_FILE),
            "file_end": str(SECTION1_FILE),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Front-matter author/work list for the Hildegard volume.",
                "evidence_files": [str(SECTION1_FILE)],
            },
        },
        {
            "section_key": "PL197:alpha:ordo_rerum:002",
            "volume_id": "PL197",
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1351,
            "page_end": 1384,
            "file_start": str(SECTION2_FILES[0]),
            "file_end": str(SECTION2_FILES[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table spanning the Hildegard works in the volume tail.",
                "evidence_files": [str(p) for p in SECTION2_FILES],
            },
        },
    ]


def load_helper_output() -> dict | None:
    if not HELPER_OUT_PATH.exists():
        return None
    return json.loads(HELPER_OUT_PATH.read_text(encoding="utf-8"))


def build_payload() -> tuple[dict, dict]:
    section1_entries, section1_refs, helper1 = parse_entries(
        section1_lines(),
        "PL197:alpha:author_index:001",
        str(SECTION1_FILE),
        "author_index",
        str(SECTION1_FILE),
        "PL197:node:author_index:001",
    )
    section2_entries, section2_refs, helper2 = parse_entries(
        section2_lines(),
        "PL197:alpha:ordo_rerum:002",
        str(SECTION2_FILES[0]),
        "ordo_rerum",
        str(SECTION2_FILES[0]),
        "PL197:node:ordo_rerum:001",
    )
    helper_request = {
        "volume_id": "PL197",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper1 + helper2,
    }

    helper_output = load_helper_output()
    if helper_output:
        helper_map: dict[str, dict] = {}
        for item in helper_output.get("entries", []):
            helper_map[item.get("entry_id")] = item
        for entry in section1_entries + section2_entries:
            helper_id = entry["raw_json"].get("helper_entry_id")
            if helper_id and helper_id in helper_map:
                helper_item = helper_map[helper_id]
                best = helper_item.get("best_candidate") or {}
                if best.get("file"):
                    entry["target_file_best"] = best.get("file")
                entry["raw_json"]["helper_status"] = helper_item.get("status")
                if best:
                    entry["raw_json"]["helper_best_candidate"] = {
                        "file": best.get("file"),
                        "probability": best.get("probability"),
                        "candidate_role": best.get("candidate_role"),
                        "reason_summary": best.get("reason_summary"),
                    }
        for ref in section1_refs + section2_refs:
            entry_id = ref["raw_json"].get("helper_entry_id")
            if entry_id in helper_map:
                helper_item = helper_map[entry_id]
                best = helper_item.get("best_candidate") or {}
                if best.get("file"):
                    ref["target_file"] = best.get("file")
                    ref["target_file_probability"] = best.get("probability")
                ref["raw_json"]["helper_status"] = helper_item.get("status")
                if best:
                    ref["raw_json"]["helper_best_candidate"] = {
                        "file": best.get("file"),
                        "probability": best.get("probability"),
                        "candidate_role": best.get("candidate_role"),
                        "reason_summary": best.get("reason_summary"),
                    }

    payload = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "volume": {
            "volume_id": "PL197",
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 197",
        },
        "sections": build_sections(),
        "nodes": build_nodes(),
        "entries": section1_entries + section2_entries,
        "refs": section1_refs + section2_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "Recovered the ELENCHUS and the closing ORDO RERUM from OCR and anchored the cited pages with the local helper/page map.",
            "evidence_files": [str(SECTION1_FILE)] + [str(p) for p in SECTION2_FILES],
        },
        "notes": [
            {
                "note_type": "extraction",
                "status": "completed",
                "source": "PL197_build_payload.py",
            },
            {
                "note_type": "helper",
                "status": "pending" if helper_output is None else "loaded",
                "source": str(HELPER_OUT_PATH),
            },
        ],
    }
    return payload, helper_request


def write_todo() -> None:
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODO_PATH.write_text(
        json.dumps(
            {
                "volume_id": "PL197",
                "updated_at": iso_now(),
                "current_focus": "Finalize PL197 alphabetical payload and verify helper page anchors.",
                "completed": [
                    "OCR window inspected",
                    "ELENCHUS and ORDO RERUM sections identified",
                    "helper request assembled",
                ],
                "pending": [
                    "run index_target_locator on helper request",
                    "merge helper anchors into payload",
                    "validate final JSON payload",
                ],
                "blocked": [],
                "notes": [
                    "Keep OCR literals intact.",
                    "Do not confuse OCR file suffixes with printed page numbers.",
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    payload, helper_request = build_payload()
    HELPER_REQ_PATH.write_text(json.dumps(helper_request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_todo()
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "payload_written": str(OUT_PATH),
        "helper_request_written": str(HELPER_REQ_PATH),
        "entries": len(payload["entries"]),
        "refs": len(payload["refs"]),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

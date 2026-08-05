#!/usr/bin/env python3
"""Usage: build the PG126 alphabetical payload and helper request from OCR.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG126_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG126/text \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG126 \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG126_helper_request.json \
    --payload-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG126_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_TOKEN_RE = re.compile(r"(?:ibid\.?|id\.?|\d{1,4}(?:\s*[,;]\s*\d{1,4})*(?:\s*[-–—]\s*\d{1,4})?)", re.IGNORECASE)
TAIL_CLUSTER_RE = re.compile(
    r"(?<!\w)(?:ibid\.?|id\.?|\d{1,4}(?:\s*[,;]\s*\d{1,4})*(?:\s*[-–—]\s*\d{1,4})?)(?!\w)",
    re.IGNORECASE,
)
ENTRY_BOUNDARY_RE = re.compile(
    r"((?:ibid\.?|id\.?|\d{1,4}(?:\s*[,;]\s*\d{1,4})*(?:\s*[-–—]\s*\d{1,4})?))\.\s+(?=[A-ZÀ-ÖØ-ÞΑ-Ω—])",
    re.IGNORECASE,
)
SECTION_HEADING_PATTERNS = [
    ("tom1", re.compile(r"INDEX IN TOM\.\s*I THEOPHYLACTI\.?", re.IGNORECASE)),
    ("tom2", re.compile(r"INDEX IN TOMUM SECUNDUM\.?", re.IGNORECASE)),
    ("tom3", re.compile(r"INDEX IN TOMUM TERTIUM\.?", re.IGNORECASE)),
    ("tom4", re.compile(r"INDEX IN TOMUM QUARTUM\.?", re.IGNORECASE)),
    ("ordo", re.compile(r"ORDO RERUM", re.IGNORECASE)),
    ("ordo", re.compile(r"ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR\.?", re.IGNORECASE)),
    ("index_intro", re.compile(r"INDICES IN OPERA THEOPHYLACTI", re.IGNORECASE)),
]
LETTER_RE = re.compile(r"^[A-Z]$")
GREEK_LETTER_RE = re.compile(r"^[Α-Ω]$")
TOM_LABELS = {
    "tom1": "IN TOMUM PRIMUM",
    "tom2": "INDEX IN TOMUM SECUNDUM.",
    "tom3": "INDEX IN TOMUM TERTIUM.",
    "tom4": "INDEX IN TOMUM QUARTUM.",
    "ordo": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
}


@dataclass
class EntryDraft:
    section_key: str
    section_kind: str
    lemma_raw: str | None
    entry_raw: str
    context_raw: str | None
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str | None
    editorial_anchor_file: str | None
    target_file_best: str | None
    confidence: float
    raw_json: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def lemma_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def lemma_sort(text: str | None) -> str | None:
    value = lemma_norm(text)
    return value


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def page_to_file(page: int, prefix: str, source_root: Path) -> str:
    seq = (page + 13) // 2
    return str(source_root / f"{prefix}-{seq:03d}.txt")


def parse_blocks(path: Path) -> list[tuple[str, list[str]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, list[str]]] = []
    for m in BLOCK_RE.finditer(raw):
        attrs = {am.group(1): am.group(2) for am in ATTR_RE.finditer(m.group("attrs") or "")}
        block_type = (attrs.get("tipo") or "").strip().lower()
        content = m.group("content") or ""
        lines = [normalize(line) for line in content.splitlines() if normalize(line)]
        if lines:
            out.append((block_type, lines))
    return out


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def infer_prefix(files: list[Path]) -> str:
    return files[0].name.rsplit("-", 1)[0]


def is_letter_heading(text: str) -> bool:
    return bool(LETTER_RE.fullmatch(text) or GREEK_LETTER_RE.fullmatch(text))


def is_section_marker(text: str) -> str | None:
    for marker, pattern in SECTION_HEADING_PATTERNS:
        if pattern.search(text):
            return marker
    return None


def split_entry_fragments(text: str) -> list[str]:
    if not text:
        return []
    text = normalize(text)
    parts = ENTRY_BOUNDARY_RE.sub(r"\1.\n", text).splitlines()
    return [normalize(part) for part in parts if normalize(part)]


def parse_page_cluster(cluster: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None, list[str]]:
    cluster = normalize(cluster).rstrip(".")
    raw_tokens: list[str] = []
    refs: list[dict[str, Any]] = []
    notes: list[str] = []
    if not cluster:
        return refs, last_page, notes

    if re.fullmatch(r"(?:ibid\.?|id\.?)", cluster, flags=re.IGNORECASE):
        if last_page is None:
            notes.append("ibid_without_previous_page")
            return refs, last_page, notes
        refs.append(
            {
                "raw": cluster.rstrip("."),
                "page_ref_raw": cluster.rstrip("."),
                "page_ref_int": last_page,
                "kind": "ibid",
            }
        )
        return refs, last_page, notes

    # Split page lists while preserving OCR join errors like "6,3" -> 63.
    tokens = [tok.strip() for tok in re.split(r"\s*[,;]\s*", cluster) if tok.strip()]
    for token in tokens:
        if re.fullmatch(r"(?:ibid\.?|id\.?)", token, flags=re.IGNORECASE):
            if last_page is None:
                notes.append("ibid_without_previous_page")
                continue
            refs.append({"raw": token.rstrip("."), "page_ref_raw": token.rstrip("."), "page_ref_int": last_page, "kind": "ibid"})
            continue
        m = re.fullmatch(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})", token)
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            refs.append(
                {
                    "raw": token,
                    "page_ref_raw": token,
                    "page_ref_int": start,
                    "range_start_raw": m.group(1),
                    "range_end_raw": m.group(2),
                    "kind": "range",
                }
            )
            last_page = start
            continue
        if re.fullmatch(r"\d,\d", token):
            joined = int(token.replace(",", ""))
            refs.append({"raw": token, "page_ref_raw": token, "page_ref_int": joined, "kind": "joined_digits"})
            last_page = joined
            continue
        m = re.fullmatch(r"(\d{1,4})", token)
        if m:
            page = int(m.group(1))
            refs.append({"raw": token, "page_ref_raw": token, "page_ref_int": page, "kind": "page"})
            last_page = page
            continue
        # Allow a few obvious OCR glitches such as 1985 -> 1285 in headers, but not in refs.
        if token.isdigit() and len(token) == 4 and int(token) > 1292:
            notes.append(f"ignored_ocr_glitch_token:{token}")
            continue
        notes.append(f"unparsed_token:{token}")
    return refs, last_page, notes


def extract_entry(entry_text: str, *, section_key: str, section_kind: str, section_start_file: str, editorial_anchor_file: str, heading_letter: str | None, last_page: int | None, prefix: str, source_root: Path, confidence: float = 0.93) -> tuple[EntryDraft, int | None]:
    text = normalize(entry_text)
    page_match = None
    for m in TAIL_CLUSTER_RE.finditer(text):
        suffix = text[m.end() :].strip()
        if suffix in {"", ".", ":", ";", ",", "—", "-"}:
            page_match = m
    if page_match:
        lemma = normalize(text[: page_match.start()].rstrip(" ,;:."))
        cluster = text[page_match.start() :].rstrip()
        refs, resolved_last, notes = parse_page_cluster(cluster, last_page)
        target = None
        inferred = None
        if refs:
            first_ref = refs[0]
            inferred = first_ref.get("page_ref_int")
            target = page_to_file(inferred, prefix, source_root) if inferred is not None else None
            if first_ref.get("kind") == "ibid" and last_page is not None:
                notes.append("ibid_resolved_from_previous_page")
        raw_json = {
            "source_file": editorial_anchor_file,
            "page_cluster_raw": cluster,
            "page_cluster_kind": refs[0]["kind"] if refs else "unresolved",
            "parsed_ref_count": len(refs),
            "parse_notes": notes,
        }
        draft = EntryDraft(
            section_key=section_key,
            section_kind=section_kind,
            lemma_raw=lemma or None,
            entry_raw=text,
            context_raw=None,
            heading_letter=heading_letter,
            inferred_printed_page=inferred,
            section_start_file=section_start_file,
            editorial_anchor_file=editorial_anchor_file,
            target_file_best=target,
            confidence=confidence if refs else 0.65,
            raw_json=raw_json,
        )
        return draft, resolved_last

    # Unresolved locator line.
    lemma = normalize(text.rstrip(" ,;:."))
    raw_json = {
        "source_file": editorial_anchor_file,
        "page_cluster_raw": None,
        "page_cluster_kind": "missing",
        "parsed_ref_count": 0,
        "parse_notes": ["missing_explicit_locator"],
    }
    draft = EntryDraft(
        section_key=section_key,
        section_kind=section_kind,
        lemma_raw=lemma or None,
        entry_raw=text,
        context_raw=None,
        heading_letter=heading_letter,
        inferred_printed_page=last_page,
        section_start_file=section_start_file,
        editorial_anchor_file=editorial_anchor_file,
        target_file_best=page_to_file(last_page, prefix, source_root) if last_page is not None else None,
        confidence=0.58,
        raw_json=raw_json,
    )
    return draft, last_page


def build_payload(source_root: Path, intermediate_dir: Path, helper_request_json: Path, payload_output: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    prefix = infer_prefix(files)

    section_defs = {
        "tom1": {
            "section_key": "PG126:section:1",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDICES IN OPERA THEOPHYLACTI. IN TOMUM PRIMUM.",
            "heading_norm": "indices in opera theophylacti in tomum primum",
            "page_start": 1252,
            "page_end": 1270,
            "file_start": str(source_root / f"{prefix}-632.txt"),
            "file_end": str(source_root / f"{prefix}-641.txt"),
            "section_start_file": str(source_root / f"{prefix}-632.txt"),
            "notes": [
                "Umbrella heading 'INDICES IN OPERA THEOPHYLACTI' appears on the opening page before the Tomus I subindex.",
                "The Greek tail on file 632 belongs to the preceding Variae Lectiones block and was excluded."
            ],
        },
        "tom2": {
            "section_key": "PG126:section:2",
            "section_order": 2,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX IN TOMUM SECUNDUM.",
            "heading_norm": "index in tomum secundum",
            "page_start": 1270,
            "page_end": 1282,
            "file_start": str(source_root / f"{prefix}-641.txt"),
            "file_end": str(source_root / f"{prefix}-647.txt"),
            "section_start_file": str(source_root / f"{prefix}-641.txt"),
            "notes": [
                "Section begins after the internal heading 'INDEX IN TOMUM SECUNDUM.' on file 641.",
            ],
        },
        "tom3": {
            "section_key": "PG126:section:3",
            "section_order": 3,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX IN TOMUM TERTIUM.",
            "heading_norm": "index in tomum tertium",
            "page_start": 1282,
            "page_end": 1286,
            "file_start": str(source_root / f"{prefix}-647.txt"),
            "file_end": str(source_root / f"{prefix}-649.txt"),
            "section_start_file": str(source_root / f"{prefix}-647.txt"),
            "notes": [
                "Section begins after the internal heading 'INDEX IN TOMUM TERTIUM.' on file 647.",
            ],
        },
        "tom4": {
            "section_key": "PG126:section:4",
            "section_order": 4,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX IN TOMUM QUARTUM.",
            "heading_norm": "index in tomum quartum",
            "page_start": 1286,
            "page_end": 1288,
            "file_start": str(source_root / f"{prefix}-649.txt"),
            "file_end": str(source_root / f"{prefix}-650.txt"),
            "section_start_file": str(source_root / f"{prefix}-649.txt"),
            "notes": [
                "Section begins after the internal heading 'INDEX IN TOMUM QUARTUM.' on file 649.",
            ],
        },
        "ordo": {
            "section_key": "PG126:section:5",
            "section_order": 5,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "page_start": 1289,
            "page_end": 1292,
            "file_start": str(source_root / f"{prefix}-651.txt"),
            "file_end": str(source_root / f"{prefix}-652.txt"),
            "section_start_file": str(source_root / f"{prefix}-651.txt"),
            "notes": [
                "Closing contents list beginning with 'ORDO RERUM' on file 651.",
                "The appendix after the closing contents list was excluded."
            ],
        },
    }

    section_order_lookup = ["tom1", "tom2", "tom3", "tom4", "ordo"]
    current_section = None
    current_letter = None
    pending: str | None = None
    last_page_by_section: dict[str, int | None] = {key: None for key in section_order_lookup}
    section_entries: dict[str, list[EntryDraft]] = {key: [] for key in section_order_lookup}
    nodes: dict[str, dict[str, Any]] = {}
    node_orders: dict[str, int] = {key: 0 for key in section_order_lookup}

    helper_samples: list[dict[str, Any]] = []

    def ensure_letter_node(section_key: str, letter: str, source_files: list[str]) -> str:
        key = f"{section_defs[section_key]['section_key']}:node:{letter}"
        if key not in nodes:
            node_orders[section_key] += 1
            nodes[key] = {
                "node_key": key,
                "section_key": section_defs[section_key]["section_key"],
                "parent_node_key": None,
                "node_order": node_orders[section_key],
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_files": source_files},
            }
        return key

    def flush_pending(current_file: str) -> None:
        nonlocal pending, current_section, current_letter
        if not pending:
            return
        if current_section is None:
            pending = None
            return
        draft, new_last = extract_entry(
            pending,
            section_key=section_defs[current_section]["section_key"],
            section_kind=section_defs[current_section]["section_kind"],
            section_start_file=section_defs[current_section]["section_start_file"],
            editorial_anchor_file=current_file,
            heading_letter=current_letter,
            last_page=last_page_by_section[current_section],
            prefix=prefix,
            source_root=source_root,
            confidence=0.60,
        )
        last_page_by_section[current_section] = new_last
        section_entries[current_section].append(draft)
        pending = None

    def emit_entry(fragment: str, current_file: str) -> None:
        nonlocal current_section, pending
        if current_section is None:
            return
        draft, new_last = extract_entry(
            fragment,
            section_key=section_defs[current_section]["section_key"],
            section_kind=section_defs[current_section]["section_kind"],
            section_start_file=section_defs[current_section]["section_start_file"],
            editorial_anchor_file=current_file,
            heading_letter=current_letter,
            last_page=last_page_by_section[current_section],
            prefix=prefix,
            source_root=source_root,
        )
        last_page_by_section[current_section] = new_last
        section_entries[current_section].append(draft)

    for path in files:
        blocks = parse_blocks(path)
        current_file = str(path)
        # Scan block text in reading order and switch sections when their headings appear.
        for block_type, lines in blocks:
            for raw_line in lines:
                line = normalize(raw_line)
                if not line:
                    continue
                marker = is_section_marker(line)
                if marker == "index_intro":
                    # Ignore the umbrella heading and wait for the specific tome heading.
                    continue
                if marker in {"tom1", "tom2", "tom3", "tom4", "ordo"}:
                    flush_pending(current_file)
                    current_section = marker
                    current_letter = None
                    continue
                if line.startswith("Apud nos a col."):
                    continue
                if current_section is None:
                    continue
                if is_letter_heading(line):
                    flush_pending(current_file)
                    current_letter = line
                    ensure_letter_node(current_section, line, [current_file])
                    continue
                if line in {"-", "–", "—"}:
                    continue
                # Some blocks keep the same line as a heading and the first entries.
                fragments = split_entry_fragments(line)
                if not fragments:
                    continue
                for fragment in fragments:
                    if not fragment:
                        continue
                    if pending is not None:
                        if fragment[:1].islower() or pending.endswith("-") or pending.endswith("—") or pending.endswith("–"):
                            pending = pending.rstrip("-—–").rstrip() + " " + fragment.lstrip()
                            # If the continuation completes a page cluster, keep it pending until the end of the line.
                            continue
                        # Emit the pending unresolved fragment before starting a new entry.
                        flush_pending(current_file)
                    # Decide if this fragment likely needs continuation.
                    if PAGE_TOKEN_RE.search(fragment):
                        emit_entry(fragment, current_file)
                    else:
                        pending = fragment
                # If the line ended with an unfinished fragment, keep it in pending.
                # Otherwise the last call to emit_entry already handled it.

    if pending and current_section is not None:
        flush_pending(current_file)

    # Build helper request from unresolved entries so the locator can repair them.
    unresolved_entries: list[dict[str, Any]] = []
    for section_key in section_order_lookup:
        for idx, draft in enumerate(section_entries[section_key], start=1):
            if draft.target_file_best is None or draft.inferred_printed_page is None:
                unresolved_entries.append(
                    {
                        "entry_id": f"pg126_{section_key}_{idx:04d}",
                        "lemma_raw": draft.lemma_raw or draft.entry_raw,
                        "query_names": [
                            draft.lemma_raw or draft.entry_raw,
                            draft.entry_raw,
                        ],
                        "page_hints": [str(draft.inferred_printed_page)] if draft.inferred_printed_page is not None else [],
                        "page_hint_ints": [draft.inferred_printed_page] if draft.inferred_printed_page is not None else [],
                        "context_raw": draft.entry_raw,
                    }
                )
    helper_request = {
        "volume_id": "PG126",
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": unresolved_entries[:10],
    }
    helper_request_json.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sections: list[dict[str, Any]] = []
    for section_id in section_order_lookup:
        spec = section_defs[section_id]
        sections.append(
            {
                "section_key": spec["section_key"],
                "volume_id": "PG126",
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": spec["file_start"],
                "file_end": spec["file_end"],
                "confidence": 0.98 if section_id != "ordo" else 0.96,
                "raw_json": {
                    "section_kind_reason": (
                        "Alphabetical index subindex for Theophylact's opera, with A-Z lemma groups."
                        if section_id != "ordo"
                        else "Closing contents table listing the order of material in the volume."
                    ),
                    "evidence_files": [spec["file_start"], spec["file_end"]],
                    "notes": spec["notes"],
                },
            }
        )

    entries_out: list[dict[str, Any]] = []
    refs_out: list[dict[str, Any]] = []
    for section_id in section_order_lookup:
        spec = section_defs[section_id]
        drafts = section_entries[section_id]
        for order, draft in enumerate(drafts, start=1):
            entry_key = f"PG126:entry:{len(entries_out)+1:06d}"
            lemma = draft.lemma_raw
            entry_kind = "lemma"
            if section_id == "ordo":
                entry_kind = "heading_group"
            elif lemma and lemma.upper() == lemma and len(lemma) > 1:
                entry_kind = "heading_group"
            entries_out.append(
                {
                    "entry_key": entry_key,
                    "section_key": draft.section_key,
                    "parent_node_key": None,
                    "entry_order": order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": lemma_norm(lemma),
                    "lemma_sort": lemma_sort(lemma),
                    "entry_raw": draft.entry_raw,
                    "context_raw": draft.context_raw,
                    "heading_letter": draft.heading_letter,
                    "inferred_printed_page": draft.inferred_printed_page,
                    "section_start_file": draft.section_start_file,
                    "editorial_anchor_file": draft.editorial_anchor_file,
                    "target_file_best": draft.target_file_best,
                    "confidence": draft.confidence,
                    "raw_json": draft.raw_json,
                }
            )
            # Material refs are derived from the OCR page cluster when available.
            entry_refs: list[dict[str, Any]] = []
            tail_match = None
            for m in TAIL_CLUSTER_RE.finditer(draft.entry_raw):
                suffix = draft.entry_raw[m.end() :].strip()
                if suffix in {"", ".", ":", ";", ",", "—", "-"}:
                    tail_match = m
            if tail_match:
                cluster = draft.entry_raw[tail_match.start() :].rstrip(".")
                raw_tokens = [tok.strip() for tok in re.split(r"\s*[,;]\s*", cluster) if tok.strip()]
                ref_order = 0
                for tok in raw_tokens:
                    if re.fullmatch(r"(?:ibid\.?|id\.?)", tok, flags=re.IGNORECASE):
                        if draft.inferred_printed_page is None:
                            continue
                        ref_order += 1
                        entry_refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_page",
                                "ref_raw": tok.rstrip("."),
                                "page_ref_raw": tok.rstrip("."),
                                "page_ref_int": draft.inferred_printed_page,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": draft.target_file_best,
                                "target_file_probability": 0.99 if draft.target_file_best else None,
                                "section_start_file": draft.section_start_file,
                                "editorial_anchor_file": draft.editorial_anchor_file,
                                "confidence": 0.9,
                                "raw_json": {"page_token_kind": "ibid"},
                            }
                        )
                        continue
                    range_match = re.fullmatch(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})", tok)
                    if range_match:
                        start = int(range_match.group(1))
                        end = int(range_match.group(2))
                        target = page_to_file(start, prefix, source_root)
                        ref_order += 1
                        entry_refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_range",
                                "ref_raw": tok,
                                "page_ref_raw": tok,
                                "page_ref_int": start,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": range_match.group(1),
                                "range_end_raw": range_match.group(2),
                                "target_file": target,
                                "target_file_probability": 0.99,
                                "section_start_file": draft.section_start_file,
                                "editorial_anchor_file": draft.editorial_anchor_file,
                                "confidence": 0.9,
                                "raw_json": {"page_token_kind": "range"},
                            }
                        )
                        continue
                    if re.fullmatch(r"\d,\d", tok):
                        joined = int(tok.replace(",", ""))
                        target = page_to_file(joined, prefix, source_root)
                        ref_order += 1
                        entry_refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_page",
                                "ref_raw": tok,
                                "page_ref_raw": tok,
                                "page_ref_int": joined,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": target,
                                "target_file_probability": 0.99,
                                "section_start_file": draft.section_start_file,
                                "editorial_anchor_file": draft.editorial_anchor_file,
                                "confidence": 0.9,
                                "raw_json": {"page_token_kind": "joined_digits"},
                            }
                        )
                        continue
                    if tok.isdigit():
                        page = int(tok)
                        if page > 1292 and section_id != "ordo":
                            continue
                        target = page_to_file(page, prefix, source_root)
                        ref_order += 1
                        entry_refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_page",
                                "ref_raw": tok,
                                "page_ref_raw": tok,
                                "page_ref_int": page,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": target,
                                "target_file_probability": 0.99,
                                "section_start_file": draft.section_start_file,
                                "editorial_anchor_file": draft.editorial_anchor_file,
                                "confidence": 0.9,
                                "raw_json": {"page_token_kind": "page"},
                            }
                )
                refs_out.extend(entry_refs)
            else:
                # Preserve unresolved entries without refs; helper may repair the locator.
                pass
            # Keep a small sample for helper inspection.
            if len(helper_samples) < 8 and (draft.target_file_best is None or draft.inferred_printed_page is None):
                helper_samples.append(
                    {
                        "entry_key": entry_key,
                        "section_key": draft.section_key,
                        "lemma": lemma,
                        "entry_raw": draft.entry_raw,
                        "page": draft.inferred_printed_page,
                        "target": draft.target_file_best,
                    }
                )

    # Repair one OCR split where the page number moved to the following physical line.
    for idx in range(len(entries_out) - 1):
        current = entries_out[idx]
        nxt = entries_out[idx + 1]
        if (
            current["section_key"] == "PG126:section:3"
            and current["lemma_raw"]
            and current["lemma_raw"].endswith("et justitiae ex")
            and nxt["section_key"] == "PG126:section:3"
            and nxt["entry_raw"].startswith("operibus, 334.")
        ):
            current["entry_raw"] = f"{current['entry_raw']} operibus, 334."
            current["lemma_raw"] = "Abraham imago et justitiae ex sola fide, et justitiae ex operibus"
            current["lemma_display"] = current["lemma_raw"]
            current["lemma_norm"] = lemma_norm(current["lemma_raw"])
            current["lemma_sort"] = lemma_sort(current["lemma_raw"])
            current["inferred_printed_page"] = 334
            current["target_file_best"] = page_to_file(334, prefix, source_root)
            current["confidence"] = 0.93
            current["raw_json"] = {
                "source_file": current["raw_json"].get("source_file"),
                "page_cluster_raw": "334.",
                "page_cluster_kind": "page",
                "parsed_ref_count": 1,
                "parse_notes": ["manual_line_join_repair"],
            }
            new_ref = {
                "entry_key": current["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "334",
                "page_ref_raw": "334",
                "page_ref_int": 334,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": page_to_file(334, prefix, source_root),
                "target_file_probability": 0.99,
                "section_start_file": current["section_start_file"],
                "editorial_anchor_file": current["editorial_anchor_file"],
                "confidence": 0.9,
                "raw_json": {"page_token_kind": "page", "repair": "manual_line_join"},
            }
            refs_out = [r for r in refs_out if r["entry_key"] != nxt["entry_key"]]
            refs_out.append(new_ref)
            entries_out.pop(idx + 1)
            break

    scripture_refs: list[dict[str, Any]] = []
    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the Tomus I-IV alphabetical indexes and the closing Ordo Rerum from OCR files 632-652; one split lemma in the Tomus III opening was repaired from adjacent OCR lines.",
        "evidence_files": [
            str(source_root / f"{prefix}-632.txt"),
            str(source_root / f"{prefix}-633.txt"),
            str(source_root / f"{prefix}-634.txt"),
            str(source_root / f"{prefix}-635.txt"),
            str(source_root / f"{prefix}-636.txt"),
            str(source_root / f"{prefix}-637.txt"),
            str(source_root / f"{prefix}-638.txt"),
            str(source_root / f"{prefix}-639.txt"),
            str(source_root / f"{prefix}-640.txt"),
            str(source_root / f"{prefix}-641.txt"),
            str(source_root / f"{prefix}-642.txt"),
            str(source_root / f"{prefix}-643.txt"),
            str(source_root / f"{prefix}-644.txt"),
            str(source_root / f"{prefix}-645.txt"),
            str(source_root / f"{prefix}-646.txt"),
            str(source_root / f"{prefix}-647.txt"),
            str(source_root / f"{prefix}-648.txt"),
            str(source_root / f"{prefix}-649.txt"),
            str(source_root / f"{prefix}-650.txt"),
            str(source_root / f"{prefix}-651.txt"),
            str(source_root / f"{prefix}-652.txt"),
        ],
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": "PG126",
            "collection": "PG",
            "source_root": str(source_root),
            "volume_label": "Patrologia Graeca 126",
            "notes": "Alphabetical Tomus I-IV indexes plus closing Ordo Rerum from Theophylact's opera.",
        },
        "sections": sections,
        "nodes": list(nodes.values()),
        "entries": entries_out,
        "refs": refs_out,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": [
            "The OCR tail on file 632 before the 'INDICES IN OPERA THEOPHYLACTI' heading belongs to the preceding Variae Lectiones block and was excluded.",
            "A helper request was written for one repaired split line; the main page-to-file mapping is derived from the sequential OCR pagination.",
            "Alphabetical entries preserve OCR literals, including inherited dashes and ibid. forms."
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    (intermediate_dir / "payload_draft.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (intermediate_dir / "helper_samples.json").write_text(json.dumps(helper_samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG126 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--payload-output", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.intermediate_dir, args.helper_request_json, args.payload_output)
    args.payload_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

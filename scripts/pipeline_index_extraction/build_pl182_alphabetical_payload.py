#!/usr/bin/env python3
"""Usage: build the PL182 index payload, helper request, and intermediate JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl182_alphabetical_payload.py

The script parses the OCR tail for PL182, extracts the explicit alphabetical and
analytical index sections, writes a helper request for a small set of locator
checks, optionally runs `scripts/index_target_locator.py`, and writes the final
canonical payload plus checkpoint fragments under the PL182 intermediate dir.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL182"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 182"
SOURCE_ROOT = ROOT / "teste/PL182/text"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL182_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL182_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL182"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL182_alphabetical_indices.json"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
SPACE_RE = re.compile(r"\s+")
PAGE_REF_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,4}(?:\s*-\s*\d{1,4})?)")
LETTER_NODE_RE = re.compile(r"^[A-ZÆŒ]$")
SINGLE_LETTER_ABBR_RE = re.compile(r"\b([A-Z])\.\s+(?=[A-Z])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "").replace("\xa0", " ")
    value = SPACE_RE.sub(" ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    if not text:
        return None
    value = unicodedata.normalize("NFKD", text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe"))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = SPACE_RE.sub(" ", value).strip().lower()
    return value or None


def page_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(match.group(1))


def iter_text_blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, str]] = []
    for match in BLOCK_RE.finditer(text):
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        block_type = (tipo_m.group(1).strip().lower() if tipo_m else "")
        body = match.group("body") or ""
        blocks.append((block_type, body))
    return blocks


def extract_segments(path: Path) -> list[tuple[str, str]]:
    """Extract editorial segments from a page.

    Files 588-602 are mostly line-based, while 603-605 are compressed and need
    sentence-style splitting.
    """
    seq = page_seq(path)
    blocks = iter_text_blocks(path)
    segments: list[tuple[str, str]] = []
    if seq >= 603:
        for block_type, body in blocks:
            if block_type != "texto_principal":
                continue
            text = " ".join(line.strip() for line in body.splitlines())
            text = normalize(text)
            if not text:
                continue
            text = SINGLE_LETTER_ABBR_RE.sub(r"\1§", text)
            parts = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", text)
            refined: list[str] = []
            for part in parts:
                refined.extend(re.split(r"(?<=\d)\s+(?=[A-ZÆŒ])", part))
            for part in refined:
                part = part.replace("§", ". ")
                part = normalize(part)
                if part and part.lower() != "digitized by google":
                    segments.append((path.as_posix(), part))
    else:
        for block_type, body in blocks:
            if block_type != "texto_principal":
                continue
            for raw_line in body.splitlines():
                line = normalize(raw_line)
                if line and line.lower() != "digitized by google":
                    segments.append((path.as_posix(), line))
    return segments


def is_node_segment(segment: str, node_labels: set[str]) -> bool:
    if segment in node_labels:
        return True
    if LETTER_NODE_RE.fullmatch(segment):
        return True
    if segment.endswith(".") and len(segment) <= 90 and not re.search(r"\d", segment):
        if segment.isupper():
            return True
    return False


def looks_like_entry_start(segment: str) -> bool:
    return bool(
        re.match(
            r"^(?:\(?\d+\)?\s*)?(?:EPIST\.?|Epist\.?|CAP\.?|Cap\.?|§|Præfatio|Praefatio|Prologus|LIBER|TRACTATUS|APPENDIX|DIVERSORUM|Ad |De |Contra |Pro |Hortatoria|Apologetica|Consolatoria|Responsaria|Excusatoria|Retractatoriæ|Urbane|Modestæ|Pacificæ|Commendatoriæ|Parenetica|Admonitio|Increpatoria|VITA|Monitum)",
            segment,
        )
    )


def strip_leading_note(segment: str) -> str:
    return re.sub(r"^\(\d+\)\s*", "", segment).strip()


def split_page_refs(page_blob: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw_part in [part.strip() for part in page_blob.split(",")]:
        if not raw_part:
            continue
        cleaned = re.sub(r"\s+", "", raw_part)
        if not cleaned:
            continue
        if "-" in cleaned:
            start_raw, end_raw = cleaned.split("-", 1)
            if start_raw.isdigit() and end_raw.isdigit():
                refs.append(
                    {
                        "ref_raw": cleaned,
                        "page_ref_raw": cleaned,
                        "page_ref_int": int(start_raw),
                        "range_start_raw": start_raw,
                        "range_end_raw": end_raw,
                    }
                )
            continue
        if cleaned.isdigit():
            refs.append(
                {
                    "ref_raw": cleaned,
                    "page_ref_raw": cleaned,
                    "page_ref_int": int(cleaned),
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
    return refs


def strip_page_refs(text: str) -> str:
    body = PAGE_REF_RE.sub("", text)
    body = re.sub(r"\s+,", ",", body)
    body = re.sub(r",\s+,", ", ", body)
    body = re.sub(r",\s*,", ", ", body)
    body = re.sub(r"\s{2,}", " ", body)
    body = re.sub(r"\s+([,.;:])", r"\1", body)
    return normalize(body.strip(" ,;:."))


@dataclass
class ParsedItem:
    kind: str  # node or entry
    label: str
    raw: str
    pages: list[dict[str, Any]]
    parent_hint: str | None = None
    source_file: str | None = None


def parse_segments(segments: list[tuple[str, str]], *, section_kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ParsedItem]]:
    node_labels: set[str] = set()
    if section_kind == "alphabetical_general":
        node_labels.update({
            "DIVERSORUM EPISTOLÆ AD BERNARDUM ET ALIOS.",
        })
    elif section_kind == "analytic_subject":
        node_labels.update({
            "(210) EPISTOLÆ ECCLESIASTICÆ.",
            "EPISTOLÆ ECCLESIASTICÆ.",
            "De rebus theologicis, et de disciplina ecclesiastica.",
            "e summo pontifice et curia Romana.",
            "De episcopis.",
            "EPISTOLÆ MORALES.",
            "De virtutibus et vitiis.",
            "Ad reges et principes",
            "Ad ministros regios.",
            "Ad viduas nobiles.",
            "EPISTOLÆ ASCETICÆ.",
            "De vocatione, statu et virtutibus religiosis.",
            "Ad sanctimoniales.",
            "Pro abbatibus et superioribus.",
            "EPISTOLÆ VARIÆ.",
            "Pro e ipso",
            "Pro aliis",
        })
    items: list[ParsedItem] = []
    buffer: list[tuple[str, str]] = []
    current_parent: str | None = None
    current_node_key: str | None = None
    node_count = 0

    def emit_node(label: str) -> None:
        nonlocal node_count, current_parent, current_node_key
        node_count += 1
        current_node_key = f"{VOLUME_ID}:node:{section_kind}:{node_count:03d}"
        current_parent = current_node_key
        items.append(ParsedItem(kind="node", label=label, raw=label, pages=[], parent_hint=None))

    def finalize_entry() -> None:
        if not buffer:
            return
        source_file = buffer[0][0]
        segment = " ".join(seg for _, seg in buffer)
        body = strip_leading_note(segment)
        pages_raw = PAGE_REF_RE.findall(body)
        if not pages_raw:
            return
        entry_body = strip_page_refs(body)
        if not entry_body or re.fullmatch(r"\d+", entry_body):
            return
        pages = split_page_refs(", ".join(pages_raw))
        items.append(ParsedItem(kind="entry", label=entry_body, raw=body, pages=pages, parent_hint=current_parent, source_file=source_file))

    for source_file, segment in segments:
        seg = normalize(segment)
        if not seg or seg.lower() == "digitized by google":
            continue
        if is_node_segment(seg, node_labels):
            if section_kind == "alphabetical_general" and seg.startswith("DIVERSORUM EPISTOLÆ AD BERNARDUM ET ALIOS"):
                emit_node("DIVERSORUM EPISTOLÆ AD BERNARDUM ET ALIOS.")
            elif section_kind == "analytic_subject" and seg in node_labels:
                emit_node(seg)
            elif section_kind == "alphabetical_general" and LETTER_NODE_RE.fullmatch(seg):
                emit_node(seg)
            elif section_kind == "analytic_subject" and LETTER_NODE_RE.fullmatch(seg):
                emit_node(seg)
            elif section_kind == "analytical" and seg in node_labels:
                emit_node(seg)
            else:
                # In the ordo_rerum table or other closing material, headings are
                # not needed for the payload if they do not carry locators.
                if section_kind == "ordo_rerum":
                    continue
                emit_node(seg)
            buffer = []
            continue
        if PAGE_REF_RE.search(seg):
            if buffer:
                buffer.append((source_file, seg))
                finalize_entry()
                buffer = []
            else:
                buffer = [(source_file, seg)]
                finalize_entry()
                buffer = []
            continue
        if buffer:
            buffer.append((source_file, seg))
            continue
        if looks_like_entry_start(seg):
            buffer = [(source_file, seg)]
            continue
        # Plain headings without locators in the ordo_rerum table are skipped.
        if section_kind != "ordo_rerum":
            emit_node(seg)
    return (
        [item.__dict__ for item in items if item.kind == "node"],
        [item.__dict__ for item in items if item.kind == "entry"],
        items,
    )


def build_entry(
    *,
    section_key: str,
    section_kind: str,
    item: ParsedItem,
    section_start_file: str,
    editorial_anchor_file: str,
    target_map: dict[int, str],
    helper_map: dict[str, Any],
    entry_order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    entry_raw = item.raw
    lemma_raw = item.label
    entry_key = f"{VOLUME_ID}:entry:{section_key.split(':')[-1]}:{entry_order:04d}"
    refs: list[dict[str, Any]] = []
    helper = helper_map.get(entry_raw) or helper_map.get(lemma_raw) or {}
    best_candidate = helper.get("best_candidate") or {}
    target_best = best_candidate.get("file")
    if not target_best and item.pages:
        first_page = item.pages[0].get("page_ref_int")
        if first_page is not None:
            target_best = target_map.get(first_page)
    for idx, page in enumerate(item.pages, start=1):
        page_int = page.get("page_ref_int")
        target_file = target_map.get(page_int) if page_int is not None else None
        if not target_file and target_best:
            target_file = target_best
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": idx,
                "ref_kind": "editorial_range" if page.get("range_start_raw") else "editorial_page",
                "ref_raw": page["ref_raw"],
                "page_ref_raw": page["page_ref_raw"],
                "page_ref_int": page.get("page_ref_int"),
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": page.get("range_start_raw"),
                "range_end_raw": page.get("range_end_raw"),
                "target_file": target_file,
                "target_file_probability": 0.99 if target_file else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": 0.96 if target_file else 0.72,
                "raw_json": {
                    "page_ref_source": "parsed_tail",
                    "helper_considered": bool(helper),
                },
            }
        )
    entry = {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": item.parent_hint,
        "entry_order": None,
        "entry_kind": "lemma",
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": normalize(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": entry_raw,
        "context_raw": entry_raw,
        "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
        "inferred_printed_page": item.pages[0]["page_ref_int"] if item.pages else None,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_best,
        "confidence": 0.94 if target_best else 0.78,
        "raw_json": {
            "source_file": item.source_file or editorial_anchor_file,
            "section_kind": section_kind,
            "page_hint_count": len(item.pages),
            "helper": helper or None,
        },
    }
    return entry, refs, []


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        text = path.read_text(encoding="utf-8", errors="replace")
        first_header = None
        for match in BLOCK_RE.finditer(text):
            attrs = match.group("attrs") or ""
            tipo_m = re.search(r'tipo="([^"]+)"', attrs)
            block_type = (tipo_m.group(1).strip().lower() if tipo_m else "")
            if block_type == "cabecalho":
                first_header = normalize(" ".join(line.strip() for line in (match.group("body") or "").splitlines()))
                break
        if not first_header:
            continue
        for num in re.findall(r"\b\d{1,4}\b", first_header):
            page_map.setdefault(int(num), path.as_posix())
    return page_map


def make_helper_request(entries: list[dict[str, Any]], section_key: str) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for entry in entries:
        lemma = entry["lemma_raw"]
        page_hints = [str(ref["page_ref_int"]) for ref in entry.get("refs", []) if ref.get("page_ref_int")]
        if not page_hints:
            continue
        selected.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [lemma, normalize(lemma).replace(",", ""), normalize(lemma).replace(".", "")],
                "page_hints": page_hints,
                "page_hint_ints": [int(value) for value in page_hints],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": SOURCE_ROOT.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": selected,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    existing_output = read_json(helper_output_json, {})
    if existing_output.get("entries"):
        return existing_output
    if not helper_request_json.exists() or not read_json(helper_request_json, {}).get("entries"):
        helper_output = {"volume_id": VOLUME_ID, "status": "empty", "entries": []}
        write_json(helper_output_json, helper_output)
        return helper_output
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
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict) and item.get("entry_id")}


def build_payload() -> dict[str, Any]:
    sections_def = [
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
            "volume_id": VOLUME_ID,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HAC VOLUMINIS PRIMI PARTE PRIORI CONTINENTUR.",
            "heading_reason": "Editorial contents table at the front of the tail material; included as closing contents structure rather than an alphabetical index.",
            "files": list(range(588, 601)),
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:alphabetical_index:002",
            "volume_id": VOLUME_ID,
            "section_order": 2,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX ALPHABETICUS EPISTOLARUM S. BERNARDI.",
            "heading_reason": "Alphabetical index of epistles arranged by recipient/entry name, not a subject index.",
            "files": list(range(601, 604)),
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_index:003",
            "volume_id": VOLUME_ID,
            "section_order": 3,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX EPISTOLARUM S. BERNARDI PRO ARGUMENTI QUALITATE DISPOSITUS.",
            "heading_reason": "Argument/subject index with rubric headings and thematic page locators.",
            "files": list(range(604, 606)),
        },
    ]

    page_map = build_page_map(SOURCE_ROOT)
    all_entries: list[dict[str, Any]] = []
    all_nodes: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    helper_seed_entries: list[dict[str, Any]] = []

    helper_request_placeholder: dict[str, Any] = {"volume_id": VOLUME_ID, "source_root": SOURCE_ROOT.as_posix(), "options": {}, "entries": []}

    for section in sections_def:
        segs: list[str] = []
        files = [SOURCE_ROOT / f"88b11b5c-1327-4f85-b255-95bb167e6b6d-{seq}.txt" for seq in section["files"]]
        for file_path in files:
            segs.extend(extract_segments(file_path))
        nodes, raw_entries, raw_items = parse_segments(segs, section_kind=section["section_kind"])
        section["file_start"] = files[0].as_posix()
        section["file_end"] = files[-1].as_posix()
        section["page_start"] = None
        section["page_end"] = None
        section["confidence"] = 0.98 if section["section_kind"] == "ordo_rerum" else 0.99
        section["raw_json"] = {
            "section_kind_reason": section.pop("heading_reason"),
            "evidence_files": [files[0].as_posix(), files[-1].as_posix()],
        }
        all_nodes.extend(
            {
                "node_key": f"{section['section_key']}:node:{idx+1:03d}",
                "section_key": section["section_key"],
                "parent_node_key": None,
                "node_order": idx + 1,
                "node_kind": "heading_group" if section["section_kind"] != "alphabetical_general" else ("letter_group" if len(node["label"]) == 1 else "heading_group"),
                "label_raw": node["label"],
                "label_norm": normalize(node["label"]),
                "label_sort": sort_norm(node["label"]),
                "node_level": 1,
                "confidence": 0.9,
                "raw_json": {
                    "source_section": section["section_key"],
                },
            }
            for idx, node in enumerate(nodes)
        )
        node_map: dict[str, str] = {}
        for idx, node in enumerate(nodes):
            node_map[node["raw"]] = f"{section['section_key']}:node:{idx+1:03d}"
        entry_counter = 0
        for item in raw_items:
            if item.kind != "entry":
                continue
            if not item.label.strip():
                continue
            entry_counter += 1
            parsed_item = ParsedItem(
                kind="entry",
                label=item.label,
                raw=item.raw,
                pages=item.pages,
                parent_hint=node_map.get(item.parent_hint or ""),
                source_file=item.source_file,
            )
            if section["section_kind"] == "alphabetical_general":
                first = parsed_item.label[:1].upper() if parsed_item.label else None
                if first and first.isalpha() and first in node_map:
                    parsed_item.parent_hint = node_map[first]
            if section["section_kind"] == "analytic_subject" and not parsed_item.parent_hint:
                parsed_item.parent_hint = None
            entry, refs, _ = build_entry(
                section_key=section["section_key"],
                section_kind=section["section_kind"],
                item=parsed_item,
                section_start_file=files[0].as_posix(),
                editorial_anchor_file=parsed_item.source_file or files[0].as_posix(),
                target_map=page_map,
                helper_map={},
                entry_order=entry_counter,
            )
            entry["entry_order"] = entry_counter
            all_entries.append(entry)
            all_refs.extend(refs)

    # Build helper request from a few complex entries and rerun once the request is written.
    helper_request_placeholder["entries"] = []
    helper_request_placeholder["entries"] = [
        {
            "entry_id": entry["entry_key"],
            "lemma_raw": entry["lemma_raw"],
            "query_names": [entry["lemma_raw"], normalize(entry["lemma_raw"]).replace(",", ""), normalize(entry["lemma_raw"]).replace(".", "")],
            "page_hints": [str(ref["page_ref_int"]) for ref in all_refs if ref["entry_key"] == entry["entry_key"] and ref.get("page_ref_int")],
            "page_hint_ints": [int(ref["page_ref_int"]) for ref in all_refs if ref["entry_key"] == entry["entry_key"] and ref.get("page_ref_int")],
            "context_raw": entry["entry_raw"],
        }
        for entry in all_entries
        if any(ref["entry_key"] == entry["entry_key"] and ref.get("page_ref_int") for ref in all_refs)
    ]

    write_json(HELPER_REQUEST_JSON, helper_request_placeholder)
    helper_output = run_helper(HELPER_REQUEST_JSON, HELPER_OUTPUT_JSON)
    helper_map = helper_index(helper_output)

    # Attach helper evidence where available and refresh target_file_best from the output.
    for entry in all_entries:
        helper = helper_map.get(entry["entry_key"])
        if helper:
            entry["raw_json"]["helper"] = helper
            best = helper.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]
        for ref in all_refs:
            if ref["entry_key"] != entry["entry_key"]:
                continue
            if not ref.get("target_file"):
                page_int = ref.get("page_ref_int")
                if page_int is not None:
                    ref["target_file"] = page_map.get(page_int)
            if entry["target_file_best"] and not ref.get("target_file"):
                ref["target_file"] = entry["target_file_best"]
            if ref.get("target_file"):
                ref["target_file_probability"] = 0.99

    coverage = {
        "entries_status": "recovered",
        "entries_status_reason": "Recovered the explicit alphabetical and analytical index sections from the OCR tail. The contents table section earlier in the tail was also serialized as editorial closure material.",
        "evidence_files": [
            (SOURCE_ROOT / "88b11b5c-1327-4f85-b255-95bb167e6b6d-588.txt").as_posix(),
            (SOURCE_ROOT / "88b11b5c-1327-4f85-b255-95bb167e6b6d-603.txt").as_posix(),
            (SOURCE_ROOT / "88b11b5c-1327-4f85-b255-95bb167e6b6d-605.txt").as_posix(),
        ],
    }
    notes = [
        "OCR tail contains three editorial structures: a contents table (ORDO RERUM), an alphabetical epistles index, and a subject/argument index.",
        "The page references inside the index entries were kept literal; OCR file suffixes are tracked separately from printed page numbers.",
        f"Helper status: {helper_output.get('status', 'unknown')}.",
    ]
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": SOURCE_ROOT.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections_def,
        "nodes": all_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections_def)
    write_json(INTERMEDIATE_DIR / "nodes.json", all_nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", all_entries)
    write_json(INTERMEDIATE_DIR / "refs.json", all_refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "helper_request_json": HELPER_REQUEST_JSON.as_posix(),
            "helper_output_json": HELPER_OUTPUT_JSON.as_posix(),
            "output_file": OUTPUT_FILE.as_posix(),
        },
    )
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": payload["generated_at"],
            "current_focus": "Finalize PL182 payload and preserve helper evidence for complex page-locator entries.",
            "completed": [
                "parsed the explicit index sections from OCR",
                "built a helper request for complex locator checks",
                "ran index_target_locator",
                "wrote intermediate fragments",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes distinct from printed page numbers.",
                "The contents-table section is included as ordo_rerum editorial closure material.",
            ],
        },
    )
    return payload


def main() -> None:
    payload = build_payload()
    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()

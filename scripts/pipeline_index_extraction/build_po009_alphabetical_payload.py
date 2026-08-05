#!/usr/bin/env python3
"""Usage: rebuild PO009 alphabetical-index payload from inspected OCR index pages.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_po009_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PO009"
COLLECTION = "PO"
SOURCE_ROOT = ROOT / "teste/PO009/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PO009_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PO009_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PO009_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO009"
READER = ROOT / "scripts/read_ocr_page_text.py"

SUPERSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
SPACE_RE = re.compile(r"\s+")
LATIN_REF_RE = re.compile(r"(?<![A-Za-zÀ-ÿܐ-ݏ])(?P<num>\d{1,3})(?:\s*(?P<fest>[SHPN]))?(?![A-Za-zÀ-ÿ])")
LINE_REF_RE = re.compile(
    rf"(?<![\wܐ-ݏ])(?P<page>\d{{1,3}})(?P<line>(?:[_{SUBSCRIPT_DIGITS}][0-9{SUBSCRIPT_DIGITS},₋_\-]*)+|(?:\s+n\.\s*\d+))?"
)
RANGE_RE = re.compile(r"(?P<a>\d{1,3})\s*(?:à|-)\s*(?P<b>\d{1,3})")
ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
BOOKS = {
    "GENÈSE": "Gênesis",
    "NOMBRES": "Números",
    "I ROIS": "1 Reis",
    "II ROIS": "2 Reis",
    "III ROIS": "1 Reis",
    "JOB": "Jó",
    "PSAUMES": "Salmos",
    "PROVERBES": "Provérbios",
    "SAGESSE": "Sabedoria",
    "ECCLÉSIASTIQUE": "Eclesiástico",
    "ISAIE": "Isaías",
    "JÉRÉMIE": "Jeremias",
    "JONAS": "Jonas",
    "MATTH.": "Mateus",
    "LUC": "São Lucas",
    "JEAN": "São João",
    "ACTES": "Atos",
    "ROM.": "Romanos",
    "I COR.": "1 Coríntios",
    "II COR.": "2 Coríntios",
}


SECTION_FILES = {
    "toc_open": [150],
    "testament_names": [243, 244],
    "french_names": list(range(487, 496)),
    "citations": [678, 679],
    "syriac_names": list(range(680, 686)),
    "toc_close": [686, 687],
}

SECTIONS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "TABLE ANALYTIQUE",
        "heading_norm": "table analytique",
        "page_start": None,
        "page_end": None,
        "file_start_seq": 150,
        "file_end_seq": 150,
        "confidence": 0.99,
        "source_group": "toc_open",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:002",
        "section_order": 2,
        "section_kind": "onomastic_mixed",
        "heading_raw": "TABLE DES NOMS PROPRES CONTENUS DANS LE TESTAMENT",
        "heading_norm": "table des noms propres contenus dans le testament",
        "page_start": 234,
        "page_end": 234,
        "file_start_seq": 243,
        "file_end_seq": 244,
        "confidence": 0.99,
        "source_group": "testament_names",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:003",
        "section_order": 3,
        "section_kind": "onomastic_mixed",
        "heading_raw": "TABLE ALPHABÉTIQUE DES NOMS PROPRES",
        "heading_norm": "table alphabetique des noms propres",
        "page_start": 478,
        "page_end": 485,
        "file_start_seq": 487,
        "file_end_seq": 495,
        "confidence": 0.99,
        "source_group": "french_names",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:scripture_index:004",
        "section_order": 4,
        "section_kind": "scripture_index",
        "heading_raw": "TABLE DES CITATIONS",
        "heading_norm": "table des citations",
        "page_start": 668,
        "page_end": 669,
        "file_start_seq": 678,
        "file_end_seq": 679,
        "confidence": 0.98,
        "source_group": "citations",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:005",
        "section_order": 5,
        "section_kind": "onomastic_mixed",
        "heading_raw": "TABLE ALPHABÉTIQUE DES NOMS PROPRES SYRIAQUES",
        "heading_norm": "table alphabetique des noms propres syriaques",
        "page_start": 671,
        "page_end": 675,
        "file_start_seq": 680,
        "file_end_seq": 685,
        "confidence": 0.99,
        "source_group": "syriac_names",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:006",
        "section_order": 6,
        "section_kind": "analytic_subject",
        "heading_raw": "TABLE ANALYTIQUE DES MATIÈRES",
        "heading_norm": "table analytique des matieres",
        "page_start": 676,
        "page_end": 677,
        "file_start_seq": 686,
        "file_end_seq": 687,
        "confidence": 0.99,
        "source_group": "toc_close",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.translate(SUPERSCRIPT_DIGITS)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = re.sub(r"[^\wܐ-ݏ؀-ۿ']+", " ", text, flags=re.UNICODE)
    return SPACE_RE.sub(" ", text).strip().lower() or None


def roman_to_int(value: str) -> int | None:
    total = 0
    prev = 0
    for ch in reversed(value.upper()):
        cur = ROMAN_MAP.get(ch)
        if cur is None:
            return None
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    return total or None


def read_pages(pages: str) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["python", str(READER), "--volume", VOLUME_ID, "--pages", pages, "--view", "blocks", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def seq_path_map(pages: list[dict[str, Any]]) -> dict[int, str]:
    return {p["file_seq"]: str((ROOT / p["file"]).resolve()) for p in pages}


def build_printed_page_map(all_pages: list[dict[str, Any]]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    index_seqs = {seq for seqs in SECTION_FILES.values() for seq in seqs}
    for page in all_pages:
        seq = page["file_seq"]
        if seq in index_seqs:
            continue
        header = page.get("header_text") or ""
        nums = [int(n) for n in re.findall(r"(?<!\d)\d{1,3}(?!\d)", header)]
        if not nums:
            continue
        candidates = [n for n in nums if 1 <= n <= 700]
        if not candidates:
            continue
        file_path = str((ROOT / page["file"]).resolve())
        for candidate in candidates:
            page_map.setdefault(candidate, file_path)
        printed = candidates[-1] if header.lstrip().startswith("[") else candidates[0]
        page_map[printed] = file_path
    return page_map


def block_lines(pages: list[dict[str, Any]], seqs: list[int]) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    by_seq = {p["file_seq"]: p for p in pages}
    for seq in seqs:
        page = by_seq[seq]
        for block in page.get("blocks", []):
            if block.get("tipo") != "texto_principal":
                continue
            for line in (block.get("content_clean") or "").splitlines():
                line = SPACE_RE.sub(" ", line).strip()
                if line:
                    rows.append((line, seq, str((ROOT / page["file"]).resolve())))
    return rows


def is_letter_line(line: str) -> bool:
    text = line.strip()
    if len(text) == 1 and (text.isalpha() or "\u0710" <= text <= "\u074f"):
        return True
    return text in {"I", "II"} and False


def is_continuation(line: str, previous: str | None, script: str) -> bool:
    if not previous:
        return False
    if line.startswith("—") or line.startswith("-"):
        return True
    if re.match(r"^\d", line):
        return True
    if script == "latin" and re.match(r"^(de|du|des|d'|l'|la|le|les|et|à|au|aux)\b", line):
        return True
    return False


def join_entries(lines: list[tuple[str, int, str]], script: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line, seq, file in lines:
        if is_letter_line(line):
            if current:
                items.append(current)
                current = None
            items.append({"type": "node", "label": line, "file_seq": seq, "file": file})
            continue
        if is_continuation(line, current["raw"] if current else None, script):
            assert current is not None
            current["raw"] += "\n" + line
            current["file_end_seq"] = seq
            current["file_end"] = file
            continue
        if current:
            items.append(current)
        current = {"type": "entry", "raw": line, "file_seq": seq, "file": file, "file_end_seq": seq, "file_end": file}
    if current:
        items.append(current)
    return items


def lemma_from_entry(entry_raw: str) -> str:
    first = entry_raw.splitlines()[0].strip()
    first = re.split(r"\s+Cf\.|\s+V\.|\s+cf\.", first, maxsplit=1)[0]
    first = re.sub(r"\s*\.\s*\.+\s*\d.*$", "", first)
    first = re.split(r"\s(?=\d{1,3}(?:[_₀-₉]|\b))", first, maxsplit=1)[0]
    first = first.strip(" ,.;")
    if "," in first:
        return first.rsplit(",", 1)[0].strip(" ,.;") or first.strip(" ,.;")
    return first or entry_raw.splitlines()[0].strip(" ,.;")


def parse_refs(entry_raw: str, latin: bool = True) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None]] = set()
    protected = re.sub(r"\b\d{1,2}\s+[SHPN]\b", " ", entry_raw) if latin else entry_raw
    for rng in RANGE_RE.finditer(protected):
        a, b = int(rng.group("a")), int(rng.group("b"))
        if a > 700 or b > 700:
            continue
        raw = rng.group(0)
        key = (raw, a, str(b))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": a,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(a),
                "range_end_raw": str(b),
            }
        )
    for match in LINE_REF_RE.finditer(protected):
        raw = match.group(0).strip()
        page = int(match.group("page"))
        if page > 700 or any(r["ref_raw"] == raw for r in refs):
            continue
        line_raw = match.group("line")
        if line_raw:
            line_raw = line_raw.strip()
        key = (raw, page, line_raw)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_page_line" if line_raw else "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": line_raw,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def parse_citation_material_refs(entry_raw: str) -> list[dict[str, Any]]:
    line = entry_raw.splitlines()[-1]
    match = re.search(r"(?:\.\s*){2,}\s*(?P<tail>\d.*)$", line)
    if not match:
        return []
    return parse_refs(match.group("tail"), latin=True)


def parse_scripture(entry_raw: str, entry_key: str) -> dict[str, Any] | None:
    lines = [ln.strip() for ln in entry_raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    book = lines[0].strip()
    if book not in BOOKS:
        return None
    m = re.search(r"([ivxlcdmIVXLCDM]+)\s*,\s*(\d+)(?:\s*(?:,|-)\s*(\d+))?", lines[1])
    if not m:
        return None
    chapter = roman_to_int(m.group(1))
    verse = int(m.group(2))
    verse_end = int(m.group(3)) if m.group(3) else None
    raw = m.group(0)
    return {
        "entry_key": entry_key,
        "ref_order": 1,
        "ref_role": "citation",
        "ref_raw": raw,
        "book_raw": book,
        "book_norm": BOOKS[book],
        "chapter_start": chapter,
        "verse_start": verse,
        "chapter_end": chapter if verse_end else None,
        "verse_end": verse_end,
        "is_range": bool(verse_end),
        "confidence": 0.95,
        "raw_json": {"source": "scripture_index_line", "split_from": entry_raw},
    }


def parse_citation_items(lines: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_book: str | None = None
    for line, seq, file in lines:
        clean = line.strip()
        if clean in {"TABLE DES CITATIONS", "ANCIEN TESTAMENT", "NOUVEAU TESTAMENT", "AUTRES CITATIONS"}:
            continue
        if clean in BOOKS:
            current_book = clean
            continue
        if current_book and re.match(r"^(?:[IVXLCDMivxlcdm]+|—|--)\b", clean):
            items.append({"type": "entry", "raw": f"{current_book}\n{clean}", "file_seq": seq, "file": file, "file_end_seq": seq, "file_end": file})
            continue
        current_book = None
        if clean:
            items.append({"type": "entry", "raw": clean, "file_seq": seq, "file": file, "file_end_seq": seq, "file_end": file})
    return items


def helper_by_entry() -> dict[str, dict[str, Any]]:
    if not HELPER_OUTPUT_JSON.exists():
        return {}
    data = json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))
    return {item["entry_id"]: item for item in data.get("entries", [])}


def compact_helper(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    return {
        "status": item.get("status"),
        "best_candidate_file": best.get("file"),
        "best_candidate_probability": best.get("probability"),
        "reason_summary": best.get("reason_summary"),
        "candidate_role": best.get("candidate_role"),
        "top_candidates": [
            {
                "file": c.get("file"),
                "probability": c.get("probability"),
                "candidate_role": c.get("candidate_role"),
                "reason_summary": c.get("reason_summary"),
            }
            for c in (item.get("candidates") or [])[:3]
        ],
    }


def target_for_ref(ref: dict[str, Any], page_map: dict[int, str]) -> tuple[str | None, float, str]:
    page = ref.get("page_ref_int")
    if not page:
        return None, 0.0, "no_page_int"
    target = page_map.get(page)
    if target:
        return target, 0.55, "printed_page_header_map"
    return None, 0.0, "no_header_map_candidate"


def entry_id_slug(section_order: int, raw: str, order: int) -> str:
    lemma = norm_text(lemma_from_entry(raw)) or "entry"
    slug = re.sub(r"[^a-z0-9]+", "_", lemma.translate(SUPERSCRIPT_DIGITS))[:50].strip("_") or f"entry_{order}"
    return f"po009_s{section_order:02d}_{order:04d}_{slug}"


def build() -> dict[str, Any]:
    index_pages = read_pages("150,243-244,487-496,678-687")
    all_pages = read_pages("1-694")
    path_by_seq = seq_path_map(all_pages)
    page_map = build_printed_page_map(all_pages)
    helpers = helper_by_entry()

    sections: list[dict[str, Any]] = []
    for s in SECTIONS:
        sections.append(
            {
                "section_key": s["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": s["section_order"],
                "section_kind": s["section_kind"],
                "heading_raw": s["heading_raw"],
                "heading_norm": s["heading_norm"],
                "heading_letter": None,
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "file_start": path_by_seq[s["file_start_seq"]],
                "file_end": path_by_seq[s["file_end_seq"]],
                "confidence": s["confidence"],
                "raw_json": {
                    "source": f"OCR files {s['file_start_seq']}-{s['file_end_seq']}",
                    "section_kind_reason": "Heading and continuation pages inspected through read_ocr_page_text.",
                },
            }
        )

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    node_counter = 0
    entry_counter = 0

    for s in SECTIONS:
        section_key = s["section_key"]
        group = s["source_group"]
        rows = block_lines(index_pages, SECTION_FILES[group])
        script = "syriac" if group == "syriac_names" else "latin"
        items = parse_citation_items(rows) if group == "citations" else join_entries(rows, script)
        current_node_key = None
        local_order = 0
        for item in items:
            if item["type"] == "node":
                node_counter += 1
                current_node_key = f"{VOLUME_ID}:node:{node_counter:03d}"
                label = item["label"]
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_counter,
                        "node_kind": "letter_group",
                        "label_raw": label,
                        "label_norm": norm_text(label),
                        "label_sort": norm_text(label),
                        "node_level": 1,
                        "confidence": 0.92,
                        "raw_json": {"source_file": item["file"], "source_file_seq": item["file_seq"]},
                    }
                )
                continue
            raw = item["raw"]
            if group == "citations" and raw.startswith("TABLE DES CITATIONS"):
                continue
            parsed_refs = parse_citation_material_refs(raw) if group == "citations" else parse_refs(raw, latin=script == "latin")
            if not parsed_refs and group not in {"french_names", "syriac_names"}:
                continue
            local_order += 1
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            lemma = lemma_from_entry(raw)
            h_entry_id = entry_id_slug(s["section_order"], raw, local_order)
            helper = helpers.get(h_entry_id)
            helper_compact = compact_helper(helper)
            target_best = helper_compact["best_candidate_file"] if helper_compact else None
            if not target_best and parsed_refs:
                target_best = target_for_ref(parsed_refs[0], page_map)[0]
            first_page = parsed_refs[0]["page_ref_int"] if parsed_refs else None
            entry_kind = "scripture_citation" if parse_scripture(raw, entry_key) else "lemma"
            if group in {"toc_open", "toc_close"}:
                entry_kind = "heading_group"
            if re.search(r"\b(?:Cf|cf|V)\.", raw) and not parsed_refs:
                entry_kind = "cross_reference"
            confidence = 0.86 if target_best else 0.72
            raw_json: dict[str, Any] = {
                "source_file": item["file"],
                "source_file_seq": item["file_seq"],
                "source_file_end_seq": item.get("file_end_seq"),
                "entry_id_for_helper": h_entry_id,
            }
            if helper_compact:
                raw_json["helper"] = helper_compact
            elif parsed_refs:
                raw_json["locator_method"] = "printed_page_header_map"
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": current_node_key,
                    "entry_order": local_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma,
                    "lemma_display": lemma,
                    "lemma_norm": norm_text(lemma),
                    "lemma_sort": norm_text(lemma),
                    "entry_raw": raw,
                    "context_raw": None,
                    "heading_letter": nodes[-1]["label_raw"] if current_node_key and nodes and nodes[-1]["node_key"] == current_node_key else None,
                    "inferred_printed_page": first_page,
                    "section_start_file": path_by_seq[s["file_start_seq"]],
                    "editorial_anchor_file": item["file"],
                    "target_file_best": target_best,
                    "confidence": confidence,
                    "raw_json": raw_json,
                }
            )
            for ref_order, parsed in enumerate(parsed_refs, 1):
                target, prob, method = target_for_ref(parsed, page_map)
                if helper_compact and ref_order == 1 and helper_compact.get("best_candidate_file"):
                    target = helper_compact["best_candidate_file"]
                    prob = helper_compact.get("best_candidate_probability") or prob
                    method = "helper_best_candidate"
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        **parsed,
                        "target_file": target,
                        "target_file_probability": prob,
                        "section_start_file": path_by_seq[s["file_start_seq"]],
                        "editorial_anchor_file": item["file"],
                        "confidence": min(0.9, max(0.45, prob)) if target else 0.35,
                        "raw_json": {"locator_method": method, **({"helper": helper_compact} if helper_compact and ref_order == 1 else {})},
                    }
                )
            sref = parse_scripture(raw, entry_key)
            if sref:
                scripture_refs.append(sref)

    evidence_files = [path_by_seq[seq] for seqs in SECTION_FILES.values() for seq in seqs if seq in path_by_seq]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": [
                "PO009 contains multiple distinct end-matter tables; this rebuild expands the previous representative checkpoint into line-level entries from inspected OCR blocks.",
                "Target locators use helper evidence when available and otherwise a conservative printed-page header map scoped to PO009.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "expanded_ocr_recovery",
            "entries_status_reason": "Recovered line-level entries from the opening analytical table, Testament names table, later French names table, mixed citation table, Syriac names table, and concluding analytical table. Some dense wrapped Syriac/foreign-name rows remain conservative where OCR segmentation is visibly damaged.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "Section headings and continuations were confirmed through scripts/read_ocr_page_text.py.",
            "Festival-day markers such as '25 H' in the French names table are not treated as ordinary material page refs.",
            "Bare Cf./V. remissions are preserved in entry_raw and are not emitted as refs unless a material locator is also present.",
        ],
    }
    return payload


def validate(payload: dict[str, Any]) -> None:
    required = ["schema_version", "generated_at", "volume", "sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"]
    if list(payload.keys()) != required:
        raise SystemExit(f"unexpected top-level keys: {list(payload.keys())}")
    section_keys = {s["section_key"] for s in payload["sections"]}
    node_keys = {n["node_key"] for n in payload["nodes"]}
    entry_keys = {e["entry_key"] for e in payload["entries"]}
    if len(entry_keys) != len(payload["entries"]):
        raise SystemExit("duplicate entry_key")
    for e in payload["entries"]:
        if e["section_key"] not in section_keys:
            raise SystemExit(f"bad entry section: {e['entry_key']}")
        if e["parent_node_key"] and e["parent_node_key"] not in node_keys:
            raise SystemExit(f"bad parent node: {e['entry_key']}")
        if len(e["entry_raw"]) > 1500:
            raise SystemExit(f"oversized entry_raw: {e['entry_key']}")
    seen_refs: set[tuple[str, int]] = set()
    for r in payload["refs"]:
        key = (r["entry_key"], r["ref_order"])
        if key in seen_refs:
            raise SystemExit(f"duplicate ref_order: {key}")
        seen_refs.add(key)
        if r["entry_key"] not in entry_keys:
            raise SystemExit(f"bad ref entry: {r['entry_key']}")
    seen_srefs: set[tuple[str, int]] = set()
    for r in payload["scripture_refs"]:
        key = (r["entry_key"], r["ref_order"])
        if key in seen_srefs:
            raise SystemExit(f"duplicate scripture ref_order: {key}")
        seen_srefs.add(key)
        if r["entry_key"] not in entry_keys:
            raise SystemExit(f"bad scripture ref entry: {r['entry_key']}")


def main() -> None:
    payload = build()
    validate(payload)
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    for key in ["sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"]:
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PO009 expanded payload assembled and validated",
            "completed": [
                "section windows confirmed",
                "line-level entries extracted from OCR blocks",
                "refs parsed and locators mapped where available",
                "final payload written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Helper output was reused as checkpoint evidence where entry ids matched.",
                "Uncertain target files are marked with lower confidence and locator_method in raw_json.",
            ],
        },
    )
    write_json(OUTPUT_FILE, payload)
    print(json.dumps({"status": "ok", "entries": len(payload["entries"]), "refs": len(payload["refs"]), "nodes": len(payload["nodes"]), "scripture_refs": len(payload["scripture_refs"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

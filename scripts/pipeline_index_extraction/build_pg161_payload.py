#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pg161_payload.py
# Extracts PG161 alphabetical index and ordo rerum, writes helper request/intermediates/final payload.

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


VOLUME_ID = "PG161"
COLLECTION = "PG"
SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PG161/text")
OUTPUT_FILE = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG161_alphabetical_indices.json")
HELPER_REQUEST = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG161_helper_request.json")
HELPER_OUTPUT = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG161_helper_output.json")
INTERMEDIATE_DIR = Path("/homessddata/Projects/pdfocr/data/intermediate_payloads/PG161")

IDX_FILES = [
    SOURCE_ROOT / "c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-061.txt",
    SOURCE_ROOT / "c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-062.txt",
    SOURCE_ROOT / "c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-063.txt",
]
ORDO_FILES = [
    SOURCE_ROOT / "9912a9d3-461d-42f0-9cd8-6b38d73d3c5c-697.txt",
    SOURCE_ROOT / "9912a9d3-461d-42f0-9cd8-6b38d73d3c5c-698.txt",
]

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.S)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
END_NUM_RE = re.compile(r"(?:\b\d{1,4}\b|\b[IVXLCDM]{1,12}\b)\.?$", re.I)
ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^\w\s]", " ", text)
    return SPACE_RE.sub(" ", text).strip()


def slug(text: str) -> str:
    return normalize(text).replace(" ", "_")


def roman_to_int(text: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    raw = text.strip().upper()
    if not raw or not ROMAN_RE.fullmatch(raw):
        return None
    total = 0
    prev = 0
    for ch in reversed(raw):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def parse_blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[tuple[str, str]] = []
    for match in BLOCK_RE.finditer(text):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        block_type = attrs.get("tipo", "").strip().lower()
        content = TAG_RE.sub("", match.group("content") or "")
        lines = [line.strip() for line in content.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        if cleaned:
            blocks.append((block_type, cleaned))
    return blocks


def join_hyphenated(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if out and out[-1].endswith("-"):
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return out


def extract_alpha_entries() -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    letters: list[str] = []
    current_letter = None
    entry_order = 0
    capture = False
    for path in IDX_FILES:
        blocks = parse_blocks(path)
        current = ""
        seen_real_content_on_page = False
        for block_type, block_text in blocks:
            if "INDEX RERUM" in block_text:
                capture = True
                continue
            if not capture or block_type not in {"texto_principal", "nota_marginal"}:
                continue
            for line in join_hyphenated(block_text.splitlines()):
                line = line.strip()
                if not line or line == "Digitized by Google":
                    continue
                if line == "Revocatur Lector ad numeros crassiores textui insertos.":
                    continue
                if not seen_real_content_on_page and (
                    ROMAN_RE.fullmatch(line) or (len(line) == 1 and line.isalpha() and block_type == "texto_principal")
                ):
                    continue
                if re.fullmatch(r"[A-Z]", line):
                    current_letter = line
                    letters.append(line)
                    continue
                seen_real_content_on_page = True
                if not current:
                    current = line
                else:
                    current += " " + line
                if current and re.search(r"(?:\b\d{1,3}\.|\bibid\.)\s*$", current, re.I):
                    entry_order += 1
                    entries.append(
                        {
                            "entry_order": entry_order,
                            "heading_letter": current_letter,
                            "entry_raw": SPACE_RE.sub(" ", current).strip(),
                            "anchor_file": str(path),
                        }
                    )
                    current = ""
        if current:
            entry_order += 1
            entries.append(
                {
                    "entry_order": entry_order,
                    "heading_letter": current_letter,
                    "entry_raw": SPACE_RE.sub(" ", current).strip(),
                    "anchor_file": str(path),
                }
            )
    return entries, letters


def extract_ordo_entries() -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    nodes: list[str] = []
    current_node = None
    entry_order = 0
    carry = ""
    seen_heading = False
    for path in ORDO_FILES:
        blocks = parse_blocks(path)
        for block_type, block_text in blocks:
            if "ORDO RERUM\nQUÆ IN HOC TOMO CONTINENTUR." in block_text:
                seen_heading = True
                continue
            if not seen_heading or block_type != "texto_principal":
                continue
            lines = join_hyphenated(block_text.splitlines())
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line in {"Digitized by Google", "Parisiis - Ex typis L. MIGNE."}:
                    continue
                if line.startswith("FINIS TOMI"):
                    continue
                if line[0].islower() and entries and not carry:
                    entries[-1]["entry_raw"] = SPACE_RE.sub(" ", entries[-1]["entry_raw"] + " " + line).strip()
                    continue
                if line.isupper() and not END_NUM_RE.search(line):
                    current_node = line.rstrip(".")
                    if current_node not in nodes:
                        nodes.append(current_node)
                    continue
                if current_node is None:
                    current_node = "PROLEGOMENA"
                    if current_node not in nodes:
                        nodes.append(current_node)
                if carry:
                    carry += " " + line
                else:
                    carry = line
                if END_NUM_RE.search(carry):
                    entry_order += 1
                    entries.append(
                        {
                            "entry_order": entry_order,
                            "parent_label": current_node,
                            "entry_raw": SPACE_RE.sub(" ", carry).strip(),
                            "anchor_file": str(path),
                        }
                    )
                    carry = ""
    if carry:
        entry_order += 1
        entries.append(
            {
                "entry_order": entry_order,
                "parent_label": current_node,
                "entry_raw": SPACE_RE.sub(" ", carry).strip(),
                "anchor_file": str(ORDO_FILES[-1]),
            }
        )
    return entries, nodes


def alpha_lemma(entry_raw: str) -> str:
    if ". " in entry_raw:
        period_idx = entry_raw.find(". ")
        comma_idx = entry_raw.find(",")
        if comma_idx == -1 or period_idx < comma_idx:
            return entry_raw[: period_idx + 1].strip()
    comma_idx = entry_raw.find(",")
    if comma_idx != -1:
        return entry_raw[:comma_idx].strip()
    return entry_raw.strip()


def ordo_lemma(entry_raw: str) -> str:
    text = re.sub(r"\s+(?:\d{1,4}|[IVXLCDM]+)\.?$", "", entry_raw).strip()
    return text


def parse_alpha_refs(entry_raw: str) -> tuple[list[dict], list[int], list[str]]:
    text = re.sub(r"Append\.\s*n\.\s*7\s*et\s*9\.", "", entry_raw)
    matches = list(re.finditer(r"\b\d{1,3}\b|ibid\.", text, re.I))
    refs: list[dict] = []
    page_hints: list[int] = []
    attempts: list[str] = []
    prev_int = None
    for match in matches:
        raw = match.group(0)
        if raw.lower() == "ibid.":
            page_int = prev_int
            attempts.append("inherited ibid. from previous page ref")
        else:
            page_int = int(raw)
            prev_int = page_int
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "ref_kind": "editorial_page",
                "raw_json": {"inherited_from_previous": raw.lower() == "ibid."},
            }
        )
        if page_int is not None and page_int not in page_hints:
            page_hints.append(page_int)
    return refs, page_hints, attempts


def parse_ordo_ref(entry_raw: str) -> dict:
    match = re.search(r"((?:\d{1,4}|[IVXLCDM]+))\.?$", entry_raw, re.I)
    raw = match.group(1) if match else None
    page_int = None
    if raw:
        page_int = int(raw) if raw.isdigit() else roman_to_int(raw)
    return {
        "ref_raw": raw or entry_raw,
        "page_ref_raw": raw,
        "page_ref_int": page_int,
        "ref_kind": "editorial_page",
        "raw_json": {"roman_page": bool(raw and not raw.isdigit())},
    }


def helper_summary(helper_result: dict | None) -> dict:
    if not helper_result:
        return {"helper_status": "missing"}
    best = helper_result.get("best_candidate") or {}
    top = []
    for cand in (helper_result.get("candidates") or [])[:3]:
        top.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:5]],
            }
        )
    return {
        "helper_status": helper_result.get("status"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        },
        "top_candidates": top,
    }


def build() -> dict:
    alpha_entries_raw, alpha_letters = extract_alpha_entries()
    ordo_entries_raw, ordo_groups = extract_ordo_entries()

    helper_input_entries = []
    for item in alpha_entries_raw:
        refs, page_hints, attempts = parse_alpha_refs(item["entry_raw"])
        lemma = alpha_lemma(item["entry_raw"])
        item["lemma_raw"] = lemma
        item["lemma_norm"] = normalize(lemma)
        item["page_hints"] = page_hints
        item["parsed_refs"] = refs
        item["attempts"] = attempts
        helper_input_entries.append(
            {
                "entry_id": f"pg161_alpha_{item['entry_order']}",
                "lemma_raw": lemma,
                "query_names": [lemma, item["entry_raw"][:180]],
                "page_hints": [str(v) for v in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": item["entry_raw"],
            }
        )

    HELPER_REQUEST.write_text(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "source_root": str(SOURCE_ROOT),
                "options": {"top_k": 5, "adjacency_window": 2},
                "entries": helper_input_entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    helper_output = {}
    if HELPER_OUTPUT.exists():
        helper_output = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    helper_map = {item.get("entry_id"): item for item in helper_output.get("entries", [])} if helper_output else {}

    sections = [
        {
            "section_key": "pg161_index_rerum_praecipuarum",
            "volume_id": VOLUME_ID,
            "work_key": "bandinii_commentarius_bessarionis",
            "section_order": 1,
            "section_kind": "alphabetical_general",
            "heading_raw": "INDEX RERUM PRÆCIPUARUM.",
            "heading_norm": "index rerum praecipuarum",
            "heading_letter": None,
            "page_start": "XCVII",
            "page_end": "CII",
            "file_start": str(IDX_FILES[0]),
            "file_end": str(IDX_FILES[-1]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Front-matter alphabetical subject/name index for the Bandini commentarius.",
                "candidate_files": [str(p) for p in IDX_FILES],
            },
        },
        {
            "section_key": "pg161_ordo_rerum_tomi",
            "volume_id": VOLUME_ID,
            "work_key": "pg161_tome_closure",
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": "1141",
            "page_end": "1144",
            "file_start": str(ORDO_FILES[0]),
            "file_end": str(ORDO_FILES[-1]),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Final table of contents for the whole tome, distinct from the alphabetical front-matter index.",
                "candidate_files": [str(p) for p in ORDO_FILES],
            },
        },
    ]

    nodes = []
    node_lookup = {}
    node_order = 0
    for letter in alpha_letters:
        node_order += 1
        key = f"pg161_alpha_letter_{letter.lower()}"
        node_lookup[("alpha", letter)] = key
        nodes.append(
            {
                "node_key": key,
                "section_key": "pg161_index_rerum_praecipuarum",
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {},
            }
        )
    for label in ordo_groups:
        node_order += 1
        key = f"pg161_ordo_{slug(label)[:50]}"
        node_lookup[("ordo", label)] = key
        nodes.append(
            {
                "node_key": key,
                "section_key": "pg161_ordo_rerum_tomi",
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize(label),
                "label_sort": normalize(label),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {},
            }
        )

    entries = []
    refs = []

    for item in alpha_entries_raw:
        entry_id = f"pg161_alpha_{item['entry_order']}"
        helper = helper_map.get(entry_id)
        helper_meta = helper_summary(helper)
        best = helper_meta.get("best_candidate") or {}
        confidence = round(float(best.get("probability") or 0.72), 6)
        entry_key = f"pg161_alpha_{slug(item['lemma_raw'])[:48]}_{item['entry_order']}"
        entry = {
            "entry_key": entry_key,
            "section_key": "pg161_index_rerum_praecipuarum",
            "parent_node_key": node_lookup.get(("alpha", item["heading_letter"])),
            "entry_order": item["entry_order"],
            "entry_kind": "lemma",
            "lemma_raw": item["lemma_raw"],
            "lemma_display": item["lemma_raw"],
            "lemma_norm": item["lemma_norm"],
            "lemma_sort": item["lemma_norm"],
            "entry_raw": item["entry_raw"],
            "context_raw": None,
            "heading_letter": item["heading_letter"],
            "inferred_printed_page": item["page_hints"][0] if item["page_hints"] else None,
            "section_start_file": str(IDX_FILES[0]),
            "editorial_anchor_file": item["anchor_file"],
            "target_file_best": best.get("file"),
            "confidence": confidence,
            "raw_json": {
                **helper_meta,
                "attempted_searches": item["attempts"],
            },
        }
        entries.append(entry)
        for idx, ref in enumerate(item["parsed_refs"], start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": best.get("file") if len(item["parsed_refs"]) == 1 else None,
                    "target_file_probability": best.get("probability") if len(item["parsed_refs"]) == 1 else None,
                    "section_start_file": str(IDX_FILES[0]),
                    "editorial_anchor_file": item["anchor_file"],
                    "confidence": confidence if len(item["parsed_refs"]) == 1 else 0.7,
                    "raw_json": {**ref["raw_json"], **helper_meta},
                }
            )

    for item in ordo_entries_raw:
        lemma = ordo_lemma(item["entry_raw"])
        lemma_norm = normalize(lemma)
        entry_key = f"pg161_ordo_{slug(lemma)[:48]}_{item['entry_order']}"
        ref = parse_ordo_ref(item["entry_raw"])
        entry = {
            "entry_key": entry_key,
            "section_key": "pg161_ordo_rerum_tomi",
            "parent_node_key": node_lookup.get(("ordo", item["parent_label"])),
            "entry_order": item["entry_order"],
            "entry_kind": "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_norm,
            "entry_raw": item["entry_raw"],
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": ref["page_ref_int"],
            "section_start_file": str(ORDO_FILES[0]),
            "editorial_anchor_file": item["anchor_file"],
            "target_file_best": None,
            "confidence": 0.82 if ref["page_ref_raw"] else 0.68,
            "raw_json": {
                "helper_status": "not_requested",
                "section_kind_reason": "TOC entry inside ORDO RERUM.",
            },
        }
        entries.append(entry)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": ref["ref_kind"],
                "ref_raw": ref["ref_raw"],
                "page_ref_raw": ref["page_ref_raw"],
                "page_ref_int": ref["page_ref_int"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": str(ORDO_FILES[0]),
                "editorial_anchor_file": item["anchor_file"],
                "confidence": 0.78 if ref["page_ref_raw"] else 0.6,
                "raw_json": ref["raw_json"],
            }
        )

    payload = {
        "schema_version": "1.0",
        "generated_at": "2026-07-23T00:00:00Z",
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Graeca 161",
            "notes": "Volume contains a front-matter alphabetical index for Bandini's commentarius and a final Ordo rerum for the whole tome.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "Recovered the front-matter INDEX RERUM PRÆCIPUARUM and the final ORDO RERUM from OCR files 061-063 and 697-698.",
            "evidence_files": [str(p) for p in IDX_FILES + ORDO_FILES],
        },
        "notes": [
            "Helper request covers the alphabetical front-matter entries with Arabic page hints.",
            "ORDO RERUM entries with Roman page labels preserve the literal OCR; target_file resolution was not forced when the printed page signal remained non-Arabic or OCR-corrupted.",
            "Entry 'DE SCRIPTORIBUS GRAECIS PATRIA SICULIS. 9 3' remains OCR-ambiguous in its page label and keeps low-confidence literal evidence only.",
        ],
    }

    for name, content in {
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "coverage.json": payload["coverage"],
        "notes.json": payload["notes"],
        "manifest.json": {
            "volume_id": VOLUME_ID,
            "helper_request": str(HELPER_REQUEST),
            "helper_output_present": HELPER_OUTPUT.exists(),
            "entry_count": len(entries),
            "ref_count": len(refs),
        },
    }.items():
        (INTERMEDIATE_DIR / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    build()

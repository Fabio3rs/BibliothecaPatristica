#!/usr/bin/env python3
"""Build the PL173 alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/pl173_build_payload.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL173/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL173_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL173_helper_output.json"
SECTION_FILES = {
    768: SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-768.txt",
    769: SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-769.txt",
    770: SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-770.txt",
}


def norm_text(text: str) -> str:
    text = text.lower()
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[’'\".,;:()\\[\\]{}]", " ", text)
    text = re.sub(r"[^0-9a-zà-ÿ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def trim_page(text: str) -> tuple[str, int | None]:
    m = re.search(r"^(.*?)(?:\s+)(\d{1,4})\s*$", text)
    if not m:
        return text.strip(), None
    return m.group(1).strip(), int(m.group(2))


def extract_principal_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', raw, flags=re.S)
    lines: list[str] = []
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("<") or line.startswith("["):
                continue
            lines.append(line)
    return lines


def load_helper_map() -> dict[tuple[str, int | None], dict]:
    if not HELPER_OUTPUT.exists():
        return {}
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    out: dict[tuple[str, int | None], dict] = {}
    for entry in data.get("entries", []):
        key = (entry.get("entry_id"), None)
        out[key] = entry
    return out


def best_helper_match(helper_data: dict[tuple[str, int | None], dict], lemma_norm: str, page: int | None) -> dict | None:
    # Match the curated helper examples by a stable page + lemma prefix.
    if page == 9 and lemma_norm.startswith("notitia historica et litteraria"):
        return helper_data.get(("pl173_toc_notitia_historia", None))
    if page == 113 and lemma_norm.startswith("gestorum abbatum trudonensium continuatio prima"):
        return helper_data.get(("pl173_toc_gestorum_continuatio_prima", None))
    if page == 1411 and lemma_norm.startswith("stephani parisiensis episcopi"):
        return helper_data.get(("pl173_toc_stephani_parisiensis", None))
    if page == 1429 and lemma_norm.startswith("historia mediolanensis"):
        return helper_data.get(("pl173_toc_historia_mediolanensis", None))
    if page == 1545 and lemma_norm.startswith("lotharius papiensibus fusis mediolanum venit"):
        return helper_data.get(("pl173_toc_lotharius_fusis", None))
    return None


def build_page_anchor_map() -> list[tuple[int, str]]:
    # Anchor points taken from direct OCR inspection and exact page-header searches.
    return [
        (9, str(SOURCE_ROOT / "5c498f63-6301-4ce4-b7d1-0da4402abc1b-012.txt")),
        (113, str(SOURCE_ROOT / "5c498f63-6301-4ce4-b7d1-0da4402abc1b-065.txt")),
        (193, str(SOURCE_ROOT / "b9640cb9-074f-4164-ba7b-c710cb0c8138-103.txt")),
        (209, str(SOURCE_ROOT / "5c498f63-6301-4ce4-b7d1-0da4402abc1b-058.txt")),
        (219, str(SOURCE_ROOT / "b9640cb9-074f-4164-ba7b-c710cb0c8138-116.txt")),
        (269, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-141.txt")),
        (327, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-170.txt")),
        (333, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-173.txt")),
        (337, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-160.txt")),
        (339, str(SOURCE_ROOT / "b9640cb9-074f-4164-ba7b-c710cb0c8138-089.txt")),
        (351, str(SOURCE_ROOT / "18a05869-a16c-4dd8-be36-d3881a4c5765-424.txt")),
        (353, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-183.txt")),
        (355, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-184.txt")),
        (385, str(SOURCE_ROOT / "4e173018-6de3-4db0-8357-d85c74953180-199.txt")),
        (423, str(SOURCE_ROOT / "4e173018-6de3-4db0-8357-d85c74953180-218.txt")),
        (431, str(SOURCE_ROOT / "4e173018-6de3-4db0-8357-d85c74953180-222.txt")),
        (433, str(SOURCE_ROOT / "0d1d3814-a07a-4289-981b-5740ee9847f9-134.txt")),
        (439, str(SOURCE_ROOT / "6d900ab0-0a11-4a68-8a7d-0f137eb9a2a2-374.txt")),
        (493, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-493.txt")),
        (497, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-497.txt")),
        (503, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-503.txt")),
        (505, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-505.txt")),
        (508, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-508.txt")),
        (510, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-510.txt")),
        (511, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-511.txt")),
        (514, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-514.txt")),
        (515, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-515.txt")),
        (517, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-517.txt")),
        (520, str(SOURCE_ROOT / "bb927e4a-df00-43b9-b6e6-d7376ab8df73-520.txt")),
        (521, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-521.txt")),
        (522, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-522.txt")),
        (523, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-523.txt")),
        (524, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-524.txt")),
        (527, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-527.txt")),
        (567, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-567.txt")),
        (570, str(SOURCE_ROOT / "ee129f6e-2014-46cc-91ab-a6091fa93128-570.txt")),
        (627, str(SOURCE_ROOT / "970de513-4b7a-4246-a929-3103236be7a6-627.txt")),
        (658, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-658.txt")),
        (659, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-659.txt")),
        (660, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-660.txt")),
        (661, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-661.txt")),
        (663, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-663.txt")),
        (672, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-672.txt")),
        (680, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-680.txt")),
        (681, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-681.txt")),
        (682, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-682.txt")),
        (683, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-683.txt")),
        (686, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-686.txt")),
        (1411, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-702.txt")),
        (1413, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-703.txt")),
        (1419, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-706.txt")),
        (1421, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-707.txt")),
        (1429, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-713.txt")),
        (1437, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-715.txt")),
        (1445, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-719.txt")),
        (1447, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-720.txt")),
        (1450, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-721.txt")),
        (1452, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-726.txt")),
        (1457, str(SOURCE_ROOT / "8ee439e9-a5c9-414f-8b59-b20ef178626f-686.txt")),
        (1473, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-731.txt")),
        (1478, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-733.txt")),
        (1483, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-736.txt")),
        (1490, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-739.txt")),
        (1495, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-742.txt")),
        (1499, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-742.txt")),
        (1501, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-745.txt")),
        (1503, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-747.txt")),
        (1509, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-749.txt")),
        (1510, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-749.txt")),
        (1511, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-750.txt")),
        (1516, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-752.txt")),
        (1518, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-753.txt")),
        (1522, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-755.txt")),
        (1525, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-757.txt")),
        (1529, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-759.txt")),
        (1533, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-761.txt")),
        (1537, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-763.txt")),
        (1539, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-764.txt")),
        (1545, str(SOURCE_ROOT / "a9738359-51bd-42b2-952e-b0ba08f27f48-766.txt")),
    ]


def target_for_page(page: int, anchors: list[tuple[int, str]]) -> str:
    best = anchors[0][1]
    for anchor_page, file_path in anchors:
        if page >= anchor_page:
            best = file_path
        else:
            break
    return best


def build_nodes() -> list[dict]:
    section_key = "PL173:alpha:ordo_rerum:001"
    nodes = [
        {
            "node_key": "PL173:node:001",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "RODULPHUS ABBAS S. TRUDONIS.",
            "label_norm": norm_text("RODULPHUS ABBAS S. TRUDONIS."),
            "label_sort": norm_text("RODULPHUS ABBAS S. TRUDONIS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "RODULPHUS ABBAS S. TRUDONIS.", "section_kind": "ordo_rerum"},
        },
        {
            "node_key": "PL173:node:002",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "LEO MARSICANUS ET PETRUS DIACONUS.",
            "label_norm": norm_text("LEO MARSICANUS ET PETRUS DIACONUS."),
            "label_sort": norm_text("LEO MARSICANUS ET PETRUS DIACONUS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "LEO MARSICANUS ET PETRUS DIACONUS.", "section_kind": "ordo_rerum"},
        },
        {
            "node_key": "PL173:node:003",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 3,
            "node_kind": "heading_group",
            "label_raw": "LANDULPHUS JUNIOR.",
            "label_norm": norm_text("LANDULPHUS JUNIOR."),
            "label_sort": norm_text("LANDULPHUS JUNIOR."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "LANDULPHUS JUNIOR.", "section_kind": "ordo_rerum"},
        },
        {
            "node_key": "PL173:node:004",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 4,
            "node_kind": "heading_group",
            "label_raw": "FALCO BENEVENTANUS.",
            "label_norm": norm_text("FALCO BENEVENTANUS."),
            "label_sort": norm_text("FALCO BENEVENTANUS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "FALCO BENEVENTANUS.", "section_kind": "ordo_rerum"},
        },
        {
            "node_key": "PL173:node:005",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 5,
            "node_kind": "heading_group",
            "label_raw": "MATTHÆUS CARDINALIS.",
            "label_norm": norm_text("MATTHÆUS CARDINALIS."),
            "label_sort": norm_text("MATTHÆUS CARDINALIS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "MATTHÆUS CARDINALIS.", "section_kind": "ordo_rerum"},
        },
        {
            "node_key": "PL173:node:006",
            "section_key": section_key,
            "parent_node_key": None,
            "node_order": 6,
            "node_kind": "heading_group",
            "label_raw": "S. OTTO BAMBERGENSIS EPISCOPUS.",
            "label_norm": norm_text("S. OTTO BAMBERGENSIS EPISCOPUS."),
            "label_sort": norm_text("S. OTTO BAMBERGENSIS EPISCOPUS."),
            "node_level": 1,
            "confidence": 0.98,
            "raw_json": {"source_token": "S. OTTO BAMBERGENSIS EPISCOPUS.", "section_kind": "ordo_rerum"},
        },
    ]
    return nodes


def main() -> None:
    helper_map = load_helper_map()
    anchors = build_page_anchor_map()
    section_file = str(SECTION_FILES[768])

    lines: list[tuple[int, str, str]] = []
    for seq, path in SECTION_FILES.items():
        for line in extract_principal_lines(path):
            lines.append((seq, str(path), line))

    skip_headings = {
        "RODULPHUS ABBAS S. TRUDONIS.",
        "LEO MARSICANUS ET PETRUS DIACONUS.",
        "LANDULPHUS JUNIOR.",
        "FALCO BENEVENTANUS.",
        "MATTHÆUS CARDINALIS.",
        "S. OTTO BAMBERGENSIS EPISCOPUS.",
    }

    entries = []
    refs = []
    entry_order = 0
    buffer_lines: list[str] = []
    buffer_file: str | None = None
    buffer_seq: int | None = None

    def flush_buffer() -> None:
        nonlocal entry_order, buffer_lines, buffer_file, buffer_seq
        if not buffer_lines:
            return
        raw = " ".join(s.strip() for s in buffer_lines)
        raw = re.sub(r"\s+", " ", raw).strip()
        lemma, page = trim_page(raw)
        if page is None:
            buffer_lines = []
            buffer_file = None
            buffer_seq = None
            return
        entry_order += 1
        lemma_display = lemma
        lemma_norm = norm_text(lemma_display)
        target_file = target_for_page(page, anchors)
        helper_entry = best_helper_match(helper_map, lemma_norm, page)
        confidence = 0.93
        if helper_entry and helper_entry.get("status") == "resolved":
            confidence = max(confidence, float(helper_entry["best_candidate"]["probability"]))
        elif target_file == section_file:
            confidence = 0.76
        entry_key = f"PL173:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": "PL173:alpha:ordo_rerum:001",
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": "heading_group",
            "lemma_raw": lemma,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_norm,
            "entry_raw": raw,
            "context_raw": raw,
            "heading_letter": None,
            "inferred_printed_page": page,
            "section_start_file": section_file,
            "editorial_anchor_file": buffer_file,
            "target_file_best": target_file,
            "confidence": round(confidence, 6),
            "raw_json": {
                "source_token": raw,
                "source_file_seq": buffer_seq,
                "section_kind": "ordo_rerum",
                "target_file_method": "anchor_floor",
                "target_file_anchor_page": max(a for a, f in anchors if page >= a),
            },
        }
        if helper_entry:
            entry["raw_json"].update(
                {
                    "helper_status": helper_entry.get("status"),
                    "helper_candidate_role": helper_entry.get("best_candidate", {}).get("candidate_role"),
                    "helper_reason_summary": helper_entry.get("best_candidate", {}).get("reason_summary"),
                    "helper_best_candidate": helper_entry.get("best_candidate"),
                }
            )
            if helper_entry.get("best_candidate", {}).get("file") != target_file:
                entry["raw_json"]["helper_override_reason"] = (
                    "OCR title line is the contents entry, but the referenced printed page is anchored elsewhere."
                )
        entries.append(entry)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(page),
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": round(confidence, 6),
                "section_start_file": section_file,
                "editorial_anchor_file": buffer_file,
                "confidence": round(confidence, 6),
                "raw_json": {
                    "source_token": raw,
                    "source_file_seq": buffer_seq,
                    "section_kind": "ordo_rerum",
                },
            }
        )
        buffer_lines = []
        buffer_file = None
        buffer_seq = None

    for seq, file_path, line in lines:
        if line in skip_headings:
            continue
        if line.startswith("RODULPHUS ABBAS S. TRUDONIS") or line.startswith("LEO MARSICANUS ET PETRUS DIACONUS") or line.startswith("LANDULPHUS JUNIOR") or line.startswith("FALCO BENEVENTANUS") or line.startswith("MATTHÆUS CARDINALIS") or line.startswith("S. OTTO BAMBERGENSIS EPISCOPUS"):
            continue
        buffer_lines.append(line)
        buffer_file = file_path
        buffer_seq = seq
        if re.search(r"\d{1,4}\s*$", line):
            flush_buffer()

    if buffer_lines:
        flush_buffer()

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "volume": {
            "volume_id": "PL173",
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PL173",
            "notes": [
                "The recoverable tail material is an ordo rerum / contents table, not an alphabetical lemma index.",
                "Printed pages and OCR file suffixes are preserved as distinct numbering systems.",
            ],
        },
        "sections": [
            {
                "section_key": "PL173:alpha:ordo_rerum:001",
                "volume_id": "PL173",
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": norm_text("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."),
                "heading_letter": None,
                "page_start": 1347,
                "page_end": 1552,
                "file_start": str(SECTION_FILES[768]),
                "file_end": str(SECTION_FILES[770]),
                "confidence": 0.99,
                "raw_json": {
                    "running_title": "ORDO RERUM",
                    "section_kind_reason": "The recoverable material is a table of contents / ordo rerum rather than a lemma index.",
                    "helper_used": True,
                },
            }
        ],
        "nodes": build_nodes(),
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the full ORDO RERUM contents list from OCR files 768-770, with helper-backed anchors and conservative page/file mapping.",
            "evidence_files": [str(SECTION_FILES[768]), str(SECTION_FILES[769]), str(SECTION_FILES[770])],
        },
        "notes": [
            "This volume closes with an editorial contents table rather than an alphabetical lemma index, so the extracted section is modeled as ordo_rerum.",
            "Page anchors were resolved conservatively from the TOC entries, neighboring OCR pages, and the helper output for representative ambiguous cases.",
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

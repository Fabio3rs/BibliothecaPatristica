#!/usr/bin/env python3
"""Build PG001 Ordo Rerum payload from OCR.

Usage:
  python scripts/pipeline_index_extraction/build_pg001_ordo_payload.py

The script reads the PG001 closing Ordo Rerum OCR pages, merges wrapped lines
and line-break hyphenation, reuses index_target_locator helper evidence when
available, writes volume intermediates, and emits the canonical payload.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG001"
SOURCE_ROOT = ROOT / "teste" / "PG001" / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
OUTPUT_FILE = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data" / "alphabetical_index_payloads" / "PG001_helper_output.json"
HELPER_REQUEST = ROOT / "data" / "alphabetical_index_payloads" / "PG001_helper_request.json"
FALLBACK_HELPER_OUTPUT = INTERMEDIATE_DIR / "helper_output.json"

SECTION_KEY = "PG001:section:ordo_rerum:001"
SECTION_START = SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-739.txt"
SECTION_END = SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-742.txt"
OCR_FILES = [
    SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-739.txt",
    SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-740.txt",
    SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-741.txt",
    SOURCE_ROOT / "15dc9aa3-74cf-48d1-9da8-1e58e135b3f7-742.txt",
]

PAGE_RE = re.compile(r"(?<!\d)(\d{2,4})\s*$")
CAP_RE = re.compile(r"^(?:CAP|Cap)\.\s+[IVXLCDM]+\.?\b")
LIBER_RE = re.compile(r"^LIBER\s+[A-Z]+\.?\b")
ARTICLE_RE = re.compile(r"^(?:Articulus|§|Epist\.|EPISTOLA|Argumentum\.|Index capitum\.|Incipit epistola\.)")
MAJOR_HEADING_RE = re.compile(
    r"^(?:S\. CLEMENTIS|CONSTITUTIONES|EPISTOLÆ|RECOGNITIONES|Canones apostolici|Fragmenta\.|"
    r"Monitum|Proœmia\.|Judicium|Adnotatio|Dissertatio|D\. |Gallandii|Veterum|Præfatio|Liber\s)"
)


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    lowered = text.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = lowered.replace("quæ", "quae")
    return lowered


def sort_key(text: str | None) -> str | None:
    return norm(text)


def clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def logical_join(base: str, continuation: str) -> str:
    base = base.rstrip()
    continuation = continuation.strip()
    if base.endswith("-"):
        return base[:-1] + continuation
    return f"{base} {continuation}".strip()


def extract_text_lines(path: Path) -> list[dict]:
    rows: list[dict] = []
    in_text = False
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith('<bloco tipo="texto_principal"'):
            in_text = True
            continue
        if in_text and stripped.startswith("</bloco>"):
            in_text = False
            continue
        if not in_text or not stripped or stripped.startswith("<"):
            continue
        rows.append({"file": str(path), "line_no": line_no, "text": stripped})
    return rows


def starts_logical_item(text: str) -> bool:
    return bool(CAP_RE.match(text) or LIBER_RE.match(text) or ARTICLE_RE.match(text) or MAJOR_HEADING_RE.match(text))


def page_ref(text: str) -> int | None:
    match = PAGE_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def lemma_from_entry(entry_raw: str) -> str:
    return PAGE_RE.sub("", entry_raw).strip()


def query_names_from_lemma(lemma: str) -> list[str]:
    names = [lemma]
    without_enum = re.sub(r"^(?:CAP|Cap)\.\s+[IVXLCDM]+\.?\s*[—-]\s*", "", lemma).strip()
    without_liber = re.sub(r"^LIBER\s+[A-Z]+\.?\s*[—-]\s*", "", lemma).strip()
    for candidate in (without_enum, without_liber):
        if candidate and candidate not in names:
            names.append(candidate)
    return names[:3]


def classify_heading(text: str) -> str | None:
    if CAP_RE.match(text):
        return None
    if LIBER_RE.match(text):
        return "liber"
    if re.match(r"^(?:S\. CLEMENTIS|CONSTITUTIONES|EPISTOLÆ|RECOGNITIONES)", text):
        return "major"
    return None


def parse_items() -> tuple[list[dict], list[dict]]:
    rows = [row for path in OCR_FILES for row in extract_text_lines(path)]
    nodes: list[dict] = []
    entries: list[dict] = []
    current: dict | None = None
    parent_node_key: str | None = None
    last_major_node_key: str | None = None

    def flush_current() -> None:
        nonlocal current, parent_node_key, last_major_node_key
        if not current:
            return
        text = clean_ws(current["text"])
        current["text"] = text
        ref = page_ref(text)
        if ref is not None:
            current["page_ref"] = ref
            current["parent_node_key"] = parent_node_key
            entries.append(current)
        else:
            kind = classify_heading(text)
            if kind:
                node_key = f"PG001:node:{len(nodes) + 1:03d}"
                node_level = 1 if kind == "major" else 2
                parent = None if node_level == 1 else last_major_node_key
                node = {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": parent,
                    "node_order": len(nodes) + 1,
                    "node_kind": "heading_group",
                    "label_raw": text,
                    "label_norm": norm(text),
                    "label_sort": sort_key(text),
                    "node_level": node_level,
                    "confidence": 0.96,
                    "raw_json": {
                        "source_file": current["source_file"],
                        "line_index": current["line_no"],
                        "line_numbers": current["line_numbers"],
                        "node_level_reason": "major_contents_heading" if node_level == 1 else "subheading_within_contents",
                    },
                }
                nodes.append(node)
                if node_level == 1:
                    last_major_node_key = node_key
                    parent_node_key = node_key
                else:
                    parent_node_key = node_key
        current = None

    for row in rows:
        text = row["text"]
        if current is None:
            current = {
                "text": text,
                "source_file": row["file"],
                "line_no": row["line_no"],
                "line_numbers": [row["line_no"]],
                "source_files": [row["file"]],
            }
            if page_ref(text) is not None:
                flush_current()
            continue

        if page_ref(current["text"]) is not None:
            flush_current()

        if current and starts_logical_item(text) and not current["text"].rstrip().endswith("-"):
            flush_current()

        if current is None:
            current = {
                "text": text,
                "source_file": row["file"],
                "line_no": row["line_no"],
                "line_numbers": [row["line_no"]],
                "source_files": [row["file"]],
            }
        else:
            current["text"] = logical_join(current["text"], text)
            current["line_numbers"].append(row["line_no"])
            if row["file"] not in current["source_files"]:
                current["source_files"].append(row["file"])

        if current and page_ref(current["text"]) is not None:
            flush_current()

    flush_current()
    return nodes, entries


def load_helper() -> list[dict]:
    path = HELPER_OUTPUT if HELPER_OUTPUT.exists() else FALLBACK_HELPER_OUTPUT
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("entries", [])


def helper_summary(helper: dict | None) -> dict:
    if not helper:
        return {}
    best = helper.get("best_candidate") or {}
    candidates = helper.get("candidates") or []
    return {
        "helper_status": helper.get("status"),
        "helper_reason_summary": best.get("reason_summary"),
        "helper_best_candidate": best or None,
        "helper_candidates_top": [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
            }
            for cand in candidates[:3]
        ],
    }


def build_payload() -> dict:
    nodes, parsed_entries = parse_items()
    helpers = load_helper()
    entries = []
    refs = []
    for idx, item in enumerate(parsed_entries, start=1):
        helper = helpers[idx - 1] if idx - 1 < len(helpers) else None
        best = (helper or {}).get("best_candidate") or {}
        helper_status = (helper or {}).get("status")
        confidence = 0.93 if helper_status == "resolved" else 0.79 if helper_status == "ambiguous" else 0.72
        entry_key = f"PG001:entry:{idx:04d}"
        entry_raw = item["text"]
        lemma_raw = lemma_from_entry(entry_raw)
        source_file = item["source_files"][0]
        raw_json = {
            "source_file": source_file,
            "line_index": item["line_no"],
            "line_numbers": item["line_numbers"],
            "source_files": item["source_files"],
            "line_class": "entry_line",
            "section_kind": "ordo_rerum",
            "page_ref_source": str(item["page_ref"]),
            "hyphenation_policy": "OCR line-break hyphens were joined to the following line and removed.",
        }
        raw_json.update(helper_summary(helper))
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": item.get("parent_node_key"),
                "entry_order": idx,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": norm(lemma_raw),
                "lemma_sort": sort_key(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": item["page_ref"],
                "section_start_file": str(SECTION_START),
                "editorial_anchor_file": source_file,
                "target_file_best": best.get("file"),
                "confidence": confidence,
                "raw_json": raw_json,
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(item["page_ref"]),
                "page_ref_raw": str(item["page_ref"]),
                "page_ref_int": item["page_ref"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": best.get("file"),
                "target_file_probability": best.get("probability"),
                "section_start_file": str(SECTION_START),
                "editorial_anchor_file": source_file,
                "confidence": confidence,
                "raw_json": raw_json,
            }
        )

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    evidence_files = [str(path) for path in OCR_FILES]
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Graeca 1",
        },
        "sections": [
            {
                "section_key": SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": "ordo rerum quae in hoc tomo continentur",
                "heading_letter": None,
                "page_start": 1477,
                "page_end": 1484,
                "file_start": str(SECTION_START),
                "file_end": str(SECTION_END),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": "closing_ordo_rerum_table_of_contents recovered from files 739-742; file 738 is preceding addenda content, not part of the contents table.",
                    "evidence_files": evidence_files,
                    "heading_evidence": [{"file": str(SECTION_END), "text": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."}],
                    "ocr_note": "File 739 header OCR reads 4477/4478; context and neighboring headers indicate it is the 1477/1478 contents page.",
                    "rerun_note": "Rebuilt from OCR to remove terminal hyphen and split-word artifacts in prior checkpoint.",
                },
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "Closing Ordo Rerum line items were recovered from OCR files 739-742 with wrapped entries merged.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "PG001 contains a closing Ordo Rerum/table of contents, not an alphabetical subject index.",
            "OCR line-break hyphenation was resolved during this rerun; no terminal split-word artifacts are intentionally preserved.",
            "The page numbers in refs are cited editorial pages, distinct from OCR file suffixes.",
        ],
    }


def build_helper_request(payload: dict) -> dict:
    helper_entries = []
    for idx, entry in enumerate(payload["entries"], start=1):
        page = entry.get("inferred_printed_page")
        if page is None:
            continue
        lemma = entry.get("lemma_raw") or entry.get("entry_raw") or ""
        helper_entries.append(
            {
                "entry_id": f"pg001_{idx:04d}",
                "lemma_raw": lemma,
                "query_names": query_names_from_lemma(lemma),
                "page_hints": [str(page)],
                "page_hint_ints": [page],
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    helper_request = build_helper_request(payload)
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "items.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "payload.json", payload)
    write_json(INTERMEDIATE_DIR / "helper_request.json", helper_request)
    write_json(HELPER_REQUEST, helper_request)
    write_json(OUTPUT_FILE, payload)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": payload["generated_at"],
        "current_focus": "PG001 Ordo Rerum payload rebuilt and ready for validation.",
        "completed": [
            "confirmed files 739-742 as closing Ordo Rerum",
            "rebuilt entries from OCR logical lines",
            "removed OCR line-break hyphen artifacts",
            "wrote final payload",
        ],
        "pending": ["validate final payload"],
        "blocked": [],
        "notes": [
            "Helper evidence was reused by sequential material entry order.",
            "File 738 remains excluded as preceding addenda/closure material.",
        ],
    }
    write_json(INTERMEDIATE_DIR / "todo.json", todo)
    print(json.dumps({"entries": len(payload["entries"]), "refs": len(payload["refs"]), "nodes": len(payload["nodes"])}))


if __name__ == "__main__":
    main()

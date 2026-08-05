#!/usr/bin/env python3
"""Usage: build the PL076 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl076_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL076/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL076_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL076_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL076 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL076_alphabetical_indices.json
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


VOLUME_ID = "PL076"
COLLECTION = "PL"

SECTION_DEFS = [
    {
        "section_key": "PL076:alpha:onomastic_mixed:001",
        "section_order": 1,
        "section_kind": "onomastic_mixed",
        "heading_raw": "INDEX IN TRIPLICEM S. GREGORII MAGNI VITAM.",
        "file_start": 665,
        "file_end": 679,
        "start_after_line": 37,
        "stop_before_line": None,
        "section_kind_reason": "Biographical/onomastic index for the triple life of St. Gregory, with mostly personal names and related historical notices.",
    },
    {
        "section_key": "PL076:alpha:analytic_subject:002",
        "section_order": 2,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX IN LIBROS MORALIUM ET HOMILIAS S. GREGORII.",
        "file_start": 680,
        "file_end": 767,
        "start_after_line": 4,
        "stop_before_line": 18,
        "section_kind_reason": "Main subject index for the Moralium and Homiliae volumes, with dense alphabetical subject entries and cross-references.",
    },
    {
        "section_key": "PL076:alpha:ordo_rerum:003",
        "section_order": 3,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "file_start": 767,
        "file_end": 768,
        "start_after_line": 17,
        "stop_before_line": None,
        "section_kind_reason": "Closing table of contents for the volume.",
    },
]

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
META_SKIP_RE = re.compile(
    r"^(?:UNIV\. OF MICHIGAN(?:\s+JAN\s+\d{1,2}\s+\d{4})?|JAN\s+\d{1,2}\s+\d{4}|Ex typis\s+MIGNE,?\s+au Petit-Montrouge\.?|PATROL\.\s+LXXVI\.\s+\d+|QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.|S\.\s+GREGORII\s+LIBRORUM\s+MORALIUM\s+CONTINUATIO\.|INDICES\s+IN\s+PRÆCEDENTEM\s+ET\s+HUNC\s+TOMUM\.|INDEX\s+IN\s+TRIPLICEM\s+S\.?\s+GREGORII\s+MAGNI\s+VITAM\.?|GREGORII\s+MAGNI\s+VITAM\.?|\d{1,4}\.?$)$",
    re.IGNORECASE,
)
PAGE_RE = re.compile(r"\b(?:ibid\.?|id\.?|\d{1,4}(?:\s+et\s+seqq\.)?)\b", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADING_RE = re.compile(
    r"^(?:INDICES\s+IN\s+PRÆCEDENTEM\s+ET\s+HUNC\s+TOMUM\.|INDEX\s+IN\s+TRIPLICEM\s+S\.?\s+GREGORII\s+MAGNI\s+VITAM\.?|INDEX\s+IN\s+LIBROS\s+MORALIUM\s+ET\s+HOMILIAS\s+S\.?\s+GREGORII\.?|ORDO\s+RERUM(?:\s+QUÆ\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|FINIS\s+TOMI\s+SEPTUAGESIMI\s+SEXTI\.)$",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed["all_text"].splitlines():
        text = norm(raw)
        if not text:
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def split_entry_text(line: str) -> list[str]:
    text = norm(line) or ""
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.;])\s+(?=[A-ZÆŒΣ])", text) if part.strip()]
    return parts or [text]


def infer_lemma(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^\d+\s*", "", text)
    text = re.sub(r"^[A-ZÆŒ]\s+", "", text)
    if text.startswith("Vide ") or text.startswith("Vid. ") or text.startswith("Voir ") or text.startswith("V. "):
        return None
    if "," in text:
        text = text.split(",", 1)[0]
    if "." in text and not text.startswith("DOMINICA "):
        candidate = text.split(".", 1)[0]
        if len(candidate) > 2:
            text = candidate
    text = text.strip(" .;:")
    return text or None


def current_entry_kind(section_kind: str, entry_raw: str) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    if re.match(r"^(?:Vide|Vid\.|Voir|V\.)\b", entry_raw, re.IGNORECASE):
        return "cross_reference"
    if entry_raw.startswith("DOMINICA "):
        return "heading_group"
    return "lemma"


def parse_refs(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last = last_page
    for match in PAGE_RE.finditer(text):
        ref_raw = match.group(0).strip()
        if ref_raw.lower().startswith("ibid") or ref_raw.lower() == "id.":
            if current_last is None:
                continue
            refs.append(
                {
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": current_last,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                }
            )
            continue
        number_match = re.search(r"\d{1,4}", ref_raw)
        if not number_match:
            continue
        page_int = int(number_match.group(0))
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        current_last = page_int
    return refs, current_last


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl076_triplicem_abbas_24",
                "lemma_raw": "Abbas",
                "query_names": [
                    "In abbatem eligitur Gregorius",
                    "Abbatum commendatiorum origo prima",
                    "Abbatibus monita utilissima",
                ],
                "page_hints": ["24", "298", "303"],
                "page_hint_ints": [24, 298, 303],
                "context_raw": "Abbas. In abbatem eligitur Gregorius, 24. Rogantibus et cogentibus fratribus, 215. S. Gregorii in eligendis dignis abbatibus sollicitudo, 298.",
            },
            {
                "entry_id": "pl076_moralia_vita_contemplativa_308",
                "lemma_raw": "Vita contemplativa",
                "query_names": [
                    "Vita contemplativa paucorum",
                    "Activa vita multorum est",
                    "Contemplativa minime aufertur",
                ],
                "page_hints": ["308", "1193", "1323"],
                "page_hint_ints": [308, 1193, 1323],
                "context_raw": "Vita contemplativa tempore minor, at major merito quam activa. 210, 1193. Activa vita multorum est, contemplativa paucorum, 1048.",
            },
            {
                "entry_id": "pl076_moralia_vox_1240",
                "lemma_raw": "Vox",
                "query_names": [
                    "Vox est in mente quasi quidam sonus intelligentiæ",
                    "Vox supra firmamentum",
                    "Vox sanctorum quæ",
                ],
                "page_hints": ["42", "1240", "1241"],
                "page_hint_ints": [42, 1240, 1241],
                "context_raw": "Vox angelorum. V. Locutio. Vox sanctorum quæ, 42. Vox est in mente quasi quidam sonus intelligentiæ, 1240. Vox supra firmamentum quæ et Deo loquitur, 1241.",
            },
        ],
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json, {})


def make_section_payload(section: dict[str, Any], files: list[Path], helper_output: dict[str, Any] | None) -> dict[str, Any]:
    section_files = [str(path) for path in files if section["file_start"] <= file_num(path) <= section["file_end"]]
    payload = {
        "section_key": section["section_key"],
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section["section_order"],
        "section_kind": section["section_kind"],
        "heading_raw": section["heading_raw"],
        "heading_norm": sort_norm(section["heading_raw"].rstrip(".")),
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": section_files[0] if section_files else None,
        "file_end": section_files[-1] if section_files else None,
        "confidence": 0.93 if section["section_kind"] != "ordo_rerum" else 0.98,
        "raw_json": {
            "section_kind_reason": section["section_kind_reason"],
            "source_files": section_files,
        },
    }
    if helper_output:
        payload["raw_json"]["helper_locator_status"] = helper_output.get("status")
    return payload


def process_section(
    section: dict[str, Any],
    files: list[Path],
    helper_output: dict[str, Any] | None,
    entry_order_start: int,
    node_order_start: int,
    entry_lookup: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    seen_entry_raws: set[str] = set()
    current_letter: str | None = None
    last_page: int | None = None
    entry_order = entry_order_start
    node_order = node_order_start
    heading_seen = False
    in_section = False

    for path in files:
        num = file_num(path)
        if num < section["file_start"] or num > section["file_end"]:
            continue
        lines = extract_lines(path)
        for line in lines:
            cleaned = norm(line) or ""
            if not cleaned:
                continue

            if section["section_kind"] != "ordo_rerum" and num == 767 and "ORDO RERUM" in cleaned:
                break

            if not heading_seen:
                if section["section_kind"] == "onomastic_mixed" and cleaned.startswith("INDEX IN TRIPLICEM"):
                    heading_seen = True
                    continue
                if section["section_kind"] == "analytic_subject" and cleaned.startswith("INDEX IN LIBROS MORALIUM ET HOMILIAS"):
                    heading_seen = True
                    continue
                if section["section_kind"] == "ordo_rerum" and cleaned.startswith("ORDO RERUM"):
                    heading_seen = True
                    in_section = True
                    continue
                continue

            if FOOTER_RE.fullmatch(cleaned) or META_SKIP_RE.fullmatch(cleaned) or HEADING_RE.fullmatch(cleaned):
                continue
            if "INDEX IN TRIPLICEM" in cleaned or "GREGORII MAGNI VITAM" in cleaned or "INDEX IN LIBROS MORALIUM ET HOMILIAS" in cleaned:
                continue

            if not in_section:
                if section["section_kind"] == "ordo_rerum":
                    continue
                if LETTER_RE.fullmatch(cleaned):
                    in_section = True
                    if cleaned != current_letter:
                        current_letter = cleaned
                        node_order += 1
                        nodes.append(
                            {
                                "node_key": f"{VOLUME_ID}:node:{node_order:06d}",
                                "section_key": section["section_key"],
                                "parent_node_key": None,
                                "node_order": node_order,
                                "node_kind": "letter_group",
                                "label_raw": cleaned,
                                "label_norm": cleaned.lower(),
                                "label_sort": cleaned.lower(),
                                "node_level": 1,
                                "confidence": 0.97,
                                "raw_json": {"source_file": str(path)},
                            }
                        )
                    continue
                continue

            if LETTER_RE.fullmatch(cleaned):
                if cleaned != current_letter:
                    current_letter = cleaned
                    node_order += 1
                    nodes.append(
                        {
                            "node_key": f"{VOLUME_ID}:node:{node_order:06d}",
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": cleaned,
                            "label_norm": cleaned.lower(),
                            "label_sort": cleaned.lower(),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {"source_file": str(path)},
                        }
                    )
                continue

            for segment in split_entry_text(cleaned):
                seg = norm(segment) or ""
                if not seg:
                    continue
                if HEADING_RE.fullmatch(seg) or FOOTER_RE.fullmatch(seg) or LETTER_RE.fullmatch(seg):
                    continue
                seg_key = seg.lower()
                if seg_key in seen_entry_raws:
                    continue
                seen_entry_raws.add(seg_key)

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
                entry_kind = current_entry_kind(section["section_kind"], seg)
                lemma_raw = infer_lemma(seg)
                page_refs, last_page = parse_refs(seg, last_page)
                entry_payload = {
                    "entry_key": entry_key,
                    "section_key": section["section_key"],
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": lemma_raw.lower() if lemma_raw else None,
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": seg,
                    "context_raw": cleaned,
                    "heading_letter": current_letter,
                    "inferred_printed_page": None,
                    "section_start_file": section["file_start"],
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.78 if entry_kind == "lemma" else 0.72,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section["section_kind"],
                        "section_kind_reason": section["section_kind_reason"],
                    },
                }
                if helper_output:
                    entry_payload["raw_json"]["helper_locator_status"] = helper_output.get("status")
                    entry_payload["raw_json"]["helper_candidate_role"] = helper_output.get("candidate_role")
                entries.append(entry_payload)

                for ref_idx, page_ref in enumerate(page_refs, 1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_idx,
                            "ref_kind": page_ref["ref_kind"],
                            "ref_raw": page_ref["ref_raw"],
                            "page_ref_raw": page_ref["page_ref_raw"],
                            "page_ref_int": page_ref["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": page_ref["range_start_raw"],
                            "range_end_raw": page_ref["range_end_raw"],
                            "target_file": None,
                            "target_file_probability": None,
                            "section_start_file": section["file_start"],
                            "editorial_anchor_file": str(path),
                            "confidence": 0.64,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": section["section_kind"],
                            },
                        }
                    )

                if entry_lookup is not None:
                    entry_lookup[entry_key] = entry_payload

    return entries, nodes, refs, entry_order, node_order


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    if helper_output.get("status") == "ok" and helper_output.get("entries"):
        helper_status = helper_output.get("status")
    else:
        helper_status = helper_output.get("status")

    sections = [make_section_payload(section, files, helper_output) for section in SECTION_DEFS]
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0
    entry_lookup: dict[str, dict[str, Any]] = {}

    for section in SECTION_DEFS:
        section_entries, section_nodes, section_refs, entry_order, node_order = process_section(
            section,
            files,
            helper_output,
            entry_order,
            node_order,
            entry_lookup,
        )
        entries.extend(section_entries)
        nodes.extend(section_nodes)
        refs.extend(section_refs)

    noisy_entry_re = re.compile(
        r"^(?:\d{1,4}\.?|.*(?:INDEX IN TRIPLICEM|INDEX IN LIBROS MORALIUM ET HOMILIAS|ORDO RERUM|GREGORII MAGNI VITAM).*)$",
        re.IGNORECASE,
    )
    clean_entries = [entry for entry in entries if not noisy_entry_re.fullmatch(entry["entry_raw"].strip())]
    allowed_entry_keys = {entry["entry_key"] for entry in clean_entries}
    entries = clean_entries
    refs = [ref for ref in refs if ref["entry_key"] in allowed_entry_keys]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina, volume 76",
        "notes": "Recovered the triple-life index, the main Moralium/Homiliarum index, and the closing Ordo Rerum conservatively from the OCR tail.",
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the visible alphabetical and closure blocks conservatively from the OCR tail; OCR repetition and wrapped columns required deduplication, and the material references remain indexed primarily at the OCR-file level.",
        "evidence_files": [
            str(next(path for path in files if file_num(path) == num))
            for num in [665, 680, 717, 765, 767, 768]
            if any(file_num(path) == num for path in files)
        ],
    }

    notes = [
        "Section 1 is the onomastic/mixed index for the triple life of St. Gregory.",
        "Section 2 is the main analytical subject index for the Moralium and Homiliae volumes.",
        "Section 3 is the closing Ordo Rerum, modeled as editorial closure.",
        "Entries are deduplicated by normalized OCR literal to suppress repeated wrapped-column duplication in the tail files.",
        f"Helper locator status: {helper_status}.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": "/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL076_alphabetical_indices.json",
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate PL076 index segmentation and confirm no additional alphabetic closure blocks remain outside the three detected sections.",
            "completed": [
                "section headings mapped",
                "helper request written and executed",
                "entries, nodes, refs, and closure fragments assembled",
            ],
            "pending": [
                "spot-check the final JSON structure",
                "review any schema/import failures if validation complains",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not conflate OCR file suffixes with editorial page numbers.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL076 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

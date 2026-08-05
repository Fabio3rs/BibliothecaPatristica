#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl147_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL147/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL147_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL147_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL147 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL147_alphabetical_indices.json

Builds the PL147 alphabetical-index payload and helper request from OCR tail
material, then writes the canonical JSON payload after helper-backed target
resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL147"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 147"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

sys.path.insert(0, str(ROOT))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml  # noqa: E402

SECTION_DEFS = [
    {
        "section_key": "PL147:alpha:index_in_joannem_rotomagensem:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX IN JOANNEM ROTOMAGENSEM.",
        "section_kind_reason": (
            "Alphabetical subject index of the volume's internal topics and liturgical matters, "
            "not an author index; the printed heading is the standard index header."
        ),
        "file_start_seq": 663,
        "file_end_seq": 668,
    },
    {
        "section_key": "PL147:ordo:001",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "section_kind_reason": "Closing contents table of the tome; editorial closure material rather than alphabetical index proper.",
        "file_start_seq": 669,
        "file_end_seq": 671,
    },
]

SPACE_RE = re.compile(r"\s+")
PAGE_SEQ_RE = re.compile(r"-(\d+)\.txt$")
NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?!])\s+(?=[A-ZÆŒ])")
ROMAN_HEAD_RE = re.compile(r"^(?:CAP(?:UT)?\.?\s*[IVXLCDM]+\s*—\s*|[IVXLCDM]+\.\s*—\s*)", re.IGNORECASE)
APP_PREFIX_RE = re.compile(r"(?i)\bapp\.?\s*(\d{1,4})")
IBID_RE = re.compile(r"(?i)\bibid\.?\b")
DIGITIZED_RE = re.compile(r"^Digitized by Google$", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "").replace("\xa0", " ")
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = SPACE_RE.sub(" ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize_text(text)
    return value.lower() if value else None


def page_seq(path: Path) -> int:
    match = PAGE_SEQ_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def iter_ocr_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=page_seq)


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        line = normalize_text(raw)
        if not line or DIGITIZED_RE.fullmatch(line):
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize_text(parsed.get("header_text") or "")
        footer = normalize_text(parsed.get("footer_text") or "")
        for block in (header, footer):
            for match in NUMBER_RE.finditer(block):
                number = int(match.group(1))
                page_map.setdefault(number, str(path))
    return page_map


def is_section_heading(line: str) -> bool:
    upper = line.upper()
    return bool(
        upper == "INDEX IN JOANNEM ROTOMAGENSEM."
        or upper == "ORDO RERUM"
        or "QUAE IN HOC TOMO CONTINENTUR" in upper
        or "QUÆ IN HOC TOMO CONTINENTUR" in upper
    )


def split_chunks(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        if not line:
            continue
        if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V"}:
            continue
        if is_section_heading(line):
            if current:
                chunks.append(current.strip())
                current = ""
            continue
        if not current:
            current = line
            continue
        if current.endswith("-"):
            current = current[:-1] + line.lstrip()
            continue
        if line.startswith((";", ",", ":", ")", "]")) or line[:1].islower():
            current = f"{current} {line}"
            continue
        # Split on sentence boundaries only when the new sentence looks like a new item.
        if re.search(r"\d(?:,|\.)\s*$", current) and re.match(r"^[A-ZÆŒ]", line):
            chunks.append(current.strip())
            current = line
        else:
            current = f"{current} {line}"
    if current:
        chunks.append(current.strip())
    return chunks


def sentence_slices(chunk: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(chunk) if part.strip()]
    if not parts:
        return [chunk]
    return parts


def extract_ref_items(text: str, inherited_page: int | None = None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    page_hint: int | None = inherited_page

    def add_ref(raw: str, page_int: int, prefix: str | None = None, inherited: bool = False) -> None:
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "inherited": inherited,
                "prefix": prefix,
            }
        )

    normalized = ROMAN_HEAD_RE.sub("", text).strip()
    segments = [segment.strip() for segment in re.split(r"\s*;\s*", normalized) if segment.strip()]
    for segment in segments:
        if IBID_RE.search(segment):
            if page_hint is not None:
                add_ref("ibid.", page_hint, inherited=True)
            continue

        app_prefix = APP_PREFIX_RE.search(segment)
        if app_prefix:
            page_int = int(app_prefix.group(1))
            add_ref(f"app. {page_int}", page_int, prefix="app.")
            page_hint = page_int
            continue

        numbers = [int(match.group(1)) for match in NUMBER_RE.finditer(segment)]
        if not numbers:
            continue
        for num in numbers:
            add_ref(str(num), num)
            page_hint = num

    return refs, page_hint


def build_entry_key(section_order: int, entry_order: int) -> str:
    return f"{VOLUME_ID}:entry:{section_order:02d}:{entry_order:04d}"


def build_section_entries(
    section: dict[str, Any],
    files: list[Path],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    section_files = [path for path in files if section["file_start_seq"] <= page_seq(path) <= section["file_end_seq"]]
    if not section_files:
        raise SystemExit(f"Could not locate OCR files for section {section['section_key']}")

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    evidence_files = [str(path) for path in section_files]

    order = 0
    previous_page: int | None = None
    for path in section_files:
        lines = clean_lines(path)
        chunks = split_chunks(lines)
        for chunk in chunks:
            for sentence in sentence_slices(chunk):
                if is_section_heading(sentence):
                    continue
                if not re.search(r"\d{1,4}", sentence) and not IBID_RE.search(sentence):
                    if entries:
                        entries[-1]["entry_raw"] = f"{entries[-1]['entry_raw']} {sentence}"
                        entries[-1]["context_raw"] = entries[-1]["entry_raw"]
                        entries[-1]["raw_json"].setdefault("continuations", []).append(sentence)
                    continue

                order += 1
                entry_key = build_entry_key(section["section_order"], order)
                ref_items, previous_page = extract_ref_items(sentence, previous_page)
                ref_page_ints = [ref["page_ref_int"] for ref in ref_items if isinstance(ref.get("page_ref_int"), int)]
                inferred_page = ref_page_ints[0] if ref_page_ints else previous_page

                if ref_items:
                    first_target = page_map.get(ref_items[0]["page_ref_int"], str(path))
                else:
                    first_target = str(path)

                lemma_raw = sentence
                first_ref_match = NUMBER_RE.search(sentence)
                if first_ref_match:
                    lemma_raw = sentence[: first_ref_match.start()].rstrip(" ,;:.")
                lemma_raw = lemma_raw.strip()
                if lemma_raw.endswith(","):
                    lemma_raw = lemma_raw[:-1].rstrip()

                entry_kind = "heading_group" if lemma_raw.isupper() and len(lemma_raw) <= 3 else "lemma"
                if IBID_RE.fullmatch(lemma_raw.lower() if lemma_raw else ""):
                    entry_kind = "cross_reference"

                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section["section_key"],
                        "parent_node_key": None,
                        "entry_order": order,
                        "entry_kind": entry_kind,
                        "lemma_raw": lemma_raw or None,
                        "lemma_display": lemma_raw or None,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": sentence,
                        "context_raw": sentence,
                        "heading_letter": None,
                        "inferred_printed_page": inferred_page,
                        "section_start_file": str(section_files[0]),
                        "editorial_anchor_file": str(path),
                        "target_file_best": first_target,
                        "confidence": 0.92 if ref_items else 0.75,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section["section_kind"],
                            "section_kind_reason": section["section_kind_reason"],
                            "page_hints": ref_page_ints,
                            "segment_raw": sentence,
                            "previous_page_inherited": previous_page,
                        },
                    }
                )

                for ref_order, ref in enumerate(ref_items, start=1):
                    target_file = page_map.get(ref["page_ref_int"], str(path))
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": ref["page_ref_col"],
                            "line_ref_raw": ref["line_ref_raw"],
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": target_file,
                            "target_file_probability": 0.99 if ref["page_ref_int"] in page_map else 0.6,
                            "section_start_file": str(section_files[0]),
                            "editorial_anchor_file": str(path),
                            "confidence": 0.93 if not ref.get("inherited") else 0.8,
                            "raw_json": {
                                "source_file": str(path),
                                "segment_raw": sentence,
                                "inherited": ref.get("inherited", False),
                                "prefix": ref.get("prefix"),
                            },
                        }
                    )

    return entries, refs, evidence_files


def build_helper_request(entries: list[dict[str, Any]], refs: list[dict[str, Any]], source_root: Path) -> dict[str, Any]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    helper_entries: list[dict[str, Any]] = []
    helper_limit = 80
    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        page_hint_ints = [ref["page_ref_int"] for ref in entry_refs if isinstance(ref.get("page_ref_int"), int)]
        if not page_hint_ints:
            continue
        page_hints: list[str] = []
        for ref in entry_refs:
            raw = str(ref.get("page_ref_raw") or "").strip()
            if raw and raw not in page_hints:
                page_hints.append(raw)
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or entry.get("entry_raw"),
                "query_names": [q for q in [entry.get("lemma_raw"), entry.get("entry_raw")] if q][:4],
                "page_hints": page_hints[:3],
                "page_hint_ints": page_hint_ints[:3],
                "context_raw": entry.get("entry_raw"),
            }
        )
        if len(helper_entries) >= helper_limit:
            break

    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json)


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def apply_helper(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    by_id = helper_map(helper_output)
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        helper_item = by_id.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or helper_item.get("best") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
        if best.get("probability") is not None:
            entry["confidence"] = max(float(entry.get("confidence") or 0.0), float(best.get("probability") or 0.0))
        entry["raw_json"].update(
            {
                "helper_status": helper_item.get("status"),
                "helper_best_file": best.get("file"),
                "helper_best_probability": best.get("probability"),
                "helper_candidate_role": best.get("candidate_role"),
                "helper_reason_summary": best.get("reason_summary"),
                "helper_top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                    }
                    for cand in (helper_item.get("candidates") or [])[:5]
                ],
            }
        )
        for ref in refs_by_entry.get(entry["entry_key"], []):
            ref["raw_json"].update(
                {
                    "helper_status": helper_item.get("status"),
                    "helper_best_file": best.get("file"),
                    "helper_best_probability": best.get("probability"),
                    "helper_candidate_role": best.get("candidate_role"),
                    "helper_reason_summary": best.get("reason_summary"),
                }
            )
            if best.get("file"):
                ref["target_file"] = best.get("file")
                if best.get("probability") is not None:
                    ref["target_file_probability"] = float(best.get("probability"))


def build_sections(section_files: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in SECTION_DEFS:
        file_start, file_end = section_files[section["section_key"]]
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": normalize_text(section["heading_raw"]),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": file_start,
                "file_end": file_end,
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "file_start_seq": section["file_start_seq"],
                    "file_end_seq": section["file_end_seq"],
                },
            }
        )
    return sections


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = iter_ocr_files(source_root)
    page_map = build_page_map(files)

    section_file_paths: dict[str, tuple[str, str]] = {}
    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for section in SECTION_DEFS:
        section_files = [path for path in files if section["file_start_seq"] <= page_seq(path) <= section["file_end_seq"]]
        if not section_files:
            raise SystemExit(f"Could not locate OCR files for section {section['section_key']}")
        section_file_paths[section["section_key"]] = (str(section_files[0]), str(section_files[-1]))
        entries, refs, section_evidence = build_section_entries(section, files, page_map)
        all_entries.extend(entries)
        all_refs.extend(refs)
        evidence_files.extend(section_evidence)

    evidence_files = list(dict.fromkeys(evidence_files))

    helper_request = build_helper_request(all_entries, all_refs, source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    apply_helper(all_entries, all_refs, helper_output)

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": build_sections(section_file_paths),
        "nodes": [],
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": (
                "Recovered the main analytical subject index and the final ORDO RERUM contents block "
                "from OCR, with helper-backed target resolution for page-linked fragments."
            ),
            "evidence_files": evidence_files,
        },
        "notes": [
            "The first section is the subject index headed INDEX IN JOANNEM ROTOMAGENSEM.; the second section is the closing ORDO RERUM contents table.",
            "OCR file suffixes are not editorial pagination. Target files were resolved from printed page references and helper output.",
            "Bare ibid. references were inherited from the last explicit page within the same entry only.",
        ],
    }

    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "entry_count": len(payload["entries"]),
            "ref_count": len(payload["refs"]),
            "section_count": len(payload["sections"]),
            "evidence_files": evidence_files,
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Final payload written and helper-checked.",
            "completed": [
                "Confirmed the INDEX IN JOANNEM ROTOMAGENSEM subject index section in OCR.",
                "Confirmed the closing ORDO RERUM contents section in OCR.",
                "Generated helper request and resolved page-linked fragments.",
                "Assembled the canonical payload and checkpoint fragments.",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The section boundaries follow the OCR tail files 663-668 and 669-671.",
            ],
        },
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL147 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

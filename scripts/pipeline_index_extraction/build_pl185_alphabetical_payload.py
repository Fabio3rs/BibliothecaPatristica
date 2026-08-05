#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl185_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL185/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL185_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL185_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL185 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL185_alphabetical_indices.json

Builds the PL185 alphabetical-index payload from the OCR tail, including the
subject index, the Vita S. Bernardi onomastic index, the lineage index, and the
closing ORDO RERUM table.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL185"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 185"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PL185/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL185_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL185_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL185_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL185"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX RERUM ET VERBORUM.",
        "heading_norm": "index rerum et verborum",
        "page_start": 1923,
        "page_end": 1974,
        "section_order": 1,
        "kind_reason": "Alphabetical subject index headed INDEX RERUM ET VERBORUM with dense lemma-to-page citations.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:002",
        "section_kind": "onomastic_mixed",
        "heading_raw": "INDEX HISTORICUS VITAE SANCTI BERNARDI.",
        "heading_norm": "index historicus vitae sancti bernardi",
        "page_start": 1975,
        "page_end": 1984,
        "section_order": 2,
        "kind_reason": "Historical onomastic index of people and places related to the Vita S. Bernardi.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:onomastic_person:003",
        "section_kind": "onomastic_person",
        "heading_raw": "INDEX IN GENUS ILLUSTRE S. BERNARDI.",
        "heading_norm": "index in genus illustre s bernardi",
        "page_start": 1985,
        "page_end": 1988,
        "section_order": 3,
        "kind_reason": "Genealogical onomastic index of the illustrious lineage of S. Bernard.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:004",
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "page_start": 1995,
        "page_end": 2016,
        "section_order": 4,
        "kind_reason": "Editorial contents table (ordo rerum) at the end of the volume, distinct from the alphabetical and onomastic indexes.",
    },
]

HEADING_TRANSITIONS = {
    "INDEX RERUM ET VERBORUM": "analytic_subject",
    "INDEX HISTORICUS": "onomastic_mixed",
    "INDEX IN GENUS ILLUSTRE S. BERNARDI": "onomastic_person",
    "ORDO RERUM": "ordo_rerum",
}

NOISE_LINES = {
    "Digitized by Google",
    "-",
    "—",
}

PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*-\s*(\d{1,4}))?(?!\d)")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
SPACE_RE = re.compile(r"\s+")
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
OCR_LETTER_RE = re.compile(r"^[A-ZÆŒ](?:[a-z])?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = SPACE_RE.sub(" ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip().lower()
    return cleaned or None


def page_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(match.group(1))


def discover_canonical_files(source_root: Path) -> list[Path]:
    by_seq: dict[int, list[Path]] = {}
    for path in source_root.glob("*.txt"):
        by_seq.setdefault(page_seq(path), []).append(path)
    files: list[Path] = []
    for seq in sorted(by_seq):
        paths = sorted(by_seq[seq], key=lambda p: (0 if p.name.startswith("PL185-") else 1, p.name))
        files.append(paths[0])
    return files


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    blocks: list[tuple[str, str]] = []
    for key in ("header_text", "body_text", "footer_text"):
        text = parsed.get(key) or ""
        if not text:
            continue
        blocks.append((key, text))
    return blocks


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    body = parsed.get("body_text") or ""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = normalize(raw_line)
        if not line or line in NOISE_LINES:
            continue
        lines.append(line)
    return lines


def page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in discover_canonical_files(source_root):
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for token in HEADER_NUM_RE.findall(header):
            value = int(token)
            mapping.setdefault(value, path.as_posix())
    return mapping


def split_page_hints(text: str) -> list[tuple[str, int, int | None]]:
    hints: list[tuple[str, int, int | None]] = []
    for match in PAGE_RE.finditer(text):
        start_raw = match.group(1)
        end_raw = match.group(2)
        if start_raw.startswith("0"):
            continue
        start_int = int(start_raw)
        end_int = int(end_raw) if end_raw else None
        hints.append((match.group(0).replace(" ", ""), start_int, end_int))
    return hints


def lemma_from_line(text: str, section_kind: str) -> str | None:
    cleaned = normalize(text) or ""
    if not cleaned:
        return None
    if section_kind == "ordo_rerum":
        return cleaned.strip(" ,;:.") or None
    first_ref = re.search(r"(?<!\d)\d{1,4}(?:\s*-\s*\d{1,4})?(?!\d)", cleaned)
    if first_ref:
        cleaned = cleaned[: first_ref.start()].rstrip(" ,;:.")
    return cleaned.strip(" ,;:.") or None


def entry_kind_for(text: str, section_kind: str, has_refs: bool) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    if not has_refs and re.search(r"\b(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def target_for_page(page: int, mapping: dict[int, str]) -> str | None:
    return mapping.get(page)


def canonical_section_index(section_kind: str) -> int:
    for idx, spec in enumerate(SECTION_SPECS, start=1):
        if spec["section_kind"] == section_kind:
            return idx
    raise KeyError(section_kind)


def build_sections() -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for spec in SECTION_SPECS:
        sections.append(
            {
                "section_key": spec["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": None,
                "file_end": None,
                "confidence": 0.97 if spec["section_kind"] != "ordo_rerum" else 0.95,
                "raw_json": {
                    "section_kind_reason": spec["kind_reason"],
                    "section_kind_source": "OCR headings observed in the tail window and neighboring pages.",
                },
            }
        )
    return sections


def section_for_transition(line: str) -> str | None:
    normalized = normalize(line) or ""
    for marker, kind in HEADING_TRANSITIONS.items():
        if marker in normalized:
            return kind
    return None


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
    *,
    skip_helper: bool = False,
) -> dict[str, Any]:
    files = discover_canonical_files(source_root)
    mapping = page_map(source_root)

    sections = build_sections()
    section_lookup = {spec["section_kind"]: spec for spec in SECTION_SPECS}
    section_state: str | None = None
    current_section_files: dict[str, list[Path]] = {spec["section_kind"]: [] for spec in SECTION_SPECS}

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    entry_counter_by_section: dict[str, int] = {spec["section_kind"]: 0 for spec in SECTION_SPECS}
    node_counter_by_section: dict[str, int] = {spec["section_kind"]: 0 for spec in SECTION_SPECS}
    current_node_key_by_section: dict[str, str | None] = {spec["section_kind"]: None for spec in SECTION_SPECS}

    def ensure_section_file_bounds(section_kind: str, path: Path) -> None:
        current_section_files[section_kind].append(path)

    def maybe_make_node(section_kind: str, label: str, source_file: str) -> str:
        node_counter_by_section[section_kind] += 1
        node_key = f"{VOLUME_ID}:node:{section_kind}:{node_counter_by_section[section_kind]:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_lookup[section_kind]["section_key"],
                "parent_node_key": None,
                "node_order": node_counter_by_section[section_kind],
                "node_kind": "letter_group" if len(label) <= 2 and label[:1].isalpha() else "heading_group",
                "label_raw": label,
                "label_norm": normalize(label),
                "label_sort": sort_norm(label),
                "node_level": 1,
                "confidence": 0.99 if len(label) <= 2 else 0.93,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section_kind,
                },
            }
        )
        current_node_key_by_section[section_kind] = node_key
        return node_key

    for path in files:
        seq = page_seq(path)
        lines = extract_lines(path)
        if not lines:
            continue
        current_file_section = section_state or "analytic_subject"
        ensure_section_file_bounds(current_file_section, path)
        for raw_line in lines:
            transition = section_for_transition(raw_line)
            if transition:
                section_state = transition
                current_file_section = transition
                ensure_section_file_bounds(current_file_section, path)
                if "ORDO RERUM" in raw_line:
                    continue
                if "INDEX HISTORICUS" in raw_line or "INDEX IN GENUS ILLUSTRE" in raw_line:
                    continue
                if "INDEX RERUM ET VERBORUM" in raw_line:
                    continue
            if section_state is None:
                continue
            section_kind = section_state
            line = normalize(raw_line) or ""
            if not line or line in NOISE_LINES:
                continue
            if any(marker in line for marker in HEADING_TRANSITIONS):
                # Already handled above.
                continue
            if section_kind != "ordo_rerum" and (SINGLE_LETTER_RE.fullmatch(line) or OCR_LETTER_RE.fullmatch(line)):
                letter = line[0].upper()
                maybe_make_node(section_kind, letter, path.as_posix())
                continue

            text = line
            if section_kind != "ordo_rerum":
                # Split large OCR lines conservatively at sentence boundaries
                # so that dense index lines can still be serialized one fragment
                # at a time.
                fragments = [frag.strip() for frag in re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", text) if frag.strip()]
            else:
                fragments = [text]

            for fragment in fragments:
                if not fragment or fragment in NOISE_LINES:
                    continue
                has_refs = bool(PAGE_RE.search(fragment))
                if section_kind != "ordo_rerum" and not has_refs and not re.search(r"\b(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", fragment, re.IGNORECASE):
                    # Keep high-confidence alphabetical/onamastic line items only.
                    continue
                lemma_raw = lemma_from_line(fragment, section_kind)
                if not lemma_raw:
                    continue
                entry_counter_by_section[section_kind] += 1
                entry_key = f"{VOLUME_ID}:entry:{canonical_section_index(section_kind)}:{entry_counter_by_section[section_kind]:04d}"
                hints = split_page_hints(fragment)
                inferred_page = hints[0][1] if hints else None
                target_best = target_for_page(inferred_page, mapping) if inferred_page else None
                entry_kind = entry_kind_for(fragment, section_kind, has_refs)
                node_key = current_node_key_by_section.get(section_kind)
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_lookup[section_kind]["section_key"],
                    "parent_node_key": node_key,
                    "entry_order": entry_counter_by_section[section_kind],
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize(lemma_raw),
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": fragment,
                    "context_raw": fragment,
                    "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": path.as_posix(),
                    "editorial_anchor_file": path.as_posix(),
                    "target_file_best": target_best,
                    "confidence": 0.92 if has_refs else 0.78,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "section_kind": section_kind,
                        "page_hints": [hint[1] for hint in hints],
                        "page_hints_raw": [hint[0] for hint in hints],
                        "entry_kind_reason": "Conservative OCR line item from the index tail" if section_kind != "ordo_rerum" else "Editorial contents-table line item.",
                    },
                }
                entries.append(entry)

                helper_entries.append(
                    {
                        "entry_id": entry_key,
                        "lemma_raw": lemma_raw,
                        "query_names": list(
                            OrderedDict.fromkeys(
                                q
                                for q in [
                                    lemma_raw,
                                    normalize(lemma_raw),
                                    lemma_raw.replace("Æ", "AE").replace("æ", "ae"),
                                ]
                                if q
                            )
                        ),
                        "page_hints": [str(hint[1]) for hint in hints[:3]],
                        "page_hint_ints": [hint[1] for hint in hints[:3]],
                        "context_raw": fragment,
                    }
                )

                ref_order = 0
                for hint_raw, hint_int, hint_end in hints:
                    if section_kind == "ordo_rerum" and not hint_int:
                        continue
                    ref_order += 1
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_range" if hint_end else "editorial_page",
                            "ref_raw": hint_raw,
                            "page_ref_raw": str(hint_int),
                            "page_ref_int": hint_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": str(hint_int) if hint_end else None,
                            "range_end_raw": str(hint_end) if hint_end else None,
                            "target_file": target_for_page(hint_int, mapping),
                            "target_file_probability": 0.99 if target_for_page(hint_int, mapping) else None,
                            "section_start_file": path.as_posix(),
                            "editorial_anchor_file": path.as_posix(),
                            "confidence": 0.96 if target_for_page(hint_int, mapping) else 0.72,
                            "raw_json": {
                                "locator_source": "OCR page map",
                                "section_kind": section_kind,
                            },
                        }
                    )

    selected_helper_entries = [entry for entry in helper_entries if entry["page_hint_ints"]][:24]

    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Run helper on a reduced PL185 sample and finish payload assembly.",
            "completed": [
                "OCR tail segmented into four editorial structures",
                "page map built from OCR headers",
            ],
            "pending": [
                "run helper target locator on reduced sample",
                "finalize refs and write payload",
            ],
            "blocked": [],
            "notes": [
                "Helper entries are intentionally capped because the local page map already resolves most targets.",
                "Keep OCR file suffixes separate from printed page numbers.",
            ],
        },
    )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": selected_helper_entries,
    }
    write_json(helper_request_json, helper_request)

    if skip_helper:
        helper_output = {"volume_id": VOLUME_ID, "status": "skipped", "entries": []}
        write_json(helper_output_json, helper_output)
    elif helper_request["entries"]:
        proc = subprocess.run(
            [
                sys.executable,
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
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        helper_output = read_json(helper_output_json, {})
    else:
        helper_output = {"volume_id": VOLUME_ID, "status": "empty", "entries": []}
        write_json(helper_output_json, helper_output)

    helper_by_id = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }

    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        entry["raw_json"]["helper"] = {
            "status": helper.get("status"),
            "candidate_role": helper.get("candidate_role"),
            "reason_summary": helper.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in best.get("evidence", [])[:8] if isinstance(ev, dict)],
            }
            if best
            else None,
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                }
                for cand in helper.get("candidates", [])[:5]
                if isinstance(cand, dict)
            ],
        }
        best_file = best.get("file")
        if best_file:
            entry["target_file_best"] = best_file
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))

    for ref in refs:
        helper = helper_by_id.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref["raw_json"]["helper_best_file"] = best.get("file")
            ref["raw_json"]["helper_best_probability"] = best.get("probability")

    for section in sections:
        matching_files = current_section_files[section["section_kind"]]
        if matching_files:
            section["file_start"] = matching_files[0].as_posix()
            section["file_end"] = matching_files[-1].as_posix()
            section["raw_json"]["evidence_files"] = [matching_files[0].as_posix(), matching_files[-1].as_posix()]

    coverage = {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": "Recovered the tail subject index, the Vita S. Bernardi onomastic index, the lineage index, and the closing ORDO RERUM table from the OCR tail. Some OCR lines are conservative line-level units where wrap boundaries do not support safer sub-entry splitting.",
        "evidence_files": [files[0].as_posix(), files[-1].as_posix()],
    }

    notes = [
        "The tail contains four editorial structures: a subject index, a Vita S. Bernardi index, a lineage index, and an ORDO RERUM contents table.",
        "OCR file suffixes were kept separate from printed page numbers; the page map is built from header text across the volume.",
        "Residual ambiguity is mostly line segmentation inside dense OCR rows, not section identity.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": generated_at, "updated_at": generated_at})
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": generated_at,
            "current_focus": "Validate PL185 alphabetical payload and confirm final section boundaries.",
            "completed": [
                "OCR tail segmented into four editorial structures",
                "helper request prepared and executed",
                "intermediate payload fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page numbers.",
                "Review any section whose file_start/file_end was inferred from mixed pages.",
            ],
        },
    )

    write_json(intermediate_dir / "payload.json", payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL185 alphabetical payload from OCR tail sections.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    ap.add_argument("--skip-helper", action="store_true", help="Skip running index_target_locator.py and rely on the local page map.")
    args = ap.parse_args()

    payload = build_payload(
        args.source_root,
        args.helper_request_json,
        args.helper_output_json,
        args.intermediate_dir,
        skip_helper=args.skip_helper,
    )
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg065_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG065/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG065_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG065_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG065 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG065_alphabetical_indices.json

Build the PG065 alphabetical-index payload from the OCR tail. The volume
contains a section index for Vita S. Porphyrii and a closing Ordo Rerum table.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG065"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 65"
SECTION1_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION2_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"
TODO_FILENAME = "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION1_HEADING = "INDEX SECTIONUM VITÆ S. PORPHYRII."
SECTION2_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def parse_blocks(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        if 'tipo="nota_marginal"' in attrs or 'tipo="outro"' in attrs:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize_space(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def discover_files(source_root: Path) -> list[Path]:
    def seq(path: Path) -> int:
        m = re.search(r"-(\d+)\.txt$", path.name)
        return int(m.group(1)) if m else -1

    return sorted(source_root.glob("*.txt"), key=seq)


def build_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in discover_files(source_root):
        for line in parse_blocks(path):
            if not line:
                continue
            if SECTION1_HEADING in line or SECTION2_HEADING in line:
                for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", line):
                    mapping.setdefault(int(token), path.as_posix())
                continue
            if re.fullmatch(r"\d{3,4}(?:\s+\w+.*)?\s+\d{3,4}", line):
                for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", line):
                    mapping.setdefault(int(token), path.as_posix())
    return mapping


def is_header_like(line: str) -> bool:
    return bool(
        line == SECTION1_HEADING
        or line == SECTION2_HEADING
        or re.fullmatch(r"\d{3,4}(?:\s+[A-ZÆŒ][A-Za-zÆŒæœ\.\s\-']*)?\s+\d{3,4}", line)
        or re.fullmatch(r"\d{3,4}", line)
    )


def split_page_ref(text: str) -> tuple[str, int | None]:
    m = re.search(r"\s+(\d{1,4})\s*$", text)
    if not m:
        return text.strip(), None
    return text[: m.start()].rstrip(" ,;:.—-"), int(m.group(1))


def normalize_entry_text(text: str) -> str:
    return normalize_space(text).strip(" ,;:.")


def parse_section1(lines: list[tuple[str, str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: list[tuple[str, str]] = []
    for file_path, line in lines:
        if is_header_like(line):
            if current:
                entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
                current = []
            continue
        if re.match(r"^(?:[IVXLCDM]+|[0-9]+)\.\s", line):
            if current:
                entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
            current = [(file_path, line)]
            continue
        if current and line and line[0].islower():
            current.append((file_path, line))
            continue
        if current and not re.match(r"^(?:[IVXLCDM]+|[0-9]+)\.\s", line):
            current.append((file_path, line))
            continue
        if current:
            entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
        current = [(file_path, line)]
    if current:
        entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
    return entries


def line_starts_new_entry(line: str) -> bool:
    if re.match(r"^(?:[IVXLCDM]+|[0-9]+)\.\s", line):
        return True
    if line[:1].islower():
        return False
    if line.startswith(("Notitia.", "Prologus.", "Monitum.", "Cap.", "CAP.", "Oratio", "Epistola", "Sermo", "HOMILIÆ.", "HOMILIÆ", "PHILOSTORGIUS.", "INDEX", "ORDO RERUM")):
        return True
    if line.isupper() and len(line.split()) <= 12:
        return True
    return True


def parse_section2(lines: list[tuple[str, str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: list[tuple[str, str]] = []
    for file_path, line in lines:
        if is_header_like(line):
            if current:
                entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
                current = []
            continue
        if not current:
            current = [(file_path, line)]
            continue
        prev = current[-1][1]
        if prev.endswith("-") or line[:1].islower():
            current.append((file_path, line))
            continue
        if current and not re.search(r"\s+\d{1,4}\s*$", prev) and line_starts_new_entry(line):
            entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
            current = [(file_path, line)]
            continue
        if re.search(r"\s+\d{1,4}\s*$", prev) and line_starts_new_entry(line):
            entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
            current = [(file_path, line)]
            continue
        current.append((file_path, line))
    if current:
        entries.append({"file": current[0][0], "text": normalize_space(" ".join(piece for _, piece in current))})
    return entries


def classify_section1_entry(text: str) -> tuple[str, str | None]:
    m = re.match(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*(.*)$", text)
    if m:
        body = normalize_entry_text(m.group(1))
        return "heading_group", body or None
    return "heading_group", normalize_entry_text(text) or None


def classify_section2_entry(text: str) -> tuple[str, str | None, int | None]:
    body, page_int = split_page_ref(text)
    body = normalize_entry_text(body)
    return "heading_group", body or None, page_int


def build_page_map_entries(entries: list[dict[str, Any]], page_map: dict[int, str], helper_page_map: dict[int, str], section_key: str, section_start_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    serialized_entries: list[dict[str, Any]] = []
    serialized_refs: list[dict[str, Any]] = []
    for idx, item in enumerate(entries, start=1):
        text = item["text"]
        file_path = item["file"]
        entry_key = f"{VOLUME_ID}:entry:{section_key.split(':')[-1]}:{idx:04d}"
        entry_kind, lemma_raw, page_int = classify_section2_entry(text)
        target_file = None
        if page_int is not None:
            target_file = helper_page_map.get(page_int) or page_map.get(page_int)
        serialized_entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": None,
                "entry_order": idx,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw) if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
                "entry_raw": text,
                "context_raw": text if len(text) < 220 else text[:217] + "...",
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": section_start_file,
                "editorial_anchor_file": file_path,
                "target_file_best": target_file,
                "confidence": 0.92 if page_int is not None else 0.78,
                "raw_json": {
                    "source": "ocr_line_group",
                    "page_ref_tokens": [page_int] if page_int is not None else [],
                    "target_resolution": "page_map" if target_file else "unresolved",
                },
            }
        )
        if page_int is not None:
            serialized_refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_int),
                    "page_ref_raw": str(page_int),
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.99 if target_file else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": file_path,
                    "confidence": 0.92 if target_file else 0.7,
                    "raw_json": {
                        "source": "ocr_line_group",
                        "target_resolution": "page_map" if target_file else "unresolved",
                    },
                }
            )
    return serialized_entries, serialized_refs


def build_section1_payload(entries: list[dict[str, Any]], section_start_file: str, section_end_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    serialized_entries: list[dict[str, Any]] = []
    for idx, item in enumerate(entries, start=1):
        entry_key = f"{VOLUME_ID}:entry:001:{idx:04d}"
        entry_kind, lemma_raw = classify_section1_entry(item["text"])
        serialized_entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION1_KEY,
                "parent_node_key": None,
                "entry_order": idx,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw) if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
                "entry_raw": item["text"],
                "context_raw": item["text"] if len(item["text"]) < 220 else item["text"][:217] + "...",
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": item["file"],
                "target_file_best": item["file"],
                "confidence": 0.88,
                "raw_json": {
                    "source": "ocr_roman_section_index",
                    "page_ref_tokens": [],
                },
            }
        )
    return serialized_entries, []


def make_helper_request(section2_entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> None:
    helper_entries = []
    for idx, item in enumerate(section2_entries, start=1):
        text = item["text"]
        body, page_int = split_page_ref(text)
        if page_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"pg065_ordo_{idx:04d}",
                "lemma_raw": normalize_entry_text(body) or text,
                "query_names": [normalize_entry_text(body) or text],
                "page_hints": [str(page_int)],
                "page_hint_ints": [page_int],
                "context_raw": text,
            }
        )
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
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
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if not helper_output_json.exists():
        raise SystemExit("helper output was not written")
    return read_json(helper_output_json)


def update_todo(intermediate_dir: Path, notes: list[str]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build and validate the PG065 ordo_rerum payload.",
        "completed": [
            "inspected OCR tail and identified the section index plus closing ORDO RERUM table",
        ],
        "pending": [
            "run helper on page-linked table entries",
            "write final payload",
            "validate output JSON",
        ],
        "blocked": [],
        "notes": notes,
    }
    write_json(intermediate_dir / TODO_FILENAME, todo)


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, output_file: Path) -> dict[str, Any]:
    all_files = discover_files(source_root)
    file_lines: list[tuple[str, str]] = []
    for path in all_files:
        for line in parse_blocks(path):
            file_lines.append((path.as_posix(), line))

    section1_lines: list[tuple[str, str]] = []
    section2_lines: list[tuple[str, str]] = []
    section = 0
    for file_path, line in file_lines:
        if SECTION1_HEADING in line:
            section = 1
            continue
        if "ORDO RERUM" in line:
            section = 2
            continue
        if section == 1:
            section1_lines.append((file_path, line))
        elif section == 2:
            section2_lines.append((file_path, line))

    sec1_entries = parse_section1(section1_lines)
    sec2_entries = parse_section2(section2_lines)

    # Build helper request and run helper before assembly.
    make_helper_request(sec2_entries, helper_request_json, source_root)
    helper = run_helper(helper_request_json, helper_output_json)
    helper_request = read_json(helper_request_json, {})
    helper_page_map: dict[int, str] = {}
    request_entries = helper_request.get("entries", []) if isinstance(helper_request, dict) else []
    helper_entries = helper.get("entries", []) if isinstance(helper, dict) else []
    request_page_by_id = {
        item.get("entry_id"): (item.get("page_hint_ints") or [None])[0]
        for item in request_entries
        if item.get("entry_id")
    }
    for item in helper_entries:
        entry_id = item.get("entry_id")
        best = item.get("best_candidate") or {}
        page_int = request_page_by_id.get(entry_id)
        if entry_id and page_int is not None and best.get("file"):
            helper_page_map[int(page_int)] = best.get("file")

    page_map = build_page_map(source_root)

    sec1_serialized, sec1_refs = build_section1_payload(
        sec1_entries,
        section_start_file=sec1_entries[0]["file"] if sec1_entries else all_files[0].as_posix(),
        section_end_file=sec1_entries[-1]["file"] if sec1_entries else all_files[0].as_posix(),
    )
    sec2_serialized, sec2_refs = build_page_map_entries(
        sec2_entries,
        page_map=page_map,
        helper_page_map=helper_page_map,
        section_key=SECTION2_KEY,
        section_start_file=sec2_entries[0]["file"] if sec2_entries else all_files[0].as_posix(),
    )

    sections = [
        {
            "section_key": SECTION1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION1_HEADING,
            "heading_norm": "index sectionum vitae s porphyrii",
            "heading_letter": None,
            "page_start": 1261,
            "page_end": 1262,
            "file_start": sec1_entries[0]["file"] if sec1_entries else all_files[0].as_posix(),
            "file_end": sec1_entries[-1]["file"] if sec1_entries else all_files[0].as_posix(),
            "confidence": 0.82,
            "raw_json": {
                "section_kind_reason": "Structural section index for Vita S. Porphyrii, expressed as Roman-numbered headings without material page refs.",
                "evidence_files": [sec1_entries[0]["file"]] if sec1_entries else [all_files[0].as_posix()],
            },
        },
        {
            "section_key": SECTION2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION2_HEADING,
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1264,
            "page_end": 1273,
            "file_start": sec2_entries[0]["file"] if sec2_entries else all_files[0].as_posix(),
            "file_end": sec2_entries[-1]["file"] if sec2_entries else all_files[-1].as_posix(),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Closing contents table for the tome; each page-linked line is a contents item rather than an alphabetical lemma.",
                "helper_status": helper.get("status"),
                "helper_summary": {
                    "entry_count": len(helper.get("entries", [])) if isinstance(helper, dict) else None,
                    "resolved": sum(1 for entry in helper.get("entries", []) if entry.get("status") == "resolved") if isinstance(helper, dict) else None,
                },
                "evidence_files": [
                    (sec2_entries[0]["file"] if sec2_entries else all_files[0].as_posix()),
                    (sec2_entries[-1]["file"] if sec2_entries else all_files[-1].as_posix()),
                ],
            },
        },
    ]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the section index for Vita S. Porphyrii and the closing Ordo Rerum table from the OCR tail. Section 1 is a structural Roman-numbered index without material refs; section 2 carries page-linked contents entries.",
        "evidence_files": [
            (sec1_entries[0]["file"] if sec1_entries else all_files[0].as_posix()),
            (sec2_entries[0]["file"] if sec2_entries else all_files[0].as_posix()),
            (sec2_entries[-1]["file"] if sec2_entries else all_files[-1].as_posix()),
        ],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "OCR tail contains a section index for Vita S. Porphyrii and the closing Ordo Rerum contents table.",
        },
        "sections": sections,
        "nodes": [],
        "entries": sec1_serialized + sec2_serialized,
        "refs": sec1_refs + sec2_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": [
            "Page-linked contents entries were resolved against the local OCR page map; no scripture references were present.",
            "The section index is structural and intentionally serialized without refs.",
        ],
    }
    write_json(output_file, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG065 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=ROOT / "teste/PG065/text")
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    update_todo(args.intermediate_dir, [
        "Use helper evidence only for page-linked Ordo Rerum entries.",
        "Keep the Roman-numbered section index structural and ref-less.",
    ])
    build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.output_file)
    update_todo(args.intermediate_dir, [
        "Payload written; validate JSON shape and any unresolved target files.",
        "If needed, refine section1 page span from adjacent headers.",
    ])


if __name__ == "__main__":
    main()

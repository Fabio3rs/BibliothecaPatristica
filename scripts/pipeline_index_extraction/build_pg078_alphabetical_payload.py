#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg078_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG078/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG078 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG078_alphabetical_indices.json

Build the PG078 alphabetical-index payload from the OCR tail and run the local
target locator helper on the extracted index entries.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG078"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 78"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

INDEX_START_SEQ = 861
INDEX_END_SEQ = 897
ORDO_SEQ = 898

LETTER_RE = re.compile(r"^[A-ZÆŒΑ-Ω]$")
PAGE_ONLY_RE = re.compile(r"^\d{1,4}$")
REF_TOKEN_RE = re.compile(
    r"(?P<book>[IVXLCDM]{1,6})[.,]?\s*(?P<num1>\d{1,4})(?:\s*(?:,|et)\s*(?P<num2>\d{1,4}))?",
    re.IGNORECASE,
)
REF_ONLY_RE = re.compile(
    r"^(?:[IVXLCDM]{1,6}[.,]?\s*)?\d{1,4}(?:\s*(?:,|et)\s*\d{1,4})*\.?$",
    re.IGNORECASE,
)
LEADING_REF_RE = re.compile(
    r"^(?P<ref>(?:[IVXLCDM]{1,6}[.,]?\s*)?\d{1,4}(?:\s*(?:,|et)\s*\d{1,4})*\.?)\s+(?=[^\d])",
    re.IGNORECASE,
)
TRAILING_BARE_REF_RE = re.compile(
    r"(?<![\w])(?P<num1>\d{1,4})(?:\s*(?:,|et)\s*(?P<num2>\d{1,4}))?\.?$",
    re.IGNORECASE,
)
LEADING_NUMBER_RE = re.compile(r"^(?P<num>\d{1,4})\s+(?=[A-ZΑ-ΩÆŒ])")


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
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm_sort(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def discovered_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def is_boilerplate(line: str) -> bool:
    if not line:
        return True
    if line == "Digitized by Google":
        return True
    if PAGE_ONLY_RE.fullmatch(line):
        return True
    if LETTER_RE.fullmatch(line):
        return True
    if "INDEX ANALYTICUS" in line and len(line) < 120:
        return True
    if "INDEX RERUM" in line and len(line) < 120:
        return True
    if line.startswith("QUÆ IN QUINQUE LIBRIS"):
        return True
    if line.startswith("Numeri Romani Librum"):
        return True
    if line.startswith("FINIS TOMI"):
        return True
    if line.startswith("Parisiis, — Ex typis"):
        return True
    return False


def collect_lines(files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        body = parsed.get("body_text") or ""
        header = parsed.get("header_text") or ""
        footer = parsed.get("footer_text") or ""
        for raw in [header, body, footer]:
            if not raw:
                continue
            for line in raw.splitlines():
                cleaned = normalize(line)
                if not cleaned or is_boilerplate(cleaned):
                    continue
                rows.append({"file": str(path), "text": cleaned})
    return rows


def split_fragments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for row in rows:
        text = row["text"]
        file = row["file"]
        text = re.sub(r"(?<=\.)\s+(?=\d{1,4}\s+[A-ZΑ-ΩÆŒ])", "\n", text)
        text = re.sub(r"(?<=\.)\s+(?=[A-ZΑ-ΩÆŒ])", "\n", text)
        text = re.sub(r"(?<=\bibid\.)\s+(?=[A-ZΑ-ΩÆŒ])", "\n", text, flags=re.IGNORECASE)
        for part in text.split("\n"):
            part = normalize(part)
            if part:
                fragments.append({"file": file, "text": part})
    return fragments


def ref_numbers(text: str) -> list[int]:
    nums: list[int] = []
    for match in REF_TOKEN_RE.finditer(text):
        for key in ("num1", "num2"):
            raw = match.group(key)
            if raw:
                value = int(raw)
                if value not in nums:
                    nums.append(value)
        # Accept a leading book-less number if the token started with digits only.
        if not match.group("book") and match.group("num1"):
            value = int(match.group("num1"))
            if value not in nums:
                nums.append(value)
    tail = TRAILING_BARE_REF_RE.search(normalize(text))
    if tail:
        for key in ("num1", "num2"):
            raw = tail.group(key)
            if raw:
                value = int(raw)
                if value not in nums:
                    nums.append(value)
    return nums


def extract_lemma(text: str) -> str | None:
    value = normalize(text)
    if not value:
        return None
    cutoff = len(value)
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bcf\.\b", r"\bid\.\b"]:
        m = re.search(pattern, value, flags=re.IGNORECASE)
        if m:
            cutoff = min(cutoff, m.start())
    m = REF_TOKEN_RE.search(value)
    if m:
        cutoff = min(cutoff, m.start())
    lemma = value[:cutoff].strip(" ,;:.")
    return lemma or None


def first_letter(lemma: str | None) -> str | None:
    if not lemma:
        return None
    value = norm_sort(lemma)
    if not value:
        return None
    for ch in value:
        if ch.isalpha():
            return ch.upper()
    return None


def line_kind(text: str) -> str:
    stripped = normalize(text)
    if not stripped:
        return "empty"
    if REF_ONLY_RE.fullmatch(stripped):
        return "ref_only"
    if LEADING_NUMBER_RE.match(stripped):
        return "numbered_entry"
    return "entry"


def build_entries(
    fragments: list[dict[str, Any]],
    section_start_file: str,
    ordo_file: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX ANALYTICUS.",
            "heading_norm": "index analyticus",
            "heading_letter": None,
            "page_start": 1710,
            "page_end": 1781,
            "file_start": section_start_file,
            "file_end": str(Path(fragments[-1]["file"])) if fragments else section_start_file,
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Analytical alphabetical index of the five books of S. Isidorus Pelusiota; the OCR tail begins with the index head and continues through the A-Z lemma run.",
                "evidence_files": sorted({frag["file"] for frag in fragments}),
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1783,
            "page_end": 1784,
            "file_start": ordo_file or (str(Path(fragments[-1]["file"])) if fragments else section_start_file),
            "file_end": ordo_file or (str(Path(fragments[-1]["file"])) if fragments else section_start_file),
            "confidence": 0.95,
            "raw_json": {
                "section_kind_reason": "Closing contents table, editorial closure distinct from the alphabetical index.",
                "evidence_files": [ordo_file] if ordo_file else ([str(Path(fragments[-1]["file"]))] if fragments else [section_start_file]),
            },
        },
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    node_map: dict[str, str] = {}
    node_order = 1
    entry_order = 1

    pending: dict[str, Any] | None = None
    last_file = section_start_file

    def ensure_letter(letter: str, source_file: str) -> str:
        nonlocal node_order
        if letter in node_map:
            return node_map[letter]
        node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:letter:{letter}"
        node_map[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source_file": source_file},
            }
        )
        node_order += 1
        return node_key

    def finalize_pending() -> None:
        nonlocal pending, entry_order
        if not pending:
            return
        lemma = pending.get("lemma_raw") or extract_lemma(pending["entry_raw"])
        page_hints = pending.get("page_hints") or []
        letter = first_letter(lemma)
        parent_node_key = ensure_letter(letter, pending["source_file"]) if letter else None
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "parent_node_key": parent_node_key,
            "entry_order": entry_order,
            "entry_kind": "cross_reference" if re.search(r"\b(?:Vide|vid\.|cf\.|id\.)\b", pending["entry_raw"], flags=re.IGNORECASE) and not page_hints else "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": norm_sort(lemma),
            "lemma_sort": norm_sort(lemma),
            "entry_raw": pending["entry_raw"].strip(),
            "context_raw": pending["entry_raw"].strip() if len(pending["entry_raw"]) < 220 else pending["entry_raw"][:220].strip(),
            "heading_letter": letter,
            "inferred_printed_page": page_hints[0] if page_hints else None,
            "section_start_file": section_start_file,
            "editorial_anchor_file": pending["source_file"],
            "target_file_best": None,
            "confidence": 0.82 if page_hints else 0.68,
            "raw_json": {
                "source_file": pending["source_file"],
                "page_hints": page_hints,
                "segment_kind": pending.get("segment_kind"),
            },
        }
        entries.append(entry)
        for ref_order, page_hint in enumerate(page_hints, start=1):
            ref_key = f"{entry_key}:ref:{ref_order:03d}"
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": str(page_hint),
                    "page_ref_raw": str(page_hint),
                    "page_ref_int": page_hint,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": pending["source_file"],
                    "confidence": 0.68,
                    "raw_json": {
                        "ref_key": ref_key,
                        "source_file": pending["source_file"],
                        "segment_text": pending["entry_raw"][:240],
                    },
                }
            )
        entry_order += 1
        pending = None

    for frag in fragments:
        text = normalize(frag["text"])
        if not text or is_boilerplate(text):
            continue
        if pending:
            lead_ref = LEADING_REF_RE.match(text)
            if lead_ref:
                ref_text = normalize(lead_ref.group("ref"))
                for value in ref_numbers(ref_text):
                    if value not in pending["page_hints"]:
                        pending["page_hints"].append(value)
                pending["entry_raw"] = f"{pending['entry_raw']} {ref_text}".strip()
                text = normalize(text[lead_ref.end():])
                if not text:
                    continue
        kind = line_kind(text)
        if pending and kind != "ref_only" and LEADING_NUMBER_RE.match(text):
            number = int(LEADING_NUMBER_RE.match(text).group("num"))
            if number not in pending["page_hints"]:
                pending["page_hints"].append(number)
            text = LEADING_NUMBER_RE.sub("", text, count=1).strip()
            kind = line_kind(text)
        if kind == "ref_only":
            page_hints = ref_numbers(text)
            if pending:
                for value in page_hints:
                    if value not in pending["page_hints"]:
                        pending["page_hints"].append(value)
                pending["entry_raw"] = f"{pending['entry_raw']} {text}".strip()
                continue
            # orphaned reference line, keep it as a tiny unresolved note entry
            pending = {
                "source_file": frag["file"],
                "entry_raw": text,
                "lemma_raw": extract_lemma(text),
                "page_hints": page_hints,
                "segment_kind": "orphan_ref",
            }
            finalize_pending()
            continue
        if pending:
            finalize_pending()
        pending = {
            "source_file": frag["file"],
            "entry_raw": text,
            "lemma_raw": extract_lemma(text),
            "page_hints": ref_numbers(text),
            "segment_kind": "entry",
        }
        last_file = frag["file"]

    finalize_pending()
    return sections, nodes, entries, refs


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path, source_root: Path) -> None:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_hints = [int(v) for v in (entry.get("raw_json", {}).get("page_hints") or [])]
        if not page_hints:
            continue
        lemma = entry.get("lemma_raw") or entry.get("entry_raw")
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [q for q in [lemma, entry.get("lemma_norm"), entry.get("entry_raw")] if q],
                "page_hints": [str(v) for v in page_hints[:4]],
                "page_hint_ints": page_hints[:4],
                "context_raw": entry.get("entry_raw"),
            }
        )
    write_json(
        helper_request_json,
        {
            "volume_id": VOLUME_ID,
            "source_root": str(source_root),
            "options": {"top_k": 5, "adjacency_window": 2},
            "entries": helper_entries,
        },
    )


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    request_payload = read_json(helper_request_json, {})
    request_entries = request_payload.get("entries") or []
    if len(request_entries) <= 400:
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
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return read_json(helper_output_json, {})

    merged_entries: list[dict[str, Any]] = []
    chunk_size = 250
    with tempfile.TemporaryDirectory(prefix="pg078_helper_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for start in range(0, len(request_entries), chunk_size):
            chunk_entries = request_entries[start : start + chunk_size]
            chunk_input = tmpdir_path / f"chunk_{start:05d}.json"
            chunk_output = tmpdir_path / f"chunk_{start:05d}_out.json"
            chunk_payload = {
                "volume_id": request_payload.get("volume_id"),
                "source_root": request_payload.get("source_root"),
                "options": request_payload.get("options") or {},
                "entries": chunk_entries,
            }
            write_json(chunk_input, chunk_payload)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_TARGET_LOCATOR),
                    "--input",
                    str(chunk_input),
                    "--output",
                    str(chunk_output),
                    "--pretty",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
            if proc.returncode != 0:
                raise SystemExit(
                    f"index_target_locator.py failed on chunk starting {start}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
                )
            merged_entries.extend((read_json(chunk_output, {}) or {}).get("entries") or [])

    merged_payload = {
        "volume_id": request_payload.get("volume_id"),
        "source_root": request_payload.get("source_root"),
        "options_used": request_payload.get("options") or {},
        "entries": merged_entries,
    }
    write_json(helper_output_json, merged_payload)
    return merged_payload


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


def helper_summary(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates: list[dict[str, Any]] = []
    for cand in (item.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in (cand.get("evidence") or [])[:4]],
            }
        )
    return {
        "status": item.get("status"),
        "candidate_role": item.get("candidate_role"),
        "reason_summary": item.get("reason_summary"),
        "best_candidate": best,
        "top_candidates": candidates,
    }


def assemble_payload(intermediate_dir: Path, generated_at: str | None = None) -> dict[str, Any]:
    manifest = read_json(intermediate_dir / "manifest.json", {})
    volume = read_json(intermediate_dir / "volume.json")
    coverage = read_json(intermediate_dir / "coverage.json", {})
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at or manifest.get("generated_at") or manifest.get("updated_at"),
        "volume": volume,
        "sections": read_json(intermediate_dir / "sections.json", []),
        "nodes": read_json(intermediate_dir / "nodes.json", []),
        "entries": read_json(intermediate_dir / "entries.json", []),
        "refs": read_json(intermediate_dir / "refs.json", []),
        "scripture_refs": read_json(intermediate_dir / "scripture_refs.json", []),
        "coverage": coverage,
        "notes": read_json(intermediate_dir / "notes.json", []),
    }
    if payload["generated_at"] is None:
        raise SystemExit("generated_at is required via manifest.json or --generated-at")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG078 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = discovered_files(args.source_root)
    index_files = [path for path in files if INDEX_START_SEQ <= file_seq(path) <= INDEX_END_SEQ]
    ordo_file = next((path for path in files if file_seq(path) == ORDO_SEQ), None)
    if not index_files:
        raise SystemExit("No index OCR files found for PG078")

    rows = collect_lines(index_files)
    fragments = split_fragments(rows)
    sections, nodes, entries, refs = build_entries(fragments, str(index_files[0]), str(ordo_file) if ordo_file else None)

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
    }
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytic alphabetical index tail and the closing Ordo Rerum from the OCR files; entries were segmented conservatively from the local OCR text.",
        "evidence_files": [str(path) for path in index_files[:4]] + [str(index_files[-1])] + ([str(ordo_file)] if ordo_file else []),
    }
    notes = [
        "The OCR tail contains one long INDEX ANALYTICUS run followed by a closing ORDO RERUM table.",
        "Because the index citations are internal book/epistle locators, helper requests were generated per entry from the extracted page hints.",
        "Entry segmentation is conservative: a few OCR runs split across files or contain damaged glyphs, so helper evidence is preserved in raw_json.",
    ]

    build_helper_request(entries, args.helper_request_json, args.source_root)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_map = helper_lookup(helper_output)

    for entry in entries:
        helper_item = helper_map.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        if best.get("file"):
            entry["target_file_best"] = best.get("file")
        if best.get("probability") is not None:
            entry["confidence"] = max(entry["confidence"], float(best.get("probability")))
        entry.setdefault("raw_json", {})["helper"] = helper_summary(helper_item)

    for ref in refs:
        helper_item = helper_map.get(ref["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            if best.get("probability") is not None:
                ref["confidence"] = max(ref["confidence"], float(best.get("probability")))
        ref.setdefault("raw_json", {})["helper"] = helper_summary(helper_item)

    # Propagate a per-entry target from the first ref when the helper found one.
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for entry in entries:
        ref_list = refs_by_entry.get(entry["entry_key"]) or []
        for ref in ref_list:
            if ref.get("target_file"):
                entry["target_file_best"] = ref["target_file"]
                break

    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()})
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PG078 alphabetical payload",
            "completed": [
                "OCR tail segmented",
                "helper request generated",
                "helper output applied",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The index is a long analytic alphabetical run with a closing ORDO RERUM table.",
                "If a few OCR fragments remain imperfect, preserve them rather than merging across lemmatized boundaries.",
            ],
        },
    )

    payload = assemble_payload(args.intermediate_dir, generated_at=now_iso())
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

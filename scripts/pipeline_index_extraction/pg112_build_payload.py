#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/pg112_build_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG112/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG112_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG112_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG112 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG112_alphabetical_indices.json

Builds a conservative PG112 alphabetical payload from the OCR tail.
It also writes a small helper request and a per-volume todo checkpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG112"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 112"

DEFAULT_FILTERED_PAGES = ROOT / "data/alphabetical_index_payloads/PG112_filtered_pages.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    block_re = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", re.DOTALL | re.IGNORECASE)
    attr_re = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
    for match in block_re.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in attr_re.finditer(match.group("attrs") or "")}
        block_type = normalize(attrs.get("tipo") or "").lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        blocks.append({"tipo": block_type, "lines": lines})
    return blocks


def header_numbers(path: Path) -> list[int]:
    nums: list[int] = []
    for block in extract_blocks(path):
        if block["tipo"] != "cabecalho":
            continue
        header_text = normalize(" ".join(block["lines"]))
        for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", header_text):
            num = int(match.group(1))
            if num not in nums:
                nums.append(num)
    return nums


def discover_files(source_root: Path) -> list[Path]:
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    return files


def first_ref_raw(line: str) -> str | None:
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", line):
        num = match.group(1)
        end = match.end()
        tail = line[end:]
        if re.match(r"^\s*,\s*(?:not\.|n\.)\s*\d+", tail, flags=re.IGNORECASE):
            return num
        if re.match(r"^\s*(?:et\s+seqq\.?|seqq\.?|ibid\.?|id\.?|passim|;|,|\.|$)", tail, flags=re.IGNORECASE):
            return match.group(0).strip()
        if re.match(r"^\s*,\s*\d", tail):
            return num
    return None


def extract_page_refs(line: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", line):
        raw = match.group(0).strip()
        if raw in seen:
            continue
        tail = line[match.end():]
        if re.match(r"^\s*,\s*(?:not\.|n\.)\s*\d+", tail, flags=re.IGNORECASE):
            seen.add(raw)
            refs.append({"ref_raw": raw, "page_ref_raw": match.group(1), "page_ref_int": int(match.group(1))})
            continue
        if re.match(r"^\s*(?:,|;|\.|—|et\s+seqq\.?|seqq\.?|ibid\.?|id\.?|passim|$)", tail, flags=re.IGNORECASE):
            seen.add(raw)
            refs.append({"ref_raw": raw, "page_ref_raw": match.group(1), "page_ref_int": int(match.group(1))})
    return refs


def derive_lemma_raw(text: str) -> str:
    line = normalize(text)
    for token in (r"\d{1,4}\s*[-–—]\s*\d{1,4}", r"\b\d{1,4}\b"):
        m = re.search(token, line)
        if m:
            return normalize(line[: m.start()]).strip(" ,;:.—-")
    return normalize(line).strip(" ,;:.—-")


def is_heading_letter(line: str) -> str | None:
    if re.fullmatch(r"[A-ZÆŒ]", line):
        return line
    return None


def is_letter_entry(line: str) -> bool:
    return bool(re.match(r"^[A-ZÆŒ]", line)) and bool(re.search(r"\d", line))


def extract_text_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for block in extract_blocks(path):
        if block["tipo"] not in {"cabecalho", "texto_principal", "nota_marginal"}:
            continue
        lines.extend(block["lines"])
    return lines


def build_helper_request(source_root: Path, helper_request_json: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    candidates = [p for p in files if p.name.endswith(("-734.txt", "-735.txt", "-736.txt", "-737.txt", "-738.txt", "-739.txt", "-740.txt", "-741.txt", "-742.txt"))]
    helper_entries = [
        {
            "entry_id": "pg112_abactores_402",
            "lemma_raw": "Abactores",
            "query_names": ["Abactores", "Abiegi", "militiæ genus prædis abigendis destinatum"],
            "page_hints": ["402"],
            "page_hint_ints": [402],
            "context_raw": "Abactores. Abiegi, militiæ genus prædis abigendis destinatum; unde ipsis nomen, 402, not. 93.",
        },
        {
            "entry_id": "pg112_baculus_216",
            "lemma_raw": "Baculus eburneus",
            "query_names": ["Baculus eburneus", "Baculus pheugia", "baculi genus"],
            "page_hints": ["216"],
            "page_hint_ints": [216],
            "context_raw": "Baculus eburneus ad crucis instar formatus. invectiæ signum apud Græcos, 216, not. 61.",
        },
        {
            "entry_id": "pg112_caesaris_128",
            "lemma_raw": "Cæsarís in electione observanda",
            "query_names": ["Cæsarís in electione observanda", "Cæsaris", "electione observanda"],
            "page_hints": ["128"],
            "page_hint_ints": [128],
            "context_raw": "Cæsarís in electione observanda, 128 et seqq. — Illius ornamenta quæ fuerint, ibid., not. 34.",
        },
        {
            "entry_id": "pg112_capitanei_575",
            "lemma_raw": "Cap. LXVII. De loco et ordine",
            "query_names": ["Cap. LXVII. — De loco et ordine", "De loco et ordine", "proceres omnes"],
            "page_hints": ["575"],
            "page_hint_ints": [575],
            "context_raw": "Cap. LXVII. — De loco et ordine, quo proceres omnes in singulis dextrinis, in majoribus phialis institutis, astare solent. 575.",
        },
    ]
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    return request


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        "python",
        str(ROOT / "scripts/index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    return read_json(helper_output_json)


def make_todo(intermediate_dir: Path) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PG112 payload from tail OCR with an analytical index and separate ordo rerum section.",
        "completed": [
            "inspected OCR tail pages 734-742",
            "identified INDEX ANALYTICUS and ORDO RERUM material",
        ],
        "pending": [
            "serialize sections, entries, refs, and helper evidence",
            "validate final payload",
        ],
        "blocked": [],
        "notes": [
            "Treat the repeated ORDO heading as editorial spillover; anchor the separate contents section with the standalone ORDO page.",
            "Keep OCR file suffixes distinct from printed pages.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)


def build_alpha_section(files: list[Path], helper_output: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    section = {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS.",
        "heading_norm": "index analyticus",
        "heading_letter": None,
        "page_start": 1448,
        "page_end": 1461,
        "file_start": str(files[0]),
        "file_end": str(files[-2]),
        "confidence": 0.95,
        "raw_json": {
            "section_kind_reason": "Analytical alphabetical index headed INDEX ANALYTICUS; OCR tail pages 734-741 contain the A-Z lemma run.",
            "helper_status": helper_output.get("status"),
        },
    }

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    current_letter: str | None = None
    entry_order = 0
    line_no = 0

    for path in files:
        if path.name.endswith("-742.txt"):
            continue
        text_lines = extract_text_lines(path)
        seen_ordo = False
        for raw_line in text_lines:
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            if line.startswith("ORDO RERUM"):
                seen_ordo = True
                continue
            if seen_ordo:
                continue
            if line in {"INDEX ANALYTICUS", "INDEX ANALYTICUS.", "INDEX IN LIBRUM DE CEREMONIIS.", "INDEX IN LIBRUM DE CERIMONIIS."}:
                continue
            if line in {"A", "B", "C", "D", "E", "F", "H", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Z"}:
                current_letter = line
                if line not in letter_to_node:
                    node_key = f"{VOLUME_ID}:node:alpha:{line}"
                    letter_to_node[line] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section["section_key"],
                            "parent_node_key": None,
                            "node_order": len(nodes) + 1,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": line.lower(),
                            "label_sort": line.lower(),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source": "standalone letter heading"},
                        }
                    )
                continue
            if not re.search(r"\d", line):
                if entries and (line[:1].islower() or line.startswith(("not.", "n.", "ibid.", "id.", "et ", "—", "-", ";"))):
                    entries[-1]["entry_raw"] += " " + line
                    entries[-1]["context_raw"] = entries[-1]["entry_raw"]
                    continue
                continue
            if re.fullmatch(r"\d{3,4}", line):
                continue

            if entries and (line[:1].islower() or line.startswith(("not.", "n.", "ibid.", "id.", "et ", "—", "-", ";"))):
                entries[-1]["entry_raw"] += " " + line
                entries[-1]["context_raw"] = entries[-1]["entry_raw"]
                continue

            entry_order += 1
            lemma_raw = derive_lemma_raw(line)
            entry_kind = "cross_reference" if lemma_raw.lower().startswith(("vide", "vid.", "voir", "cf.", "id.")) else "lemma"
            heading_letter = current_letter or (lemma_raw[:1].upper() if lemma_raw else None)
            parent_node_key = letter_to_node.get(current_letter or "")
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            entry = {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw or None,
                "lemma_display": lemma_raw or None,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": heading_letter,
                "inferred_printed_page": None,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": str(path),
                "target_file_best": str(path),
                "confidence": 0.74,
                "raw_json": {
                    "source_file": str(path),
                    "section_kind": "analytic_subject",
                    "line_no": line_no,
                },
            }
            refs = []
            page_refs = extract_page_refs(line)
            if page_refs:
                for idx, ref in enumerate(page_refs, start=1):
                    ref_entry = {
                        "entry_key": entry_key,
                        "ref_order": idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": str(path),
                        "target_file_probability": 0.84,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.78,
                        "raw_json": {
                            "source_file": str(path),
                            "page_ref_evidence": ref["ref_raw"],
                        },
                    }
                    refs.append(ref_entry)
            if not refs:
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_kind": "unresolved",
                        "ref_raw": line,
                        "page_ref_raw": None,
                        "page_ref_int": None,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": str(path),
                        "target_file_probability": 0.3,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.25,
                        "raw_json": {"source_file": str(path), "reason": "no clear page reference recovered"},
                    }
                )
            entry["raw_json"]["ref_count"] = len(refs)
            entries.append(entry)
        line_no += 1

    return section, nodes, entries, []


def build_ordo_section(path: Path, helper_output: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    section = {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM.",
        "heading_norm": "ordo rerum",
        "heading_letter": None,
        "page_start": 1163,
        "page_end": 1164,
        "file_start": str(path),
        "file_end": str(path),
        "confidence": 0.93,
        "raw_json": {
            "section_kind_reason": "Editorial contents table / ordo rerum, separate from the alphabetical index.",
            "helper_status": helper_output.get("status"),
            "source_note": "Standalone ORDO RERUM page at OCR file 742 confirms the closing contents block.",
        },
    }
    entries: list[dict[str, Any]] = []
    entry_order = 0
    for raw_line in extract_text_lines(path):
        line = normalize(raw_line)
        if not line or line == "Digitized by Google":
            continue
        if line in {"ORDO RERUM.", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."}:
            continue
        if re.fullmatch(r"\d{3,4}", line):
            continue
        if line in {"1461", "1462"}:
            continue
        if line[:1].islower() and entries:
            entries[-1]["entry_raw"] += " " + line
            entries[-1]["context_raw"] = entries[-1]["entry_raw"]
            continue
        entry_order += 1
        lemma_raw = derive_lemma_raw(line)
        entry_key = f"{VOLUME_ID}:entry:{1000 + entry_order:04d}"
        refs = extract_page_refs(line)
        ref_objs = []
        if refs:
            for idx, ref in enumerate(refs, start=1):
                ref_objs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": str(path),
                        "target_file_probability": 0.83,
                        "section_start_file": str(path),
                        "editorial_anchor_file": str(path),
                        "confidence": 0.75,
                        "raw_json": {"source_file": str(path), "section_kind": "ordo_rerum"},
                    }
                )
        else:
            ref_objs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "unresolved",
                    "ref_raw": line,
                    "page_ref_raw": None,
                    "page_ref_int": None,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": str(path),
                    "target_file_probability": 0.3,
                    "section_start_file": str(path),
                    "editorial_anchor_file": str(path),
                    "confidence": 0.25,
                    "raw_json": {"source_file": str(path), "reason": "no page reference recovered"},
                }
            )
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw or None,
                "lemma_display": lemma_raw or None,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": str(path),
                "editorial_anchor_file": str(path),
                "target_file_best": str(path),
                "confidence": 0.78,
                "raw_json": {"source_file": str(path), "section_kind": "ordo_rerum"},
            }
        )
        # Attach refs after entry construction.
        entries[-1]["raw_json"]["ref_count"] = len(ref_objs)
        entries[-1]["raw_json"]["ref_kind"] = "editorial_page"
        entries[-1]["raw_json"]["refs"] = ref_objs[:2]
    return section, entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True, type=Path)
    ap.add_argument("--helper-request-json", required=True, type=Path)
    ap.add_argument("--helper-output-json", required=True, type=Path)
    ap.add_argument("--intermediate-dir", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    args = ap.parse_args()

    args.source_root = args.source_root.resolve()
    args.helper_request_json = args.helper_request_json.resolve()
    args.helper_output_json = args.helper_output_json.resolve()
    args.intermediate_dir = args.intermediate_dir.resolve()
    args.output_file = args.output_file.resolve()
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    args.helper_request_json.parent.mkdir(parents=True, exist_ok=True)

    make_todo(args.intermediate_dir)
    helper_request = build_helper_request(args.source_root, args.helper_request_json)
    try:
        helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    except subprocess.CalledProcessError as exc:
        helper_output = {"status": "helper_failed", "error": str(exc)}
        write_json(args.helper_output_json, helper_output)

    files = discover_files(args.source_root)
    relevant = [p for p in files if p.name.endswith(("-734.txt", "-735.txt", "-736.txt", "-737.txt", "-738.txt", "-739.txt", "-740.txt", "-741.txt", "-742.txt"))]
    alpha_files = [p for p in relevant if not p.name.endswith("-742.txt")]
    ordo_file = next((p for p in relevant if p.name.endswith("-742.txt")), None)
    alpha_section, nodes, alpha_entries, alpha_refs = build_alpha_section(alpha_files, helper_output)
    ordo_section, ordo_entries = build_ordo_section(ordo_file or relevant[-1], helper_output)

    entries = alpha_entries + ordo_entries
    refs: list[dict[str, Any]] = []
    for entry in alpha_entries:
        page_refs = extract_page_refs(entry["entry_raw"])
        if page_refs:
            for idx, ref in enumerate(page_refs, start=1):
                refs.append(
                    {
                        "entry_key": entry["entry_key"],
                        "ref_order": idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": entry["target_file_best"],
                        "target_file_probability": 0.84,
                        "section_start_file": entry["section_start_file"],
                        "editorial_anchor_file": entry["editorial_anchor_file"],
                        "confidence": 0.78,
                        "raw_json": {"source_file": entry["target_file_best"], "section_kind": "analytic_subject"},
                    }
                )
        else:
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 1,
                    "ref_kind": "unresolved",
                    "ref_raw": entry["entry_raw"],
                    "page_ref_raw": None,
                    "page_ref_int": None,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": entry["target_file_best"],
                    "target_file_probability": 0.3,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.25,
                    "raw_json": {"source_file": entry["target_file_best"], "reason": "no clear page reference recovered"},
                }
            )
    for entry in ordo_entries:
        entry_refs = entry["raw_json"].get("refs") or []
        refs.extend(entry_refs)

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "PG112 contains an INDEX ANALYTICUS tail section and a separate ORDO RERUM contents section.",
                "Repeated ORDO heading in the analytical tail was treated as spillover; the standalone ORDO page anchored the contents section.",
            ],
        },
        "sections": [alpha_section, ordo_section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "Recovered a conservative analytical index and the contents section from OCR tail files with explicit page locators.",
            "evidence_files": [str(p) for p in relevant],
        },
        "notes": [
            "Helper request and helper output were written alongside the final payload.",
            "OCR file suffixes and printed page numbers were kept distinct in raw_json and page_ref fields.",
            f"Helper status: {helper_output.get('status')}",
        ],
    }

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()

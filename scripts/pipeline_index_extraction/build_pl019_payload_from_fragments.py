#!/usr/bin/env python3
"""Usage: rebuild PL019 alphabetical payload from assembled fragments and OCR-backed locator repair.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl019_payload_from_fragments.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL019"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
ASSEMBLED_PATH = INTERMEDIATE_DIR / "assembled_fragments.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"
LOCATOR_CHUNK_RUNNER = ROOT / "scripts" / "pipeline_index_extraction" / "run_index_target_locator_in_chunks.py"

ORDO_SECTION_KEY = "PL019:section:001"
ORDO_FILE_518 = SOURCE_ROOT / "28f9984f-b1b8-4590-a4cb-ec017c316d06-518.txt"
ORDO_FILE_519 = SOURCE_ROOT / "28f9984f-b1b8-4590-a4cb-ec017c316d06-519.txt"

BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
SUFFIX_RE = re.compile(r"-(\d+)\.txt$")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
RANGE_RE = re.compile(r"(\d{1,4})\s*-\s*(\d{1,4})")
IBID_RE = re.compile(r"\bIbid\.", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def file_seq(path: str | Path) -> int:
    match = SUFFIX_RE.search(str(path))
    if not match:
        raise ValueError(f"Could not extract OCR suffix from {path}")
    return int(match.group(1))


def top_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8")
    blocks = [(kind, normalize_space(strip_tags(inner))) for kind, inner in BLOCK_RE.findall(raw)]
    return [(kind, text) for kind, text in blocks if text]


def block_text_lines(path: Path, prefix: str) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    for kind, inner in BLOCK_RE.findall(raw):
        if kind != "texto_principal":
            continue
        plain = strip_tags(inner)
        lines = [normalize_space(line) for line in plain.splitlines() if normalize_space(line)]
        if lines and lines[0].startswith(prefix):
            return lines
    raise ValueError(f"Could not find text block starting with {prefix!r} in {path}")


def header_candidate_start(path: Path) -> int | None:
    candidates: list[int] = []
    for kind, text in top_blocks(path)[:4]:
        if kind not in {"cabecalho", "outro", "texto_principal"}:
            continue
        nums = [int(m.group(1)) for m in HEADER_NUM_RE.finditer(text)]
        if len(nums) >= 2:
            nums = sorted(nums[:2])
            if nums[1] - nums[0] == 1 and nums[0] <= 1500:
                candidates.append(nums[0])
        elif len(nums) == 1 and nums[0] <= 1500:
            candidates.append(nums[0])
    if not candidates:
        return None
    return min(candidates)


def build_page_map() -> dict[int, str]:
    files = sorted(SOURCE_ROOT.glob("*.txt"), key=file_seq)
    seq_to_file = {file_seq(path): str(path) for path in files}
    raw_start: dict[int, int | None] = {seq: header_candidate_start(path) for seq, path in ((file_seq(p), p) for p in files)}
    candidates = [(seq, start) for seq, start in sorted(raw_start.items()) if isinstance(start, int)]
    if not candidates:
        return {}

    next_links: dict[int, list[int]] = defaultdict(list)
    for i, (seq_i, start_i) in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            seq_j, start_j = candidates[j]
            if seq_j - seq_i > 10:
                break
            if abs((start_j - start_i) - 2 * (seq_j - seq_i)) <= 2:
                next_links[i].append(j)

    best_len = [1] * len(candidates)
    best_next: list[int | None] = [None] * len(candidates)
    for i in range(len(candidates) - 1, -1, -1):
        for j in next_links.get(i, []):
            if 1 + best_len[j] > best_len[i]:
                best_len[i] = 1 + best_len[j]
                best_next[i] = j

    start_idx = max(range(len(candidates)), key=lambda idx: best_len[idx])
    reliable: list[tuple[int, int]] = []
    idx = start_idx
    while idx is not None:
        reliable.append(candidates[idx])
        idx = best_next[idx]

    resolved_start: dict[int, int] = {seq: start for seq, start in reliable}
    for idx in range(1, len(reliable)):
        prev_seq, prev_start = reliable[idx - 1]
        seq, _ = reliable[idx]
        for missing_seq in range(prev_seq + 1, seq):
            resolved_start[missing_seq] = prev_start + 2 * (missing_seq - prev_seq)

    page_map: dict[int, str] = {}
    for seq, start in resolved_start.items():
        path = seq_to_file.get(seq)
        if not path:
            continue
        page_map[start] = path
        page_map[start + 1] = path
    return page_map


def extract_page_hints(entry_raw: str, refs: list[dict[str, Any]]) -> list[int]:
    hints: list[int] = []
    for ref in refs:
        page = ref.get("page_ref_int")
        if isinstance(page, int) and page not in hints:
            hints.append(page)
    if not hints:
        for match in HEADER_NUM_RE.finditer(entry_raw):
            value = int(match.group(1))
            if value <= 1500 and value not in hints:
                hints.append(value)
    return hints


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        if entry["section_key"] == "PL019:section:004:juvenci_phrases":
            continue
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        page_hints = extract_page_hints(entry["entry_raw"], entry_refs)
        if not page_hints:
            continue
        lemma = entry.get("lemma_raw") or entry["entry_raw"][:120]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [lemma, (entry["entry_raw"].split(",", 1)[0] or lemma).strip()],
                "page_hints": [str(v) for v in page_hints[:6]],
                "page_hint_ints": page_hints[:6],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"max_candidates": 5},
        "entries": helper_entries,
    }


def run_helper() -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            str(LOCATOR_CHUNK_RUNNER),
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--chunk-size",
            "80",
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return read_json(HELPER_OUTPUT_PATH)


def parse_ordo_blocks() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    section_start = str(ORDO_FILE_518)
    blocks_518 = top_blocks(ORDO_FILE_518)
    blocks_519 = top_blocks(ORDO_FILE_519)

    right_518 = next(text for kind, text in blocks_518 if kind == "texto_principal" and text.startswith("SEDULII CARMEN PASCHALE"))
    left_518 = next(text for kind, text in blocks_518 if kind == "texto_principal" and text.startswith("JUVENCUS."))
    left_519 = next(text for kind, text in blocks_519 if kind == "texto_principal" and text.startswith("Idem. — XXVII."))
    right_519 = next(text for kind, text in blocks_519 if kind == "texto_principal" and text.startswith("cundo, grammatico"))

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    headings = [
        ("PL019:section:001:node:juvencus", "JUVENCUS.", left_518, str(ORDO_FILE_518)),
        (
            "PL019:section:001:node:sedulius_paschale",
            "SEDULII CARMEN PASCHALE, CUI SUBJACET ET CONTINENTER RESPONDET OPUS PASCHALE.",
            right_518,
            str(ORDO_FILE_518),
        ),
        ("PL019:section:001:node:epigrammata", "EPIGRAMMATA.", left_519, str(ORDO_FILE_519)),
        ("PL019:section:001:node:commemoratio", "COMMEMORATIO PROFESSORUM BURDIGALENSIUM.", right_519, str(ORDO_FILE_519)),
    ]

    for order, (node_key, label, _, source_file) in enumerate(headings, start=1):
        nodes.append(
            {
                "node_key": node_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": None,
                "node_order": order,
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize_space(label).lower(),
                "label_sort": normalize_space(label).lower(),
                "node_level": 1,
                "confidence": 0.97,
                "raw_json": {"source_file": source_file, "role": "ordo_rerum_heading"},
            }
        )

    current_pages: list[int] = []
    entry_order = 1

    def add_entry(parent_node_key: str, source_file: str, text: str, label: str | None = None) -> None:
        nonlocal entry_order, current_pages
        cleaned = normalize_space(text)
        if not cleaned:
            return
        ranges = [(int(a), int(b)) for a, b in RANGE_RE.findall(cleaned)]
        pages = current_pages.copy()
        if ranges:
            pages = list(range(ranges[-1][0], ranges[-1][1] + 1))
        else:
            nums = [int(m.group(1)) for m in HEADER_NUM_RE.finditer(cleaned)]
            if nums:
                last_num = nums[-1]
                if last_num <= 1500:
                    pages = [last_num]
                    current_pages = [last_num]
        inferred_page = pages[0] if pages else None
        entry_key = f"{ORDO_SECTION_KEY}:entry:{entry_order:04d}"
        entry_label = label or cleaned.split(". ", 1)[0]
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": ORDO_SECTION_KEY,
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": entry_label,
                "lemma_display": entry_label,
                "lemma_norm": normalize_space(entry_label).lower(),
                "lemma_sort": normalize_space(entry_label).lower(),
                "entry_raw": cleaned,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": inferred_page,
                "section_start_file": section_start,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.9,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": "ordo_rerum",
                    "parse_strategy": "block_resegmented_from_assembled_fragment",
                    "page_hints": pages,
                },
            }
        )
        for ref_order, page in enumerate(pages, start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page" if len(pages) == 1 else "editorial_range",
                    "ref_raw": str(page) if len(pages) == 1 else f"{pages[0]}-{pages[-1]}",
                    "page_ref_raw": str(page) if len(pages) == 1 else None,
                    "page_ref_int": page if len(pages) == 1 else None,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(pages[0]) if len(pages) > 1 else None,
                    "range_end_raw": str(pages[-1]) if len(pages) > 1 else None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": section_start,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.88,
                    "raw_json": {"source_file": source_file, "locator_method": "range_parse"},
                }
            )
        entry_order += 1

    # JUVENCUS / SEDULIUS block
    juv_block_lines = block_text_lines(ORDO_FILE_518, "JUVENCUS.")
    current_node = "PL019:section:001:node:juvencus"
    buffer: list[str] = []
    for raw_line in juv_block_lines:
        line = normalize_space(raw_line)
        if line in {"JUVENCUS.", "SEDULIUS."}:
            if buffer:
                add_entry(current_node, str(ORDO_FILE_518), " ".join(buffer))
                buffer = []
            current_node = "PL019:section:001:node:juvencus" if line == "JUVENCUS." else "PL019:section:001:node:sedulius_paschale"
            continue
        if re.match(r"^(?:CAP(?:UT)?\.|PROLEGOMENA|HISTORIÆ|LIBER|CARMEN|TRIUMPHUS|PORPHYRIUS|PANEGYRICUS|Epistola|Testimonia|CARMINA\.)", line):
            if buffer:
                add_entry(current_node, str(ORDO_FILE_518), " ".join(buffer))
                buffer = []
        buffer.append(line)
    if buffer:
        add_entry(current_node, str(ORDO_FILE_518), " ".join(buffer))

    sed_block_lines = block_text_lines(ORDO_FILE_518, "SEDULII CARMEN PASCHALE")
    buffer = []
    for raw_line in sed_block_lines:
        line = normalize_space(raw_line)
        if line == "EPIGRAMMATA. Ibid.":
            if buffer:
                add_entry("PL019:section:001:node:sedulius_paschale", str(ORDO_FILE_518), " ".join(buffer))
                buffer = []
            current_node = "PL019:section:001:node:epigrammata"
            buffer.append(line)
            continue
        if re.match(r"^(?:Dedicatio|Prologus|LIBER|Elegia|Hymnus|Epigramma|APPENDIX|Carmen|Carmina|DECRETUM|PARS|SEVERUS|FALTONIA|CENTONES|AUSONIUS|In Ausonium|PRÆFATIUNCULÆ|Epistola )", line):
            if buffer and not buffer[-1].endswith("—XXVI."):
                add_entry(current_node, str(ORDO_FILE_518), " ".join(buffer))
                buffer = []
        buffer.append(line)
    if buffer:
        add_entry(current_node, str(ORDO_FILE_518), " ".join(buffer))

    split_519 = re.split(r"(?=(?:EPHEMERIS\.|PARENTALIA\.|COMMEMORATIO PROFESSORUM BURDIGALENSIUM\.))", left_519)
    for part in split_519:
        part = normalize_space(part)
        if not part:
            continue
        node_key = "PL019:section:001:node:epigrammata"
        if part.startswith("PARENTALIA."):
            node_key = "PL019:section:001:node:epigrammata"
        if part.startswith("COMMEMORATIO PROFESSORUM BURDIGALENSIUM."):
            node_key = "PL019:section:001:node:commemoratio"
        add_entry(node_key, str(ORDO_FILE_519), part)

    for part in re.split(r"(?=(?:XIII\. Citario|XX\. Staphylius|EPITAPHIA HEROUM\.|EPISTOLÆ\.|GRATIARUM ACTIO AD GRATIANUM\.))", right_519):
        part = normalize_space(part)
        if not part:
            continue
        node_key = "PL019:section:001:node:commemoratio"
        add_entry(node_key, str(ORDO_FILE_519), part)

    return nodes, entries, refs


def apply_page_map(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    page_map: dict[int, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)
    for ref_list in refs_by_entry.values():
        ref_list.sort(key=lambda item: item["ref_order"])

    for ref in refs:
        if ref["ref_kind"] == "parallel_locator":
            continue
        target_file = None
        if isinstance(ref.get("page_ref_int"), int):
            target_file = page_map.get(ref["page_ref_int"])
        elif ref.get("range_start_raw") and ref.get("range_end_raw"):
            start = int(ref["range_start_raw"])
            target_file = page_map.get(start)
        if target_file:
            ref["target_file"] = target_file
            ref["target_file_probability"] = 0.9
            ref["raw_json"]["locator_method"] = "editorial_page_map"
            ref["confidence"] = max(ref.get("confidence") or 0.0, 0.9)

    # The Juvencus concordance entries use internal book/line locators rather than page locators.
    # When no page-based target could be resolved, retain the OCR file that carries the entry itself
    # as a material anchor so the import validator can accept the reference without inventing pages.
    for ref in refs:
        if ref.get("target_file") is not None:
            continue
        entry_key = str(ref.get("entry_key") or "")
        if not entry_key.startswith("PL019:section:004:juvenci_phrases:"):
            continue
        entry_anchor = None
        ref_source = None
        if isinstance(ref.get("raw_json"), dict):
            ref_source = str(ref["raw_json"].get("source_file") or "")
        if ref_source:
            entry_anchor = ref_source
        if not entry_anchor:
            continue
        ref["target_file"] = entry_anchor
        ref["target_file_probability"] = 0.5
        ref.setdefault("raw_json", {})["locator_method"] = "entry_source_fallback"
        ref["confidence"] = max(ref.get("confidence") or 0.0, 0.62)

    for entry in entries:
        if entry["section_key"] == "PL019:section:004:juvenci_phrases":
            if not entry.get("target_file_best"):
                source_file = str((entry.get("raw_json") or {}).get("source_file") or "") or str(
                    entry.get("editorial_anchor_file") or ""
                )
                if source_file:
                    entry["target_file_best"] = source_file
                    entry.setdefault("raw_json", {})["target_file_best_reason"] = "entry_source_fallback"
                    entry["confidence"] = max(entry.get("confidence") or 0.0, 0.74)
            continue
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        first_target = next((ref["target_file"] for ref in entry_refs if ref.get("target_file")), None)
        if first_target:
            entry["target_file_best"] = first_target
            entry["raw_json"]["target_file_best_reason"] = "editorial_page_map"
            entry["confidence"] = max(entry.get("confidence") or 0.0, 0.86)
    entries_by_key = {entry["entry_key"]: entry for entry in entries}
    return refs_by_entry, entries_by_key


def apply_helper(
    helper_output: dict[str, Any],
    entries_by_key: dict[str, dict[str, Any]],
) -> None:
    for item in helper_output.get("entries", []):
        entry = entries_by_key.get(item.get("entry_id"))
        if not entry:
            continue
        helper_payload = {
            "status": item.get("status"),
            "best_candidate": item.get("best_candidate"),
            "candidates": item.get("candidates", [])[:5],
        }
        entry.setdefault("raw_json", {})["helper"] = helper_payload
        best = item.get("best_candidate") or {}
        if best.get("file") and best.get("candidate_role") == "target_candidate":
            entry["target_file_best"] = best["file"]
            entry["raw_json"]["target_file_best_reason"] = f"helper:{item.get('status')}"
            entry["confidence"] = max(entry.get("confidence") or 0.0, 0.9 if item.get("status") == "resolved" else 0.78)


def reorder_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(sections, key=lambda item: ((item.get("page_start") or 10**9), file_seq(item.get("file_start") or OUTPUT_PATH.name)))
    for order, section in enumerate(ordered, start=1):
        section["section_order"] = order
    return ordered


def update_todo() -> None:
    write_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL019 payload rebuilt from assembled fragments with resegmented ordo rerum and concordance fallback anchoring",
            "completed": [
                "consumed validated objects from assembled_fragments.json",
                "resegmented ordo rerum into smaller heading-group entries",
                "rebuilt helper request and ran index_target_locator in chunks",
                "filled editorial-page target files via OCR page-map and helper evidence",
                "anchored unresolved Juvencus concordance locators to their owning OCR files",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Concordance-style parallel locators in the Juvencus phrase index use source-file fallback anchors when no page target resolves.",
                "Section order was normalized to ascending printed-page order for final serialization.",
            ],
        },
    )


def main() -> None:
    assembled = read_json(ASSEMBLED_PATH)["data"]
    page_map = build_page_map()

    sections = [dict(item) for item in assembled["sections"]]
    nodes = [dict(item) for item in assembled["nodes"] if item["section_key"] != ORDO_SECTION_KEY]
    entries = [dict(item) for item in assembled["entries"] if item["section_key"] != ORDO_SECTION_KEY]
    refs = [dict(item) for item in assembled["refs"] if not item["entry_key"].startswith(f"{ORDO_SECTION_KEY}:")]

    ordo_nodes, ordo_entries, ordo_refs = parse_ordo_blocks()
    nodes.extend(ordo_nodes)
    entries.extend(ordo_entries)
    refs.extend(ordo_refs)

    sections = reorder_sections(sections)
    refs_by_entry, entries_by_key = apply_page_map(entries, refs, page_map)

    helper_request = build_helper_request(entries, refs_by_entry)
    write_json(HELPER_REQUEST_PATH, helper_request)
    helper_output = run_helper()
    apply_helper(helper_output, entries_by_key)

    coverage = dict(assembled.get("coverage") or {})
    coverage.update(
        {
            "entries_status": "complete_for_detected_sections",
            "entries_status_reason": "Rebuilt PL019 from validated chunk assembly, resegmented the concluding ordo rerum, and retried editorial-page locators with an OCR page-map plus chunked helper output.",
            "evidence_files": [
                str(ORDO_FILE_518),
                str(ORDO_FILE_519),
                str(SOURCE_ROOT / "1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-486.txt"),
                str(SOURCE_ROOT / "1bcaf53b-99b9-41a1-bcf4-7305e2dc2cc1-503.txt"),
                str(SOURCE_ROOT / "28f9984f-b1b8-4590-a4cb-ec017c316d06-512.txt"),
            ],
        }
    )

    notes = list(assembled.get("notes") or [])
    notes.append(
        {
            "kind": "locator_repair",
            "text": "Editorial-page refs were repopulated from an OCR-derived spread page-map and then refined at entry level with chunked index_target_locator output.",
        }
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_ID,
            "notes": None,
        },
        "sections": sections,
        "nodes": sorted(nodes, key=lambda item: (item["section_key"], item.get("node_order") or 0, item["node_key"])),
        "entries": sorted(entries, key=lambda item: (item["section_key"], item["entry_order"], item["entry_key"])),
        "refs": sorted(refs, key=lambda item: (item["entry_key"], item["ref_order"])),
        "scripture_refs": list(assembled.get("scripture_refs") or []),
        "coverage": coverage,
        "notes": notes,
    }

    write_json(OUTPUT_PATH, payload)
    update_todo()


if __name__ == "__main__":
    main()

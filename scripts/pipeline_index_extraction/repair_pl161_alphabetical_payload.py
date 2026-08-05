#!/usr/bin/env python3
"""Usage: rebuild PL161 alphabetical refs and final payload from the checked OCR/index intermediates."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL161"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
PREVIOUS_PAYLOAD = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
OUTPUT_JSON = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"

ENTRY_KEY_RE = re.compile(r":entry:(\d+)$")
ROMAN_RE = re.compile(r"\b[IVXLCDM]+\b", re.IGNORECASE)
HEADER_NUM_RE = re.compile(r"\b(\d{1,4})\b")
DECRETUM_PART_RE = re.compile(r"DECRETI\s+PARS\s+([A-ZIVXLCDM\.]+)", re.IGNORECASE)
PANORMIA_LIB_RE = re.compile(r"PANORMIA.*?LIB\.?\s*([A-Z0-9IVXLCDM\.]+)", re.IGNORECASE)
PROLOGUS_RE = re.compile(r"PROLOGUS\s+PANORMI", re.IGNORECASE)
CAP_RE = re.compile(r"\bcap+p?\.?\s*([0-9]+(?:\s*(?:,|et)\s*[0-9]+)*)", re.IGNORECASE)
LIB_INLINE_RE = re.compile(r"\blib\.?\s*([ivxlcdm]+)\b", re.IGNORECASE)
IBID_RE = re.compile(r"\bibid(?:em)?\.?\b", re.IGNORECASE)

DECRETUM_PART_ROMAN = {
    "PRIMA": 1,
    "I": 1,
    "SECUNDA": 2,
    "II": 2,
    "TERTIA": 3,
    "III": 3,
    "QUARTA": 4,
    "IV": 4,
    "QUINTA": 5,
    "V": 5,
    "SEXTA": 6,
    "VI": 6,
    "SEPTIMA": 7,
    "VII": 7,
    "OCTAVA": 8,
    "VIII": 8,
    "NONA": 9,
    "IX": 9,
    "DECIMA": 10,
    "X": 10,
    "UNDECIMA": 11,
    "XI": 11,
    "DUODECIMA": 12,
    "XII": 12,
    "DECIMA TERTIA": 13,
    "XIII": 13,
    "DECIMA QUARTA": 14,
    "XIV": 14,
    "DECIMA QUINTA": 15,
    "XV": 15,
    "DECIMA SEXTA": 16,
    "XVI": 16,
    "DECIMA SEPTIMA": 17,
    "XVII": 17,
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def roman_to_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^IVXLCDM]", "", value.upper())
    if not cleaned:
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(cleaned):
        cur = values[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    return total


def normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def parse_entry_num(entry_key: str) -> int:
    match = ENTRY_KEY_RE.search(entry_key)
    if not match:
        raise ValueError(f"bad entry_key: {entry_key}")
    return int(match.group(1))


def extract_header_text(text: str) -> str:
    lines = []
    in_header = False
    for line in text.splitlines():
        if 'tipo="cabecalho"' in line:
            in_header = True
            continue
        if in_header and "</bloco>" in line:
            break
        if in_header:
            stripped = re.sub(r"<[^>]+>", " ", line)
            stripped = normalize_space(stripped)
            if stripped:
                lines.append(stripped)
    return " ".join(lines)


def build_decretum_page_map(files: list[Path]) -> dict[tuple[int, int], str]:
    page_map: dict[tuple[int, int], str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        header = extract_header_text(text)
        part_match = DECRETUM_PART_RE.search(header)
        if not part_match:
            continue
        token = normalize_space(part_match.group(1)).strip(".").upper()
        part = DECRETUM_PART_ROMAN.get(token) or roman_to_int(token)
        if not part:
            continue
        nums = [int(n) for n in HEADER_NUM_RE.findall(header) if 1 <= int(n) <= 1600]
        for num in nums:
            page_map[(part, num)] = str(path)
    return page_map


def build_panormia_context(files: list[Path]) -> tuple[dict[int, str], dict[tuple[str, int], list[str]]]:
    lib_by_seq: dict[int, str] = {}
    cap_map: dict[tuple[str, int], list[str]] = defaultdict(list)
    current_lib: str | None = None
    seq_to_path = {int(path.stem.rsplit("-", 1)[1]): path for path in files}
    for seq in sorted(seq_to_path):
        path = seq_to_path[seq]
        text = path.read_text(encoding="utf-8", errors="ignore")
        header = extract_header_text(text)
        explicit_lib: str | None = None
        if PROLOGUS_RE.search(text):
            explicit_lib = "prologus"
        else:
            match = PANORMIA_LIB_RE.search(header) or PANORMIA_LIB_RE.search(text[:1200])
            if match:
                explicit_lib = match.group(1).strip(".").lower()
        if explicit_lib:
            if explicit_lib.isdigit():
                explicit_lib = str(explicit_lib)
            elif explicit_lib != "prologus":
                num = roman_to_int(explicit_lib)
                explicit_lib = str(num) if num else explicit_lib
            current_lib = explicit_lib
        if current_lib:
            lib_by_seq[seq] = current_lib
            for match in CAP_RE.finditer(text):
                for cap_num in [int(n) for n in re.findall(r"\d+", match.group(1))]:
                    cap_map[(current_lib, cap_num)].append(str(path))
    return lib_by_seq, cap_map


def parse_decretum_refs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    text = entry.get("entry_raw") or ""
    nums = [int(n) for n in re.findall(r"\b\d{1,3}\b", text)]
    refs: list[dict[str, Any]] = []
    current_part: int | None = None
    idx = 0
    seen: set[tuple[int | None, int]] = set()
    while idx < len(nums):
        cur = nums[idx]
        nxt = nums[idx + 1] if idx + 1 < len(nums) else None
        if current_part is None:
            if nxt is None:
                pair = (None, cur)
                idx += 1
            else:
                current_part = cur
                pair = (current_part, nxt)
                idx += 2
        else:
            if cur <= 17 and nxt is not None and nxt > 17:
                current_part = cur
                pair = (current_part, nxt)
                idx += 2
            else:
                pair = (current_part, cur)
                idx += 1
        if pair in seen:
            continue
        seen.add(pair)
        part_ref, page_ref = pair
        ref_raw = f"{part_ref}, {page_ref}" if part_ref is not None else str(page_ref)
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": str(page_ref),
                "page_ref_int": page_ref,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "raw_json": {"part_ref": part_ref} if part_ref is not None else {},
            }
        )
    return refs


def entry_is_roman_heading(entry: dict[str, Any]) -> bool:
    raw = normalize_space(entry.get("entry_raw"))
    return raw.endswith(".") and raw[:-1].upper() in {"I", "II", "III", "IV", "V", "VI", "VII", "VIII"}


def parse_panormia_refs(entry: dict[str, Any], inherited_lib: str | None) -> tuple[list[dict[str, Any]], str | None]:
    text = entry.get("entry_raw") or ""
    refs: list[dict[str, Any]] = []
    current_lib = inherited_lib
    seen: set[tuple[str | None, int]] = set()
    if "prolog" in text.lower():
        current_lib = "prologus"
    inline = LIB_INLINE_RE.search(text)
    if inline:
        maybe = roman_to_int(inline.group(1))
        current_lib = str(maybe) if maybe else current_lib
    for match in CAP_RE.finditer(text):
        chunk = match.group(0)
        nums = [int(n) for n in re.findall(r"\d+", match.group(1))]
        tail = text[match.end() : match.end() + 50]
        local_lib = current_lib
        inline_local = LIB_INLINE_RE.search(chunk + " " + tail)
        if "prolog" in (chunk + " " + tail).lower():
            local_lib = "prologus"
        elif inline_local:
            maybe = roman_to_int(inline_local.group(1))
            local_lib = str(maybe) if maybe else local_lib
        elif IBID_RE.search(chunk + " " + tail):
            local_lib = current_lib
        for cap_num in nums:
            key = (local_lib, cap_num)
            if key in seen:
                continue
            seen.add(key)
            ref_raw = f"cap. {cap_num}" + (f", lib. {local_lib}" if local_lib and local_lib != "prologus" else "")
            if local_lib == "prologus":
                ref_raw = f"cap. {cap_num} Prologi"
            refs.append(
                {
                    "ref_kind": "target_locator",
                    "ref_raw": ref_raw,
                    "page_ref_raw": str(cap_num),
                    "page_ref_int": cap_num,
                    "page_ref_col": None,
                    "line_ref_raw": f"lib. {local_lib}" if local_lib and local_lib != "prologus" else ("Prologi" if local_lib == "prologus" else None),
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "raw_json": {"lib_ref": local_lib} if local_lib else {},
                }
            )
        current_lib = local_lib or current_lib
    return refs, current_lib


def build_helper_request(entries: list[dict[str, Any]], refs_by_entry: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        refs = refs_by_entry.get(entry["entry_key"], [])
        if not refs:
            continue
        if all(ref.get("target_file") for ref in refs):
            continue
        page_hints = [ref["page_ref_int"] for ref in refs if ref.get("page_ref_int") is not None][:6]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw") or (entry.get("entry_raw") or "")[:120],
                "query_names": [entry.get("lemma_raw") or "", normalize_space(entry.get("entry_raw"))[:160]],
                "page_hints": [str(n) for n in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": normalize_space(entry.get("context_raw") or entry.get("entry_raw"))[:320],
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
            "python",
            str(ROOT / "scripts" / "index_target_locator.py"),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        check=True,
        cwd=ROOT,
    )
    return read_json(HELPER_OUTPUT_JSON)


def rebuild_ordo_entries(previous_sections: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section = next(s for s in previous_sections if s["section_kind"] == "ordo_rerum")
    source_file = Path(section["file_start"])
    text = source_file.read_text(encoding="utf-8", errors="ignore")
    lower = text.split("ORDO RERUM", 1)[1]
    lines = [normalize_space(re.sub(r"<[^>]+>", " ", line)) for line in lower.splitlines()]
    lines = [line for line in lines if line and line not in {"QUÆ IN HOC TOMO CONTINENTUR.", "Digitized by Google"}]
    entry_texts = [
        ("heading_group", "D. IVO CARNOTENSIS EPISCOPUS."),
        ("lemma", "Notitia historico-litteraria Dissertatio de Decreto Ivonis aliisque antiquis cano- num collectioibus Gratiano anterioribus. XLIX"),
        ("lemma", "Prolegomena editionis Operum Ivonis anni 1617. 9"),
        ("heading_group", "D. Ivonis Decretum."),
        ("lemma", "Panormia. 165?"),
        ("lemma", "Indices locupletissimi, in Decretum et Panormia 153?"),
        ("editorial_note", "FINIS TOMI CENTESIMI SEXAGESIMI PRIMI."),
        ("editorial_note", "Ex typis L. MIGNE, au Petit-Montrouge."),
    ]
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    start_num = 4338
    for offset, (kind, raw) in enumerate(entry_texts):
        num = start_num + offset
        entry_key = f"{VOLUME_ID}:entry:{num:06d}"
        inferred = None
        ref_match = re.search(r"(XLIX|\d+\??)$", raw)
        if ref_match and kind == "lemma":
            value = ref_match.group(1).rstrip("?")
            inferred = roman_to_int(value) if ROMAN_RE.fullmatch(value) else int(value)
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "entry_order": num,
                "entry_kind": kind,
                "lemma_raw": raw.rstrip(".") if kind in {"heading_group", "lemma"} else None,
                "lemma_display": raw.rstrip(".") if kind in {"heading_group", "lemma"} else None,
                "lemma_norm": raw.lower().rstrip(".") if kind in {"heading_group", "lemma"} else None,
                "lemma_sort": raw.lower().rstrip(".") if kind in {"heading_group", "lemma"} else None,
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": inferred,
                "section_start_file": section["file_start"],
                "editorial_anchor_file": section["file_start"],
                "target_file_best": section["file_start"],
                "confidence": 0.95 if kind == "lemma" else 0.92,
                "raw_json": {
                    "source_file": section["file_start"],
                    "section_kind": "ordo_rerum",
                    "candidate_role": "section_entry",
                },
            }
        )
        if inferred is not None:
            ref_raw = ref_match.group(1)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": inferred,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": section["file_start"],
                    "target_file_probability": 0.9,
                    "section_start_file": section["file_start"],
                    "editorial_anchor_file": section["file_start"],
                    "confidence": 0.85,
                    "raw_json": {"locator_method": "ordo_rerum_page_literal"},
                }
            )
    return entries, refs


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair PL161 alphabetical payload from checked OCR patterns.")
    parser.add_argument("--write-output", action="store_true", help="Write final payload JSON after rebuilding intermediates.")
    args = parser.parse_args()

    payload = read_json(PREVIOUS_PAYLOAD)
    sections = payload["sections"]
    all_entries = payload["entries"]
    section1_key = next(s["section_key"] for s in sections if s["section_kind"] == "analytic_subject" and "DECRETO" in s["heading_raw"])
    section2_key = next(s["section_key"] for s in sections if s["section_kind"] == "analytic_subject" and "PANORMIAM" in s["heading_raw"])

    files = sorted(SOURCE_ROOT.glob("*.txt"))
    decretum_page_map = build_decretum_page_map(files)
    panormia_lib_by_seq, panormia_cap_map = build_panormia_context(files)

    new_entries = [e for e in all_entries if e["section_key"] in {section1_key, section2_key}]
    refs: list[dict[str, Any]] = []
    current_panormia_lib: str | None = None
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for entry in new_entries:
        local_refs: list[dict[str, Any]] = []
        if entry["section_key"] == section1_key and entry["entry_kind"] in {"lemma", "sublemma"}:
            local_refs = parse_decretum_refs(entry)
            for ref in local_refs:
                part_ref = ref["raw_json"].get("part_ref")
                target = decretum_page_map.get((part_ref, ref["page_ref_int"])) if part_ref is not None else None
                if target is None:
                    target = entry.get("target_file_best")
                ref["target_file"] = target
                ref["target_file_probability"] = 0.93 if target and part_ref is not None else (0.72 if target else None)
                ref["confidence"] = 0.88 if part_ref is not None else 0.7
                ref["section_start_file"] = entry["section_start_file"]
                ref["editorial_anchor_file"] = entry["editorial_anchor_file"]
                ref["raw_json"]["locator_method"] = "decretum_part_page_map" if part_ref is not None and target else "entry_target_fallback"
        elif entry["section_key"] == section2_key:
            if entry_is_roman_heading(entry):
                current_panormia_lib = str(roman_to_int(entry["entry_raw"].rstrip(".")))
            elif entry["entry_kind"] in {"lemma", "sublemma"}:
                local_refs, current_panormia_lib = parse_panormia_refs(entry, current_panormia_lib)
                for ref in local_refs:
                    lib_ref = ref["raw_json"].get("lib_ref")
                    cap = ref["page_ref_int"]
                    candidates = panormia_cap_map.get((lib_ref, cap), [])
                    target = candidates[0] if len(candidates) == 1 else (entry.get("target_file_best") if candidates else entry.get("target_file_best"))
                    ref["target_file"] = target
                    ref["target_file_probability"] = 0.96 if len(candidates) == 1 else (0.74 if target else None)
                    ref["confidence"] = 0.9 if len(candidates) == 1 else 0.76
                    ref["section_start_file"] = entry["section_start_file"]
                    ref["editorial_anchor_file"] = entry["editorial_anchor_file"]
                    ref["raw_json"]["locator_method"] = "panormia_cap_map" if candidates else "entry_target_fallback"
                    if len(candidates) > 1:
                        ref["raw_json"]["candidate_files"] = candidates[:5]

        for idx, ref in enumerate(local_refs, start=1):
            ref["entry_key"] = entry["entry_key"]
            ref["ref_order"] = idx
            refs.append(ref)
            refs_by_entry[entry["entry_key"]].append(ref)
        if local_refs:
            entry["target_file_best"] = local_refs[0].get("target_file") or entry.get("target_file_best")

    ordo_entries, ordo_refs = rebuild_ordo_entries(sections)
    new_entries.extend(ordo_entries)
    refs.extend(ordo_refs)
    for ref in ordo_refs:
        refs_by_entry[ref["entry_key"]].append(ref)

    helper_request = build_helper_request(new_entries, refs_by_entry)
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output = run_helper()
    helper_by_id = {item["entry_id"]: item for item in helper_output.get("entries", [])}

    for entry in new_entries:
        helper_info = helper_by_id.get(entry["entry_key"])
        if not helper_info:
            continue
        entry.setdefault("raw_json", {})["helper_status"] = helper_info.get("status")
        best = helper_info.get("best_candidate") or {}
        if best.get("file") and best.get("candidate_role") == "target_candidate":
            if not entry.get("target_file_best") or entry["target_file_best"] == entry.get("editorial_anchor_file"):
                entry["target_file_best"] = best["file"]
        entry["raw_json"]["helper_best_candidate"] = {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        for ref in refs_by_entry.get(entry["entry_key"], []):
            if not ref.get("target_file") and best.get("file"):
                ref["target_file"] = best["file"]
                ref["target_file_probability"] = best.get("probability")
                ref["raw_json"]["locator_method"] = "helper_target_candidate"

    volume = payload["volume"]
    coverage = {
        "entries_status": "rebuilt_refs",
        "entries_status_reason": "Rebuilt PL161 refs from the OCR-backed entry text, repaired broken ref-to-entry associations, and replaced the corrupted ordo rerum spillover with the real closing table from file 764.",
        "evidence_files": [
            str(SOURCE_ROOT / "1c0895cd-5206-42c9-823f-4393ce030be6-740.txt"),
            str(SOURCE_ROOT / "1c0895cd-5206-42c9-823f-4393ce030be6-764.txt"),
            str(SOURCE_ROOT / "ee8da5d2-bfe9-4604-9dbd-0777c914bf50-239.txt"),
            str(SOURCE_ROOT / "d8e47782-fef8-4d74-ac8d-968f62eb617a-637.txt"),
        ],
    }
    notes = [
        "The previous checkpoint had widespread ref-to-entry misalignment and left refs.target_file null.",
        "Decretum refs were rebuilt as part/page locators from patterns like `5, 378` and mapped against body headers.",
        "Panormia refs were rebuilt as cap/lib locators from patterns like `cap. 53, lib. IV` and mapped against body chapter headings.",
        "The closing ordo rerum section was rebuilt from the lower half of OCR file 764; the upper spillover lines from the preceding index page were discarded.",
    ]
    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": utc_now(),
        "updated_at": utc_now(),
        "source_root": str(SOURCE_ROOT),
        "helper_request_json": str(HELPER_REQUEST_JSON),
        "helper_output_json": str(HELPER_OUTPUT_JSON),
        "output_file": str(OUTPUT_JSON),
    }
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": utc_now(),
        "current_focus": "PL161 refs rebuilt and payload reassembled.",
        "completed": [
            "reparsed Decretum refs from entries",
            "reparsed Panormia refs from entries",
            "rebuilt closing ordo rerum entries",
            "ran helper on unresolved ref-bearing entries",
        ],
        "pending": [
            "validate import for the rebuilt payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR file suffix, printed page, and cited locator separate.",
            "Decretum uses part/page-style pairs; Panormia uses cap/lib locators.",
        ],
    }

    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", [])
    write_json(INTERMEDIATE_DIR / "entries.json", new_entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(INTERMEDIATE_DIR / "manifest.json", manifest)
    write_json(INTERMEDIATE_DIR / "todo.json", todo)

    payload_out = {
        "schema_version": 1,
        "generated_at": manifest["updated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": new_entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    if args.write_output:
        write_json(OUTPUT_JSON, payload_out)


if __name__ == "__main__":
    main()

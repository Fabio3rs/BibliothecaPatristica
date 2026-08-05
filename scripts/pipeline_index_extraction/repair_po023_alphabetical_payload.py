#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/repair_po023_alphabetical_payload.py

Repair PO023 alphabetical payload from the existing checkpoint by adding OCR-confirmed
sections omitted in the first pass and normalizing biblical book names to PT-BR.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PO023/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PO023_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PO023_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PO023_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PO023"
MARK = "repair_po023_alphabetical_payload"

F176 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-176.txt"
F179 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-179.txt"
F180 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-180.txt"
F182 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-182.txt"
F183 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-183.txt"
F184 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-184.txt"
F185 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-185.txt"
F186 = SOURCE_ROOT / "6f06fe5b-2e04-403d-a2d8-9092173f0442-186.txt"
F346 = SOURCE_ROOT / "e991feb5-e4b0-4ecb-be30-dc9f0b0b8df1-346.txt"
F351 = SOURCE_ROOT / "e991feb5-e4b0-4ecb-be30-dc9f0b0b8df1-351.txt"
F352 = SOURCE_ROOT / "e991feb5-e4b0-4ecb-be30-dc9f0b0b8df1-352.txt"
F353 = SOURCE_ROOT / "e991feb5-e4b0-4ecb-be30-dc9f0b0b8df1-353.txt"

NEW_SECTION_KEYS = {
    "PO023:alpha:foreign_terms:002",
    "PO023:alpha:foreign_terms:003",
    "PO023:alpha:author_index:005",
    "PO023:alpha:editorial_closure:006",
}

SECTION_ORDER = {
    "PO023:alpha:onomastic_mixed:001": 1,
    "PO023:alpha:foreign_terms:002": 2,
    "PO023:alpha:foreign_terms:003": 3,
    "PO023:alpha:scripture_index:002": 4,
    "PO023:alpha:author_index:005": 5,
    "PO023:alpha:editorial_closure:006": 6,
    "PO023:alpha:onomastic_mixed:003": 7,
    "PO023:alpha:analytic_subject:004": 8,
}

SCRIPTURE_BOOKS = {
    "Genèse": "Gênesis",
    "Psaumes": "Salmos",
    "Isaïe": "Isaías",
    "Matthieu": "São Mateus",
    "Jean": "São João",
    "Actes": "Atos dos Apóstolos",
    "Hébreux": "Hebreus",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(value: str) -> str:
    return value.lower().replace("é", "e").replace("è", "e").replace("ê", "e").replace("î", "i")


def next_entry_number(entries: list[dict[str, Any]]) -> int:
    nums = []
    for entry in entries:
        m = re.search(r":entry:(\d+)$", entry["entry_key"])
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def ref_from_raw(
    entry_key: str,
    order: int,
    raw: str,
    section_file: Path,
    confidence: float,
    kind: str | None = None,
    raw_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_match = re.search(r"\d+", raw)
    page_raw = page_match.group(0) if page_match else None
    suffix = raw[page_match.end() :] if page_match else ""
    line_raw = None
    if page_match:
        sub_match = re.search(r"[₀₁₂₃₄₅₆₇₈₉_]\{?([0-9₀₁₂₃₄₅₆₇₈₉,\-₋ ]+)\}?", raw[page_match.end() :])
        note_match = re.search(r"n\.\s*\d+", suffix)
        if sub_match:
            line_raw = sub_match.group(1).translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉₋", "0123456789-")).strip(" {}")
        elif note_match:
            line_raw = note_match.group(0)
    ref_kind = kind
    if ref_kind is None:
        ref_kind = "editorial_range" if line_raw and "-" in line_raw else "editorial_page_line" if line_raw else "editorial_page"
    return {
        "entry_key": entry_key,
        "ref_order": order,
        "ref_kind": ref_kind,
        "ref_raw": raw,
        "page_ref_raw": page_raw,
        "page_ref_int": int(page_raw) if page_raw else None,
        "page_ref_col": None,
        "line_ref_raw": line_raw,
        "range_start_raw": line_raw.split("-", 1)[0] if line_raw and "-" in line_raw else None,
        "range_end_raw": line_raw.rsplit("-", 1)[-1] if line_raw and "-" in line_raw else None,
        "target_file": str(section_file),
        "target_file_probability": confidence,
        "section_start_file": str(section_file),
        "editorial_anchor_file": str(section_file),
        "confidence": confidence,
        "raw_json": raw_json or {},
    }


def make_entry(
    entry_no: int,
    entry_order: int,
    section_key: str,
    entry_kind: str,
    lemma: str,
    entry_raw: str,
    section_file: Path,
    confidence: float,
    inferred_page: int | None,
    source_note: str,
) -> dict[str, Any]:
    key = f"PO023:entry:{entry_no:04d}"
    return {
        "entry_key": key,
        "section_key": section_key,
        "parent_node_key": None,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma,
        "lemma_display": lemma,
        "lemma_norm": norm(lemma),
        "lemma_sort": norm(lemma),
        "entry_raw": entry_raw,
        "context_raw": None,
        "heading_letter": None,
        "inferred_printed_page": inferred_page,
        "section_start_file": str(section_file),
        "editorial_anchor_file": str(section_file),
        "target_file_best": str(section_file),
        "confidence": confidence,
        "raw_json": {"source": source_note, "repair_run": MARK},
    }


def build_sections(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = [s for s in existing if s["section_key"] not in NEW_SECTION_KEYS]
    by_key = {s["section_key"]: s for s in sections}
    by_key["PO023:alpha:onomastic_mixed:003"]["heading_raw"] = (
        "TABLE ALPHABÉTIQUE DES NOMS PROPRES SYRIAQUES ET DES MOTS ÉTRANGERS OU REMARQUABLES"
    )
    by_key["PO023:alpha:onomastic_mixed:003"]["heading_norm"] = (
        "table alphabetique des noms propres syriaques et des mots etrangers ou remarquables"
    )
    additions = [
        {
            "section_key": "PO023:alpha:foreign_terms:002",
            "volume_id": "PO023",
            "work_key": None,
            "section_order": 2,
            "section_kind": "foreign_terms",
            "heading_raw": "TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES",
            "heading_norm": "table des mots syriaques etrangers ou remarquables",
            "heading_letter": None,
            "page_start": 169,
            "page_end": 172,
            "file_start": str(F179),
            "file_end": str(F182),
            "confidence": 0.98,
            "raw_json": {"source": "OCR files 179-182", "repair_run": MARK},
        },
        {
            "section_key": "PO023:alpha:foreign_terms:003",
            "volume_id": "PO023",
            "work_key": None,
            "section_order": 3,
            "section_kind": "foreign_terms",
            "heading_raw": "TABLE DES MOTS GRECS CITÉS DANS LES MSS.",
            "heading_norm": "table des mots grecs cites dans les mss",
            "heading_letter": None,
            "page_start": 173,
            "page_end": 173,
            "file_start": str(F183),
            "file_end": str(F183),
            "confidence": 0.99,
            "raw_json": {"source": "OCR file 183", "repair_run": MARK},
        },
        {
            "section_key": "PO023:alpha:author_index:005",
            "volume_id": "PO023",
            "work_key": None,
            "section_order": 5,
            "section_kind": "author_index",
            "heading_raw": "TABLE DES CITATIONS DES PÈRES DE L'ÉGLISE",
            "heading_norm": "table des citations des peres de l eglise",
            "heading_letter": None,
            "page_start": 176,
            "page_end": 176,
            "file_start": str(F186),
            "file_end": str(F186),
            "confidence": 0.99,
            "raw_json": {"source": "OCR file 186", "repair_run": MARK},
        },
        {
            "section_key": "PO023:alpha:editorial_closure:006",
            "volume_id": "PO023",
            "work_key": None,
            "section_order": 6,
            "section_kind": "editorial_closure",
            "heading_raw": "TABLE DES MATIÈRES / TABLES",
            "heading_norm": "table des matieres tables",
            "heading_letter": None,
            "page_start": 176,
            "page_end": 176,
            "file_start": str(F186),
            "file_end": str(F186),
            "confidence": 0.98,
            "raw_json": {"source": "OCR file 186", "repair_run": MARK},
        },
    ]
    sections.extend(additions)
    for section in sections:
        section["section_order"] = SECTION_ORDER[section["section_key"]]
    return sorted(sections, key=lambda item: item["section_order"])


def append_entries(payload: dict[str, Any]) -> None:
    removed_keys = {
        e["entry_key"]
        for e in payload["entries"]
        if e.get("raw_json", {}).get("repair_run") == MARK
    }
    payload["entries"] = [e for e in payload["entries"] if e["entry_key"] not in removed_keys]
    payload["refs"] = [r for r in payload["refs"] if r["entry_key"] not in removed_keys]
    entry_no = next_entry_number(payload["entries"])
    entry_order = max(e["entry_order"] for e in payload["entries"]) + 1

    rows = [
        ("PO023:alpha:foreign_terms:002", "lemma", "ܐܝܙ", "ܐܝܙ 329₁₄ 330₂ 332₁₂ 333₈", F179, ["329₁₄", "330₂", "332₁₂", "333₈"], 0.84),
        ("PO023:alpha:foreign_terms:002", "lemma", "ܐܘܢܝܡܛ", "ܐܘܢܝܡܛ, ܐܘܢܝܡܛ 292₃ 298₅ 308₈ 312₉ 313₉ 325₄ 338₃ 339₂ 349₁₃ 363₂ 368₂ 369₅ 381₅ 390₇ 407₃₋₄ 426₁₄", F179, ["292₃", "298₅", "407₃₋₄", "426₁₄"], 0.78),
        ("PO023:alpha:foreign_terms:002", "lemma", "ܒܡ", "ܒܡ, ܒܡ 277₈ 284₅ 285₄ 304₅ 320₂ 324₃ 336₁₂ 338₂ 342₂ 350₁₃ 373₁₀ 379₁₂ 387₈ 406₁₃", F179, ["277₈", "284₅", "350₁₃", "406₁₃"], 0.78),
        ("PO023:alpha:foreign_terms:002", "lemma", "ܡܫܐܪܐ", "— ܡܫܐܪܐ 300, 303,11, 416,11, 417,6, 418,9-10, 419,1-2-5 422,10 423,1", F180, ["300", "303,11", "418,9-10", "423,1"], 0.75),
        ("PO023:alpha:foreign_terms:002", "lemma", "ܗܘܢܐ ܕܐܚܢܐ", "ܗܘܢܐ ܕܐܚܢܐ , ܢܝ ܐܚܪܝ 350_9-10 393_4-5 399_3 403_10-12 414_9", F182, ["350_9-10", "393_4-5", "403_10-12", "414_9"], 0.8),
        ("PO023:alpha:foreign_terms:003", "lemma", "ἀλειψόμενοι", "ἀλειψόμενοι 305n.2", F183, ["305n.2"], 0.95),
        ("PO023:alpha:foreign_terms:003", "lemma", "Βασίλειος", "Βασίλειος 275n.2 276n.1 277n.2", F183, ["275n.2", "276n.1", "277n.2"], 0.95),
        ("PO023:alpha:foreign_terms:003", "lemma", "Γρηγόριος", "Γρηγόριος 275n.4 278n.2", F183, ["275n.4", "278n.2"], 0.95),
        ("PO023:alpha:foreign_terms:003", "lemma", "θεολογία", "θεολογία 396₈", F183, ["396₈"], 0.96),
        ("PO023:alpha:foreign_terms:003", "lemma", "λαβύρινθος", "λαβύρινθος 291₁₁.n.1", F183, ["291₁₁.n.1"], 0.95),
        ("PO023:alpha:foreign_terms:003", "lemma", "χαλκῆον", "χαλκῆον (sic) 311₁₀.n.3", F183, ["311₁₀.n.3"], 0.95),
        ("PO023:alpha:author_index:005", "lemma", "Saint Grégoire de Nazianze", "Saint Grégoire de Nazianze. . . . P. G., t. XXXVII, col. 177. . . . . . . . . . 419", F186, ["419"], 0.98),
        ("PO023:alpha:author_index:005", "lemma", "Saint Grégoire le Thaumaturge", "Saint Grégoire le Thaumaturge¹. . P. G., t. X, col. 985. . . . . . . . . . . . 411", F186, ["411"], 0.98),
        ("PO023:alpha:author_index:005", "lemma", "Saint Ignace d'Antioche", "Saint Ignace d'Antioche. . . . . . P. G., t. V, col. 679-680 . . . . . . . . . . 291", F186, ["291"], 0.98),
        ("PO023:alpha:editorial_closure:006", "heading_group", "Homélie LXXXIV.", "Homélie LXXXIV. — Sur Basile le Grand et Grégoire le Théologien. . . . . 275", F186, ["275"], 0.97),
        ("PO023:alpha:editorial_closure:006", "heading_group", "Homélie LXXXIX.", "Homélie LXXXIX. — Sur Luc, x, 30-36 . . . . . . . . . . . . . . . . . . . . 368", F186, ["368"], 0.97),
        ("PO023:alpha:editorial_closure:006", "heading_group", "IV. — Table des citations de la Bible", "IV. — Table des citations de la Bible . . . . . . . . . . . . . . . . . . . . . . . 442", F186, ["442"], 0.96),
        ("PO023:alpha:editorial_closure:006", "heading_group", "V. — Table des citations des Pères de l'Église", "V. — Table des citations des Pères de l'Église . . . . . . . . . . . . . . . . 444", F186, ["444"], 0.96),
    ]

    for section_key, kind, lemma, raw, file_path, raw_refs, confidence in rows:
        first_page = int(re.search(r"\d+", raw_refs[0]).group(0))
        entry = make_entry(
            entry_no,
            entry_order,
            section_key,
            kind,
            lemma,
            raw,
            file_path,
            confidence,
            first_page,
            f"OCR file {file_path.name.rsplit('-', 1)[-1].removesuffix('.txt')}",
        )
        payload["entries"].append(entry)
        for ref_order, ref_raw in enumerate(raw_refs, start=1):
            raw_json = {}
            if section_key == "PO023:alpha:author_index:005":
                raw_json = {"parallel_locator_preserved_in_entry_raw": True}
            payload["refs"].append(ref_from_raw(entry["entry_key"], ref_order, ref_raw, file_path, confidence, raw_json=raw_json))
        entry_no += 1
        entry_order += 1


def normalize_scripture_refs(payload: dict[str, Any]) -> None:
    for ref in payload["scripture_refs"]:
        book_raw = ref.get("book_raw")
        book_norm = SCRIPTURE_BOOKS.get(book_raw, SCRIPTURE_BOOKS.get(ref.get("book_norm"), ref.get("book_norm")))
        ref["book_norm"] = book_norm
        if book_norm and ref.get("chapter_start") and ref.get("verse_start"):
            start = f"{book_norm} {ref['chapter_start']},{ref['verse_start']}"
            if ref.get("is_range") and ref.get("chapter_end") and ref.get("verse_end"):
                end = f"{ref['chapter_end']},{ref['verse_end']}"
                if ref["chapter_end"] == ref["chapter_start"]:
                    end = str(ref["verse_end"])
                ref["ref_norm"] = f"{start}-{end}"
            else:
                ref["ref_norm"] = start


def build_helper_request(payload: dict[str, Any]) -> dict[str, Any]:
    helper_entries = []
    for entry in payload["entries"]:
        refs = [r for r in payload["refs"] if r["entry_key"] == entry["entry_key"] and r.get("page_ref_int")]
        if not refs:
            continue
        hints = []
        for ref in refs[:6]:
            value = ref["page_ref_int"]
            if value not in hints:
                hints.append(value)
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [x for x in [entry.get("lemma_raw"), entry.get("lemma_norm")] if x],
                "page_hints": [str(x) for x in hints],
                "page_hint_ints": hints,
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": "PO023",
        "source_root": str(SOURCE_ROOT),
        "options": {"max_candidates": 5, "repair_run": MARK},
        "entries": helper_entries[:60],
    }


def apply_helper_output(payload: dict[str, Any]) -> None:
    if not HELPER_OUTPUT.exists():
        return
    helper = read_json(HELPER_OUTPUT)
    by_id = {item.get("entry_id"): item for item in helper.get("entries", []) if isinstance(item, dict)}
    entries_by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    for entry_key, item in by_id.items():
        entry = entries_by_key.get(entry_key)
        if not entry:
            continue
        best = item.get("best_candidate") or {}
        candidates = item.get("candidates") or []
        helper_summary = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "probability": best.get("probability"),
            },
            "top_candidates": [
                {
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "evidence_kinds": [e.get("kind") for e in candidate.get("evidence", [])[:4]],
                }
                for candidate in candidates[:3]
            ],
        }
        entry.setdefault("raw_json", {})["helper"] = helper_summary
        probability = best.get("probability")
        if best.get("file") and isinstance(probability, (int, float)) and probability >= 0.55:
            entry["target_file_best"] = best["file"]
            entry["confidence"] = max(min(float(probability), 0.99), min(entry.get("confidence") or 0.0, 0.99))
            for ref in payload["refs"]:
                if ref["entry_key"] == entry_key:
                    ref["target_file"] = best["file"]
                    ref["target_file_probability"] = float(probability)
                    ref["confidence"] = max(min(float(probability), 0.99), min(ref.get("confidence") or 0.0, 0.99))
                    ref.setdefault("raw_json", {})["helper_entry_id"] = entry_key


def main() -> None:
    payload = read_json(PAYLOAD_PATH)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["generated_at"] = now
    payload["sections"] = build_sections(payload["sections"])
    append_entries(payload)
    normalize_scripture_refs(payload)
    apply_helper_output(payload)
    payload["coverage"] = {
        "entries_status": "partial_recovered",
        "entries_status_reason": (
            "OCR-confirmed sections and representative line items were recovered for all detected end-matter "
            "index blocks. The dense Syriac proper-name and foreign-term tables remain selectively segmented "
            "where OCR corruption makes exhaustive line recovery unsafe in this payload."
        ),
        "evidence_files": [str(p) for p in [F176, F179, F182, F183, F184, F185, F186, F346, F351, F352, F353]],
    }
    notes = [n for n in payload.get("notes", []) if "four distinct" not in n]
    notes.extend(
        [
            "Added OCR-confirmed foreign-terms, Greek-terms, patristic-citation, and editorial-closure sections omitted by the checkpoint.",
            "Biblical scripture_refs now carry PT-BR book_norm and ref_norm values while preserving French book_raw/ref_raw.",
            "The Syriac tables are dense and noisy; added line items are conservative representatives rather than synthetic page dumps.",
        ]
    )
    payload["notes"] = notes

    write_json(PAYLOAD_PATH, payload)
    write_json(HELPER_REQUEST, build_helper_request(payload))
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    for key in ("sections", "nodes", "entries", "refs", "scripture_refs"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": "PO023",
            "updated_at": now,
            "current_focus": "Payload repaired and ready for validation/import.",
            "completed": [
                "verified candidate section windows with read_ocr_page_text",
                "added missing sections from OCR files 179-183 and 186",
                "normalized scripture_refs to PT-BR book_norm/ref_norm",
                "rebuilt helper request for current payload entries",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Foreign Syriac tables remain selectively represented because OCR is dense and noisy.",
                "Helper output should be used conservatively for material anchors only.",
            ],
        },
    )


if __name__ == "__main__":
    main()

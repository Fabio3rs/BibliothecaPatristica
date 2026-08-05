#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/fix_po019_alphabetical_payload.py
# Repairs PO019 alphabetical payload line-break artifacts, a few OCR-confirmed entry issues,
# regenerates helper input for unresolved locators, and writes the updated payload.

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PO019_alphabetical_indices.json"
HELPER_REQUEST_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PO019_helper_request.json"
HELPER_OUTPUT_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PO019_helper_output.json"
SOURCE_ROOT = PROJECT_ROOT / "teste/PO019/text"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PO019/todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: str | None) -> str | None:
    if value is None:
        return None
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def update_entry(entry: dict, *, lemma: str | None = None, entry_raw: str | None = None, context_raw: str | None = None) -> None:
    if lemma is not None:
        entry["lemma_raw"] = lemma
        entry["lemma_display"] = lemma
    if entry_raw is not None:
        entry["entry_raw"] = entry_raw
    entry["context_raw"] = context_raw


def build_refs_by_entry(refs: list[dict]) -> dict[str, list[dict]]:
    refs_by_entry: dict[str, list[dict]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)
    for items in refs_by_entry.values():
        items.sort(key=lambda item: item["ref_order"])
    return refs_by_entry


def apply_global_hyphen_repairs(payload: dict) -> None:
    for entry in payload["entries"]:
        for field in ("lemma_raw", "lemma_display", "entry_raw", "context_raw"):
            if field in entry:
                entry[field] = merge_linebreak_hyphens(entry.get(field))
    for ref in payload["refs"]:
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            if field in ref:
                ref[field] = merge_linebreak_hyphens(ref.get(field))


def apply_manual_repairs(payload: dict) -> None:
    entries_by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    refs = build_refs_by_entry(payload["refs"])

    manual_entries = {
        "po019_s2_0138": {
            "lemma": "Al-Sharīf al-Murtaḍā Abū al-Qāsim 'Alī filius Ṭāhir",
            "entry_raw": "Al-Sharīf al-Murtaḍā Abū al-Qāsim 'Alī filius Ṭāhir. . . . . . . . . . . 375",
        },
        "po019_s2_0077": {
            "entry_raw": "Ka'b al-Aḥbār, 350, 383, 405, 406, 407, 425, 430.",
        },
        "po019_s2_0124": {
            "entry_raw": "Rosweydus, 431; II, 540, 544, 553, 565, 570, 573, 574, 581",
        },
        "po019_s3_0004": {
            "entry_raw": "Yalyâ filius Mo'âd . . . . . . . . . 413",
        },
        "po019_s3_0005": {
            "entry_raw": "Wallis Budge. . . . . . . . . . . 431",
        },
        "po019_s3_0020": {
            "entry_raw": "Angeli in coelo Dei thronum circumdantes, 127, 214.",
        },
        "po019_s3_0026": {
            "entry_raw": "Apostoli ad omnes gentes docendas missi, 211, 214.",
        },
        "po019_s3_0039": {
            "entry_raw": "Cerealia qui corrumpit gravissime peccat, 223.",
        },
        "po019_s3_0078": {
            "entry_raw": "Dei recordatio divitiarum cura impeditur, 50, 86, 232.",
        },
        "po019_s3_0095": {
            "entry_raw": "Diaboli tentatio cupiditatibus exercetur, 174, 229.",
        },
        "po019_s3_0119": {
            "entry_raw": "Fidei lumen divitiarum fulgore aufertur, 74.",
        },
        "po019_s3_0122": {
            "entry_raw": "Fidei virtute super aquam ambulari potest, 160.",
        },
        "po019_s3_0133": {
            "entry_raw": "Hypocrisis, 5, 6, 7, 8, 9, 52, 53, 55, 61, 83, 87, 94, 108, 110, 144, 158, 161, 165, 197, 216, 225.",
        },
        "po019_s3_0162": {
            "entry_raw": "Jesus a parietis umbra qua recreabatur expulsus, 79.",
        },
        "po019_s3_0165": {
            "entry_raw": "Jesus ad pluviam petendam precaturus exit, 10, 201.",
        },
        "po019_s3_0177": {
            "entry_raw": "Jesus denarium et drachma ut stercus despicit, 49, 126, 220.",
        },
        "po019_s3_0229": {
            "entry_raw": "Jesus patrem duarum mulierum resuscitat, 226.",
        },
        "po019_s3_0233": {
            "lemma": "Jesus per villam transit cujus incolae ira efferbentes eum lapidibus obruere conantur",
            "entry_raw": "Jesus per villam transit cujus incolae ira efferbentes eum lapidibus obruere conantur, 38.",
        },
        "po019_s3_0244": {
            "entry_raw": "Jesus sapientes hypocritas arguit, 2, 5, 6, 7, 8, 9, 53, 144, 155, 156, 216, 225.",
        },
        "po019_s3_0246": {
            "entry_raw": "Jesus semetipsum bis natum esse asserit, 187, 203.",
        },
        "po019_s3_0266": {
            "lemma": "Joannes baptista a Jesu monitus ut filiis Israël praedicet",
            "entry_raw": "Joannes baptista a Jesu monitus ut filiis Israël praedicet, 143bis.",
        },
        "po019_s3_0275": {
            "entry_raw": "Joannes baptista juxta mulierem transiens eam impulit quasi parietem offensus, 95, 171.",
        },
        "po019_s3_0319": {
            "entry_raw": "Mors res omnes amatas ab homine separat, 102, 218.",
        },
        "po019_s3_0322": {
            "entry_raw": "Mortis memoria in mente semper habenda, 99, 115, 116, 145, 194.",
        },
        "po019_s3_0325": {
            "entry_raw": "Mundi amatores a via veritatis declinant, 147.",
        },
        "po019_s3_0348": {
            "entry_raw": "Mundus despiciendus intuitu futurae vitae, 39, 70, 42, 53, 54, 63, 115, 124, 126, 145, 168, 180, 220, 221.",
        },
        "po019_s3_0382": {
            "entry_raw": "Parabola cameli laxati qui ad patriam fugit, 216.",
        },
        "po019_s3_0524": {
            "entry_raw": "Vita activa perfectior est contemplativae, 109.",
        },
        "po019_s3_0528": {
            "entry_raw": "Vitia. Vide : Arrogantia. — Avaritia. — Calumnia. — Coecitas. — Contumelia. — Cupiditas. — Deceptio. — Desperatio. — Detractio. — Devotio. — Disputatio. — Dolus. — Ebrietas. — Facetia. — Fullatia. — Fiducia. — Furtum. — Hypocrisis. — Insipientia. — Invidia. — Ira. — Jactantia. — Jocatio. — Juramentum. — Linguae peccata. — Luxus. — Maledicentia. — Mendacium. — Mundi amor. — Odium. — Pigritia. — Restrictio mentalis. — Risus. — Saturitas ventris. — Scandalum. — Scortatio. — Simulatio. — Sodomia. — Sollicitudo. — Stultitia. — Superbia. — Suspitio. — Tristitia spiritualis. — Vanitas. — Vehementia.",
        },
    }

    for entry_key, fix in manual_entries.items():
        entry = entries_by_key[entry_key]
        update_entry(
            entry,
            lemma=fix.get("lemma"),
            entry_raw=fix["entry_raw"],
            context_raw=None,
        )
        entry.setdefault("raw_json", {})["repair_note"] = "PO019 OCR-confirmed entry repair."

    if not refs.get("po019_s2_0138"):
        entry = entries_by_key["po019_s2_0138"]
        payload["refs"].append(
            {
                "entry_key": "po019_s2_0138",
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "375",
                "page_ref_raw": "375",
                "page_ref_int": 375,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": entry.get("section_start_file"),
                "editorial_anchor_file": entry.get("editorial_anchor_file"),
                "confidence": 0.75,
                "raw_json": {
                    "repair_note": "Added missing locator from ONOMASTICON OCR line in file 630."
                },
            }
        )


def build_helper_request(payload: dict) -> dict:
    refs_by_entry = build_refs_by_entry(payload["refs"])
    entries = []
    for entry in payload["entries"]:
        if entry.get("target_file_best"):
            continue
        entry_key = entry["entry_key"]
        entry_refs = refs_by_entry.get(entry_key, [])
        if not entry_refs:
            continue
        if entry.get("entry_kind") == "cross_reference":
            continue
        page_hints = []
        page_hint_ints = []
        for ref in entry_refs:
            raw = ref.get("page_ref_raw") or ref.get("ref_raw")
            if raw and raw not in page_hints:
                page_hints.append(raw)
            page_int = ref.get("page_ref_int")
            if isinstance(page_int, int) and page_int not in page_hint_ints:
                page_hint_ints.append(page_int)
        if not page_hints and not page_hint_ints:
            continue
        entries.append(
            {
                "entry_id": entry_key,
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [value for value in [entry.get("lemma_raw"), entry.get("entry_raw")] if value],
                "page_hints": page_hints[:12],
                "page_hint_ints": page_hint_ints[:12],
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": "PO019",
        "source_root": str(SOURCE_ROOT),
        "options": {"max_candidates": 5},
        "entries": entries,
    }


def run_helper() -> dict:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    return load_json(HELPER_OUTPUT_PATH)


def apply_helper_results(payload: dict, helper_output: dict) -> None:
    entries_by_key = {entry["entry_key"]: entry for entry in payload["entries"]}
    refs_by_entry = build_refs_by_entry(payload["refs"])
    for item in helper_output.get("entries", []):
        entry_key = item.get("entry_id")
        entry = entries_by_key.get(entry_key)
        if not entry:
            continue
        best = item.get("best_candidate") or {}
        helper_summary = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": {
                "file": best.get("file"),
                "file_seq": best.get("file_seq"),
                "score": best.get("score"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
            },
            "top_candidates": [
                {
                    "file": candidate.get("file"),
                    "probability": candidate.get("probability"),
                    "evidence_kinds": candidate.get("evidence_kinds"),
                }
                for candidate in item.get("top_candidates", [])[:3]
            ],
        }
        entry.setdefault("raw_json", {})["helper_requested"] = True
        entry["raw_json"]["helper"] = helper_summary
        best_file = best.get("file")
        best_probability = best.get("probability")
        if best_file and not entry.get("target_file_best"):
            entry["target_file_best"] = best_file
        entry_refs = refs_by_entry.get(entry_key, [])
        if best_file and len(entry_refs) == 1 and not entry_refs[0].get("target_file"):
            entry_refs[0]["target_file"] = best_file
            entry_refs[0]["target_file_probability"] = best_probability
            entry_refs[0].setdefault("raw_json", {})["helper"] = helper_summary


def update_todo() -> None:
    todo = load_json(TODO_PATH)
    todo["updated_at"] = now_iso()
    todo["current_focus"] = "PO019 payload repaired and helper rerun; validating final file"
    todo["completed"] = [
        "Read skill instructions and repository docs",
        "Confirmed true index sections in OCR files 626, 629, 631",
        "Validated current failure mode",
        "Repaired OCR line-break hyphens and selected OCR truncations",
        "Regenerated helper request for unresolved target locators",
    ]
    todo["pending"] = [
        "Validate final payload with import_alphabetical_index_json.py",
    ]
    todo["blocked"] = []
    todo["notes"] = [
        "Filtered tail 737-768 was not the true index block; actual sections start at files 626, 629, and 631.",
        "Single-ref unresolved entries inherit helper target_file when a best candidate exists; multi-ref cases keep per-ref ambiguity unless separately confirmed.",
    ]
    dump_json(TODO_PATH, todo)


def refresh_metadata(payload: dict) -> None:
    payload["generated_at"] = now_iso()
    notes = payload.setdefault("notes", [])
    repair_note = "PO019 rerun repaired OCR line-break hyphen artifacts, fixed selected OCR-truncated entries against files 630-640, and reran helper resolution for unresolved targets."
    if repair_note not in notes:
        notes.append(repair_note)


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    apply_global_hyphen_repairs(payload)
    apply_manual_repairs(payload)
    payload["refs"].sort(key=lambda item: (item["entry_key"], item["ref_order"]))
    helper_request = build_helper_request(payload)
    dump_json(HELPER_REQUEST_PATH, helper_request)
    helper_output = run_helper()
    apply_helper_results(payload, helper_output)
    refresh_metadata(payload)
    dump_json(PAYLOAD_PATH, payload)
    update_todo()


if __name__ == "__main__":
    main()

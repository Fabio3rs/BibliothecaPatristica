#!/usr/bin/env python3
"""Rebuild PG029 from validated fragments and OCR-reviewed anchors."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_PATH = ROOT / "data/index_payloads/PG029_indices.json"
FRAGMENTS_PATH = ROOT / "data/index_intermediate_payloads/PG029/assembled_fragments.json"
TEXT_ROOT = ROOT / "teste/PG029/text"


def page_file(suffix: int) -> str:
    matches = list(TEXT_ROOT.glob(f"*-{suffix:03d}.txt"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one PG029 OCR file for suffix {suffix:03d}, found {matches}")
    return str(matches[0])


def resolve_anchor(work: dict, start_suffix: int, evidence: list[str]) -> None:
    raw_json = work.setdefault("raw_json", {})
    raw_json.pop("work_anchor_rerun", None)
    raw_json["anchor_resolution"] = {
        "status": "resolved",
        "start_file": page_file(start_suffix),
        "evidence": evidence,
        "reviewed_files": [page_file(value) for value in (
            9, 296, 297, 298, 411, 412, 413, 514, 515, 516, 658, 659, 660, 807
        )],
        "numbering_note": "Physical file suffixes are locators only; no editorial page was inferred from them.",
    }
    work["start_file"] = page_file(start_suffix)
    work["start_page"] = None
    work["confidence"] = "high"


def main() -> None:
    previous = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assembled = json.loads(FRAGMENTS_PATH.read_text(encoding="utf-8"))
    stable = assembled["data"]

    payload = {
        "volume": previous["volume"],
        "works": stable["works"],
        "sections": stable["sections"],
        "notes": stable["notes"],
    }
    payload["volume"]["notes"] = [
        "Front matter index begins in OCR file 008 with 'ELENCHUS AUCTORUM ET OPERUM QUÆ IN HOC TOMO XXIX CONTINENTUR.'",
        "Closing ORDO RERUM occupies OCR files 807–810 and ends with 'FINIS TOMI VICESIMI NONI.' in 810.",
        "OCR physical-file suffixes and printed/editorial numbers are preserved as separate concepts.",
    ]
    payload["volume"]["scan_summary"] = {
        "front_index_files": [page_file(8)],
        "end_index_files": [page_file(value) for value in (807, 808, 809, 810)],
    }

    works = {work["work_key"]: work for work in payload["works"]}
    resolve_anchor(
        works["PG029:work:vita_s_basilii_magni"],
        9,
        [
            "OCR 009 has the direct title blocks 'VITA S. BASILII MAGNI' and 'ARCHIEPISCOPI CÆSARIENSIS*.'",
            "The same leaf starts CAPUT PRIMUM and its chapter outline, confirming that it is the work opening rather than an inventory occurrence.",
        ],
    )
    resolve_anchor(
        works["PG029:work:basilius_caesariensis_homiliae_in_hexaemeron"],
        411,
        [
            "OCR 411 has the direct work-front heading 'MONITUM AD HOMILIAS IN HEXAEMERON'.",
            "OCR 412 immediately follows with the author/title block 'S. P. N. BASILII ... HOMILIÆ IX IN HEXAEMERON' and begins HOMILIA I.",
            "OCR 413 continues the first homily under the running header 'HOMILIA I IN HEXAEMERON'.",
        ],
    )
    resolve_anchor(
        works["PG029:work:homiliae_in_hexaemeron"],
        411,
        [
            "OCR 411 has the direct work-front heading 'MONITUM AD HOMILIAS IN HEXAEMERON'.",
            "OCR 412 provides the explicit Latin title and starts HOMILIA I; OCR 413 continues it.",
        ],
    )
    resolve_anchor(
        works["PG029:work:basilius_caesariensis_in_psalmos"],
        514,
        [
            "OCR 514 opens 'MONITUM' and states 'Basilii in Psalmos orationes post Opus sex dierum edendas esse iudicavimus'.",
            "OCR 515 follows with 'HOMILIA IN PSALMUM PRIMUM' and the running header 'HOMILIA IN PSALMUM I'.",
        ],
    )
    resolve_anchor(
        works["PG029:work:basilius_caesariensis_contra_eunomium_libri_quinque"],
        658,
        [
            "OCR 658 begins a MONITUM explicitly about 'Basilii in Eunomium libros'.",
            "OCR 659 follows with the Greek/Latin title blocks and 'LIBER PRIMUS'; OCR 660–661 continue Liber I.",
        ],
    )
    resolve_anchor(
        works["PG029:work:libri_adversus_eunomium"],
        658,
        [
            "OCR 658 is the work-front MONITUM for Basilius' books against Eunomius.",
            "OCR 659 is the ensuing title leaf, 'LIBRI QUIBUS IMPII EUNOMII APOLOGETICUS EVERTITUR', with 'LIBER PRIMUS'.",
            "OCR 807 contains the direct closing notice for the confutations before the ORDO RERUM begins on the same physical file.",
        ],
    )
    resolve_anchor(
        works["PG029:work:vita_s_basilii_apocrypha"],
        296,
        [
            "OCR 296 has the direct heading 'DE VITA S. BASILII APOCRYPHA, Et sancto Amphilochio episcopo Iconiensi perperam imputata.'",
            "OCR 298 continues with 'VITA APOCRYPHA' and its prologue; OCR 299 begins CAPUT I.",
        ],
    )

    # The source page ends mid-word at a column/capture boundary.  No following OCR
    # text proves a continuation, so retain the available literal without a terminal
    # line-break hyphen and preserve the unmodified source fragment as evidence.
    target_key = "PG029:section:volume_front:ordo_rerum:002:PG029:chunk:002:001:033"
    for section in payload["sections"]:
        for entry in section["entries"]:
            if entry.get("entry_key") == target_key:
                raw_json = entry.setdefault("raw_json", {})
                raw_json["truncated_ocr_literal_parts"] = [
                    "V. Concilium Amphilochii de re",
                    "-",
                ]
                raw_json["hyphen_recovery"] = (
                    "OCR 808 was reread in XML. The source ends at 're-' and no following "
                    "line or page proves the continuation; the terminal fragment was omitted "
                    "from entry_raw to avoid asserting an unverified join."
                )
                entry["entry_raw"] = entry["entry_raw"].removesuffix("re-").rstrip()
                entry["note_raw"] = "truncated OCR entry; terminal partial word omitted after XML verification"
                break
        else:
            continue
        break
    else:
        raise RuntimeError("CAP. XXXII entry not found in validated fragments")

    payload["notes"].append(
        "PG029 rerun consumed both validated fragments, resolved all six reviewed work starts from direct OCR evidence, and repaired the CAP. XXXII terminal line-break artifact after rereading OCR 808 XML."
    )
    PAYLOAD_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

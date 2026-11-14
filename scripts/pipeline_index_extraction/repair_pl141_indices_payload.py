#!/usr/bin/env python3
"""Rebuild the PL141 general-index payload from its validated fragments."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME = "PL141"
TEXT = ROOT / "teste" / VOLUME / "text"
ASSEMBLED = ROOT / "data" / "index_intermediate_payloads" / VOLUME / "assembled_fragments.json"
OUTPUT = ROOT / "data" / "index_payloads" / f"{VOLUME}_indices.json"


def physical(name: str) -> str:
    path = TEXT / name
    if not path.is_file():
        raise ValueError(f"Missing PL141 OCR file: {path}")
    return str(path)


def resolved_anchor(work: dict, *, start_file: str, end_file: str, evidence: list[str],
                    start_page: int | None = None, end_page: int | None = None) -> None:
    raw = work.setdefault("raw_json", {})
    prior = raw.pop("work_anchor_rerun", None)
    if prior is None and "prior_work_anchor_rerun" not in raw:
        raise ValueError(f"Expected pending anchor review for {work['work_key']}")
    if prior is not None:
        raw["prior_work_anchor_rerun"] = prior
    raw["anchor_review_resolution"] = {
        "status": "resolved",
        "method": "direct local OCR opening/boundary evidence",
        "evidence": evidence,
    }
    work["start_file"] = physical(start_file)
    work["end_file"] = physical(end_file)
    if start_page is not None:
        work["start_page"] = start_page
    if end_page is not None:
        work["end_page"] = end_page
    work["confidence"] = "high"


def main() -> None:
    existing = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assembled_document = json.loads(ASSEMBLED.read_text(encoding="utf-8"))
    assembled = assembled_document["data"]

    if assembled_document["status"] != "complete" or assembled_document["counts"]["entries"] != 497:
        raise ValueError("PL141 assembled fragments are not the expected complete 497-entry checkpoint")
    if assembled["works"]:
        raise ValueError("Unexpected stable work records require an explicit merge")

    works = copy.deepcopy(existing["works"])
    by_key = {work["work_key"]: work for work in works}
    front_key = "PL141:section:volume_front:elenchus:001"
    for work in works:
        work["source_section_key"] = front_key

    resolved_anchor(
        by_key["gauslinus_bituricensis_episcopus"],
        start_file="14b95e22-4d79-49e7-a1ef-7983cca9db62-384.txt",
        end_file="14b95e22-4d79-49e7-a1ef-7983cca9db62-388.txt",
        end_page=767,
        evidence=[
            "File 384 assembles the title blocks GAUSLINUS / BITURICENSIS ECCLESIÆ ARCHIEPISCOPUS / NOTITIA HISTORICA; it is the local work opening, not an index occurrence.",
            "Files 385–387 continue the Gauslinus notice and epistles; file 387 explicitly prints GAUSLINI EPISTOLÆ.",
            "File 388 directly prints GAUSLINI SERMO DE S. MARTIALI before the separate ADALBERO opening on the same scan. Its 767/768 header resolves the Gauslinus ending to editorial page 767 without using a filename suffix.",
        ],
    )
    resolved_anchor(
        by_key["guillelmus_v_dux_aquitaniae"],
        start_file="14b95e22-4d79-49e7-a1ef-7983cca9db62-418.txt",
        end_file="14b95e22-4d79-49e7-a1ef-7983cca9db62-420.txt",
        evidence=[
            "File 418 has the direct heading GUILLELMI DUCIS EPISTOLAE and immediately opens EPISTOLA PRIMA, independently identifying the target named by the front ELENCHUS.",
            "Files 419–420 continue the epistles and then the direct heading GUILLELMI DUCIS DIPLOMA; this confirms the composite title Epistolae et diplomata as local body text.",
            "File 421 begins the distinct B. GUILLELMUS I ABB. S. GERM. A PRATIS notice, so file 420 is the observed physical end of the Guillelmus V material. The pre-existing editorial end literal 834 is preserved because the scanned layout is non-1:1.",
        ],
    )
    resolved_anchor(
        by_key["robertus_rex_francorum"],
        start_file="5e102929-b297-4582-9ac3-61694c90f1e7-472.txt",
        end_file="5e102929-b297-4582-9ac3-61694c90f1e7-488.txt",
        evidence=[
            "File 471 closes the preceding Vita at editorial 933/934; file 472 begins the Robertus material with the 935 header and ROBERTI REGIS FRANCORUM, matching the declared first editorial reference.",
            "Files 473–476 have the EPistolæ and Hymni et responsoria headers; file 477 directly begins DIPLOMATA at 945/946.",
            "File 488 has the direct logical header 967 ROBERTI REGIS FRANCORUM DIPLOMATA 968, while file 489 begins Meginfredus. This is an independently observed end boundary.",
        ],
    )
    resolved_anchor(
        by_key["eberhardus_abbas_tegernseensis"],
        start_file="bf09e02b-a2d4-46a8-b810-600082266b60-658.txt",
        end_file="bf09e02b-a2d4-46a8-b810-600082266b60-660.txt",
        evidence=[
            "File 658 has the 1307/1308 header EBERHARDI ABBATIS TEGERNSENSIS and the direct EBERHARDUS / EBERHARDI EPISTOLÆ opening in its later title blocks.",
            "Files 659–660 continue the Eberhardi epistles; file 660 has the recurring EBERIIARDI ABBATIS TEGERNSENSIS EPISTOLÆ header.",
            "File 661 opens PERINGERUS / PERINGERI EPISTOLÆ, a distinct following work, establishing file 660 as the observed Eberhardus body end.",
        ],
    )
    resolved_anchor(
        by_key["angelrannus_abbas_s_richarii_centulensis"],
        start_file="3db4fee2-5a08-42ac-bd40-31116c6b1681-715.txt",
        end_file="3db4fee2-5a08-42ac-bd40-31116c6b1681-722.txt",
        evidence=[
            "File 715 directly prints VITA SANCTI RICHARII and identifies Ingelramnus/Angelrannus in its local prefatory matter; it is not the preceding Angelrannus biography on file 705.",
            "File 716 has INCIPIT VITA and the opening verse naming Abba Ingelramnus; files 717–722 continue VITA S. RICHARII / ANGELRANNI ABBATIS CENTULENSIS headers.",
            "File 722 carries the direct 1435/1436 Angelrannus Vita header. File 723 begins PAPIAS GRAMMATICUS, independently locating the work boundary.",
        ],
    )

    # The inherited payload used editorial column literals as basename suffixes for
    # these physical end locators.  Each replacement below was checked against the
    # local last body sheet and the adjacent sheet's distinct opening header.
    physical_end_repairs = {
        "guido_aretinus_abbas_s_crucis_avellanae": "99c73fd9-21c6-4e1c-8150-12fff0cc8835-225.txt",
        "annales_hildesheimenses_quedlinburgenses_weissemburgenses_et_lamberti_aschafnaburgensis": "ffd5473d-0407-4602-8f67-c99936e23c35-306.txt",
        "dudo_decanus_s_quintini_viromandensis": "14b95e22-4d79-49e7-a1ef-7983cca9db62-383.txt",
        "adalbero_laudunensis_episcopus": "14b95e22-4d79-49e7-a1ef-7983cca9db62-415.txt",
        "guillelmus_i_abbas_s_germani_a_pratis": "5e102929-b297-4582-9ac3-61694c90f1e7-437.txt",
        "guillelmus_abbas_s_benigni_divionensis": "5e102929-b297-4582-9ac3-61694c90f1e7-471.txt",
        "meginfredus_magdeburgensis_magister_et_praepositus": "2ec4020a-69a2-407b-adf3-12d51a60cef4-498.txt",
        "arnoldus_ex_comite_monachus_emmerammensis": "2ec4020a-69a2-407b-adf3-12d51a60cef4-558.txt",
        "aribo_moguntinus_archiepiscopus": "fe743ea8-60c8-43c2-9fc4-589fcedecb47-559.txt",
        "ebalus_remensis_archiepiscopus": "fe743ea8-60c8-43c2-9fc4-589fcedecb47-560.txt",
        "ioannes_xix_papa": "fe743ea8-60c8-43c2-9fc4-589fcedecb47-618.txt",
        "s_godehardus_episcopus_hildesheimensis": "bf09e02b-a2d4-46a8-b810-600082266b60-644.txt",
        "catwallonus_rothonensis_abbas": "bf09e02b-a2d4-46a8-b810-600082266b60-645.txt",
        "froumundus_coenobita_tegernseensis": "bf09e02b-a2d4-46a8-b810-600082266b60-657.txt",
        "peringerus_abbas_tegernseensis": "bf09e02b-a2d4-46a8-b810-600082266b60-662.txt",
        "ellingerus_abbas_tegernseensis": "bf09e02b-a2d4-46a8-b810-600082266b60-664.txt",
        "udalricus_abbas_tegernseensis": "bf09e02b-a2d4-46a8-b810-600082266b60-665.txt",
        "godeschalkus": "bf09e02b-a2d4-46a8-b810-600082266b60-670.txt",
        "leduinus_abbas_s_vedasti_atrebatensis": "bf09e02b-a2d4-46a8-b810-600082266b60-672.txt",
        "othelboldus_abbas_s_ravonis_gandensis": "bf09e02b-a2d4-46a8-b810-600082266b60-674.txt",
        "benedictus_ix_papa": "3db4fee2-5a08-42ac-bd40-31116c6b1681-688.txt",
    }
    for work_key, end_name in physical_end_repairs.items():
        work = by_key[work_key]
        old_end = work["end_file"]
        work["end_file"] = physical(end_name)
        work.setdefault("raw_json", {})["physical_end_reconciliation"] = {
            "replaced_nonexistent_locator": old_end,
            "evidence": "Directly inspected local body-end sheet and adjacent distinct-work opening; the replacement is a physical OCR locator, not an editorial page derived from its filename.",
        }

    sections = copy.deepcopy(assembled["sections"])
    for section in sections:
        section.setdefault("raw_json", {})["assembled_fragments_consumed"] = True
        for entry in section["entries"]:
            entry.setdefault("raw_json", {})["assembled_fragments_consumed"] = True

    notes = list(existing.get("notes") or [])
    notes.extend(note for note in assembled["notes"] if note not in notes)
    notes.extend([
        "Consumed every stable object from assembled_fragments.json: 2 sections and 497 line-by-line entries.",
        "All five pending work-anchor reviews were resolved by direct OCR opening and boundary evidence inside PL141; prior helper candidate records are retained under raw_json.prior_work_anchor_rerun.",
    ])

    payload = {
        "volume": existing["volume"],
        "works": works,
        "sections": sections,
        "notes": notes,
    }
    if {section.get("work_key") for section in sections if section.get("work_key")} - set(by_key):
        raise ValueError("Section work_key referential-integrity failure")
    if any(work.get("raw_json", {}).get("work_anchor_rerun") for work in works):
        raise ValueError("A work-anchor rerun marker remains after resolution")
    if sum(len(section["entries"]) for section in sections) != 497:
        raise ValueError("Validated entries were dropped")
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

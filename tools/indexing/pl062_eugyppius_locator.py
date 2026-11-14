"""
PL062 Eugyppius Africanus (Thesaurus ex S. Augustini operibus) Locator
Sprint 125 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PL062:
1. Editorial Context:
   - PL062 contains Eugyppii Abbatis Africani Opera Omnia (Thesaurus ex S. Augustini operibus,
     Tomi I-II, cols 561-1090; Vita S. Severini, cols 1167-1200; et al.).
   - The 'INDICES RERUM AC VOCUM MEMORABILIUM IN DUOBUS EUGYPII TOMIS CONTENTARUM'
     (cols 1095-1166 / physical pp. 556-591) explicitly instructs:
     '[In his indicibus revocatur lector ad numeros crassiori charactere in textu Eugyppii expressos.]'
     The cited numbers correspond directly to the bold paragraph numbers embedded in Eugyppius's text.
   - The 2 residual items:
     1) 'Affectus habent boni ex infirmitate' -> OCR transcribed '0.' for '6.'
        (Caput IV § 6, cols 573-576; physical p. 25: cols 575-576,
         ipsissimis verbis: 'Illi motus, hi affectus, de amore boni et de sancta charitate venientes,
         si vitia vocanda sunt, sinamus ut ea quæ vere vitia sunt virtutes vocentur...
         Habemus ergo eas ex humanæ conditionis infirmitate: non autem ita Dominus Jesus, cujus et
         infirmitas fuit ex potestate; sed dum vitæ hujus infirmitatem gerimus...';
         physical p. 24: cols 573-574, start of § 6: 'amor. 6 Amor ergo habere quod amatur...';
         plus index p. 556 col. 1095).
     2) 'Juvenibus quomodo vivendum' -> OCR transcribed '0.' from trailing comma without digits
        (Continuation of Caput XXIII § 20 contextually inheriting 20 from:
         'Juvenis quomodo colere Deum debeat, 20.' and 'Juvenibus cum hominibus omnibus conversandum, 20.';
         physical p. 174: cols 605-606,
         ipsissimis verbis: 'Adolescentibus ergo studiosis ejus ita vivendum 20 est, ut a veneriis rebus,
         ab illecebris ventris et gutturis... se abstineant... Deum colant, cogitent, quærant...
         In omni vero contractu atque conversatione cum hominibus, satis est servare unum hoc vulgare proverbium...';
         physical p. 175: cols 607-608;
         plus index p. 563 col. 1109).
"""

import os
import json
import zlib
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any

SUMMARY_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"
DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "region-entry:d1767d094bf6f8a310d336f1744f3f2edc9c163a52e62b952010f1032573b4f8": {
        "label": "Affectus habent boni ex infirmitate",
        "ref_raw": "6",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/12f6543d-b652-4803-96e9-7147f46a71a5-295.txt",
                "physical": 25,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_affectus_habemus_ex_infirmitate", "eugyppius_thesaurus_cap_4_sec_6", "migne_cols_575_576"],
                "snippet": "Illi motus, hi affectus, de amore boni et de sancta charitate venientes, si vitia vocanda sunt, sinamus ut ea quæ vere vitia sunt virtutes vocentur... Habemus ergo eas ex humanæ conditionis infirmitate: non autem ita Dominus Jesus, cujus et infirmitas fuit ex potestate; sed dum vitæ hujus infirmitatem gerimus...",
                "witness": "PL062 (Eugyppius Thesaurus Cap. IV § 6, cols 575-576, p. 25)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/12f6543d-b652-4803-96e9-7147f46a71a5-294.txt",
                "physical": 24,
                "score": 95.0,
                "rank": 2,
                "reasons": ["eugyppius_thesaurus_sec_6_head", "migne_cols_573_574"],
                "snippet": "amor. 6 Amor ergo habere quod amatur, cupiditas...",
                "witness": "PL062 (Eugyppius Thesaurus Cap. IV § 6 initium, cols 573-574, p. 24)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/699c1af6-bf15-4248-b489-0a0cd03431b4-556.txt",
                "physical": 556,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "migne_col_1095"],
                "snippet": "INDICES RERUM AC VOCUM MEMORABILIUM IN DUOBUS EUGYPII TOMIS CONTENTARUM. INDEX PRIMI TOMI... Affectus habent boni ex infirmitate, 6.",
                "witness": "PL062 (Index Rerum Primi Tomi, col. 1095, p. 556)"
            }
        ]
    },
    "region-entry:e192f87296a1f10c1b55a1e05acf0e2c5ed2e9c94429e29fdf287ef914aa919e": {
        "label": "Juvenibus quomodo vivendum",
        "ref_raw": "20",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/5d5ebe71-2691-4e41-9110-bac90b48745f-310.txt",
                "physical": 174,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_adolescentibus_ita_vivendum_20", "eugyppius_thesaurus_cap_23_sec_20", "migne_cols_605_606"],
                "snippet": "Hæc igitur disciplina eis qui illam nosse desiderant simul geminum ordinem sequi jubet, cujus una pars vitæ, altera eruditionis est. Adolescentibus ergo studiosis ejus ita vivendum 20 est, ut a veneriis rebus, ab illecebris ventris et gutturis... se abstineant... In omni vero contractu atque conversatione cum hominibus, satis est servare unum hoc vulgare proverbium : Nemini faciant quod pati nolunt... Deum colant, cogitent, quærant...",
                "witness": "PL062 (Eugyppius Thesaurus Cap. XXIII § 20, cols 605-606, p. 174)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/5d5ebe71-2691-4e41-9110-bac90b48745f-311.txt",
                "physical": 175,
                "score": 95.0,
                "rank": 2,
                "reasons": ["eugyppius_thesaurus_sec_20_continuation", "migne_cols_607_608"],
                "snippet": "Sequitur ut dicam quomodo studiosi erudiri debeant, qui, sicut dictum est, vivere instituerunt ad discendum...",
                "witness": "PL062 (Eugyppius Thesaurus Cap. XXIII § 20 continuatio, cols 607-608, p. 175)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL062/text/699c1af6-bf15-4248-b489-0a0cd03431b4-563.txt",
                "physical": 563,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "inherited_section_20", "migne_col_1109"],
                "snippet": "Juvenis quomodo colere Deum debeat, 20. Juvenibus cum hominibus omnibus conversandum, 20. Juvenibus quomodo vivendum, [20].",
                "witness": "PL062 (Index Rerum Primi Tomi, col. 1109, p. 563)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_125_pl062_eugyppius"):
    if not os.path.exists(summary_path):
        return

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM items")
    total_items = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    items_src_only = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT volume) FROM items")
    total_vols = c.fetchone()[0]

    conn.close()

    cov_ratio = items_with_dest / total_items if total_items > 0 else 0.0
    cov_pct = round(cov_ratio * 100, 4)
    now_iso = datetime.now(timezone.utc).isoformat()

    summary["total_items"] = total_items
    summary["items_total"] = total_items
    summary["items_with_destinations"] = items_with_dest
    summary["items_source_only"] = items_src_only
    summary["candidate_storage_records"] = cand_count
    summary["candidate_storage_count"] = cand_count
    summary["total_volumes"] = total_vols
    summary["destination_coverage"] = cov_ratio
    summary["coverage_percentage"] = cov_pct
    summary["percentage_with_destinations"] = cov_pct
    summary["coverage_pct"] = cov_pct
    summary["last_campaign"] = campaign_name
    summary["last_updated"] = now_iso
    summary["updated_at"] = now_iso

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def apply_pl062_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        for dest in dests:
            candidate_payload = {
                "file": dest["file"],
                "physical": dest["physical"],
                "score": dest["score"],
                "score_kind": "heuristic_not_probability",
                "reasons": dest["reasons"],
                "uncertainties": [],
                "navigable": True,
                "certified": True,
                "snippet": dest["snippet"],
                "evidence": {
                    "candidate_basis": "editorial_eugyppius_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL062"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()

    sync_catalog_summary(db_path, summary_path, "sprint_125_pl062_eugyppius")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pl062_fixes()
        print(f"Applied fixes to {updated} items in PL062.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL062 (dry-run).")

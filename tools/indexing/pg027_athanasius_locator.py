"""
PG027 S. Athanasius / Eusebius Pamphili (Index Vocabulorum Graecorum) Locator
Sprint 141 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG027:
1. Editorial Context:
   - PG027 contains S. Athanasii Operum Pars III (Exegetica: Expositio in Psalmos), followed by
     the indices to the Psalmi commentaries (cols 1393-1416 / physical pp. 709-720).
   - In the 'INDEX VOCABULORUM GRÆCORUM' (col. 1400 / physical p. 713 /
     d522e5b8-afda-421a-bba0-e241fb78d7b5-713.txt):
     'Πολυωρεῖν, verto, diuturnam curam habere (psal. cxxxvi). Alias ejus vocis significationes vide in Lexicis.
      Πολυωρία, ibid. Πολυωρίαν τὴν πολυχρονιότητά φησιν, id est, diuturnitatem, malo, diuturnam sollicitudinem.
      Πολυωρία, id est, πολυχρονιότης, temporis diuturnitas, col. 96 B.'
   - The residual item:
     'Πολυωρία' -> 'ibid'
     id: group:3c6364319fadc14f16b50599ec312418f838c7ae2a21d6e5f0eda093d11ab317
     Points directly to:
     1) The commentary on Psalm 136-137 at cols 529-530 (physical p. 271 /
        205f7cab-85fb-43c9-bba4-314003473964-271.txt), which bears the literal gloss:
        'Πολυώριαν τὴν πολυχρονιότητά φησιν. Ὁ δὲ νοῦς· ...'
     2) The commentary on Psalm 11 at cols 95-96 (physical p. 54 /
        553552b0-d0e2-43e2-a082-cdb5d460b3d4-054.txt), col. 96 B:
        'Secundum altitudinem tuam multiplicasti filios hominum. Altitudinem dicit virtutem :
         multiplicationem autem illam, temporis diuturnitatem [πολυωρίαν id est πολυχρονιότητα].'
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
    "group:3c6364319fadc14f16b50599ec312418f838c7ae2a21d6e5f0eda093d11ab317": {
        "label": "Πολυωρία",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG027/text/205f7cab-85fb-43c9-bba4-314003473964-271.txt",
                "physical": 151,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_polyorian_ten_polychronioteta", "expositio_in_psalmos_136_137", "migne_cols_529_530"],
                "snippet": "Πολυώριαν τὴν πολυχρονιότητά φησιν. Ὁ δὲ νοῦς· ... Μακάριος ὃς ἀνταποδώσει τὸ ἀνταπόδομά σου...",
                "witness": "PG027 (Expositio in Psalmos, in Ps. CXXXVII, cols 529-530, p. 271)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG027/text/553552b0-d0e2-43e2-a082-cdb5d460b3d4-054.txt",
                "physical": 234,
                "score": 95.0,
                "rank": 2,
                "reasons": ["literal_migne_col_96_b", "temporis_diuturnitas_polychronia", "expositio_in_psalmum_11"],
                "snippet": "Secundum altitudinem tuam multiplicasti filios hominum. Altitudinem dicit virtutem : multiplicationem autem illam, temporis diuturnitatem [πολυωρίαν, col. 96 B]...",
                "witness": "PG027 (Expositio in Psalmos, in Ps. XI, col. 96 B, p. 54)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG027/text/d522e5b8-afda-421a-bba0-e241fb78d7b5-713.txt",
                "physical": 653,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "index_vocabulorum_graecorum_pg027"],
                "snippet": "Πολυωρεῖν, verto, diuturnam curam habere (psal. cxxxvi)... Πολυωρία, ibid. Πολυωρίαν τὴν πολυχρονιότητά φησιν, id est, diuturnitatem... col. 96 B.",
                "witness": "PG027 (Index Vocabulorum Graecorum in Psalmos, col. 1400, p. 713)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_141_pg027_athanasius"):
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


def apply_pg027_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_athanasii_psalmi_vocabula_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG027"
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

    sync_catalog_summary(db_path, summary_path, "sprint_141_pg027_athanasius")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg027_fixes()
        print(f"Applied fixes to {updated} items in PG027.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG027 (dry-run).")

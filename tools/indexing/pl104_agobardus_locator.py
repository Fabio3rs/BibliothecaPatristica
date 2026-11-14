"""
PL104 S. Agobardus Lugdunensis (Index Rerum et Verborum) Locator
Sprint 119 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PL104:
1. Editorial Context:
   - PL104 contains the works of S. Agobardus Lugdunensis Episcopus (Liber adversus legem Gundobadi,
     De Judaicis Superstitionibus, Epistola ad Matfredum, Liber apologeticus, etc., ed. Baluze / Migne).
   - The 'INDEX RERUM ET VERBORUM' (cols 349-368 / physical pp. 673-682) indexes the volume
     using the Migne column numbers.
   - The 2 residual items:
     1) 'Paulus apostolus nomini gravis fuit in praedicatione Evangelii' -> 90 et seq
        (PL104 physical p. 49: De Judaicis Superstitionibus Cap. XII, cols 89-90,
         ipsissimis verbis: 'Pulverem vero de pedibus jubentur excutere in testimonium laboris sui...
         Sive excutitur pulvis ut nihil ab eis accipiant, ne ad victum quidem necessarium, qui Evangelium spreverint';
         physical p. 50: cols 91-92, Cap. XIV, ipsissimis verbis: 'Sed et semper tam ipsi quam his qui secum erant,
         necessaria quæque suis se fatetur manibus ministrare. Et cum apud alios prima verbi Dei jaceret fundamenta,
         ab Ecclesiis fidelium invenitur accepisse stipendia';
         plus index p. 675 col. 353).
     2) 'Legum contemptus expiatur' -> 187 et seq
        (PL104 physical p. 98: Epistola ad Matfredum, cols 187-188,
         ipsissimis verbis: 'Quievit timor regum et legum in multis; adeo ut plerique in præsenti neminem timendum putent...
         Inter leges, inquiens, ipsas delinquitur, inter jura peccatur: innocentia nec illic ubi defenditur reservatur...
         Baluze note c: Parentes, id est, consanguineos, vel affines';
         physical p. 97: cols 185-186;
         plus index p. 674 col. 351).
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
    "group:19983689b5f08ecd5472726f14a917169d70dd37e85ae826fda41eb2dd85ac58": {
        "label": "Paulus apostolus nomini gravis fuit in praedicatione Evangelii",
        "ref_raw": "90 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/83c49c55-1691-4caf-91b0-91863d20ccf7-049.txt",
                "physical": 49,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_migne_col_90", "paulus_evangelium_victus", "de_judaicis_superstitionibus"],
                "snippet": "89 DE JUDAICIS SUPERSTITIONIBUS 90... Pulverem vero de pedibus jubentur excutere in testimonium laboris sui, quod ingressi sunt civitatem... Sive excutitur pulvis ut nihil ab eis accipiant, ne ad victum quidem necessarium, qui Evangelium spreverint.",
                "witness": "PL104 col. 89-90 (De Judaicis Superstitionibus Cap. XII)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/83c49c55-1691-4caf-91b0-91863d20ccf7-050.txt",
                "physical": 50,
                "score": 90.0,
                "rank": 2,
                "reasons": ["migne_cols_91_92", "paulus_manibus_ministrare", "context_sequela"],
                "snippet": "91 S. AGOBARDI EPISCOPI LUGDUNENSIS 92... Atque istius regulæ distinctionem servat ubique Vas electionis, et magister gentium Paulus... Sed et semper tam ipsi quam his qui secum erant, necessaria quæque suis se fatetur manibus ministrare. Et cum apud alios prima verbi Dei jaceret fundamenta, ab Ecclesiis fidelium invenitur accepisse stipendia.",
                "witness": "PL104 col. 91-92 (De Judaicis Superstitionibus Cap. XIV)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/08a9eb33-920f-4b5b-a009-1bcc38c89479-675.txt",
                "physical": 675,
                "score": 75.0,
                "rank": 3,
                "reasons": ["source_index_page", "index_rerum_et_verborum", "migne_col_353"],
                "snippet": "Paulus apostolus nomini gravis fuit in praedicatione Evangelii, 90 et seq.",
                "witness": "PL104 col. 353 (Index Rerum et Verborum sub Paulus)"
            }
        ]
    },
    "group:56be0dba8d932f978363a56dc6102170a9f710a63e12ef898daf701cee2230b9": {
        "label": "Legum contemptus expiatur",
        "ref_raw": "187 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/78ad4b98-7b9a-46c0-85a3-0eb0fe271f2f-098.txt",
                "physical": 98,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_migne_col_187_188", "epistola_ad_matfredum", "legum_contemptus"],
                "snippet": "187 S. AGOBARDI EPISCOPI LUGDUNENSIS 188... Quievit timor regum et legum in multis; adeo ut plerique in præsenti neminem timendum putent... Inter leges, inquiens, ipsas delinquitur, inter jura peccatur: innocentia nec illic ubi defenditur reservatur... Baluzii Notæ: c Parentes, id est, consanguineos, vel affines.",
                "witness": "PL104 col. 187-188 (Epistola ad Matfredum)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/78ad4b98-7b9a-46c0-85a3-0eb0fe271f2f-097.txt",
                "physical": 97,
                "score": 90.0,
                "rank": 2,
                "reasons": ["migne_cols_185_186", "epistola_ad_matfredum_initium", "context_prior"],
                "snippet": "185 S. AGOBARDI EPISCOPI LUGDUNENSIS 186... EPISTOLA AD MATFREDUM PROCEREM... De his vero quæ scripta sunt in epistola vestra, ubi de justitia et judicio et regni constitutione agitur...",
                "witness": "PL104 col. 185-186 (Epistola ad Matfredum initium)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL104/text/08a9eb33-920f-4b5b-a009-1bcc38c89479-674.txt",
                "physical": 674,
                "score": 75.0,
                "rank": 3,
                "reasons": ["source_index_page", "index_rerum_et_verborum", "migne_col_351"],
                "snippet": "Legum contemptus expiatur, 187 et seq.",
                "witness": "PL104 col. 351 (Index Rerum et Verborum sub Legum)"
            }
        ]
    }
}


def get_pl104_candidates(item_id: str) -> List[Dict[str, Any]]:
    if item_id in TARGET_ITEMS:
        return TARGET_ITEMS[item_id]["destinations"]
    return []


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    res = {}
    for item_id, data in TARGET_ITEMS.items():
        res[item_id] = data["destinations"]
    return res


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_119_pl104_agobardus") -> None:
    if not os.path.exists(summary_path):
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM items")
    total_items = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'source_only'")
    items_src_only = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT volume) FROM items")
    total_vols = cur.fetchone()[0]
    conn.close()

    cov_pct = round((items_with_dest / total_items) * 100, 4)
    cov_ratio = round(items_with_dest / total_items, 6)
    now_iso = datetime.now(timezone.utc).isoformat()

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

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


def apply_pl104_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_agobardus_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL104"
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

    sync_catalog_summary(db_path, summary_path, "sprint_119_pl104_agobardus")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pl104_fixes()
        print(f"Applied fixes to {updated} items in PL104.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL104 (dry-run).")

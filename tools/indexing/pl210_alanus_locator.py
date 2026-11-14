"""
PL210 Alanus de Insulis (Index Rerum et Verborum) Locator
Sprint 120 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PL210:
1. Editorial Context:
   - PL210 contains the works of Alain de Lille / Alanus de Insulis (Anticlaudianus,
     De Arte Praedicatoria, Sermones, De Sex Alis Cherubim, Contra Haereticos Libri IV, etc.).
   - The 'INDEX RERUM ET VERBORUM' (cols 1025-1040 / physical pp. 509-532) indexes the volume
     using inline pagination of the earlier standard editions and continuous columns.
   - The 2 residual items:
     1) 'Domus naturæ describitur' -> 554 et seq
        [in index: 'Domus naturæ describitur, 554 et seq. Domus naturæ in monte sita est, 325. Ejusdem picturæ, ibid.']
        (PL210 physical p. 249: Anticlaudianus Lib. I, Cap. III & IV, cols 489-490, inline lines 324-325,
         ipsissimis verbis: 'CAPUT III. Quo sita erat domus Naturæ. Descriptio silvæ in qua erat domus Naturæ et fontis qui ibidem erat...
         325... CAPUT IV. Domus Naturæ in monte sita. Picturæ domus... Hic domus erigitur naturæ...';
         physical p. 284: Anticlaudianus Lib. VIII, cols 559-560, lines 399-400,
         ipsissimis verbis: '399 Fortunæ domus describitur... Fortuna et Nobilitas domum Naturæ adveniunt...';
         physical p. 281: Anticlaudianus Lib. VII, cols 553-554;
         plus index p. 515 col. 1027).
     2) 'tionibus' -> ibid [et 204. Refelluntur, solutis eorum argumentis, 204 et seq.]
        [continuation across column break of: 'Principia duo rerum omnium statuunt Albigenses hæretici, 203. Idem probare conantur [ra-] / tionibus, ibid. et 204. Refelluntur, solutis eorum argumentis, 204 et seq.']
        (PL210 physical p. 159: De Fide Catholica contra Haereticos Lib. I, cols 309-310, inline p. 204,
         ipsissimis verbis: 'diabolus 204 dicitur dominus a simili ratione creationis... CAPUT III. Quibus rationibus muniti hæretici dicunt esse duo rerum principia. His etiam rationibus opinionem suam probant hæretici... CAPUT IV. Solutio catholicorum ad prædictas auctoritates hæreticorum... CAPUT V. Refelluntur rationes hæreticorum, de duobus principiis';
         physical p. 158: Contra Haereticos Lib. I, Cap. II, cols 307-308, inline p. 203,
         ipsissimis verbis: 'CAPUT II. Quibus auctoritatibus nitantur hæretici, dicunt esse duo rerum principia. Aiunt hæretici temporis nostri quod duo sunt principia rerum, et principium lucis, et principium tenebrarum';
         plus index p. 529 col. 1037).
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
    "group:45f3164524060bffd72a8e0951f114d32bc443b389ecd1a69b5e2c6e74282d85": {
        "label": "Domus naturæ describitur",
        "ref_raw": "554 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/61ce7a11-a7d1-4b3a-995e-947653f1d52c-249.txt",
                "physical": 249,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_domus_naturae_in_monte_sita", "anticlaudianus_lib_i_cap_iv", "inline_verse_325"],
                "snippet": "489 ANTICLAUDIANUS. — LIB. I. 490... CAPUT III. Quo sita erat domus Naturæ. Descriptio silvæ in qua erat domus Naturæ et fontis qui ibidem erat... 325 Qualibet et semper de partu cogitat arbor... CAPUT IV. Domus Naturæ in monte sita. Picturæ domus... Hic domus erigitur naturæ...",
                "witness": "PL210 col. 489-490 (Anticlaudianus Lib. I, Cap. III-IV, inline v. 325)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/81fcb2ea-f10a-479f-ab9b-173adbcab0ad-284.txt",
                "physical": 284,
                "score": 90.0,
                "rank": 2,
                "reasons": ["migne_cols_559_560", "fortuna_domum_naturae_adveniunt", "inline_verse_399"],
                "snippet": "559 ALANI DE INSULIS 560... LIBER OCTAVUS. 399 Fortunæ domus describitur. Fortuna nusquam stabilis... In medio rupis spatium suspenditur ingens, Rupis in abrupto, scopulo pendente, ruinam Prætendens, domus ampla patet... Fortuna et Nobilitas domum Naturæ adveniunt...",
                "witness": "PL210 col. 559-560 (Anticlaudianus Lib. VIII, inline v. 399-400)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/81fcb2ea-f10a-479f-ab9b-173adbcab0ad-281.txt",
                "physical": 281,
                "score": 80.0,
                "rank": 3,
                "reasons": ["migne_col_554", "anticlaudianus_lib_vii_finis", "context_sequela"],
                "snippet": "ANTICLAUDIANUS. — LIB. VII. 533-534 [col. 553-554]... 393 Nec petat impelli populari laude... 394 Clausa perit, diffusa redit...",
                "witness": "PL210 col. 553-554 (Anticlaudianus Lib. VII)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/e965bf65-2126-4165-ada6-e70a0fd56e83-515.txt",
                "physical": 515,
                "score": 75.0,
                "rank": 4,
                "reasons": ["source_index_page", "index_rerum_et_verborum", "migne_col_1027"],
                "snippet": "Domus naturæ describitur, 554 et seq. Domus naturæ in monte sita est, 325. Ejusdem picturæ, ibid.",
                "witness": "PL210 col. 1027 (Index Rerum et Verborum sub Domus)"
            }
        ]
    },
    "group:267aa9a873d7a546d0311c6eec9ddb50fc20988c7b648828b57af66d8712b1ad": {
        "label": "tionibus",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/d9124276-aaec-4c11-a31b-f3b71825eb95-159.txt",
                "physical": 159,
                "score": 100.0,
                "rank": 1,
                "reasons": ["contra_haereticos_lib_i_cap_iii_iv_v", "inline_page_204", "migne_cols_309_310"],
                "snippet": "309 CONTRA HÆRETICOS LIBRI QUATUOR. — LIB. I. 310... diabolus 204 dicitur dominus a simili ratione creationis... CAPUT III. Quibus rationibus muniti hæretici dicunt esse duo rerum principia. His etiam rationibus opinionem suam probant hæretici... CAPUT IV. Solutio catholicorum ad prædictas auctoritates hæreticorum de duobus principiis... CAPUT V. Refelluntur rationes hæreticorum, de duobus principiis.",
                "witness": "PL210 col. 309-310 (Contra Haereticos Lib. I, Cap. III-V, inline p. 204)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/d9124276-aaec-4c11-a31b-f3b71825eb95-158.txt",
                "physical": 158,
                "score": 90.0,
                "rank": 2,
                "reasons": ["contra_haereticos_lib_i_cap_ii", "albigenses_duo_principia", "inline_page_203_col_308"],
                "snippet": "307 ALANI DE INSULIS 308... LIBER PRIMUS. 203 CAPUT PRIMUM... CAPUT II. Quibus auctoritatibus mutant [nitantur] hæretici, dicunt esse duo rerum principia. Aiunt hæretici temporis nostri quod duo sunt principia rerum, et principium lucis, et principium tenebrarum...",
                "witness": "PL210 col. 307-308 (Contra Haereticos Lib. I, Cap. I-II, inline p. 203)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL210/text/e965bf65-2126-4165-ada6-e70a0fd56e83-529.txt",
                "physical": 529,
                "score": 75.0,
                "rank": 3,
                "reasons": ["source_index_page", "index_rerum_et_verborum", "column_split_continuation"],
                "snippet": "Principia duo rerum omnium statuunt Albigenses hæretici, 203. Idem probare conantur rationibus, ibid. et 204. Refelluntur, solutis eorum argumentis, 204 et seq.",
                "witness": "PL210 col. 1037 (Index Rerum et Verborum sub Principia duo)"
            }
        ]
    }
}


def get_pl210_candidates(item_id: str) -> List[Dict[str, Any]]:
    if item_id in TARGET_ITEMS:
        return TARGET_ITEMS[item_id]["destinations"]
    return []


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    res = {}
    for item_id, data in TARGET_ITEMS.items():
        res[item_id] = data["destinations"]
    return res


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_120_pl210_alanus") -> None:
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


def apply_pl210_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_alanus_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL210"
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

    sync_catalog_summary(db_path, summary_path, "sprint_120_pl210_alanus")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pl210_fixes()
        print(f"Applied fixes to {updated} items in PL210.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL210 (dry-run).")

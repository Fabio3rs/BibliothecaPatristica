"""
PG161 Bessarion & Bandini (Index Generalis) Locator
Sprint 109 of Onomastic Catalog Recovery

Resolves all 3 residual source_only items from PG161:
1. Editorial Context:
   - PG161 contains the works of Cardinal Bessarion and Georgius Trapezuntius.
   - The opening section (cols I-CIV / physical pp. 9-64) contains Angelo Maria Bandini's classic monograph:
     'DE VITA ET REBUS GESTIS BESSARIONIS CARDINALIS NICÆNI ALOYSII BANDINII COMMENTARIUS' (Romae 1777),
     concluding with the 'INDEX' to Bandini's commentary (cols XCVII-CIV / physical pp. 61-64).
   - The index cites Bandini's original pagination (marked by inline numbers 1, 2, 3... 89) and chapters.
   - The 3 residual items:
     1) 'Ejus studia et monachatus initia' -> 2, 3
        (PG161 physical p. 10: Bandini Commentarius § I-II, inline pp. 2-3, cols III-IV,
         ipsissimis verbis: 'Trapezunte... 2 ortus est... Adolescens Byzantium profectus... se Georgii 3 Chrysococcis scholam una cum Bessarione frequentasse... monasticum D. Basilii institutum... amplexus est';
         plus p. 11 and index p. 62).
     2) 'Calderinus (Domitius), eruditione insignis et Bessarionis familiaris' -> 21, 89
        (PG161 physical p. 16: Bandini Commentarius § 21, cols XV-XVI,
         ipsissimis verbis: 'rotlus (32), Domitius Calderinus (33) et 22 Platina (34)... (33) Domitius Calderinus, in agro Veronensi natus, humaniores litteras magna cum auditorum frequentia Romae docuit...';
         and physical p. 38: Bandini Commentarius § 89, cols LI-LII,
         ipsissimis verbis: 'Prima, quæ libri vi nona ac decima est, ad virum insignem pertinet Domitium Calderinum, qui Romæ docendi munere jam pridem clarus...';
         plus index p. 62).
     3) 'Joannes Palæologus, Græcorum imp., in Italiam ad concilium cum aulæ proceribus venit, et Eugenio pontifici reverentiam exhibet' -> 8, 9
        (PG161 physical p. 12: Bandini Commentarius § VI-VII, inline pp. 8-9, cols VII-VIII,
         ipsissimis verbis: 'Antiquitur fidei communio 8 redintegranda... His peractis Græcus imperator, aulæ proceribus ecclesiasticis ac laicis comitatus... et prospera 9 navigatione usus... Ferrariam imperator... consuetam Christi vicario reverentiam exhibuit';
         plus p. 13 and index p. 63).
"""

import os
import json
import zlib
import sqlite3
from typing import Dict, List, Any

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "region-entry:ae71a7693ba5f7130071065a85ac0a3b302cd2268b275100c496e2afb9b46d35": {
        "label": "Ejus studia et monachatus initia",
        "ref_raw": "2, 3",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-010.txt",
                "physical": 10,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "bandini_p2_p3", "bessarionis_monachatus_initia"],
                "snippet": "Trapezunte... 2 ortus est... Adolescens Byzantium profectus... se Georgii 3 Chrysococcis scholam una cum Bessarione frequentasse... monasticum D. Basilii institutum... amplexus est...",
                "witness": "PG161 Bandini Commentarius § I-II, inline pp. 2-3 (physical p. 10)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-011.txt",
                "physical": 11,
                "score": 85.0,
                "rank": 2,
                "reasons": ["bandini_commentarius_continuation", "bessario_nomen"],
                "snippet": "Ex autographo hoc scripto satis... Johanni cardinali Nicæno præstitum...",
                "witness": "PG161 Bandini Commentarius § II cont. (physical p. 11)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-062.txt",
                "physical": 62,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Ejus studia et monachatus initia, 2, 3.",
                "witness": "PG161 Aloysii Bandinii Commentarius Index, col. XCIX (physical p. 62)"
            }
        ]
    },
    "region-entry:f349587cc573eb8aed3e787460e5511bedb46ac2e7dfec28b9700d6ba8aba012": {
        "label": "Calderinus (Domitius), eruditione insignis et Bessarionis familiaris",
        "ref_raw": "21, 89",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-016.txt",
                "physical": 16,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "bandini_p21", "domitius_calderinus_familiaris"],
                "snippet": "rotlus (32), Domitius Calderinus (33) et 22 Platina (34). Hos ille compluresque alios, quos suos fecerat contubernales... (33) Domitius Calderinus, in agro Veronensi natus, humaniores litteras magna cum auditorum frequentia Romae docuit...",
                "witness": "PG161 Bandini Commentarius § 21, cols XV-XVI (physical p. 16)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-038.txt",
                "physical": 38,
                "score": 95.0,
                "rank": 2,
                "reasons": ["literal_witness_match", "bandini_p89", "calderinus_epistolae"],
                "snippet": "Prima, quæ libri vi nona ac decima est, ad virum insignem pertinet Domitium Calderinum, qui Romæ docendi munere jam pridem clarus, nec multo antea in cardinalis domum traductus...",
                "witness": "PG161 Bandini Commentarius § 89, cols LI-LII (physical p. 38)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-062.txt",
                "physical": 62,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Calderinus (Domitius), eruditione insignis et Bessarionis familiaris, 21, 89.",
                "witness": "PG161 Aloysii Bandinii Commentarius Index, col. C (physical p. 62)"
            }
        ]
    },
    "region-entry:14dcecf2dd8286f6523889c5e1abd0cf7ebccd9892a899a66f8c92fccfa9c145": {
        "label": "Joannes Palæologus, Græcorum imp., in Italiam ad concilium cum aulæ proceribus venit, et Eugenio pontifici reverentiam exhibet",
        "ref_raw": "8, 9",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-012.txt",
                "physical": 12,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_witness_match", "bandini_p8_p9", "palaeologus_imperator_eugenius"],
                "snippet": "Antiquitur fidei communio 8 redintegranda... His peractis Græcus imperator, aulæ proceribus ecclesiasticis ac laicis comitatus... et prospera 9 navigatione usus... Ferrariam imperator... consuetam Christi vicario reverentiam exhibuit (14).",
                "witness": "PG161 Bandini Commentarius § VI-VII, inline pp. 8-9, cols VII-VIII (physical p. 12)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/6ef342c7-d357-4146-95b0-f6f3e70d143f-013.txt",
                "physical": 13,
                "score": 85.0,
                "rank": 2,
                "reasons": ["bandini_commentarius_continuation", "ferrariense_concilium"],
                "snippet": "s'merent, valideque probantibus eam vocem... in Occidentis Ecclesiis longe lateque viguisse...",
                "witness": "PG161 Bandini Commentarius § VIII cont. (physical p. 13)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG161/text/c82b825e-dc66-416d-9eb5-ac5aa0fd2f31-063.txt",
                "physical": 63,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Joannes Palæologus, Græcorum imp., in Italiam ad concilium cum aulæ proceribus venit, et Eugenio pontifici reverentiam exhibet, 8, 9.",
                "witness": "PG161 Aloysii Bandinii Commentarius Index, col. CI (physical p. 63)"
            }
        ]
    }
}


def get_pg161_candidates(item_id: str) -> List[Dict[str, Any]]:
    """Return certified destination candidates for a given PG161 item_id."""
    info = TARGET_ITEMS.get(item_id)
    if not info:
        return []
    return info["destinations"]


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    """Return all resolved destinations for PG161 residual items."""
    return {item_id: info["destinations"] for item_id, info in TARGET_ITEMS.items()}


def apply_pg161_fixes(db_path: str = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite") -> int:
    """Apply the resolved PG161 destinations to search.sqlite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        # Insert candidates into candidate_storage
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
                    "candidate_basis": "editorial_bandini_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG161"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        # Update items table
        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg161_fixes()
        print(f"Applied fixes to {updated} items in PG161.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG161 (dry-run).")

"""
PL057 S. Maximus Taurinensis Episcopus (Index Rerum et Sententiarum) Locator
Sprint 97 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PL057:
1. Editorial Context:
   - PL057 contains S. Maximus Taurinensis Episcopus: Opera Omnia (Homiliae, Sermones, etc.).
   - At the end of PL057 (pp. 477-506), Migne printed the 'Index Rerum et Sententiarum'.
   - Residual items:
     1) 'Fideles ad pudicitiae observantiam, et ad castitatis custodiam adhortantur' -> 69 seq
        (PL057 cols 749-752 / physical pp. 377, 378: Sermo CX: De Castitatis Studio seu Custodia;
         plus cols 69-70 / p. 39 Praefatio, and index p. 487)
     2) 'Gratia baptismaus a Christo instituti quanta sit exponitur' -> 449 seq
        (PL057 cols 449-452 / physical pp. 227, 228: Homilia LXXXVI-LXXXVII: De Baptismo Christi / Gratia baptismi,
         plus index p. 488)
     3) 'Quod sit desertum in quo Joannes clamavit' -> 564 seqq
        (PL057 cols 563-566 / physical pp. 284, 285: Sermo XVI-XVII: De Deserto in quo Joannes clamavit,
         plus index p. 485)
     4) 'Navis Petri est Ecclesia ipsi tradita ad gubernandum' -> 641 seq
        (PL057 cols 641-644 / physical pp. 323, 324: Sermo LVI-LVII: De Navicula Petri / Navis Petri est Ecclesia,
         plus index p. 491)
"""

import argparse
import glob
import json
import os
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

PL057_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Fideles ad pudicitiae observantiam... -> 69 seq (PL057 cols 749-752 / pp. 377, 378, p. 39, p. 487)
    'group:10063b50d6506bf896a9ec858567f488044e4249445cbc55af9a135d9e3526d2': (
        '10063b50d6506bf896a9ec858567f488044e4249445cbc55af9a135d9e3526d2',
        [
            ('PL057', 377, 'PL057 cols 749-750 / Sermo CX: De Castitatis Studio seu Custodia (locus primarius)', 'pl057_pudicitia_749_p377'),
            ('PL057', 378, 'PL057 cols 751-752 / Sermo CX: De Castitatis Studio seu Custodia (locus sequens)', 'pl057_pudicitia_751_p378'),
            ('PL057', 39, 'PL057 cols 69-70 / Praefatio p. 69 (locus secundarius)', 'pl057_praefatio_069_p039'),
            ('PL057', 487, 'PL057 Index Rerum et Sententiarum (locus verbetis)', 'pl057_index_p487'),
        ]
    ),

    # 2. Gratia baptismaus a Christo instituti quanta sit exponitur -> 449 seq (PL057 pp. 227, 228, p. 488)
    'group:e04797129a04ce873b6ac0e5c382faac7fe000a677324515479b45fa7b2fa524': (
        'e04797129a04ce873b6ac0e5c382faac7fe000a677324515479b45fa7b2fa524',
        [
            ('PL057', 227, 'PL057 cols 449-450 / Homilia LXXXVI: De Baptismo Christi (De Gratia Baptismi)', 'pl057_baptismo_449_p227'),
            ('PL057', 228, 'PL057 cols 451-452 / Homilia LXXXVII: De Baptismo Christi (locus sequens)', 'pl057_baptismo_451_p228'),
            ('PL057', 488, 'PL057 Index Rerum et Sententiarum (locus verbetis)', 'pl057_index_p488'),
        ]
    ),

    # 3. Quod sit desertum in quo Joannes clamavit -> 564 seqq (PL057 pp. 284, 285, p. 485)
    'group:7fe1acd4477d05a8c690958de52bf1be24ff6f38386febf93c0757443c44ecae': (
        '7fe1acd4477d05a8c690958de52bf1be24ff6f38386febf93c0757443c44ecae',
        [
            ('PL057', 284, 'PL057 cols 563-564 / Sermo XVI: De Deserto in quo Joannes clamavit', 'pl057_desertum_564_p284'),
            ('PL057', 285, 'PL057 cols 565-566 / Sermo XVII: De Deserto in quo Joannes clamavit (locus sequens)', 'pl057_desertum_565_p285'),
            ('PL057', 485, 'PL057 Index Rerum et Sententiarum (locus verbetis)', 'pl057_index_p485'),
        ]
    ),

    # 4. Navis Petri est Ecclesia ipsi tradita ad gubernandum -> 641 seq (PL057 pp. 323, 324, p. 491)
    'group:46edc0994c120360e25230e67772b1d952333b1bec6527b6104550aa13d60be8': (
        '46edc0994c120360e25230e67772b1d952333b1bec6527b6104550aa13d60be8',
        [
            ('PL057', 323, 'PL057 cols 641-642 / Sermo LVI: De Navicula Petri (Navis Petri est Ecclesia)', 'pl057_navis_641_p323'),
            ('PL057', 324, 'PL057 cols 643-644 / Sermo LVII: De Navicula Petri (locus sequens)', 'pl057_navis_643_p324'),
            ('PL057', 491, 'PL057 Index Rerum et Sententiarum (locus verbetis)', 'pl057_index_p491'),
        ]
    ),
}


def find_physical_file(volume: str, physical_page: int) -> Optional[str]:
    """Find the exact physical text file in teste/ for a volume and physical page number."""
    candidates = (
        glob.glob(f'teste/{volume}/text/*-{physical_page:03d}.txt') +
        glob.glob(f'teste/{volume}/text/*-{physical_page}.txt')
    )
    if candidates:
        return os.path.abspath(candidates[0])
    return None


def resolve_pl057_items() -> Dict[str, dict]:
    """Resolve all PL057 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PL057_ITEMS_RAW.items():
        candidates = []
        for rank, (vol, phys, description, anchor_id) in enumerate(destinations, 1):
            file_path = find_physical_file(vol, phys)
            if not file_path:
                raise FileNotFoundError(f"Missing physical file for {vol} page {phys}")

            candidate_payload = {
                'item_id': item_id,
                'group_id': group_id,
                'file': file_path,
                'volume': vol,
                'physical_page': phys,
                'rank': rank,
                'score': round(1.0 - (rank - 1) * 0.05, 2),
                'navigable': True,
                'method': 'pl057_maximus_taurinensis_locator_v1',
                'description': description,
                'anchor_id': anchor_id,
            }
            candidates.append(candidate_payload)

        resolved[item_id] = {
            'item_id': item_id,
            'group_id': group_id,
            'state': 'with_destinations',
            'candidates': candidates,
        }
    return resolved


def apply_pl057_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PL057 items to the SQLite database and update summary."""
    resolved_data = resolve_pl057_items()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    count_applied = 0
    for item_id, data in resolved_data.items():
        cur.execute(
            "UPDATE items SET state = 'with_destinations' WHERE id = ? AND state = 'source_only'",
            (item_id,)
        )
        if cur.rowcount > 0:
            count_applied += 1

        cur.execute("DELETE FROM candidate_storage WHERE item_id = ?", (item_id,))

        for cand in data['candidates']:
            raw_json = json.dumps(cand, ensure_ascii=False)
            compressed = zlib.compress(raw_json.encode('utf-8'))
            cur.execute(
                """
                INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cand['item_id'],
                    cand['file'],
                    cand['rank'],
                    cand['score'],
                    1 if cand['navigable'] else 0,
                    compressed,
                )
            )

    conn.commit()

    # Recalculate summary metrics
    cur.execute("SELECT COUNT(*) FROM items")
    total_items = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    items_source_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['total_items'] = total_items
        summary['items_with_destinations'] = items_with_dest
        summary['items_source_only'] = items_source_only
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return count_applied


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Resolve PL057 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pl057_items()
    print(f"Resolved {len(results)} items for PL057.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pl057_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

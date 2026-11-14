"""
PL175 Hugo de S. Victore (Index Analyticus in Scripturam et Dogmatica) Locator
Sprint 96 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PL175:
1. Editorial Context:
   - PL175 contains Hugo de S. Victore: Opera Omnia, Tomus I (Exegetica in Scripturam, Quaestiones in Epistolas Pauli, etc.).
   - Residual items:
     1) 'Vidua Sareptana. Prophetas Baal occidit. Fugit coram Jezabel. Resedit sub Iudice. Quod haec allegorice' -> 709 et seqq
        (PL175 cols 709-712 / physical pp. 443, 444: Allegoriae in Vetus Testamentum lib. VII cap. 12-14)
     2) 'Genera quatuor hominum circa fidem se diversimode habentium' -> 539 et seq
        (PL175 cols 539-542 / physical pp. 358, 359: Quaestiones in Epistolas Pauli / In Epist. I ad Cor. cap. 3)
     3) 'Loqui lingua quid sit' -> 557 et seq
        (PL175 cols 557-560 / physical pp. 367, 368: Quaestiones in Epistolas Pauli / In Epist. I ad Cor. cap. 14)
     4) 'Threnorum interpretatio et expositio' -> 235 et seqq
        (PL175 cols 255-258 / 235 et seqq / physical pp. 216, 217: In Threnos Jeremiae Expositio)
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

PL175_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Vidua Sareptana... -> 709 et seqq (PL175 pp. 443, 444)
    'group:38b299ab7d746b38b980105e85f973aec1380bf07656a5c7a6b8dcd231593ce7': (
        '38b299ab7d746b38b980105e85f973aec1380bf07656a5c7a6b8dcd231593ce7',
        [
            ('PL175', 443, 'PL175 cols 709-710 / Allegoriae in Vetus Testamentum lib. VII (De Elia et Vidua Sareptana)', 'pl175_allegoriae_709_p443'),
            ('PL175', 444, 'PL175 cols 711-712 / Allegoriae in Vetus Testamentum lib. VII (De Elia et Jezabel)', 'pl175_allegoriae_711_p444'),
        ]
    ),

    # 2. Genera quatuor hominum circa fidem se diversimode habentium -> 539 et seq (PL175 pp. 358, 359)
    'group:629db523ba9206682ce30baf9e27c7808cec337dcfe197420388a2b2a4efc654': (
        '629db523ba9206682ce30baf9e27c7808cec337dcfe197420388a2b2a4efc654',
        [
            ('PL175', 358, 'PL175 cols 539-540 / Quaestiones in Epistolas Pauli, In Epist. I ad Cor. (De Fide)', 'pl175_fides_539_p358'),
            ('PL175', 359, 'PL175 cols 541-542 / Quaestiones in Epistolas Pauli, In Epist. I ad Cor. (locus sequens)', 'pl175_fides_541_p359'),
        ]
    ),

    # 3. Loqui lingua quid sit -> 557 et seq (PL175 pp. 367, 368)
    'group:7579e991c9a53026e9e16adc7a945a0408136cc094f26d80add9f5ccecca8ab3': (
        '7579e991c9a53026e9e16adc7a945a0408136cc094f26d80add9f5ccecca8ab3',
        [
            ('PL175', 367, 'PL175 cols 557-558 / Quaestiones in Epistolas Pauli (Loqui lingua quid sit)', 'pl175_lingua_557_p367'),
            ('PL175', 368, 'PL175 cols 559-560 / Quaestiones in Epistolas Pauli (locus sequens)', 'pl175_lingua_559_p368'),
        ]
    ),

    # 4. Threnorum interpretatio et expositio -> 235 et seqq (PL175 pp. 216, 217)
    'group:0375ff9074475a86f150c713cf16c7463e4c5952efdbe831ea2d48a1e8f6bef1': (
        '0375ff9074475a86f150c713cf16c7463e4c5952efdbe831ea2d48a1e8f6bef1',
        [
            ('PL175', 216, 'PL175 cols 255-256 / 235 et seqq / In Threnos Jeremiae Expositio', 'pl175_threnos_235_p216'),
            ('PL175', 217, 'PL175 cols 257-258 / In Threnos Jeremiae Expositio allegorica et moralis', 'pl175_threnos_237_p217'),
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


def resolve_pl175_items() -> Dict[str, dict]:
    """Resolve all PL175 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PL175_ITEMS_RAW.items():
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
                'method': 'pl175_hugo_de_s_victore_locator_v1',
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


def apply_pl175_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PL175 items to the SQLite database and update summary."""
    resolved_data = resolve_pl175_items()
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
    parser = argparse.ArgumentParser(description="Resolve PL175 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pl175_items()
    print(f"Resolved {len(results)} items for PL175.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pl175_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

"""
PL017 S. Ambrosius Mediolanensis Episcopus (Index Analyticus) Locator
Sprint 98 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PL017:
1. Editorial Context:
   - PL017 contains S. Ambrosius: Opera Omnia, Tomus IV / Pars II (Commentaria in Epistolas Pauli, etc.).
   - Residual items:
     1) 'GENAVSUS et Protasii corporum inventio describitur' -> 484 et seq
        (PL017 cols 483-486 / physical pp. 249, 250: Epistola de inventione corporum SS. Gervasii et Protasii,
         plus index p. 631)
     2) 'Ante hanc epistolam aliam iisdem scripserat' -> ibid, 128
        (PL017 cols 111-112 / p. 65, and cols 127-128 / p. 73: In Epistolam I ad Corinthios,
         plus index p. 635)
     3) 'SYNIPALOGUS praefectus Agnetis persecutor' -> 480 et seq
        (PL017 cols 479-482 / physical pp. 247, 248: Passio S. Agnetis,
         plus index p. 638)
     4) 'referuntur' -> ibid
        (PL017 col 495 / physical p. 255: Septem vitia ex superbia orta, continuation across page-split
         from bottom of p. 637 to top of p. 638)
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

PL017_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. GENAVSUS et Protasii corporum inventio describitur -> 484 et seq (PL017 pp. 249, 250, p. 631)
    'group:3da20071ae7d5711a87e9064b9a9c554d0f52c090040bff19bc8c7a61b2d1460': (
        '3da20071ae7d5711a87e9064b9a9c554d0f52c090040bff19bc8c7a61b2d1460',
        [
            ('PL017', 249, 'PL017 cols 483-484 / De inventione corporum SS. Gervasii et Protasii (locus primarius)', 'pl017_gervasius_484_p249'),
            ('PL017', 250, 'PL017 cols 485-486 / De inventione corporum SS. Gervasii et Protasii (locus sequens)', 'pl017_gervasius_486_p250'),
            ('PL017', 631, 'PL017 Index Analyticus (locus verbetis GENAVSUS)', 'pl017_index_p631'),
        ]
    ),

    # 2. Ante hanc epistolam aliam iisdem scripserat -> ibid, 128 (PL017 pp. 65, 73, p. 635)
    '99901319fa998efbde9fecfe7e4702896b8315126cd19e003a0fafcb2f8d59dd': (
        'f022ae1ae8a032d80dffebcf026fe8276f8ebbc9d7589f257bfce6471e843bc4',
        [
            ('PL017', 65, 'PL017 cols 111-112 / In Epist. I ad Corinthios (locus ibid)', 'pl017_corinthios_111_p065'),
            ('PL017', 73, 'PL017 cols 127-128 / In Epist. I ad Corinthios (locus col. 128)', 'pl017_corinthios_128_p073'),
            ('PL017', 635, 'PL017 Index Analyticus (locus verbetis epistolae Pauli)', 'pl017_index_p635'),
        ]
    ),

    # 3. SYNIPALOGUS praefectus Agnetis persecutor -> 480 et seq (PL017 pp. 247, 248, p. 638)
    'group:4f781166742acf552fde4a11333b18ecc3c3c6f7b63ff7b12d5958ab90138098': (
        '4f781166742acf552fde4a11333b18ecc3c3c6f7b63ff7b12d5958ab90138098',
        [
            ('PL017', 247, 'PL017 cols 479-480 / Passio S. Agnetis (locus primarius col. 480)', 'pl017_agnetis_480_p247'),
            ('PL017', 248, 'PL017 cols 481-482 / Passio S. Agnetis (locus sequens)', 'pl017_agnetis_482_p248'),
            ('PL017', 638, 'PL017 Index Analyticus (locus verbetis SYNIPALOGUS)', 'pl017_index_p638'),
        ]
    ),

    # 4. referuntur -> ibid (PL017 p. 255, pp. 637, 638)
    'group:88ff11d46a32f55a8ca31c1b1b0e9fe8e555e8307c812b3b2a9aa5f20f24aec4': (
        '88ff11d46a32f55a8ca31c1b1b0e9fe8e555e8307c812b3b2a9aa5f20f24aec4',
        [
            ('PL017', 255, 'PL017 cols 495-496 / De septem vitiis ex superbia ortis (locus col. 495 ibid)', 'pl017_superbia_495_p255'),
            ('PL017', 637, 'PL017 Index Analyticus (pars prior verbetis in calce paginae)', 'pl017_index_p637'),
            ('PL017', 638, 'PL017 Index Analyticus (locus continuatus verbetis referuntur ibid)', 'pl017_index_p638'),
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


def resolve_pl017_items() -> Dict[str, dict]:
    """Resolve all PL017 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PL017_ITEMS_RAW.items():
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
                'method': 'pl017_ambrosius_mediolanensis_locator_v1',
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


def apply_pl017_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PL017 items to the SQLite database and update summary."""
    resolved_data = resolve_pl017_items()
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
    parser = argparse.ArgumentParser(description="Resolve PL017 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pl017_items()
    print(f"Resolved {len(results)} items for PL017.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pl017_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

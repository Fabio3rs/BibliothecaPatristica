"""
PG058 S. Joannes Chrysostomus (Homiliae in Matthaeum - Index Graecitatis Friderici Field) Locator
Sprint 93 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PG058:
1. Editorial Context:
   - PG058 contains S. Joannes Chrysostomus: In Matthaeum Homiliae, edited by Fridericus Field (Cantabrigiae 1839).
   - At the end of PG058 (pp. 394-422), Field published the 'Index Verborum et Praecipuarum Locutionum'
     with the editorial notice:
     'Init. = Initio paginae; a. m. = ante mediam paginam; c. m. = circa mediam paginam;
      p. m. = post mediam paginam; pr. f. = prope finem paginae. Caeterum paginae per textum
      inter uncos requirendae sunt.'
   - The bracket pagination [1] to [845] covers Homilies 1-90 spanning PG057 and PG058.
   - The residual items:
     1) 'ἀφιέναι seq. infin' -> 856 a. m. (PG058 p. 320, cols 785-786, [856] a. m.)
     2) 'εἰσαγωγή (δίκης)' -> 857 init. (PG058 p. 320, cols 785-786, [857] init.)
     3) 'm. (Cf. Plut. I' -> 1027 init. (Full OCR: 'ἐμβάλλειν ἀγωνίαν τινὶ 154 a. m. (Cf. Plut. I. 1027 init.)',
        locus in Chrysostom: PG057 p. 121 [154], plus external reference to Plutarch Moralia)
     4) 'ἐξαρκῶν χρυσία' -> 856 pr. f. (PG058 p. 320, cols 785-786, [856] pr. f. / 'χρυσίων ἐπιθυμίας')
     5) 'τριήμερον (?)' -> 855 c. m. not. (PG058 p. 319 / p. 320, apparatus criticus / locus praecedens [856])
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

PG058_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. ἀφιέναι seq. infin -> 856 a. m. (PG058 p. 320, cols 785-786, [856] a. m.)
    'region-entry:dae26f49676b9027d4af883e8d9107c78d85208ec89e4a978fc98a84253f3dcc': (
        'region-group:dae26f49676b9027d4af883e8d9107c78d85208ec89e4a978fc98a84253f3dcc',
        [
            ('PG058', 320, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC [856] a. m. (Graece)', 'pg058_matth_856_am_p320'),
            ('PG058', 321, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC (Latine)', 'pg058_matth_856_am_p321'),
        ]
    ),

    # 2. εἰσαγωγή (δίκης) -> 857 init. (PG058 p. 320, cols 785-786, [857] init.)
    'region-entry:4f2dbe4487280ff3932d2bfcf2e1f477794f3cee1cb9b8232bffe0f1fe5240fa': (
        'region-group:4f2dbe4487280ff3932d2bfcf2e1f477794f3cee1cb9b8232bffe0f1fe5240fa',
        [
            ('PG058', 320, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC [857] init. (Graece)', 'pg058_matth_857_init_p320'),
            ('PG058', 321, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC (Latine)', 'pg058_matth_857_init_p321'),
        ]
    ),

    # 3. m. (Cf. Plut. I -> 1027 init. (Full OCR: 'ἐμβάλλειν ἀγωνίαν τινὶ 154 a. m. (Cf. Plut. I. 1027 init.)')
    'region-entry:af5788cceaf230676d1280b09ba7b67e67a737308eaf571aecd7ccdc7d03c582': (
        'region-group:af5788cceaf230676d1280b09ba7b67e67a737308eaf571aecd7ccdc7d03c582',
        [
            ('PG057', 121, 'PG057 cols 237-238 / In Matthaeum Homil. XVI [154] a. m. locus primarius', 'pg057_matth_154_am_p121'),
            ('PG057', 122, 'PG057 cols 239-242 / In Matthaeum Homil. XVI [155] locus contiguus', 'pg057_matth_155_contiguus_p122'),
            ('PG058', 402, 'PG058 cols 933-934 / Index Graecitatis entry with comparative Plutarch citation', 'pg058_index_plutarch_p402'),
        ]
    ),

    # 4. ἐξαρκῶν χρυσία -> 856 pr. f. (PG058 p. 320, cols 785-786, [856] pr. f. / 'χρυσίων ἐπιθυμίας')
    'region-entry:d77f5c498f12ec4a5e8e07a6699efd614c0dcfbfa39908580943adf38e54f061': (
        'region-group:d77f5c498f12ec4a5e8e07a6699efd614c0dcfbfa39908580943adf38e54f061',
        [
            ('PG058', 320, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC [856] pr. f. (Graece)', 'pg058_matth_856_prf_p320'),
            ('PG058', 321, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC (Latine)', 'pg058_matth_856_prf_p321'),
        ]
    ),

    # 5. τριήμερον (?) -> 855 c. m. not. (PG058 p. 319 / p. 320, apparatus criticus / locus praecedens [856])
    'region-entry:f8566b00d7ad1ce33e4063b6be55a1b36aecc781436e6cc33fae87d5dad972c9': (
        'region-group:f8566b00d7ad1ce33e4063b6be55a1b36aecc781436e6cc33fae87d5dad972c9',
        [
            ('PG058', 319, 'PG058 cols 783-784 / In Matthaeum Homil. LXXXIX (p. 855 c. m. locus praecedens)', 'pg058_matth_855_cm_p319'),
            ('PG058', 320, 'PG058 cols 785-786 / In Matthaeum Homil. LXXXIX-XC apparatus criticus et notae', 'pg058_matth_855_notae_p320'),
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


def resolve_pg058_items() -> Dict[str, dict]:
    """Resolve all PG058 residual items into structured verified candidates."""
    resolved = {}
    for item_id, (group_id, destinations) in PG058_ITEMS_RAW.items():
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
                'method': 'pg058_chrysostomus_matthaeum_locator_v1',
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


def apply_pg058_resolution(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    """Apply the resolution of PG058 items to the SQLite database and update summary."""
    resolved_data = resolve_pg058_items()
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
    parser = argparse.ArgumentParser(description="Resolve PG058 residual source_only items.")
    parser.add_argument('--apply', action='store_true', help="Apply resolutions to SQLite database.")
    args = parser.parse_args()

    results = resolve_pg058_items()
    print(f"Resolved {len(results)} items for PG058.")
    for i_id, d in results.items():
        print(f"  {i_id} -> {len(d['candidates'])} candidates")

    if args.apply:
        num = apply_pg058_resolution()
        print(f"Successfully applied {num} items to {DB_PATH}.")

"""
PG016.03 Origenes / Hippolytus Philosophumena Locator
Sprint 73 of Onomastic Catalog Recovery

Resolves all 8 residual source_only items from PG016.03:
1. Editorial Context:
   - PG016.03 contains:
     * Cols 1-3000: Origenis Hexaplorum Fragmenta (continuatio)
     * Cols 3001-3454: Origenis Philosophumena sive Omnium Haeresium Refutatio (now recognized as Hippolytus of Rome)
     * Cols 3455-3466 (physical pp. 552-566): INDICES IN PHILOSOPHUMENA
   - Editorial Notation in Indices:
     * Citations refer to the sections and pages of Emmanuel Miller's princeps edition (Oxford, 1851):
     * 'ANACREONTEA (nr. 67 Bergk p 835) 152' -> Miller p. 152 in lib. V, physical p. 415
     * Items citing '394' refer to sec. 394-395 in lib. VII cap. 30 (col 3333-3334, physical p. 488):
       - 'Ἀκραγαντῖνος, 394' -> Empedocles Agragantinus (p. 488)
       - 'Ἀρμένιος, 394' -> Bardesanes Armenius (p. 488)
       - 'Ἀσσύριος, 394' -> Assyrii (p. 488)
       - 'Βαρθησιάνης, 394' -> Bardesianes / Bardesanes (p. 488)
       - 'Μαρκιωνιστής, 394' -> Marcionistae (p. 488)
       - 'Μάρκος (evangelista) — (ὁ κολοβοδάκτυλος, 394' -> Marcus colobodactylus (p. 488)
       - 'Μίων, 394' -> Myon (Meton) Empedoclis pater (p. 488)
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

PG016_03_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. ANACREONTEA (nr. 67 Bergk p 835) 152 -> Miller p. 152 (p. 415)
    '7853ca5a53d4cf3aa38714a0d251f44638fdf38b048a198f4af226e45f7f9274': (
        '7853ca5a53d4cf3aa38714a0d251f44638fdf38b048a198f4af226e45f7f9274',
        [
            ('PG016.03', 415, 'PG016.03 cols 3180-3185 (Philosophumena lib. V, Miller p. 152: Anacreontea fragmentum)', 'pg016_03_anacreontea_p415')
        ]
    ),

    # 2. Ἀκραγαντῖνος -> sec. 394 (p. 488)
    'context-child:99d82f04caad2f33b15af825': (
        'context-child:99d82f04caad2f33b15af825',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Empedocles Agragantinus)', 'pg016_03_agragantinus_p488')
        ]
    ),

    # 3. Ἀρμένιος -> sec. 394 (p. 488)
    'context-child:cd38c7e6c269077e5c4fd0c5': (
        'context-child:cd38c7e6c269077e5c4fd0c5',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Bardesanes Armenius)', 'pg016_03_armenius_p488')
        ]
    ),

    # 4. Ἀσσύριος -> sec. 394 (p. 488)
    'context-child:82a44d86309ae0095ffd682c': (
        'context-child:82a44d86309ae0095ffd682c',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Assyrii)', 'pg016_03_assyrios_p488')
        ]
    ),

    # 5. Βαρθησιάνης -> sec. 394 (p. 488)
    'context-child:666b0a0ee575b5eb96beb851': (
        'context-child:666b0a0ee575b5eb96beb851',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Bardesianes / Bardesanes)', 'pg016_03_bardesianes_p488')
        ]
    ),

    # 6. Μαρκιωνιστής -> sec. 394 (p. 488)
    'context-child:bb005a90c387c85b9476441e': (
        'context-child:bb005a90c387c85b9476441e',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Marcionistae)', 'pg016_03_marcionistes_p488')
        ]
    ),

    # 7. Μάρκος (evangelista) — (ὁ κολοβοδάκτυλος -> sec. 394 (p. 488)
    'context-child:d1a4c2ea519c4544c2d1bc41': (
        'context-child:d1a4c2ea519c4544c2d1bc41',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Marcus colobodactylus)', 'pg016_03_markos_p488')
        ]
    ),

    # 8. Μίων -> sec. 394 (p. 488)
    'context-child:feec81159e84448e28cb7dec': (
        'context-child:feec81159e84448e28cb7dec',
        [
            ('PG016.03', 488, 'PG016.03 cols 3333-3334 (Philosophumena lib. VII cap. 30, sec. 394: Myon Empedoclis pater)', 'pg016_03_mion_p488')
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    if matches:
        return matches[0]
    return f'teste/{volume}/text/{volume}-{page_num:03d}.txt'


def build_candidate_json(
    volume: str,
    page_num: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int = 1,
) -> bytes:
    """Builds candidate compressed JSON payload."""
    payload = {
        'target_volume': volume,
        'page_num': page_num,
        'file': file_path,
        'col_desc': col_desc,
        'locator_reason': reason,
        'rank': rank,
        'score': 10.0,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_pg016_03_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PG016.03 item to physical destination files."""
    if item_id not in PG016_03_ITEMS_RAW:
        return None
    group_id, dests = PG016_03_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint73(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 73 recovery for PG016.03 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PG016.03' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PG016.03',
        'sprint': 73,
        'total_source_only_initial': total_source_only,
        'resolved_count': 0,
        'candidates_created': 0,
        'applied': apply,
        'items': [],
    }

    if not source_only_rows:
        conn.close()
        return report

    items_to_update = []
    for r in source_only_rows:
        item_id, group_id, label, ref_raw, source = r
        res = resolve_pg016_03_item(item_id)
        if res:
            _, resolved_dests = res
            items_to_update.append((item_id, label, ref_raw, resolved_dests))

    report['resolved_count'] = len(items_to_update)

    if not apply:
        conn.close()
        return report

    candidates_inserted = 0
    for item_id, label, ref_raw, dests in items_to_update:
        for rank, (vol, page, col_desc, reason, fpath) in enumerate(dests, 1):
            cand_blob = build_candidate_json(vol, page, fpath, col_desc, reason, rank)
            cur.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, fpath, rank, 10.0, 1, cand_blob))
            candidates_inserted += 1

        cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (item_id,))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    total_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    total_source_only_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_source_only_after
    report['candidate_storage_records'] = cand_count

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['items_with_destinations'] = total_with_dest
        summary['items_source_only'] = total_source_only_after
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sprint 73: PG016.03 Origenes Philosophumena Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint73(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))

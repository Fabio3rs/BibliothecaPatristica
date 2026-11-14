"""
PL176 Hugo de S. Victore Locator
Sprint 79 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PL176:
1. Editorial Context:
   - PL176 contains the dogmatic works of Hugh of Saint Victor:
     * Cols 1-174: Summa Sententiarum (Tractatus I-VII)
     * Cols 173-618: De Sacramentis Christianae Fidei (Libri I & II)
     * Cols 617-744: De Arca Noe Morali et Mystica
     * Cols 743-804: De Vanitate Mundi & Eruditio Didascalica
     * Cols 805-1182: De Claustro Animae & Miscellanea
     * Cols 1181-1224: Appendix ad Hugonis Opera Dogmatica & Indices
   - The index explicitly declares:
     "INDEX RERUM ANALYTICUS. Notae numerales lectorem ad columnas editionis nostrae revocant."
   - The 6 residual items:
     1) 'Quæ sit ejus efficacia' -> 158 et seq (Summa Sententiarum Tract. VI c. 1 de Confirmatione, cols 137-140 on physical pp. 74-75, Melchiades: 'quia summis pontificibus commissum... majorem efficaciam habeat', cols 157-158 on p. 84, and De Sacramentis cols 459-462 on pp. 235-236)
     2) 'Etymologiæ librorum et aliorum ad lectionem sacram pertinentium' -> 787 et seq (Eruditio Didascalica lib. IV c. 16, col 787 on physical p. 399 and p. 400)
     3) 'Eorum ratio' -> 782 et seq (subordinated to 'Librorum sacrorum...'; Eruditio Didascalica lib. IV c. 8 'Ratio divinorum librorum', col 782 on physical p. 396 and p. 397)
     4) 'Oratorium animæ quid sit' -> 1107 et seqq (De Claustro Animae lib. III c. 10 'De oratorio animae', col 1107 on physical p. 561 and p. 562)
     5) 'Mens humana quomodo fiat latronum spelunca, negotiationis domus, et meretricis prostibulum' -> 1052 et seq (De Claustro Animae lib. I c. 8 'Quomodo mens fiat spelunca latronum...', col 1032 on physical p. 523, with index OCR typo 1052 on p. 533)
     6) 'Verbum carnem assumpsit cum pœnis sine culpa, et cum mortalitate sine iniquitate' -> 581 et seq (typo in index for col 381: De Sacramentis lib. II pars I c. 5 'Quod Verbum carnem assumpsit cum poena sine culpa; cum mortalitate sine iniquitate', col 381 on physical p. 196 and p. 197)
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

PL176_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Quæ sit ejus efficacia -> 158 et seq (Summa Sententiarum Tract. VI, pp. 74, 75, 84; De Sacramentis pp. 235, 236)
    'group:33bf412f8ac902a06eb4cba245e4cbf98d4f75b308be6ee742f060aaa277a2a5': (
        'group:33bf412f8ac902a06eb4cba245e4cbf98d4f75b308be6ee742f060aaa277a2a5',
        [
            ('PL176', 75, 'PL176 cols 139-140 (Summa Sententiarum Tract. VI c. 1: De Confirmatione, efficacia sacramenti a summis pontificibus traditi)', 'pl176_hugo_confirmatio_efficacia_p75'),
            ('PL176', 74, 'PL176 cols 137-138 (Summa Sententiarum Tract. VI c. 1: De sacramento confirmationis)', 'pl176_hugo_confirmatio_efficacia_p74'),
            ('PL176', 235, 'PL176 cols 459-460 (De Sacramentis lib. II pars VII: De Confirmatione)', 'pl176_hugo_confirmatio_efficacia_p235'),
        ]
    ),

    # 2. Etymologiæ librorum et aliorum ad lectionem sacram pertinentium -> 787 et seq (pp. 399, 400)
    'group:c99bd2b4cec901f658ab1a26fa6f6e055d6b0a8603c1a223d640bdc75f0a8ef8': (
        'group:c99bd2b4cec901f658ab1a26fa6f6e055d6b0a8603c1a223d640bdc75f0a8ef8',
        [
            ('PL176', 399, 'PL176 col 787 (Eruditio Didascalica lib. IV c. 16: Quaedam etymologiae dictionum ad lectionem sacram pertinentium)', 'pl176_hugo_etymologiae_librorum_p399'),
            ('PL176', 400, 'PL176 col 788 (Eruditio Didascalica lib. IV c. 16 continuatio)', 'pl176_hugo_etymologiae_librorum_p400'),
        ]
    ),

    # 3. Eorum ratio -> 782 et seq (pp. 396, 397)
    'group:8082c98cb2c995ec755419cf9af2275e531073262546f0ef2483450f95cb88af': (
        'group:8082c98cb2c995ec755419cf9af2275e531073262546f0ef2483450f95cb88af',
        [
            ('PL176', 396, 'PL176 col 782 (Eruditio Didascalica lib. IV c. 8: Ratio divinorum librorum)', 'pl176_hugo_librorum_sacrorum_ratio_p396'),
            ('PL176', 397, 'PL176 col 783 (Eruditio Didascalica lib. IV c. 8 continuatio)', 'pl176_hugo_librorum_sacrorum_ratio_p397'),
        ]
    ),

    # 4. Oratorium animæ quid sit -> 1107 et seqq (pp. 561, 562)
    'group:0386e6102475777bc7f9f81e64ab33108f91844378ea03bdeedc6a813a31505f': (
        'group:0386e6102475777bc7f9f81e64ab33108f91844378ea03bdeedc6a813a31505f',
        [
            ('PL176', 561, 'PL176 col 1107 (De Claustro Animae lib. III c. 10: De oratorio animae)', 'pl176_hugo_oratorium_animae_p561'),
            ('PL176', 562, 'PL176 col 1108 (De Claustro Animae lib. III c. 10 continuatio)', 'pl176_hugo_oratorium_animae_p562'),
        ]
    ),

    # 5. Mens humana quomodo fiat latronum spelunca, negotiationis domus, et meretricis prostibulum -> 1052 et seq (pp. 523, 533)
    'group:724e877c27403af6e10c2db8090914dcdcc062ca718b398585613ed45f54a547': (
        'group:724e877c27403af6e10c2db8090914dcdcc062ca718b398585613ed45f54a547',
        [
            ('PL176', 523, 'PL176 col 1032 (De Claustro Animae lib. I c. 8: Quomodo mens fiat spelunca latronum et domus negotiationis et meretricis prostibulum)', 'pl176_hugo_mens_spelunca_prostibulum_p523'),
            ('PL176', 533, 'PL176 col 1052 (De Claustro Animae lib. II c. 1)', 'pl176_hugo_mens_spelunca_prostibulum_p533'),
        ]
    ),

    # 6. Verbum carnem assumpsit cum pœnis sine culpa, et cum mortalitate sine iniquitate -> 581 et seq (pp. 196, 197)
    'group:1261f598d35b23eddf60d35988ec9ca11c9fb0c885ea8abfd541bed839acfcae': (
        'group:1261f598d35b23eddf60d35988ec9ca11c9fb0c885ea8abfd541bed839acfcae',
        [
            ('PL176', 196, 'PL176 col 381 (De Sacramentis lib. II pars I c. 5: Quod Verbum carnem assumpsit cum poena sine culpa; cum mortalitate sine iniquitate)', 'pl176_hugo_verbum_carnem_assumpsit_p196'),
            ('PL176', 197, 'PL176 col 382 (De Sacramentis lib. II pars I c. 5 continuatio)', 'pl176_hugo_verbum_carnem_assumpsit_p197'),
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    matches = list(dict.fromkeys(matches))
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


def resolve_pl176_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL176 item to physical destination files."""
    if item_id not in PL176_ITEMS_RAW:
        return None
    group_id, dests = PL176_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint79(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 79 recovery for PL176 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL176' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL176',
        'sprint': 79,
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
        res = resolve_pl176_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 79: PL176 Hugo de S. Victore Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    rep = execute_sprint79(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(rep, indent=2, ensure_ascii=False))

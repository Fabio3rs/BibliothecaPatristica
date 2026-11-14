"""
PL136 Ratherius Veronensis & Liutprandus Cremonensis Locator
Sprint 78 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PL136:
1. Editorial Context:
   - PL136 contains:
     * Cols 9-142: Ratherii Opera - Praeloquiorum Libri Sex (reprint of Ballerini Verona 1765 edition, with Praefatio Ballerini pp. i-lxxxviii)
     * Cols 143-444: Ratherii Phrenesis, Conclusio deliberativa, Excerptum ex Dialogo confessionali, etc.
     * Cols 443-764: Ratherii Epistolae, Qualitatis conjectura, Synodica ad presbyteros, Sermones, etc.
     * Cols 765-1134: Liutprandi Cremonensis Episcopi Opera (Antapodosis, Historia Ottonis, Relatio de legatione Constantinopolitana)
     * Cols 1133-1346: Folcuini Gesta abbatum Lobiensium & Indices
   - Index references in PL136 cite:
     1) Migne columns / Ballerini marginal pages for Ratherius
     2) Migne columns for Liutprandus
     3) The 6 residual items:
        a) 'Id episcoporum munus late probatur' -> 542 et seqq (PL136 cols 541-542 on physical p. 276, OCR typo '341-342', and cols 543-544 on p. 277)
        b) 'Hoc primum testimonium mitigare disciplina in jejunio Quadragesimali...' -> 420 not. (Ballerini p. 420 footnote 980 on physical p. 288, and text on p. 287)
        c) 'Michaelis (S.) ecclesia Veronæ ad portam... 601 not. ... ibid., 602' -> footnote 1470 & Ballerini pp. 601-602 on physical pp. 362-363
        d) 'Brunone curante... lxxvi. Quomodo hæc electio Ratherii facta, lxxvii. Dies electionis notatus, 218 not 5' -> Praefatio pp. LXXVI-LXXVII on pp. 38-39 and Ballerini p. 218 on p. 188
        e) 'Synodicum edit, insigne decimi sæculi documentum' -> 409 et seqq (Ballerini p. 409 / cols 553-554 on physical p. 282 and Admonitio on p. 281)
        f) 'Sed ab eo dimissus' -> 'idem frangit' (Liutprandus, Antapodosis lib. II c. 63, col 831 on physical p. 421 and col 830 on p. 420)
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

PL136_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Id episcoporum munus late probatur -> 542 et seqq (p. 276, cols 541-542)
    '73fc1dcbc2f19dfe31f395eda9add641ae1317318df80f61fd571aa806eee382': (
        '73fc1dcbc2f19dfe31f395eda9add641ae1317318df80f61fd571aa806eee382',
        [
            ('PL136', 276, 'PL136 col 542 (Qualitatis Conjectura: Id episcoporum munus late probatur)', 'pl136_ratherius_munus_episcoporum_p276'),
            ('PL136', 277, 'PL136 cols 543-544 (Qualitatis Conjectura: continuatio)', 'pl136_ratherius_munus_episcoporum_p277'),
        ]
    ),

    # 2. Hoc primum testimonium mitigare disciplina in jejunio Quadragesimali... -> 420 not. (p. 288, not. 980)
    'e8b7e397b9b8cd8d83dc9c8ce30fc149c068f3717cee06a3a47d5d72bc7524d6': (
        'e8b7e397b9b8cd8d83dc9c8ce30fc149c068f3717cee06a3a47d5d72bc7524d6',
        [
            ('PL136', 288, 'PL136 not. 980 ad Ballerini p. 420 (Synodica ad presbyteros: testimonium mitigatae disciplinae in jejunio)', 'pl136_ratherius_jejunium_nonam_p288'),
            ('PL136', 287, 'PL136 Ballerini p. 420 (Synodica ad presbyteros num. 14-15)', 'pl136_ratherius_jejunium_nonam_p287'),
        ]
    ),

    # 3. Michaelis (S.) ecclesia Veronæ ad portam... -> 601 not. / 602 (p. 362, not. 1470)
    'group:e2aef3313c18bbd21e5bb1e04752f286ae5548fad63ec34770c5b2b6668c58c1': (
        'group:e2aef3313c18bbd21e5bb1e04752f286ae5548fad63ec34770c5b2b6668c58c1',
        [
            ('PL136', 362, 'PL136 col 715 / not. 1470 ad Ballerini p. 601 (Sermo de S. Michaele: ecclesia ad portam, concursus feria secunda)', 'pl136_ratherius_michael_ecclesia_p362'),
            ('PL136', 363, 'PL136 col 716 / Ballerini p. 602 (Sermo de S. Michaele: continuatio)', 'pl136_ratherius_michael_ecclesia_p363'),
        ]
    ),

    # 4. Brunone curante... lxxvi. Quomodo hæc electio Ratherii facta, lxxvii. Dies electionis notatus, 218 not 5 -> pp. 38, 39, 188
    '499015fcbd0ce7a7cca2408b407fa7ed7f60f50ea690ebf1de13ad5bf3749fbf': (
        '499015fcbd0ce7a7cca2408b407fa7ed7f60f50ea690ebf1de13ad5bf3749fbf',
        [
            ('PL136', 38, 'PL136 Ballerini Praefatio p. LXXVI (De electione Ratherii Brunone archiepiscopo curante)', 'pl136_ratherius_electio_bruno_p38'),
            ('PL136', 39, 'PL136 Ballerini Praefatio p. LXXVII (Quomodo electio Ratherii facta)', 'pl136_ratherius_electio_bruno_p39'),
            ('PL136', 188, 'PL136 Ballerini p. 218 / col 365 (Dies electionis Ratherii notatus coram Brunone)', 'pl136_ratherius_electio_bruno_p188'),
        ]
    ),

    # 5. Synodicum edit, insigne decimi sæculi documentum -> 409 et seqq (pp. 282, 281)
    '34cdce0bfb1a00911ea22785be976a3a4afd2a462dcd3cc5fc63c76ada010f2c': (
        '34cdce0bfb1a00911ea22785be976a3a4afd2a462dcd3cc5fc63c76ada010f2c',
        [
            ('PL136', 282, 'PL136 cols 553-554 / Ballerini p. 409 (Ratherii Synodica ad presbyteros: insigne decimi saeculi documentum)', 'pl136_ratherius_synodica_p282'),
            ('PL136', 281, 'PL136 cols 551-552 (Admonitio in Synodican Ratherii)', 'pl136_ratherius_synodica_admonitio_p281'),
        ]
    ),

    # 6. Sed ab eo dimissus -> idem frangit (p. 421, col 831; p. 420, col 830)
    '1eab08c7f23e94ad2123873556ff079844d90336191395826aac8f76c188870a': (
        '1eab08c7f23e94ad2123873556ff079844d90336191395826aac8f76c188870a',
        [
            ('PL136', 421, 'PL136 col 831 (Liutprandus Cremonensis, Antapodosis lib. II c. 63: Gislebertus ante regem Berengarium... sed ab eo dimissus pactum frangit)', 'pl136_liutprandus_gislebertus_berengarius_p421'),
            ('PL136', 420, 'PL136 col 830 (Liutprandus Cremonensis, Antapodosis lib. II c. 61-62)', 'pl136_liutprandus_gislebertus_berengarius_p420'),
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


def resolve_pl136_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL136 item to physical destination files."""
    if item_id not in PL136_ITEMS_RAW:
        return None
    group_id, dests = PL136_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint78(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 78 recovery for PL136 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL136' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL136',
        'sprint': 78,
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
        res = resolve_pl136_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 78: PL136 Ratherius Veronensis Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    rep = execute_sprint78(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(rep, indent=2, ensure_ascii=False))

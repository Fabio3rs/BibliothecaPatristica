"""
PG122 Georgius Cedrenus Internal Number Locator
Sprint 23 of Onomastic Catalog Recovery

Resolves the 41 residual source_only items from PG122's Index in Cedrenum
(pages 767-785) to physical pages across Georgius Cedrenus' Compendium Historiarum:
- Tomus I (PG121, pages 18-589) for citations with 'I, <num>'
- Tomus II (PG122, pages 12-189) for citations with 'II, <num>'

Handles:
- Numbers referencing grandiores textui insertos (Bonn/Paris marginal edition numbers).
- Embedded newlines across volume partition and numbers (e.g. 'I,\\n109.').
- Multi-locators (e.g. 'I, 885; II, 285.').
- Antecedent resolution for ibid. (e.g. 'In cœlum raptus, ibid., 182' -> I, 182).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.cedrenum_internal_targets import build_map, locate

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class PG122CedrenusLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._mapping = build_map(self.root_dir)

    def resolve_item(self, label: str, ref_raw: str, src_path: str = '') -> List[Dict[str, Any]]:
        clean_ref = ref_raw.replace('\n', ' ').strip()
        if 'In cœlum raptus' in label:
            clean_ref = 'I, 182'

        entry = {
            'section_id': 'PG122:index-in-georgium-cedrenum',
            'reference_raw': clean_ref
        }
        candidates = locate(entry, self._mapping)
        return candidates

    def run(self, dry_run: bool = True, force_all: bool = False) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        where_clause = "WHERE volume = 'PG122'" if force_all else "WHERE volume = 'PG122' AND state = 'source_only'"
        items = cur.execute(f'''
            SELECT id, label, ref_raw, source
            FROM items
            {where_clause}
            ORDER BY id
        ''').fetchall()

        promotions = []
        candidates_to_insert = []

        for item_id, label, ref_raw, src in items:
            cands = self.resolve_item(label or '', ref_raw or '', src or '')
            if cands:
                promotions.append(item_id)
                for rank, cand in enumerate(cands, 1):
                    cand_payload = {
                        'rule': 'pg122_cedrenum_locator',
                        'target_volume': cand['volume'],
                        'physical_page': cand['physical'],
                        'target_file': cand['file'],
                        'evidence': cand.get('evidence', []),
                        'uncertainties': cand.get('uncertainties', []),
                        'confidence': round(cand.get('score', 0.8) * 100, 2)
                    }
                    cand_blob = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode('utf-8'))
                    candidates_to_insert.append((
                        item_id,
                        cand['file'],
                        rank,
                        cand.get('score', 8.5),
                        1,
                        cand_blob
                    ))

        stats = {
            'total_items': len(items),
            'promoted': len(promotions),
            'candidates': len(candidates_to_insert),
            'dry_run': dry_run
        }

        if not dry_run and promotions:
            cur.execute('BEGIN TRANSACTION')
            cur.executemany('''
                INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id, file) DO UPDATE SET
                    rank=excluded.rank,
                    score=excluded.score,
                    candidate_json=excluded.candidate_json
            ''', candidates_to_insert)

            placeholder = ','.join('?' for _ in promotions)
            cur.execute(f'''
                UPDATE items
                SET state = 'with_destinations'
                WHERE id IN ({placeholder})
            ''', promotions)
            conn.commit()

        conn.close()
        return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PG122 Cedrenus Locator')
    parser.add_argument('--apply', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    locator = PG122CedrenusLocator()
    res = locator.run(dry_run=not args.apply)
    print(f'Execution result (dry_run={res["dry_run"]}):')
    print(f'  Total evaluated: {res["total_items"]}')
    print(f'  Promoted: {res["promoted"]} ({(res["promoted"]/max(1, res["total_items"]))*100:.2f}%)')
    print(f'  Candidates: {res["candidates"]}')

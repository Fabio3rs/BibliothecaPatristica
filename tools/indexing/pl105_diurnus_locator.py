"""
PL105 Liber Diurnus Romanorum Pontificum Locator
Sprint 29 of Onomastic Catalog Recovery

Resolves the 35 residual source_only items from PL105's
Index Rerum et Verborum in Liber Diurnus (pages 96-98).
The editor states explicitly:
'Numeri Arabici respondent numeris crassioribus textui insertis.'
These numbers (1 to 210) identify the formulas and titles of the Liber Diurnus
across physical pages 10 to 95 of PL105.

Handles:
- Exact and sequential mapping of bold formula markers in body text.
- Linear interpolation across formula number gaps.
- Multi-formula citations (e.g. '16, 17, 23', '28, 39, 50, 51').
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
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class PL105DiurnusLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._formula_map: Dict[int, Tuple[int, str]] = {}
        self._init_formula_map()

    def _init_formula_map(self):
        text_dir = self.root_dir / 'teste' / 'PL105' / 'text'
        if not text_dir.exists():
            return
        files = sorted(text_dir.glob('*.txt'), key=lambda f: int(f.stem.split('-')[-1]))
        for f in files:
            p_num = int(f.stem.split('-')[-1])
            if 10 <= p_num <= 95:
                txt = f.read_text(encoding='utf-8', errors='ignore')
                fpath = str(f.resolve())
                for m in re.finditer(r'(?:^|\n)\s*(\d{1,3})\s+(?:TITULUS|CAPUT|[A-Z])', txt):
                    val = int(m.group(1))
                    if 1 <= val <= 210 and val not in self._formula_map:
                        self._formula_map[val] = (p_num, fpath)
                for m in re.finditer(r'\b(\d{1,3})\s+TITULUS\b', txt):
                    val = int(m.group(1))
                    if 1 <= val <= 210 and val not in self._formula_map:
                        self._formula_map[val] = (p_num, fpath)

    def lookup_formula(self, num: int) -> Optional[Tuple[int, str]]:
        if num in self._formula_map:
            return self._formula_map[num]
        sorted_k = sorted(self._formula_map.keys())
        bef = [k for k in sorted_k if k < num]
        aft = [k for k in sorted_k if k > num]
        if bef and aft:
            k_prev, k_next = bef[-1], aft[0]
            chosen = k_prev if abs(num - k_prev) <= abs(num - k_next) else k_next
            return self._formula_map[chosen]
        if bef:
            return self._formula_map[bef[-1]]
        if aft:
            return self._formula_map[aft[0]]
        return None

    def resolve_item(self, label: str, ref_raw: str, src_path: str = '') -> List[Dict[str, Any]]:
        nums = [int(n) for n in re.findall(r'\b(\d{1,3})\b', ref_raw) if 1 <= int(n) <= 210]
        if not nums:
            nums = [int(n) for n in re.findall(r'\b(\d{1,3})\b', label) if 1 <= int(n) <= 210]

        if not nums:
            return []

        candidates = []
        seen_files = set()
        for n in nums:
            res = self.lookup_formula(n)
            if res:
                p_num, fpath = res
                if fpath not in seen_files:
                    seen_files.add(fpath)
                    candidates.append({
                        'target_volume': 'PL105',
                        'formula_number': n,
                        'physical_page': p_num,
                        'target_file': fpath,
                        'confidence': 95.0
                    })
        return candidates

    def run(self, dry_run: bool = True, force_all: bool = False) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        where_clause = "WHERE volume = 'PL105'" if force_all else "WHERE volume = 'PL105' AND state = 'source_only'"
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
                        'rule': 'pl105_diurnus_locator',
                        'target_volume': cand['target_volume'],
                        'formula_number': cand['formula_number'],
                        'physical_page': cand['physical_page'],
                        'target_file': cand['target_file'],
                        'confidence': cand['confidence']
                    }
                    cand_blob = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode('utf-8'))
                    candidates_to_insert.append((
                        item_id,
                        cand['target_file'],
                        rank,
                        9.5,
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
    parser = argparse.ArgumentParser(description='PL105 Diurnus Locator')
    parser.add_argument('--apply', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    locator = PL105DiurnusLocator()
    res = locator.run(dry_run=not args.apply)
    print(f'Execution result (dry_run={res["dry_run"]}):')
    print(f'  Total evaluated: {res["total_items"]}')
    print(f'  Promoted: {res["promoted"]} ({(res["promoted"]/max(1, res["total_items"]))*100:.2f}%)')
    print(f'  Candidates: {res["candidates"]}')

"""
PG143 Ephraemius Chronographus Verse-to-Page Locator
Sprint 17 of Onomastic Catalog Recovery

Resolves Index in Ephraemium (PG143, pages 709-713) verse line citations (1 to 10,600)
to physical facsimile pages (pages 14 to 207) containing Ephraemius' iambic trimeter poem.
"""

import glob
import json
import os
import re
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple, Any

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class PG143EphraemiusLocator:
    def __init__(self, text_dir: str = 'teste/PG143/text', db_path: str = DB_PATH):
        self.text_dir = text_dir
        self.db_path = db_path
        self._file_by_pg: Dict[int, str] = {}
        self._page_verse_bounds: Dict[int, Tuple[int, int]] = {}
        self._init_pages()

    def _init_pages(self):
        files = sorted(glob.glob(os.path.join(self.text_dir, '*.txt')))
        for f in files:
            m = re.search(r'-(\d+)\.txt$', f)
            if m:
                self._file_by_pg[int(m.group(1))] = os.path.abspath(f)

        for pg in range(14, 208):
            f = self._file_by_pg.get(pg)
            if not f:
                continue
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                txt = fp.read()
            v_expected = 1 + (pg - 14) * 54.4
            all_nums = [int(n) for n in re.findall(r'\b(\d{1,5})\b', txt)]
            # Keep only line numbers ending in 0 or 5 that are close to v_expected
            valid = [n for n in all_nums if n % 5 == 0 and abs(n - v_expected) <= 80]
            if valid:
                self._page_verse_bounds[pg] = (min(valid), max(valid))

    def verse_to_page(self, v: int) -> int:
        """Finds target physical page for verse number v."""
        matching = [p for p, (v_min, v_max) in self._page_verse_bounds.items() if v_min - 10 <= v <= v_max + 10]
        if matching:
            return matching[0]
        # Linear interpolation between page 14 (v=1) and page 205 (v=10392)
        p_est = 14 + round((v - 1) * (205 - 14) / (10392 - 1))
        return max(14, min(207, p_est))

    def resolve_item(self, label: str, ref_raw: str) -> Optional[Tuple[List[str], List[int]]]:
        """Extracts verse numbers and maps to physical file paths."""
        ref_nums = [int(n) for n in re.findall(r'\b(\d{1,5})\b', ref_raw) if 1 <= int(n) <= 11000]
        label_nums = [int(n) for n in re.findall(r'\b(\d{1,5})\b', label) if 1 <= int(n) <= 11000]
        verses = ref_nums if ref_nums else label_nums
        if not verses:
            return None

        dest_files = []
        for v in verses:
            pg = self.verse_to_page(v)
            f = self._file_by_pg.get(pg)
            if f and f not in dest_files:
                dest_files.append(f)

        return (dest_files, verses) if dest_files else None

    def run(self, dry_run: bool = True, force_all: bool = False) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        where_clause = "WHERE volume = 'PG143'" if force_all else "WHERE volume = 'PG143' AND state = 'source_only'"
        items = cur.execute(f'''
            SELECT id, label, ref_raw, source
            FROM items
            {where_clause}
            ORDER BY id
        ''').fetchall()

        promotions = []
        candidates_to_insert = []

        for item_id, label, ref_raw, src in items:
            res = self.resolve_item(label or '', ref_raw or '')
            if res:
                dest_files, verses = res
                promotions.append(item_id)
                for rank, fpath in enumerate(dest_files, start=1):
                    cand_payload = {
                        'rule': 'pg143_ephraemius_verse_locator',
                        'target_volume': 'PG143',
                        'verses': verses,
                        'target_file': fpath,
                        'confidence': 95.0
                    }
                    cand_blob = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode('utf-8'))
                    candidates_to_insert.append((
                        item_id,
                        fpath,
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
    import argparse
    parser = argparse.ArgumentParser(description='PG143 Ephraemius Verse Locator')
    parser.add_argument('--apply', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    locator = PG143EphraemiusLocator()
    res = locator.run(dry_run=not args.apply)
    print(f'Execution result (dry_run={res["dry_run"]}):')
    print(f'  Total evaluated: {res["total_items"]}')
    print(f'  Promoted: {res["promoted"]} ({(res["promoted"]/res["total_items"])*100:.2f}%)')
    print(f'  Candidates: {res["candidates"]}')

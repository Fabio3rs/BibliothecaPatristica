#!/usr/bin/env python3
"""Select additive experimental links, preserving existing catalog evidence."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly


def source_path(value):
    return str((Path(__file__).resolve().parents[1]/value).resolve())


def select(catalog, overlay, out, reviews=None):
    catalog, overlay, out = map(lambda p: Path(p).resolve(), (catalog, overlay, out))
    if out.exists():
        raise FileExistsError(out)
    assertions = json.loads(Path(reviews).read_text())['exclusions'] if reviews else []
    excluded = {(r['item_id'], source_path(r['file'])): r for r in assertions}
    selected, selected_ids, existing, rejected = [], set(), 0, []
    with closing(readonly(catalog)) as db, closing(readonly(overlay)) as src:
        summary = json.loads(src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
        if Path(summary['catalog']).resolve() != catalog:
            raise ValueError('Overlay belongs to another catalog')
        for row in src.execute('SELECT * FROM candidates ORDER BY item_id,file'):
            item = db.execute('SELECT source,ref_raw FROM items WHERE id=?', (row['item_id'],)).fetchone()
            if item is None:
                raise ValueError('Unknown item: '+row['item_id'])
            key = (row['item_id'], row['file'])
            assertion = excluded.get((key[0], source_path(key[1])))
            if assertion:
                if source_path(item['source']) != source_path(assertion['source']) or item['ref_raw'] != assertion['reference_raw']:
                    raise ValueError('Review no longer matches source occurrence: '+row['item_id'])
                rejected.append(assertion)
                continue
            if db.execute('SELECT 1 FROM candidates WHERE item_id=? AND file=?', key).fetchone():
                existing += 1
                continue
            selected.append(tuple(row[k] for k in ('item_id','file','rank','score','navigable','candidate_json')))
            selected_ids.add(row['item_id'])
        newly_located = sum(not db.execute('SELECT 1 FROM candidates WHERE item_id=? AND navigable=1', (ident,)).fetchone()
                            for ident in {r[0] for r in selected if r[4]})
        result = dict(catalog=str(catalog), source_overlay=str(overlay), links_selected=len(selected),
                      items_affected=len(selected_ids), newly_located_items=newly_located,
                      existing_links_preserved=existing, reviewed_exclusions=rejected,
                      unused_reviews=[r for r in assertions if r not in rejected],
                      logical_sha256=hashlib.sha256(json.dumps(selected, ensure_ascii=False).encode()).hexdigest())
        out.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(out)) as dest:
            dest.execute('PRAGMA journal_mode=WAL')
            dest.executescript('''
                CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
                CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            ''')
            dest.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?)', selected)
            for ident in sorted(selected_ids):
                row = src.execute('SELECT * FROM relations WHERE item_id=?', (ident,)).fetchone()
                if row:
                    dest.execute('INSERT INTO relations VALUES(?,?,?,?,?)', tuple(row))
            dest.execute('INSERT INTO metadata VALUES(?,?)', ('summary', json.dumps(result, ensure_ascii=False)))
            dest.commit()
    out.with_suffix('.summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--overlay', required=True, type=Path)
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(select(args.catalog, args.overlay, args.out, args.reviews), ensure_ascii=False, indent=2))

"""Select newly covered physical sources from a full contextual extraction.

Always parse the complete reviewed section first: selecting only missing pages
before parsing would lose preceding lemmas and ibid context. Existing physical
sources are kept in the full extraction for reconciliation, never overwritten or
duplicated by this additive supplement. No canonical writes or inference.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from tools.corpus_utils import page_number
from tools.indexing.candidate_search import normalize
from tools.indexing.entry_parser_bulk import readonly


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def select(extraction, catalog, out):
    if extraction.resolve() == catalog.resolve():
        raise ValueError('Extraction must be separate from catalog')
    out.mkdir(parents=True, exist_ok=False)
    with closing(readonly(catalog)) as current, closing(readonly(extraction)) as src:
        sources = {r['source']: r['volume'] for r in current.execute('SELECT DISTINCT volume,source FROM items')}
        physical = {(v, page_number(Path(s))) for s, v in sources.items() if page_number(Path(s)) is not None}
        catalog_ids = {r[0] for r in current.execute('SELECT id FROM items')}
        rows = list(src.execute('SELECT * FROM items ORDER BY id'))
        decision, by_source = {}, {}
        for row in rows:
            source = row['source']; key = (row['volume'], page_number(Path(source)))
            if not Path(source).is_file():
                raise ValueError('Missing current extraction source: ' + source)
            if source not in decision:
                if source in sources:
                    reason = 'existing_source_deferred_for_reconciliation'
                elif key[1] is None:
                    reason = 'unknown_physical_identity_deferred'
                elif key in physical:
                    reason = 'existing_physical_page_under_other_path_deferred'
                else:
                    reason = 'new_physical_source'
                decision[source] = reason
                by_source[source] = dict(source=source, volume=row['volume'], physical=key[1],
                                         decision=reason, entries=0)
            by_source[source]['entries'] += 1
        counts, by_volume, logical = Counter(), {}, hashlib.sha256()
        with closing(sqlite3.connect(out / 'search.sqlite')) as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
                CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
                CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
                CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');''')
            for row in rows:
                reason = decision[row['source']]
                counts[reason] += 1
                vc = by_volume.setdefault(row['volume'], Counter()); vc[reason] += 1
                if reason != 'new_physical_source':
                    continue
                if row['id'] in catalog_ids:
                    raise ValueError('New source collides with existing item ID: ' + row['id'])
                meta = json.loads(row['metadata_json'])
                if meta.get('source') != row['source']:
                    raise ValueError('Metadata source mismatch')
                cur = db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', tuple(row[k] for k in
                    ('id','group_id','volume','label','ref_raw','source','state','metadata_json')))
                db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',
                           (cur.lastrowid, normalize(row['label']), normalize(row['ref_raw']), row['volume']))
                logical.update(row['metadata_json'].encode())
            summary = dict(version='missing-page-supplement-v1', catalog=str(catalog.resolve()),
                extraction=str(extraction.resolve()), counts=dict(counts),
                by_volume={v: dict(c) for v,c in sorted(by_volume.items())},
                source_decisions=dict(Counter(decision.values())), logical_sha256=logical.hexdigest(),
                code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                canonical_updates=0, inference_calls=0,
                quality='Literal extraction candidates from previously absent physical pages; not certified complete/correct entries.')
            db.execute('INSERT INTO metadata VALUES(?,?)', ('summary',encode(summary))); db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    (out / 'source_decisions.json').write_text(json.dumps(list(by_source.values()), ensure_ascii=False, indent=2) + '\n')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--extraction', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    print(encode(select(args.extraction, args.catalog, args.out)))

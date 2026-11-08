#!/usr/bin/env python3
"""Write a standalone candidate overlay; every input database stays readonly."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.onomastic_numeric_recovery import VERSION, read_pages, recover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--profile', type=Path, default=Path('docs/fixtures/index_rebuild/po015_onomastic_numeric.json'))
    parser.add_argument('--root', type=Path, default=Path('teste'))
    parser.add_argument('--items-manifest', type=Path, help='Replay preserved input rows even after links were integrated')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    pages = read_pages(args.root, profile)
    with closing(readonly(args.catalog)) as db:
        if args.items_manifest:
            rows = [json.loads(line) for line in args.items_manifest.read_text().splitlines() if line.strip()]
            for row in rows:
                current = db.execute('SELECT id,source,label,ref_raw FROM items WHERE id=?',(row['id'],)).fetchone()
                if not current or any(current[key]!=row[key] for key in ('source','label','ref_raw')):
                    raise ValueError('Manifest occurrence no longer matches catalog: '+row['id'])
        else:
            rows = [dict(r) for r in db.execute('''SELECT i.* FROM items i WHERE volume=? AND id LIKE ?
            AND NOT EXISTS(SELECT 1 FROM candidates c WHERE c.item_id=i.id AND c.navigable=1) ORDER BY i.id''',
            (profile['volume'],profile['item_prefix']+'%'))]
    lo,hi=profile['index_physical_scope']
    rows=sorted([r for r in rows if r['volume']==profile['volume'] and r['id'].startswith(profile['item_prefix'])
        and lo<=int(Path(r['source']).stem.rsplit('-',1)[-1])<=hi],key=lambda r:r['id'])
    results=[recover(row,profile,pages) for row in rows]
    payload=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in results)
    summary=dict(version=VERSION,catalog=str(args.catalog.resolve()),profile=profile,
        source_files_read=len(pages), examined=len(rows), with_candidates=sum(bool(r['candidates']) for r in results),
        pending=sum(not r['candidates'] for r in results), links=sum(len(r['candidates']) for r in results),
        causes=dict(Counter(r['reason'] for r in results)),logical_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        canonical_write_allowed=False)
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'results.jsonl').write_text(payload)
    (args.out/'input_rows.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows))
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (args.out/'source_inventory.json').write_text(json.dumps(list(pages.values()),ensure_ascii=False,indent=2)+'\n')
    with closing(sqlite3.connect(args.out/'relations.sqlite')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));''')
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps(summary)))
        for result in results:
            db.execute('INSERT INTO relations VALUES(?,?,?,?,?)',(result['item_id'],result['group_id'],result['volume'],
                'candidate_files' if result['candidates'] else 'no_candidate_files',json.dumps(result,ensure_ascii=False)))
            for c in result['candidates']:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',(result['item_id'],c['file'],c['rank'],c['score'],1,json.dumps(c,ensure_ascii=False)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

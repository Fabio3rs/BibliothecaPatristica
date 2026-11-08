"""Materialize the experimental search view, retaining every candidate and source."""
import argparse
import csv
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.candidate_search import normalize
from tools.indexing.experiment_storage import register_json_storage, pack_json


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    register_json_storage(db)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=500')
    db.execute('PRAGMA temp_store=MEMORY')
    return db


def stats(db):
    total = db.execute('SELECT count(*) FROM items').fetchone()[0]
    linked = db.execute('SELECT count(DISTINCT item_id) FROM candidates WHERE navigable=1').fetchone()[0]
    return dict(items=total, with_destinations=linked, pending=total-linked,
                candidate_coverage=linked / total if total else 0,
                candidate_links=db.execute('SELECT count(*) FROM candidates').fetchone()[0])


def refresh(db):
    db.execute("UPDATE items SET state='with_destinations' WHERE state!='with_destinations' AND EXISTS(SELECT 1 FROM candidates c WHERE c.item_id=items.id AND navigable=1)")
    db.execute("UPDATE items SET state='source_only' WHERE state!='source_only' AND NOT EXISTS(SELECT 1 FROM candidates c WHERE c.item_id=items.id AND navigable=1)")
    db.commit()
    return stats(db)


def merge_candidates(db, source, origin):
    for n, row in enumerate(source.execute('SELECT * FROM candidates'), 1):
        if not db.execute('SELECT 1 FROM items WHERE id=?', (row['item_id'],)).fetchone():
            raise ValueError('Candidate without item: '+row['item_id'])
        db.execute('INSERT OR IGNORE INTO candidate_origins VALUES(?,?,?)', (row['item_id'], row['file'], origin))
        db.execute('''INSERT INTO candidate_storage VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,file) DO UPDATE SET
            rank=excluded.rank,score=excluded.score,navigable=excluded.navigable,candidate_json=excluded.candidate_json
            WHERE (excluded.navigable,excluded.score) > (candidate_storage.navigable,candidate_storage.score)''', tuple(row[k] for k in ('item_id','file','rank','score','navigable')) + (pack_json(row['candidate_json']),))
        if n % 1000 == 0:
            db.commit()
    db.commit()


def consolidate(base, relations, supplements, out):
    out.mkdir(parents=True, exist_ok=False)
    with closing(connect(out/'search.sqlite')) as db:
        db.executescript('''
            CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
            CREATE TABLE candidate_storage(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json BLOB,PRIMARY KEY(item_id,file)) WITHOUT ROWID;
            CREATE VIEW candidates AS SELECT item_id,file,rank,score,navigable,experiment_json(candidate_json) AS candidate_json FROM candidate_storage;
            CREATE TABLE candidate_origins(item_id TEXT,file TEXT,origin TEXT,PRIMARY KEY(item_id,file,origin)) WITHOUT ROWID;
            CREATE TABLE item_origins(item_id TEXT,origin TEXT,role TEXT,PRIMARY KEY(item_id,origin)) WITHOUT ROWID;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            CREATE TABLE localization_queue(item_id TEXT PRIMARY KEY,reason TEXT);
            CREATE TABLE localization_results(item_id TEXT PRIMARY KEY,evidence_json TEXT);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');
        ''')
        replaced = set()
        for path in [base, *supplements]:
            with closing(readonly(path)) as src:
                replacements = {}
                if path != base:
                    summary = json.loads(src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
                    if Path(summary['catalog']).resolve() != base.resolve():
                        raise ValueError('Supplement belongs to another catalog')
                    if src.execute("SELECT 1 FROM sqlite_master WHERE name='item_replacements'").fetchone():
                        replacements = {r['item_id']:dict(r) for r in src.execute('SELECT * FROM item_replacements')}
                for n, row in enumerate(src.execute('SELECT * FROM items'), 1):
                    old = db.execute('SELECT label,ref_raw,source FROM items WHERE id=?', (row['id'],)).fetchone()
                    if old:
                        r = replacements.get(row['id'])
                        if not r or row['id'] in replaced or tuple(old) != (r['original_label'],r['original_ref'],r['source']):
                            raise ValueError('Conflicting replacement: '+row['id'])
                        replaced.add(row['id'])
                    role = 'replacement' if old else 'base' if path==base else 'addition'
                    db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?)', tuple(row[k] for k in ('id','group_id','volume','label','ref_raw','source','state','metadata_json')))
                    db.execute('INSERT INTO item_origins VALUES(?,?,?)',(row['id'],str(path.resolve()),role))
                    if path != base:
                        db.execute('INSERT INTO localization_queue VALUES(?,?)', (row['id'],role))
                    if n % 1000 == 0:
                        db.commit()
                db.commit()
                merge_candidates(db, src, str(path.resolve()))
            print(encoded({'imported':str(path)}), flush=True)
        for path in relations:
            with closing(readonly(path)) as src:
                summary = json.loads(src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
                if Path(summary['catalog']).resolve() != base.resolve():
                    raise ValueError('Relation overlay belongs to another catalog')
                merge_candidates(db, src, str(path.resolve()))
        refresh(db)
        db.execute("DELETE FROM localization_queue WHERE item_id IN(SELECT id FROM items WHERE state='with_destinations')")
        for n, row in enumerate(db.execute('SELECT rowid,label,ref_raw,volume FROM items'),1):
            db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)', (row[0],normalize(row[1] or ''),normalize(row[2] or ''),row[3]))
            if n % 1000 == 0:
                db.commit()
        summary = {'catalog':str(base.resolve()), 'relations':[str(p.resolve()) for p in relations],
                   'supplements':[str(p.resolve()) for p in supplements], 'baseline':stats(db),
                   'priority_queue':db.execute('SELECT count(*) FROM localization_queue').fetchone()[0],
                   'canonical_write_allowed':False, 'candidate_storage':'zlib-json-v1'}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary',encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        (out/'baseline.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(encoded(summary),flush=True)


def localize(db_path, survey, workers):
    from tools.indexing.repaired_candidate_localization import localize_volume
    with closing(connect(db_path)) as db:
        volumes = {}
        for row in db.execute('SELECT i.* FROM items i JOIN localization_queue q ON q.item_id=i.id LEFT JOIN localization_results r ON r.item_id=i.id WHERE r.item_id IS NULL'):
            volumes.setdefault(row['volume'],[]).append(dict(row))
        before = stats(db)
        counts = Counter()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(localize_volume,items,survey_dir=survey,volume=volume):volume for volume,items in volumes.items()}
            for future in as_completed(futures):
                result = future.result()
                for item in result['results']:
                    counts[item['status']] += 1
                    db.execute('INSERT INTO localization_results VALUES(?,?)',(item['item_id'],encoded(item)))
                    for c in item['candidates']:
                        db.execute('INSERT OR IGNORE INTO candidate_storage VALUES(?,?,?,?,?,?)',(item['item_id'],c['file'],c.get('rank',1),c['score'],c['navigable'],pack_json(encoded(c))))
                        db.execute('INSERT OR IGNORE INTO candidate_origins VALUES(?,?,?)',(item['item_id'],c['file'],'mechanical_relocalization_v1'))
                db.commit()
                print(encoded({'volume':futures[future],'processed':sum(counts.values()),'statuses':dict(counts)}),flush=True)
        after = refresh(db)
        summary = dict(before=before,after=after,statuses=dict(counts))
        previous = db.execute("SELECT json FROM metadata WHERE key='relocalization'").fetchone()
        if previous:
            db.execute('INSERT INTO metadata VALUES(?,?)',
                       ('relocalization_history:'+datetime.now(timezone.utc).isoformat(),previous[0]))
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('relocalization',encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db_path.with_name('relocalization.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(encoded(summary),flush=True)


def add_supplement(db_path, supplement, refresh_unlinked=False):
    """Append new literal occurrences; never replace existing interpretations."""
    origin = str(supplement.resolve())
    if db_path.resolve() == supplement.resolve():
        raise ValueError('Supplement cannot be the destination catalog')
    with closing(connect(db_path)) as db, closing(readonly(supplement)) as src:
        summary = json.loads(src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
        if Path(summary['catalog']).resolve() != db_path.resolve():
            raise ValueError('Supplement belongs to another catalog')
        rows = list(src.execute('SELECT * FROM items'))
        immutable = ('id','group_id','volume','label','ref_raw','source','metadata_json')
        additions, revisions = [], []
        preserved_linked = 0
        for row in rows:
            old = db.execute('SELECT * FROM items WHERE id=?',(row['id'],)).fetchone()
            if old:
                registered = db.execute('SELECT 1 FROM item_origins WHERE item_id=? AND origin=?',
                                        (row['id'],origin)).fetchone()
                identical = all(old[k] == row[k] for k in immutable)
                if refresh_unlinked:
                    old_meta,new_meta=json.loads(old['metadata_json']),json.loads(row['metadata_json'])
                    keys=('source_sha256','entry_span','owner_span','owner_raw','reference_span','reference_raw')
                    stable=(all(old[k]==row[k] for k in ('id','group_id','volume','source','ref_raw'))
                            and old_meta.get('version')==new_meta.get('version')=='unrepresented-regions-v1'
                            and all(old_meta.get(k)==new_meta.get(k) for k in keys))
                    if not stable:
                        raise ValueError('Regional revision changes literal occurrence: '+row['id'])
                    linked=db.execute('SELECT 1 FROM candidates WHERE item_id=? LIMIT 1',(row['id'],)).fetchone()
                    if linked:
                        preserved_linked+=1
                    elif not identical:
                        revisions.append((old,row))
                elif not registered or not identical:
                    raise ValueError('Conflicting addition: '+row['id'])
            else:
                additions.append(row)
        candidate_rows = list(src.execute('SELECT * FROM candidates'))
        source_ids = {r['id'] for r in rows}
        if any(r['item_id'] not in source_ids for r in candidate_rows):
            raise ValueError('Candidate without supplement item')
        before = stats(db)
        with db:
            if revisions:
                db.execute('''CREATE TABLE IF NOT EXISTS item_revision_history(
                    revision_id INTEGER PRIMARY KEY,item_id TEXT,origin TEXT,previous_label TEXT,
                    previous_metadata TEXT,previous_localization TEXT,recorded_at TEXT)''')
            for old,row in revisions:
                evidence=db.execute('SELECT evidence_json FROM localization_results WHERE item_id=?',(row['id'],)).fetchone()
                db.execute('INSERT INTO item_revision_history VALUES(NULL,?,?,?,?,?,?)',
                           (row['id'],origin,old['label'],old['metadata_json'],evidence[0] if evidence else None,
                            datetime.now(timezone.utc).isoformat()))
                db.execute('UPDATE items SET label=?,metadata_json=? WHERE id=?',
                           (row['label'],row['metadata_json'],row['id']))
                rowid=db.execute('SELECT rowid FROM items WHERE id=?',(row['id'],)).fetchone()[0]
                db.execute('UPDATE items_fts SET label=? WHERE rowid=?',(normalize(row['label'] or ''),rowid))
                db.execute('DELETE FROM localization_results WHERE item_id=?',(row['id'],))
                db.execute('INSERT OR REPLACE INTO localization_queue VALUES(?,?)',(row['id'],'regional_metadata_refresh'))
                db.execute('INSERT OR IGNORE INTO item_origins VALUES(?,?,?)',(row['id'],origin,'regional_metadata_refresh'))
            for row in additions:
                cur = db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                    tuple(row[k] for k in ('id','group_id','volume','label','ref_raw','source'))+
                    ('source_only',row['metadata_json']))
                db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',
                           (cur.lastrowid,normalize(row['label'] or ''),normalize(row['ref_raw'] or ''),row['volume']))
                db.execute('INSERT INTO item_origins VALUES(?,?,?)',(row['id'],origin,'regional_addition'))
                db.execute('INSERT INTO localization_queue VALUES(?,?)',(row['id'],'regional_addition'))
            for row in candidate_rows:
                db.execute('INSERT OR IGNORE INTO candidate_storage VALUES(?,?,?,?,?,?)',
                    tuple(row[k] for k in ('item_id','file','rank','score','navigable'))+(pack_json(row['candidate_json']),))
                db.execute('INSERT OR IGNORE INTO candidate_origins VALUES(?,?,?)',(row['item_id'],row['file'],origin))
            config = json.loads(db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
            sources = config.setdefault('incremental_supplements',[])
            if origin not in sources:
                sources.append(origin)
            db.execute("UPDATE metadata SET json=? WHERE key='summary'",(encoded(config),))
        after = refresh(db)
        result = dict(before=before,after=after,added=len(additions),revised_unlinked=len(revisions),
                      preserved_linked=preserved_linked,already_present=len(rows)-len(additions),supplement=origin)
        db.execute('INSERT INTO metadata VALUES(?,?)',
                   ('supplement_import:'+datetime.now(timezone.utc).isoformat(),encoded(result)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        print(encoded(result),flush=True)
        return result


def merge_overlay(db_path, overlay):
    with closing(connect(db_path)) as db, closing(readonly(overlay)) as src:
        summary = json.loads(src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
        if Path(summary['catalog']).resolve() != db_path.resolve():
            raise ValueError('Overlay belongs to another catalog')
        before = stats(db)
        merge_candidates(db,src,str(overlay.resolve()))
        after = refresh(db)
        result = dict(before=before,after=after,overlay=str(overlay.resolve()))
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('relations_refresh',encoded(result)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db_path.with_name('relations_refresh.json').write_text(json.dumps(result,indent=2)+'\n')
        print(encoded(result))


def report(db_path):
    with closing(readonly(db_path)) as db:
        metadata = {r[0]: json.loads(r[1]) for r in db.execute('SELECT * FROM metadata')}
        current = stats(db)
        baseline = metadata['summary']['baseline']
        volumes = [dict(r) for r in db.execute('''SELECT volume,count(*) AS items,
            sum(state='with_destinations') AS with_destinations,
            sum(state='source_only') AS pending FROM items GROUP BY volume ORDER BY volume''')]
        reasons = Counter()
        for row in db.execute('SELECT evidence_json FROM localization_results'):
            evidence = json.loads(row[0])
            if evidence.get('abstention'):
                reasons[evidence['abstention']] += 1
        summary = {'database':str(db_path.resolve()),'baseline':baseline,'current':current,
                   'items_gaining_candidates':current['with_destinations']-baseline['with_destinations'],
                   'records_added_since_baseline':current['items']-baseline['items'],
                   'volumes':len(volumes), 'localization_abstentions':dict(reasons),
                   'inference_calls':0, 'canonical_updates':0,
                   'quality':'Candidate coverage; not a measurement of editorial accuracy or Recall@K.'}
    db_path.with_name('summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with db_path.with_name('by_volume.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=['volume','items','with_destinations','pending'])
        writer.writeheader()
        writer.writerows(volumes)
    lines=['# Catálogo experimental consolidado','',
           'Registros pesquisáveis e candidatos físicos; sem promoção para o banco principal.','',
           '| Métrica | Antes | Depois |','| --- | ---: | ---: |']
    for key,label in [('items','Registros'),('with_destinations','Com candidato físico'),
                      ('pending','Sem candidato físico'),('candidate_links','Ligações registro–arquivo')]:
        lines.append(f'| {label} | {baseline[key]} | {current[key]} |')
    lines += ['',f"Cobertura de candidatos: {100*baseline['candidate_coverage']:.2f}% → {100*current['candidate_coverage']:.2f}%.",
              f"Registros adicionados desde o baseline: {summary['records_added_since_baseline']}. As coberturas têm denominadores diferentes quando há adições.",
              '', 'Contagens incluem interpretações alternativas, subcláusulas e registros sem referência interpretada; não equivalem a verbetes impressos únicos.',
              'Coincidências numéricas genéricas conservam a incerteza de edição/paginação. Os destinos anteriores foram preservados, sem nova certificação.',
              '', 'Proveniência: `item_origins`, `candidate_origins`, `localization_results` e `metadata` no banco. '
              'Evidências completas dos candidatos são comprimidas sem perda em `candidate_storage`; '
              'a view `candidates` é decodificada por `tools.indexing.entry_parser_bulk.readonly`.',
              '', 'Reprodução e comandos: `docs/index_search_consolidation.md`. Nenhuma inferência ou modificação canônica nesta campanha.']
    db_path.with_name('report.md').write_text('\n'.join(lines)+'\n')
    print(encoded(summary))


def verify(db_path):
    """Check conservation against all immutable inputs and actual target files."""
    with closing(readonly(db_path)) as db:
        config = json.loads(db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
        failures = []
        checked = []
        overlay = db.execute("SELECT json FROM metadata WHERE key='relations_refresh'").fetchone()
        relation_sources = [json.loads(overlay[0])['overlay']] if overlay else []
        for path in dict.fromkeys([config['catalog'], *config['supplements'], *config['relations'],
                                  *relation_sources, *config.get('incremental_supplements',[])]):
            db.execute('ATTACH DATABASE ? AS origin', (Path(path).resolve().as_uri()+'?mode=ro',))
            missing = db.execute('''SELECT count(*) FROM origin.candidates o
                WHERE NOT EXISTS(SELECT 1 FROM candidate_storage c
                WHERE c.item_id=o.item_id AND c.file=o.file)''').fetchone()[0]
            if missing:
                failures.append({'source':path,'missing_pairs':missing})
            if db.execute("SELECT 1 FROM origin.sqlite_master WHERE name='items'").fetchone():
                missing_items = db.execute('''SELECT count(*) FROM origin.items o
                    WHERE NOT EXISTS(SELECT 1 FROM items i WHERE i.id=o.id)''').fetchone()[0]
                if missing_items:
                    failures.append({'source':path,'missing_items':missing_items})
            checked.append(path)
            db.execute('DETACH DATABASE origin')
        missing_files = [r[0] for r in db.execute('SELECT DISTINCT file FROM candidate_storage WHERE navigable=1')
                         if not Path(r[0]).is_file()]
        if missing_files:
            failures.append({'missing_files':missing_files})
        check = db.execute('PRAGMA quick_check').fetchone()[0]
        if check != 'ok':
            failures.append({'quick_check':check})
        fts_count = db.execute('SELECT count(*) FROM items_fts').fetchone()[0]
        if fts_count != stats(db)['items']:
            failures.append({'fts_count':fts_count})
        recovery_pairs = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_candidate_support'").fetchone():
            recovery_pairs = db.execute('SELECT count(*) FROM recovery_candidate_support WHERE eligible=1').fetchone()[0]
            missing_recovery = db.execute('''SELECT count(*) FROM recovery_candidate_support s
                WHERE s.eligible=1 AND NOT EXISTS(SELECT 1 FROM candidate_storage c
                    WHERE c.item_id=s.item_id AND c.file=s.file AND c.navigable=1)''').fetchone()[0]
            if missing_recovery:
                failures.append({'missing_recovery_pairs':missing_recovery})
        result = {'checked_sources':checked,'missing_prior_pairs':sum(f.get('missing_pairs',0) for f in failures),
                  'missing_prior_items':sum(f.get('missing_items',0) for f in failures),
                  'missing_destination_files':missing_files,'quick_check':check,'fts_rows':fts_count,
                  'recovery_support_pairs_checked':recovery_pairs,
                  'failures':failures,'status':'failed' if failures else 'ok'}
    db_path.with_name('verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(encoded(result))
    if failures:
        raise ValueError('Consolidated catalog conservation failed')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('consolidate')
    c.add_argument('--base',type=Path,required=True)
    c.add_argument('--relations',type=Path,action='append',default=[])
    c.add_argument('--supplement',type=Path,action='append',default=[])
    c.add_argument('--out',type=Path,required=True)
    l=sub.add_parser('localize')
    l.add_argument('--db',type=Path,required=True)
    l.add_argument('--survey',type=Path,required=True)
    l.add_argument('--workers',type=int,default=20)
    m=sub.add_parser('merge-relations')
    m.add_argument('--db',type=Path,required=True)
    m.add_argument('--overlay',type=Path,required=True)
    addition=sub.add_parser('add-supplement')
    addition.add_argument('--db',type=Path,required=True)
    addition.add_argument('--supplement',type=Path,required=True)
    addition.add_argument('--refresh-unlinked',action='store_true',help='Revise metadata only for identical regional occurrences without candidates, preserving revision history')
    r=sub.add_parser('report')
    r.add_argument('--db',type=Path,required=True)
    v=sub.add_parser('verify')
    v.add_argument('--db',type=Path,required=True)
    a=p.parse_args()
    if a.command=='consolidate':
        consolidate(a.base,a.relations,a.supplement,a.out)
    elif a.command=='localize':
        localize(a.db,a.survey,a.workers)
    elif a.command=='merge-relations':
        merge_overlay(a.db,a.overlay)
    elif a.command=='add-supplement':
        add_supplement(a.db,a.supplement,a.refresh_unlinked)
    elif a.command=='report':
        report(a.db)
    else:
        verify(a.db)

if __name__=='__main__':
    main()

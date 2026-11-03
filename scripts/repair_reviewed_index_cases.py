#!/usr/bin/env python3
"""Plan/apply reviewed index repairs without inference. Default: JSON plan only.

Apply to a disposable SQLite copy first. --apply requires a new backup and audit.
Every change guards the old reference, entry, and source hashes. Resolved records
are retained and listed for review. Original values/evidence are archived in DB.
"""
from contextlib import closing
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.resolve_alphabetical_unresolved import connect_readonly, QUERY
from scripts.repair_alphabetical_material import read_source, normalize_offsets, insert_row
from scripts.alphabetical_index_db import refresh_volume_quality
from scripts.reviewed_index_evidence import checksum, identity
from tools.corpus_utils import page_number
from tools.indexing.index_target_locator import normalize_for_search

METHOD = 'reviewed_editorial_cases_v1'
PAIR = re.compile(r'^(?P<lemma>[^0-9;]+?)\s+(?P<page>\d{1,4}),\s*(?P<line>\d{1,3})'
                  r'(?:,\s*Leo\s+(?P<leo_page>\d{1,4}),\s*(?P<leo_line>\d{1,3}))?\.?$')


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def source_proof(path):
    return {'file': str(Path(path).resolve()), 'sha256': checksum(path)}


def span(source, text):
    norm = normalize_offsets(text)[0]
    if source['normalized'].count(norm) != 1:
        raise ValueError('source_anchor_not_unique: ' + text[:80])
    start = source['normalized'].index(norm)
    lo, hi = source['offsets'][start], source['offsets'][start+len(norm)-1]+1
    first = source['raw'].count('\n', 0, lo)+1
    last = source['raw'].count('\n', 0, hi)+1
    raw_lines = ''.join(source['raw'].splitlines(keepends=True)[first-1:last])
    return {'line_start': first, 'line_end': last, 'text_sha256': hashlib.sha256(raw_lines.encode()).hexdigest()}


def location_fields(page, line=None):
    return {'page_ref_int': page, 'page_ref_raw': str(page),
            'cited_editorial_page_start_raw': str(page), 'cited_editorial_page_start_number': page,
            'line_ref_raw': str(line) if line is not None else None,
            'cited_editorial_line_raw': str(line) if line is not None else None,
            'cited_editorial_line_start_raw': str(line) if line is not None else None,
            'cited_editorial_line_start_number': line, 'cited_location_parse_status': 'parsed'}


def plan(db, review_path):
    review = json.loads(Path(review_path).read_text())
    reviewed = {s['ref_id']: s for s in review['samples']}
    with closing(connect_readonly(db)) as con:
        rows = {r['ref_id']: dict(r) for r in con.execute(QUERY.replace('WHERE s.volume_id = ?',
            "WHERE s.volume_id IN ('PG040','PG087.01','PG110','PG148','PL010')"))}
        entries = {r['entry_key']: dict(r) for r in con.execute('SELECT * FROM alphabetical_entries WHERE entry_key IN '
            '(SELECT entry_key FROM alphabetical_refs WHERE ref_id IN ('+','.join('?' for _ in rows)+'))', list(rows))}
        ref_columns = [r['name'] for r in con.execute('PRAGMA table_info(alphabetical_refs)')]
    changes, protected, similar = {}, [], []

    def pending(row):
        return row['locator_status'] in ('unresolved', 'unverified', 'ambiguous') and not row['target_file'] and not row['resolved_target_ocr_file']

    def patch(row, reason, sources, block=None, namespace=None, child=None, fields=None):
        if not pending(row):
            protected.append({'ref_id': row['ref_id'], 'reason': reason, 'target': row['target_file']})
            return
        raw = json.loads(row['raw_json'] or '{}')
        raw['reviewed_editorial'] = {'method': METHOD, 'reason': reason, 'sources': sources,
            'block_reason': block, 'namespace': namespace}
        p = {'ref_id': row['ref_id'], 'volume_id': row['volume_id'],
             'old_ref': {k: row[k] for k in ref_columns}, 'old_entry': entries[row['entry_key']],
             'fields': fields or {}, 'raw': raw, 'sources': sources, 'child': child}
        changes[row['ref_id']] = p
        return p

    # Four independently reviewed mappings become candidates, never semantic resolutions.
    targets = {423605:356, 423654:350, 423621:403, 531845:614}
    for ref_id, physical in targets.items():
        r = rows[ref_id]; rev = reviewed[ref_id]
        if json.loads(r['raw_json']).get('reviewed_location'):
            continue
        files = list((ROOT/'teste'/r['volume_id']/'text').glob(f'*-{physical:03d}.txt'))
        if len(files) != 1:
            raise ValueError('reviewed target is not unique')
        target = files[0]
        sources = [source_proof(rev['source_file']), *rev['visual_evidence'], source_proof(target)]
        p = patch(r, rev['finding'], sources)
        if p:
            # ref_raw remains exactly as printed (including 121 9).
            proof = {'method':'facsimile_review_v1','identity':identity(r),'file':str(target.resolve()),
                'target_sha256':checksum(target),'cited_number':r['page_ref_int'],
                'candidate_source':'internal-marker' if r['volume_id']=='PG040' else 'printed-header',
                'numbering_scope':'Antwerp' if r['volume_id']=='PG040' else 'Migne_columns',
                'sources':sources,'finding':rev['finding'],'review_report':source_proof(review_path)}
            p['raw']['reviewed_location'] = proof

    # Only the two source assignments actually established in the review are moved.
    cases = [(939842, 'Andronicus imp. (is qui Senior dictus est)',
        'Consilium Gregoræ de die Paschali constituendo probat : rem aggreditur metuit, 372.', None),
        (973754, 'Inhonorabilis', 'Inhonorabilis. I, 757.', 'verona_edition_tome_I')]
    for ref_id, lemma, text, namespace in cases:
        r = rows[ref_id]
        if ':review:' in r['entry_key']:
            continue
        source = read_source(entries[r['entry_key']]['index_entry_source_ocr_file'])
        if lemma not in source['normalized']:
            raise ValueError('reviewed parent lemma missing')
        child = {'lemma':lemma,'text':text,'source_file':source['path'],**span(source,text)}
        sources = [source_proof(source['path']), source_proof(review_path)]
        patch(r, reviewed[ref_id]['finding'], sources,
              'editorial_namespace_requires_mapping' if namespace else None, namespace, child)

    # The glossary explicitly uses Petropolitan page/line pairs and parallel Leo loci.
    notice_path = ROOT/'teste/PG110/text/fc71e7ab-8ea5-4e9a-9197-0d4e455a3fb6-660.txt'
    notice = notice_path.read_text()
    if 'paginas editionis Petropolitanae' not in notice or 'secundus lineas' not in notice:
        raise ValueError('PG110 numbering notice changed')
    sources_cache = {}
    groups = defaultdict(list)
    for r in rows.values():
        if r['volume_id'] != 'PG110' or r['section_kind'] != 'onomastic_mixed':
            continue
        path = entries[r['entry_key']]['index_entry_source_ocr_file']
        if path and 661 <= page_number(Path(path)) <= 666:
            groups[r['entry_key']].append(r)
    for key, refs in groups.items():
        e = entries[key]; path = e['index_entry_source_ocr_file']
        source = sources_cache.setdefault(path, read_source(path))
        sources = [source_proof(path), source_proof(notice_path)]
        for r in refs:
            if json.loads(r['raw_json']).get('reviewed_editorial', {}).get('method') == METHOD:
                continue
            patch(r, 'Glossary uses Petropolitan pages and lines, not Migne headers; Leo is a parallel series.',
                  sources, 'editorial_namespace_requires_mapping',
                  'Leo_parallel' if r['ref_kind']=='parallel_locator' else 'Petropolitana_page_line')
        # Conservative whole-entry grammar; no guesswork across grouped/continued entries.
        text = normalize_offsets(e['entry_raw'] or '')[0]
        match = PAIR.fullmatch(text)
        if not match or source['normalized'].count(text) != 1:
            continue
        if normalize_for_search(match['lemma']) != normalize_for_search(e['lemma_raw'] or ''):
            continue
        page, line = int(match['page']), int(match['line'])
        page_refs = [r for r in refs if r['ref_raw']==str(page) and r['ref_kind']=='editorial_page']
        line_refs = [r for r in refs if r['ref_raw']==str(line) and r['ref_kind']=='editorial_page']
        if page==line or len(page_refs)!=1 or len(line_refs)!=1 or not all(pending(r) for r in refs):
            continue
        primary, component = page_refs[0], line_refs[0]
        if primary['ref_id'] not in changes or component['ref_id'] not in changes:
            continue
        p=changes[primary['ref_id']]
        p['fields'].update(location_fields(page,line),ref_kind='editorial_page_line',ref_raw=f'{page}, {line}')
        p['raw']['reviewed_editorial'].update(reason='Exact source entry and glossary notice establish a page/line pair.',
                                              source_span=span(source,text))
        p=changes[component['ref_id']]
        p['fields'].update({k:None for k in component if k.startswith('cited_editorial_') or k in
            ('page_ref_int','page_ref_raw','range_start_raw','range_end_raw')})
        p['fields'].update(ref_kind='target_locator',cited_location_parse_status='not_applicable')
        p['raw']['reviewed_editorial'].update(block_reason='line_component_not_page',
            reason='Line is retained as an archived component of the corrected page/line reference.',
            primary_ref_id=primary['ref_id'], page=page, line=line)
        similar.append({'entry_key':key,'page_ref_id':primary['ref_id'],'line_component_ref_id':component['ref_id']})
    # Audited OCR/legacy lemma discrepancy: both refs belong to the same exact printed entry.
    for ref_id in (888650,888651):
        r=rows[ref_id]
        if ':review:' in r['entry_key']:
            continue
        source=read_source(entries[r['entry_key']]['index_entry_source_ocr_file'])
        text='ἐξαρχος 770, 9.'
        child={'lemma':'ἐξαρχος','text':text,'source_file':source['path'],**span(source,text)}
        p=patch(r, reviewed[888651]['finding'], [source_proof(source['path']),source_proof(notice_path),
            *reviewed[888651]['visual_evidence']], 'editorial_namespace_requires_mapping', 'Petropolitana_page_line', child)
        if p and ref_id==888650:
            p['fields'].update(location_fields(770,9),ref_kind='editorial_page_line',ref_raw='770, 9')
        elif p:
            p['fields'].update({k:None for k in r if k.startswith('cited_editorial_') or k in ('page_ref_int','page_ref_raw')})
            p['fields'].update(ref_kind='target_locator',cited_location_parse_status='not_applicable')
            p['raw']['reviewed_editorial'].update(block_reason='line_component_not_page',primary_ref_id=888650)
    return {'method':METHOD,'input_db':str(Path(db).resolve()),'review':source_proof(review_path),
            'changes':list(changes.values()),'similar_page_line_pairs':similar,'protected_resolved':protected,
            'counts':{'changed_refs':len(changes),'similar_page_line_pairs':len(similar),
                      'protected_resolved':len(protected)}}


def apply_plan(db, plan_path, backup_path, audit_path):
    db, backup_path, audit_path = [Path(p).resolve() for p in (db,backup_path,audit_path)]
    if len({db,backup_path,audit_path,Path(plan_path).resolve()})!=4 or backup_path.exists() or audit_path.exists():
        raise ValueError('Distinct new backup and audit paths required')
    document=json.loads(Path(plan_path).read_text())
    if document['method']!=METHOD:
        raise ValueError('Unsupported plan')
    plan_hash=checksum(plan_path); affected=set(); applied=0; skipped=0
    with closing(sqlite3.connect(db.as_uri()+'?mode=rw',uri=True,timeout=30)) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON');con.execute('BEGIN IMMEDIATE')
        has_archive=con.execute("SELECT 1 FROM sqlite_master WHERE name='alphabetical_reviewed_patches'").fetchone()
        pending=[]; checked=set()
        for p in document['changes']:
            patch_key=f"{plan_hash}:{p['ref_id']}"
            if has_archive and con.execute('SELECT 1 FROM alphabetical_reviewed_patches WHERE patch_key=?',(patch_key,)).fetchone():
                skipped+=1;continue
            row=con.execute('SELECT * FROM alphabetical_refs WHERE ref_id=?',(p['ref_id'],)).fetchone()
            entry=con.execute('SELECT * FROM alphabetical_entries WHERE entry_key=?',(p['old_entry']['entry_key'],)).fetchone()
            if not row or dict(row)!=p['old_ref'] or not entry or dict(entry)!=p['old_entry']:
                raise ValueError(f"Canonical rows changed: {p['ref_id']}")
            if row['locator_status']=='resolved' or row['target_file'] or row['resolved_target_ocr_file']:
                raise ValueError('Resolved target must be reviewed separately')
            for proof in p['sources']:
                key=(proof['file'],proof['sha256'])
                if key not in checked and checksum(proof['file'])!=proof['sha256']:
                    raise ValueError('Evidence changed')
                checked.add(key)
            pending.append((patch_key,p))
        backup_path.parent.mkdir(parents=True,exist_ok=True)
        with backup_path.open('xb'):pass
        with closing(connect_readonly(db)) as src,closing(sqlite3.connect(backup_path)) as dst:src.backup(dst)
        con.execute('CREATE TABLE IF NOT EXISTS alphabetical_reviewed_patches '
            '(patch_key TEXT PRIMARY KEY,ref_id INTEGER NOT NULL,original_json TEXT NOT NULL,proposal_json TEXT NOT NULL,applied_at TEXT NOT NULL)')
        orders=dict(con.execute('SELECT section_key,MAX(entry_order) FROM alphabetical_entries GROUP BY section_key'))
        children={}
        for patch_key,p in pending:
            original={'ref':p['old_ref'],'entry':p['old_entry'],'evidence':[dict(r) for r in
                con.execute('SELECT * FROM alphabetical_locator_evidence WHERE ref_id=?',(p['ref_id'],))]}
            fields=dict(p['fields']);raw=p['raw'];child=p.get('child')
            if child:
                child_key=p['old_entry']['entry_key']+':review:'+hashlib.sha256(json.dumps(child,sort_keys=True).encode()).hexdigest()[:12]
                if child_key not in children:
                    entry=dict(p['old_entry']);section=entry['section_key'];orders[section]+=1
                    entry.update(entry_key=child_key,entry_order=orders[section],lemma_raw=child['lemma'],lemma_display=child['lemma'],
                        lemma_norm=normalize_for_search(child['lemma']),lemma_sort=normalize_for_search(child['lemma']),
                        entry_raw=child['text'],context_raw=child['text'],target_file_best=None,resolved_target_ocr_file=None,
                        confidence=None,raw_json=json.dumps({'reviewed_editorial':raw['reviewed_editorial'],'original_entry_key':p['old_entry']['entry_key']},ensure_ascii=False))
                    insert_row(con,'alphabetical_entries',entry)
                    con.execute('INSERT OR IGNORE INTO alphabetical_source_spans '
                        '(volume_id,ocr_file_path,line_start,line_end,block_type,text_sha256,created_at) VALUES (?,?,?,?,?,?,?)',
                        (p['volume_id'],child['source_file'],child['line_start'],child['line_end'],'texto_principal',child['text_sha256'],datetime.now(timezone.utc).isoformat()))
                    sid=con.execute('SELECT span_id FROM alphabetical_source_spans WHERE volume_id=? AND ocr_file_path=? AND line_start=? AND line_end=? AND text_sha256=?',
                        (p['volume_id'],child['source_file'],child['line_start'],child['line_end'],child['text_sha256'])).fetchone()[0]
                    con.execute('INSERT INTO alphabetical_entry_source_spans(entry_key,span_id) VALUES (?,?)',(child_key,sid))
                    children[child_key]=0
                children[child_key]+=1;fields.update(entry_key=child_key,ref_order=children[child_key])
            current_entry = con.execute('SELECT * FROM alphabetical_entries WHERE entry_key=?',
                (fields.get('entry_key',p['old_ref']['entry_key']),)).fetchone()
            raw['reviewed_editorial']['identity'] = identity({**dict(current_entry),**p['old_ref'],**fields})
            fields.update(raw_json=json.dumps(raw,ensure_ascii=False),target_file_probability=None,target_ocr_file_candidate_score=None)
            con.execute('UPDATE alphabetical_refs SET '+','.join(f'{k}=?' for k in fields)+' WHERE ref_id=?',[*fields.values(),p['ref_id']])
            con.execute('DELETE FROM alphabetical_locator_evidence WHERE ref_id=?',(p['ref_id'],))
            con.execute('INSERT INTO alphabetical_reviewed_patches VALUES (?,?,?,?,?)',(patch_key,p['ref_id'],json.dumps(original,ensure_ascii=False),json.dumps(p,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            affected.add(p['volume_id']);applied+=1
        for volume in affected:refresh_volume_quality(con,volume)
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity check failed')
        for name,digest in checked:
            if checksum(name)!=digest:raise ValueError('Evidence changed during apply')
        con.commit()
    result={'method':METHOD,'db':str(db),'backup':str(backup_path),'plan_sha256':plan_hash,
            'applied_refs':applied,'already_applied':skipped,'new_entries':len(children),'volumes':sorted(affected),'integrity':'ok'}
    write_new(audit_path,result)
    return result



def refresh_reports(db, plan_path, output_dir, base_dir, previous_dir):
    """Publish candidate overlays from CURRENT rows, preserving unrelated corrections.

    Reassignment keeps a numeric candidate only when its number is unchanged.
    Namespace-blocked or line-component rows never inherit a physical candidate.
    These are classifier overlays, not resolver --apply proposals.
    """
    document=json.loads(Path(plan_path).read_text())
    ids=defaultdict(set)
    for change in document['changes']:ids[change['volume_id']].add(change['ref_id'])
    ids['PG003'].update((347582,347583))
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    outputs=[]
    with closing(connect_readonly(db)) as con:
        for volume,selected in sorted(ids.items()):
            base_path=Path(base_dir)/(volume+'.json');previous_path=Path(previous_dir)/(volume+'.json')
            base=json.loads(base_path.read_text())
            previous=json.loads(previous_path.read_text()) if previous_path.exists() else {}
            available={r['ref_id']:r for r in base['records']}
            available.update({r['ref_id']:r for r in previous.get('records',[])})
            records={r['ref_id']:r for r in previous.get('records',[])}
            snapshots={r['ref_id']:r for r in previous.get('job',{}).get('rows',[])}
            current={r['ref_id']:dict(r) for r in con.execute(QUERY,(volume,))}
            for ref_id in selected:
                if ref_id not in current:raise ValueError('Reference disappeared')
                row=current[ref_id];raw=json.loads(row['raw_json']);record=dict(available.get(ref_id,{}))
                record.update(ref_id=ref_id)
                if raw.get('reviewed_editorial',{}).get('block_reason') or row['ref_kind']=='target_locator':
                    record.update(proposal=None,reason=raw.get('reviewed_editorial',{}).get('block_reason','internal_work_locator'),
                        locator_result={'file_search':{'complete_for_literal_headers':True,'candidates':[]}})
                elif raw.get('reviewed_location'):
                    proof=raw['reviewed_location']
                    record.update(proposal=None,reason='reviewed_file_mapping',locator_result={'file_search':{
                        'complete_for_literal_headers':True,'candidates':[{'file':proof['file']}]}})
                else:
                    # The two reviewed reassignments retain their literal page number.
                    old=next(p['old_ref'] for p in document['changes'] if p['ref_id']==ref_id)
                    if row['page_ref_int']!=old['page_ref_int']:raise ValueError('Reassignment needs fresh location')
                    record['proposal']=None
                    record['reason']='reviewed_entry_reassignment_requires_semantic_validation'
                records[ref_id]=record;snapshots[ref_id]=row
            root=con.execute('SELECT source_root FROM alphabetical_volumes WHERE volume_id=?',(volume,)).fetchone()[0]
            overlay={'report_kind':'reviewed_candidate_overlay','method':METHOD,'db':str(Path(db).resolve()),
                'plan_sha256':checksum(plan_path),'job':{'volume_id':volume,'source_root':root,'rows':list(snapshots.values())},
                'records':list(records.values()),'editorial_page_map':previous.get('editorial_page_map',{}),
                'provenance':{'base_report':source_proof(base_path),'prior_corrected_report':source_proof(previous_path) if previous_path.exists() else None}}
            path=output_dir/(volume+'.json')
            if path.exists():
                backup=path.with_name(path.name+'.before-'+checksum(plan_path)[:12])
                with backup.open('xb') as f:f.write(path.read_bytes())
                temporary=path.with_name(path.name+'.pending')
                write_new(temporary,overlay);temporary.replace(path)
            else:write_new(path,overlay)
            outputs.append({'volume':volume,'updated_refs':len(selected),'records':len(records),'file':str(path),'sha256':checksum(path)})
    return outputs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=ROOT/'data/alphabetical_indices.db')
    p.add_argument('--review',type=Path,default=ROOT/'data/jev_validation/blocked_review_seed42/review.json')
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--refresh-reports',type=Path)
    p.add_argument('--base-reports',type=Path,default=ROOT/'data/alphabetical_locator_full_fast')
    p.add_argument('--previous-reports',type=Path,default=ROOT/'data/alphabetical_locator_corrected')
    p.add_argument('--apply',action='store_true');p.add_argument('--backup',type=Path);p.add_argument('--audit',type=Path)
    args=p.parse_args()
    if args.refresh_reports:
        if args.apply:p.error('Refresh reports after applying, in a separate invocation')
        print(json.dumps(refresh_reports(args.db,args.plan,args.refresh_reports,args.base_reports,args.previous_reports)))
    elif args.apply:
        if not args.backup or not args.audit:p.error('--apply requires --backup and --audit')
        print(json.dumps(apply_plan(args.db,args.plan,args.backup,args.audit)))
    else:
        result=plan(args.db,args.review);write_new(args.plan,result);print(json.dumps(result['counts']))

if __name__=='__main__':main()

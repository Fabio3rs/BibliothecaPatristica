#!/usr/bin/env python3
"""Extract bounded internal-number indices and stage division-opening targets.

The JSON plan supplies reviewed physical bounds and literal boundary strings.
Source OCR/catalogs are read-only; all writes go to the new output directory.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.corpus_utils import page_number
from tools.indexing.unrepresented_region_recovery import run, VERSION as SELECTION_VERSION
from tools.indexing.internal_index_targets import scan_targets,locate,add_bounded_section_candidates
from tools.indexing.ordinal_index_formats import reconcile_bounded_ordinals


def export_records(database, out):
    """Portable, deterministic exports of the final extraction and candidates."""
    with sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        with (out/'entries.jsonl').open('w', encoding='utf-8') as lines, (out/'entries.csv').open('w', encoding='utf-8', newline='') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=['id', 'volume', 'section_id', 'label', 'ref_raw', 'source', 'state', 'candidate_files', 'flags'])
            writer.writeheader()
            for item in db.execute('SELECT * FROM items ORDER BY volume,source,id'):
                row = dict(item)
                meta = json.loads(row.pop('metadata_json'))
                candidates = [json.loads(r[0]) for r in db.execute('SELECT candidate_json FROM candidates WHERE item_id=? ORDER BY rank,file', (row['id'],))]
                lines.write(json.dumps({**row, 'metadata': meta, 'candidates': candidates}, ensure_ascii=False, sort_keys=True)+'\n')
                writer.writerow({k: row[k] for k in ['id','volume','label','ref_raw','source','state']} |
                    {'section_id': meta['section_id'], 'candidate_files': json.dumps([c['file'] for c in candidates], ensure_ascii=False),
                     'flags': json.dumps(meta.get('flags', []), ensure_ascii=False)})


def select_pages(root,volume,start,end):
    pages={}
    for path in (root/volume/'text').glob('*.txt'):
        physical=page_number(path)
        if physical is not None and start<=physical<=end:
            if physical in pages:raise ValueError(f'Duplicate physical page: {volume}/{physical}')
            pages[physical]={'source':str(path.resolve()),'physical':physical,'raw':path.read_text()}
    if set(pages)!=set(range(start,end+1)):raise ValueError('Missing physical pages in declared bounds')
    return [pages[n] for n in sorted(pages)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--catalog',type=Path,help='Optional catalog provenance for later supplement import; not needed to extract or locate the explicit plan')
    p.add_argument('--root',type=Path,default=Path('teste'));p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--max-neighbor-pages',type=int,default=3,help='Maximum bounded physical window for missing narrative section numbers; 0 disables')
    args=p.parse_args();plans=json.loads(args.plan.read_text())['sections']
    if len({p['id'] for p in plans})!=len(plans):
        raise ValueError('Section IDs must be unique')
    by_section={p['id']:p for p in plans}
    args.out.mkdir(parents=True,exist_ok=False)
    regions=[];maps={};boundary_proofs=[];diagnostics=[];body_manifest=[]
    for plan in plans:
        pages=select_pages(args.root,plan['volume'],*plan['index_pages'])
        if plan.get('reference_context'):
            context=plan['reference_context']
            page=next(p for p in pages if p['physical']==context['source_physical'])
            literal=context['literal']
            if page['raw'].count(literal)!=1:
                raise ValueError('Reference-context evidence must occur uniquely in source OCR')
            start=page['raw'].index(literal)
            context.update(source=page['source'],span=[start,start+len(literal)],
                           source_sha256=hashlib.sha256(page['raw'].encode()).hexdigest())
        if plan.get('index_start_literal'):
            pages[0]['start']=pages[0]['raw'].index(plan['index_start_literal'])
        if plan.get('index_end_before'):
            pages[-1]['end']=pages[-1]['raw'].index(plan['index_end_before'])
        region={'section_id':plan['id'],'volume':plan['volume'],'kind':'thematic_index',
                'internal_index_family':plan['family'],'end_status':'explicit_reviewed_bounds','spans':[]}
        for page in pages:
            a,b=page.get('start',0),page.get('end',len(page['raw']))
            region['spans'].append({'source':page['source'],'physical':page['physical'],'span':[a,b],
                'source_sha256':hashlib.sha256(page['raw'].encode()).hexdigest()})
        regions.append(region)
        body=select_pages(args.root,plan['volume'],*plan['body_pages'])
        if plan.get('body_start_literal'):body[0]['start']=body[0]['raw'].index(plan['body_start_literal'])
        if plan.get('body_end_before'):body[-1]['end']=body[-1]['raw'].index(plan['body_end_before'])
        body_manifest.extend({'volume':plan['volume'],'section_id':plan['id'],'source':page['source'],'physical':page['physical'],
            'span':[page.get('start',0),page.get('end',len(page['raw']))],
            'source_sha256':hashlib.sha256(page['raw'].encode()).hexdigest()} for page in body)
        maps[plan['id']]=scan_targets(body,plan['family'],diagnostics)
        if plan['family']=='section_number':
            add_bounded_section_candidates(maps[plan['id']],body,args.max_neighbor_pages)
        boundary_proofs.append({'plan':plan,'index_first':pages[0]['source'],'index_last':pages[-1]['source'],
                                'body_first':body[0]['source'],'body_last':body[-1]['source']})
    regions_path=args.out/'regions.jsonl'
    regions_path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in regions))
    (args.out/'plan.json').write_text(json.dumps({'sections':plans,'boundary_proofs':boundary_proofs},ensure_ascii=False,indent=2)+'\n')
    (args.out/'body_manifest.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in body_manifest))
    selection=args.out/'selection.json'
    selection.write_text(json.dumps({'version':SELECTION_VERSION,'by_volume':[{'volume':p['volume']} for p in plans]}))
    run(args.catalog,regions_path,args.out/'extraction',args.workers,selection_manifest=selection)
    stats=Counter();per_volume={};unmapped=[];methods=Counter()
    with sqlite3.connect(args.out/'extraction'/'search.sqlite') as db:
        db.row_factory=sqlite3.Row
        items=[dict(row) for row in db.execute('SELECT * FROM items')]
        metadata=[json.loads(item['metadata_json']) for item in items]
        ordinal_recovery=reconcile_bounded_ordinals(metadata)
        for item,meta in zip(items,metadata):
            targets=maps[meta['section_id']]
            if by_section[meta['section_id']].get('reference_context'):
                meta['reference_context']=by_section[meta['section_id']]['reference_context']
            db.execute('UPDATE items SET metadata_json=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),item['id']))
            candidates,missing=locate(item,meta,targets)
            state='with_destinations' if candidates else 'source_only'
            db.execute('UPDATE items SET state=? WHERE id=?',(state,item['id']))
            for c in candidates:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',(item['id'],c['file'],c['rank'],c['score'],1,json.dumps(c,ensure_ascii=False)))
            stats[state]+=1;stats['candidate_links']+=len(candidates)
            for tier in {c['tier'] for c in candidates}:methods[tier]+=1
            vc=per_volume.setdefault(item['volume'],Counter());vc[state]+=1
            if missing or not candidates:unmapped.append({'id':item['id'],'volume':item['volume'],'label':item['label'],
                                       'binding':meta['internal_binding'],'missing_numbers':missing,'source':item['source']})
        summary={'counts':dict(stats),'by_volume':{k:dict(v) for k,v in per_volume.items()},
                 'unmapped':unmapped,'target_keys':{k:len(v) for k,v in maps.items()},'canonical_updates':0,'inference_calls':0,
                 'context_diagnostics':diagnostics,
                 'ordinal_recovery':ordinal_recovery,
                 'localization_methods_by_item':dict(methods),'max_neighbor_pages':args.max_neighbor_pages,
                 'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__),Path('tools/indexing/internal_index_formats.py'),Path('tools/indexing/internal_index_targets.py'),Path('tools/indexing/ordinal_index_formats.py'),Path('tools/indexing/named_division_formats.py')]}}
        db.execute('INSERT INTO metadata VALUES(?,?)',('internal_localization',json.dumps(summary,ensure_ascii=False)))
    (args.out/'localization.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (args.out/'target_anchors.jsonl').open('w') as f:
        for section_id,mapping in maps.items():
            for key,anchors in mapping.items():f.write(json.dumps({'volume':by_section[section_id]['volume'],'section_id':section_id,'key':key,'anchors':anchors},ensure_ascii=False)+'\n')
    export_records(args.out/'extraction'/'search.sqlite',args.out)
    print(json.dumps({k:v for k,v in summary.items() if k!='unmapped'},ensure_ascii=False))


if __name__=='__main__':main()

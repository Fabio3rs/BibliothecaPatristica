#!/usr/bin/env python3
"""Rebuild a declared page selection from current raw OCR, without an old catalog.

The selection is source identity and scope policy, never a copy of parsed entries.
Uses the same regional parser and catalog schema as the regional campaign.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.corpus_utils import page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.reproducible_selection import source_path
from tools.indexing.unrepresented_region_recovery import run as extract_regions


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def rebuild(manifest, root, out, workers=2):
    spec=json.loads(manifest.read_text())
    if spec.get('version')!='editorial-page-rebuild-v1':
        raise ValueError('Unsupported source selection')
    sources=[p['source'] for p in spec['pages']]
    if len(sources)!=len(set(sources)):
        raise ValueError('Duplicate source selection')
    if any(Path(p['source']).parts[0]!=p['volume'] for p in spec['pages']):
        raise ValueError('Source selection volume mismatch')
    out.mkdir(parents=True,exist_ok=False)
    regions=[]
    for page in sorted(spec['pages'],key=lambda p:p['source']):
        source=source_path(root,page['source'])
        raw=source.read_text()
        # Scopes are recomputed from current XML, so text repairs do not leave stale offsets.
        if page['scope']!='principal_blocks':
            raise ValueError('Unsupported scope policy')
        spans=[{'source':str(source),'physical':page_number(source),'span':[b['body_start'],b['body_end']],
                'source_sha256':hashlib.sha256(raw.encode()).hexdigest()}
               for b in blocks(raw) if b['kind']=='texto_principal']
        if not spans:
            raise ValueError('No principal blocks: '+str(source))
        regions.append({'section_id':page['section_id'],'volume':page['volume'],
                        'kind':'index_unspecified','spans':spans,'end_status':'selected_page_not_complete_section'})
    inventory=out/'regions.jsonl'
    inventory.write_text(''.join(encode(r)+'\n' for r in regions))
    selection=out/'selection.json'
    selection.write_text(encode({'version':'unrepresented-regions-v1',
                                'by_volume':[{'volume':v} for v in sorted({r['volume'] for r in regions})]})+'\n')
    extract_regions(None,inventory,out/'extraction',workers=workers,selection_manifest=selection)
    grouped=defaultdict(list)
    row_count=0
    digest=hashlib.sha256()
    with closing(readonly(out/'extraction/search.sqlite')) as db:
        for row in db.execute('SELECT * FROM items ORDER BY volume,source,id'):
            row_count+=1
            item=dict(row);meta=json.loads(item['metadata_json']);span=meta['reference_span']
            digest.update(encode(item).encode())
            if not span or not item['ref_raw']:
                continue
            group={'id':item['group_id'],'source':item['source'],'volume':item['volume'],
                   'char_start':span[0],'char_end':span[1],'raw':item['ref_raw'],
                   'source_sha256':meta['source_sha256'],'owner_raw':meta['owner_raw'],
                   'owner_span':meta['owner_span'],'owner_span_required':True,
                   'section_id':meta['section_id'],'flags':meta['flags'],
                   'status':'numeric_screen_only'}
            if meta['reference_namespace'] not in {'unknown','page','qualified_page','bracketed_page'}:
                group.update(profile='namespace:'+meta['reference_namespace'],status='profile_notation_pending')
            grouped[item['volume']].append(group)
    for volume,groups in sorted(grouped.items()):
        folder=out/'survey/volumes'/volume;folder.mkdir(parents=True)
        groups.sort(key=lambda g:(g['source'],g['char_start'],g['char_end'],g['id']))
        (folder/'groups.jsonl').write_text(''.join(encode(g)+'\n' for g in groups))
        sources=sorted({g['source'] for g in groups})
        (folder/'sources.jsonl').write_text(''.join(encode({'source':s})+'\n' for s in sources))
    result={'version':spec['version'],'manifest':str(manifest.resolve()),
            'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'root':str(root.resolve()),'pages':len(spec['pages']),'rows':row_count,
            'logical_sha256':digest.hexdigest(),'source_dependencies':['current_raw_ocr','source_selection_manifest'],
            'previous_database_required':False,'entire_corpus_rebuilt':False,'canonical_updates':0}
    (out/'rebuild.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(encode(result))
    return result


def export_page_overlay(catalog, estimates, output):
    """Adapt numeric evidence to the existing search consolidator, preserving alternatives."""
    if output.exists():
        raise ValueError('Overlay output already exists')
    with closing(readonly(estimates)) as src:
        config=json.loads(src.execute('SELECT config_json FROM runs').fetchone()[0])
        if Path(config['catalog']).resolve()!=catalog.resolve():
            raise ValueError('Page estimates belong to another catalog')
        with closing(sqlite3.connect(output)) as dst:
            dst.executescript('''
                CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
                CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,
                    navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            ''')
            dst.execute('INSERT INTO metadata VALUES(?,?)',('summary',encode({
                'catalog':str(catalog.resolve()),'source':str(estimates.resolve()),
                'version':'estimated-page-search-overlay-v1','canonical_updates':0})))
            for row in src.execute('SELECT * FROM candidates ORDER BY item_id,rank,file'):
                evidence=json.loads(row['evidence_json'])
                score=max((m['score'] for m in evidence['matches']),default=0)
                candidate=dict(evidence,score=score,certified=False,
                    navigable=not evidence.get('source_file_itself',False) and Path(row['file']).is_file(),
                    reasons=['editorial_page_estimate'],uncertainties=['unreviewed_page_candidate'],
                    estimator_evidence=evidence,estimator_database=str(estimates.resolve()))
                dst.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',(
                    row['item_id'],row['file'],row['rank'],score,candidate['navigable'],encode(candidate)))
            dst.commit()


def localize_rebuild(out, root, workers, works_db, estimator_db):
    from tools.indexing.estimator_candidate_recovery import main as estimate
    from tools.indexing.editorial_relation_recovery import recover
    from tools.indexing.search_catalog_consolidation import consolidate
    catalog=out/'extraction/search.sqlite'
    estimate(['--catalog',str(catalog),'--all-volumes','--workers',str(workers),
              '--corpus-root',str(root),'--works-db',str(works_db),
              '--estimator-db',str(estimator_db),'--out',str(out/'page_candidates')])
    estimates=out/'page_candidates/candidates.sqlite'
    recover(catalog,out/'survey',out/'relations',catalog_context=True,
            estimator_candidates=estimates,only_ibid=True)
    overlay=out/'page_overlay.sqlite'
    export_page_overlay(catalog,estimates,overlay)
    consolidate(catalog,[overlay,out/'relations/relations.sqlite'],[],out/'catalog')
    result=json.loads((out/'rebuild.json').read_text())
    result['search_catalog']=str((out/'catalog/search.sqlite').resolve())
    result['candidate_statistics']=json.loads((out/'catalog/baseline.json').read_text())['baseline']
    result['localization_dependencies']={
        'works_db_readonly':str(works_db.resolve()),
        'optional_observation_cache_readonly':str(estimator_db.resolve()),
        'raw_destination_ocr':str(root.resolve())}
    (out/'rebuild.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def compare_rebuilds(left, right):
    """Compare catalog data, normalizing only each run's output location."""
    def signature(folder):
        result={}
        with closing(readonly(folder/'catalog/search.sqlite')) as db:
            for table,order in [('items','id'),('candidates','item_id,file')]:
                digest=hashlib.sha256();count=0
                for row in db.execute(f'SELECT * FROM {table} ORDER BY {order}'):
                    record=dict(row)
                    for key in ('metadata_json','candidate_json'):
                        if key in record:
                            record[key]=json.loads(record[key])
                    data=encode(record).replace(str(folder.resolve()),'<RUN>')
                    if not folder.is_absolute():
                        data=data.replace(str(folder),'<RUN>')
                    digest.update((data+'\n').encode());count+=1
                result[table]={'rows':count,'sha256':digest.hexdigest()}
        return result
    a,b=signature(left),signature(right)
    result={'status':'identical' if a==b else 'different',
            'left':str(left.resolve()),'right':str(right.resolve()),
            'logical_signatures':[a,b],
            'normalization':'run output directory only; items and full candidate evidence compared',
            'entire_corpus_verified':False}
    (left/'reproducibility.json').write_text(json.dumps(result,indent=2)+'\n')
    if a!=b:
        raise ValueError('Rebuilt catalogs differ; see reproducibility.json')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--root',type=Path,default=Path('teste'))
    p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--with-destinations',action='store_true',help='Also rebuild page candidates, ibid relations and a searchable catalog')
    p.add_argument('--works-db',type=Path,default=Path('data/patristic_indices.db'))
    p.add_argument('--estimator-db',type=Path,default=Path('data/editorial_page_estimator.db'))
    p.add_argument('--compare-to',type=Path,help='Compare the rebuilt catalog with a previous complete replay')
    args=p.parse_args()
    if args.workers<1:p.error('workers must be positive')
    if args.compare_to and not args.with_destinations:p.error('--compare-to requires --with-destinations')
    rebuild(args.manifest,args.root,args.out,args.workers)
    if args.with_destinations:
        print(encode(localize_rebuild(args.out,args.root,args.workers,args.works_db,args.estimator_db)))
        if args.compare_to:
            print(encode(compare_rebuilds(args.out,args.compare_to)))

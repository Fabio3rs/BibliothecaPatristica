"""Apply source-anchored context repairs in a searchable experimental supplement."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.audit_po015_names_lexical import normalize
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import display
from tools.indexing.roman_owner_context import repair_roman_owner
from tools.indexing.line_entry_recovery import repair_line_owner
from tools.indexing.line_recovery_eligibility import assess_line_recovery

VERSION = 'index-context-repairs-v2'


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def lines(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def match_item_reference(item, raw):
    meta=json.loads(item['metadata_json'])
    span=meta.get('source_span')
    if not span or not 0<=span[0]<span[1]<=len(raw):
        return False
    return raw[span[0]:span[1]].strip().rstrip('.') == item['ref_raw'].strip().rstrip('.')


def entry_label(entry):
    owner=entry['owner_raw']
    if entry.get('parent_owner_span') and entry['parent_owner_span']!=entry['owner_span']:
        return display(entry['parent_owner_raw'])+' — '+display(owner)
    return display(owner)


def map_availability(volume, physical, survey, reference):
    """Only explicitly scoped existing maps may interpret the repaired notation."""
    path=survey/'volumes'/volume/'profiles.json'
    profiles=json.loads(path.read_text()) if path.exists() else []
    applicable=[p for p in profiles if p['profile']['index_physical_start']<=physical<=p['profile']['index_physical_end']]
    return {'status':'scoped_map_requires_notation_adapter' if applicable else
            'outside_existing_profile_scope' if profiles else 'no_scoped_map_available',
            'reference_raw':reference,'inventory':str(path.resolve()),
            'profile_scopes':[{'notation':p['profile']['notation'],
                               'physical_start':p['profile']['index_physical_start'],
                               'physical_end':p['profile']['index_physical_end']} for p in profiles],
            'candidates':[]}


def run(patterns, survey, catalog, out, relations=(), previous=None):
    patterns,survey,catalog,out=map(Path,(patterns,survey,catalog,out))
    selected=[r for r in lines(patterns/'occurrences.jsonl') if set(r['extraction_diagnostics']) &
              {'roman_context_in_owner_not_reference','multiple_entry_like_lines_in_owner'}]
    wanted={r['group_id'] for r in selected}
    flags_by_group=defaultdict(set)
    for record in selected:
        flags_by_group[record['group_id']].update(record['extraction_diagnostics'])
    with closing(readonly(survey/'survey.sqlite')) as db:
        groups={ident:json.loads(raw) for ident,raw in db.execute('SELECT id,evidence_json FROM groups') if ident in wanted}
    out.mkdir(parents=True,exist_ok=False)
    cache={}
    proposals={}
    for ident,g in groups.items():
        if g['source'] not in cache:
            cache[g['source']]=Path(g['source']).read_text()
        raw=cache[g['source']]
        flags=flags_by_group[ident]
        proposal=repair_roman_owner(raw,g) if 'roman_context_in_owner_not_reference' in flags else repair_line_owner(raw,g)
        if 'roman_context_in_owner_not_reference' not in flags:
            proposal['format_assessment']=assess_line_recovery(g,proposal)
            if not proposal['format_assessment']['eligible']:
                proposal['status']='format_requires_review'
        proposals[ident]={'group_id':ident,'volume':g['volume'],'physical':g['physical'],
                         'source':g['source'],'source_sha256':sha(raw),'parser_result':proposal,
                         'repair_kind':'roman_context' if 'roman_context_in_owner_not_reference' in flags else 'line_segmentation'}
    base_items={};by_source=defaultdict(list);copied_candidates=defaultdict(dict)
    with closing(readonly(catalog)) as db:
        for row in db.execute('SELECT * FROM items'):
            if row['source'] in cache:
                item=dict(row);base_items[item['id']]=item;by_source[item['source']].append(item)
        for ident,value in db.execute('SELECT item_id,candidate_json FROM candidates'):
            if ident in base_items:
                candidate=json.loads(value)
                copied_candidates[ident][candidate['file']]=candidate
    for path in relations:
        with closing(readonly(path)) as db:
            meta=json.loads(db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
            if Path(meta['catalog']).resolve()!=catalog.resolve():
                raise ValueError('Relations belong to another catalog')
            for ident,value in db.execute('SELECT item_id,candidate_json FROM candidates'):
                if ident in base_items:
                    c=json.loads(value)
                    c['recovery_database']=str(Path(path).resolve())
                    old=copied_candidates[ident].get(c['file'])
                    if old is not None:
                        winner,other=(c,old) if (c['navigable'],c['score'])>(old['navigable'],old['score']) else (old,c)
                        winner.setdefault('additional_support',[]).append(other)
                        c=winner
                    copied_candidates[ident][c['file']]=c
    output,annotations,replacements,states={},[],[],Counter()
    for record in selected:
        ident=record['item_id'];item=base_items.get(ident);g=groups.get(record['group_id'])
        if not item or not g:
            states['missing_catalog_or_group']+=1;continue
        proposed=proposals[g['id']];p=proposed['parser_result'];kind=proposed['repair_kind']
        raw=cache[g['source']]
        owner=None;ref=item['ref_raw'];span=json.loads(item['metadata_json']).get('source_span')
        if kind=='roman_context' and p['status']=='restored_candidate':
            owner=p['corrected_owner_raw']
            if not match_item_reference(item,raw):
                states['item_span_requires_reconciliation']+=1;continue
            # The expanded group preserves list-wide context without multiplying
            # every numeric atom into a copy of the entire list.
            ref=p['roman_raw']+', '+item['ref_raw']
            span_parts=[p['expanded_reference_span'],span]
        elif kind=='line_segmentation' and p['status']=='candidate_repair' and p.get('existing_reference_entry') is not None:
            entry=p['entries'][p['existing_reference_entry']]
            owner=entry_label(entry);span_parts=[entry['reference_span']]
            if not match_item_reference(item,raw):
                states['item_span_requires_reconciliation']+=1;continue
        else:
            states[p['status']]+=1
            annotations.append((ident,proposed));continue
        if not owner or not any(c.isalpha() for c in owner):
            states['empty_owner_deferred']+=1;continue
        mapping=map_availability(g['volume'],g['physical'],survey,ref)
        evidence={'version':VERSION,**proposed,'original_item':{k:item[k] for k in ('id','label','ref_raw','source')},
                  'map_lookup':mapping,'original_item_metadata':json.loads(item['metadata_json']),
                  'source_span':span,'reference_evidence_spans':span_parts,
                  'reference_display_is_contextualized':kind=='roman_context',
                  'expanded_group_literal':p.get('expanded_reference_raw'),
                  'preserved_destination_evidence_predates_owner_repair':bool(copied_candidates[ident]),
                  'destination_scores_not_recomputed':bool(copied_candidates[ident]),
                  'canonical_write_allowed':False,'certified':False}
        new={**item,'label':display(owner),'ref_raw':ref,'metadata_json':encoded(evidence)}
        output[ident]=new;replacements.append((ident,item['label'],item['ref_raw'],item['source']))
        states['repaired_'+kind]+=1;states['map:'+mapping['status']]+=1
    # New clauses are deduplicated by printed reference spans, not labels.
    child_stats=Counter();seen=set()
    for proposed in proposals.values():
        if proposed['repair_kind']!='line_segmentation':continue
        g=groups[proposed['group_id']];p=proposed['parser_result']
        if p['status']!='candidate_repair':continue
        for index,entry in enumerate(p.get('entries',[])):
            if index in p.get('format_assessment',{}).get('excluded_entry_indices',[]):
                child_stats['child_boundary_requires_review']+=1;continue
            if index==p.get('existing_reference_entry'):continue
            a,b=entry['reference_span'];key=(g['source'],a,b)
            if key in seen:child_stats['duplicate_child_span']+=1;continue
            seen.add(key)
            represented=[]
            for old in by_source[g['source']]:
                meta=json.loads(old['metadata_json']);span=meta.get('source_span')
                if span and max(a,span[0])<min(b,span[1]):represented.append(old['id'])
            if represented:
                child_stats['child_reference_already_represented']+=1;continue
            ident='context-child:'+sha(encoded(key))[:24]
            mapping=map_availability(g['volume'],g['physical'],survey,entry['reference_raw'])
            evidence={'version':VERSION,'parent_group_id':g['id'],'entry':entry,
                      'source_span':[a,b],'parser_ambiguities':p.get('ambiguities',[]),
                      'source_sha256':sha(cache[g['source']]),'map_lookup':mapping,
                      'canonical_write_allowed':False,'certified':False,
                      'source_link_is_index_not_destination':True}
            output[ident]={'id':ident,'group_id':ident,'volume':g['volume'],
                'label':entry_label(entry),'ref_raw':entry['reference_raw'],
                'source':g['source'],'state':'source_only','metadata_json':encoded(evidence)}
            child_stats['additional_searchable_clauses']+=1
    comparison=None
    if previous:
        with closing(readonly(previous)) as old_db:
            old_rows={r['id']:dict(r) for r in old_db.execute('SELECT id,label,ref_raw FROM items')}
            old_replacements={r[0] for r in old_db.execute('SELECT item_id FROM item_replacements')}
        new_replacements={r[0] for r in replacements}
        old_ids,new_ids=set(old_rows),set(output)
        comparison={'previous':str(Path(previous).resolve()),
                    'previous_replacements':len(old_replacements),
                    'previous_replacements_retained':len(old_replacements & new_replacements),
                    'additional_replacements':len(new_replacements-old_replacements),
                    'previous_rows_retained':len(old_ids & new_ids),
                    'previous_rows_not_reissued':[old_rows[i] for i in sorted(old_ids-new_ids)],
                    'changed_labels_or_references':[i for i in sorted(old_ids & new_ids)
                        if any(old_rows[i][key]!=output[i][key] for key in ('label','ref_raw'))]}
        (out/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
    with sqlite3.connect(out/'search.sqlite') as db:
        db.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
          CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
          CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,tokenize='unicode61 remove_diacritics 2');
          CREATE TABLE item_annotations(item_id TEXT,evidence_json TEXT);
          CREATE TABLE item_replacements(item_id TEXT PRIMARY KEY,original_label TEXT,original_ref TEXT,source TEXT);
          CREATE TABLE repairs(group_id TEXT PRIMARY KEY,source TEXT,evidence_json TEXT);
          CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for ident,item in output.items():
            candidates=list(copied_candidates[ident].values())
            if any(c['navigable'] for c in candidates):item['state']='with_destinations'
            cursor=db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',tuple(item[k] for k in ('id','group_id','volume','label','ref_raw','source','state','metadata_json')))
            db.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',(cursor.lastrowid,normalize(item['label']),normalize(item['ref_raw']),item['volume']))
            for c in candidates:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',(ident,c['file'],c['rank'],c['score'],c['navigable'],encoded(c)))
        db.executemany('INSERT INTO item_replacements VALUES(?,?,?,?)',replacements)
        db.executemany('INSERT INTO item_annotations VALUES(?,?)',[(i,encoded(e)) for i,e in annotations])
        db.executemany('INSERT INTO repairs VALUES(?,?,?)',[(p['group_id'],p['source'],encoded(p)) for p in proposals.values()])
        summary={'version':VERSION,'catalog':str(catalog.resolve()),'input_items':len(selected),'input_groups':len(wanted),
                 'source_files':len(cache),'replaced_search_records':len(replacements),'supplement_rows':len(output),
                 'states':dict(states),'child_counts':dict(child_stats),'new_destination_links':0,
                 'preserved_candidate_links':sum(len(copied_candidates[i]) for i in output),
                 'replacements_by_volume':dict(Counter(output[i]['volume'] for i, *_ in replacements)),
                 'additional_clauses_by_volume':dict(Counter(r['volume'] for i,r in output.items() if i.startswith('context-child:'))),
                 'canonical_updates':0,'ocr_updates':0,'inference_calls':0,
                 'previous_comparison':comparison,
                 'interpretation':'Corrected search view plus source-anchored clauses; not unique certified printed lemmas.'}
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'repairs.jsonl').write_text(''.join(encoded(p)+'\n' for p in proposals.values()))
    (out/'items.jsonl').write_text(''.join(encoded({**i,'metadata_json':json.loads(i['metadata_json'])})+'\n' for i in output.values()))
    manifest={'version':VERSION,'patterns':str(patterns.resolve()),'pattern_occurrences_sha256':sha((patterns/'occurrences.jsonl').read_text()),
              'catalog':str(catalog.resolve()),'survey':str(survey.resolve()),'relations':[str(Path(p).resolve()) for p in relations],
              'source_hashes':{p:sha(r) for p,r in cache.items()},'groups_sha256':sha(encoded(groups)),
              'catalog_rows_sha256':sha(encoded(base_items)),
            'code_hashes':{p.name:sha(p.read_text()) for p in [Path(__file__),Path(__file__).with_name('roman_owner_context.py'),Path(__file__).with_name('line_entry_recovery.py'),Path(__file__).with_name('line_recovery_eligibility.py')]}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    report=['# Correções mecânicas de contexto e segmentação','',
            f"- Registros examinados: {len(selected)}; grupos: {len(wanted)}; arquivos: {len(cache)}.",
            f"- Registros substituídos na visão de busca: {len(replacements)}.",
            f"- Cláusulas adicionais pesquisáveis: {child_stats['additional_searchable_clauses']}.",
            '- Novos destinos físicos: 0. OCR e banco principal permanecem intactos.','',
            '| Estado | Registros |','| --- | ---: |']
    report.extend(f'| {state} | {count} |' for state,count in states.items())
    report.extend(['','As substituições conservam o ID e não aumentam o total de entradas. '
                   'As cláusulas adicionais podem ser subverbetes; não são uma contagem certificada de lemas.',
                   'A interpretação dos romanos permanece em aberto quando falta mapa de edição/seção. '
                   'Ambiguidades e relações de contexto estão em repairs.jsonl e items.jsonl.',
                   'Reexecute em outra saída após atualizar OCR, extração ou mapas.'])
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patterns',type=Path,default=Path('data/index_catalog/editorial_patterns_pilot_004'))
    parser.add_argument('--survey',type=Path,default=Path('data/index_entry_parser/global_targets_20261106_004'))
    parser.add_argument('--catalog',type=Path,default=Path('data/index_catalog/search_candidates_002/search.sqlite'))
    parser.add_argument('--relations-db',type=Path,action='append',default=[])
    parser.add_argument('--previous-supplement',type=Path,help='Read-only comparison; the new supplement replaces it in search')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.patterns,args.survey,args.catalog,args.out,args.relations_db,args.previous_supplement),ensure_ascii=False,indent=2))


if __name__=='__main__':main()

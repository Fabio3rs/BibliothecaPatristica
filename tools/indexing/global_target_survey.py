"""Offline corpus-wide file-candidate survey; immutable inputs, isolated staging.

Header coincidences are a diagnostic sieve, never an inferred editorial namespace.
Explicit scoped profiles take precedence and never fall back to header numbers.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
import csv
import json
import multiprocessing
import os
from pathlib import Path
import re
import sqlite3
import time

from scripts.audit_alphabetical_numbering import build_series, supported_sequence_nodes
from tools.corpus_utils import page_files, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.structured_index_experiment import encoded, key
from tools.indexing import literal_target_links as literal
from tools.indexing.parser_target_experiment import (
    build_header_series, locate_header, locate_number, marker_options, volume_bindings,
)
from tools.indexing.parser_target_notation import interpret
from tools.indexing.page_line_candidates import interpret_page_line_entry
from tools.indexing.tome_candidates import parse_tome_candidates
from tools.indexing.po_locator_candidates import parse_po_locators, locate_po_candidates

VERSION = 'global-target-survey-v2'
SIMPLE_LIST = re.compile(r'\s*[1-9][0-9]{0,3}(?:\s*[,;]\s*[1-9][0-9]{0,3})*\s*\.?\s*')
NUMBER = re.compile(r'(?<!\w)[1-9][0-9]{0,3}(?!\w)')
DEFAULT_RUNS = ['1dfc8043e93e40a1b773d32824d8364e', 'c111c87b1a2646509eaffc44d0192e60']
DEFAULT_PROFILES = ['pg069_internal_anchored_pilot.json', 'pl010_verona_lists_anchored_pilot.json',
                    'pg110_page_line_pilot.json', 'po022_bracketed_pilot.json']


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def load_pages(db, runs, structured, volume):
    """One explicit page version: later run wins, then consolidated selection."""
    selected, replacements = {}, Counter()
    with closing(readonly(db)) as con:
        for run_id in runs:
            state = con.execute('SELECT status FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if not state or state[0] != 'completed':
                raise ValueError(f'Parser run not completed: {run_id}')
            for row in con.execute('''SELECT p.source,p.physical,t.result_json FROM run_pages p
                JOIN tasks t USING(fingerprint) WHERE p.run_id=? AND p.volume=? ORDER BY p.source''', (run_id,volume)):
                source = str(Path(row['source']).resolve())
                if source in selected:
                    replacements['parser_page_override'] += 1
                result = json.loads(row['result_json'])
                selected[source] = {'page':result.get('page'), 'physical':row['physical'],
                                    'selection':run_id, 'task_error':result.get('error')}
    with closing(readonly(structured)) as con:
        for row in con.execute('SELECT path,physical,page_json FROM sources WHERE volume=? ORDER BY path', (volume,)):
            source = str(Path(row['path']).resolve())
            if source in selected:
                replacements['consolidated_page_override'] += 1
            selected[source] = {'page':json.loads(row['page_json']), 'physical':row['physical'],
                                'selection':'consolidated', 'task_error':None}
        claimed = {(r['path'],r['char_start'],r['char_end']) for r in con.execute('''
            SELECT s.path,r.char_start,r.char_end FROM reference_claims x
            JOIN reference_groups r USING(reference_id) JOIN units u USING(unit_id)
            JOIN sources s USING(source_id) WHERE s.volume=?''', (volume,))}
    return selected, claimed, replacements


def load_raw_pages(config, volume):
    from tools.indexing.reproducible_selection import selected_recipes, rebuild_page
    root=Path(config['root'])
    recipes=selected_recipes(Path(config['raw_selection']),root,[volume],
        Path(config['source_aliases']) if config.get('source_aliases') else None)
    selected,counts={},Counter()
    for recipe in recipes:
        record=rebuild_page((recipe,root,'current'))
        source=str((root/recipe.get('source_alias',{}).get('source',recipe['source'])).resolve())
        if source in selected:
            raise ValueError('Two recipes resolve to the same physical source: '+source)
        counts['raw_'+record['status']]+=1
        counts['raw_source_changed']+=bool(record.get('source_changed'))
        counts['raw_source_alias_applied']+=bool(record.get('source_alias'))
        selected[source]={'page':record['page'],'physical':recipe['physical'],
            'selection':'raw_recipe','task_error':record.get('error'),
            'replay_evidence':{k:v for k,v in record.items() if k!='page'}}
    return selected,set(),counts


def scan_headers(root, volume, out):
    files, markers, nodes, counts, checksums = {}, [], defaultdict(set), Counter(), {}
    with (out/'inventory.jsonl').open('w') as inventory:
        for physical, paths in sorted(page_files(root/volume/'text', suffixes=['.txt']).items()):
            for path in paths:
                path = path.resolve()
                counts['physical_files'] += 1
                try:
                    data = path.read_bytes(); checksum = digest(data); raw = data.decode('utf-8')
                except (OSError, UnicodeError) as exc:
                    counts['read_errors'] += 1
                    inventory.write(encoded({'path':str(path),'physical':physical,'error':str(exc)})+'\n')
                    continue
                checksums[str(path)] = checksum
                record = {'path':str(path),'physical':physical,'sha256':checksum,'volume':volume}
                inventory.write(encoded(record)+'\n')
                if len(paths)>1:
                    counts['duplicate_physical_files'] += 1
                    continue
                files[physical] = record
                found = blocks(raw)
                if not found:
                    counts['no_recognized_blocks'] += 1
                for block in found:
                    if block['kind'] not in {'cabecalho','rodape'}:
                        continue
                    for match in NUMBER.finditer(block['body']):
                        number = int(match[0]); a = block['body_start']+match.start(); b = a+len(match[0])
                        # Numbers inside nested XML attributes are not printed text.
                        if block['body'][:match.start()].rfind('<') > block['body'][:match.start()].rfind('>'):
                            continue
                        marker = {**record,'number':number,'char_start':a,'char_end':b,
                                  'raw':raw[a:b],'zone':block['kind'],
                                  'excerpt':raw[max(block['body_start'],a-70):min(block['body_end'],b+70)]}
                        marker['id'] = key(str(path),checksum,a,b)
                        markers.append(marker); nodes[number].add(physical)
    support = supported_sequence_nodes(nodes)
    by_number = defaultdict(dict)
    with (out/'header_markers.jsonl').open('w') as handle:
        for marker in markers:
            marker['sequence_support'] = support.get((marker['number'],marker['physical']))
            handle.write(encoded(marker)+'\n')
            by_number[marker['number']][marker['physical']] = {
                **files[marker['physical']], 'sequence_support': marker['sequence_support'],
                'candidate_basis': 'supported_header' if marker['sequence_support'] else 'header_without_sequence'}
    counts.update(header_markers=len(markers), supported_header_nodes=len(support))
    return by_number, checksums, counts


def prepare_profiles(paths, volume, root):
    profiles, errors = [], []
    for path in paths:
        profile = json.loads(Path(path).read_text())
        if profile['volume'] != volume:
            continue
        item = {'profile':profile,'path':str(path),'series':{},'sources':{}}
        try:
            proof = profile['notice']; notice_path = root/proof['path']; data = notice_path.read_bytes()
            if digest(data)!=proof['sha256'] or proof['quote'] not in data.decode():
                raise ValueError('Editorial notice changed')
            item['sources'][str(notice_path.resolve())] = digest(data)
            specs = profile['series'] if isinstance(profile['series'],list) else [profile['series']]
            bindings, sources = volume_bindings(profile,specs,root)
            item['sources'].update(sources); item['bindings'] = bindings
            for spec in specs:
                duplicates = [n for n,p in page_files(root/spec['volume']/'text',suffixes=['.txt']).items()
                              if len(p)>1 and spec['physical_start']<=n<=spec['physical_end']]
                if duplicates:
                    raise ValueError('Duplicate physical files in scoped map')
                method = spec.get('method',profile.get('method'))
                series = build_series(spec,root) if method=='body_sequence' else build_header_series(spec,root)
                series['method'] = method
                item['series'][spec.get('key','default')] = series
                item['sources'].update(dict(series['sources']))
        except (ValueError,OSError,UnicodeError) as exc:
            item['error'] = str(exc); errors.append({'profile':str(path),'error':str(exc)})
        profiles.append(item)
    return profiles, errors


def profile_references(entry, profile):
    if profile['notation']=='plain_page_list':
        raw = entry.get('references_raw') or ''
        if entry.get('entry_type')!='material' or not SIMPLE_LIST.fullmatch(raw):
            return None
        return [{'number':int(m[0]),'series_key':'default','ref_raw':m[0],
                 'source_span':[entry['reference_span']['char_start']+m.start(),
                                entry['reference_span']['char_start']+m.end()]} for m in NUMBER.finditer(raw)]
    result = interpret(entry,profile['notation'])
    if not result and entry.get('entry_type') == 'material':
        if profile['notation'] == 'page_line_leo_simple':
            result = interpret_page_line_entry(entry)
        elif profile['notation'] in {'explicit_tome_list', 'explicit_tome_page'}:
            result = parse_tome_candidates(entry)
        elif profile['notation'] == 'po_page_parts' and entry.get('reference_span'):
            parsed = parse_po_locators(entry['references_raw'], 'page_subscript',
                                      base=entry['reference_span']['char_start'])
            return [{'number': r['page'], 'series_key': 'bracketed', 'ref_raw': r['raw'],
                     'source_span': [r['char_start'], r['char_end']],
                     'detail_raw': r['detail_raw'], 'uncertainties': r['warnings'],
                     'grammar_version': parsed['version'], 'parser_residuals': parsed['residuals']}
                    for r in parsed['locators']] or None
    if result:
        for ref in result['interpreted_references']:
            ref.setdefault('owner_raw', ref.get('query_text', result.get('query_text', entry.get('text_raw', ''))))
            ref.setdefault('grammar_version', result.get('grammar_version', result.get('parser', 'scoped_notation')))
    return result['interpreted_references'] if result else None


def scoped_location(ref, item):
    series = item['series'].get(ref['series_key'])
    if series is None:
        return {'status':'namespace_not_mapped','files':[]}
    if series['method']=='bracketed_header_sequence':
        return locate_po_candidates(series,ref['number'])
    if not ref.get('number') or ref['number'] < 0:
        return {'status': 'invalid_numeric_locator', 'files': []}
    end = ref.get('range_end')
    if end is not None and end < ref['number']:
        result = {'status': 'reversed_numeric_range', 'files': []}
    else:
        result = locate_number(series,ref['number'],range_end=end)
    # A start-page candidate does not establish coverage of the full range.
    if not result['files']:
        starts = marker_options(series,ref['number'])
        files = {m['file']:m for m in starts}
        if not files:
            files = {series['pages'][n]['file']: {
                        'physical': n, 'file': series['pages'][n]['file'],
                        'sha256': series['pages'][n]['sha256'],
                        'candidate_basis': 'scoped_body_marker_without_sequence',
                        'number_evidence': series['pages'][n].get('occurrences', {}).get(ref['number'], [])}
                     for n in sorted(series['nodes'].get(ref['number'], ()))}
        if files:
            result.update(previous_status=result['status'],
                          status='marker_file_candidate' if len(files)==1 else 'alternative_marker_candidates',
                          files=list(files.values()),extent_status='range_start_only' if ref.get('range_end') else 'not_bounded')
    return result


def groups(entry):
    if 'reference_groups' in entry:
        return entry['reference_groups']
    if entry.get('references_raw') and entry.get('reference_span'):
        return [{'raw':entry['references_raw'],**entry['reference_span'],
                 'owner_text_raw':entry.get('text_raw','')}]
    return []


def numeric_screen(raw, offset, by_number, source, excluded_sources=()):
    if not SIMPLE_LIST.fullmatch(raw):
        return None
    return [{'number':int(m[0]),'raw':m[0],'char_start':offset+m.start(),'char_end':offset+m.end(),
             'files':[f for _,f in sorted(by_number.get(int(m[0]),{}).items())
                      if f['path']!=source and f['path'] not in excluded_sources]}
            for m in NUMBER.finditer(raw)]


def survey_volume(config, volume):
    root,out = Path(config['root']),Path(config['out'])/'volumes'/volume
    out.mkdir(parents=True)
    if config.get('raw_selection'):
        selected, claimed, replaced = load_raw_pages(config,volume)
        # Preserve units without numeric references and parser residuals as well.
        with (out/'raw_pages.jsonl').open('w') as handle:
            for source,selection in sorted(selected.items()):
                handle.write(encoded({'source':source,**selection})+'\n')
    else:
        selected, claimed, replaced = load_pages(config['experiment_db'],config['parser_runs'],config['structured_db'],volume)
    by_number, checksums, counts = scan_headers(root,volume,out)
    profiles, errors = prepare_profiles(config['profiles'],volume,root)
    literal_groups = {}
    literal_profile = json.loads(Path(config['literal_profile']).read_text())
    index_scopes=[(p['profile']['index_physical_start'],p['profile']['index_physical_end']) for p in profiles]
    if literal_profile['index_scope']['volume']==volume:
        index_scopes.append((literal_profile['index_scope']['first'],literal_profile['index_scope']['last']))
    excluded_sources=set(selected)
    for source in checksums:
        physical=page_number(Path(source))
        if any(first<=physical<=last for first,last in index_scopes):
            excluded_sources.add(source)
    counts['documented_index_sources_excluded_from_numeric_screen']=len(excluded_sources-set(selected))
    if literal_profile['index_scope']['volume']==volume and config.get('literal_proposals'):
        try:
            literal.run(config['literal_proposals'],config['literal_profile'],root,out/'literal_profile')
            for line in (out/'literal_profile/links.jsonl').read_text().splitlines():
                row=json.loads(line)
                literal_groups.setdefault((row['source'],*row['group_span']),[]).append(row)
            pages_by_source={}
            for line in Path(config['literal_proposals']).read_text().splitlines():
                proposal=json.loads(line)
                if proposal['volume']!=volume:
                    continue
                source=proposal['source']
                page=pages_by_source.setdefault(source,{'sha256':proposal['source_sha256'],'entries':[]})
                page['entries'].append(proposal)
            for source,page in pages_by_source.items():
                counts['literal_page_override']+=source in selected
                selected[source]={'page':page,'physical':page_number(Path(source)),
                                  'selection':'literal_subdivision_selected','task_error':None}
            counts['literal_pages_selected']=len(pages_by_source)
        except (ValueError,OSError,UnicodeError) as exc:
            errors.append({'profile':config['literal_profile'],'error':str(exc)})
    counts.update(replaced); counts['selected_source_pages']=len(selected)
    statuses, group_statuses, selections = Counter(), Counter(), Counter()
    start=time.monotonic()
    with (out/'groups.jsonl').open('w') as gh, (out/'occurrences.jsonl').open('w') as oh, (out/'sources.jsonl').open('w') as sh:
        def emit_group(record, refs):
            group_statuses[record['status']]+=1
            gh.write(encoded(record)+'\n')
            for i,ref in enumerate(refs):
                row={**ref,'id':key(record['id'],i),'group_id':record['id'],
                     'source':record['source'],'source_sha256':record['source_sha256'],
                     'verified':False,'import_ready':False}
                statuses[row['status']]+=1
                if row.get('files'):
                    counts[row['tier']+'_with_files']+=1
                    counts[row['tier']+'_files_'+('1' if len(row['files'])==1 else '2_4' if len(row['files'])<=4 else '5_20' if len(row['files'])<=20 else 'over20')]+=1
                oh.write(encoded(row)+'\n')
        for source,selection in sorted(selected.items()):
            page=selection['page']; selections[selection['selection']]+=1
            valid=bool(page) and checksums.get(source)==page.get('sha256')
            state='current' if valid else 'missing_or_changed_source' if page else 'parser_error'
            sh.write(encoded({'source':source,'selection':selection['selection'],'state':state,
                              'expected_sha256':page.get('sha256') if page else None,
                              'actual_sha256':checksums.get(source),'error':selection['task_error'],
                              'replay_evidence':selection.get('replay_evidence')})+'\n')
            counts['source_'+state]+=1
            if not page:
                continue
            raw=Path(source).read_text() if valid else ''
            physical=selection['physical']
            scoped=[p for p in profiles if p['profile']['index_physical_start']<=physical<=p['profile']['index_physical_end']]
            if len(scoped)>1:
                raise ValueError('Overlapping explicit profiles')
            item=scoped[0] if scoped else None
            for entry in page['entries']:
                counts['units']+=1
                gs=groups(entry)
                if not gs:
                    counts['units_without_reference_groups']+=1
                entry_valid=valid and raw[entry['char_start']:entry['char_end']]==entry['entry_raw']
                interpreted=None
                if entry_valid and item and not item.get('error'):
                    interpreted=profile_references(entry,item['profile'])
                for group in gs:
                    a,b=group['char_start'],group['char_end']
                    record={'id':key(source,page['sha256'],a,b),'source':source,'source_sha256':page['sha256'],
                            'volume':volume,'physical':physical,'raw':group['raw'],'char_start':a,'char_end':b,
                            'owner_raw':group.get('owner_text_raw',entry.get('text_raw','')),
                            'flags':entry.get('flags',[]),'selection':selection['selection'],
                            'status':'notation_pending','source_assignment_status':'not_certified'}
                    refs=[]
                    if not entry_valid or raw[a:b]!=group['raw'] or not entry['char_start']<=a<b<=entry['char_end']:
                        record['status']='source_or_span_invalid'
                    elif (source,a,b) in claimed:
                        record['status']='logical_continuation_deferred'
                    elif (source,a,b) in literal_groups:
                        record['status']='profile_interpreted'
                        refs=[{**r,'tier':'documented_profile','profile':config['literal_profile']}
                              for r in literal_groups[(source,a,b)] if r['reference'] is not None]
                        if not refs:
                            record['status']='profile_notation_pending'
                    elif volume==literal_profile['index_scope']['volume'] and literal_profile['index_scope']['first']<=physical<=literal_profile['index_scope']['last']:
                        record['status']='literal_profile_scope_pending'
                    elif item:
                        record['profile']=item['path']
                        if item.get('error'):
                            record['status']='profile_evidence_invalid'
                        elif interpreted:
                            # A scoped interpretation may cover the whole suffix, including a tome
                            # before the legacy parser's reference_span. Emit it once per entry.
                            if group is gs[0]:
                                refs=[{**ref,**scoped_location(ref,item),'tier':'documented_profile',
                                       'profile':item['path']} for ref in interpreted]
                                record['status']='profile_interpreted'
                            else:
                                record['status']='profile_additional_group_pending'
                        else:
                            record['status']='profile_notation_pending'
                    elif entry.get('entry_type')=='cross_reference':
                        record['status']='cross_reference_not_material'
                    else:
                        screened=numeric_screen(group['raw'],a,by_number,source,excluded_sources)
                        if screened is not None:
                            record['status']='numeric_screen_only'
                            refs=[{**r,'tier':'unmapped_namespace_screen',
                                   'status':('header_coincidence' if any(f.get('sequence_support') for f in r['files'])
                                             else 'header_without_sequence_candidate') if r['files'] else 'no_header_coincidence',
                                   'namespace_status':'unmapped','number_role':'uninterpreted_numeric_token'} for r in screened]
                            if entry.get('flags'):
                                record['warnings_preserved'] = entry['flags']
                        elif entry.get('flags'):
                            record['status']='segmentation_or_notation_review'
                    emit_group(record,refs)
    for item in profiles:
        if not item.get('error'):
            for path,checksum in item['sources'].items():
                if digest(Path(path).read_bytes())!=checksum:
                    raise ValueError('Scoped source changed during run')
    # Recheck reference sources; full target inventories record the bytes observed in this run.
    for source in selected:
        if source in checksums and digest(Path(source).read_bytes())!=checksums[source]:
            raise ValueError('Reference source changed during run')
    counts['profile_errors']=len(errors)
    write_json(out/'profiles.json',[{k:v for k,v in p.items() if k!='series'} for p in profiles])
    summary={'volume':volume,'status':'completed','pid':os.getpid(),'counts':dict(counts),
             'group_statuses':dict(group_statuses),'occurrence_statuses':dict(statuses),
             'selections':dict(selections),'profile_errors':errors,'seconds_after_scan':time.monotonic()-start}
    write_json(out/'summary.json',summary)
    return summary


def aggregate(out, config, summaries):
    totals, groups_count, occurrences = Counter(),Counter(),Counter()
    with closing(sqlite3.connect(out/'survey.sqlite')) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE volumes(volume TEXT PRIMARY KEY,status TEXT,evidence_json TEXT);
        CREATE TABLE groups(id TEXT PRIMARY KEY,volume TEXT REFERENCES volumes,source TEXT,status TEXT,evidence_json TEXT);
        CREATE TABLE occurrences(id TEXT PRIMARY KEY,group_id TEXT REFERENCES groups,tier TEXT,status TEXT,evidence_json TEXT);
        CREATE TABLE candidates(occurrence_id TEXT REFERENCES occurrences,path TEXT,physical INTEGER,PRIMARY KEY(occurrence_id,path));
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
        for summary in sorted(summaries,key=lambda s:s['volume']):
            volume=summary['volume']
            with con:
                con.execute('INSERT INTO volumes VALUES(?,?,?)',(volume,summary['status'],encoded(summary)))
                if summary['status']!='completed':
                    continue
                totals.update(summary['counts']);groups_count.update(summary['group_statuses']);occurrences.update(summary['occurrence_statuses'])
                for name in ['groups','occurrences']:
                    with (out/'volumes'/volume/(name+'.jsonl')).open() as handle:
                        for line in handle:
                            r=json.loads(line)
                            if name=='groups':
                                con.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(r['id'],volume,r['source'],r['status'],line.strip()))
                            else:
                                con.execute('INSERT INTO occurrences VALUES(?,?,?,?,?)',(r['id'],r['group_id'],r['tier'],r['status'],line.strip()))
                                con.executemany('INSERT INTO candidates VALUES(?,?,?)',[(r['id'],f.get('path',f.get('file')),f['physical']) for f in r.get('files',[])])
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Invalid output database')
        summary={'version':VERSION,'status':'completed' if all(s['status']=='completed' for s in summaries) else 'partial',
                 'volumes_in_corpus':len(summaries),'completed_volumes':sum(s['status']=='completed' for s in summaries),
                 'volumes_with_reference_groups':sum(bool(s.get('group_statuses')) for s in summaries),
                 'volumes_without_selected_extraction':sum(s.get('counts',{}).get('selected_source_pages')==0 for s in summaries),
                 'worker_processes_used':len({s['pid'] for s in summaries if 'pid' in s}),
                 'counts':dict(totals),'group_statuses':dict(groups_count),'occurrence_statuses':dict(occurrences),
                 'verified_links':0,'source_updates':0,'production_updates':0,'inference_calls':0,
                 'warning':'Header coincidences have no confirmed namespace and are not resolved links.'}
        if config.get('raw_selection'):
            summary['extraction_source']='current_raw_ocr_and_selection_recipe'
            summary['omitted_legacy_layers']=config['omitted_legacy_layers']
            summary['entire_historical_catalog_rebuilt']=False
            if totals['raw_error'] or totals['source_missing_or_changed_source']:
                summary['status']='partial'
        with con:
            con.executemany('INSERT INTO metadata VALUES(?,?)',[('config',encoded(config)),('summary',encoded(summary))])
    write_json(out/'summary.json',summary)
    with (out/'by_volume.csv').open('w') as handle:
        fields=['volume','status','physical_files','selected_source_pages','units','groups','documented_profile_with_files','unmapped_namespace_screen_with_files']
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for s in sorted(summaries,key=lambda s:s['volume']):
            c=s.get('counts',{})
            writer.writerow({**{k:c.get(k,0) for k in fields},'volume':s['volume'],'status':s['status'],'groups':sum(s.get('group_statuses',{}).values())})
    (out/'report.md').write_text('# Localização global experimental\n\n'+
        'Mapas documentados e coincidências numéricas são camadas separadas. Nenhum link foi certificado.\n\n'+
        '```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n\n'+
        'Por volume: by_volume.csv. Evidências: volumes/*/*.jsonl; consulta relacional: survey.sqlite (WAL).\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('teste'))
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--experiment-db',type=Path)
    inputs.add_argument('--raw-selection',type=Path,help='Re-extract selected raw OCR directly; no old parser or structured database')
    parser.add_argument('--source-aliases',type=Path,help='Documented source aliases for --raw-selection')
    parser.add_argument('--structured-db',type=Path)
    parser.add_argument('--parser-run',action='append')
    parser.add_argument('--profile',type=Path,action='append')
    parser.add_argument('--literal-profile',type=Path,default=Path('docs/examples/literal_target_links/pg104_photius_ab.json'))
    parser.add_argument('--literal-proposals',type=Path)
    parser.add_argument('--volume',action='append')
    parser.add_argument('--workers',type=int,default=20)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.workers<1:
        parser.error('workers must be positive')
    if args.raw_selection and (args.structured_db or args.parser_run):
        parser.error('--raw-selection cannot reuse structured DB or parser runs')
    if args.source_aliases and not args.raw_selection:
        parser.error('--source-aliases requires --raw-selection')
    root=args.root.resolve();out=args.out.resolve()
    if not args.raw_selection:
        args.experiment_db=args.experiment_db or Path('data/index_entry_parser/experiments.sqlite')
        args.structured_db=args.structured_db or Path('data/index_entry_parser/recovered_consolidation_003/structured/index.sqlite')
        args.literal_proposals=args.literal_proposals or Path('data/index_entry_parser/subdivision_clauses_004/proposals.jsonl')
    profile_root=Path('docs/fixtures/index_rebuild/historical_assertions') if args.raw_selection else Path('data/index_entry_parser/profiles')
    profiles=args.profile or [profile_root/p for p in DEFAULT_PROFILES]
    database_inputs=[p for p in [args.experiment_db,args.structured_db] if p]
    file_inputs=[p for p in [*profiles,args.literal_profile,args.literal_proposals,args.raw_selection,args.source_aliases] if p]
    config={'version':VERSION,'root':str(root),'out':str(out),'workers':args.workers,
            'experiment_db':str(args.experiment_db.resolve()) if args.experiment_db else None,
            'structured_db':str(args.structured_db.resolve()) if args.structured_db else None,
            'raw_selection':str(args.raw_selection.resolve()) if args.raw_selection else None,
            'source_aliases':str(args.source_aliases.resolve()) if args.source_aliases else None,
            'literal_profile':str(args.literal_profile.resolve()),
            'literal_proposals':str(args.literal_proposals.resolve()) if args.literal_proposals else None,
            'parser_runs':args.parser_run or DEFAULT_RUNS,'profiles':[str(p.resolve()) for p in profiles],
            'code_sha256':digest(Path(__file__).read_bytes()),
            'source_db_stats':{str(p.resolve()):{'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in database_inputs},
            'selection_policy':('frozen raw selection; recompute current OCR; no legacy overrides' if args.raw_selection
                else 'later parser run replaces page; consolidated replaces both; no union of page versions'),
            'omitted_legacy_layers':(['structured_overrides','reviewed_continuations','historical_ocr_repairs']
                + ([] if args.literal_proposals else ['literal_subdivision_proposals'])) if args.raw_selection else [],
            'input_files':{str(p.resolve()):digest(p.read_bytes()) for p in file_inputs},
            'namespace_policy':'scoped profile or diagnostic header coincidence; no implicit cross-volume mapping'}
    volumes=sorted(p.name for p in root.iterdir() if p.name.startswith(('PG','PL','PO','PS')) and (p/'text').is_dir()
                   and (not args.volume or p.name in args.volume))
    if args.volume and set(args.volume)-set(volumes):
        parser.error('Unknown requested volume')
    out.mkdir(parents=True,exist_ok=False);write_json(out/'config.json',config)
    summaries=[]
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        jobs={pool.submit(survey_volume,config,v):v for v in volumes}
        for future in as_completed(jobs):
            volume=jobs[future]
            try:
                result=future.result()
            except Exception as exc:
                result={'volume':volume,'status':'error','error':f'{type(exc).__name__}: {exc}'}
                write_json(out/'volumes'/volume/'error.json',result)
            summaries.append(result)
            print(encoded({'completed':len(summaries),'total':len(volumes),'volume':volume,
                           'status':result['status'],'error':result.get('error')}),flush=True)
    changed=[p for p,v in config['source_db_stats'].items() if Path(p).stat().st_size!=v['size'] or Path(p).stat().st_mtime_ns!=v['mtime_ns']]
    config['source_databases_changed_during_run']=changed
    write_json(out/'config.json',config)
    print(json.dumps(aggregate(out,config,summaries),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

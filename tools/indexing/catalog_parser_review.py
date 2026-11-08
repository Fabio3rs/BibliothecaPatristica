"""Review existing catalog occurrences against OCR; no canonical writes or inference."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.line_entry_recovery import repair_line_owner
from tools.indexing.roman_owner_context import repair_roman_owner

VERSION = 'catalog-parser-review-v1'
BOUNDARY = re.compile(r'\d+[.;]\s*\n\s*[^\W\d_]')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def signals(group):
    owner, reference = group.get('owner_raw', ''), group.get('raw', '')
    flags = []
    if re.search(r'\b[IVXLCDM]+\s*[,;.]?\s*$', owner) and re.match(r'\s*\d', reference):
        flags.append('roman_context_in_owner_not_reference')
    boundaries = len(BOUNDARY.findall(owner))
    if boundaries:
        flags.append('multiple_entry_like_lines_in_owner' if boundaries >= 2 else 'single_entry_like_line_in_owner')
    if re.search(r'\d+\s*[,;]?\s*$', owner):
        flags.append('numeric_tail_in_owner')
    if len(owner) > 500:
        flags.append('long_owner_review')
    if len(re.findall(r'\d+[.;]\s+[^\W\d_]', owner)) >= 3:
        flags.append('multiple_numeric_clauses_in_owner')
    if re.search(r'<[^>]+>', owner):
        flags.append('markup_in_owner')
    return flags


def review_source(task):
    source, groups = task
    try:
        raw = Path(source).read_text()
    except (OSError, UnicodeError) as exc:
        return {'source': source, 'error': str(exc), 'groups': len(groups), 'findings': [],
                'span_states': {'source_unavailable': len(groups)}}
    checksum = hashlib.sha256(raw.encode()).hexdigest()
    span_states = Counter()
    findings = []
    for group in groups:
        start, end = group.get('char_start'), group.get('char_end')
        matches = (isinstance(start, int) and isinstance(end, int) and
                   0 <= start < end <= len(raw) and raw[start:end] == group['raw'])
        state = ('exact_reference_span' if group.get('source_sha256') == checksum else
                 'source_changed_local_reference_matches') if matches else 'reference_span_mismatch'
        span_states[state] += 1
        flags = signals(group)
        if not flags and matches:
            continue
        finding = {'group_id': group['id'], 'volume': group['volume'], 'source': source,
                   'physical': group['physical'], 'source_sha256': checksum, 'span_state': state,
                   'owner_raw': group.get('owner_raw', ''), 'reference_raw': group['raw'],
                   'reference_span': [start, end], 'signals': flags, 'parsers': {},
                   'repair_diagnostics': []}
        if matches and 'roman_context_in_owner_not_reference' in flags:
            proposal = repair_roman_owner(raw, group)
            finding['parsers']['roman'] = proposal
            finding['repair_diagnostics'].append('roman_context_in_owner_not_reference')
        if matches and any(f in flags for f in ('multiple_entry_like_lines_in_owner', 'single_entry_like_line_in_owner')):
            from tools.indexing.line_recovery_eligibility import assess_line_recovery
            proposal = repair_line_owner(raw, group)
            assessment = assess_line_recovery(group, proposal)
            finding['parsers']['lines'] = {'proposal': proposal, 'assessment': assessment}
            if assessment['eligible'] and not finding['repair_diagnostics']:
                finding['repair_diagnostics'].append('multiple_entry_like_lines_in_owner')
        if matches and 'numeric_tail_in_owner' in flags:
            from tools.indexing.numeric_owner_context import repair_numeric_owner
            proposal = repair_numeric_owner(raw, group)
            finding['parsers']['numeric'] = proposal
            # Keep this family's alternatives in the audit until integrated with
            # occurrence-level list splitting; do not duplicate prefix references.
        findings.append(finding)
    return {'source': source, 'groups': len(groups), 'sha256': checksum,
            'span_states': dict(span_states), 'findings': findings}


def run(catalog, survey, out, workers=20, volumes=(), previous=None):
    if workers < 1:
        raise ValueError('workers must be positive')
    catalog, survey, out = map(Path, (catalog, survey, out))
    items = defaultdict(list)
    catalog_volumes, catalog_sources = set(), set()
    with closing(readonly(catalog)) as db:
        for row in db.execute('SELECT id,group_id,volume,source,state FROM items ORDER BY id'):
            if volumes and row['volume'] not in volumes:
                continue
            items[row['group_id']].append(dict(row))
            catalog_volumes.add(row['volume']); catalog_sources.add(row['source'])
    by_source = defaultdict(list)
    found = set()
    with closing(readonly(survey)) as db:
        for ident, evidence in db.execute('SELECT id,evidence_json FROM groups ORDER BY id'):
            if ident not in items:
                continue
            group = json.loads(evidence)
            by_source[group['source']].append(group)
            found.add(ident)
    previous_ids = set()
    if previous:
        with closing(readonly(previous)) as db:
            previous_ids = {r[0] for r in db.execute('SELECT item_id FROM item_replacements')}
    out.mkdir(parents=True, exist_ok=False)
    totals, states, parser_counts, by_volume = Counter(), Counter(), Counter(), defaultdict(Counter)
    source_manifest, errors, selected_ids = [], [], set()
    with sqlite3.connect(out/'audit.sqlite') as db, (out/'findings.jsonl').open('w') as findings_file, (out/'occurrences.jsonl').open('w') as occurrences:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE findings(group_id TEXT PRIMARY KEY,volume TEXT,source TEXT,evidence_json TEXT);
            CREATE TABLE sources(source TEXT PRIMARY KEY,groups_count INTEGER,sha256 TEXT,error TEXT);
            CREATE TABLE missing_groups(group_id TEXT PRIMARY KEY,item_count INTEGER);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for n, result in enumerate(pool.map(review_source, sorted(by_source.items()), chunksize=1), 1):
                states.update(result['span_states'])
                source_manifest.append({k: result.get(k) for k in ('source', 'sha256', 'groups', 'error')})
                db.execute('INSERT INTO sources VALUES(?,?,?,?)', (result['source'], result['groups'], result.get('sha256'), result.get('error')))
                if result.get('error'):
                    errors.append({'source': result['source'], 'error': result['error']})
                for finding in result['findings']:
                    rows = items[finding['group_id']]
                    finding['item_ids'] = [r['id'] for r in rows]
                    finding['catalog_items_with_destinations'] = sum(r['state']=='with_destinations' for r in rows)
                    totals['flagged_groups'] += 1
                    totals['flagged_items'] += len(rows)
                    for flag in finding['signals']:
                        totals['groups:'+flag] += 1
                        totals['items:'+flag] += len(rows)
                        by_volume[finding['volume']][flag] += 1
                    for name, proposal in finding['parsers'].items():
                        status = proposal['proposal']['status'] if name=='lines' else proposal['status']
                        parser_counts[name+':'+status] += 1
                        if name=='lines':
                            assessment=proposal['assessment']
                            parser_counts['lines:eligible' if assessment['eligible'] else 'lines:review_only'] += 1
                    for row in rows:
                        if finding['repair_diagnostics'] and row['source']==finding['source']:
                            selected_ids.add(row['id'])
                            occurrences.write(encoded({'item_id': row['id'], 'group_id': finding['group_id'],
                                'extraction_diagnostics': finding['repair_diagnostics']})+'\n')
                    evidence=encoded(finding)
                    findings_file.write(evidence+'\n')
                    db.execute('INSERT INTO findings VALUES(?,?,?,?)', (finding['group_id'],finding['volume'],finding['source'],evidence))
                if n % 100 == 0:
                    db.commit()
                    print(encoded({'progress': True, 'source_pages': n, 'total_pages': len(by_source)}), flush=True)
        for ident in sorted(set(items)-found):
            db.execute('INSERT INTO missing_groups VALUES(?,?)', (ident, len(items[ident])))
        summary={'version':VERSION,'catalog':str(catalog.resolve()),'survey':str(survey.resolve()),
                 'catalog_items':sum(map(len,items.values())),'catalog_groups':len(items),
                 'catalog_volumes':len(catalog_volumes),'catalog_source_pages':len(catalog_sources),
                 'groups_with_source_evidence':len(found),'source_pages_examined':len(by_source),
                 'groups_missing_survey':len(set(items)-found),
                 'items_missing_survey':sum(len(items[i]) for i in set(items)-found),
                 'span_states':dict(states),'signals_nonexclusive':dict(totals),
                 'parser_group_statuses':dict(parser_counts),'by_volume':dict(by_volume),
                 'repair_queue_items':len(selected_ids),'previous_replacements_in_queue':len(selected_ids & previous_ids),
                 'previous_replacements_outside_queue':sorted(previous_ids-selected_ids),
                 'workers':workers,'errors':errors,'canonical_updates':0,'ocr_updates':0,'inference_calls':0,
                 'scope':'Existing catalog only. Signals are not confirmed errors; excludes OCR omitted from the catalog.'}
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    dependencies=['catalog_parser_review.py','roman_owner_context.py','line_entry_recovery.py',
                  'line_recovery_eligibility.py','numeric_owner_context.py']
    manifest={'version':VERSION,'catalog':str(catalog.resolve()),'survey':str(survey.resolve()),
              'volumes':list(volumes),'workers':workers,'sources':source_manifest,
              'code_hashes':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in dependencies}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    report=['# Revisão global dos registros existentes','',
            f"{summary['catalog_items']} registros; {len(items)} grupos; {len(catalog_volumes)} volumes.",
            f"{len(by_source)} páginas consultadas; {summary['groups_missing_survey']} grupos sem evidência neste survey.",
            '', 'Sinais sobrepostos, não contagens de erros confirmados. Não mede omissões do OCR ou do catálogo.',
            '', '| Sinal | Grupos | Registros |','| --- | ---: | ---: |']
    report.extend(f'| {k[7:]} | {v} | {totals["items:"+k[7:]]} |' for k,v in totals.items() if k.startswith('groups:'))
    report.extend(['','## Parsers','',encoded(dict(parser_counts)), '',
                   'A fila occurrences.jsonl permite reaplicar repair_index_context.py. '
                   'O suplemento resultante deve substituir o anterior, não ser somado a ele. '
                   'Casos numéricos permanecem propostas em findings.jsonl, evitando duplicar referências da lista.',
                   '', '## Volumes com mais sinais','', '| Volume | Ocorrências de sinais |','| --- | ---: |'])
    report.extend(f'| {v} | {sum(c.values())} |' for v,c in sorted(by_volume.items(),key=lambda p:(-sum(p[1].values()),p[0]))[:30])
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=Path('data/index_catalog/search_candidates_002/search.sqlite'))
    parser.add_argument('--survey',type=Path,default=Path('data/index_entry_parser/global_targets_20261106_004/survey.sqlite'))
    parser.add_argument('--previous',type=Path,help='Optional prior supplement, used only for comparison statistics')
    parser.add_argument('--volume',action='append',default=[])
    parser.add_argument('--workers',type=int,default=20)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    summary=run(args.catalog,args.survey,args.out,args.workers,args.volume,args.previous)
    print(encoded({k:v for k,v in summary.items() if k!='by_volume'}))


if __name__=='__main__':
    main()

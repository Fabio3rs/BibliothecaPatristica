"""Mine literal locator templates and nearby editorial explanations, in staging."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_index_destination_gaps import notation_shape
from tools.corpus_utils import page_files, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.editorial_regions_experiment import fold, material_kind, numbering_notice
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.editorial_pattern_history import audit_history, extraction_diagnostics

VERSION = 'editorial-pattern-discovery-v1'
TOKEN = re.compile(r'<sub>.*?</sub>|[₀-₉]+|\d+|\b[IVXLCDM]+\b|\b[A-Da-d]\b|[^\W\d_]+|[^\w\s]', re.S)
UNITS = {
    'tome': r'\bTOM(?:UM|US|O|I|E|ES|OS)\b',
    'column': r'\bCOL(?:UMN\w*|ONN\w*)\b',
    'page': r'\bPAG(?:INA\w*|E|ES)\b',
    'line': r'\bLIN(?:EA\w*|E|ES)|\bLIGN\w*',
    'book': r'\bLIB(?:ER|RUM|RI|RO|ROS)\b|\bLIVRE\w*',
    'epistle': r'\bEPISTOL\w*|\bLETTRES\b',
    'chapter': r'\bCAPIT(?:UL\w*|A|IS)|\bCHAPITRE\w*',
    'note': r'\bADNOT\w*|\bANNOT\w*|\bNOTES\b',
    'year': r'\bANN(?:US|UM|I|OS|O)\b|\bANNEE\w*',
}
EDITION = re.compile(r'\b(?:FROBEN\w*|VERON\w*|SYLBURG\w*|SAVIL\w*|MOREL\w*|PETROPOL\w*|PARISIENS\w*|MIGNE|LEO|CEDREN\w*)\b')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def signature(raw):
    """Numbers become typed slots; punctuation and named qualifiers survive."""
    slots, template = [], []
    for match in TOKEN.finditer(raw):
        value = match[0]
        if value.startswith('<sub>') or re.fullmatch('[₀-₉]+', value):
            kind = 'SUB'
        elif value.isascii() and value.isdigit():
            kind = 'N'
        elif re.fullmatch('[IVXLCDM]+', value):
            kind = 'R'
        elif re.fullmatch('[A-Da-d]', value):
            kind = 'LETTER'
        else:
            template.append(value.casefold())
            continue
        slots.append({'kind': kind, 'raw': value, 'span': [match.start(), match.end()]})
        template.append(kind)
    return {'template': ' '.join(template), 'shape': notation_shape(raw), 'slots': slots}


def interpretations(raw):
    """Return alternatives, not a semantic decision from punctuation alone."""
    shape = notation_shape(raw)
    if shape == 'roman_and_arabic':
        return ['tome_and_page_or_column', 'book_and_internal_locator']
    if re.fullmatch(r'\s*\d+\s*,\s*\d+[. ]*', raw):
        return ['two_editorial_pages', 'page_and_line', 'chapter_and_verse_or_section']
    if shape == 'simple_numeric':
        return ['page_or_column', 'internal_number_or_chronological_year']
    if shape in {'ibid_only', 'ibid_mixed'}:
        return ['inherits_local_antecedent']
    if shape == 'letter_subdivision':
        return ['page_or_column_subdivision', 'letter_belongs_to_adjacent_clause']
    if shape == 'numeric_range':
        return ['range_in_namespace_not_yet_identified']
    return ['notation_requires_editorial_context']


def explain(text):
    """Recognize explanation language; ordinary references are not definitions."""
    t = fold(text)
    existing = numbering_notice(text)
    number_words = list(re.finditer(r'\b(?:NUMER(?:US|I|O|OS|UM|ALES)|CHIFFR\w*|CIFR\w*|NOTA ARITHMETICA|LETTRES|LITTERA)\b', t))
    verbs = list(re.finditer(r'\b(?:INDIC\w*|DESIGN\w*|DENOT\w*|SIGNIF\w*|SIGNAT|RENVO\w*|CORRESPOND\w*|REPRESENT\w*|EXPRIM\w*|REVOC\w*|RESPICI\w*)\b', t))
    def linked(n, v):
        a,b = sorted((n.start(),v.start()))
        between = re.sub(r'\b[IVXLCDM]+[.]', '', t[a:b])
        return b-a <= 110 and not re.search(r'[0-9.;!?]',between)
    nearby_predicate = any(linked(n,v) for n in number_words for v in verbs)
    chronology = bool(re.search(r'NUMERI.{0,100}ANN\w*.{0,150}ACCOMMOD',t))
    if not nearby_predicate and not chronology and existing in {None,'numbering_declaration_unclassified'}:
        return None
    roles = [name for name, pattern in UNITS.items() if re.search(pattern, t)]
    mappings = []
    for clause in re.split(r'[;!?]|,\s*(?=(?:NUMER|POSTERIOR|SECUND))', t):
        # One marker and one unit in a clause gives an explicit association.
        markers = [name for name, pat in [('roman',r'\bROMAN\w*'), ('arabic',r'\bARAB\w*'),
                   ('letter',r'\bLITTERA\b|\bLETTRES\b'), ('first',r'\bPREMIER\w*|\bPRIOR\b|\bPRIMUS\b'),
                   ('second',r'\bSECOND\w*|\bSECUNDUS\b|\bPOSTERIOR\b')] if re.search(pat,clause)]
        targets = [name for name, pat in UNITS.items() if re.search(pat,clause)]
        if len(targets) == 1:
            mappings.extend({'marker': m, 'unit': targets[0]} for m in markers)
    editions = sorted(set(EDITION.findall(t)))
    if not (roles or editions or existing):
        return None
    return {'units': roles, 'slot_mappings': mappings, 'edition_mentions': editions,
            'chronological_era_conversion_mentioned':chronology,
            'existing_namespace_hint': existing, 'status': 'explanation_candidate_scope_unverified'}


def page_evidence(path):
    data = path.read_bytes()
    raw = data.decode('utf-8')
    regions = blocks(raw)
    if not regions and '<' not in raw and '[bloco' not in raw:
        regions = [{'body':raw,'body_start':0,'body_end':len(raw),'kind':'plain_text'}]
    notices, titles = [], []
    for block in regions:
        body = block['body']
        for line in re.finditer(r'[^\n]+', body):
            kind = material_kind(line[0])
            if kind:
                titles.append({'span':[block['body_start']+line.start(),block['body_start']+line.end()],
                               'quote':line[0], 'kind':kind})
        # Bounded paragraph windows catch line-wrapped notices without borrowing
        # unrelated paragraphs or XML attributes as semantic evidence.
        for paragraph in re.finditer(r'\S(?:.*?)(?=\n\s*\n|\Z)', body, re.S):
            value = paragraph[0]
            anchors = re.finditer(r'\b(?:numer\w*|chiffr\w*|nota\s+arithmetica|revocatur|lettres|littera)\b', value, re.I)
            intervals = []
            for anchor in anchors:
                start, end = max(0,anchor.start()-100), min(len(value),anchor.end()+500)
                if intervals and start <= intervals[-1][1] and end-intervals[-1][0] <= 900:
                    intervals[-1][1] = end
                else:
                    intervals.append([start,end])
            for start,end in intervals:
                quote = value[start:end]
                meaning = explain(quote)
                if not meaning:
                    continue
                a = block['body_start']+paragraph.start()+start
                b = block['body_start']+paragraph.start()+end
                notices.append({'id':sha(str(path.resolve())+str(a)+str(b)+sha(raw)),
                                'source':str(path.resolve()), 'source_sha256':sha(raw),
                                'span':[a,b], 'quote':raw[a:b], 'block_kind':block['kind'], **meaning})
    return {'source':str(path.resolve()), 'sha256':sha(raw), 'raw':raw,
            'notices':notices, 'titles':titles, 'recognized_blocks':len(regions)}


def group_context(item, group):
    if not group or group['source'] != item['source']:
        return item['ref_raw'], None, ['group_missing_or_different_source']
    flags = []
    owner = group.get('owner_raw','')
    roman = re.search(r'\b([IVXLCDM]+)\s*[,;.]?\s*$',owner)
    if roman:
        flags.append('roman_suffix_in_owner_candidate')
    if re.search(r'\d',owner):
        flags.append('numbers_in_owner_may_be_previous_clauses')
    return group['raw'], roman[1] if roman else None, flags


def discover(pending, survey, root, out, volumes=(), neighbors=2,
             versions_db=None, history_max_pages=8, history_max_groups=5):
    pending, survey, root, out = map(Path,(pending,survey,root,out))
    if neighbors < 0 or neighbors > 10:
        raise ValueError('neighbors must be between 0 and 10')
    if history_max_pages < 1 or history_max_groups < 1:
        raise ValueError('History limits must be positive')
    if out.resolve() == root.resolve() or root.resolve() in out.resolve().parents:
        raise ValueError('Output must be outside the OCR corpus')
    items = [json.loads(line) for line in pending.open() if line.strip()]
    items = [r for r in items if not volumes or r['volume'] in volumes]
    if len({r['id'] for r in items}) != len(items):
        raise ValueError('Duplicate pending item IDs')
    wanted = {r['group_id'] for r in items}
    groups = {}
    with closing(readonly(survey)) as db:
        for ident, raw in db.execute('SELECT id,evidence_json FROM groups'):
            if ident in wanted:
                groups[ident] = json.loads(raw)
    out.mkdir(parents=True,exist_ok=False)
    by_source = defaultdict(list)
    for item in items:
        by_source[(item['volume'],item['source'])].append(item)
    files = {v:page_files(root/v/'text',suffixes=['.txt']) for v in {r['volume'] for r in items}}
    cache, errors, patterns, records, all_notices = {}, [], {}, [], {}
    totals = Counter()
    for (volume,source), rows in sorted(by_source.items()):
        physical = page_number(Path(source))
        paths = {Path(source)}
        if physical is not None:
            for n in range(max(1,physical-neighbors),physical+neighbors+1):
                paths.update(files[volume].get(n,[]))
        evidence = []
        for path in sorted(paths):
            key = str(path.resolve())
            if key not in cache:
                try:
                    cache[key] = page_evidence(path)
                except (OSError,UnicodeError) as exc:
                    cache[key] = None
                    errors.append({'source':key,'error':str(exc)})
            if cache[key]:
                evidence.append(cache[key])
        current = cache.get(str(Path(source).resolve()))
        notices = {n['id']:n for e in evidence for n in e['notices']}
        all_notices.update(notices)
        for item in rows:
            group = groups.get(item['group_id'])
            full, roman, warnings = group_context(item,group)
            sig, atom = signature(full), signature(item['ref_raw'])
            current_status = 'missing_source'
            if current:
                current_status = 'group_missing' if not group else 'source_changed'
                if group and current['sha256'] == group.get('source_sha256'):
                    a,b = group['char_start'],group['char_end']
                    current_status = ('current_span_matches' if 0<=a<b<=len(current['raw']) and
                                      current['raw'][a:b]==full else 'span_mismatch')
            ident = sha(encoded([volume,source,sig['template'],roman]))
            if ident not in patterns:
                patterns[ident] = {'id':ident,'volume':volume,'source':source,'physical':physical,
                    'template':sig['template'],'shape':sig['shape'],'owner_roman_suffix':roman,
                    'interpretation_candidates':interpretations(full),'item_ids':[], 'group_ids':set(),
                    'examples':[], 'causes':Counter(),'source_states':Counter(),'flags':Counter(),
                    'notice_ids':sorted(notices),'slot_values':defaultdict(set),
                    'titles_on_source':current['titles'] if current else [],
                    'scope':'source_page_and_literal_template_not_confirmed_section',
                    'status':'searchable_pattern_candidate_not_destination_mapping'}
            pattern = patterns[ident]
            pattern['item_ids'].append(item['id']);pattern['group_ids'].add(item['group_id'])
            pattern['causes'][item['cause']]+=1;pattern['source_states'][current_status]+=1
            pattern['flags'].update(warnings)
            if len(pattern['examples']) < 3:
                pattern['examples'].append({'item_id':item['id'],'group_id':item['group_id'],
                    'label':item['label'],'item_ref_raw':item['ref_raw'],'group_ref_raw':full})
            for i,slot in enumerate(sig['slots']):
                pattern['slot_values'][i].add(slot['raw'])
            richer = atom['template'] != sig['template']
            diagnostics = extraction_diagnostics(group, current['raw']) if current else ['source_unavailable']
            totals['richer_group_than_item'] += richer
            totals[current_status] += 1
            records.append({'item_id':item['id'],'group_id':item['group_id'],'pattern_id':ident,
                'item_signature':atom,'group_signature':sig,'group_reference_raw':full,
                'richer_group_than_item':richer,'source_status':current_status,'warnings':warnings,
                'extraction_diagnostics':diagnostics})
    final = []
    for pattern in patterns.values():
        pattern['item_count'] = len(pattern['item_ids'])
        pattern['distinct_group_count'] = len(pattern['group_ids'])
        pattern['group_ids'] = sorted(pattern['group_ids'])
        pattern['slot_statistics'] = []
        for index,values in pattern.pop('slot_values').items():
            numbers = sorted(int(v) for v in values if re.fullmatch(r'[0-9]+',v))
            pattern['slot_statistics'].append({'position':index,'distinct_values':len(values),
                'examples':sorted(values)[:8],'minimum':min(numbers) if numbers else None,
                'maximum':max(numbers) if numbers else None})
        # Repetition is counted by source group, never by alternative interpretations.
        pattern['repeated_pattern'] = pattern['distinct_group_count'] >= 3
        final.append(pattern)
    final.sort(key=lambda p:(-p['item_count'],p['id']))
    families = defaultdict(lambda: {'items':0,'patterns':0,'groups':set(),'volumes':set()})
    for pattern in final:
        family = families[pattern['template']]
        family['items'] += pattern['item_count'];family['patterns'] += 1
        family['groups'].update(pattern['group_ids']);family['volumes'].add(pattern['volume'])
    families = [{'template':k,'items':v['items'],'patterns':v['patterns'],
                 'distinct_groups':len(v['groups']),'volumes':len(v['volumes'])} for k,v in families.items()]
    families.sort(key=lambda r:(-r['items'],r['template']))
    history = audit_history(final,groups,cache,versions_db,root,out,history_max_pages,history_max_groups) if versions_db else None
    summary = {'version':VERSION,'items':len(items),'distinct_groups':len(wanted),
        'volumes':len(files),'source_pages':len(by_source),'context_files_read':sum(v is not None for v in cache.values()),
        'patterns':len(final),'templates':len(families),'repeated_patterns':sum(p['repeated_pattern'] for p in final),
        'notice_candidates':len(all_notices),'items_with_nearby_notice':sum(p['item_count'] for p in final if p['notice_ids']),
        'extraction_diagnostics_nonexclusive':dict(Counter(d for r in records for d in r['extraction_diagnostics'])),
        'history':{'pages':len(history['pages']), 'counts':history['counts'], 'selection':history['selection']} if history else None,
        'counts':dict(totals),'errors':errors,'new_destination_links':0,'canonical_updates':0,'inference_calls':0,
        'limits':'Nearby declarations are leads, not section-wide rules. Syntax alone does not establish page/column/book semantics.'}
    with sqlite3.connect(out/'patterns.sqlite') as db:
        db.executescript('PRAGMA journal_mode=WAL; CREATE TABLE patterns(id TEXT PRIMARY KEY,volume TEXT,source TEXT,template TEXT,item_count INTEGER,evidence_json TEXT); CREATE TABLE occurrences(item_id TEXT PRIMARY KEY,group_id TEXT,pattern_id TEXT,evidence_json TEXT); CREATE TABLE notices(id TEXT PRIMARY KEY,source TEXT,evidence_json TEXT); CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);')
        db.executemany('INSERT INTO patterns VALUES(?,?,?,?,?,?)',[(p['id'],p['volume'],p['source'],p['template'],p['item_count'],encoded(p)) for p in final])
        db.executemany('INSERT INTO occurrences VALUES(?,?,?,?)',[(r['item_id'],r['group_id'],r['pattern_id'],encoded(r)) for r in records])
        db.executemany('INSERT INTO notices VALUES(?,?,?)',[(n['id'],n['source'],encoded(n)) for n in all_notices.values()])
        db.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        if history:
            db.executescript('CREATE TABLE history_pages(source TEXT PRIMARY KEY,evidence_json TEXT); CREATE TABLE history_checks(group_id TEXT,version_id INTEGER,evidence_json TEXT);')
            db.executemany('INSERT INTO history_pages VALUES(?,?)',[(p['source'],encoded(p)) for p in history['pages']])
            db.executemany('INSERT INTO history_checks VALUES(?,?,?)',[(r['group_id'],r['version_id'],encoded(r)) for r in history['checks']])
    for name,values in [('patterns',final),('occurrences',records),('notices',sorted(all_notices.values(),key=lambda n:n['id']))]:
        with (out/f'{name}.jsonl').open('w') as handle:
            for value in values: handle.write(encoded(value)+'\n')
    with (out/'families.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=['template','items','distinct_groups','patterns','volumes'])
        writer.writeheader();writer.writerows(families)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    if history:
        (out/'history_audit.json').write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n')
    manifest={'version':VERSION,'pending':str(pending.resolve()),'pending_sha256':hashlib.sha256(pending.read_bytes()).hexdigest(),
        'survey':str(survey.resolve()),'selected_group_evidence_sha256':sha(encoded(groups)),
        'volumes':sorted(volumes),'neighbors':neighbors,'root':str(root.resolve()),
        'versions_db':str(Path(versions_db).resolve()) if versions_db else None,
        'history_max_pages':history_max_pages,'history_max_groups':history_max_groups,
        'sources':[{'path':k,'sha256':v['sha256']} for k,v in sorted(cache.items()) if v],
        'code_sha256':sha(Path(__file__).read_text())}
    manifest['dependencies_sha256']={str(p):sha(p.read_text()) for p in (
        Path(__file__).with_name('editorial_pattern_history.py'),
        Path(__file__).with_name('editorial_ocr_blocks.py'),
        Path(__file__).with_name('editorial_regions_experiment.py'),
        Path(__file__).with_name('ocr_variant_audit.py'))}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    report=['# Padrões editoriais candidatos','',f"{len(items)} itens; {len(wanted)} grupos; {len(final)} padrões por página; {len(all_notices)} pistas de explicação.",
            '', 'N = arábico; R = romano; LETTER = letra isolada; SUB = subscrito. Pontuação preservada.',
            'Repetição não prova significado. Avisos próximos não são propagados entre seções. Nenhum destino novo é declarado.',
            '', '| Template | Itens | Grupos | Volumes |','| --- | ---: | ---: | ---: |']
    for f in families[:30]: report.append(f"| `{f['template'].replace('|','/')}` | {f['items']} | {f['distinct_groups']} | {f['volumes']} |")
    report += ['', '## Extração versus fonte', '',
               'Contagens por item, sobrepostas; suspeitas não equivalem a erros confirmados.',
               encoded(summary['extraction_diagnostics_nonexclusive'])]
    if history:
        report += ['', '## Conferência de versões OCR', '', encoded(summary['history']),
                   'Amostra dirigida. Divergência não decide qual versão está correta; falta de alinhamento não demonstra omissão.',
                   'Evidências, modelos, IDs, spans e snapshots em `history_audit.json` e `history_sources/`.']
    report += ['', '## Pistas de explicação','']
    for notice in sorted(all_notices.values(),key=lambda n:(n['source'],n['span'])):
        report += [f"- `{notice['source']}` {notice['span']}: {notice['quote'].replace(chr(10),' ')}",f"  Interpretação candidata: {encoded({k:notice[k] for k in ('units','slot_mappings','edition_mentions','existing_namespace_hint')})}"]
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending',type=Path,default=Path('data/index_catalog/destination_gap_audit_combined_002/items.jsonl'))
    parser.add_argument('--survey',type=Path,default=Path('data/index_entry_parser/global_targets_20261106_004/survey.sqlite'))
    parser.add_argument('--root',type=Path,default=Path('teste'))
    parser.add_argument('--volume',action='append',default=[])
    parser.add_argument('--neighbors',type=int,default=2)
    parser.add_argument('--versions-db',type=Path)
    parser.add_argument('--history-max-pages',type=int,default=8)
    parser.add_argument('--history-max-groups',type=int,default=5)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(discover(args.pending,args.survey,args.root,args.out,args.volume,args.neighbors,
        args.versions_db,args.history_max_pages,args.history_max_groups),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

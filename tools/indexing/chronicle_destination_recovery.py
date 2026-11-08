"""Searchable PL027 chronology candidates; no canonical edits or inferred certification."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_po015_names_lexical import normalized_text
from tools.indexing.candidate_search import encoded
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'pl027-chronicle-recovery-v1'
# Physical section boundaries, recorded from title/closure; never editorial page numbers.
SECTION = (132, 258)
RUN = re.compile(r'(?<![\w.])\d{1,4}(?:[ \t]+\d{1,4})+(?!\w)')
DIGIT = re.compile(r'\d+')


def page_rows(raw, physical):
    """Identify the chronological field in table rows, excluding note apparatus."""
    parsed = blocks(raw)
    series = 'christus' if physical >= 225 else 'abraham'
    # In CHRISTUS tables JUD. adds one regnal column until physical232.
    heads = ' '.join(b['body'] for b in parsed if 'OLYMP' in b['body'].upper())
    jud = bool(re.search(r'\bJUD\.', heads, re.I))
    result = []
    for block in parsed:
        numeric_block = bool(re.fullmatch(r'[\s\d.]+',block['body']))
        if block['kind'] not in {'texto_principal', 'cabecalho', 'outro', 'unknown'}:
            continue
        if block['kind'] in {'outro','unknown'} and not numeric_block:
            continue
        if block['kind'] == 'cabecalho' and 'OLYMP' not in block['body'].upper():
            continue
        if series == 'christus' and physical >= 236 and numeric_block:
            for digit in DIGIT.finditer(block['body']):
                year = int(digit.group())
                if 100 <= year <= 500:
                    start=block['body_start']+digit.start()
                    result.append({'series':series,'year':year,'span':[start,start+len(digit.group())],
                        'raw':digit.group(),'row':block['body'],'field':None,'block_kind':block['kind'],
                        'method':'numeric_column_run_candidate'})
        for match in RUN.finditer(block['body']):
            nums = list(DIGIT.finditer(match.group()))
            values = [int(x.group()) for x in nums]
            line_start = block['body'].rfind('\n', 0, match.start()) + 1
            line_end = block['body'].find('\n', match.end())
            if line_end < 0:
                line_end = len(block['body'])
            outside = block['body'][line_start:match.start()] + block['body'][match.end():line_end]
            # Numeric pairs embedded in prose are too weak; full row or >=3 fields.
            if len(values) == 2 and outside.strip():
                continue
            if series == 'abraham':
                index = 0 if physical < 177 else max(range(min(2, len(values))), key=values.__getitem__)
            else:
                minimum = 3 if jud else 2
                if len(values) not in {minimum, minimum+1}:
                    if not jud and physical >= 236:
                        for digit in nums:
                            value=int(digit.group())
                            if 100 <= value <= 500:
                                start=block['body_start']+match.start()+digit.start()
                                result.append({'series':series,'year':value,'span':[start,start+len(digit.group())],
                                    'raw':digit.group(),'row':match.group(),'field':None,'block_kind':block['kind'],
                                    'method':'flattened_numeric_rows_candidate'})
                    continue
                index = (max(range(2),key=values.__getitem__) if not jud and len(values)==2
                         else int(len(values) == minimum+1))
            year = values[index]
            if not (1 <= year <= (2015 if series == 'abraham' else 500)):
                continue
            start = block['body_start'] + match.start() + nums[index].start()
            result.append({'series': series, 'year': year, 'span': [start, start+len(nums[index].group())],
                           'raw': nums[index].group(), 'row': match.group(), 'field': index,
                           'block_kind': block['kind']})
    # Keep the largest local chronological run; isolate OCR outliers and other axes.
    values = sorted({r['year'] for r in result})
    groups = []
    for year in values:
        if not groups or year-groups[-1][-1] > 5:
            groups.append([])
        groups[-1].append(year)
    if not groups:
        return []
    best = max(groups, key=lambda g: (len(g), -g[0]))
    if len(best) < 2 or best[-1]-best[0] > 80:
        return []
    distinct = {}
    for row in result:
        if row['year'] in best:
            distinct[tuple(row['span'])]=row
    return list(distinct.values())


def build_map(root):
    sources = {}
    for path in sorted((root/'teste/PL027/text').glob('*.txt')):
        try:
            physical = int(path.stem.rsplit('-', 1)[1])
        except ValueError:
            continue
        if SECTION[0] <= physical <= SECTION[1]:
            raw = path.read_text()
            sources[physical] = (path, raw, page_rows(raw, physical))
    pages = []
    for physical,(path,raw,original_rows) in sorted(sources.items()):
        rows = list(original_rows)
        bounds_evidence = []
        # Separate numeric blocks and flattened rows need neighboring axis support.
        # This admits literal values only; no correction of 4xxx OCR to 1xxx.
        before, after = sources.get(physical-1), sources.get(physical+1)
        if before and after and before[2] and after[2]:
            lo,hi = max(r['year'] for r in before[2]), min(r['year'] for r in after[2])
            same_series = before[2][0]['series'] == after[2][0]['series']
            if same_series and 1 < hi-lo <= 80:
                bounded = []
                numeric_apparatus = []
                for block in blocks(raw):
                    numeric_only = bool(re.fullmatch(r'[\s\d.]+',block['body']))
                    if block['kind']=='aparato_critico' and numeric_only:
                        for match in DIGIT.finditer(block['body']):
                            value=int(match.group())
                            if lo < value < hi:
                                absolute=block['body_start']+match.start()
                                numeric_apparatus.append({'series':before[2][0]['series'],'year':value,
                                    'span':[absolute,absolute+len(match.group())],'raw':match.group(),
                                    'row':block['body'],'field':None,'block_kind':block['kind'],
                                    'method':'numeric_column_bounded_despite_xml_type',
                                    'uncertainties':['xml_block_type_conflict']})
                    if block['kind'] not in {'texto_principal','cabecalho','outro','unknown'}:
                        continue
                    if block['kind'] != 'texto_principal' and not numeric_only:
                        continue
                    spans = [m.span() for m in RUN.finditer(block['body'])]
                    if numeric_only:
                        spans = [(0,len(block['body']))]
                    for start,end in spans:
                        for match in DIGIT.finditer(block['body'],start,end):
                            value = int(match.group())
                            if lo < value < hi:
                                absolute = block['body_start']+match.start()
                                bounded.append({'series':before[2][0]['series'],'year':value,
                                    'span':[absolute,absolute+len(match.group())],'raw':match.group(),
                                    'row':block['body'][start:end], 'field':None,
                                    'block_kind':block['kind'], 'method':'literal_number_bounded_by_adjacent_pages'})
                values={r['year'] for r in numeric_apparatus}
                supported={v for v in values if any({start,start+1,start+2}<=values for start in (v-2,v-1,v))}
                bounded.extend(r for r in numeric_apparatus if r['year'] in supported)
                if len({r['year'] for r in bounded}) >= 2:
                    rows = bounded
                    bounds_evidence = [{'file':str(p.resolve()),'source_sha256':hashlib.sha256(t.encode()).hexdigest(),
                        'year_bounds':[min(r['year'] for r in rs),max(r['year'] for r in rs)]}
                        for p,t,rs in (before,after)]
        if not rows:
            continue
        text = '\n'.join(re.sub(r'<[^>]+>', ' ', b['body']) for b in blocks(raw)
                         if b['kind'] in {'texto_principal', 'nota_marginal'})
        pages.append({'file': str(path.resolve()), 'physical': physical, 'series': rows[0]['series'],
                      'year_min': min(r['year'] for r in rows), 'year_max': max(r['year'] for r in rows),
                      'rows': rows, 'source_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'text': text,
                      'bounds_evidence':bounds_evidence})
    for index, page in enumerate(pages):
        peers = [p for p in pages[max(0,index-1):index+2] if p['series']==page['series'] and p is not page]
        page['sequence_conflicts'] = [p['physical'] for p in peers
            if (p['physical'] < page['physical'] and p['year_min'] > page['year_max'])
            or (p['physical'] > page['physical'] and p['year_max'] < page['year_min'])]
        # If a regnal/Olympic column was selected hundreds of years off both peers,
        # it is not a useful chronological candidate for this namespace.
        page['wrong_axis_suspected'] = bool(peers) and all(
            min(abs(page['year_min']-p['year_max']),abs(page['year_max']-p['year_min'])) > 100
            for p in peers)
    return [p for p in pages if not p['wrong_axis_suspected']]


def interpret_reference(raw):
    """No cross-entry ibid invention. Preserve unsupported qualifiers as residuals."""
    match = re.fullmatch(r'\s*(\d{1,4})\s*[.,]?\s*', raw)
    if not match:
        return {'numbers': [], 'residual': raw}
    return {'numbers': [int(match[1])], 'residual': ''}


def candidate_years(number):
    if number < 2015:
        return [('abraham', number, 'literal_abraham_year')]
    if number == 2015:
        return [('abraham', 2015, 'literal_abraham_year'), ('christus', 1, 'birth_boundary_alternative')]
    return [('christus', number-2015, 'legend_offset_2015'),
            ('christus', number-2014, 'adjacent_year_edition_alternative')]


def lexical_support(label, text):
    query, haystack = normalized_text(label), normalized_text(text)
    match = re.search(r'(?<!\w)'+re.escape(query)+r'(?!\w)', haystack) if query else None
    if match:
        return {'method': 'normalized_label', 'score': 20, 'snippet': haystack[max(0,match.start()-70):match.end()+180]}
    # Token overlap ranks the same numeric candidates, never searches other pages.
    words = {w for w in query.split() if len(w) >= 5}
    present = sorted(words & set(haystack.split()))
    score = 8*len(present)/len(words) if words else 0
    return {'method': 'token_overlap' if present else 'not_found', 'score': score,
            'matched_tokens': present, 'snippet': ''}


def rank_candidates(number, label, pages):
    found = defaultdict(list)
    for series,year,conversion in candidate_years(number):
        for page in pages:
            if page['series'] != series or not page['year_min'] <= year <= page['year_max']:
                continue
            exact = [r for r in page['rows'] if r['year']==year]
            lexical = lexical_support(label, page['text'])
            score = (40 if exact else 30) + lexical['score'] - (3 if 'alternative' in conversion else 0)
            warnings = ['candidate_not_certified', 'page_only_chronological_alignment']
            if any('xml_block_type_conflict' in r.get('uncertainties',[]) for r in (exact or page['rows'])):
                warnings.append('xml_block_type_conflict')
            if not exact:
                warnings.append('year_between_observed_rows_not_literal')
            if series == 'christus':
                warnings.append('cross_edition_year_conversion_candidate')
            if page['sequence_conflicts']:
                warnings.append('local_chronology_sequence_conflict')
                score -= 15
            if not lexical['score']:
                warnings.append('no_lexical_support')
            found[page['file']].append({'year': year, 'series': series, 'conversion': conversion,
                'score': score, 'physical': page['physical'], 'rows': exact or [page['rows'][0],page['rows'][-1]],
                'year_bounds': [page['year_min'],page['year_max']], 'bounds_evidence':page.get('bounds_evidence',[]), 'lexical': lexical,
                'source_sha256': page['source_sha256'], 'uncertainties': warnings})
    candidates = []
    for file,supports in found.items():
        best = max(supports, key=lambda s:s['score'])
        candidates.append({'file': file, 'physical': best['physical'], 'rank': 0,
            'score': best['score'], 'score_kind': 'heuristic_not_probability', 'navigable': Path(file).is_file(),
            'certified': False, 'reasons': ['chronological_table_year',best['lexical']['method']],
            'snippet': best['lexical']['snippet'], 'uncertainties': sorted({w for s in supports for w in s['uncertainties']}),
            'evidence': {'index_number': number, 'supports': supports}})
    candidates.sort(key=lambda c:(not c['navigable'],-c['score'],c['file']))
    for rank,c in enumerate(candidates,1):
        c['rank']=rank
    return candidates


def recover(catalog, root, out, pages=None):
    out.mkdir(parents=True,exist_ok=False)
    pages = build_map(root) if pages is None else pages
    counts, statuses = Counter(), Counter()
    rows = []
    legend_path = next((root/'teste/PL027/text').glob('*-582.txt'), None)
    legend = None
    if legend_path:
        legend_raw = legend_path.read_text()
        notes = [b for b in blocks(legend_raw) if 'Id est Abrahamiticum' in b['body']]
        legend = {'physical':582,'file':str(legend_path.resolve()),
            'source_sha256':hashlib.sha256(legend_raw.encode()).hexdigest(),
            'spans':[[b['body_start'],b['body_end']] for b in notes],
            'meaning':'Eusebian/Abraham years; Christ-era conversion candidate after2015'}
    with closing(readonly(catalog)) as base, sqlite3.connect(out/'relations.sqlite') as dest:
        base.execute('BEGIN')
        dest.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for item in base.execute("SELECT * FROM items WHERE volume='PL027' AND state='source_only' ORDER BY id"):
            parsed = interpret_reference(item['ref_raw'])
            candidates = rank_candidates(parsed['numbers'][0],item['label'],pages) if parsed['numbers'] else []
            status = 'candidate_recovered' if candidates else ('unsupported_reference' if parsed['residual'] else 'year_without_mapped_table')
            meta = json.loads(item['metadata_json'])
            evidence = {'version': VERSION, 'item_id':item['id'], 'group_id':item['group_id'], 'label': item['label'], 'ref_raw': item['ref_raw'],
                'source': item['source'], 'source_span': meta.get('source_span'),
                'extraction_source_sha256': meta.get('source_sha256'), 'parsed': parsed,
                'status': status, 'source_provenance': 'historical_catalog_extraction',
                'legend': legend,
                'warnings': meta.get('group_flags',[]), 'import_ready': False}
            dest.execute('INSERT INTO relations VALUES(?,?,?,?,?)',(item['id'],item['group_id'],'PL027',status,encoded(evidence)))
            for c in candidates:
                c['evidence']['reference_source']=item['source']
                c['evidence']['reference_span']=meta.get('source_span')
                dest.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',(item['id'],c['file'],c['rank'],c['score'],int(c['navigable']),encoded(c)))
            counts['examined']+=1
            counts['with_candidates']+=bool(candidates)
            counts['candidate_links']+=len(candidates)
            counts['with_multiple_candidates']+=len(candidates)>1
            counts['normalized_label_support']+=any('normalized_label' in c['reasons'] for c in candidates)
            counts['token_overlap_support']+=any('token_overlap' in c['reasons'] for c in candidates)
            statuses[status]+=1
            rows.append({'item_id':item['id'],'label':item['label'],'ref_raw':item['ref_raw'],'status':status,
                         'candidates':len(candidates),'top_file': candidates[0]['file'] if candidates else ''})
        summary = {'version': VERSION,'catalog':str(catalog.resolve()), 'root':str(root.resolve()),
            'section_physical':list(SECTION),'table_pages':len(pages),'literal_rows':sum(len(p['rows']) for p in pages),
            'counts':dict(counts),'statuses':dict(statuses),'canonical_updates':0,'inference_calls':0,
            'prosper_cross_volume_mapped':False,'quality':'Chronological page candidates; no measured recall or certification.',
            'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        dest.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        dest.commit()
        assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        dest.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (out/'tables.jsonl').open('w') as handle:
        for page in pages:
            handle.write(encoded({k:v for k,v in page.items() if k!='text'})+'\n')
    with (out/'items.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]) if rows else ['item_id']);writer.writeheader();writer.writerows(rows)
    (out/'report.md').write_text('# Candidatos cronológicos PL027\n\nSem alterações canônicas. Anos convertidos e lacunas são candidatos sinalizados.\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(recover(args.catalog,args.root,args.out),ensure_ascii=False,indent=2))

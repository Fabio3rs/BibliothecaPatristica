"""Map PL217's book/letter references to openings, retaining searchable alternatives."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_po015_names_lexical import normalized_text, tokens
from tools.indexing.candidate_search import encoded
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.epistle_destination_map import build_destination_map
from tools.indexing.epistle_continuation_context import continuation_context
from tools.indexing.epistle_index_locators import locator_key, parse_epistle_locators

VERSION = 'epistle-catalog-recovery-v2'


def incipit_evidence(label, opening):
    """Compare only the mapped opening, never borrow matches elsewhere on the page."""
    opening = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', opening)
    query, text = normalized_text(label), normalized_text(opening)
    words = query.split()
    if not words:
        return {'method':'missing_query','score':0,'snippet':''}
    weak = len(words) < 3 or len(query) < 16
    match = re.search(r'(?<!\w)'+re.escape(query)+r'(?!\w)',text)
    if match:
        source_tokens = list(tokens(opening))
        token_start = text[:match.start()].count(' ')
        a, b = source_tokens[token_start]['start'], source_tokens[token_start+len(words)-1]['end']
        return {'method':'normalized','score':12 if weak else 30,'query':query,
                'short_or_formulaic':weak,'snippet':opening[max(0,a-100):b+250],
                'match_scope':'within_mapped_opening_segment'}
    if weak:
        return {'method':'not_found','score':0,'query':query,'short_or_formulaic':True,'snippet':''}
    haystack = text.split()
    best, window, best_start = 0, '', 0
    # Adjacent token windows tolerate OCR letter errors without semantic inference.
    for start in range(max(0,len(haystack)-len(words)+1)):
        candidate = ' '.join(haystack[start:start+len(words)])
        score = SequenceMatcher(None,query,candidate,autojunk=False).ratio()
        if score > best:
            best,window,best_start = score,candidate,start
    if best >= .88:
        source_tokens = list(tokens(opening))
        a, b = source_tokens[best_start]['start'], source_tokens[best_start+len(words)-1]['end']
        return {'method':'fuzzy','score':18,'query':query,'similarity':best,
                'matched_normalized_text':window,'short_or_formulaic':False,
                'snippet':opening[max(0,a-100):b+250],'match_scope':'within_mapped_opening_segment'}
    return {'method':'not_found','score':0,'query':query,'best_similarity':best,'snippet':''}


def ranked_openings(locators, label, index, root):
    found, outcomes = defaultdict(list), []
    for loc in locators:
        anchors = index.get(locator_key(loc),[])
        outcomes.append({'locator':loc,'opening_candidates':len(anchors)})
        for anchor in anchors:
            path = Path(anchor['file'])
            if not path.is_absolute():
                path = root/path
            lexical = incipit_evidence(label,anchor.get('opening_text',''))
            continuation = anchor.get('continuation_context')
            if continuation and lexical['method'] not in {'normalized', 'fuzzy'}:
                extended = incipit_evidence(label, anchor.get('opening_text', '') + '\n' + continuation['text'])
                if extended['method'] in {'normalized', 'fuzzy'}:
                    extended['score'] = max(0, extended['score'] - 6)
                    extended['match_scope'] = 'opening_and_bounded_adjacent_prefix'
                    extended['context_file'] = continuation['file']
                    lexical = extended
            warnings = list(anchor.get('uncertainties',[]))+list(loc.get('warnings',[]))
            warnings.extend(['destination_not_certified','opening_page_only'])
            if lexical.get('context_file'):
                warnings.append('incipit_support_uses_candidate_continuation')
            if lexical['method'] not in {'normalized','fuzzy'}:
                warnings.append('no_incipit_support')
            if lexical.get('short_or_formulaic'):
                warnings.append('short_incipit_not_discriminative')
            exists = path.is_file()
            if not exists:
                warnings.append('file_unavailable')
            found[str(path.resolve())].append({'anchor':anchor,'locator':loc,'incipit':lexical,
                'score':40+lexical['score'],'navigable':exists,'warnings':warnings})
    ranked = []
    for file,supports in found.items():
        best = max(supports,key=lambda s:s['score'])
        ranked.append({'file':file,'physical':best['anchor']['physical'],
            'score':best['score'],'score_kind':'heuristic_not_probability',
            'reasons':['editorial_book_epistle']+(['incipit_'+best['incipit']['method']]
                if best['incipit']['method'] in {'normalized','fuzzy'} else []),
            'uncertainties':sorted({w for s in supports for w in s['warnings']}),
            'navigable':best['navigable'],'certified':False,'snippet':best['incipit']['snippet'],
            'evidence':{'supports':supports,'extent_status':'opening_only'}})
    ranked.sort(key=lambda c:(not c['navigable'],-c['score'],c['file']))
    for rank,c in enumerate(ranked,1):
        c['rank']=rank
    return ranked,outcomes


def recover(catalog, root, out, volumes=('PL214','PL215','PL216','PL217'), anchors=None, previous_relations=(),
            include_continuations=True):
    out.mkdir(parents=True,exist_ok=False)
    if anchors is None:
        anchors = build_destination_map(root,volumes)
        if include_continuations:
            anchors = continuation_context(anchors, root)
    index = defaultdict(list)
    with (out/'openings.jsonl').open('w') as handle:
        for anchor in anchors:
            index[locator_key(anchor)].append(anchor)
            handle.write(encoded(anchor)+'\n')
    counts, statuses, methods, series, source_states = (Counter() for _ in range(5))
    source_cache, details = {}, []
    previous_ids = set()
    for path in previous_relations:
        with closing(readonly(path)) as con:
            metadata = json.loads(con.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
            if Path(metadata['catalog']).resolve()!=catalog.resolve():
                raise ValueError('Previous recovery belongs to another catalog')
            previous_ids.update(r[0] for r in con.execute('SELECT DISTINCT item_id FROM candidates WHERE navigable=1'))
    new_ids = set()
    with closing(readonly(catalog)) as base, sqlite3.connect(out/'relations.sqlite') as dest:
        base.execute('BEGIN')
        baseline = dict(base.execute('SELECT state,count(*) FROM items GROUP BY state'))
        # Previous overlays may include already navigable baseline rows; count the union.
        baseline_pending = {r[0] for r in base.execute("SELECT id FROM items WHERE state='source_only'")}
        previous_ids.intersection_update(baseline_pending)
        dest.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,
                candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for row in base.execute("SELECT * FROM items WHERE volume='PL217' ORDER BY id"):
            meta = json.loads(row['metadata_json'])
            span = meta.get('source_span') or [0,0]
            parsed = parse_epistle_locators(row['ref_raw'],offset=span[0])
            ranked,outcomes = ranked_openings(parsed['locators'],row['label'],index,root)
            source = row['source']
            if source not in source_cache:
                try:
                    data=Path(source).read_bytes()
                    source_cache[source]=(data.decode('utf-8'),hashlib.sha256(data).hexdigest())
                except (OSError,UnicodeError):
                    source_cache[source]=('',None)
            raw, checksum = source_cache[source]
            source_state = 'current' if checksum==meta.get('source_sha256') else 'changed'
            span_matches = raw[span[0]:span[1]]==row['ref_raw']
            if not span_matches:
                source_state = 'historical_extraction_span_not_current'
            if source_state!='current':
                for c in ranked:
                    c['uncertainties'].append(source_state)
            accessible = [c for c in ranked if c['navigable']]
            status = ('candidate_recovered' if accessible else 'locator_without_opening'
                      if parsed['locators'] else 'unparsed_reference')
            evidence = {'version':VERSION,'item_id':row['id'],'group_id':row['group_id'],
                'label':row['label'],'ref_raw':row['ref_raw'],'source':source,'source_span':span,
                'extraction_source_sha256':meta.get('source_sha256'),'observed_source_sha256':checksum,
                'source_state':source_state,'source_span_matches':span_matches,
                'parsed':parsed,'locator_outcomes':outcomes,'status':status,'candidate_count':len(ranked),
                'import_ready':False,'baseline_state':row['state'],
                'warnings':meta.get('group_flags',[])}
            if parsed['residuals'] or any(not x['opening_candidates'] for x in outcomes):
                evidence['warnings'] = evidence['warnings']+['some_reference_components_without_openings']
            for c in ranked:
                c['uncertainties']=sorted(set(c['uncertainties']+evidence['warnings']))
                c['evidence']['reference_item_id']=row['id']
            dest.execute('INSERT INTO relations VALUES(?,?,?,?,?)',
                (row['id'],row['group_id'],'PL217',status,encoded(evidence)))
            dest.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                [(row['id'],c['file'],c['rank'],c['score'],int(c['navigable']),encoded(c)) for c in ranked])
            if accessible and row['state']=='source_only':
                new_ids.add(row['id'])
            counts['items_examined']+=1
            counts['items_with_candidates']+=bool(accessible)
            counts['items_with_multiple_candidates']+=len(accessible)>1
            counts['candidate_links']+=len(ranked)
            counts['items_with_incipit_support']+=any('incipit_normalized' in c['reasons'] or 'incipit_fuzzy' in c['reasons'] for c in accessible)
            counts['items_with_longer_incipit_support']+=any(
                support['incipit']['method'] in {'normalized','fuzzy'} and not support['incipit'].get('short_or_formulaic')
                for c in accessible for support in c['evidence']['supports'])
            counts['items_with_bounded_continuation_support']+=any(
                support['incipit'].get('context_file')
                for c in accessible for support in c['evidence']['supports'])
            source_states[source_state]+=1
            statuses[status]+=1
            method = 'normalized' if any('incipit_normalized' in c['reasons'] for c in accessible) else 'fuzzy' if any('incipit_fuzzy' in c['reasons'] for c in accessible) else 'not_found'
            methods[method]+=1
            for loc in parsed['locators']:
                series[loc['series']+':'+('with_opening' if index.get(locator_key(loc)) else 'without_opening')]+=1
            details.append({'item_id':row['id'],'label':row['label'],'ref_raw':row['ref_raw'],'status':status,
                'candidate_count':len(accessible),'incipit_method':method,
                'top_file':accessible[0]['file'] if accessible else '',
                'top_physical':accessible[0]['physical'] if accessible else '',
                'source_state':source_state})
        summary = {'version':VERSION,'catalog':str(catalog.resolve()),'root':str(root.resolve()),
            'bounded_continuation_context':include_continuations,
            'destination_volumes':list(volumes),'openings':len(anchors),'distinct_editorial_keys':len(index),
            'counts':dict(counts),'statuses':dict(statuses),'incipit_methods':dict(methods),'series':dict(series),
            'source_states':dict(source_states),
            'baseline':baseline,'new_candidate_items':len(new_ids),
            'previous_overlay_items':len(previous_ids),'new_items_beyond_previous_overlays':len(new_ids-previous_ids),
            'with_destinations_combined':baseline.get('with_destinations',0)+len(previous_ids|new_ids),
            'source_only_combined':baseline.get('source_only',0)-len(previous_ids|new_ids),
            'previous_relations':[str(p.resolve()) for p in previous_relations],
            'canonical_updates':0,'inference_calls':0,'certified_links':0,
            'quality':'Opening candidates and lexical support; not measured full-index accuracy or recall.',
            'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'dependency_sha256':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ['epistle_destination_map.py','epistle_index_locators.py',
                             'epistle_continuation_context.py']}}
        dest.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        dest.commit()
        assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        dest.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    with (out/'items.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(details[0]) if details else ['item_id'])
        writer.writeheader();writer.writerows(details)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'report.md').write_text('# Destinos das epístolas referidas em PL217\n\n'
        'Resultados são candidatos de abertura. O suporte pelo incipit não certifica a referência.\n\n'
        '```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n\n'
        'Por item: items.csv. Âncoras: openings.jsonl. Evidências: relations.sqlite.\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--volume',action='append',help='Destination volume; defaults to PL214–PL217')
    p.add_argument('--previous-relations',type=Path,action='append',default=[])
    p.add_argument('--no-continuation-context', action='store_true',
                   help='Compare opening-only ranking against bounded adjacent-page context')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(recover(a.catalog,a.root,a.out,a.volume or ('PL214','PL215','PL216','PL217'),
                             previous_relations=a.previous_relations,
                             include_continuations=not a.no_continuation_context),ensure_ascii=False,indent=2))

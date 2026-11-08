"""Recover navigable candidates from literal qualifiers and local editorial relations.

Inputs stay read-only. The small output database overlays the search catalog;
it never transfers lexical evidence from an antecedent to another lemma.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing, ExitStack
import csv
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re
import sqlite3

from tools.indexing.candidate_search import encoded
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.consolidated_relation_adapter import adapted_groups, context_boundary

VERSION = 'editorial-relations-v2'
ATOM = re.compile(r'(?P<ibid>ibid(?:em)?\.?)|(?P<number>[1-9][0-9]{0,3})'
                  r'(?:\s*[-–—]\s*(?P<end>[1-9][0-9]{0,3}))?'
                  r'(?:\s+(?P<qual>(?:et\s+)?(?:seqq?|sqq?|sq)\.?))?', re.I)
ELIGIBLE = {'segmentation_or_notation_review', 'notation_pending', 'numeric_screen_only'}


def parse_relations(raw):
    """Entire literal suffix must be accounted for; no number mining from prose."""
    tokens, pos = [], 0
    value = raw.strip()
    while pos < len(value):
        m = ATOM.match(value, pos)
        if not m:
            return None
        token = {'kind': 'ibid' if m['ibid'] else 'number', 'raw': m[0],
                 'relative_span': [m.start(), m.end()]}
        end = m.end()
        if m['ibid']:
            subdivision = re.match(r'(?:\s*,\s*|\s+)([A-D](?:\s*,\s*[A-D])*)\.?(?!\w)', value[end:])
            if subdivision:
                token['subdivision_raw'] = subdivision[1]
                end += subdivision.end()
            continuation = re.match(r'\s+((?:et\s+)?(?:seqq?|sqq?|sq)\.?)\b\.?', value[end:], re.I)
            if continuation:
                token['qualifier'] = continuation[1]
                end += continuation.end()
            token['raw'] = value[m.start():end]
            token['relative_span'][1] = end
        if m['number']:
            token.update(number=int(m['number']), range_end=int(m['end']) if m['end'] else None,
                         qualifier=m['qual'])
            if token['range_end'] is not None and token['range_end'] < token['number']:
                return None
        tokens.append(token)
        pos = end
        if re.fullmatch(r'\s*\.?\s*', value[pos:]):
            break
        if token['kind']=='ibid' and re.fullmatch(r'[\s,;]*', value[pos:]):
            break
        sep = re.match(r'\s*[,;]\s*', value[pos:])
        if not sep:
            return None
        pos += sep.end()
        if pos == len(value):
            return None
    return tokens or None


def adjacency(raw, previous, current, page_blocks, max_gap=800):
    if previous is None:
        return 'no_local_antecedent'
    a, b = previous['char_end'], current['char_start']
    if b < a:
        return 'overlapping_reference_spans'
    if b-a > max_gap:
        return 'distant_antecedent'
    containing = [x for x in page_blocks if x['body_start'] <= previous['char_start']
                  and current['char_end'] <= x['body_end']]
    if len(containing) != 1 or containing[0]['kind'] not in {'texto_principal', 'nota', 'rodape'}:
        return 'block_or_section_boundary'
    gap = raw[a:b]
    if re.search(r'[0-9]|\b[Ii][Bb][Ii][Dd](?:[Ee][Mm])?\b|\b[IVXLCDM]+\b|<|\[/', gap):
        return 'intervening_locator_or_markup'
    if re.search(r'(?m)^\s*[A-ZÀ-ÝΑ-Ω][A-ZÀ-ÝΑ-Ω .]{0,60}\s*$', gap):
        return 'possible_section_heading'
    return None


def recover(catalog, survey, out, volumes=(), max_gap=800, catalog_context=False,
            estimator_candidates=None, only_ibid=False):
    if max_gap < 1:
        raise ValueError('max_gap must be positive')
    out.mkdir(parents=True, exist_ok=False)
    counts, states, by_volume, relation_reasons = Counter(), Counter(), defaultdict(Counter), Counter()
    requested_ids, processed_ids = set(), set()
    with ExitStack() as resources, closing(readonly(catalog)) as base, sqlite3.connect(out/'relations.sqlite') as dest:
        overlay = resources.enter_context(closing(readonly(estimator_candidates))) if estimator_candidates else None
        if overlay:
            config = json.loads(overlay.execute('SELECT config_json FROM runs').fetchone()[0])
            if Path(config['catalog']).resolve() != catalog.resolve():
                raise ValueError('estimator overlay belongs to a different catalog')
        base.execute('BEGIN')
        dest.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE relations(group_id TEXT PRIMARY KEY,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,
                navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        baseline = dict(base.execute('SELECT state,count(*) FROM items GROUP BY state'))
        for volume, rows in groupby(base.execute('SELECT * FROM items ORDER BY volume,id'), lambda r:r['volume']):
            items = defaultdict(list)
            for r in rows:
                items[r['group_id']].append(dict(r))
            if volumes and volume not in volumes:
                continue
            requested_ids.update(i['id'] for records in items.values() for i in records
                                 if i['state']=='source_only' and re.search(r'\bibid',i['ref_raw'] or '',re.I))
            folder = survey/'volumes'/volume
            if not (folder/'groups.jsonl').exists():
                counts['volumes_without_survey'] += 1
                continue
            with (folder/'groups.jsonl').open() as f:
                groups = [json.loads(line) for line in f]
            if catalog_context:
                groups = adapted_groups(groups, items)
            with (folder/'sources.jsonl').open() as f:
                excluded = {json.loads(line)['source'] for line in f}
            headers = defaultdict(list)
            if (folder/'header_markers.jsonl').exists():
                with (folder/'header_markers.jsonl').open() as f:
                    for line in f:
                        m = json.loads(line)
                        if m['path'] not in excluded:
                            headers[m['number']].append(m)
            cache = {}

            def estimated(g, number=None):
                found = {}
                if overlay:
                    for item in items[g['id']]:
                        for row in overlay.execute('SELECT evidence_json FROM candidates WHERE item_id=? ORDER BY rank,file', (item['id'],)):
                            candidate = json.loads(row[0])
                            supports = [m for m in candidate['matches'] if number is None or m['number']==number]
                            if supports and not candidate.get('source_file_itself'):
                                found[candidate['file']] = {'file':candidate['file'],'physical':candidate.get('physical'),
                                    'basis':'antecedent_estimated_page_candidate','antecedent_item_id':item['id'],
                                    'estimator_evidence':supports, 'estimator_database':str(estimator_candidates.resolve()),
                                    'uncertainties':['antecedent_destination_not_certified','estimated_pagination_not_certified']}
                return list(found.values())

            def existing(g):
                if g['id'] not in cache:
                    found = {}
                    for item in items[g['id']]:
                        for row in base.execute('SELECT candidate_json FROM candidates WHERE item_id=?', (item['id'],)):
                            c = json.loads(row[0])
                            found[c['file']] = {'file': c['file'], 'physical': c.get('physical'),
                                'basis': 'antecedent_catalog_candidate', 'antecedent_item_id': item['id'],
                                'uncertainties': ['antecedent_destination_not_certified']}
                    for candidate in estimated(g):
                        found.setdefault(candidate['file'],candidate)
                    cache[g['id']] = list(found.values())
                return cache[g['id']]

            for source, source_groups in groupby(sorted(groups, key=lambda g:(g['source'],g['char_start'],g['char_end'])), lambda g:g['source']):
                source_groups = list(source_groups)
                relevant = [g for g in source_groups if any(i['state']=='source_only' for i in items[g['id']])
                            and (re.search(r'\bibid',g['raw'],re.I) or
                                 any(t.get('qualifier') or t.get('range_end') for t in (parse_relations(g['raw']) or [])))]
                if not relevant:
                    continue
                try:
                    data = Path(source).read_bytes(); raw = data.decode('utf-8')
                except (OSError,UnicodeError):
                    counts['unreadable_sources'] += 1
                    continue
                actual_hash = hashlib.sha256(data).hexdigest()
                page_blocks = blocks(raw)
                previous = None
                for g in source_groups:
                    tokens = parse_relations(g['raw'])
                    pending = [i for i in items[g['id']] if i['state']=='source_only'
                               and i['ref_raw'].strip() == g['raw'].strip()]
                    interesting = bool(re.search(r'\bibid',g['raw'],re.I) or
                                       tokens and any(t.get('qualifier') or t.get('range_end') for t in tokens))
                    if only_ibid:
                        interesting = bool(re.search(r'\bibid',g['raw'],re.I))
                    span_ok = raw[g['char_start']:g['char_end']] == g['raw']
                    if not pending or not interesting:
                        previous = g if span_ok else None
                        continue
                    evidence = {'version':VERSION,'group_id':g['id'],'source':source,
                        'source_span':[g['char_start'],g['char_end']], 'raw':g['raw'],
                        'source_sha256':actual_hash,'extraction_sha256':g['source_sha256'],
                        'tokens':tokens,'relations':[],'warnings':list(g.get('flags',[])),
                        'canonical_write_allowed':False}
                    if actual_hash != g['source_sha256']:
                        evidence['warnings'].append('source_changed_local_span_matches_only')
                    candidates, last = [], None
                    if not span_ok:
                        evidence['status'] = 'source_span_changed'
                    elif not tokens:
                        evidence['status'] = 'unsupported_reference_list'
                    else:
                        for token_index,token in enumerate(tokens):
                            if token['kind']=='ibid':
                                reason = adjacency(raw,previous,g,page_blocks,max_gap) if last is None else None
                                if catalog_context and last is None and reason is None:
                                    reason = context_boundary(raw, previous, g)
                                antecedent = previous if last is None else g
                                inherited = last if last is not None else existing(previous) if reason is None else []
                                relation = {'kind':'ibid','antecedent_group_id':antecedent['id'] if antecedent else None,
                                    'scope':'same_reference_list' if last is not None else 'preceding_group_same_block',
                                    'token_index':token_index,
                                    'antecedent_token_index':token_index-1 if last is not None else None,
                                    'owner_relation':'same_owner' if antecedent and g.get('owner_raw') and antecedent.get('owner_raw') == g.get('owner_raw')
                                                     else 'adjacent_owner_candidate',
                                    'reason':reason or ('antecedent_candidates' if inherited else 'antecedent_without_destination'),
                                    'ambiguity':'all_antecedent_group_alternatives_preserved',
                                    'antecedent_raw':antecedent['raw'] if antecedent else None,
                                    'antecedent_span':[antecedent['char_start'],antecedent['char_end']] if antecedent else None,
                                    'subdivision_raw':token.get('subdivision_raw'), 'qualifier':token.get('qualifier'),
                                    'extent':'start_only' if token.get('qualifier') else 'inherited_page_candidates'}
                                evidence['relations'].append(relation)
                                last = [{**c,'basis':'ibid_candidate','antecedent_group_id':antecedent['id'],
                                    'token_index':token_index,
                                    'inheritance_depth':c.get('inheritance_depth',0)+1,
                                    'uncertainties':list(c.get('uncertainties',[]))+['ibid_scope_unverified','no_lexical_support']+
                                        (['range_or_continuation_start_only'] if token.get('qualifier') else [])}
                                    for c in inherited]
                                candidates.extend(last)
                            elif g['status'] in ELIGIBLE and not g.get('profile') and not g.get('relation_context_blocked'):
                                selected = headers.get(token['number'],[])
                                last = [{'file':m['path'],'physical':m['physical'],'basis':'explicit_numeric_anchor',
                                    'token_index':token_index,
                                    'marker':m,'uncertainties':['editorial_namespace_unconfirmed','no_lexical_support']+
                                        (['range_or_continuation_start_only'] if token.get('qualifier') or token.get('range_end') else [])}
                                    for m in selected]
                                last.extend({**c,'basis':'explicit_numeric_anchor','token_index':token_index,
                                             'marker':{'number':token['number']}}
                                            for c in estimated(g, token['number']))
                                candidates.extend(last)
                                evidence['relations'].append({'kind':'explicit_numeric_anchor','number':token['number'],
                                    'candidate_count':len(last),
                                    'extent':'start_only' if token.get('qualifier') or token.get('range_end') else 'literal_number',
                                    'range_end':token.get('range_end'),'qualifier':token.get('qualifier')})
                            else:
                                last = []
                                evidence['relations'].append({'kind':'scoped_number','reason':'requires_existing_specialized_map'})
                        evidence['status'] = 'candidate_recovered' if candidates else 'relation_without_destination'
                    evidence['all_literal_anchors_have_candidates'] = bool(evidence['relations']) and all(
                        r.get('reason')=='antecedent_candidates' if r['kind']=='ibid'
                        else bool(r.get('candidate_count')) for r in evidence['relations'])
                    if not evidence['all_literal_anchors_have_candidates']:
                        evidence['warnings'].append('some_reference_components_without_candidates')
                    for c in candidates:
                        c['uncertainties'] = sorted(set(c['uncertainties']+evidence['warnings']))
                    # Keep an unresolved immediate antecedent; never jump backwards over it.
                    if span_ok:
                        cache[g['id']] = candidates or existing(g)
                    unique = defaultdict(list)
                    for c in candidates:
                        unique[c['file']].append(c)
                    ranked = []
                    for path,supports in unique.items():
                        c = supports[0]
                        nav = Path(path).is_file()
                        warnings = {w for support in supports for w in support['uncertainties']}
                        if not nav:
                            warnings.add('file_unavailable')
                        ranked.append({'file':path,'physical':c.get('physical'),'score':6 if c['basis']=='ibid_candidate' else 8,
                            'score_kind':'heuristic_not_probability','reasons':sorted({s['basis'] for s in supports}),
                            'uncertainties':sorted(warnings|set(evidence['warnings'])),
                            'navigable':nav,'certified':False,'snippet':'',
                            'evidence':{'relation_group_id':g['id'],**c,
                                'supporting_relations':[{'basis':s['basis'],'token_index':s.get('token_index'),
                                    'antecedent_group_id':s.get('antecedent_group_id'),
                                    'number':s.get('marker',{}).get('number'),
                                    'antecedent_item_id':s.get('antecedent_item_id')} for s in supports]}})
                    ranked.sort(key=lambda c:(not c['navigable'],-c['score'],c['file']))
                    for rank,c in enumerate(ranked,1):
                        c['rank'] = rank
                    recovered = any(c['navigable'] for c in ranked)
                    for item in pending:
                        processed_ids.add(item['id'])
                        dest.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                            [(item['id'],c['file'],c['rank'],c['score'],int(c['navigable']),encoded(c)) for c in ranked])
                    evidence['candidate_count'] = len(ranked)
                    dest.execute('INSERT INTO relations VALUES(?,?,?,?)',(g['id'],volume,evidence['status'],encoded(evidence)))
                    states[evidence['status']] += 1
                    for relation in evidence['relations']:
                        relation_reasons[relation['kind']+':'+relation.get('reason','literal_anchor')] += 1
                    counts['groups_examined'] += 1
                    counts['items_gaining_navigable_candidates'] += len(pending) if recovered else 0
                    counts['candidate_links_added'] += len(pending)*len(ranked)
                    if recovered:
                        counts['items_with_ibid_candidates'] += len(pending) if any(c['navigable'] and 'ibid_candidate' in c['reasons'] for c in ranked) else 0
                        counts['items_with_partially_unmapped_references'] += len(pending) if not evidence['all_literal_anchors_have_candidates'] else 0
                    by_volume[volume]['groups_examined'] += 1
                    by_volume[volume]['items_gaining_navigable_candidates'] += len(pending) if recovered else 0
                    previous = g if span_ok else None
            dest.commit()
            print(encoded({'volume':volume,'recovery':dict(by_volume[volume])}),flush=True)
        summary = {'version':VERSION,'catalog':str(catalog.resolve()),'survey':str(survey.resolve()),
            'baseline':baseline,'counts':dict(counts),'relation_statuses':dict(states),'by_volume':dict(by_volume),
            'relation_reasons':dict(relation_reasons),
            'with_destinations_after':baseline.get('with_destinations',0)+counts['items_gaining_navigable_candidates'],
            'source_only_after':baseline.get('source_only',0)-counts['items_gaining_navigable_candidates'],
            'parameters':{'volumes':list(volumes),'max_gap':max_gap,'catalog_context':catalog_context},'canonical_updates':0,'inference_calls':0,
            'estimator_candidates':str(estimator_candidates.resolve()) if estimator_candidates else None,
            'only_ibid':only_ibid,'requested_ibid_items':len(requested_ids),
            'processed_ibid_items':len(requested_ids & processed_ids),
            'unprocessed_ibid_item_ids':sorted(requested_ids-processed_ids),
            'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'adapter_code_sha256':hashlib.sha256(Path(__file__).with_name('consolidated_relation_adapter.py').read_bytes()).hexdigest(),
            'quality':'Additional unverified search candidates; not measured editorial accuracy or Recall@K.'}
        dest.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        dest.commit()
        assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        dest.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (out/'by_volume.csv').open('w') as handle:
        writer = csv.writer(handle)
        writer.writerow(['volume','groups_examined','items_gaining_navigable_candidates'])
        writer.writerows((v,c['groups_examined'],c['items_gaining_navigable_candidates']) for v,c in sorted(by_volume.items()))
    (out/'report.md').write_text('# Recuperação de relações editoriais\n\n'
        f"Itens com destinos: {baseline.get('with_destinations',0)} → {summary['with_destinations_after']}.\n\n"
        f"Itens sem destinos: {baseline.get('source_only',0)} → {summary['source_only_after']}.\n\n"
        'Candidatos não certificados. Intervalos e continuações oferecem apenas a âncora inicial.\n'
        'Evidências e cadeias: relations.sqlite, tabelas relations e candidates. Distribuição: by_volume.csv.\n\n'
        '```json\n'+json.dumps(dict(counts),ensure_ascii=False,indent=2)+'\n```\n')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--survey',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--volume',action='append',default=[])
    p.add_argument('--max-gap',type=int,default=800)
    p.add_argument('--catalog-context',action='store_true',
                   help='Include source-spanned catalog clauses and enforce repaired owner/scope boundaries')
    p.add_argument('--estimator-candidates',type=Path,help='Read-only candidates.sqlite from page estimator recovery')
    p.add_argument('--only-ibid',action='store_true',help='Recover ibid groups only; other groups remain antecedents/barriers')
    a = p.parse_args()
    print(json.dumps(recover(a.catalog,a.survey,a.out,a.volume,a.max_gap,a.catalog_context,
                            a.estimator_candidates,a.only_ibid),ensure_ascii=False,indent=2))

"""Generate bounded OCR-reading hypotheses; never rewrite text or canonical links."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3

from tools.corpus_utils import page_number
from tools.indexing.bounded_index_audit import (
    encoded, digest, expected_range, query_variants, reading_key, relocate_row,
)
from tools.indexing.bounded_lexical_match import match_text
from tools.indexing.index_version_evidence import read_page_versions
from tools.indexing.scoped_body_numbering import load_series
from tools.indexing.lexical_candidate_policy import DEFAULT_POLICY, annotate, load_policy, ranking_key

VERSION = 'ocr-confusion-recovery-v1'
SIMPLE = re.compile(r'\d+(?:\s*[,;]\s*\d+)*')


def simple_numbers(text):
    text = text.strip(' \r\n\t,;.')
    return re.findall(r'\d+', text) if SIMPLE.fullmatch(text) else None


def single_edit(before, after):
    """One character edit or adjacent transposition; no guessed multi-edit rule."""
    if before == after:
        return None
    if len(before) == len(after):
        positions = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        if len(positions) == 1:
            i = positions[0]
            return ('substitution', before[i], after[i])
        if len(positions) == 2:
            i, j = positions
            if j == i + 1 and before[i:j+1] == after[i:j+1][::-1]:
                return ('transposition', before[i:j+1], after[i:j+1])
    if len(before) == len(after) + 1:
        for i in range(len(before)):
            if before[:i] + before[i+1:] == after:
                return ('deletion', before[i], '')
    if len(after) == len(before) + 1:
        for i in range(len(after)):
            if after[:i] + after[i+1:] == before:
                return ('insertion', '', after[i])
    return None


def classify(check):
    old, new = check.get('current_tokens', []), check.get('alternative_tokens', [])
    if not new and old:
        return ['possible_omission_or_alignment_gap']
    kinds = set()
    for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        left, right = old[a:b], new[c:d]
        if len(left) == len(right) == 1:
            edit = single_edit(left[0], right[0])
            prefix = 'numeric' if left[0].isdigit() and right[0].isdigit() else 'lexical'
            kinds.add(prefix + '_' + (edit[0] if edit else 'multiple_edits'))
        elif ''.join(left) == ''.join(right):
            kinds.add('token_split_or_fusion')
        else:
            kinds.add('tokens_inserted' if not left else 'tokens_omitted' if not right
                      else 'multiple_tokens_changed')
    if check['field'] == 'owner' and (check.get('current_numbers') or check.get('alternative_numbers')):
        kinds.add('numeric_material_in_owner_possible_segmentation')
    return sorted(kinds or {'punctuation_or_alignment_difference'})


def build_catalog(rows):
    events, rules = [], defaultdict(list)
    for row in rows:
        for check in row['index_version_checks']:
            if 'disagreement' not in check['status']:
                continue
            event = dict(item_id=row['item_id'], profile=row['profile'], source=row['source'],
                         classes=classify(check), evidence=check, certified=False)
            events.append(event)
            if check['field'] != 'reference':
                continue
            if check.get('page_identity', '').startswith('excluded_') or check.get('page_identity') == 'replacement_source_needs_review':
                continue
            old, new = simple_numbers(check['current_quote']), simple_numbers(check['alternative_quote'])
            if not old or not new or len(old) != len(new):
                continue
            for before, after in zip(old, new):
                edit = single_edit(before, after)
                if edit:
                    rules[(row['profile'], *edit)].append(dict(
                        item_id=row['item_id'], source=row['source'], before=before, after=after,
                        version_id=check['version_id'], model=check.get('model'),
                        alternative_sha256=check.get('alternative_sha256'),
                        current_span=check.get('current_span'), alternative_span=check.get('alternative_span')))
    catalog = []
    for (profile, kind, before, after), evidence in sorted(rules.items()):
        catalog.append(dict(profile=profile, kind=kind, before=before, after=after,
                            supporting_items=len({e['item_id'] for e in evidence}),
                            supporting_sources=len({e['source'] for e in evidence}), evidence=evidence,
                            meaning='observed_direction_of_disagreement_not_correct_reading'))
    return events, catalog


def propose(number, row, catalog, maximum, min_support=2):
    proposals = {}
    raw = str(number)
    for rule in catalog:
        if rule['profile'] != row['profile']:
            continue
        evidence = [e for e in rule['evidence']
                    if e['source'] != row['source'] and e['item_id'] != row['item_id']]
        support = len({e['item_id'] for e in evidence})
        if support < min_support:
            continue
        before, after = rule['before'], rule['after']
        for i in range(len(raw) + 1):
            if not raw.startswith(before, i):
                continue
            text = raw[:i] + after + raw[i+len(before):]
            if not text or text.startswith('0') or text == raw or not text.isdigit():
                continue
            value = int(text)
            if not 1 <= value <= maximum:
                continue
            proof = dict(kind=rule['kind'], before=before, after=after, position=i,
                         supporting_items=support, evidence=evidence)
            proposal = proposals.setdefault(value, dict(original_number=number, number=value, rules=[]))
            proposal['rules'].append(proof)
    return sorted(proposals.values(), key=lambda p: (
        -max(r['supporting_items'] for r in p['rules']), p['number']))


def join_soft_wraps(raw):
    """Only letter-hyphen-newline-letter; keep a map to original raw offsets."""
    cuts = list(re.finditer(r'(?<=[^\W\d_])[-\u00ad]\s*\n[ \t]*(?=[^\W\d_])', raw))
    positions, pieces, last = [], [], 0
    for cut in cuts:
        pieces.append(raw[last:cut.start()])
        positions.extend(range(last, cut.start()))
        last = cut.end()
    pieces.append(raw[last:])
    positions.extend(range(last, len(raw)))
    return ''.join(pieces), positions, len(cuts)


def match_with_wraps(query, raw):
    plain = match_text(query, raw, max_matches=3)
    joined_query, _, query_cuts = join_soft_wraps(query)
    joined, offsets, cuts = join_soft_wraps(raw)
    if not cuts and not query_cuts:
        return plain
    extra = match_text(joined_query, joined, max_matches=3)
    extra = json.loads(encoded(extra))
    if extra['method'] == 'exact':
        extra['method'] = 'normalized'
    extra['normalization'] = 'possible_soft_wrap_join'
    extra['query_original'] = query
    for hit in extra['matches']:
        if hit['method'] == 'exact':
            hit['method'] = 'normalized'
        a, b = hit['start'], hit['end']
        hit['matched_joined_text'] = hit['text_raw']
        a, b = offsets[a], offsets[b-1] + 1
        hit.update(start=a, end=b, char_start=a, char_end=b, text_raw=raw[a:b],
                   snippet=raw[max(0, a-80):b+80])
    def key(value):
        return reading_key(dict(lexical=value, query_variant=dict(kind='current_owner'), document_kind='current_ocr'))
    return extra if key(extra) < key(plain) else plain


def metrics(results, labels):
    """Only explicit, complete, human-reviewed gold; never treat old links as gold."""
    gold = {r['item_id']: r for r in labels if r.get('human_reviewed') is True
            and r.get('complete_gold') is True and r.get('gold_files')}
    values = defaultdict(list)
    for row in results:
        if row['item_id'] not in gold:
            continue
        relevant = {str(Path(p).resolve()) for p in gold[row['item_id']]['gold_files']}
        for mode in ('baseline', 'expanded'):
            ranked = row['baseline_files'] if mode == 'baseline' else [c['file'] for c in row['candidates']]
            ranked = list(dict.fromkeys(str(Path(p).resolve()) for p in ranked))
            ranks = [i for i, p in enumerate(ranked, 1) if p in relevant]
            values[mode + ':MRR'].append(1 / min(ranks) if ranks else 0)
            values[mode + ':coverage'].append(len(set(ranked) & relevant) / len(relevant))
            for k in (5, 10, 20):
                hits = len(set(ranked[:k]) & relevant)
                values[f'{mode}:Recall@{k}'].append(hits / len(relevant))
                values[f'{mode}:Precision@{k}'].append(hits / k)
    return dict(reviewed_queries=len(values.get('baseline:MRR', [])),
                status='measured' if values else 'awaiting_independent_review',
                metrics={k: sum(v)/len(v) for k, v in sorted(values.items())})


def refresh_outputs(out, stopwords=DEFAULT_POLICY, labels=None):
    """Rerank saved evidence and evaluate labels without another OCR/history search."""
    rows = [json.loads(line) for line in (out/'results.jsonl').open()]
    summary = json.loads((out/'summary.json').read_text())
    policy = load_policy(stopwords)
    policy_counts = Counter()
    by_stratum = defaultdict(Counter)
    development = []
    fixtures = Path(__file__).resolve().parents[2]/'docs/fixtures/bounded_lexical_case_reviews.json'
    cases = {c['item_id']:c for c in json.loads(fixtures.read_text())['cases']
             if c.get('item_id') and c.get('destination',{}).get('source')}
    with closing(sqlite3.connect(out/'audit.sqlite')) as db:
        for row in rows:
            for candidate in row['candidates']:
                candidate.setdefault('retrieval_rank', candidate['rank'])
                lexical = candidate['best_reading']['lexical']
                if lexical.get('normalization') == 'possible_soft_wrap_join' and lexical['method'] == 'exact':
                    lexical['method'] = candidate['method'] = 'normalized'
                    for hit in lexical['matches']:
                        if hit['method'] == 'exact':
                            hit['method'] = 'normalized'
                annotate(candidate, policy)
                detail = candidate['lexical_policy']
                if detail.get('complete_informative_terms'):
                    policy_counts['candidate_pairs_all_informative_terms'] += 1
                if detail.get('informative_order_changed'):
                    policy_counts['candidate_pairs_term_order_changed'] += 1
                if detail.get('query_negations_missing'):
                    policy_counts['candidate_pairs_query_negation_missing'] += 1
            row['candidates'].sort(key=ranking_key)
            for rank,candidate in enumerate(row['candidates'],1):
                candidate['rank']=rank
                db.execute('UPDATE candidates SET rank=?,json=? WHERE item_id=? AND file=?',
                           (rank,encoded(candidate),row['item_id'],candidate['file']))
            old, new = row['baseline_files'], [c['file'] for c in row['candidates']]
            delta = by_stratum[row['cohort']+':'+row['baseline_status']]
            delta['records'] += 1
            delta['baseline_candidate_pairs'] += len(old)
            delta['expanded_candidate_pairs'] += len(new)
            delta['baseline_files_lost'] += len(set(old)-set(new))
            delta['baseline_top_file_below_20'] += bool(old) and old[0] not in new[:20]
            delta['above_20_candidates'] += len(new)>20
            delta['with_any_lexical_evidence'] += any(c['method']!='absent' for c in row['candidates'])
            if row['item_id'] in cases:
                case = cases[row['item_id']]
                target = str((Path(__file__).resolve().parents[2]/case['destination']['source']).resolve())
                development.append(dict(item_id=row['item_id'],case=case['id'],file=target,
                    baseline_rank=old.index(target)+1 if target in old else None,
                    expanded_rank=new.index(target)+1 if target in new else None,
                    independent=False,human_reviewed=False))
            db.execute('UPDATE results SET json=? WHERE item_id=?',(encoded(row),row['item_id']))
        summary['ranking_policy'] = dict(path=str(stopwords),sha256=digest(stopwords),
            implementation_sha256=digest(Path(__file__).with_name('lexical_candidate_policy.py')),
            counts=dict(policy_counts))
        summary['comparison']={k:dict(v) for k,v in sorted(by_stratum.items())}
        summary['development_cases']=development
        summary['evaluation']=metrics(rows,json.loads(labels.read_text()) if labels else [])
        summary['logical_sha256']=hashlib.sha256('\n'.join(encoded(r) for r in rows).encode()).hexdigest()
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('summary',encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out/'results.jsonl').write_text(''.join(encoded(r)+'\n' for r in rows))
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (out/'candidates.csv').open('w') as handle:
        writer=csv.writer(handle)
        writer.writerow(['item_id','file','rank','origin','method','informative_coverage','weighted_coverage','term_order_changed','query_negations_missing'])
        for row in rows:
            for c in row['candidates']:
                detail=c['lexical_policy']
                writer.writerow([row['item_id'],c['file'],c['rank'],c['origin'],c['method'],
                    detail['informative_coverage'],detail['weighted_coverage'],detail.get('informative_order_changed'),
                    encoded(detail.get('query_negations_missing',[]))])
    report = (out/'report.md').read_text().split('\n## Ranking lexical')[0]
    report += '\n## Ranking lexical\n\nStopwords têm peso 0,1; termos informativos e negações têm peso 1. '
    report += 'Ordem diferente não reduz a cobertura lexical; ausência de negação é sinalizada. Não há equivalência semântica presumida.\n\n'
    report += '| Coorte e estado anterior | Registros | Pares antes | Pares depois | Arquivos antigos perdidos | Antigo top 1 fora do top 20 |\n| --- | ---: | ---: | ---: | ---: | ---: |\n'
    for name,d in sorted(by_stratum.items()):
        report += f"| {name} | {d['records']} | {d['baseline_candidate_pairs']} | {d['expanded_candidate_pairs']} | {d['baseline_files_lost']} | {d['baseline_top_file_below_20']} |\n"
    report += '\nAvaliação independente: '+encoded(summary['evaluation'])+'\n'
    report += '\nCasos de desenvolvimento (não amostra independente):\n\n'
    report += '\n'.join(f"- {d['case']}: posição {d['baseline_rank']} → {d['expanded_rank']}" for d in development)+'\n'
    (out/'report.md').write_text(report)
    return summary


def run(args):
    implementation_sha256 = digest(__file__)
    rows = sorted((json.loads(line) for line in args.input.open()), key=lambda r: r['item_id'])
    args.out.mkdir(parents=True, exist_ok=False)
    events, catalog = build_catalog(rows)
    profiles = {p['id']: p for p in json.loads(args.plan.read_text())['profiles']}
    matrices = {key: load_series(profiles[key], args.root.resolve()) for key in sorted({r['profile'] for r in rows})}
    results, counts, classes = [], Counter(), defaultdict(set)
    for event in events:
        for cause in event['classes']:
            classes[cause].add(event['item_id'])
    (args.out/'divergences.jsonl').write_text(''.join(encoded(e)+'\n' for e in events))
    (args.out/'confusions.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n')

    @lru_cache(maxsize=128)
    def historical(path, volume, physical):
        if not args.versions_db:
            return []
        record = read_page_versions(path, volume, physical, args.versions_db, args.max_versions)
        return [v for v in record['versions'] if v.get('text_content') and not v.get('text_matches_current')]

    with closing(sqlite3.connect(args.out/'audit.sqlite')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE results(item_id TEXT PRIMARY KEY,json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for index, row in enumerate(rows, 1):
            profile = profiles[row['profile']]
            pages, supported, inventory = matrices[row['profile']]
            baseline = row['candidates']
            result = dict(item_id=row['item_id'], profile=row['profile'], cohort=row['cohort'],
                          owner_raw=row['owner_raw'], reference_raw=row['reference_raw'], source=row['source'],
                          baseline_status=row['status'], baseline_files=[c['file'] for c in baseline],
                          proposals=[], candidates=[], flags=list(row['flags']), certified=False)
            raw = Path(row['source']).read_text()
            relocated = relocate_row(row, raw)
            current = all(raw[s[0]:s[1]] == relocated[f'{field}_raw']
                          for field in ('owner', 'reference') for s in [relocated[f'{field}_span']])
            candidates = {c['physical']: {k: v for k, v in c.items() if k != 'readings'} for c in baseline}
            for candidate in candidates.values():
                candidate['origin'] = 'baseline'
            if current:
                variants, _ = query_variants(relocated, raw)
                # Existing local historical owner variants retain their original provenance.
                for check in row['index_version_checks']:
                    if check['field'] == 'owner' and check['status'] == 'lexical_reading_disagreement':
                        variants.append(dict(kind='historical_owner_reading', text=check['alternative_quote'],
                                             version_id=check['version_id'], model=check.get('model')))
                proposals = []
                # Expand only items still lacking full-label evidence; keep all old candidates.
                if row['status'] not in {'range_exact', 'range_normalized', 'range_fuzzy'}:
                    for number in simple_numbers(row['reference_raw']) or []:
                        accepted = 0
                        for proposal in propose(int(number), row, catalog, profile['maximum_number'], args.min_support):
                            window = expected_range(proposal['number'], pages, supported, profile, max_pages=args.max_pages)
                            if not window['pages'] or len(window['pages']) > args.max_pages:
                                continue
                            proposals.append(dict(**proposal, range=window))
                            accepted += 1
                            if accepted == args.max_alternatives:
                                break
                result['proposals'] = proposals
                search_pages = set(candidates) | {p for proposal in proposals for p in proposal['range']['pages']}
                for physical in sorted(search_pages):
                    page = pages[physical]
                    if physical in candidates and candidates[physical]['sha256'] != page['sha256']:
                        result['flags'].append('destination_changed_refresh_baseline')
                        # Keep the file hypothesis, discard obsolete lexical evidence and spans.
                        candidates.pop(physical)
                    readings = []
                    for variant in variants:
                        readings.append(dict(query_variant=variant, document_kind='current_ocr',
                                             lexical=match_with_wraps(variant['text'], page['raw'])))
                    best = min(readings, key=reading_key)
                    if best['lexical']['method'] in {'absent', 'partial', 'fuzzy'}:
                        for version in historical(page['file'], profile['volume'], physical):
                            for variant in variants:
                                reading = dict(query_variant=variant, document_kind='historical_ocr',
                                               document_version={k:v for k,v in version.items() if k != 'text_content'},
                                               lexical=match_with_wraps(variant['text'], version['text_content']))
                                if reading_key(reading) < reading_key(best):
                                    best = reading
                    previous = candidates.get(physical)
                    if previous and reading_key(previous['best_reading']) <= reading_key(best):
                        continue
                    candidates[physical] = dict(file=page['file'], physical=physical, sha256=page['sha256'],
                        method=best['lexical']['method'], best_reading=best,
                        origin='baseline' if page['file'] in result['baseline_files'] else 'numeric_confusion_hypothesis',
                        certified=False, localization_level='ocr_page_file')
            else:
                result['flags'].append('source_field_changed_refresh_baseline')
            # Weak new matches must not displace an original full-label match.
            result['candidates'] = sorted(candidates.values(), key=lambda c: (
                reading_key(c['best_reading'])[:4], c['origin'] != 'baseline', c['physical']))
            for rank, candidate in enumerate(result['candidates'], 1):
                candidate['rank'] = rank
                candidate['supporting_proposals'] = [i for i,p in enumerate(result['proposals'])
                                                     if candidate['physical'] in p['range']['pages']]
                db.execute('INSERT INTO candidates VALUES(?,?,?,?)', (row['item_id'], candidate['file'], rank, encoded(candidate)))
            result['method'] = result['candidates'][0]['method'] if result['candidates'] else 'no_candidates'
            prefix = row['cohort'] + ':'
            counts[prefix+'records'] += 1
            counts[prefix+'with_proposals'] += bool(result['proposals'])
            counts[prefix+'new_files'] += len(set(c['file'] for c in result['candidates']) - set(result['baseline_files']))
            counts[prefix+'newly_bounded'] += not baseline and bool(result['candidates'])
            counts[prefix+'new_lexical'] += row['status'] in {'range_absent','no_bounded_range'} and result['method'] not in {'absent','no_candidates'}
            if row['status'] == 'range_absent':
                counts[prefix+'previous_absent:'+result['method']] += 1
            results.append(result)
            db.execute('INSERT INTO results VALUES(?,?)', (row['item_id'], encoded(result)))
            if index % 25 == 0:
                db.commit()
                print(encoded(dict(completed=index, total=len(rows), counts=dict(counts))), flush=True)
        summary = dict(version=VERSION, records=len(rows), counts=dict(counts),
            divergence_records_by_class={k:len(v) for k,v in sorted(classes.items())},
            rules=len(catalog), input=str(args.input), input_sha256=digest(args.input),
            plan_sha256=digest(args.plan), implementation_sha256=implementation_sha256,
            parameters={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
            logical_sha256=hashlib.sha256('\n'.join(encoded(r) for r in results).encode()).hexdigest(),
            evaluation=metrics(results, json.loads(args.labels.read_text()) if args.labels else []),
            canonical_updates=0, catalog_updates=0, inference_calls=0)
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (args.out/'results.jsonl').write_text(''.join(encoded(r)+'\n' for r in results))
    (args.out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    # Selection depends on baseline strata, never on which hypotheses succeeded.
    selected = []
    reviews = Path(__file__).resolve().parents[2]/'docs/fixtures/bounded_lexical_case_reviews.json'
    known_development = {c.get('item_id') for c in json.loads(reviews.read_text())['cases']}
    for status in ('range_absent', 'no_bounded_range', 'range_partial', 'range_exact'):
        group = [r for r in rows if r['status'] == status and r['cohort'] != 'targeted_comparison'
                 and r['item_id'] not in known_development]
        selected.extend(random.Random(args.seed).sample(group, min(5, len(group))))
    review = [dict(item_id=r['item_id'], source=r['source'], owner_raw=r['owner_raw'],
                   reference_raw=r['reference_raw'], baseline_stratum=r['status'],
                   image=str(Path(r['source']).parent.parent/'images'/f"{profiles[r['profile']]['volume']}-{page_number(Path(r['source'])):03d}.png"),
                   human_reviewed=False, complete_gold=False, gold_files=[], notes='') for r in selected]
    (args.out/'review_sample.json').write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n')
    lines = ['# Hipóteses de confusão OCR', '',
             'Alternativas direcionais, não correções. Treino exclui a página do item consultado.', '',
             '| Resultado | Contagem |', '| --- | ---: |']
    lines += [f'| {key} | {value} |' for key,value in sorted(counts.items())]
    lines += ['', '## Divergências por causa (categorias podem sobrepor)', '']
    lines += [f'- {key}: {len(value)} registros' for key,value in sorted(classes.items())]
    lines += ['', '## Avaliação', '', encoded(summary['evaluation']), '',
              'Amostra de revisão em review_sample.json. Não usar concordância OCR ou links antigos como ouro.',
              'Nenhum OCR ou catálogo canônico foi modificado. Faixas alternativas são separadas; não são um intervalo entre números concorrentes.']
    (args.out/'report.md').write_text('\n'.join(lines)+'\n')
    summary = refresh_outputs(args.out,getattr(args,'stopwords',DEFAULT_POLICY),args.labels)
    print(encoded(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='results.jsonl from bounded lexical audit')
    parser.add_argument('--report-only', action='store_true', help='Rerank saved evidence and evaluate labels, no OCR/history reads')
    parser.add_argument('--stopwords', type=Path, default=DEFAULT_POLICY)
    parser.add_argument('--plan', type=Path, default=Path('docs/fixtures/scoped_body_numbering.json'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--versions-db', type=Path)
    parser.add_argument('--max-versions', type=int, default=5)
    parser.add_argument('--min-support', type=int, default=2)
    parser.add_argument('--max-alternatives', type=int, default=3)
    parser.add_argument('--max-pages', type=int, default=6)
    parser.add_argument('--seed', type=int, default=711)
    parser.add_argument('--labels', type=Path, help='JSON list with human_reviewed, complete_gold, gold_files')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.report_only:
        print(encoded(refresh_outputs(args.out,args.stopwords,args.labels)))
        return
    if not args.input:
        parser.error('--input required except with --report-only')
    if min(args.min_support,args.max_alternatives,args.max_pages,args.max_versions) < 1:
        parser.error('Limits must be positive')
    run(args)

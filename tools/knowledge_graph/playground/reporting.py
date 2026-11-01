from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from .common import dumps
from .validation import completion_details
from .pricing import run_estimate


def run_metrics(run, case):
    result, validation = run.get('result') or {}, run.get('validation') or {}
    extraction = validation.get('parsed')
    valid_shape = validation.get('structural_valid') and isinstance(extraction, dict)
    completion = completion_details(result, run['prepared'])
    cost = run.get('cost_estimate') or run_estimate(run)
    pricing = cost.get('pricing') or {}
    return {'run_id': run['id'], 'experiment_id': run['experiment_id'], 'page': case['page'],
            'provider': run['config']['provider'], 'model': run['config']['model'],
            'variant': run['prepared']['variant']['id'], 'mode': run['prepared']['mode'],
            'facsimile_policy': run['prepared']['facsimile_policy'],
            'facsimile_attached': result.get('facsimile_attached'),
            'status': run['status'], 'synthetic': bool(case['synthetic']),
            'schema_valid': validation.get('structural_valid'), 'evidence_valid': validation.get('source_valid'),
            'validation_version': validation.get('version'), 'latest_validation_valid': validation.get('valid'),
            'exact_evidence_valid': validation.get('source_exact', validation.get('source_valid')),
            'exact_quotes': validation.get('evidence_counts', {}).get('exact'),
            'approximate_quotes': (sum(validation['evidence_counts'].get(k, 0) for k in ('normalized', 'fuzzy'))
                                   if 'evidence_counts' in validation else None),
            'concepts': len(extraction.get('concepts', [])) if valid_shape else None,
            'relations': len(extraction.get('relations', [])) if valid_shape else None,
            'duplicate_label_warnings': sum(w.startswith('duplicate_concept') for w in validation.get('warnings', [])),
            'latency_seconds': result.get('latency_seconds'), 'cost_usd': cost['cost_usd'],
            'cost_basis': cost['basis'], 'price_source': pricing.get('source'),
            'price_recorded_on': pricing.get('recorded_on'), 'price_catalog_sha256': pricing.get('sha256'),
            'price_rates_json': dumps(pricing.get('prices', {})),
            'input_tokens': result.get('usage', {}).get('input_tokens'),
            'output_tokens': result.get('usage', {}).get('output_tokens'),
            'max_output_tokens': completion['max_output_tokens'],
            'reasoning_tokens': completion['reasoning_tokens'],
            'visible_output_tokens': completion['visible_output_tokens'],
            'output_limit_reached': completion['output_limit_reached'],
            'cached_input_tokens': result.get('usage', {}).get('cached_input_tokens'),
            'inference_performed': result.get('inference_performed'), 'review_count': len(run['reviews'])}


def aggregate(rows, corpus_pages):
    evaluated = [r for r in rows if r['schema_valid'] is not None]
    shaped = [r for r in evaluated if r['schema_valid']]
    actual = [r for r in rows if r['inference_performed'] and not r['synthetic']]
    latencies = sorted(r['latency_seconds'] for r in actual if r['latency_seconds'] is not None)
    costs = [r['cost_usd'] for r in actual if r['cost_usd'] is not None]
    mean = lambda items: statistics.mean(items) if items else None
    return {'runs': len(rows), 'evaluated': len(evaluated), 'schema_valid_fraction': mean([int(r['schema_valid']) for r in evaluated]),
            'located_evidence_valid_fraction_of_structural_valid': mean([int(r['evidence_valid']) for r in shaped]),
            'exact_evidence_valid_fraction_of_structural_valid': mean([int(r['exact_evidence_valid']) for r in shaped]),
            'approximate_quotes': sum(r['approximate_quotes'] or 0 for r in rows),
            'average_concepts': mean([r['concepts'] for r in shaped]), 'average_relations': mean([r['relations'] for r in shaped]),
            'duplicate_label_warnings': sum(r['duplicate_label_warnings'] for r in rows),
            'actual_inference_runs': len(actual), 'priced_runs': len(costs),
            'latency_median_seconds': statistics.median(latencies) if latencies else None,
            'latency_p95_seconds': latencies[max(0, __import__('math').ceil(len(latencies) * .95) - 1)] if latencies else None,
            'input_tokens_reported': sum(r['input_tokens'] or 0 for r in actual),
            'output_tokens_reported': sum(r['output_tokens'] or 0 for r in actual),
            'reasoning_tokens_reported': sum(r['reasoning_tokens'] or 0 for r in actual),
            'reasoning_usage_reported_runs': sum(r['reasoning_tokens'] is not None for r in actual),
            'output_limit_reached_runs': sum(r['output_limit_reached'] for r in rows),
            'usage_reported_runs': sum(r['input_tokens'] is not None and r['output_tokens'] is not None for r in actual),
            'estimated_spend_usd_known_runs': sum(costs) if costs else None,
            'estimated_cost_per_page_inference_usd': mean(costs),
            'estimated_corpus_cost_usd': mean(costs) * corpus_pages if costs else None,
            'corpus_pages': corpus_pages, 'semantic_status': 'requires_review',
            'cost_note': 'Configured USD rates, including dated user-supplied catalog reestimates for old unpriced runs (see per-run cost_basis and price_source). Extrapolation uses priced actual calls only. Missing usage, failed/uncertain remote requests and heterogeneous pages limit this estimate. Cache replays and synthetic examples excluded.'}


def report(store, experiment, corpus_pages=288000):
    rows = [run_metrics(store.get_run(r['id']), store.get_case(r['case_id'])) for r in store.list_runs(experiment)]
    groups = defaultdict(list)
    for row in rows:
        key = tuple(row[k] for k in ('provider', 'model', 'variant', 'mode', 'facsimile_policy', 'synthetic'))
        groups[key].append(row)
    return {'experiment': experiment, 'summary': aggregate(rows, corpus_pages),
            'groups': [{'configuration': list(k), **aggregate(v, corpus_pages)} for k, v in groups.items()], 'runs': rows,
            'precision_recall': None, 'note': 'No adjudicated reference annotations: precision and recall are not estimated.'}


def markdown(data):
    lines = [f"# KG experiment {data['experiment']}", '', data['note'], '',
             '| Run | Page | Provider / model | Original status | Latest validation | Schema | Located evidence | Exact / approximate quotes | Concepts | Relations | Tokens in/out total | Reasoning included | Output limit | Limit reached | Seconds | USD | Cost basis / rate date |',
             '|---|---|---|---|---|---|---|---|---:|---:|---|---:|---:|---|---:|---:|---|']
    def cell(value):
        return str(value if value is not None else '—').replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;')
    for r in data['runs']:
        values = [r['run_id'], r['page'], r['provider'] + ' / ' + r['model'], r['status'], r['latest_validation_valid'], r['schema_valid'],
                  r['evidence_valid'], f"{r['exact_quotes']} / {r['approximate_quotes']}", r['concepts'], r['relations'], f"{r['input_tokens']}/{r['output_tokens']}",
                  r['reasoning_tokens'], r['max_output_tokens'], r['output_limit_reached'], r['latency_seconds'], r['cost_usd'],
                  f"{r['cost_basis']} / {r['price_recorded_on'] or r['price_source'] or '—'}"]
        lines.append('| ' + ' | '.join(cell(x) for x in values) + ' |')
    lines += ['', '## Aggregate (automatic checks, not historical correctness)', '', '```json',
              __import__('json').dumps(data['summary'], ensure_ascii=False, indent=2), '```', '',
              '## Models / prompts', '', '```json', __import__('json').dumps(data['groups'], ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n'


def csv_report(data):
    if not data['runs']:
        return ''
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(data['runs'][0]))
    writer.writeheader()
    for row in data['runs']:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in row.items()})
    return stream.getvalue()


def export_jsonl(store, experiment):
    before = None
    while True:
        rows = store.list_runs(experiment, limit=100, before=before)
        if not rows:
            break
        for run in rows:
            yield dumps({'run': store.get_run(run['id']), 'case': store.get_case(run['case_id'])}) + '\n'
        before = rows[-1]['id']

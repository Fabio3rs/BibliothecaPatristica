#!/usr/bin/env python3
"""Summarize a completed recovery run without modifying either input database."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.estimator_candidate_recovery import readonly


def unresolved_reason(row):
    route = row['route']
    if route in {'pagination', 'pagination_hypothesis'}:
        return 'numeric_without_map_candidate' if row['numbers'] else 'no_page_number_extracted'
    if route.startswith('requires_namespace_map:'):
        return 'internal_or_specialized_namespace'
    return route


def summarize(directory):
    run = json.loads((directory / 'summary.json').read_text())
    counts, reasons, routes, evidence = Counter(), Counter(), Counter(), Counter()
    collections = defaultdict(Counter)
    examples = defaultdict(list)
    lengths, seen, missing_files, target_files = [], set(), set(), set()
    with closing(readonly(run['config']['catalog'])) as catalog:
        baseline = dict(catalog.execute('SELECT state,count(*) FROM items GROUP BY state'))
        pending = {r[0] for r in catalog.execute("SELECT id FROM items WHERE state='source_only'")}
    with closing(readonly(directory / 'candidates.sqlite')) as conn:
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
        for result in conn.execute('SELECT payload_json FROM results ORDER BY volume,item_id'):
            row = json.loads(result[0])
            seen.add(row['id'])
            candidates = row['candidates']
            counts['examined'] += 1
            routes[row['route']] += 1
            collections[row['volume'][:2]]['examined'] += 1
            counts['source_span_unconfirmed'] += not row['source_span_matches']
            counts['with_section_context'] += bool(row['contexts'])
            counts['with_source_work_context'] += any(c['work_key'] for c in row['contexts'])
            if candidates:
                counts['with_candidates'] += 1
                counts['with_candidates_and_unconfirmed_source_span'] += not row['source_span_matches']
                collections[row['volume'][:2]]['with_candidates'] += 1
                lengths.append(len(candidates))
                bucket = ('1' if len(candidates) == 1 else '2_to_5' if len(candidates) <= 5 else
                          '6_to_20' if len(candidates) <= 20 else 'over_20')
                counts['candidate_count_' + bucket] += 1
                counts['candidate_links'] += len(candidates)
                counts['with_non_source_candidate'] += any(not c['source_file_itself'] for c in candidates)
                counts['only_source_file_candidates'] += all(c['source_file_itself'] for c in candidates)
                counts['with_target_work_context'] += any(c['target_work_keys'] for c in candidates)
                counts['with_unmatched_numbers_despite_candidates'] += bool(row['unmatched_numbers'])
                kinds = {e['kind'] for m in candidates[0]['matches'] for e in m['evidence']}
                category = ('direct_header' if kinds & {'header_pair','header_single'} else
                            'sequence_consensus' if 'sequence_consensus' in kinds else 'other_neighbor_or_anchor')
                evidence[category] += 1
                for c in candidates:
                    if c['file'] not in target_files:
                        target_files.add(c['file'])
                        if not Path(c['file']).is_file():
                            missing_files.add(c['file'])
            else:
                counts['without_candidates'] += 1
                collections[row['volume'][:2]]['without_candidates'] += 1
                reason = unresolved_reason(row)
                reasons[reason] += 1
                if len(examples[reason]) < 5:
                    examples[reason].append({k: row[k] for k in ['id','volume','label','ref_raw','source','route']})
    volumes = run['volumes']
    overview = {
        'version': 'estimated-page-candidates-overview-v1',
        'run_logical_sha256': run['logical_sha256'],
        'counts': dict(counts), 'routes': dict(routes),
        'without_candidate_reasons': dict(reasons.most_common()),
        'collections': {k: dict(v) for k,v in sorted(collections.items())},
        'top_candidate_evidence': dict(evidence),
        'candidate_count_median': statistics.median(lengths) if lengths else None,
        'candidate_count_max': max(lengths, default=0),
        'distinct_candidate_files': len(target_files),
        'volumes_examined': len(volumes),
        'volumes_with_candidates': sum(bool(v['with_candidates']) for v in volumes),
        'ocr_files_in_inventory': sum(v['ocr_files'] for v in volumes),
        'cache_hits': sum(v.get('cache_hits',0) for v in volumes),
        'observations_recomputed': sum(v.get('observations_recomputed',0) for v in volumes),
        'work_entry_metadata_warnings': sum(v.get('work_entry_metadata_warnings',0) for v in volumes),
        'catalog_baseline': baseline,
        'coverage_check': {'missing_pending_ids': len(pending-seen), 'unexpected_ids': len(seen-pending),
                           'sqlite_quick_check': integrity, 'missing_candidate_files': sorted(missing_files),
                           'summary_count_matches': len(seen)==run['items']},
        'top_volumes_by_recovery': sorted(volumes,key=lambda v: (-v['with_candidates'],v['volume']))[:20],
        'top_volumes_still_without_candidates': sorted(volumes,key=lambda v: (-(v['items']-v['with_candidates']),v['volume']))[:20],
        'unresolved_examples': dict(examples), 'canonical_updates': 0, 'active_catalog_updates': 0,
        'quality': 'candidate_coverage_not_accuracy',
    }
    (directory/'overview.json').write_text(json.dumps(overview,ensure_ascii=False,indent=2)+'\n')
    lines = ['# Visão geral da recuperação de páginas candidatas','',
             f"Registros examinados: **{counts['examined']:,}**, em **{len(volumes)} volumes/partes**.",
             f"Com candidatos: **{counts['with_candidates']:,}**. Sem candidatos nesta camada: **{counts['without_candidates']:,}**.", '',
             'Todos são registros experimentais; não são contagens de verbetes únicos nem destinos certificados. O catálogo ativo não foi alterado.', '',
             '| Coleção | Examinados | Com candidato | Sem candidato |','|---|---:|---:|---:|']
    for k,v in sorted(collections.items()):
        lines.append(f"| {k} | {v['examined']} | {v['with_candidates']} | {v['without_candidates']} |")
    lines += ['', '| Motivo restante nesta camada | Registros |','|---|---:|']
    for reason,n in reasons.most_common():
        lines.append(f'| {reason} | {n} |')
    lines += ['', '| Evidência do primeiro candidato | Registros |','|---|---:|']
    for kind,n in evidence.most_common():
        lines.append(f'| {kind} | {n} |')
    lines += ['', '| Volume | Examinados | Com candidato | Restantes |','|---|---:|---:|---:|']
    for v in overview['top_volumes_by_recovery']:
        lines.append(f"| {v['volume']} | {v['items']} | {v['with_candidates']} | {v['items']-v['with_candidates']} |")
    lines += ['', f"Mediana de candidatos: {overview['candidate_count_median']}; máximo: {overview['candidate_count_max']}.",
              f"Somente o próprio arquivo do índice como candidato: {counts['only_source_file_candidates']} registros; isso não demonstra um destino distinto.",
              f"Registros com candidato mas com outros números não encontrados: {counts['with_unmatched_numbers_despite_candidates']}.",
              f"Spans de fonte não confirmados: {counts['source_span_unconfirmed']}; alternativas permanecem disponíveis com aviso.",
              '', 'Verificação de cobertura: `'+json.dumps(overview['coverage_check'],ensure_ascii=False)+'`.', '',
              'As rotas indicam o tratamento disponível nesta camada. Não demonstram erro no impresso, no OCR ou na extração. PO precisa do mapa específico da coleção.',
              'Consulte overview.json para os volumes com mais pendências e exemplos reproduzíveis; results.jsonl preserva as evidências e alternativas.']
    (directory/'overview.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:overview[k] for k in ['counts','collections','without_candidate_reasons','top_candidate_evidence','coverage_check']},ensure_ascii=False,indent=2))
    return overview


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    summarize(parser.parse_args().run)

"""Paired diagnostics and separately blinded semantic adjudication packets."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .common import digest, readonly
from .verification_contract import without_confidence

DECISIONS = ('correct', 'incorrect', 'partially_correct', 'redundant', 'uncertain')


def offline_controls(db, source_experiment):
    controls = []
    with readonly(db) as con:
        for page in ('PG001:2', 'PG004:2'):
            rows = list(con.execute('''SELECT r.id,r.repetition,r.result_json,c.split,c.snapshot_json
                FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? AND c.page=?
                AND json_extract(r.prepared_json,'$.ab.arm')='D' ORDER BY r.repetition''', (source_experiment, page)))
            if any(r['split'] == 'blind_holdout' for r in rows):
                raise ValueError('Blind holdout cannot serve as prompt-study control')
            for r in rows:
                data = json.loads(r['result_json'] or '{}').get('parsed_json')
                snap = json.loads(r['snapshot_json'])
                controls.append({'page': page, 'run_id': r['id'], 'repetition': r['repetition'],
                                 'source': snap['source'], 'current_segments': snap['v2']['segments'],
                                 'ocr_blocks': snap['ocr']['blocks'], 'inference_calls': 0,
                                 'empty_structures': all(not data.get(g) for g in ('concepts','entities','claims','events','relations')) if isinstance(data,dict) else None})
    return controls


def load_verification(db, experiment):
    with readonly(db) as con:
        row = con.execute('SELECT plan_json FROM experiments WHERE id=?', (experiment,)).fetchone()
        plan = json.loads(row[0]) if row else {}
        if plan.get('kind') != 'verification':
            raise ValueError('Expected a verification experiment')
        rows = []
        for r in con.execute('''SELECT r.id,r.status,r.repetition,r.result_json,
            json_remove(r.prepared_json,'$.request') prepared_json,c.page,c.snapshot_json,
            (SELECT validation_json FROM run_validation v WHERE v.run_id=r.id ORDER BY v.id DESC LIMIT 1) validation_json
            FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.experiment_id=? ORDER BY r.id''', (experiment,)):
            item = dict(r)
            for key in ('result', 'prepared', 'snapshot', 'validation'):
                item[key] = json.loads(item.pop(key + '_json') or 'null')
            rows.append(item)
    return plan, rows


def blind_material(plan, rows):
    packets, keys, forms = [], {}, []
    for row in rows:
        result = row['result'] or {}
        derived = result.get('derived_extraction')
        if not derived or not (row['validation'] or {}).get('structural_valid'):
            continue
        baseline = row['prepared']['baseline_extraction']
        token = digest({'seed': plan['seed'], 'source': row['prepared']['source_run_id']})
        pair_id = 'PAIR-' + token[:16]
        order = ('A', 'B') if int(token[-1], 16) % 2 else ('B', 'A')
        variants = {'A': baseline, 'B': derived}
        shown = {label: without_confidence(variants[arm]) for label, arm in zip(('X', 'Y'), order)}
        keys[pair_id] = dict(source_run_id=row['prepared']['source_run_id'], review_run_id=row['id'],
                             arms=dict(zip(('X', 'Y'), order)),
                             extraction_hashes={label: digest(data) for label, data in shown.items()})
        snap = row['snapshot']
        packets.append({'pair_id': pair_id, 'page': row['page'],
                        'instructions': 'Judge each displayed output against sources. Labels X/Y are randomized per pair. Confidence is withheld. Quote location is not semantic proof. Do not infer a preferred variant from length.',
                        'source': snap['source'], 'v2': snap['v2'], 'ocr': snap['ocr'],
                        'facsimile': snap['facsimile'], 'outputs': shown})
        outputs = {}
        for label, data in shown.items():
            outputs[label] = {'extraction_hash': digest(data)}
            for group in ('claims', 'relations'):
                outputs[label][group] = [{'id': obj['id'], 'status': 'pending', 'decision': None,
                                          'error_types': [], 'source_refs': [], 'comment': ''} for obj in data[group]]
        forms.append({'pair_id': pair_id, 'reviewer': '', 'independent_of_generator': False,
                      'source_checked': False, 'outputs': outputs,
                      'navigation_and_missing_content_notes': '', 'unresolved_ambiguities': []})
    return packets, keys, forms


def judge_transitions(review_file, keys, packets, latest):
    if not review_file:
        return {'adjudicated_objects': 0, 'counts': {}, 'transitions': [], 'semantic_improvement': 'not_established'}
    data = json.loads(Path(review_file).read_text(encoding='utf-8'))
    expected = {p['pair_id']: p for p in packets}
    seen, decisions, count = set(), {}, 0
    for entry in data['pairs']:
        pair = entry['pair_id']
        if pair not in keys or pair in seen:
            raise ValueError('Unknown or duplicate blinded pair')
        seen.add(pair)
        if set(entry['outputs']) != {'X', 'Y'}:
            raise ValueError('Both blinded variants must be present')
        for label, output in entry['outputs'].items():
            if output['extraction_hash'] != keys[pair]['extraction_hashes'][label]:
                raise ValueError('Review refers to a changed extraction')
            for group in ('claims', 'relations'):
                known = {x['id'] for x in expected[pair]['outputs'][label][group]}
                ids = [x['id'] for x in output[group]]
                if len(set(ids)) != len(ids) or set(ids) != known:
                    raise ValueError('Review object set differs from the blinded output')
                for item in output[group]:
                    if item['status'] != 'adjudicated':
                        continue
                    if (not entry.get('reviewer') or not entry.get('independent_of_generator')
                            or not entry.get('source_checked') or not item.get('comment')
                            or item.get('decision') not in DECISIONS):
                        raise ValueError('Adjudication requires independent named source review, decision and comment')
                    sources = {('ocr_block', b['id']) for b in expected[pair]['ocr']['blocks']}
                    sources |= {('summary_segment', s['id']) for s in expected[pair]['v2']['segments']}
                    if not item.get('source_refs') or any((r.get('source'), r.get('locator')) not in sources for r in item['source_refs']):
                        raise ValueError('Adjudication requires valid current-page source references')
                    decisions[(pair, keys[pair]['arms'][label], group, item['id'])] = item['decision']
                    count += 1
    transitions = []
    for pair, key in keys.items():
        row = latest[key['source_run_id']]
        review = row['result']['parsed_json']
        for group, dk, ik in (('relations', 'relation_decisions', 'relation_id'), ('claims', 'claim_decisions', 'claim_id')):
            for d in review[dk]:
                before = decisions.get((pair, 'A', group, d[ik]))
                target_ids = [d[ik]] if d['action'] == 'keep' else [o['id'] for o in d['replacements']]
                after = [decisions.get((pair, 'B', group, i)) for i in target_ids]
                verdict = 'pending_or_uncertain'
                if before in ('correct', 'incorrect') and all(x in ('correct', 'incorrect') for x in after):
                    if not after:
                        verdict = 'correct_content_removed' if before == 'correct' else 'error_removed'
                    elif all(x == 'correct' for x in after):
                        verdict = 'correct_content_preserved' if before == 'correct' else 'error_corrected'
                    else:
                        verdict = 'correct_content_damaged' if before == 'correct' else 'error_remains'
                transitions.append(dict(pair_id=pair, group=group, original_id=d[ik], replacements=target_ids,
                                        before=before, after=after, verdict=verdict))
    return {'adjudicated_objects': count, 'counts': dict(Counter(t['group'] + ':' + t['verdict'] for t in transitions)),
            'transitions': transitions, 'semantic_improvement': 'requires_review_of_paired_transitions_not_node_counts'}


def write_verification_report(db, experiment, output, *, reviews=None, matching_policy=None):
    target = Path(output)
    if reviews and Path(reviews).resolve() == (target.parent / 'blind/judgments.template.json').resolve():
        raise ValueError('Copy the template to a separate decisions file before adjudication')
    plan, rows = load_verification(db, experiment)
    policy_comparison = []
    if matching_policy is not None:
        from . import validation_micro
        if matching_policy != validation_micro.POLICY:
            raise ValueError('Unsupported offline verification policy')
        for row in rows:
            if not row['result']:
                continue
            old = row['validation'] or {}
            new = validation_micro.evaluate_review(row['snapshot'], row['result'], row['prepared'])
            old_matches = {m['evidence_id']: m for m in (old.get('derived_validation') or {}).get('evidence_matches', [])}
            new_matches = (new.get('derived_validation') or {}).get('evidence_matches', [])
            changed = [{'evidence_id': m['evidence_id'], 'before': old_matches.get(m['evidence_id']), 'after': m}
                       for m in new_matches if old_matches.get(m['evidence_id'], {}).get('accepted') != m['accepted']]
            policy_comparison.append({'run_id': row['id'], 'historical_status': row['status'],
                                      'stored_policy': old.get('matching_policy') or (old.get('derived_validation') or {}).get('matching_policy'),
                                      'stored_source_valid': old.get('source_valid'),
                                      'new_source_valid': new['source_valid'], 'changes': changed,
                                      'new_evidence_matches': new_matches})
            row['validation'] = new
    latest = {}
    for r in rows:
        latest[r['prepared']['source_run_id']] = r
    values = [latest[t['source_run_id']] for t in plan['schedule']]
    packets, keys, forms = blind_material(plan, values)
    judged = judge_transitions(reviews, keys, packets, latest)
    paired = []
    for row in values:
        p, result, validation = row['prepared'], row['result'] or {}, row['validation'] or {}
        derived = result.get('derived_extraction')
        baseline = p['baseline_extraction']
        review = result.get('parsed_json') or {}
        paired.append(dict(page=row['page'], source_run_id=p['source_run_id'], review_run_id=row['id'],
                           repetition=row['repetition'], status=row['status'],
                           baseline_counts={k: len(baseline[k]) for k in ('concepts', 'entities', 'claims', 'relations')},
                           derived_counts={k: len(derived[k]) for k in ('concepts', 'entities', 'claims', 'relations')} if derived else None,
                           decisions={g: dict(Counter(d['action'] for d in review.get(g, []) if isinstance(d, dict) and 'action' in d))
                                      for g in ('claim_decisions', 'relation_decisions')} if isinstance(review, dict) else {},
                           structural_valid=validation.get('structural_valid'), source_valid=validation.get('source_valid'),
                           current_valid=validation.get('valid'),
                           evidence_counts=(validation.get('derived_validation') or {}).get('evidence_counts'),
                           errors=validation.get('structural_errors', []) + validation.get('source_errors', []),
                           usage=result.get('usage'), cost_usd=result.get('cost_usd'), latency_seconds=result.get('latency_seconds'),
                           finish_reason=result.get('finish_reason'), semantic_status='pending_independent_review'))
    data = dict(experiment=experiment, plan=plan, states=dict(Counter(r['status'] for r in values)),
                matching_policy=matching_policy or 'stored_validation', policy_comparison=policy_comparison,
                current_validation_states=dict(Counter('valid' if (r['validation'] or {}).get('valid') else 'invalid'
                                                       for r in values if r['validation'])),
                scheduled=plan['size'], blind_pairs_available=len(packets), pairs=paired,
                offline_controls=offline_controls(db,plan['source_experiment']),
                incremental_known_cost_usd=sum((r['result'] or {}).get('cost_usd') or 0 for r in rows),
                unknown_cost_attempts=[r['id'] for r in rows if r['status'] not in ('planned',) and (r['result'] or {}).get('cost_usd') is None],
                semantic_review=judged, inference_performed=False,
                limits=['Selected regression pages, not unseen corpus accuracy.',
                        'Same-model source check is not independent adjudication.',
                        'Source-invalid but structurally applicable corrections remain inspectable.',
                        'Blind packets hide arm, run IDs, model confidence and edit reasons; style/IDs may still provide clues.',
                        'Concept/entity objects are immutable by construction; retrieval is a separate A-v2.3 vs D-v2.5 diagnostic.'])
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / 'blind').mkdir(exist_ok=True)
    (target.parent / 'private').mkdir(exist_ok=True)
    for path, value in ((target.with_suffix('.json'), data),
                        (target.parent / 'private/unblinding.json', keys),
                        (target.parent / 'blind/judgments.template.json', {'decisions': DECISIONS, 'pairs': forms})):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (target.parent / 'blind/packets.jsonl').open('w', encoding='utf-8') as f:
        for packet in packets:
            f.write(json.dumps(packet, ensure_ascii=False) + '\n')
    lines = ['# D original versus D com conferência de relações', '', f'Experimento `{experiment}`.', '',
             f'{plan["size"]} conferências planejadas; {len(packets)} pares disponíveis para revisão cega.',
             'Nenhuma nova extração de controle. A = D congelado; B = saída derivada por decisões de conferência.', '',
             f'Custo incremental conhecido: US$ {data["incremental_known_cost_usd"]:.6f}. Custos desconhecidos: {len(data["unknown_cost_attempts"])} tentativas.',
             f'Objetos adjudicados externamente: {judged["adjudicated_objects"]}. Contagens não demonstram melhoria semântica.', '',
             f'Política do relatório: `{data["matching_policy"]}`. Validação atual: {data["current_validation_states"]}. Status históricos preservados.', '',
             f'Controles vazios arquivados disponíveis: {len(data["offline_controls"])}; zero chamadas de conferência para esses controles.', '',
             '| Página | Rep. | Run original | Conferência | Status original | Evidência aceita agora | Claims A/B | Relações A/B |',
             '|---|---:|---:|---:|---|---|---|---|']
    for pair in paired:
        a, b = pair['baseline_counts'], pair['derived_counts'] or {}
        lines.append(f'| {pair["page"]} | {pair["repetition"]} | {pair["source_run_id"]} | {pair["review_run_id"]} | {pair["status"]} | {pair["source_valid"]} | {a["claims"]}/{b.get("claims", "—")} | {a["relations"]}/{b.get("relations", "—")} |')
    lines += ['', '## Revisão independente', '',
              'Entregar somente `blind/packets.jsonl` e uma cópia de `blind/judgments.template.json` ao revisor. Não entregar este relatório nem `private/unblinding.json` antes da adjudicação.',
              'Julgar atribuição, alvo integral, modalidade, suporte e redundância de cada relação; correção e preservação de cada claim. Registrar assuntos ou relações úteis ausentes nas notas.',
              'Preencher decision, status=adjudicated, comment e source_refs; informar reviewer, source_checked e independent_of_generator. Não tratar a própria conferência do Muse como adjudicação.',
              'Usar `verify-report --reviews caminho/decisions.json` para calcular transições pareadas. Resultados incertos e parciais permanecem separados.', '',
              '## Transições adjudicadas', '', '```json', json.dumps(judged['counts'], ensure_ascii=False, indent=2), '```', '',
              '## Limites', '', *('- ' + x for x in data['limits']), '']
    target.write_text('\n'.join(lines), encoding='utf-8')
    return dict(experiment=experiment, report=str(target), states=data['states'],
                matching_policy=data['matching_policy'], current_validation_states=data['current_validation_states'],
                blind_pairs_available=len(packets), adjudicated_objects=judged['adjudicated_objects'], inference_performed=False)

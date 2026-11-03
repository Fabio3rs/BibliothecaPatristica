"""Read-only paired reports and arm-blinded v3 attribution review packets."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
from statistics import median

from .common import digest
from .statement_contract import project
from .statement_review_study import CRITERIA, STUDY
from .verification_contract import candidate_payload
from .verification_reporting import load_verification

DECISIONS = ('correct', 'incorrect', 'partially_correct', 'redundant', 'uncertain')
GROUPS = ('concepts', 'entities', 'claims', 'events', 'relations', 'evidence')


def neutral_output(data, seed):
    """Hide pV/evV edit prefixes, confidence, warnings and generation order."""
    out = candidate_payload(data)
    rng, ids = random.Random(seed), {}
    for group in GROUPS:
        rng.shuffle(out[group])
        for index, obj in enumerate(out[group], 1):
            ids[obj['id']] = f'{group}_{index}'

    def rewrite(value, key=None):
        if isinstance(value, dict):
            return {k: rewrite(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v, key) for v in value]
        if key in ('id', 'entity_id', 'source_id', 'target_id', 'evidence_ids'):
            return ids.get(value, value)
        return value
    return rewrite(out), ids


def units(data):
    result = []
    for claim in data['claims']:
        result.append((claim['id'], 'claim'))
        for field in ('attributions', 'participants'):
            result.extend((f'{claim["id"]}:{field}:{i}', field) for i, _ in enumerate(claim[field]))
    result.extend((r['id'], 'relation') for r in data['relations'])
    return result


def blind_packets(plan, rows):
    packets, keys, forms = [], {}, []
    for row in rows:
        result, validation = row['result'] or {}, row['validation'] or {}
        derived = result.get('derived_extraction')
        if not derived or not validation.get('structural_valid'):
            continue
        token = digest([plan['seed'], row['prepared']['source_run_id'], row['repetition'], row['id']])
        pair_id = 'PAIR-' + token[:16]
        order = ('A', 'B') if int(token[-1], 16) % 2 else ('B', 'A')
        originals = {'A': row['prepared']['baseline_extraction'], 'B': derived}
        outputs, mappings = {}, {}
        for label, arm in zip(('X', 'Y'), order):
            outputs[label], mappings[arm] = neutral_output(originals[arm], token + label)
        keys[pair_id] = {'source_run_id': row['prepared']['source_run_id'], 'review_run_id': row['id'],
                         'repetition': row['repetition'], 'arms': dict(zip(('X', 'Y'), order)),
                         'id_maps': mappings, 'hashes': {k: digest(v) for k, v in outputs.items()}}
        snap = row['snapshot']
        packets.append({'pair_id': pair_id, 'page': row['page'],
                        'instructions': 'Assess X and Y against the supplied sources independently. IDs/order are randomized; confidence and edit reasons are withheld. Judge complete propositions, each attribution and role. A quotation being located is not proof of semantic support. Record useful content lost or missing; do not prefer more or fewer objects.',
                        'criteria': list(CRITERIA), 'source': snap['source'], 'v2': snap['v2'],
                        'ocr': snap['ocr'], 'facsimile': snap['facsimile'], 'outputs': outputs})
        forms.append({'pair_id': pair_id, 'reviewer': '', 'independent_of_generator': False,
                      'source_checked': False, 'missing_or_lost_content': '', 'ambiguities': [],
                      'outputs': {label: {'extraction_hash': digest(data), 'units': [
                          {'unit_id': identifier, 'kind': kind, 'status': 'pending', 'decision': None,
                           'criteria': {c: None for c in CRITERIA}, 'source_refs': [], 'comment': ''}
                          for identifier, kind in units(data)]} for label, data in outputs.items()}})
    return packets, keys, forms


def adjudicate(path, packets, keys, rows):
    if not path:
        return {'adjudicated_units': 0, 'counts': {}, 'criteria_counts': {}, 'transitions': [],
                'semantic_improvement': 'not_established'}
    forms = json.loads(Path(path).read_text(encoding='utf-8'))['pairs']
    by_pair = {p['pair_id']: p for p in packets}
    by_run = {r['id']: r for r in rows}
    seen, judgments, counts, criteria_counts = set(), {}, Counter(), Counter()
    for form in forms:
        pair = form['pair_id']
        if pair not in keys or pair in seen:
            raise ValueError('Unknown or duplicate blinded pair')
        seen.add(pair)
        if set(form['outputs']) != {'X', 'Y'}:
            raise ValueError('Both blinded outputs must be present')
        for label, values in form['outputs'].items():
            if values['extraction_hash'] != keys[pair]['hashes'][label]:
                raise ValueError('Judgments refer to a changed extraction')
            known = dict(units(by_pair[pair]['outputs'][label]))
            ids = [u['unit_id'] for u in values['units']]
            if len(ids) != len(set(ids)) or set(ids) != set(known):
                raise ValueError('Judgment unit set differs')
            arm = keys[pair]['arms'][label]
            for u in values['units']:
                if u['kind'] != known[u['unit_id']] or u['status'] not in ('pending', 'adjudicated'):
                    raise ValueError('Invalid review unit or status')
                if u['status'] == 'pending':
                    continue
                if (not form.get('reviewer', '').strip() or not form.get('independent_of_generator')
                        or not form.get('source_checked') or not u.get('comment', '').strip()
                        or u.get('decision') not in DECISIONS):
                    raise ValueError('Adjudication requires independent named source review, decision and comment')
                refs = {('ocr_block', b['id']) for b in by_pair[pair]['ocr']['blocks']}
                refs |= {('summary_segment', s['id']) for s in by_pair[pair]['v2']['segments']}
                if not u['source_refs'] or any((r.get('source'), r.get('locator')) not in refs for r in u['source_refs']):
                    raise ValueError('Judgment requires current-page source references')
                if (set(u['criteria']) != set(CRITERIA)
                        or any(v not in (None, 'pass', 'fail', 'uncertain', 'not_applicable') for v in u['criteria'].values())):
                    raise ValueError('Invalid semantic criterion')
                judgments[pair, arm, u['unit_id']] = u['decision']
                counts[f'{arm}:{u["kind"]}:{u["decision"]}'] += 1
                for criterion, verdict in u['criteria'].items():
                    if verdict is not None:
                        criteria_counts[f'{arm}:{criterion}:{verdict}'] += 1
    transitions = []
    for pair, key in keys.items():
        review = by_run[key['review_run_id']]['result']['parsed_json']
        for group, field in (('claim_decisions', 'claim_id'), ('relation_decisions', 'relation_id')):
            for d in review[group]:
                before_id = key['id_maps']['A'][d[field]]
                before = judgments.get((pair, 'A', before_id))
                ids = [d[field]] if d['action'] == 'keep' else [o['id'] for o in d['replacements']]
                after = [judgments.get((pair, 'B', key['id_maps']['B'][i])) for i in ids]
                verdict = 'pending_or_uncertain'
                if before in ('correct', 'incorrect') and all(v in ('correct', 'incorrect') for v in after):
                    if not after:
                        verdict = 'correct_content_removed' if before == 'correct' else 'error_removed'
                    elif all(v == 'correct' for v in after):
                        verdict = 'correct_content_preserved' if before == 'correct' else 'error_corrected'
                    else:
                        verdict = 'correct_content_damaged' if before == 'correct' else 'error_remains'
                transitions.append({'pair_id': pair, 'original_id': d[field], 'group': group,
                                    'before': before, 'after': after, 'verdict': verdict})
    notes = [{'pair_id': f['pair_id'], 'missing_or_lost_content': f.get('missing_or_lost_content', ''),
              'ambiguities': f.get('ambiguities', [])} for f in forms]
    return {'adjudicated_units': len(judgments), 'counts': dict(counts), 'criteria_counts': dict(criteria_counts),
            'transitions': transitions, 'transition_counts': dict(Counter(t['verdict'] for t in transitions)),
            'qualitative_notes': notes,
            'semantic_improvement': 'assess_paired_source_judgments_and_omissions_not_node_counts'}


def content_signature(data):
    """Ignore local IDs, order and confidence; exact wording is still significant."""
    entities = {e['id']: (e['type'], e['label']) for e in data['entities']}
    claims = []
    for c in data['claims']:
        attrs = sorted((entities[a['entity_id']], a['stance'], a['scope']) for a in c['attributions'])
        parts = sorted((entities[a['entity_id']], a['role']) for a in c['participants'])
        claims.append(digest([c['proposition'], c['mode'], attrs, parts]))
    return sorted(claims)


def write_report(db, experiment, output, *, reviews=None):
    target = Path(output)
    reserved = [target, target.with_suffix('.json'), target.parent/'blind/judgments.template.json',
                target.parent/'blind/packets.jsonl', target.parent/'private/unblinding.json']
    if reviews and Path(reviews).resolve() in {p.resolve() for p in reserved}:
        raise ValueError('Copy judgments to a separate decisions file before review')
    plan, attempts = load_verification(db, experiment)
    if plan.get('study') != STUDY:
        raise ValueError('Expected statement-boundaries-v1 study')
    latest = {(r['prepared']['source_run_id'], r['repetition']): r for r in attempts}
    rows = [latest[t['source_run_id'], t['repetition']] for t in plan['schedule']]
    packets, keys, forms = blind_packets(plan, rows)
    judged = adjudicate(reviews, packets, keys, rows)
    paired, by_source = [], defaultdict(list)
    for row in rows:
        p, result, v = row['prepared'], row['result'] or {}, row['validation'] or {}
        baseline, derived = p['baseline_extraction'], result.get('derived_extraction')
        def counts(data):
            return dict(claims=len(data['claims']), relations=len(data['relations']),
                        attributions=sum(len(c['attributions']) for c in data['claims']),
                        participants=sum(len(c['participants']) for c in data['claims']),
                        projected_relations=len(project(data)['relations']))
        pair = dict(source_run_id=p['source_run_id'], review_run_id=row['id'], page=row['page'],
                    repetition=row['repetition'], status=row['status'], baseline_counts=counts(baseline),
                    derived_counts=counts(derived) if derived else None,
                    schema_valid=v.get('schema_valid'), structural_valid=v.get('structural_valid'),
                    source_valid=v.get('source_valid'), evidence_counts=(v.get('derived_validation') or {}).get('evidence_counts'),
                    errors=v.get('structural_errors', []) + v.get('source_errors', []),
                    usage=result.get('usage'), latency_seconds=result.get('latency_seconds'),
                    cost_usd=result.get('cost_usd'), finish_reason=result.get('finish_reason'),
                    output_limit_reached=(v.get('completion') or {}).get('output_limit_reached'))
        paired.append(pair)
        if derived:
            by_source[p['source_run_id']].append((row['id'], content_signature(derived)))
    stability = [{'source_run_id': source, 'available_repetitions': len(values),
                  'comparisons': [{'runs': [a[0], b[0]], 'identical_claim_content': a[1] == b[1]}
                                  for i, a in enumerate(values) for b in values[i+1:]],
                  'note': 'Exact proposition/role equality ignoring local IDs, order and confidence; not semantic equivalence.'}
                 for source, values in by_source.items()]
    usage_totals = {k: sum(((r['result'] or {}).get('usage') or {}).get(k) or 0 for r in attempts)
                    for k in ('input_tokens', 'output_tokens', 'reasoning_tokens')}
    latencies = [(r['result'] or {}).get('latency_seconds') for r in attempts]
    latencies = [v for v in latencies if isinstance(v, (float, int))]
    data = {'experiment': experiment, 'study': STUDY, 'scheduled': plan['size'], 'pages': plan['pages'],
            'states': dict(Counter(r['status'] for r in rows)), 'pairs': paired, 'stability': stability,
            'policy': plan['policy'], 'review_protocol': plan['review_protocol'],
            'blind_pairs_available': len(packets), 'semantic_review': judged,
            'usage_totals_known_attempts': usage_totals,
            'latency_median_seconds': median(latencies) if latencies else None,
            'incremental_known_cost_usd': sum((r['result'] or {}).get('cost_usd') or 0 for r in attempts),
            'unknown_cost_attempts': [r['id'] for r in attempts if r['status'] != 'planned' and (r['result'] or {}).get('cost_usd') is None],
            'inference_performed': False, 'gold_status': 'not_gold',
            'limits': ['Known development page(s); repeated outputs are not independent pages.',
                       'The same-model review is the intervention, not an independent judge.',
                       'Concepts/entities/events are fixed, so this experiment cannot repair their labels or measure subject-retrieval gains.',
                       'Arms, ID prefixes, confidence and edit reasons are hidden; wording and length can still suggest an arm.',
                       'Structural/evidence validity and exact repeat agreement do not establish semantic improvement.',
                       'Preserved original evidence is validated even if no longer referenced. Review-only/invalid derived outputs remain inspectable.']}
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent/'blind').mkdir(exist_ok=True)
    (target.parent/'private').mkdir(exist_ok=True)
    for path, value in ((target.with_suffix('.json'), data), (target.parent/'private/unblinding.json', keys),
                        (target.parent/'blind/judgments.template.json', {'decisions': DECISIONS, 'pairs': forms})):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (target.parent/'blind/packets.jsonl').open('w', encoding='utf-8') as f:
        for packet in packets:
            f.write(json.dumps(packet, ensure_ascii=False) + '\n')
    lines = ['# Proposições v3: original e revisão de fronteiras/atribuições', '', f'Experimento `{experiment}`.', '',
             f'{plan["size"]} chamadas planejadas; {len(plan["pages"])} página(s); {len(packets)} pares disponíveis.',
             'A reutiliza a extração v3 original; B adiciona uma única conferência. Repetições de B são independentes entre si.',
             f'Custo incremental conhecido: US$ {data["incremental_known_cost_usd"]:.6f}. Tentativas sem custo conhecido: {len(data["unknown_cost_attempts"])}.',
             f'Tokens informados (entrada/saída/raciocínio incluído na saída): {usage_totals["input_tokens"]}/{usage_totals["output_tokens"]}/{usage_totals["reasoning_tokens"]}. Latência mediana: {data["latency_median_seconds"]} s.',
             f'Unidades adjudicadas independentemente: {judged["adjudicated_units"]}. Melhoria semântica não é inferida das contagens.', '',
             '| Página | Origem | Rep. | Revisão | Status | Estrutura | Localização | Claims A/B | Atribuições A/B |',
             '|---|---:|---:|---:|---|---|---|---|---|']
    for p in paired:
        a, b = p['baseline_counts'], p['derived_counts'] or {}
        lines.append(f'| {p["page"]} | {p["source_run_id"]} | {p["repetition"]} | {p["review_run_id"]} | {p["status"]} | {p["structural_valid"]} | {p["source_valid"]} | {a["claims"]}/{b.get("claims", "—")} | {a["attributions"]}/{b.get("attributions", "—")} |')
    lines += ['', '## Protocolo de avaliação', '',
              'Julgar a proposição completa e, separadamente, cada atribuição, participante e relação. Conferir titular/voz, alvo integral, condição/negação, distinção entre citação e comentário, suporte e conteúdo útil perdido.',
              'Entregar apenas `blind/packets.jsonl` e uma cópia de `blind/judgments.template.json` ao revisor. Não entregar este relatório nem `private/unblinding.json` antes da revisão.',
              'Preencher status=adjudicated, decision, comment, source_refs e os critérios aplicáveis (pass/fail/uncertain/not_applicable). Informar reviewer, independent_of_generator e source_checked. Usar `verify-report --reviews decisions.json` para importar os julgamentos sem alterar o banco.',
              'As notas missing_or_lost_content e ambiguities permanecem qualitativas. Mesmo uma substituição correta pode ter perdido conteúdo; examinar essas notas antes de decidir.', '',
              '## Transições adjudicadas', '', '```json', json.dumps(judged.get('transition_counts', {}), indent=2), '```', '',
              '## Estabilidade', '', '```json', json.dumps(stability, ensure_ascii=False, indent=2), '```', '',
              '## Limites', '', *('- ' + note for note in data['limits']), '']
    target.write_text('\n'.join(lines), encoding='utf-8')
    return {k: data[k] for k in ('experiment', 'scheduled', 'states', 'blind_pairs_available', 'incremental_known_cost_usd', 'inference_performed')} | {
        'report': str(target), 'adjudicated_units': judged['adjudicated_units']}

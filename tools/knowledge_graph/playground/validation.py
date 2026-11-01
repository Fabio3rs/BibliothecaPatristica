"""Three separate tiers: schema/IDs, source location, human semantics."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

from jsonschema import Draft202012Validator
from .common import SCHEMA_PATH, read_json
from .evidence_matching import MATCHING_POLICY, locate_quote

VALIDATION_VERSION = 'kg-validation-6'
NODES = ('concepts', 'entities', 'claims', 'events')
PARENTS = (*NODES, 'relations')


def response_text(raw):
    """Unwrap one complete Markdown fence; never search for or repair JSON."""
    if not isinstance(raw, str):
        return raw, 'object'
    match = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```',
                         raw.strip(), flags=re.IGNORECASE)
    return (match['body'], 'markdown_fence') if match else (raw, 'json')


def graph_inspectable(result):
    """Schema-checked output may be inspected without declaring it valid.

    Graph IDs must be unique together; evidence IDs must be unique separately.
    A collision across these namespaces is still a structural error, but does
    not make graph edges or evidence_ids ambiguous for a typed viewer.
    """
    graph_ids = [o['id'] for group in PARENTS for o in result[group]]
    evidence_ids = [e['id'] for e in result['evidence']]
    nodes = {o['id'] for group in NODES for o in result[group]}
    return (len(set(graph_ids)) == len(graph_ids)
            and len(set(evidence_ids)) == len(evidence_ids)
            and all(i.strip() for i in graph_ids + evidence_ids)
            and all(r[key] in nodes for r in result['relations'] for key in ('source_id', 'target_id'))
            and all(ref in evidence_ids for group in PARENTS for o in result[group] for ref in o['evidence_ids']))


def completion_details(result, prepared):
    """Keep the provider's total distinct from its reasoning-token subset."""
    usage = result.get('usage') or {}
    def count(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    total, reasoning = count('output_tokens'), count('reasoning_tokens')
    visible = total - reasoning if total is not None and reasoning is not None and reasoning <= total else None
    config = prepared.get('config') or {}
    return {'finish_reason': result.get('finish_reason'),
            'output_limit_reached': result.get('finish_reason') in {'length', 'max_tokens'},
            'max_output_tokens': config.get('max_output_tokens'),
            'output_tokens': total, 'reasoning_tokens': reasoning,
            'visible_output_tokens': visible,
            'visible_output_tokens_basis': 'output_minus_reasoning' if visible is not None else 'unavailable'}


def evaluate_response(source, result, prepared):
    """Use the same response-aware checks for execution and offline revalidation."""
    checked = evaluate(source, result.get('raw_response', ''), prepared['schema'], prepared['mode'])
    completion = completion_details(result, prepared)
    checked['completion'] = completion
    if completion['output_limit_reached']:
        message = 'O provedor interrompeu a resposta ao atingir o limite de saída'
        if completion['max_output_tokens'] is not None:
            message += f" de {completion['max_output_tokens']} tokens"
        message += '.'
        if completion['reasoning_tokens'] is not None:
            message += f" Raciocínio reportado: {completion['reasoning_tokens']} tokens."
        if completion['visible_output_tokens'] is not None:
            message += f" Texto visível por diferença: {completion['visible_output_tokens']} tokens."
        message += ' Aumente --max-output-tokens (ou Máximo de tokens na interface) antes de uma nova execução. Revalidar não recupera conteúdo ausente; não há repetição automática.'
        checked['warnings'].append('provider_output_limit_reached')
        checked['diagnostics'].insert(0, {'code': 'provider_output_limit_reached', 'message': message})
        checked['valid'] = False
        checked['validated_extraction'] = None
    return checked


def evaluate(source, raw, schema=None, mode=None):
    schema = schema if schema is not None else read_json(SCHEMA_PATH)
    errors, source_errors, warnings, diagnostics, evidence_matches = [], [], [], [], []
    text, response_format = response_text(raw)
    if response_format == 'markdown_fence':
        warnings.append('markdown_fence_removed')
        diagnostics.append({'code': 'markdown_fence_removed',
                            'message': 'Invólucro Markdown removido apenas para leitura do JSON; resposta bruta preservada. Nenhum ID ou conteúdo foi corrigido.'})
    result = None
    try:
        def reject_constant(value):
            raise ValueError(f'Non-JSON numeric constant: {value}')
        def unique_keys(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError(f'Duplicate JSON property: {key}')
                out[key] = value
            return out
        if not isinstance(text, str):
            json.dumps(text, allow_nan=False)
        result = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys) if isinstance(text, str) else text
    except (ValueError, TypeError) as exc:
        errors.append(f'json: {exc}')
    if result is not None:
        errors.extend(f'schema: {e.json_path}: {e.message}' for e in Draft202012Validator(schema).iter_errors(result))
    elif not errors:
        errors.append('schema: null is not an extraction object')
    # Invalid shapes stay inspectable, but must never crash reference validation.
    well_shaped = isinstance(result, dict) and not errors
    inspectable = well_shaped and graph_inspectable(result)
    conflicts = []
    if well_shaped:
        groups = {k: result[k] for k in (*PARENTS, 'evidence')}
        if mode == 'concepts_only' and any(groups[k] for k in ('entities', 'claims', 'events', 'relations')):
            errors.append('mode: concepts_only requires empty optional structures')
        ids = [o['id'] for objects in groups.values() for o in objects]
        errors.extend(f'duplicate id: {k}' for k, n in Counter(ids).items() if n > 1)
        conflicts = [{'id': identifier, 'groups': [group for group, objects in groups.items()
                                                  if any(o['id'] == identifier for o in objects)]}
                     for identifier, count in Counter(ids).items() if count > 1]
        if conflicts:
            diagnostics.append({'code': 'duplicate_local_ids', 'conflicts': conflicts,
                                'message': 'IDs locais repetidos: ' + '; '.join(
                                    f"{c['id']} ({', '.join(c['groups'])})" for c in conflicts)
                                + '. Entidades e evidências devem usar IDs distintos (por exemplo, e1 e ev1).'})
        if any(not i.strip() for i in ids):
            errors.append('empty local id')
        node_ids = {o['id'] for k in NODES for o in groups[k]}
        evidence_ids = {o['id'] for o in groups['evidence']}
        for k in PARENTS:
            for obj in groups[k]:
                for ref in obj['evidence_ids']:
                    if ref not in evidence_ids:
                        errors.append(f'{k}/{obj["id"]}: missing evidence {ref}')
                if not obj['evidence_ids'] and obj.get('origin') != 'bibliographic_context':
                    warnings.append(f'ungrounded_object:{obj["id"]}')
        for rel in groups['relations']:
            for endpoint in ('source_id', 'target_id'):
                if rel[endpoint] not in node_ids:
                    errors.append(f'relation {rel["id"]}: invalid {endpoint}')
            if rel['predicate'] in {'DEFENDS', 'REFUTES', 'AFFIRMS'}:
                diagnostics.append({'object_id': rel['id'], 'code': 'stance_requires_semantic_review',
                                    'question': 'Who holds the proposition, and who rejects, quotes or affirms it? Check author/editor boundaries.'})
        originals = {
            'ocr_block': {b['id']: b['text'] for b in source['ocr']['blocks']},
            'summary_segment': {s['id']: s['summary'] for s in source['v2']['segments']},
        }
        for ev in groups['evidence']:
            text = originals.get(ev['source'], {}).get(ev['locator'])
            match = dict(locate_quote(ev['quote'], text), evidence_id=ev['id'], source=ev['source'], locator=ev['locator'])
            evidence_matches.append(match)
            if not match['accepted']:
                source_errors.append(f'{ev["id"]}: {match["reason"]} ({ev["source"]}:{ev["locator"]})')
                if match.get('review_candidate'):
                    diagnostics.append({'object_id': ev['id'], 'code': 'protected_quote_difference',
                                        'message': f'{ev["id"]}: trecho semelhante localizado, mas há diferenças em números, operadores ou negações. Revisar o diff normalizado em evidence_matches; a correspondência não foi aceita automaticamente.'})
            elif match['method'] != 'exact':
                diagnostics.append({'object_id': ev['id'], 'code': 'approximate_quote_match',
                                    'message': f'{ev["id"]}: trecho localizado por correspondência aproximada ({match["method"]}); citação original preservada.'})
        by_label = Counter(unicodedata.normalize('NFC', c['label']).casefold().strip() for c in groups['concepts'])
        warnings.extend(f'duplicate_concept_label_review:{label}' for label, n in by_label.items() if n > 1)
        for c in groups['concepts']:
            if c['evidence_ids'] and all(next((e['source'] for e in groups['evidence'] if e['id'] == eid), '') == 'summary_segment' for eid in c['evidence_ids']):
                warnings.append(f'summary_only_grounding:{c["id"]}')
    return {'version': VALIDATION_VERSION, 'response_format': response_format,
            'format_compliant': isinstance(result, dict) and response_format != 'markdown_fence',
            'schema_valid': well_shaped, 'graph_inspectable': inspectable,
            'id_conflicts': conflicts, 'structural_valid': not errors,
            'source_valid': not source_errors if well_shaped else None,
            'source_exact': all(m['method'] == 'exact' for m in evidence_matches) if well_shaped else None,
            'matching_policy': MATCHING_POLICY, 'evidence_matches': evidence_matches,
            'evidence_counts': {method: sum(m['method'] == method for m in evidence_matches)
                                for method in ('exact', 'normalized', 'fuzzy', 'ambiguous', 'not_found', 'unknown_locator')},
            'valid': not errors and not source_errors,
            'structural_errors': errors, 'source_errors': source_errors,
            'warnings': warnings, 'diagnostics': diagnostics,
            'semantic_status': 'pending_review', 'gold_status': 'not_gold',
            'parsed': result, 'validated_extraction': result if not errors and not source_errors else None}

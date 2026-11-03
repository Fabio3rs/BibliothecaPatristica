"""Independent navigation-v1 validation: location is not semantic verification."""
from collections import Counter
import json
from pathlib import Path
import re
import unicodedata

from jsonschema import Draft202012Validator

from .common import digest
from .evidence_locator_v5 import POLICY, editorial_notes, locate_on_page, locate_quote
from .validation import completion_details, response_text

VERSION = 'kg-navigation-validation-1'
COLLECTIONS = ('concepts', 'arguments', 'evidence')


def prepare_input(payload):
    """Project known bibliographic records, excluding external index excerpts."""
    metadata = payload['v2'].get('metadata_context', {})
    allowed = {'work_key', 'work_order', 'volume_id', 'title_raw', 'title_norm',
               'author_raw', 'confidence', 'start_file', 'end_file', 'start_page', 'end_page'}
    omitted = []
    if 'index_works' in metadata:
        projected = []
        for i, work in enumerate(metadata['index_works']):
            omitted.extend(f'v2.metadata_context.index_works[{i}].{k}' for k in work if k not in allowed)
            projected.append({k: v for k, v in work.items() if k in allowed})
        metadata['index_works'] = projected
    reject_editorial_hints(payload)
    return {'version': 'navigation-no-hints-v1', 'omitted_metadata_paths': omitted,
            'note': 'Only bibliographic fields from index_works; archived snapshot unchanged.'}


def reject_editorial_hints(payload):
    """Fail closed for extra index/hint inputs; never alter the archived snapshot.

    OCR/summary prose is not searched or stripped. Existing page/work metadata
    is retained. This policy deliberately rejects future hint metadata too.
    """
    def visit(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                words = re.sub(r'([a-z])([A-Z])', r'\1_\2', key).lower()
                page_kind_signals = path == 'input.v2.metadata_context.static_analysis' and words == 'page_kind_hints'
                if not page_kind_signals and any(term in words for term in ('hint', 'external_index', 'editorial_index',
                                                  'subject_index', 'onomastic_index', 'thematic_index',
                                                  'alphabetical_index')):
                    raise ValueError(f'Navigation input excludes editorial hints/indexes: {path}.{key}')
                visit(child, f'{path}.{key}')
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, f'{path}[{i}]')
    visit(payload, 'input')


def _unique_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f'Duplicate JSON property: {key}')
        out[key] = value
    return out


def _reject_constant(value):
    raise ValueError(f'Non-JSON numeric constant: {value}')


def evaluate_response(source, response, prepared, *, matching_policy=None):
    policy = matching_policy or prepared.get('validation_policy', POLICY)
    if policy != POLICY:
        raise ValueError('kg-navigation-v1 requires the frozen local-quote-v5 policy')
    errors, source_errors, warnings, diagnostics, matches, conflicts = [], [], [], [], [], []
    raw, response_format = response_text(response.get('raw_response', ''))
    parsed = None
    try:
        if isinstance(raw, str):
            parsed = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
        else:
            json.dumps(raw, allow_nan=False)
            parsed = raw
        errors.extend(f'schema: {e.json_path}: {e.message}'
                      for e in Draft202012Validator(prepared['schema']).iter_errors(parsed))
    except (ValueError, TypeError) as exc:
        errors.append(f'json: {exc}')
    if response_format == 'markdown_fence':
        warnings.append('markdown_fence_removed')
    schema_valid = not errors
    if schema_valid:
        ids = [obj['id'] for group in COLLECTIONS for obj in parsed[group]]
        for identifier, count in Counter(ids).items():
            if count > 1:
                errors.append(f'duplicate id: {identifier}')
                conflicts.append({'id': identifier, 'groups': [g for g in COLLECTIONS
                                  if any(o['id'] == identifier for o in parsed[g])]})
        concepts = {c['id'] for c in parsed['concepts']}
        evidence = {e['id']: e for e in parsed['evidence']}
        for group in ('concepts', 'arguments'):
            for obj in parsed[group]:
                for ref in obj['evidence_ids']:
                    if ref not in evidence:
                        errors.append(f'{group}/{obj["id"]}: missing evidence {ref}')
                if all(evidence.get(i, {}).get('source') == 'summary_segment' for i in obj['evidence_ids']):
                    warnings.append(f'summary_only_grounding:{obj["id"]}')
        for argument in parsed['arguments']:
            for ref in argument['concept_ids']:
                if ref not in concepts:
                    errors.append(f'arguments/{argument["id"]}: missing concept {ref}')
        if prepared['mode'] == 'concepts_only' and parsed['arguments']:
            errors.append('mode: concepts_only requires empty arguments')
        labels = Counter(unicodedata.normalize('NFC', c['label']).casefold().strip() for c in parsed['concepts'])
        warnings.extend(f'duplicate_concept_label_review:{label}' for label, n in labels.items() if n > 1)
        blocks = source['ocr']['blocks']
        summaries = {s['id']: s['summary'] for s in source['v2']['segments']}
        definitions = editorial_notes(blocks)
        block_ids = {b['id'] for b in blocks}
        for ev in parsed['evidence']:
            exists = ev['locator'] in (block_ids if ev['source'] == 'ocr_block' else summaries)
            match = (locate_on_page(ev['quote'], ev['locator'], blocks, definitions=definitions)
                     if ev['source'] == 'ocr_block' and exists else
                     locate_quote(ev['quote'], summaries.get(ev['locator']) if ev['source'] == 'summary_segment' else None))
            match.update(evidence_id=ev['id'], source=ev['source'], locator=ev['locator'])
            matches.append(match)
            if not match['accepted']:
                source_errors.append(f'{ev["id"]}: {match.get("reason", "Passage not located")} ({ev["source"]}:{ev["locator"]})')
            elif match['method'] != 'exact' or match.get('resolved_locator', ev['locator']) != ev['locator']:
                diagnostics.append({'object_id': ev['id'], 'code': 'located_quote_requires_review',
                                    'message': 'Trecho localizado com diferenças ou posição a revisar; localização não confirma interpretação.'})
    completion = completion_details(response, prepared)
    if completion['output_limit_reached']:
        warnings.append('provider_output_limit_reached')
        diagnostics.append({'code': 'provider_output_limit_reached',
                            'message': 'O provedor atingiu o limite de saída; resposta bruta preservada, sem reparo automático.'})
    valid = schema_valid and not errors and not source_errors and not completion['output_limit_reached']
    methods = ('exact', 'normalized', 'fuzzy', 'note_marker', 'ambiguous', 'review_only', 'not_found', 'unknown_locator')
    return {'version': VERSION, 'response_format': response_format,
            'format_compliant': isinstance(parsed, dict) and response_format != 'markdown_fence',
            'schema_valid': schema_valid, 'structural_valid': not errors,
            'graph_inspectable': schema_valid and not errors, 'id_conflicts': conflicts,
            'source_valid': not source_errors if schema_valid else None,
            'source_exact': all(m['method'] == 'exact' for m in matches) if schema_valid else None,
            'matching_policy': policy, 'validation_target': 'textual_location',
            'policy_implementation': {name: digest(Path(__file__).with_name(name).read_bytes())
                                      for name in ('navigation_contract.py', 'evidence_locator_v5.py',
                                                   'evidence_locator_v4.py', 'evidence_locator.py', 'evidence_matching.py')},
            'evidence_matches': matches,
            'evidence_counts': {m: sum(x['method'] == m for x in matches) for m in methods},
            'valid': valid, 'structural_errors': errors, 'source_errors': source_errors,
            'warnings': warnings, 'diagnostics': diagnostics, 'completion': completion,
            'semantic_status': 'pending_review', 'gold_status': 'not_gold',
            'parsed': parsed, 'validated_extraction': parsed if valid else None}

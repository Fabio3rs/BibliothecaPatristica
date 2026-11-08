"""Read-only diagnostics of complete and truncated provider responses."""
from collections import Counter
import json
from pathlib import Path
import re

from .contract import digest, write_json
from .sources import check_sources
from .validation import evidence_spans


def diagnose(dto, task):
    page = task['pages'][0]
    lines = {l['id']: l for l in page['lines']}
    assigned = Counter(lid for s in dto.get('segments', []) if s.get('page_id') == page['page_id'] for lid in s.get('line_ids', []))
    accounted = Counter(lid for c in dto.get('coverage', []) if c.get('page_id') == page['page_id'] for lid in c.get('line_ids', []))
    evidence_errors = []
    for group in ('segments', 'sections', 'entries', 'references', 'scripture_refs', 'continuations',
                  'editorial_rules', 'edition_mappings', 'unresolved'):
        for obj in dto.get(group, []):
            for n, ev in enumerate(obj.get('evidence', [])):
                try:
                    evidence_spans([ev], task)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    evidence_errors.append({'group': group, 'id': obj.get('id'), 'evidence_index': n, 'error': str(exc)})
    refs = Counter(r.get('entry_id') for r in dto.get('references', []))
    numeric_without_refs = [e['id'] for e in dto.get('entries', []) if e.get('kind') in ('lemma', 'sublemma')
                            and re.search(r'\d', e.get('entry_raw', '')) and not refs[e['id']]]
    return {'counts': {k: len(dto.get(k, [])) for k in ('entries', 'references', 'edition_mappings', 'unresolved')},
        'primary_lines': len(lines), 'ownership_missing': sorted(set(lines)-assigned.keys()),
        'coverage_missing': sorted(set(lines)-accounted.keys()),
        'coverage_duplicated': [k for k, n in accounted.items() if n > 1],
        'entries_with_numbers_without_material_references': numeric_without_refs,
        'numeric_detection_is_a_review_heuristic': True,
        'evidence_errors': evidence_errors,
        'evidence_error_counts': dict(Counter(e['error'] for e in evidence_errors))}


def replay_run(source, out, db, review_file=None):
    """Explicit offline replay preserves the paid run and records current validators."""
    from .runner import stage
    manifest = json.loads((source/'manifest.json').read_text())
    review = json.loads(Path(review_file).read_text()) if review_file else None
    if review and not any(json.loads((Path(i['directory'])/'task.json').read_text())['task_id'] == review.get('task_id') for i in manifest['tasks']):
        raise ValueError('Review task is not present in source run')
    if out.exists():
        raise ValueError('Use a new offline audit directory')
    out.mkdir(parents=True)
    rows = []
    for item in manifest['tasks']:
        directory = Path(item['directory'])
        task = json.loads((directory/'task.json').read_text())
        check_sources(task)
        raw = (directory/'response.json').read_bytes()
        checkpoint = json.loads((directory/'checkpoint.json').read_text())
        if checkpoint['input_fingerprint'] != item['input_fingerprint'] or checkpoint['response_sha256'] != digest(raw):
            raise ValueError('Archived response identity/hash mismatch')
        response = json.loads(raw)
        row = {'task_id': task['task_id'], 'transport_status': response['status'], 'error': response.get('error'),
               'output_tokens': response.get('output_tokens'), 'response_sha256': digest(raw),
               'original_task_directory': str(directory)}
        dest = out/'tasks'/task['task_id']
        write_json(dest/'task.json', task)
        if review and review['task_id'] == task['task_id']:
            from .review import reviewed_response
            response = reviewed_response(response, task, digest(raw), review)
            write_json(dest/'visual_review.json', review)
            write_json(dest/'original_reading.json', json.loads(raw)['dto'])
            row['offline_review'] = response['offline_review']
        if response['status'] == 'received':
            result = stage({'task': task, 'directory': dest, 'fingerprint': item['input_fingerprint'], 'cache_hit': True}, response, db)
            extraction = dest/'extraction.json'
            if extraction.exists():
                row['diagnostics'] = diagnose(json.loads(extraction.read_text()), task)
            alignment = dest/'alignment.json'
            if alignment.exists():
                aligned = json.loads(alignment.read_text())
                row['alignment_complete'] = aligned['complete']
                row['pending_by_reason'] = dict(Counter(p['reason'] for p in aligned['pending']))
                row['mechanical_annotations'] = len(aligned.get('mechanical_annotations', []))
            row['revalidation_status'] = result['status']
            row['validation_errors'] = result['validation']['errors']
        else:
            row['revalidation_status'] = 'not_replayed_incomplete_or_error'
        write_json(dest/'audit.json', row)
        rows.append(row)
    report = {'source_run': str(source.resolve()), 'calls': 0, 'db_updates': 0,
        'paid_artifacts_modified': False,
        'validator_sha256': digest((Path(__file__).parent/'validation.py').read_bytes()),
        'code_sha256': digest({p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))}),
        'statuses': dict(Counter(r['revalidation_status'] for r in rows)), 'tasks': rows}
    write_json(out/'audit.json', report)
    return report

"""Compare independent runs against bounded, source-anchored verification scopes."""
from collections import Counter
import json
from pathlib import Path

from .contract import ROOT, digest


def metrics(predicted, expected):
    tp = sum((predicted & expected).values())
    np, ng = sum(predicted.values()), sum(expected.values())
    return {'true_positive': tp, 'false_positive': np-tp, 'false_negative': ng-tp,
            'precision': tp/np if np else None, 'recall': tp/ng if ng else None}


def annotated_metrics(dto, task, annotation):
    page = task['pages'][0]
    if page['ocr_sha256'] != annotation['ocr_sha256']:
        return {'status': 'stale_annotation'}
    if annotation.get('image_sha256') and page['regions']:
        full = next((r for r in page['regions'] if r['transformation'] == 'none'), None)
        if not full or full['sha256'] != annotation['image_sha256']:
            return {'status': 'stale_visual_annotation'}
    entry_predictions, entry_expected = Counter(), Counter()
    ref_predictions, ref_expected = Counter(), Counter()
    concat = 0
    for scope in annotation['scopes']:
        line_ids = set(scope['line_ids'])
        predictions = [e for e in dto['entries'] if line_ids.intersection(
            lid for ev in e['evidence'] for lid in ev['line_ids'])]
        for e in scope['entries']:
            signature = (e['lemma_raw'], e['entry_raw'])
            entry_expected[signature] += 1
            for r in e['references']:
                ref_expected[(e['lemma_raw'], r['ref_raw'], r['page_start'], r['line_start'], r['series'])] += 1
        for e in predictions:
            entry_predictions[(e['lemma_raw'], e['entry_raw'])] += 1
            if sum(g['entry_raw'] in e['entry_raw'] for g in scope['entries']) > 1:
                concat += 1
            for r in dto['references']:
                if r['entry_id'] == e['id']:
                    ref_predictions[(e['lemma_raw'], r['ref_raw'], r['page_start'], r['line_start'], r['series'])] += 1
    return {'status': 'scored_partial_scopes', 'annotation_review': annotation['review'],
            'segmentation': metrics(entry_predictions, entry_expected),
            'reference_attribution_and_interpretation': metrics(ref_predictions, ref_expected),
            'concatenated_entries_in_annotated_scopes': concat,
            'duplicate_entry_predictions': sum(max(0, n-1) for n in entry_predictions.values()),
            'scope_count': len(annotation['scopes']),
            'not_a_corpus_accuracy_estimate': True}


def compare(runs, gold):
    annotations = {(a['volume'], a['physical_page']): a for a in gold['pages']}
    rows = []
    for run in runs:
        manifest = json.loads((run/'manifest.json').read_text())
        for task_info in manifest['tasks']:
            directory = Path(task_info['directory'])
            task = json.loads((directory/'task.json').read_text())
            page = task['pages'][0]
            result_path = directory/'result.json'
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            extraction_path = directory/'extraction.json'
            dto = json.loads(extraction_path.read_text()) if extraction_path.exists() else None
            row = {'run': str(run), 'task_id': task['task_id'], 'mode': task['mode'],
                'extraction_profile': task.get('extraction_profile', 'reading-v2'),
                'volume': task['volume'], 'physical_page': page['physical_sequence'],
                'ocr_sha256': page['ocr_sha256'], 'input_fingerprint': task_info['input_fingerprint'],
                'status': result.get('status', 'prepared_not_evaluated'), 'usage': result.get('usage'),
                'counts': result.get('validation', {}).get('counts'),
                'coverage': result.get('validation', {}).get('coverage'),
                'validation_errors': result.get('validation', {}).get('errors')}
            literal_path = directory/'literal.json'
            if literal_path.exists() and result.get('validation', {}).get('valid'):
                from .literal import walk
                literal = json.loads(literal_path.read_text())
                row['literal_entries'] = [
                    {'path': path, 'parent': parent, 'name': item['name'], 'text': item['text'],
                     'references_raw': item['references_raw']}
                    for path, parent, item in walk(literal['elements']) if item['type'] == 'entry']
            annotation = annotations.get((task['volume'], page['physical_sequence']))
            if dto and result.get('validation', {}).get('valid') and annotation:
                row['verification'] = annotated_metrics(dto, task, annotation)
            else:
                row['verification'] = {'status': 'literal_annotation_required' if 'literal_entries' in row else
                                       'no_valid_response' if not dto else 'no_applicable_annotation'}
            rows.append(row)
    groups = {}
    for row in rows:
        key = (row['volume'], row['physical_page'])
        groups.setdefault(key, []).append(row)
    return {'rows': rows, 'source_comparability': [
        {'volume': v, 'physical_page': n, 'modes': sorted({r['mode'] for r in group}),
         'same_ocr': len({r['ocr_sha256'] for r in group}) == 1,
         'profiles': sorted({r['extraction_profile'] for r in group}),
         'note': 'Compare model/prompt/schema fingerprints before attributing differences to images.'}
        for (v, n), group in groups.items()],
        'gold_sha256': digest(gold), 'quality_warning': 'Counts are not accuracy. Metrics cover only annotated source scopes; the seed annotation needs independent review.'}

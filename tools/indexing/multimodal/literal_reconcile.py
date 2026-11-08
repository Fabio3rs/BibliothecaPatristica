"""Offline, literal source anchoring. No editorial interpretation or DB writes."""
from collections import Counter, defaultdict
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, ValidationError

from .alignment import Aligner
from .contract import digest, write_json
from .literal import SCHEMA, walk
from .sources import check_sources

VERSION = 'literal-reconciliation-v1'
TOKEN = re.compile(r'\w+|[^\w\s]', re.UNICODE)
WRAP = re.compile(r'(?<=[^\W\d_])[-\u00ad]\s*\n\s*(?=[^\W\d_])')
# These are literal groups, NOT parsed page/line values. Other notation stays intact.
NUMERIC_GROUP = r'[0-9]+(?:\s*,\s*[0-9]+)*\s*\.'


def alphabetic_wraps(text):
    for match in WRAP.finditer(text):
        left = re.search(r'\w+$', text[:match.start()]).group()
        right = re.match(r'\w+', text[match.end():]).group()
        # Roman-number ranges are not words split by line wrapping.
        if all(re.fullmatch(r'[IVXLCDMivxlcdm]+', word) for word in (left, right)):
            continue
        yield match


def tokens(text, offsets=None, join_wraps=False):
    offsets = list(range(len(text))) if offsets is None else offsets
    omitted = {i for m in alphabetic_wraps(text) for i in range(*m.span())} if join_wraps else set()
    kept = [i for i in range(len(text)) if i not in omitted]
    visible = ''.join(text[i] for i in kept)
    return [{'value': m.group(), 'start': offsets[kept[m.start()]],
             'end': offsets[kept[m.end()-1]] + 1}
            for m in TOKEN.finditer(visible)]


class TokenIndex:
    def __init__(self, items):
        self.items = items
        self.values = [t['value'] for t in items]
        self.starts = defaultdict(list)
        for i, value in enumerate(self.values):
            self.starts[value].append(i)

    def find(self, values):
        if not values:
            return []
        size = len(values)
        return [(i, i+size) for i in self.starts.get(values[0], [])
                if self.values[i:i+size] == values]


def unique_chain(groups):
    """Count ordered nonoverlapping assignments, saturating at two; never guess."""
    states = [(0, 1, [])]
    for candidates in groups:
        following = []
        for a, b in candidates:
            prior = [(count, path) for end, count, path in states if end <= a]
            count = min(2, sum(n for n, _ in prior))
            if count:
                following.append((b, count, prior[0][1] + [(a, b)]))
        states = following
        if not states:
            return 'not_found', []
    count = min(2, sum(n for _, n, _ in states))
    return ('aligned', states[0][2]) if count == 1 else ('ambiguous', [])


def anchor_entry(text, source):
    query = tokens(text)
    values = [t['value'] for t in query]
    matches = source.find(values)
    if len(matches) > 1:
        return {'status': 'ambiguous', 'reason': 'multiple_complete_occurrences'}, query, {}
    if matches:
        left, right = matches[0]
        opcodes = [('equal', 0, len(query), 0, right-left)]
    else:
        if len(query) < 24:
            return {'status': 'unanchored', 'reason': 'no_complete_literal_match'}, query, {}
        first, last = values[:12], values[-12:]
        if any(sum(c.isalpha() for c in ''.join(v)) < 12 for v in (first, last)):
            return {'status': 'unanchored', 'reason': 'insufficient_textual_anchors'}, query, {}
        starts, ends = source.find(first), source.find(last)
        if len(starts) != 1 or len(ends) != 1:
            return {'status': 'unanchored', 'reason': 'outer_anchors_not_unique'}, query, {}
        left, right = starts[0][0], ends[0][1]
        if not .5 * len(query) <= right-left <= 2 * len(query):
            return {'status': 'unanchored', 'reason': 'outer_anchors_order_or_distance'}, query, {}
        opcodes = SequenceMatcher(None, values, source.values[left:right], autojunk=False).get_opcodes()
    mapping, segments, differences = {}, [], []
    local = TokenIndex(source.items[left:right])
    query_index = TokenIndex(query)
    complete = all(op[0] == 'equal' for op in opcodes)
    for tag, a, b, c, d in opcodes:
        if tag == 'equal':
            block = values[a:b]
            # On partial entries only independently unique, textual blocks provide evidence.
            if not complete and (len(block) < 5 or sum(ch.isalpha() for ch in ''.join(block)) < 12
                                 or len(local.find(block)) != 1 or len(query_index.find(block)) != 1):
                continue
            mapping.update({i: left+c+i-a for i in range(a, b)})
            segments.append({'query_start': query[a]['start'], 'query_end': query[b-1]['end'],
                             'char_start': source.items[left+c]['start'],
                             'char_end': source.items[left+d-1]['end']})
        else:
            differences.append({'operation': tag, 'query_tokens': values[a:b],
                                'ocr_tokens': source.values[left+c:left+d],
                                'query_token_start': a, 'ocr_token_start': left+c})
    return {'status': 'anchored' if complete else 'partial',
            'char_start': source.items[left]['start'], 'char_end': source.items[right-1]['end'],
            'segments': segments, 'differences': differences}, query, mapping


def locate_field(value, query, mapping, source, aligner, allow_groups=False):
    if not value:
        return {'status': 'absent', 'segments': []}
    index = TokenIndex(query)
    matches = index.find([t['value'] for t in tokens(value)])
    mode = 'continuous'
    if matches:
        status, chain = ('aligned', matches) if len(matches) == 1 else ('ambiguous', [])
    elif allow_groups and re.fullmatch(rf'\s*(?:{NUMERIC_GROUP}\s*){{2,}}', value):
        groups = [m.group() for m in re.finditer(NUMERIC_GROUP, value)]
        status, chain = unique_chain([index.find([t['value'] for t in tokens(g)]) for g in groups])
        mode = 'discontinuous_numeric_groups'
    else:
        return {'status': 'unmapped', 'reason': 'literal_group_absent_or_unsupported_notation', 'segments': []}
    if status != 'aligned':
        return {'status': status, 'reason': 'no_unique_assignment_inside_entry', 'segments': []}
    segments = []
    for a, b in chain:
        ids = [mapping.get(i) for i in range(a, b)]
        if any(i is None for i in ids) or ids != list(range(ids[0], ids[0]+len(ids))):
            return {'status': 'unmapped', 'reason': 'field_crosses_unverified_text', 'segments': []}
        start, end = source.items[ids[0]]['start'], source.items[ids[-1]]['end']
        match = {'status': 'matched', 'method': 'literal_tokens', 'char_start': start, 'char_end': end}
        segments.append({'query_start': query[a]['start'], 'query_end': query[b-1]['end'],
                         'char_start': start, 'char_end': end,
                         'ocr_literal': aligner.visible_span(start, end),
                         'evidence': aligner.evidence(match)})
    return {'status': 'aligned', 'mode': mode, 'segments': segments,
            'editorial_interpretation': 'not_performed'}


def reconcile(reading, task):
    Draft202012Validator(SCHEMA).validate(reading)
    page = next(p for p in task['pages'] if p['page_id'] == task['primary_page_id'])
    aligner = Aligner(page)
    source = TokenIndex(tokens(aligner.visible, aligner.visible_offsets, join_wraps=True))
    rows = []
    for path, parent, entry in walk(reading['elements']):
        if entry['type'] != 'entry':
            continue
        anchor, query, mapping = anchor_entry(entry['text'], source)
        row = {'object': path, 'parent': parent, 'name': entry['name'],
               'text': entry['text'], 'references_raw': entry['references_raw'],
               'anchor': anchor, 'conflicts': [], 'import_ready': False,
               'continuation': entry['continuation'], 'needs_editorial_review': True}
        for segment in anchor.get('segments', []):
            a, b = segment['char_start'], segment['char_end']
            segment['ocr_literal'] = aligner.visible_span(a, b)
            segment['evidence'] = aligner.evidence({'status': 'matched', 'method': 'literal_tokens',
                                                   'char_start': a, 'char_end': b})
            segment['normalizations'] = ['whitespace']
            if next(alphabetic_wraps(segment['ocr_literal']), None):
                segment['normalizations'].append('ocr_alphabetic_line_wrap')
        for field in ('name', 'references_raw'):
            row[field + '_alignment'] = locate_field(entry[field], query, mapping, source, aligner,
                                                      allow_groups=field == 'references_raw')
        rows.append(row)
    # A second pass flags BOTH competing owners. No arbitrary first-wins allocation.
    for i, row in enumerate(rows):
        a = row['anchor']
        if 'char_start' not in a:
            continue
        for other in rows[:i]:
            b = other['anchor']
            if 'char_start' in b and max(a['char_start'], b['char_start']) < min(a['char_end'], b['char_end']):
                row['conflicts'].append(other['object'])
                other['conflicts'].append(row['object'])
    spans = [(s['char_start'], s['char_end']) for r in rows if not r['conflicts']
             for s in r['anchor'].get('segments', [])]
    total, covered = 0, 0
    for line in page['lines']:
        for i in range(line['char_start'], line['char_end']):
            if aligner.raw[i].isalnum():
                total += 1
                covered += any(a <= i < b for a, b in spans)
    counts = Counter()
    for row in rows:
        counts['entries'] += 1
        counts['entry_' + row['anchor']['status']] += 1
        refs = row['references_raw_alignment']
        counts['references_' + refs['status']] += 1
        if row['conflicts']:
            counts['entries_with_source_conflicts'] += 1
            row['next_action'] = 'review_competing_ownership'
        elif row['anchor']['status'] == 'partial':
            row['next_action'] = 'review_recorded_text_differences'
        elif row['anchor']['status'] != 'anchored':
            row['next_action'] = 'review_source_anchor_and_visual_reading'
        elif refs['status'] not in ('aligned', 'absent'):
            row['next_action'] = 'review_literal_reference_notation'
        else:
            row['next_action'] = 'validate_editorial_structure_and_interpretation'
        if row['anchor']['status'] == 'anchored' and not row['conflicts'] and refs['status'] == 'aligned':
            counts['entries_with_anchored_reference_groups'] += 1
            counts['anchored_reference_segments'] += len(refs['segments'])
            if refs['mode'] == 'discontinuous_numeric_groups':
                counts['entries_with_discontinuous_groups_recovered'] += 1
    return {'version': VERSION, 'task_id': task['task_id'], 'page_id': page['page_id'],
            'ocr_sha256': page['ocr_sha256'], 'entries': rows, 'counts': dict(counts),
            'coverage': {'entry_evidence_alphanumeric_chars': covered, 'page_alphanumeric_chars': total,
                         'ratio': covered/total if total else None, 'scope': 'entries_only',
                         'visual_completeness': 'not_verified'},
            'model_uncertainties': reading['uncertainties'],
            'import_ready': False, 'calls': 0, 'db_updates': 0,
            'policy': 'Only whitespace and observed alphabetic OCR line wraps are normalized. '
                      'Spans establish literal correspondence, not editorial attribution or page meaning. '
                      'Original validation alerts remain unchanged.'}


def reconcile_run(run, out):
    run, out = Path(run).resolve(), Path(out).resolve()
    if out.exists() or out.is_relative_to(run) or run.is_relative_to(out):
        raise ValueError('Use a new output directory separate from the source run')
    manifest = json.loads((run/'manifest.json').read_text())
    out.mkdir(parents=True)
    summaries, totals, inventory = [], Counter(), []
    for item in manifest['tasks']:
        directory = Path(item['directory'])
        task = json.loads((directory/'task.json').read_text())
        row = {'task_id': task['task_id'], 'source_directory': str(directory)}
        try:
            if task.get('extraction_profile') != 'literal-v1':
                raise ValueError('Only literal-v1 responses are supported')
            check_sources(task)
            raw = (directory/'response.json').read_bytes()
            checkpoint = json.loads((directory/'checkpoint.json').read_text())
            if checkpoint['input_fingerprint'] != item['input_fingerprint'] or checkpoint['response_sha256'] != digest(raw):
                raise ValueError('Archived response identity/hash mismatch')
            response = json.loads(raw)
            if response['status'] != 'received':
                raise ValueError('Response is not complete')
            report = reconcile(response['dto'], task)
            report['response_sha256'] = digest(raw)
            report['input_fingerprint'] = item['input_fingerprint']
            inventory.extend({'task_id': task['task_id'], 'object': e['object'],
                              'name': e['name'], 'anchor_status': e['anchor']['status'],
                              'anchor_reason': e['anchor'].get('reason'),
                              'reference_status': e['references_raw_alignment']['status'],
                              'reference_segments': len(e['references_raw_alignment']['segments']),
                              'source_conflicts': len(e['conflicts']), 'next_action': e['next_action']}
                             for e in report['entries'])
            destination = out/'tasks'/directory.name/'reconciliation.json'
            write_json(destination, report)
            row.update(status='reconciled_for_review', counts=report['counts'], report=str(destination))
            totals.update(report['counts'])
        except (ValueError, KeyError, OSError, ValidationError) as exc:
            row.update(status='source_or_response_error', error=str(exc))
        summaries.append(row)
    summary = {'version': VERSION, 'source_run': str(run), 'out': str(out),
               'calls': 0, 'db_updates': 0, 'import_ready': False, 'original_artifacts_modified': False,
               'code_sha256': digest({p.name: digest(p.read_bytes()) for p in
                                      sorted(Path(__file__).parent.glob('*.py'))}),
               'statuses': dict(Counter(r['status'] for r in summaries)),
               'next_actions': dict(Counter(r['next_action'] for r in inventory)),
               'counts': dict(totals), 'tasks': summaries}
    with (out/'entries.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['task_id', 'object', 'name', 'anchor_status',
                                              'anchor_reason', 'reference_status', 'reference_segments',
                                              'source_conflicts', 'next_action'])
        writer.writeheader()
        writer.writerows(inventory)
    write_json(out/'summary.json', summary)
    return summary

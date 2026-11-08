"""Deterministic canonical proposals, conservative diff and continuation handoffs."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys

from scripts.alphabetical_index_db import canonical_ref_location
from tools.indexing.alphabetical_compact_pipeline import _validate_semantic_payload
from tools.ocr_xml_utils import normalize_visible_text
from tools.scripture.book_catalog import canonical_book_key, canonical_book_label, historical_noncanonical_book_key
from .contract import ROOT, digest


def canonical(dto, validation, task):
    if not validation['valid']:
        return None, {'valid': False, 'errors': ['DTO failed evidence/schema validation']}
    volume = task['volume']
    primary = next(p for p in task['pages'] if p['page_id'] == task['primary_page_id'])
    source_file = primary['ocr_file']
    spans = validation['spans']
    keys = {}
    for kind in ('sections', 'entries', 'references', 'scripture_refs'):
        for item in dto[kind]:
            keys[item['id']] = f"{volume}:mm:{kind}:{digest([spans[item['id']], item.get('heading_raw'), item.get('entry_raw'), item.get('ref_raw')])[:24]}"
    sections = {s['id']: s for s in dto['sections']}
    data = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'volume': {'volume_id': volume, 'collection': volume[:2],
                       'source_root': str(Path(source_file).parent), 'volume_label': volume},
            'sections': [], 'nodes': [], 'entries': [], 'refs': [], 'scripture_refs': [],
            'coverage': {'entries_status': 'extracted' if dto['entries'] else 'no_line_items' if dto['sections'] else 'no_index_section',
                         'entries_status_reason': 'Bounded page/fragment extraction; see staging residuals and boundaries.',
                         'evidence_files': [source_file], 'locator_status': 'partial',
                         'localization_attempted': False, 'partial_volume': True},
            'notes': ['STAGING ONLY: not a complete replacement volume. Never use --replace to import this pilot.']}
    for n, section in enumerate(dto['sections'], 1):
        data['sections'].append({'section_key': keys[section['id']], 'volume_id': volume,
            'section_order': n, 'section_kind': section['kind'], 'heading_raw': section['heading_raw'],
            'heading_norm': normalize_visible_text(section['heading_raw']),
            'file_start': source_file, 'file_end': source_file, 'page_start': None, 'page_end': None,
            'raw_json': {**section['taxonomy'], 'partial_section': True,
                'index_editorial_page_raw': section['index_page_raw'],
                'scripture_numbering_profile': section['scripture_profile'],
                'source_spans': spans[section['id']], 'local_section_id': section['id']}})
    entry_by_id = {e['id']: e for e in dto['entries']}
    parent_ids = {e['parent_id'] for e in dto['entries'] if e['parent_id']}
    node_order = Counter()
    for parent_id in sorted(parent_ids, key=lambda x: entry_by_id[x]['order']):
        parent = entry_by_id[parent_id]
        sid = keys[parent['section_id']]
        node_order[sid] += 1
        data['nodes'].append({'node_key': keys[parent_id] + ':hierarchy', 'section_key': sid,
            'parent_node_key': keys[parent['parent_id']] + ':hierarchy' if parent['parent_id'] else None,
            'node_order': node_order[sid], 'node_kind': 'heading_group',
            'label_raw': parent['lemma_raw'] or parent['entry_raw'], 'node_level': 1,
            'raw_json': {'represented_parent_entry_key': keys[parent_id]}})
    for entry in sorted(dto['entries'], key=lambda x: (x['section_id'], x['order'])):
        entry_spans = spans[entry['id']]
        text_spans = [s for s in entry_spans if 'line_start' in s]
        raw = {'source_spans': entry_spans, 'local_entry_id': entry['id'],
               'visual_entry_reading': entry['visual_entry_reading'],
               'cross_reference_target': entry['cross_reference_target'],
               'parent_entry_key': keys.get(entry['parent_id']), 'original_dto': entry,
               'continuations': [c for c in dto['continuations'] if c['entry_id'] == entry['id']]}
        if entry['scripture_mode']:
            raw['scripture_mode'] = entry['scripture_mode']
        if entry['lemma_display'] != entry['lemma_raw']:
            raw['proposed_lemma_repair'] = {'ocr': entry['lemma_raw'], 'display': entry['lemma_display'],
                                          'evidence': entry_spans}
        data['entries'].append({'entry_key': keys[entry['id']], 'section_key': keys[entry['section_id']],
            'parent_node_key': keys[entry['parent_id']] + ':hierarchy' if entry['parent_id'] else None,
            'entry_order': entry['order'], 'entry_kind': entry['kind'], 'lemma_raw': entry['lemma_raw'],
            'lemma_display': entry['lemma_display'], 'lemma_norm': normalize_visible_text(entry['lemma_display'] or entry['lemma_raw'] or ''),
            'entry_raw': entry['entry_raw'], 'inferred_printed_page': None,
            'section_start_file': source_file, 'editorial_anchor_file': source_file,
            'target_file_best': None, 'source_span': text_spans[0] if text_spans else None,
            'raw_json': raw})
    scripture_order = Counter()
    scripture_orders = {}
    for item in dto['scripture_refs']:
        scripture_order[item['entry_id']] += 1
        scripture_orders[item['id']] = scripture_order[item['entry_id']]
        profile = sections[entry_by_id[item['entry_id']]['section_id']]['scripture_profile']
        historical = historical_noncanonical_book_key(item['book_raw'])
        book_key = None if historical else canonical_book_key(item['book_raw'], tradition=profile)
        data['scripture_refs'].append({'entry_key': keys[item['entry_id']],
            'ref_order': scripture_orders[item['id']], 'ref_role': item['role'], 'ref_raw': item['ref_raw'],
            'book_raw': item['book_raw'], 'book_norm': canonical_book_label(book_key),
            'book_key': book_key, **{k: item[k] for k in ('chapter_start', 'verse_start', 'chapter_end', 'verse_end')},
            'is_range': int(item['chapter_end'] is not None or item['verse_end'] is not None),
            'raw_json': {'source_spans': spans[item['id']], 'normalization_profile': profile,
                **({'canonical_status': 'historical_noncanonical', 'historical_book_key': historical} if historical else {})}})
    for ref in dto['references']:
        item = {'entry_key': keys[ref['entry_id']], 'ref_order': ref['order'],
            'scripture_ref_order': scripture_orders.get(ref['scripture_id']),
            'ref_kind': ref['kind'], 'ref_raw': ref['ref_raw'], 'page_ref_raw': ref['page_start'],
            'page_ref_int': None, 'page_ref_col': ref['column'], 'line_ref_raw': ref['line_start'],
            'range_start_raw': ref['page_start'] if ref['kind'] == 'editorial_range' else None,
            'range_end_raw': ref['page_end'] if ref['kind'] == 'editorial_range' else None,
            'cited_editorial_page_start_raw': ref['page_start'], 'cited_editorial_page_end_raw': ref['page_end'],
            'cited_editorial_line_start_raw': ref['line_start'], 'cited_editorial_line_end_raw': ref['line_end'],
            'target_file': None, 'target_file_probability': None, 'locator_status': 'unresolved',
            'section_start_file': source_file, 'editorial_anchor_file': source_file,
            'raw_json': {'source_spans': spans[ref['id']], 'original_dto': ref,
                         'edition': ref['edition'], 'tome': ref['tome'], 'series': ref['series'],
                         'stable_occurrence_key': keys[ref['id']]}}
        for axis in ('page', 'line'):
            for end in ('start', 'end'):
                literal = ref[f'{axis}_{end}']
                item[f'cited_editorial_{axis}_{end}_number'] = int(literal) if literal and re.fullmatch('[0-9]+', literal) and int(literal) > 0 else None
        item.update(canonical_ref_location(item))
        item['page_ref_int'] = item['cited_editorial_page_start_number']
        data['refs'].append(item)
    try:
        _validate_semantic_payload(data)
        # Existing canonical validator imports the scripts directory's DB helpers.
        if str(ROOT / 'scripts') not in sys.path:
            sys.path.insert(0, str(ROOT / 'scripts'))
        from scripts.import_alphabetical_index_json import build_validation_summary
        report = build_validation_summary(data)
        report.update(valid=True, import_ready=False, reason='partial-volume staged proposal; review required')
    except ValueError as exc:
        report = {'valid': False, 'import_ready': False, 'errors': [str(exc)]}
    return data, report


def db_snapshot(db, task):
    con = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    try:
        sections = [dict(r) for r in con.execute('SELECT * FROM alphabetical_sections WHERE volume_id=?', (task['volume'],))]
        entries = [dict(r) for r in con.execute('SELECT e.* FROM alphabetical_entries e JOIN alphabetical_sections s USING(section_key) WHERE s.volume_id=?', (task['volume'],))]
        refs = [dict(r) for r in con.execute('SELECT r.* FROM alphabetical_refs r JOIN alphabetical_entries e USING(entry_key) JOIN alphabetical_sections s USING(section_key) WHERE s.volume_id=?', (task['volume'],))]
    finally:
        con.close()
    primary = next(p for p in task['pages'] if p['page_id'] == task['primary_page_id'])
    path, number = primary['ocr_file'], primary['physical_sequence']
    relevant = []
    for section in sections:
        values = [section.get('index_ocr_file_' + end) or section.get('file_' + end) or '' for end in ('start', 'end')]
        nums = [re.search(r'-(\d+)\.txt$', str(v)) for v in values]
        if all(nums) and int(nums[0][1]) <= number <= int(nums[1][1]):
            relevant.append(section['section_key'])
    selected, uncertain_sources = [], []
    for entry in entries:
        raw = json.loads(entry.get('raw_json') or '{}')
        sources = [entry.get('index_entry_source_ocr_file'), raw.get('source_file')]
        exact_source = any(s and (Path(s) if Path(s).is_absolute() else ROOT/str(s)).resolve() == Path(path).resolve() for s in sources)
        if exact_source:
            selected.append({**entry, '_source_matches_primary': exact_source})
        elif entry['section_key'] in relevant:
            uncertain_sources.append(entry['entry_key'])
    keys = {e['entry_key'] for e in selected}
    return {'sections': [s for s in sections if s['section_key'] in relevant],
            'entries': selected, 'refs': [r for r in refs if r['entry_key'] in keys],
            'other_section_entries_not_attributed_to_primary': uncertain_sources,
            'comparison_scope': 'Only recorded primary-page sources; other section entries are not treated as deletions.'}


def fold(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def diff(snapshot, payload):
    if payload is None:
        return {'status': 'not_comparable', 'changes': [], 'delete_authorized': False}
    changes, matching, reverse = [], {}, defaultdict(list)
    old = snapshot['entries']
    for new in payload['entries']:
        exact = [e for e in old if e['_source_matches_primary'] and fold(e['entry_raw']) == fold(new['entry_raw'])]
        contains = [e for e in old if e['_source_matches_primary'] and fold(new['entry_raw']) and fold(new['entry_raw']) in fold(e['entry_raw'])]
        candidates = exact or contains
        matching[new['entry_key']] = candidates
        if len(candidates) == 1:
            previous = candidates[0]
            reverse[previous['entry_key']].append(new['entry_key'])
            changes.append({'kind': 'entry_retained' if exact else 'entry_split_candidate',
                            'old_entry_key': previous['entry_key'], 'new_entry_key': new['entry_key'],
                            'evidence': 'same recorded source + literal text match', 'needs_review': not bool(exact)})
        else:
            changes.append({'kind': 'new_entry_candidate' if not candidates else 'ambiguous_entry_match',
                            'new_entry_key': new['entry_key'], 'old_candidates': [e['entry_key'] for e in candidates],
                            'needs_review': True})
    for old_key, children in reverse.items():
        if len(children) > 1:
            changes.append({'kind': 'concatenated_entry_split_candidate', 'old_entry_key': old_key,
                            'new_entry_keys': children, 'needs_review': True})
    observed_refs = set()
    for new in payload['refs']:
        owners = {e['entry_key'] for e in matching.get(new['entry_key'], [])}
        candidates = [r for r in snapshot['refs'] if r['entry_key'] in owners and r['ref_raw'] == new['ref_raw']]
        if len(candidates) == 1:
            previous = candidates[0]
            observed_refs.add(previous['ref_id'])
            old_entry = next(e for e in old if e['entry_key'] == previous['entry_key'])
            new_entry = next(e for e in payload['entries'] if e['entry_key'] == new['entry_key'])
            changed = fold(old_entry['lemma_raw']) != fold(new_entry['lemma_raw'])
            changes.append({'kind': 'reference_reassignment_candidate' if changed else 'reference_retained_candidate',
                            'old_ref_id': previous['ref_id'], 'new_entry_key': new['entry_key'],
                            'ref_raw': new['ref_raw'], 'old_locator_status': previous['locator_status'],
                            'preserve_existing_resolution': True, 'needs_review': True})
            if any(previous.get(k) != new.get(k) for k in ('cited_editorial_page_start_raw', 'cited_editorial_line_start_raw', 'cited_editorial_page_end_raw')):
                changes.append({'kind': 'locator_interpretation_change', 'old_ref_id': previous['ref_id'],
                                'new_ref_order': new['ref_order'], 'needs_review': True})
            old_raw = json.loads(previous.get('raw_json') or '{}')
            if any(old_raw.get(k) != new['raw_json'].get(k) for k in ('edition', 'tome', 'series')):
                changes.append({'kind': 'namespace_interpretation_change', 'old_ref_id': previous['ref_id'],
                                'proposed': {k: new['raw_json'].get(k) for k in ('edition', 'tome', 'series')}, 'needs_review': True})
        else:
            changes.append({'kind': 'reference_added_candidate' if not candidates else 'ambiguous_reference_occurrence',
                            'new_entry_key': new['entry_key'], 'ref_order': new['ref_order'],
                            'ref_raw': new['ref_raw'], 'needs_review': True})
    primary_keys = {e['entry_key'] for e in old if e['_source_matches_primary']}
    for ref in snapshot['refs']:
        if ref['entry_key'] in primary_keys and ref['ref_id'] not in observed_refs:
            changes.append({'kind': 'old_reference_not_observed_not_a_deletion', 'old_ref_id': ref['ref_id'], 'needs_review': True})
    old_headings = {s['heading_raw'] for s in snapshot['sections']}
    new_headings = {s['heading_raw'] for s in payload['sections']}
    if old_headings != new_headings:
        changes.append({'kind': 'section_scope_change_candidate', 'old_headings': sorted(old_headings),
                        'new_headings': sorted(new_headings), 'needs_review': True})
    return {'status': 'proposals_only', 'delete_authorized': False,
            'counts': dict(Counter(c['kind'] for c in changes)), 'changes': changes,
            'db_snapshot_sha256': digest(snapshot),
            'limitation': 'Source pointers can be wrong. No matching by name or number alone; uncertain occurrences require review.'}


def continuations(results):
    """Only reciprocal, unique, literal counterpart confirmations become stitch proposals."""
    endpoints, repairs = [], []
    for result in results:
        if not result.get('validation', {}).get('valid'):
            continue
        task, dto = result['task'], result['dto']
        entries = {e['id']: e for e in dto['entries']}
        for c in dto['continuations']:
            endpoint = {'task_id': task['task_id'], 'page_id': task['primary_page_id'],
                        'physical_page': task['pages'][0]['physical_sequence'], 'volume': task['volume'],
                        'entry': entries[c['entry_id']], 'continuation': c,
                        'references': [r for r in dto['references'] if r['entry_id'] == c['entry_id']],
                        'section': next(s for s in dto['sections'] if s['id'] == entries[c['entry_id']]['section_id'])}
            endpoints.append(endpoint)
            primary = next(p for p in task['pages'] if p['page_id'] == task['primary_page_id'])
            selected = {lid for e in c['evidence'] for lid in e['line_ids']}
            ids = [l['id'] for l in primary['lines']]
            positions = [ids.index(lid) for lid in selected if lid in ids]
            expanded = ids[max(0,min(positions)-2):max(positions)+3] if positions else []
            repairs.append({'request_id': digest(endpoint)[:24], 'volume': task['volume'],
                'physical_page': primary['physical_sequence'], 'ocr_sha256': primary['ocr_sha256'],
                'line_ids': expanded, 'reason': c['reason'], 'continuation': c,
                'source_task': task['task_id'], 'status': 'stitch_pending'})
    stitches = []
    for a in endpoints:
        c = a['continuation']
        if c['direction'] != 'trailing' or c['status'] != 'confirmed':
            continue
        matches = []
        for b in endpoints:
            d = b['continuation']
            if (d['direction'] == 'leading' and d['status'] == 'confirmed' and
                a['volume'] == b['volume'] and 0 <= b['physical_page'] - a['physical_page'] <= 1 and
                c['peer_page_id'] == b['page_id'] and d['peer_page_id'] == a['page_id'] and
                c['peer_quote'] in b['entry']['entry_raw'] and d['peer_quote'] in a['entry']['entry_raw'] and
                a['section']['heading_raw'] == b['section']['heading_raw'] and
                a['section']['taxonomy'] == b['section']['taxonomy'] and
                (a['page_id'] != b['page_id'] or (c['peer_entry_id'] == b['entry']['id']
                    and d['peer_entry_id'] == a['entry']['id'] and a['entry']['order'] < b['entry']['order']))):
                namespaces = {(r['edition'], r['tome'], r['series']) for r in a['references'] + b['references']}
                if len(namespaces) > 1:
                    continue
                matches.append(b)
        if len(matches) == 1:
            b = matches[0]
            stitches.append({'status': 'reciprocal_stitch_proposal', 'from': [a['task_id'], a['entry']['id']],
                'to': [b['task_id'], b['entry']['id']],
                'joined_entry_raw': a['entry']['entry_raw'] + '\n' + b['entry']['entry_raw'],
                'reference_fragments': [{'source_task': endpoint['task_id'], 'reference': r}
                    for endpoint in (a, b) for r in endpoint['references']],
                'source_evidence': a['entry']['evidence'] + b['entry']['evidence'],
                'needs_review': True, 'changes_original_artifacts': False})
    # A leading fragment cannot be claimed by competing trailing fragments.
    counts = Counter(tuple(s['to']) for s in stitches)
    stitches = [s for s in stitches if counts[tuple(s['to'])] == 1]
    return {'stitches': stitches, 'fragment_requests': repairs,
            'policy': 'No implicit transfers; confirmations produce assembled proposals with both source fragments retained.'}

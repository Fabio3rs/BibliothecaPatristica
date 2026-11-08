"""Read-only relocalization of repaired search items against a cached survey.

The returned candidates are experimental navigation hints. Generic header matches
retain their unresolved numbering namespace, including Roman context. This module
never writes a catalog, edits corpus text, or infers a tome-to-volume binding.
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from tools.corpus_utils import page_number
from tools.indexing.candidate_search import rank_candidates
from tools.indexing.global_target_survey import prepare_profiles, profile_references, scoped_location

VERSION = 'repaired-candidate-localization-v2'
ROMAN = re.compile(r'(?<!\w)[IVXLCDM]+(?!\w)')
NUMBER = re.compile(r'(?<!\w)[1-9][0-9]{0,3}(?!\w)')
LITERAL = re.compile(r'(?:[IVXLCDM]+|[0-9]{1,4}|[\s,;.\[\]()—–\-:]|(?:seq[q]?|ibid|col|pag|p|et|fin)\.?)+', re.I)


def _jsonl(path):
    if path.is_file():
        with path.open() as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def _metadata(item):
    value = item.get('metadata', item.get('metadata_json', {}))
    return json.loads(value) if isinstance(value, str) else value


def _span(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(n, int) for n in value) and 0 <= value[0] < value[1])


class LocalizationContext:
    """One worker-local volume cache; corpus_root is the ``teste`` directory."""

    def __init__(self, survey_dir, volume, corpus_root=None):
        self.survey_dir = Path(survey_dir).resolve()
        self.volume = volume
        directory = self.survey_dir / 'volumes' / volume
        self.inventory = {r['path']: r for r in _jsonl(directory / 'inventory.jsonl') if not r.get('error')}
        self.headers = defaultdict(list)
        for marker in _jsonl(directory / 'header_markers.jsonl'):
            self.headers[marker['number']].append(marker)
        self.index_sources = {r['source'] for r in _jsonl(directory / 'sources.jsonl')}
        self.profile_records = json.loads((directory / 'profiles.json').read_text()) if (directory / 'profiles.json').is_file() else []
        config = json.loads((self.survey_dir / 'config.json').read_text()) if (self.survey_dir / 'config.json').is_file() else {}
        self.corpus_root = Path(corpus_root or config.get('root') or Path(__file__).resolve().parents[2] / 'teste')
        self._profiles = None
        self._sources = {}
        self.metrics = Counter()

    def source(self, path):
        if path not in self._sources:
            try:
                data = Path(path).read_bytes()
                self._sources[path] = (data.decode('utf-8'), sha256(data).hexdigest())
            except (OSError, UnicodeError):
                self._sources[path] = (None, None)
        return self._sources[path]

    def profiles_for(self, physical):
        records = [p for p in self.profile_records if p['profile']['index_physical_start'] <= physical <= p['profile']['index_physical_end']]
        if not records:
            return []
        if self._profiles is None:
            paths = [p['path'] for p in self.profile_records if not p.get('error')]
            self._profiles, errors = prepare_profiles(paths, self.volume, self.corpus_root)
            self.metrics['profile_errors'] += len(errors)
        return [p for p in self._profiles if p['profile']['index_physical_start'] <= physical <= p['profile']['index_physical_end']]

    def valid_target(self, target):
        path = target.get('file', target.get('path'))
        if not path or not Path(path).is_file():
            self.metrics['unavailable_targets'] += 1
            return False
        expected = target.get('sha256')
        raw, checksum = self.source(path)
        if raw is None:
            self.metrics['stale_targets'] += 1
            return False
        literal_span = target.get('raw') is not None and _span([target.get('char_start'), target.get('char_end')])
        if literal_span and raw[target['char_start']:target['char_end']] != target['raw']:
            self.metrics['invalid_marker_spans'] += 1
            return False
        if expected and checksum != expected:
            if not literal_span:
                self.metrics['stale_targets_without_local_evidence'] += 1
                return False
            self.metrics['target_changed_local_evidence_matches'] += 1
            target['source_evidence_status'] = 'source_changed_local_evidence_matches'
            target['observed_sha256'] = checksum
        return True


def _views(item, meta, context):
    """Literal reference views with exact coordinates, never display coordinates."""
    source = item['source']
    original = meta.get('original_item_metadata', {})
    raw, checksum = context.source(source)
    expected = meta.get('source_sha256') or original.get('source_sha256')
    if raw is None:
        return [], 'source_unavailable'
    changed = bool(expected and expected != checksum)
    reconciliation = meta.get('reconciliation', {})
    coordinate_space = meta.get('reference_coordinate_space') or reconciliation.get('coordinate_space')
    if coordinate_space in {'historical_snapshot', 'visual_transcription'}:
        views = []
        if coordinate_space == 'visual_transcription':
            review = reconciliation.get('prior_review') or {}
            quote = review.get('visual_transcription', {}).get('literal', '')
            for group in review.get('reference_groups', []):
                span = [group.get('char_start'), group.get('char_end')]
                if _span(span) and quote[span[0]:span[1]] == group.get('raw'):
                    views.append({'raw': group['raw'], 'span': span,
                                  'coordinate_space': 'visual_transcription',
                                  'proposal_id': review.get('proposal_id'),
                                  'roman_context': [m[0] for m in ROMAN.finditer(group['raw'])],
                                  'entry_raw': quote, 'entry_span': [0, len(quote)]})
        for proposal in reconciliation.get('proposals', []):
            reference, entry = proposal.get('reference', {}), proposal.get('entry', {})
            span, parent = reference.get('span'), entry.get('span')
            literal = reference.get('quote', '')
            if not (_span(span) and _span(parent) and parent[0] <= span[0] < span[1] <= parent[1]):
                continue
            quote = entry.get('quote', '')
            if quote[span[0]-parent[0]:span[1]-parent[0]] != literal:
                # Some historical artifacts serialize actual newlines as two characters.
                quote = quote.replace('\\n', '\n')
                if quote[span[0]-parent[0]:span[1]-parent[0]] != literal.replace('\\n', '\n'):
                    continue
                literal = literal.replace('\\n', '\n')
            views.append({'raw': literal, 'span': span, 'coordinate_space': 'historical_snapshot',
                          'version_id': proposal.get('version_id'), 'proposal_id': proposal.get('id'),
                          'roman_context': [m[0] for m in ROMAN.finditer(literal)],
                          'entry_raw': quote, 'entry_span': parent})
        for view in views:
            view['source_evidence_status'] = ('source_index_changed_reference_snapshot_preserved'
                                              if changed else 'source_hash_matches')
        return views, None if views else 'historical_reference_span_invalid'
    span = meta.get('source_span') or original.get('source_span')
    if not _span(span) or span[1] > len(raw):
        return [], 'reference_span_missing_or_invalid'
    literal = raw[span[0]:span[1]]
    printed = (meta.get('original_item', {}).get('ref_raw') if meta.get('reference_display_is_contextualized') else item.get('ref_raw'))
    if printed is not None and literal != printed:
        return [], 'reference_literal_mismatch'
    parser = meta.get('parser_result', {})
    romans = [m[0] for m in ROMAN.finditer(literal)]
    if parser.get('roman_raw'):
        expanded = parser.get('expanded_reference_span')
        expanded_raw = parser.get('expanded_reference_raw')
        if not (_span(expanded) and raw[expanded[0]:expanded[1]] == expanded_raw
                and expanded[0] <= span[0] < expanded[1]
                and (span[1] <= expanded[1] or not raw[expanded[1]:span[1]].strip(' .\t\r\n'))):
            return [], 'roman_context_span_invalid'
        romans = [parser['roman_raw']]
    entry = meta.get('entry') or {}
    if not entry and parser.get('existing_reference_entry') is not None:
        entries = parser.get('entries', [])
        index = parser['existing_reference_entry']
        if isinstance(index, int) and 0 <= index < len(entries):
            entry = entries[index]
    parent = entry.get('entry_span')
    if not _span(parent):
        owner = parser.get('corrected_owner_span')
        parent = [owner[0], span[1]] if _span(owner) and owner[0] < span[0] else span
    if not (parent[0] <= span[0] < span[1] <= parent[1] <= len(raw)):
        return [], 'reference_outside_own_entry'
    if changed:
        owner_proofs = [(entry.get('owner_span'), entry.get('owner_raw')),
                        (parser.get('corrected_owner_span'), parser.get('corrected_owner_raw'))]
        owner_matches = any(_span(bound) and text and raw[bound[0]:bound[1]] == text
                            for bound, text in owner_proofs)
        if not owner_matches:
            label = item.get('label', '')
            preceding = raw[:span[0]].rstrip(' \t\r\n,;:.')
            owner_matches = bool(label and preceding.endswith(label))
        if not owner_matches:
            return [], 'source_changed_owner_context_unconfirmed'
    return [{'raw': literal, 'span': list(span), 'coordinate_space': 'current_source',
             'source_evidence_status': 'source_changed_local_evidence_matches' if changed else 'source_hash_matches',
             'roman_context': romans, 'entry_raw': raw[parent[0]:parent[1]], 'entry_span': list(parent)}], None


def _numeric_references(view):
    if not LITERAL.fullmatch(view['raw']):
        return []
    result = []
    for match in NUMBER.finditer(view['raw']):
        result.append({'number': int(match[0]), 'ref_raw': match[0],
                       'source_span': [view['span'][0]+match.start(), view['span'][0]+match.end()],
                       'reference_literal': view['raw'], 'reference_source_span': view['span'],
                       'coordinate_space': view['coordinate_space'],
                       'roman_context': view['roman_context'],
                       'namespace_status': 'unmapped', 'number_role': 'uninterpreted_numeric_token',
                       **{k: view[k] for k in ('version_id', 'proposal_id', 'source_evidence_status',
                                              'printed_page_line', 'line_raw') if k in view}})
    return result


def _scoped_references(view, mapping):
    profile = mapping['profile']
    if view['roman_context'] and profile['notation'] not in {'explicit_tome_list', 'explicit_tome_page'}:
        return []
    entry = {'entry_type': 'material', 'entry_raw': view['entry_raw'],
             'char_start': view['entry_span'][0], 'char_end': view['entry_span'][1],
             'references_raw': view['raw'], 'reference_span': {'char_start': view['span'][0], 'char_end': view['span'][1]},
             'text_raw': '', 'text_display': '', 'flags': []}
    try:
        refs = profile_references(entry, profile) or []
    except (KeyError, ValueError):
        return []
    result = []
    for ref in refs:
        span = ref.get('source_span')
        if not _span(span) or not view['span'][0] <= span[0] < span[1] <= view['span'][1]:
            continue
        literal = view['raw'][span[0]-view['span'][0]:span[1]-view['span'][0]]
        if ref.get('ref_raw', literal) != literal:
            continue
        result.append({**ref, 'coordinate_space': view['coordinate_space'],
                       'roman_context': view['roman_context'],
                       **{k: view[k] for k in ('version_id', 'proposal_id', 'source_evidence_status') if k in view}})
    return result


def localize_pending_item(item, *, context):
    """Return candidate_search-compatible candidates and auditable alternatives."""
    item = dict(item)
    meta = _metadata(item)
    result = {'item_id': item['id'], 'status': 'source_only', 'candidates': [], 'abstention': None,
              'provenance': {'version': VERSION, 'survey': str(context.survey_dir),
                             'references': [], 'branches': [], 'profile_attempts': [],
                             'certified': False, 'canonical_write_allowed': False}}
    provenance = result['provenance']
    if item['volume'] != context.volume:
        result['abstention'] = 'context_volume_mismatch'
        return result
    namespace = meta.get('reference_namespace', 'unknown')
    provenance['reference_namespace'] = namespace
    if namespace not in {'unknown', 'page', 'page_line', 'qualified_page', 'bracketed_page'}:
        result['abstention'] = 'requires_namespace_map:'+namespace
        return result
    views, error = _views(item, meta, context)
    if error:
        result['abstention'] = error
        return result
    if namespace == 'page_line':
        page_views = []
        for token in meta.get('page_line_tokens', []):
            span, literal = token.get('span'), token.get('page_raw')
            if not _span(span) or not isinstance(literal,str) or not literal.isdecimal():
                continue
            page_span=token.get('page_span') or [span[0],span[0]+len(literal)]
            expected=token.get('raw') or literal+'_'+str(token.get('line_raw',''))
            if not (_span(page_span) and span[0]<=page_span[0]<page_span[1]<=span[1]):
                continue
            for view in views:
                if (view['span'][0] <= span[0] < span[1] <= view['span'][1]
                        and view['raw'][span[0]-view['span'][0]:span[1]-view['span'][0]]
                            == expected
                        and view['raw'][page_span[0]-view['span'][0]:page_span[1]-view['span'][0]]==literal):
                    page_views.append({**view,'raw':literal,'span':page_span,
                                       'printed_page_line':view['raw'],'line_raw':token.get('line_raw')})
        views = page_views
        if not views:
            result['abstention'] = 'page_line_tokens_missing_or_invalid'
            return result
    physical = meta.get('physical') or meta.get('reconciliation', {}).get('physical') or page_number(Path(item['source']))
    mappings = context.profiles_for(physical) if physical is not None else []
    branches = provenance['branches']
    for view in views:
        scoped_numbers = set()
        for mapping in mappings:
            attempt = {'profile': mapping['path'], 'notation': mapping['profile']['notation'],
                       'coordinate_space': view['coordinate_space']}
            provenance['profile_attempts'].append(attempt)
            if mapping.get('error'):
                attempt.update(status='invalid_profile', error=mapping['error'])
                continue
            refs = _scoped_references(view, mapping)
            attempt['status'] = 'compatible' if refs else 'notation_incompatible'
            for ref in refs:
                location = scoped_location(ref, mapping)
                files = [{**f, 'file': f.get('file', f.get('path'))}
                         for f in location.get('files', []) if context.valid_target(f)]
                branches.append({'tier': 'documented_profile', 'profile': mapping['path'], 'reference': ref,
                                 'status': location['status'], 'files': files})
                provenance['references'].append(ref)
                if files:
                    scoped_numbers.add((ref['number'], tuple(ref['source_span'])))
        for ref in _numeric_references(view):
            if (ref['number'], tuple(ref['source_span'])) in scoped_numbers:
                continue
            provenance['references'].append(ref)
            files = []
            for marker in context.headers.get(ref['number'], []):
                if marker['path'] == item['source'] or marker['path'] in context.index_sources:
                    continue
                if context.valid_target(marker):
                    files.append({**marker, 'file': marker['path'],
                                  'candidate_basis': 'supported_header' if marker.get('sequence_support') else 'header_without_sequence'})
            branches.append({'tier': 'unmapped_namespace_screen', 'reference': ref,
                             'status': 'approximate_header_candidate' if files else 'no_header_coincidence', 'files': files})
    candidates = {}
    flags = list(meta.get('group_flags', meta.get('original_item_metadata', {}).get('group_flags', [])))
    for branch in branches:
        warnings = flags[:]
        approximate = branch['tier'] != 'documented_profile'
        if approximate:
            warnings += ['approximate_header_candidate', 'same_volume_search_scope_not_editorial_binding']
        if branch['reference'].get('roman_context'):
            warnings.append('roman_context_preserved_namespace_unmapped' if approximate else 'roman_context_scoped_profile')
        if branch['reference']['coordinate_space'] != 'current_source':
            warnings.append('historical_source_alternative')
        if branch['reference'].get('source_evidence_status', '').startswith('source_changed'):
            warnings.append(branch['reference']['source_evidence_status'])
        record = {'tier': branch['tier'], 'targets': branch['files'], 'group_flags': warnings,
                  'reference_interpretation': branch['reference']}
        for candidate in rank_candidates(record):
            target_status = candidate['evidence'].get('source_evidence_status')
            if target_status:
                candidate['uncertainties'] = sorted(set(candidate['uncertainties'] + [target_status]))
            support = {'tier': branch['tier'], 'profile': branch.get('profile'),
                       'status': branch['status'], 'reference': branch['reference']}
            candidate['evidence'] = {**candidate['evidence'], 'localization_support': [support]}
            old = candidates.get(candidate['file'])
            if old is None:
                candidates[candidate['file']] = candidate
            else:
                supports = old['evidence']['localization_support'] + [support]
                if candidate['score'] > old['score']:
                    candidates[candidate['file']] = candidate
                candidates[candidate['file']]['evidence']['localization_support'] = supports
    ranked = sorted(candidates.values(), key=lambda c: (-c['score'], c['file']))
    for rank, candidate in enumerate(ranked, 1):
        candidate['rank'] = rank
    result['candidates'] = ranked
    result['status'] = 'candidate' if ranked else 'source_only'
    if not ranked:
        result['abstention'] = 'no_existing_target_candidate' if branches else 'unsupported_reference_notation'
    return result


def localize_volume(items, *, survey_dir, volume, corpus_root=None):
    """Batch API for process pools; all results and metrics are returned in memory."""
    context = LocalizationContext(survey_dir, volume, corpus_root)
    results = [localize_pending_item(item, context=context) for item in items]
    return {'results': results, 'summary': {'version': VERSION, 'volume': volume,
            'items': len(results), 'items_with_candidates': sum(bool(r['candidates']) for r in results),
            'candidate_files': sum(len(r['candidates']) for r in results),
            'statuses': dict(Counter(r['status'] for r in results)),
            'abstentions': dict(Counter(r['abstention'] for r in results if r['abstention'])),
            'cache_metrics': dict(context.metrics), 'inference_calls': 0, 'database_writes': 0}}

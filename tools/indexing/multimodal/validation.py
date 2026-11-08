"""Local schema, evidence, ownership and coverage checks. No inference or DB I/O."""
from collections import Counter
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from tools.indexing.alphabetical_mechanical_analysis import detect_heading
from .contract import SCHEMA, digest
from .alignment import literal_pattern
from .mechanical import subscript_axis_supported


def index_by_id(items, label):
    result = {}
    for item in items:
        key = item['id']
        if not key.strip() or key in result:
            raise ValueError(f'{label}: empty or duplicate id {key}')
        result[key] = item
    return result


def evidence_spans(evidence, task):
    pages = {p['page_id']: p for p in task['pages']}
    spans = []
    if not evidence:
        raise ValueError('missing evidence')
    for ev in evidence:
        if ev['page_id'] not in pages:
            raise ValueError('invented page ID')
        page = pages[ev['page_id']]
        lines = {l['id']: l for l in page['lines']}
        if len(ev['line_ids']) != len(set(ev['line_ids'])) or not set(ev['line_ids']) <= lines.keys():
            raise ValueError('unknown/duplicate line ID')
        selected = [lines[lid] for lid in ev['line_ids']]
        if selected and (any(l['block_id'] != ev['block_id'] for l in selected)):
            raise ValueError('block ID does not own cited lines')
        raw = Path(page['ocr_file']).read_bytes().decode('utf-8')
        span = {'file': page['ocr_file'], 'page_id': page['page_id'], 'line_ids': ev['line_ids'],
                'quote': ev['quote'], 'block_id': ev['block_id'], 'ocr_sha256': page['ocr_sha256']}
        if ev['quote']:
            if not selected:
                raise ValueError('OCR quote requires line IDs')
            selected.sort(key=lambda l: l['char_start'])
            all_block_lines = [l['id'] for l in page['lines'] if l['block_id'] == ev['block_id']]
            positions = [all_block_lines.index(l['id']) for l in selected]
            if positions != list(range(min(positions), max(positions)+1)):
                raise ValueError('nonconsecutive quote lines')
            a, b = selected[0]['char_start'], selected[-1]['char_end']
            matches = [m for m in re.finditer(literal_pattern(ev['quote']), raw)
                       if a <= m.start() and m.end() <= b]
            if 'ocr_char_start' in ev or 'ocr_char_end' in ev:
                start, end = ev.get('ocr_char_start'), ev.get('ocr_char_end')
                if type(start) is not int or type(end) is not int or not a <= start < end <= b:
                    raise ValueError('invalid Python OCR offsets for cited lines')
                matches = [m for m in matches if m.start() == start and m.end() == end]
            if len(matches) != 1:
                raise ValueError('quote not found uniquely in cited OCR lines')
            start, end = matches[0].start(), matches[0].end()
            span.update(char_start=start, char_end=end,
                        line_start=raw.count('\n', 0, start)+1,
                        line_end=raw.count('\n', 0, end-1)+1,
                        text_sha256=digest(raw[start:end].encode()))
        elif 'ocr_char_start' in ev or 'ocr_char_end' in ev:
            raise ValueError('OCR offsets require a literal quote')
        if ev['visual_region_id'] is not None:
            regions = {r['id']: r for r in page['regions']}
            if ev['visual_region_id'] not in regions:
                raise ValueError('visual evidence cites an image not sent')
            if not ev['visual_reading']:
                raise ValueError('visual evidence requires an explicit reading')
            region = regions[ev['visual_region_id']]
            span['visual_evidence'] = {'region_id': region['id'], 'image_sha256': region['sha256'],
                'crop_box': region['crop_box'], 'reading': ev['visual_reading'],
                'corrected_fields': ev['corrected_fields'], 'uncertain': ev['uncertain'],
                'verification': 'model_proposed_traceable_to_supplied_image_not_human_verified'}
        elif ev['visual_reading'] is not None or ev['corrected_fields']:
            raise ValueError('visual correction without a supplied image region')
        if not ev['quote'] and 'visual_evidence' not in span:
            raise ValueError('evidence has neither OCR quote nor visual reading')
        spans.append(span)
    return spans


def supported_raw(value, evidence):
    if value is None or value == '':
        return True
    quotes = [e['quote'] for e in evidence]
    return any(value in q for q in quotes) or value in '\n'.join(quotes)


def validate(dto, task):
    schema_errors = sorted(Draft202012Validator(SCHEMA).iter_errors(dto), key=lambda e: str(list(e.path)))
    if schema_errors:
        return {'valid': False, 'errors': [f'{list(e.path)}: {e.message}' for e in schema_errors[:30]],
                'warnings': [], 'spans': {}}
    errors, warnings, spans = [], [], {}
    try:
        pages = {p['page_id']: p for p in task['pages']}
        primary = pages[task['primary_page_id']]
        primary_lines = {l['id']: l for l in primary['lines']}
        groups = {k: index_by_id(dto[k], k) for k in ('segments', 'sections', 'entries', 'references',
                   'scripture_refs', 'continuations', 'editorial_rules', 'edition_mappings', 'unresolved')}
        all_objects = {}
        for values in groups.values():
            if all_objects.keys() & values.keys():
                raise ValueError('local IDs must be unique across object arrays')
            all_objects.update(values)
        for key, item in all_objects.items():
            if item.get('evidence'):
                try:
                    spans[key] = evidence_spans(item['evidence'], task)
                except ValueError as exc:
                    raise ValueError(f'{key}: {exc}') from exc
            elif key not in groups['unresolved']:
                raise ValueError(f'{key}: missing evidence')
        ownership, owning_segment = {}, {}
        for segment in dto['segments']:
            if segment['page_id'] not in pages:
                raise ValueError('segment page ID not supplied')
            allowed = {l['id'] for l in pages[segment['page_id']]['lines']}
            if not set(segment['line_ids']) <= allowed:
                raise ValueError('segment has unknown lines')
            for lid in segment['line_ids']:
                if lid in ownership:
                    raise ValueError(f"overlapping ownership segments: {owning_segment[lid]} / {segment['id']} at {lid}")
                ownership[lid] = segment['role']
                owning_segment[lid] = segment['id']
            if segment['page_id'] != primary['page_id'] and segment['role'] == 'owned':
                raise ValueError('context page cannot be extracted as owned')
        if not primary_lines.keys() <= ownership.keys():
            raise ValueError('primary lines missing ownership')
        for line in primary['lines']:
            hint = line.get('heading_hint')
            if hint and hint['role'] == 'stop_boundary' and ownership.get(line['id']) == 'owned':
                raise ValueError('explicit boundary heading classified owned')
        for section in dto['sections']:
            if not section['segment_ids'] or not set(section['segment_ids']) <= groups['segments'].keys():
                raise ValueError('section has no valid segments')
            if any(groups['segments'][sid]['role'] != 'owned' for sid in section['segment_ids']):
                raise ValueError('section includes non-owned segment')
            hint = detect_heading(section['heading_raw'])
            if hint and hint['role'] == 'stop_boundary':
                raise ValueError('boundary section cannot be canonical')
            if not supported_raw(section['heading_raw'], section['evidence']):
                raise ValueError('heading_raw not preserved in evidence')
            if not supported_raw(section['index_page_raw'], section['evidence']):
                raise ValueError('index editorial page lacks literal evidence')
        seen_orders = set()
        for entry in dto['entries']:
            section = groups['sections'].get(entry['section_id'])
            if not section:
                raise ValueError('entry references unknown section')
            section_lines = {lid for sid in section['segment_ids'] for lid in groups['segments'][sid]['line_ids']}
            for ev in entry['evidence']:
                if ev['page_id'] != primary['page_id'] or not set(ev['line_ids']) <= section_lines:
                    raise ValueError('entry evidence outside owned primary section')
            if not entry['evidence']:
                raise ValueError('entry missing evidence')
            pair = (entry['section_id'], entry['order'])
            if entry['order'] < 1 or pair in seen_orders:
                raise ValueError('entry reading order duplicate/nonpositive')
            seen_orders.add(pair)
            for field in ('entry_raw', 'lemma_raw'):
                if not supported_raw(entry[field], entry['evidence']):
                    raise ValueError(f'{field} differs from literal OCR; use visual reading separately')
            if not entry['entry_raw']:
                if not entry['visual_entry_reading']:
                    raise ValueError('empty entry without visual recovery')
                warnings.append(f"visual_only_entry:{entry['id']}")
            if entry['visual_entry_reading'] and not any(e['visual_region_id'] for e in entry['evidence']):
                raise ValueError('visual entry reading without visual evidence')
            display = entry['lemma_display']
            raw = entry['lemma_raw'] or ''
            soft_joined = re.sub(r'[-\u00ad]\s*\n\s*', '', raw)
            if display and display not in (raw, soft_joined, re.sub(r'\s+', ' ', soft_joined).strip()):
                if not any(e['visual_region_id'] and 'lemma_display' in e['corrected_fields']
                           and display in (e['visual_reading'] or '') for e in entry['evidence']):
                    raise ValueError('lemma repair requires explicit visual reading or mechanical soft wrap')
            if entry['parent_id']:
                parent = groups['entries'].get(entry['parent_id'])
                if not parent or parent['section_id'] != entry['section_id'] or parent['order'] >= entry['order']:
                    raise ValueError('parent must precede child in same section')
        ref_occurrences, ref_orders = {}, set()
        for ref in dto['references']:
            entry = groups['entries'].get(ref['entry_id'])
            if not entry:
                raise ValueError('reference has no owning entry')
            if not supported_raw(ref['ref_raw'], ref['evidence']):
                raise ValueError('ref_raw differs from literal OCR')
            if not ref['ref_raw'] and not ref['visual_ref_reading']:
                raise ValueError('empty reference without visual recovery')
            if ref['visual_ref_reading'] and not any(e['visual_region_id'] for e in ref['evidence']):
                raise ValueError('reference visual reading without image evidence')
            pair = (ref['entry_id'], ref['order'])
            if ref['order'] < 1 or pair in ref_orders:
                raise ValueError('reference order duplicate/nonpositive')
            ref_orders.add(pair)
            if re.fullmatch(r'\s*(?:vide|vid\.|voir|cf\.)\s+[^\d]+', ref['ref_raw'], re.I):
                raise ValueError('remission without locator is not a material reference')
            if ref['kind'] == 'editorial_page_line' and not (ref['page_start'] and ref['line_start']):
                raise ValueError(f"page-line locator requires separate page and line: {ref['id']} ({ref['ref_raw']!r}), entry {entry['id']}")
            if ref['line_start'] and ref['kind'] not in ('editorial_page_line', 'unresolved'):
                raise ValueError(f"line number must not be serialized as page-only reference: {ref['id']} ({ref['ref_raw']!r}), kind {ref['kind']}, entry {entry['id']}")
            if re.search(r'\b(?:seqq?|passim)\b', ref['ref_raw'], re.I) and ref['page_end'] is not None:
                raise ValueError('open range must not invent an endpoint')
            reading = ref['visual_ref_reading'] or ref['ref_raw']
            for field in ('page_start', 'page_end', 'line_start', 'line_end', 'column'):
                value = ref[field]
                if (value and not re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', reading)
                        and not subscript_axis_supported(field, value, reading, ref['kind'])):
                    raise ValueError(f'{field} not present in printed reference; inheritance remains unresolved: {ref["id"]} ({reading!r})')
            for rid in ref['rule_ids']:
                rule = groups['editorial_rules'].get(rid)
                if not rule or entry['section_id'] not in rule['section_ids']:
                    raise ValueError('reference inherits rule outside its section')
            for field in ('edition', 'tome', 'series'):
                value = ref[field]
                if value and not supported_raw(value, ref['evidence']):
                    if not any(groups['editorial_rules'][rid]['kind'] == 'explicit_declaration'
                               and groups['editorial_rules'][rid][field] == value for rid in ref['rule_ids']):
                        raise ValueError(f"namespace inheritance requires matching explicit scoped declaration: {ref['id']} ({ref['ref_raw']!r}), {field}={value!r}, entry {entry['id']}")
            if any(ref[k] for k in ('edition', 'tome', 'series')) and not ref['rule_ids']:
                if not all(not ref[k] or supported_raw(ref[k], ref['evidence']) for k in ('edition', 'tome', 'series')):
                    raise ValueError('inherited namespace lacks scoped evidence')
            parent_spans = spans[entry['id']]
            for span in spans[ref['id']]:
                if span['page_id'] != primary['page_id']:
                    raise ValueError('reference source is a context page')
                if 'char_start' in span and not any(p['page_id'] == span['page_id'] and
                        p.get('char_start', -1) <= span['char_start'] and
                        p.get('char_end', -1) >= span['char_end'] for p in parent_spans):
                    raise ValueError('reference is outside owning entry span')
            key = digest([ref['ref_raw'], [(s['page_id'], s.get('char_start'), s.get('char_end'),
                                          s.get('visual_evidence')) for s in spans[ref['id']]]])
            if key in ref_occurrences:
                previous = ref_occurrences[key]
                raise ValueError(f"duplicate printed reference occurrence or competing attribution: {previous['id']} / {ref['id']} ({ref['ref_raw']!r}), entries {previous['entry_id']} / {entry['id']}")
            ref_occurrences[key] = ref
            if ref['scripture_id'] and (ref['scripture_id'] not in groups['scripture_refs'] or
                    groups['scripture_refs'][ref['scripture_id']]['entry_id'] != ref['entry_id']):
                raise ValueError('material reference has invalid scripture parent')
        for item in dto['scripture_refs']:
            entry = groups['entries'].get(item['entry_id'])
            if not entry or not item['chapter_start'] or item['chapter_start'] < 1:
                raise ValueError('scripture requires entry and safe chapter')
            mode = entry['scripture_mode'] or groups['sections'][entry['section_id']]['taxonomy']['scripture_mode']
            if mode in ('none', 'incidental_mention'):
                raise ValueError('incidental/absent scripture mode cannot produce a biblical reference')
            evidence_spans(item['book_evidence'], task)
            if not supported_raw(item['book_raw'], item['book_evidence']) or detect_heading(item['book_raw']):
                raise ValueError('biblical book lacks real book heading evidence')
            if any(item[k] is not None and item[k] < 1 for k in ('verse_start', 'chapter_end', 'verse_end')):
                raise ValueError('biblical endpoints must be positive')
        for entry in dto['entries']:
            if entry['kind'] in ('scripture_citation', 'scripture_pericope'):
                if sum(s['entry_id'] == entry['id'] for s in dto['scripture_refs']) != 1:
                    raise ValueError('biblical entry must represent exactly one parsed passage')
        for rule in dto['editorial_rules']:
            if not set(rule['section_ids']) <= groups['sections'].keys() or not set(rule['segment_ids']) <= groups['segments'].keys():
                raise ValueError('editorial rule scope is unknown')
            if not rule['section_ids'] and not rule['segment_ids']:
                raise ValueError('editorial rule cannot have global implicit scope')
            if not supported_raw(rule['literal'], rule['evidence']):
                raise ValueError('editorial rule literal not preserved')
        for item in dto['edition_mappings']:
            if item['rule_id'] not in groups['editorial_rules']:
                raise ValueError('edition map has unknown rule')
            readings = '\n'.join(e['quote'] + '\n' + (e['visual_reading'] or '') for e in item['evidence'])
            if any(value not in readings for value in (item['from_locator'], item['to_locator'])):
                raise ValueError('edition map locator lacks source evidence')
        for item in dto['unresolved']:
            if not set(item['related_ids']) <= all_objects.keys():
                raise ValueError('unresolved item cites unknown objects')
        for cont in dto['continuations']:
            if cont['entry_id'] not in groups['entries']:
                raise ValueError('continuation missing entry')
            if cont['peer_page_id'] is not None and cont['peer_page_id'] not in pages:
                raise ValueError('continuation peer page not supplied')
            if cont['status'] == 'confirmed' and not (cont['peer_page_id'] and cont['peer_quote']):
                raise ValueError('confirmed continuation lacks counterpart')
            warnings.append(f"stitch_pending:{cont['id']}")
        coverage = Counter()
        residual = []
        for cov in dto['coverage']:
            if cov['page_id'] != primary['page_id'] or not set(cov['line_ids']) <= primary_lines.keys():
                raise ValueError('coverage contains non-primary lines')
            if not set(cov['object_ids']) <= all_objects.keys():
                raise ValueError('coverage cites invented object')
            if not cov['object_ids'] and not cov['reason'].strip():
                raise ValueError('unassigned coverage requires explanation')
            for lid in cov['line_ids']:
                coverage[lid] += 1
                if cov['disposition'] in ('entry', 'heading', 'rule', 'continuation'):
                    if not any(lid in ev['line_ids'] for oid in cov['object_ids']
                               for ev in all_objects[oid].get('evidence', [])):
                        raise ValueError('claimed coverage lacks object evidence on line')
                elif cov['disposition'] in ('unreadable', 'unassigned', 'exclusion'):
                    residual.append(lid)
                if ownership[lid] == 'owned' and cov['disposition'] in ('context', 'boundary'):
                    raise ValueError('owned line hidden as context/boundary')
        if set(coverage) != set(primary_lines) or any(n != 1 for n in coverage.values()):
            raise ValueError('each primary visible line must be accounted exactly once')
        expected_regions = {r['id'] for r in primary['regions']}
        visual_regions = [v['region_id'] for v in dto['visual_coverage']]
        if set(visual_regions) != expected_regions or len(visual_regions) != len(expected_regions):
            raise ValueError('primary image regions lack complete visual coverage accounting')
        if any(v['status'] != 'inspected' or v['missing_ocr_content'] for v in dto['visual_coverage']):
            warnings.append('visual_coverage_requires_review')
        for req in dto['expansion_requests']:
            if req['page_id'] not in pages or not 1 <= req['max_files'] <= 2:
                raise ValueError('expansion must be bounded to at most two neighbors')
            warnings.append('bounded_expansion_pending')
        if residual:
            warnings.append('residual_lines')
        if dto['unresolved']:
            warnings.append('editorial_uncertainties')
        if any(ev['uncertain'] for item in all_objects.values() for ev in item.get('evidence', [])):
            warnings.append('uncertain_reading')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(str(exc))
    intervals = sorted((s['char_start'], s['char_end']) for key, values in spans.items()
                       if key in groups.get('entries', {}) or key in groups.get('editorial_rules', {})
                       for s in values if 'char_start' in s and s['page_id'] == primary['page_id']) if 'groups' in locals() else []
    merged = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return {'valid': not errors, 'errors': errors, 'warnings': sorted(set(warnings)), 'spans': spans,
            'coverage': {'accounted_lines': sum(coverage.values()) if 'coverage' in locals() else 0,
                         'primary_lines': len(primary_lines),
                         'entry_and_rule_verified_ocr_chars': sum(b-a for a, b in merged),
                         'accounting_is_not_transcription_completeness': True,
                         'residual_lines': residual if 'residual' in locals() else [],
                         'visual_completeness': 'model_reported_not_independently_verified'},
            'counts': {k: len(dto[k]) for k in ('entries', 'references', 'scripture_refs', 'continuations', 'unresolved')}}

"""Page/line candidate grammar, enabled only by an editorial section profile.

Numbers are never physical file suffixes. Ambiguous small-number lists and
unmarked namespaces yield alternatives, not certified editorial interpretations.
"""
import re

from tools.indexing.entry_parser_experiment import display

VERSION = 'page-line-candidates-v2'
NUMBER = r'[0-9]{1,4}'
# A run cannot cross prose: punctuation and the declared parallel-series marker
# are the only bridges. Nota is retained as a literal qualifier.
RUN = re.compile(
    rf'(?<![\w])(?:Leo\s+)?{NUMBER}(?:\s+Nota\b)?'
    rf'(?:\s*[,;.—–-]\s*(?:Leo\s+)?{NUMBER}(?:\s+Nota\b)?)*', re.I)
TOKEN = re.compile(r'Leo\b|Nota\b|[0-9]{1,4}|[,;.—–-]', re.I)
EXTERNAL_SERIES = re.compile(r'\b(?:Cedreno|Cedrenus|Cedr?\.?)\s*$', re.I)


def detect_page_line_convention(text):
    """Detect the printed declaration, not merely a numeric shape or volume ID."""
    flat = ' '.join(text.split())
    return bool(re.search(r'Primus numerus signat paginas editionis Petropolitan', flat, re.I)
                and re.search(r'secundus lineas ejusdem editionis', flat, re.I))


def interpret_page_line_entry(entry, *, default_series='petropolitana', line_ceiling=99):
    """Return literal page candidates and bounded alternatives for one entry.

    ``line_ceiling`` ranks numeric interpretations only; it is not a documented
    line maximum. Alternatives and uncertainty are retained. Caller must scope
    this function to a page/line section (not the preceding onomastic page list).
    ``source_span`` and component spans are absolute Unicode half-open offsets.
    """
    if entry.get('entry_type', 'material') != 'material':
        return None
    raw = entry['entry_raw']
    base = entry.get('char_start', 0)
    refs = []
    runs = []
    first = None
    previous_end = 0
    owner = None
    for run in RUN.finditer(raw):
        tokens = list(TOKEN.finditer(run[0]))
        numbers = [t for t in tokens if t[0].isdigit()]
        if len(numbers) < 2 and not re.search(r'\bNota\b', run[0], re.I):
            continue
        # Exclude bare prose numbers and zero rather than manufacture a page.
        if any(int(n[0]) == 0 for n in numbers):
            continue
        bridge = raw[previous_end:run.start()]
        if any(c.isalpha() for c in bridge):
            trimmed = bridge.strip(' \n\t(),.;—–-')
            owner_start = previous_end + bridge.index(trimmed)
            owner = {'owner_raw': trimmed, 'owner_span': [base+owner_start, base+owner_start+len(trimmed)],
                     'query_text': display(trimmed), 'owner_status': 'local_text_before_numeric_run'}
        previous_end = run.end()
        first = run.start() if first is None else first
        run_ref_start = len(refs)
        runs.append({'raw': run[0], 'source_span': [base+run.start(), base+run.end()]})
        token_base = base+run.start()
        current = None
        after_leo = False
        explicit = False
        delimiter = None
        i = 0
        while i < len(tokens):
            token = tokens[i]
            value = token[0]
            if value.lower() == 'leo':
                after_leo = explicit = True
                current = None
                i += 1
                continue
            if value.lower() == 'nota':
                if current:
                    current['qualifier_raw'] = value
                    current['qualifier_span'] = [token_base+token.start(), token_base+token.end()]
                    _extend(current, token_base+token.end(), raw, base)
                i += 1
                continue
            if not value.isdigit():
                delimiter = value
                i += 1
                continue
            number = int(value)
            is_line = (current is not None and delimiter == ',' and number <= line_ceiling)
            line_range_end = (current is not None and delimiter in {'-', '—', '–'}
                              and number <= line_ceiling
                              and not (i+1 < len(tokens) and tokens[i+1][0] == ','))
            if is_line or line_range_end:
                current['lines_raw'].append(value)
                current['line_spans'].append([token_base+token.start(), token_base+token.end()])
                current['line_raw'] = ', '.join(current['lines_raw'])
                _extend(current, token_base+token.end(), raw, base)
                current['uncertainties'].append('line_grouping_by_numeric_scale')
                if line_range_end:
                    current['uncertainties'].append('line_range_preserved_not_expanded')
                # A small number can also begin another page/line pair. Keep
                # that competing parse with its own printed occurrence span.
                if (is_line and i+2 < len(tokens) and tokens[i+1][0] == ','
                        and tokens[i+2][0].isdigit()
                        and int(tokens[i+2][0]) <= line_ceiling):
                    nxt = tokens[i+2]
                    alt = _reference(number, token, nxt, token_base, raw, base, default_series)
                    alt['candidate_role'] = 'alternative_page_line_grouping'
                    alt['uncertainties'] = ['small_page_or_additional_line']
                    if after_leo:
                        alt['uncertainties'].append('unmarked_series_after_leo')
                        alt['series_alternatives'] = ['leo']
                    refs.append(alt)
                i += 1
                continue
            line = None
            if (i+2 < len(tokens) and tokens[i+1][0] == ','
                    and tokens[i+2][0].isdigit()):
                line = tokens[i+2]
            current = _reference(number, token, line, token_base, raw, base,
                                 'leo' if explicit else default_series)
            if explicit:
                # Include the actual printed Leo token in the reference span.
                start = tokens[i-1].start() if i and tokens[i-1][0].lower() == 'leo' else token.start()
                current['source_span'][0] = token_base+start
                _extend(current, current['source_span'][1], raw, base)
                current['series_basis'] = 'explicit_leo'
            elif after_leo:
                current['series_alternatives'] = ['leo']
                current['uncertainties'].append('unmarked_series_after_leo')
            if line is None:
                current['uncertainties'].append('line_absent')
            elif int(line[0]) > line_ceiling:
                current['uncertainties'].append('line_above_heuristic_ceiling')
            if delimiter in {'-', '—', '–'}:
                current['uncertainties'].append('range_endpoint_not_expanded')
            refs.append(current)
            explicit = False
            delimiter = None
            i += 3 if line is not None else 1
        run_refs = refs[run_ref_start:]
        for ref in run_refs:
            if 'line_above_heuristic_ceiling' in ref['uncertainties']:
                refs.append(_high_line_page_alternative(ref, raw, base, default_series))
        for ref in refs[run_ref_start:]:
            if owner:
                ref.update(owner)
                if any(c.isdigit() for c in owner['owner_raw']):
                    ref['uncertainties'].append('owner_contains_unparsed_numbers')
                external = EXTERNAL_SERIES.search(owner['owner_raw'])
                if external and ref['series_basis'] != 'explicit_leo':
                    ref['series_key'] = 'cedrenus'
                    ref['series_basis'] = 'explicit_external_marker'
                    ref['external_marker_raw'] = external[0]
                    ref['external_marker_span'] = [owner['owner_span'][0]+external.start(),
                                                   owner['owner_span'][0]+external.end()]
                    ref['uncertainties'].append('external_locator_units_uninterpreted')
                    ref.pop('series_alternatives', None)
    if not refs or first is None:
        return None
    head = raw[:first].strip(' \n\t(,;')
    if not head or not any(c.isalpha() for c in head):
        return None
    # Alternatives share source occurrences, not extra printed references.
    expanded = []
    for ref in refs:
        ref['uncertainties'] = sorted(set(ref['uncertainties']))
        expanded.append(ref)
        for series in ref.pop('series_alternatives', []):
            alt = dict(ref, series_key=series, series_basis='unmarked_series_alternative',
                       candidate_role='alternative_namespace')
            expanded.append(alt)
    return {'query_text': display(head), 'interpreted_references': expanded,
            'reference_runs': runs, 'grammar_version': VERSION,
            'line_ceiling_heuristic': line_ceiling,
            'segmentation_status': 'candidate_owner_preserved_not_certified',
            'input_flags': list(entry.get('flags', [])), 'import_ready': False}


def _reference(number, page, line, base, raw, entry_base, series):
    start, end = base+page.start(), base+(line.end() if line else page.end())
    return {'number': number, 'series_key': series,
            'series_basis': 'section_default', 'reference_kind': 'editorial_page_line',
            'source_span': [start, end], 'ref_raw': raw[start-entry_base:end-entry_base],
            'page_span': [base+page.start(), base+page.end()],
            'line_raw': line[0] if line else None,
            'lines_raw': [line[0]] if line else [],
            'line_spans': [[base+line.start(), base+line.end()]] if line else [],
            'line_location_status': 'not_located', 'candidate_role': 'primary',
            'uncertainties': [], 'canonical_certified': False}


def _extend(ref, end, raw, base):
    ref['source_span'][1] = end
    ref['ref_raw'] = raw[ref['source_span'][0]-base:end-base]


def _high_line_page_alternative(ref, raw, base, default_series):
    """Preserve a large alleged line as a competing page, never silently fix it."""
    page_span = ref['line_spans'][0]
    lines = ref['lines_raw'][1:]
    end = ref['line_spans'][-1][1]
    series = ref['series_key']
    parallel = series == 'leo' or 'leo' in ref.get('series_alternatives', [])
    alt = {'number': int(ref['lines_raw'][0]), 'series_key': default_series if parallel else series,
           'series_basis': 'section_default', 'reference_kind': 'editorial_page_line',
           'source_span': [page_span[0], end], 'page_span': list(page_span),
           'ref_raw': raw[page_span[0]-base:end-base], 'line_raw': ', '.join(lines) or None,
           'lines_raw': list(lines), 'line_spans': [list(x) for x in ref['line_spans'][1:]],
           'line_location_status': 'not_located',
           'candidate_role': 'alternative_page_line_grouping',
           'uncertainties': ['high_line_or_new_page'], 'canonical_certified': False}
    if not lines:
        alt['uncertainties'].append('line_absent')
    if parallel:
        alt['uncertainties'].append('unmarked_series_after_leo')
        alt['series_alternatives'] = ['leo']
    return alt

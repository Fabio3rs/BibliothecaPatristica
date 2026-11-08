"""Evidence-bound metadata and literal parsing; never locate target files."""
import re
import unicodedata


def subscript_axis_supported(field, value, reading, kind):
    """Verify a declared page/line value without merging baseline and subscripts."""
    if kind != 'editorial_page_line' or field not in ('page_start', 'line_start', 'line_end'):
        return False
    pattern = r'\s*(?:cf\.\s*)?([0-9]+)([₀₁₂₃₄₅₆₇₈₉]+)(?:[₋–-]([₀₁₂₃₄₅₆₇₈₉]+))?\s*'
    translate = str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789')
    match = re.fullmatch(pattern, reading, re.I)
    if match:
        axes = {'page_start': match[1], 'line_start': match[2].translate(translate),
                'line_end': (match[3] or match[2]).translate(translate)}
        if value == axes[field]:
            return True
    return False


def page_reference_parts(ref, text, page_line_context=False):
    """Parse only the model's unambiguous page-only reading, without expansion.

    A local page/line declaration takes precedence over the model's page-only kind.
    Ranges, ibid., mixed notations and partially filled interpretations stay intact.
    """
    if (page_line_context or ref['kind'] != 'editorial_page' or ref['alternatives']
            or any(ref[k] is not None for k in ('page_start', 'page_end', 'column', 'line_start', 'line_end'))):
        return None
    atom = r'[1-9][0-9]*(?:\s+(?:bis|ter|quater|quinquies|sexies|passim))?'
    if not re.fullmatch(r'\s*' + atom + r'(?:\s*[,;]\s*' + atom + r')*\s*', text):
        return None
    return [({**ref, 'ref_raw': m.group(),
             'page_start': re.match(r'[0-9]+', m.group()).group()}, m.start(), m.end())
            for m in re.finditer(atom, text)]


def separated_header_labels(aligner, value):
    """Recover two printed labels, without treating them as an expanded range."""
    match = re.fullmatch(r'\s*(\d+)\s*[-–—,]\s*(\d+)\s*', value)
    if not match or match[1] == match[2]:
        return None
    page = aligner.page
    headers = {b['id'] for b in page['blocks'] if b['type'] == 'cabecalho'}
    chosen = []
    for label in match.groups():
        lines = [l for l in page['lines'] if l['block_id'] in headers and l['text'].strip() == label]
        if len(lines) != 1:
            return None
        chosen.append(lines[0])
    if chosen[0]['block_id'] != chosen[1]['block_id'] or chosen[0]['char_start'] >= chosen[1]['char_start']:
        return None
    evidence = []
    for line in chosen:
        found = aligner.match(line['text'], within=[(line['char_start'], line['char_end'])], fuzzy=False)
        if found['status'] != 'matched':
            return None
        evidence.extend(aligner.evidence(found))
    return {'evidence': evidence, 'labels': list(match.groups()), 'model_label': value,
            'interpretation': 'separate_printed_header_labels_not_an_inferred_range'}


def initial(text):
    folded = ''.join(c for c in unicodedata.normalize('NFKD', text or '') if not unicodedata.combining(c))
    return next((c.upper() for c in folded if c.isalpha()), '')


def alphabet_dividers(page, entries, assigned_lines):
    """Single-letter markers supported by two following entries in the same block."""
    block_types = {b['id']: b['type'] for b in page['blocks']}
    lines = {l['id']: l for l in page['lines']}
    anchors = []
    for entry in entries:
        lids = [lid for ev in entry['evidence'] if ev['page_id'] == page['page_id'] for lid in ev['line_ids']]
        if not lids or not entry['lemma_raw'] or entry['parent_id']:
            continue
        first = min((lines[lid] for lid in lids), key=lambda l: l['char_start'])
        anchors.append((first['char_start'], first['block_id'], entry))
    anchors.sort(key=lambda item: item[0])
    for line in page['lines']:
        label = line['text'].strip()
        if (line['id'] in assigned_lines or block_types[line['block_id']] != 'texto_principal'
                or len(label) != 1 or not label.isalpha() or not label.isupper()
                or label in 'IVXLCDM'):
            continue
        after = [a for a in anchors if a[0] > line['char_end']][:2]
        before = [a for a in anchors if a[0] < line['char_start']]
        if len(after) != 2 or any(a[1] != line['block_id'] for a in after):
            continue
        sid = after[0][2]['section_id']
        if any(a[2]['section_id'] != sid or initial(a[2]['lemma_raw']) != initial(label) for a in after):
            continue
        if before and before[-1][2]['section_id'] != sid:
            continue
        between = [l for l in page['lines'] if line['char_end'] < l['char_start'] < after[0][0]]
        if any(l.get('heading_hint', {}).get('role') == 'stop_boundary' for l in between if l.get('heading_hint')):
            continue
        yield {'line': line, 'section_id': sid, 'support_entry_ids': [a[2]['id'] for a in after]}

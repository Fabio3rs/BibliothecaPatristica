"""Literal opening anchors for Innocent III's registers, with source offsets.

This is a candidate map, not an edition of the letters. Book context comes from
printed register headings, never from the corpus volume number. Marginal letters,
footnotes and tables of contents are excluded. No database or OCR is modified.
"""
from __future__ import annotations

import hashlib
import html
import copy
import re
from collections import Counter
from pathlib import Path

VERSION = "innocent-epistle-map-v2"
BLOCK = re.compile(r'<bloco\b(?P<attrs>[^>]*)>(?P<text>.*?)</bloco\s*>', re.S | re.I)
TYPE = re.compile(r'\btipo\s*=\s*["\']([^"\']+)', re.I)
NUMBER = re.compile(r'(?m)^[ \t]*(?:EPISTOLA[ \t]+)?(?P<number>[IVXLCDM]+|\d{1,3})'
                    r'(?:[ \t]*[-—–][ \t]*(?P<range_end>[IVXLCDM]+|\d{1,3}))?'
                    r'(?P<suffix>[ \t]+(?:bis|ter))?(?:[ \t]*\(\d+\))?'
                    r'[ \t]*[.:](?:[ \t]*[-—–])?[ \t]*(?=\S|$)')
ROMAN = re.compile(r'M{0,3}(?:CM|CD|D?C{0,4})(?:XC|XL|L?X{0,4})(?:IX|IV|V?I{0,4})$')
SUBTRACTIVE_ROMAN = re.compile(r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$')
BOOK_HEADER = re.compile(r'REGEST\w*\s+(?:SIVE\s+EPISTOLARUM\s+)?LIB[. ]+([IVXLCDM]+)\b', re.I)
BOOK_TITLE = re.compile(r'\bLIBER\s+([A-Z]+(?:\s+(?:PRIMUS|SECUNDUS|TERTIUS|QUARTUS|QUINTUS|SEXTUS))?)\b')
ORDINALS = dict(zip(('PRIMUS', 'SECUNDUS', 'TERTIUS', 'QUARTUS', 'QUINTUS',
                    'SEXTUS', 'SEPTIMUS', 'OCTAVUS', 'NONUS', 'DECIMUS',
                    'UNDECIMUS', 'DUODECIMUS', 'DECIMUS TERTIUS',
                    'DECIMUS QUARTUS', 'DECIMUS QUINTUS', 'DECIMUS SEXTUS'), range(1, 17)))
STOP = re.compile(r'\b(?:INDEX|INDICES|SERMONES|SERMO|MYSTERIO|CONTEMPTU)\b|'
                  r'QU[ÆAE]+\s+IN\s+HOC\s+TOMO|VARIORUM\s+EPISTOL', re.I)
EXCLUDED = {'nota', 'notas', 'nota_marginal', 'nota_rodape', 'rodape', 'aparato_critico', 'cabecalho'}


def roman_number(raw: str) -> int | None:
    raw = raw.upper()
    if not raw or not ROMAN.fullmatch(raw):
        return None
    values = dict(I=1, V=5, X=10, L=50, C=100, D=500, M=1000)
    return sum(-values[c] if i + 1 < len(raw) and values[c] < values[raw[i + 1]]
               else values[c] for i, c in enumerate(raw))


def _blocks(raw):
    blocks = []
    for match in BLOCK.finditer(raw):
        kind = TYPE.search(match['attrs'])
        blocks.append({'kind': kind[1] if kind else '', 'text': match['text'],
                       'start': match.start('text'), 'end': match.end('text')})
    return blocks


def _plain(raw):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _context(text):
    """Return context only from actual heading text, not prose citations."""
    upper = _plain(text).upper()
    if STOP.search(upper):
        return {'series': None, 'book': None}
    if 'REGISTRUM' in upper and 'IMPERII' in upper:
        return {'series': 'imperial_register', 'book': None}
    if 'SUPPLEMENTUM' in upper and ('REGEST' in upper or 'AN.' in upper):
        return {'series': 'supplement', 'book': None}
    if 'APPENDIX AD REGESTORUM PRIORES LIBROS' in upper:
        return {'series': 'appendix', 'book': None}
    appendix = re.search(r'APPENDIX\s+AD\s+LIBRUM\s+([IVXLCDM]+)\b', upper)
    if appendix:
        return {'series': 'appendix', 'book': roman_number(appendix[1])}
    match = BOOK_HEADER.search(upper)
    if match and (number := roman_number(match[1])) and number <= 20:
        return {'series': 'register', 'book': number}
    if 'REGESTORUM' in upper or upper.startswith('LIBER '):
        title = BOOK_TITLE.search(upper)
        if title and title[1] in ORDINALS:
            return {'series': 'register', 'book': ORDINALS[title[1]]}
    return None


def _body_blocks(blocks):
    """OCR's cabecalho type also covers internal letter numbers and rubrics."""
    result = []
    for block in blocks:
        if block['kind'] not in EXCLUDED:
            result.append(block)
        elif block['kind'] == 'cabecalho':
            text = _plain(block['text'])
            if (text and not _context(text)
                    and not re.search(r'\b(?:PONTIFICATUS|INNOCENTII|REGEST\w*)\b', text, re.I)
                    and not re.fullmatch(r'[\d\s.,-]+', text)):
                result.append(block)
    return result


def parse_page(raw: str, *, file: str, volume: str, physical: int,
               inherited_context: dict | None = None) -> tuple[list[dict], dict | None]:
    """Extract openings and return the final context for the adjacent OCR page.

    Source spans address Unicode characters in the unmodified XML. Opening text
    excludes footnotes and ends at the next recognized opening or section change.
    The map deliberately retains duplicates and sequence anomalies as candidates.
    """
    blocks = _blocks(raw)
    headers = [block for block in blocks if block['kind'] == 'cabecalho']
    header_text = ' '.join(block['text'] for block in headers)
    if STOP.search(_plain(header_text)):
        return [], None
    context = dict(inherited_context) if inherited_context else None
    section_context = context.get('section_context') if context else None
    header_context = _context(header_text)
    body_titles = [(block, _context(block['text'])) for block in blocks
                   if block['kind'] not in EXCLUDED and len(_plain(block['text'])) < 450
                   and re.search(r'\b(?:LIBER|REGESTORUM|REGISTRUM|SUPPLEMENTUM|APPENDIX)\b', block['text'])]
    # A mixed page's running header can name the NEW book before its actual
    # opening in the second column; retain the previous context until that title.
    if header_context is not None and not any(c and c['series'] for _, c in body_titles):
        context = {**header_context, 'file': file, 'physical': physical,
                   'spans': [{'start': b['start'], 'end': b['end'], 'raw': b['text'],
                              'kind': 'series_book_header'} for b in headers]}
        # A forward book transition ends the older title's jurisdiction even if
        # the new full title is missing from OCR. A backwards running head can
        # conflict with the still-current explicit title (PL214, book II).
        advanced_book = (section_context and context.get('series') == 'register'
                         and context.get('book') and section_context.get('book')
                         and context['book'] > section_context['book'])
        if section_context and context.get('series') == section_context.get('series') and not advanced_book:
            context['section_context'] = section_context
        else:
            section_context = None
    events = []
    body = _body_blocks(blocks)
    for index, block in enumerate(body):
        text = block['text']
        local_context = next((c for b, c in body_titles if b is block), None)
        if local_context:
            context = {**local_context, 'file': file, 'physical': physical,
                       'spans': [{'start': block['start'], 'end': block['end'],
                                  'raw': text, 'kind': 'series_book_title'}]}
            section_context = copy.deepcopy(context) if context.get('series') else None
            if section_context:
                context['section_context'] = section_context
            events.append((block['start'], 'boundary', None))
        if not context or not context.get('series'):
            continue
        for match in NUMBER.finditer(text):
            number = int(match['number']) if match['number'].isdigit() else roman_number(match['number'])
            if not number or number > 1000:
                continue
            number_alone = not text[match.end():].split('\n', 1)[0].strip()
            # A numbered letter has a recipient rubric, either on this line or
            # immediately after the numeral, possibly in the next OCR block.
            remainder = text[match.end():]
            if not remainder.strip():
                remainder = '\n'.join(b['text'] for b in body[index + 1:index + 3])
            lines = [line.strip() for line in remainder.splitlines() if line.strip()]
            if not lines:
                continue
            if len(lines) < 3:
                lines.extend(line.strip() for following in body[index + 1:index + 3]
                             for line in following['text'].splitlines() if line.strip())
            rubric = _plain(lines[0])
            letters = ''.join(c for c in rubric if c.isalpha())
            upper_rubric = len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) > .85
            supplement_rubric = context['series'] == 'supplement' and len(letters) >= 12
            protocol_rubric = bool(re.match(r'(?:INNOCENTIUS|HONORIUS|GREGORIUS)\s+episcopus\b', rubric))
            # Some consecutive letters abbreviate the preceding document instead
            # of repeating its recipient rubric. The numeral must stand alone;
            # a recognizable date or the printed formula supplies the evidence.
            abbreviated_rubric = (context['series'] == 'register' and number_alone
                and bool(re.match(r'In\s+eundem\s+modum\b', rubric)
                         or (re.match(r'\((?:Datum|Laterani|Rom[æae]|Viterbii|Anagni[æae])\b', rubric)
                             and re.match(r'(?:In\s+eundem\s+modum|Cum\s+a\s+nobis\s+petitur|Justis\s+petentium)\b',
                                          _plain(' '.join(lines[1:3]))))))
            if not (upper_rubric or supplement_rubric or protocol_rubric or abbreviated_rubric):
                continue
            start, end = block['start'] + match.start('number'), block['start'] + match.end()
            flags = ['candidate_not_certified']
            if context['file'] != file:
                flags.append('book_or_series_inherited_from_previous_page')
            if supplement_rubric and not upper_rubric:
                flags.append('supplement_sentence_case_rubric')
            if abbreviated_rubric:
                flags.append('abbreviated_letter_without_repeated_recipient')
            if block['kind'] == 'cabecalho':
                flags.append('internal_letter_heading_typed_as_header')
            if not match['number'].isdigit() and not SUBTRACTIVE_ROMAN.fullmatch(match['number']):
                flags.append('additive_roman_number')
            event = {'series': context['series'], 'book': context.get('book'), 'epistle': number,
                     'suffix': (match['suffix'] or '').strip() or None,
                     'file': file, 'volume': volume, 'physical': physical,
                     'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                     'spans': [{'start': start, 'end': end, 'raw': raw[start:end], 'kind': 'epistle_number'}],
                     'evidence': {'context': context, 'recipient_rubric': rubric,
                                  'method': ('literal_number_and_abbreviated_letter_formula'
                                             if abbreviated_rubric else 'literal_number_and_recipient_rubric')},
                     'uncertainties': flags, 'opening_text': '', 'version': VERSION,
                     'extent_status': 'opening_only',
                     'literal_range_end': (int(match['range_end']) if match['range_end'].isdigit()
                                           else roman_number(match['range_end'])) if match['range_end'] else None}
            events.append((start, 'opening', event))
    events.sort(key=lambda event: event[0])
    anchors = []
    for index, (start, kind, anchor) in enumerate(events):
        if kind != 'opening':
            continue
        end = events[index + 1][0] if index + 1 < len(events) else len(raw)
        pieces = []
        for block in body:
            a, b = max(start, block['start']), min(end, block['end'])
            if a < b:
                pieces.append(raw[a:b])
                anchor['spans'].append({'start': a, 'end': b, 'raw': raw[a:b], 'kind': 'opening_text'})
        anchor['opening_text'] = _plain('\n'.join(pieces))
        anchor['evidence']['opening_text_scope'] = 'current_page_until_next_opening_or_section'
        anchors.append(anchor)
        # Running heads can conflict with the book's explicit opening (including
        # errors in the printed book). Preserve both readings as candidates.
        section = anchor['evidence']['context'].get('section_context')
        if section and section.get('series') == anchor['series'] and section.get('book') != anchor['book']:
            anchor['uncertainties'].append('running_header_conflicts_with_explicit_book_title')
            alternate = copy.deepcopy(anchor)
            alternate['book'] = section['book']
            alternate['evidence']['context'] = section
            alternate['evidence']['conflicting_running_header'] = anchor['evidence']['context']
            alternate['uncertainties'].append('book_from_explicit_section_title')
            anchors.append(alternate)
        if anchor['literal_range_end'] is not None:
            end_number = anchor['literal_range_end']
            anchor['uncertainties'].append('shared_printed_opening_for_epistle_range')
            if anchor['epistle'] < end_number <= anchor['epistle'] + 20:
                for number in range(anchor['epistle'] + 1, end_number + 1):
                    additional = copy.deepcopy(anchor)
                    additional['epistle'] = number
                    anchors.append(additional)
            else:
                anchor['uncertainties'].append('unexpanded_large_or_invalid_epistle_range')
    return anchors, context


def build_destination_map(root: Path, volumes=('PL214', 'PL215', 'PL216', 'PL217')) -> list[dict]:
    """Read scoped OCR volumes once; callers persist records to staging only."""
    root = Path(root)
    result = []
    for volume in volumes:
        pages = []
        for file in (root / 'teste' / volume / 'text').glob('*.txt'):
            match = re.search(r'-(\d+)\.txt$', file.name)
            if match:
                pages.append((int(match[1]), file))
        context, previous_physical = None, None
        for physical, file in sorted(pages):
            if previous_physical is not None and physical != previous_physical + 1:
                context = None
            raw = file.read_text(encoding='utf-8')
            anchors, context = parse_page(raw, file=str(file.resolve()), volume=volume,
                                           physical=physical, inherited_context=context)
            result.extend(anchors)
            previous_physical = physical
    counts = Counter((a['series'], a['book'], a['epistle'], a['suffix']) for a in result)
    last = {}
    for anchor in result:
        key = (anchor['series'], anchor['book'], anchor['epistle'], anchor['suffix'])
        if counts[key] > 1:
            anchor['uncertainties'].append('multiple_literal_openings_for_same_key')
        series = (anchor['volume'], anchor['series'], anchor['book'])
        if series in last and anchor['epistle'] < last[series]:
            anchor['uncertainties'].append('non_monotonic_epistle_sequence')
        last[series] = anchor['epistle']
    return result

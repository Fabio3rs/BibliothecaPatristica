"""Literal Arnobius book/chapter page map; no chapter-to-page offset or writes."""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.roman_pair_index_grammar import roman_value

VERSION = 'arnobius-book-targets-v1'
ORDINALS = dict(PRIMUS=1, SECUNDUS=2, TERTIUS=3, QUARTUS=4,
                QUINTUS=5, SEXTUS=6, SEPTIMUS=7)
OPENING = re.compile(r'ARNOBII AFRI\s+DISPUTATIONUM ADVERSUS GENTES\s+LIBRI SEPTEM\.')
CLOSING = re.compile(r'APPENDIX\s+AD ARNOBII AFRI DISPUTATIONUM ADVERSUS GENTES:')
BOOK = re.compile(r'LIBER\s+(' + '|'.join(ORDINALS) + r')\.')
CONTENTS = re.compile(r'INDEX CAPITUM\.')
COMMENTARY = re.compile(r'COMMENTARIUS\.')
# Dash-prefixed lemmas belong to commentary. Oberth. is parallel numbering.
CHAPTER = re.compile(r'(?:^|\n)[ \t]*(?P<number>[IVXLC]+|[1-9]\d?)\.[ \t]*'
                     r'(?P<parallel>\(\s*Oberth\.\s*[IVXLC]+\.\s*\)\s*)?(?=[A-ZÆŒ])')
MAX_MISSING_CHAPTER_GAP = 4
MAX_MISSING_PHYSICAL_GAP = 8


def _physical(path):
    return int(path.stem.rsplit('-', 1)[-1])


def _evidence(path, text, span, **extra):
    a, b = span
    return dict(file=str(path.resolve()), physical=_physical(path), span=[a, b],
                raw=text[a:b], sha256=hashlib.sha256(text.encode()).hexdigest(), **extra)


def _unique_boundary(loaded, pattern):
    matches = [(i, m) for i, (_, text) in enumerate(loaded) for m in pattern.finditer(text)]
    if len(matches) != 1:
        raise ValueError(f'Arnobius boundary must occur once: {pattern.pattern}: {len(matches)}')
    return matches[0]


def chapter_markers(text, first=0, last=None):
    """Read main-text labels before the first commentary after `first`."""
    last = len(text) if last is None else last
    commentary = COMMENTARY.search(text, first, last)
    if commentary:
        last = commentary.start()
    for block in blocks(text):
        if block['kind'] != 'texto_principal':
            continue
        a, b = max(first, block['body_start']), min(last, block['body_end'])
        if a >= b:
            continue
        for match in CHAPTER.finditer(text[a:b]):
            token = match['number']
            value = int(token) if token.isdigit() else roman_value(token)
            if value is None:
                continue
            start, end = a + match.start('number'), a + match.end()
            yield dict(chapter=value, span=[start, end], raw=text[start:end],
                       parallel_raw=(match['parallel'] or '').strip(),
                       snippet=text[end:min(b, end + 140)])


def build_map(root: Path) -> dict:
    paths = sorted(Path(root, 'teste/PL005/text').glob('*.txt'), key=lambda p: (_physical(p), p.name))
    loaded = [(p, p.read_text()) for p in paths]
    oi, om = _unique_boundary(loaded, OPENING)
    ci, cm = _unique_boundary(loaded, CLOSING)
    if oi >= ci:
        raise ValueError('Arnobius opening must precede appendix')
    boundaries = [_evidence(*loaded[oi], om.span(), role='work_opening'),
                  _evidence(*loaded[ci], cm.span(), role='appendix_boundary')]
    result = dict(version=VERSION, volume='PL005', namespace='book_chapter',
                  work='Arnobii adversus gentes libri septem', boundaries=boundaries,
                  books={}, pages=[], rejected_markers=[],
                  parameters=dict(max_missing_chapter_gap=MAX_MISSING_CHAPTER_GAP,
                                  max_missing_physical_gap=MAX_MISSING_PHYSICAL_GAP))
    book = None
    last_chapter = 0
    for i in range(oi, ci + 1):
        path, text = loaded[i]
        first, last = (om.start() if i == oi else 0), (cm.start() if i == ci else len(text))
        result['pages'].append(dict(file=str(path.resolve()), physical=_physical(path),
                                   sha256=hashlib.sha256(text.encode()).hexdigest()))
        contents = list(CONTENTS.finditer(text, first, last))
        if len(contents) > 1:
            raise ValueError(f'Multiple book contents boundaries: {path}')
        if contents:
            marker = contents[0]
            headings = list(BOOK.finditer(text, first, marker.start()))
            if not headings:
                raise ValueError(f'Book contents lacks ordinal opening: {path}')
            heading = headings[-1]
            new_book = ORDINALS[heading[1]]
            if new_book != (book or 0) + 1:
                raise ValueError(f'Unexpected book sequence at {path}: {book} -> {new_book}')
            # Prior book can still occupy the top of this same physical file.
            if book:
                result['books'][str(book)]['end'] = _evidence(path, text, heading.span(), role='next_book_opening')
            book, last_chapter, first = new_book, 0, marker.end()
            result['books'][str(book)] = dict(opening=_evidence(path, text, heading.span()), anchors=[])
        if book is None:
            continue
        for marker in chapter_markers(text, first, last):
            anchor = _evidence(path, text, marker['span'], chapter=marker['chapter'],
                               book=book, parallel_raw=marker['parallel_raw'], snippet=marker['snippet'])
            # Reject outliers rather than restarting a book at a quoted numeral.
            if not last_chapter < marker['chapter'] <= last_chapter + MAX_MISSING_CHAPTER_GAP:
                result['rejected_markers'].append(dict(anchor, reason='nonincreasing_or_large_chapter_jump'))
                continue
            result['books'][str(book)]['anchors'].append(anchor)
            last_chapter = marker['chapter']
    if sorted(result['books']) != [str(n) for n in range(1, 8)]:
        raise ValueError('Expected seven independently identified Arnobius books')
    result['books']['7']['end'] = dict(boundaries[-1])
    for section in result['books'].values():
        section['chapters'] = {str(a['chapter']): a for a in section['anchors']}
        section['missing_between_anchors'] = [n for n in range(1, max([a['chapter'] for a in section['anchors']], default=0) + 1)
                                               if str(n) not in section['chapters']]
    result['counts'] = dict(books=len(result['books']), pages=len(result['pages']),
                           anchors=sum(len(b['anchors']) for b in result['books'].values()),
                           rejected_markers=len(result['rejected_markers']))
    return result


def _pages_for(book, chapter, mapping):
    section = mapping['books'].get(str(book))
    if not section:
        return []
    anchors = section['anchors']
    exact = section['chapters'].get(str(chapter))
    if exact:
        after = [a for a in anchors if a['chapter'] > chapter]
        right = after[0] if after else section['end']
        end_page = right['physical'] - (right.get('role') == 'appendix_boundary')
        warnings = ['chapter_span_includes_commentary_and_boundary_page']
        if after and right['chapter'] != chapter + 1:
            warnings.append('successor_chapter_marker_missing')
        return [dict(file=p['file'], physical=p['physical'],
                     score=.93 if p['file'] == exact['file'] else .72,
                     evidence=[dict(kind='literal_chapter_span', book=book, chapter=chapter, left=exact, right=right)],
                     uncertainties=list(warnings))
                for p in mapping['pages'] if exact['physical'] <= p['physical'] <= end_page]
    lefts = [a for a in anchors if a['chapter'] < chapter]
    rights = [a for a in anchors if a['chapter'] > chapter]
    if not lefts or not rights:
        return []
    left, right = lefts[-1], rights[0]
    if right['chapter'] - left['chapter'] > MAX_MISSING_CHAPTER_GAP or right['physical'] - left['physical'] > MAX_MISSING_PHYSICAL_GAP:
        return []
    return [dict(file=p['file'], physical=p['physical'], score=.54,
                 evidence=[dict(kind='bounded_missing_chapter', book=book, chapter=chapter, left=left, right=right)],
                 uncertainties=['chapter_marker_missing_or_unrecognized', 'bounded_page_interval'])
            for p in mapping['pages'] if left['physical'] <= p['physical'] <= right['physical']]


def _locators(entry):
    if entry.get('volume') != 'PL005' or entry.get('reference_namespace') != 'book_chapter':
        return []
    return entry.get('locators', [])


def locate(entry: dict, mapping: dict) -> list[dict]:
    out = []
    for atom in _locators(entry):
        book, chapter = atom.get('roman_value'), atom.get('arabic_value')
        if not isinstance(book, int) or not isinstance(chapter, int):
            continue
        for candidate in _pages_for(book, chapter, mapping):
            candidate.update(volume='PL005', namespace='book_chapter', method=VERSION)
            candidate['evidence'].append(dict(kind='index_book_chapter', locator=atom,
                                               source=entry.get('source'), span=entry.get('reference_span'),
                                               notice=entry.get('reviewed_source_section', {}).get('notice_evidence', [])))
            if atom.get('qualifier_raw'):
                candidate['uncertainties'].append('following_or_repeated_extent_not_expanded')
            out.append(candidate)
    return out


def diagnose(entry: dict, mapping: dict) -> dict:
    atoms = _locators(entry)
    missing, methods = [], Counter()
    for atom in atoms:
        book, chapter = atom.get('roman_value'), atom.get('arabic_value')
        candidates = _pages_for(book, chapter, mapping) if isinstance(book, int) and isinstance(chapter, int) else []
        if candidates:
            methods[candidates[0]['evidence'][0]['kind']] += 1
        else:
            missing.append(dict(locator=atom, reason='missing_inherited_context' if book is None or chapter is None else 'chapter_outside_anchored_book_sequence'))
    return dict(locator_count=len(atoms), located_locator_count=len(atoms) - len(missing),
                unmatched_locators=missing, methods=dict(methods),
                completeness='all_known_locators_have_candidates' if atoms and not missing else 'partial_or_pending',
                qualified_extent_unexpanded=any(a.get('qualifier_raw') for a in atoms), certified=False)

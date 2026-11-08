"""Bounded lexical context after a letter opening; never an inferred destination."""
from collections import defaultdict
import hashlib
from pathlib import Path
import re

from tools.indexing.epistle_destination_map import (
    NUMBER, STOP, _blocks, _body_blocks, _context, _plain,
)

VERSION = 'epistle-continuation-v1'


def continuation_context(anchors, root):
    """Return a copy with at most the immediately adjacent page's leading text.

    Stop at even rejected letter numerals and section titles. Missing or conflicting
    running heads abstain: this optional context must not turn a whole-page match
    into apparent support for a different letter.
    """
    inventories, pages = {}, {}
    positions = defaultdict(set)
    for anchor in anchors:
        for span in anchor.get('spans', []):
            if span['kind'] == 'epistle_number':
                positions[anchor['file']].add(span['start'])

    def read(path):
        if path not in pages:
            raw = path.read_text(encoding='utf-8')
            pages[path] = raw, _blocks(raw)
        return pages[path]

    result = []
    for source in anchors:
        anchor = dict(source)
        result.append(anchor)
        volume = anchor.get('volume')
        number_spans = [s for s in anchor.get('spans', []) if s['kind'] == 'epistle_number']
        text_spans = [s for s in anchor.get('spans', []) if s['kind'] == 'opening_text']
        if not volume or not number_spans or not text_spans:
            continue
        start = number_spans[0]['start']
        if any(p > start for p in positions[anchor['file']]):
            continue
        if volume not in inventories:
            files = defaultdict(list)
            for path in (Path(root)/'teste'/volume/'text').glob('*.txt'):
                match = re.search(r'-(\d+)\.txt$', path.name)
                if match:
                    files[int(match[1])].append(path.resolve())
            inventories[volume] = files
        following = inventories[volume].get(anchor['physical'] + 1, [])
        if len(following) != 1:
            continue
        current = Path(anchor['file'])
        if not current.is_absolute():
            current = Path(root)/current
        try:
            raw, blocks = read(current)
            next_raw, next_blocks = read(following[0])
        except (OSError, UnicodeError):
            continue
        body = _body_blocks(blocks)
        # The recorded opening must actually reach the last body character.
        if not body or max(s['end'] for s in text_spans) < body[-1]['end']:
            continue
        interrupted = False
        for block in body:
            for match in NUMBER.finditer(block['text']):
                if block['start'] + match.start('number') > start:
                    interrupted = True
        if interrupted:
            continue
        header = ' '.join(b['text'] for b in next_blocks if b['kind'] == 'cabecalho')
        context = _context(header)
        if not context or (context['series'], context['book']) != (anchor['series'], anchor['book']):
            continue
        spans = []
        for block in _body_blocks(next_blocks):
            plain = _plain(block['text'])
            if block['kind'] in {'titulo', 'subtitulo'} or (
                len(plain) < 450 and (_context(plain) is not None or STOP.search(plain))
            ):
                break
            match = NUMBER.search(block['text'])
            end = block['start'] + match.start() if match else block['end']
            if end > block['start']:
                spans.append({'start': block['start'], 'end': end,
                              'raw': next_raw[block['start']:end]})
            if match:
                break
        text = _plain('\n'.join(s['raw'] for s in spans))
        if text:
            anchor['continuation_context'] = {
                'file': str(following[0]), 'physical': anchor['physical'] + 1,
                'source_sha256': hashlib.sha256(next_raw.encode()).hexdigest(),
                'spans': spans, 'text': text, 'version': VERSION,
                'scope': 'adjacent_page_prefix_before_next_number_or_section',
                'uncertainties': ['continuity_candidate_not_certified', 'single_page_context_limit'],
            }
    return result

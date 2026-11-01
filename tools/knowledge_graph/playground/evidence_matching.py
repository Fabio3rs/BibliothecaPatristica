"""Locate quotations in one supplied source, without rewriting either text.

Similarity is lexical, not semantic entailment. Bounded normalized Levenshtein
alignment records differences; changed numbers/negations still require review.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from rapidfuzz.distance import Levenshtein

MATCHING_POLICY = 'local-quote-v2'
MAX_FUZZY_TOKENS = 160
MAX_FUZZY_CHARS = 4096
MAX_CANDIDATES = 512
MIN_SCORE = .90
AMBIGUITY_MARGIN = .03
PROTECTED = {'non', 'ne', 'nec', 'neque', 'haud', 'nemo', 'nihil', 'nullus',
             'nao', 'nem', 'sem', 'not', 'no', 'never', 'without',
             'ου', 'ουκ', 'ουχ', 'ουδε', 'ουτε', 'μη', 'μηδε', 'μητε'}
OPERATORS = {'<', '>', '=', '+', '-', '−', '/', '.', ','}
LINE_HYPHEN = re.compile(r'(?<=\w)[\-\u2010]\s*\n\s*(?=\w)')
TOKEN = re.compile(r'[^\W_]+|[^\w\s]', re.UNICODE)


def tokens(text):
    """Normalize with offsets into the ORIGINAL Python Unicode string."""
    omitted = {i for m in LINE_HYPHEN.finditer(text) for i in range(*m.span())}
    normalized, spans = [], []
    for i, char in enumerate(text):
        if i in omitted or char == '\u00ad':
            continue
        folded = unicodedata.normalize('NFKD', char.casefold()).replace('œ', 'oe').replace('æ', 'ae')
        visible = ''.join(c for c in folded if not unicodedata.category(c).startswith('M'))
        if not visible and spans:
            spans[-1] = (spans[-1][0], i + 1)
        for c in visible:
            normalized.append(c)
            spans.append((i, i + 1))
    normalized = ''.join(normalized)
    out = []
    for m in TOKEN.finditer(normalized):
        word = m.group()
        # Retain operators and numeric punctuation; ignore ordinary typography.
        punctuation = unicodedata.category(word[0]).startswith('P')
        numeric_separator = (m.start() > 0 and m.end() < len(normalized)
                             and normalized[m.start()-1].isdigit() and normalized[m.end()].isdigit())
        if punctuation and not numeric_separator and word not in {'<', '>', '=', '+', '-', '−', '/'}:
            continue
        out.append((word, spans[m.start()][0], spans[m.end()-1][1]))
    return out


def _protected(word):
    return word in PROTECTED or word in OPERATORS or any(c.isdigit() for c in word)


def _alignment_guard(wanted, window):
    """Reject protected edits and large substitutions despite high overall scores."""
    changes = []
    for op in Levenshtein.opcodes(wanted, window):
        if op.tag == 'equal':
            continue
        if changes and changes[-1][1] == op.src_start and changes[-1][3] == op.dest_start:
            changes[-1][1], changes[-1][3] = op.src_end, op.dest_end
        else:
            changes.append([op.src_start, op.src_end, op.dest_start, op.dest_end])
    changed = 0
    for a, b, c, d in changes:
        left, right = wanted[a:b], window[c:d]
        if any(_protected(word) for word in left + right):
            return 'protected_tokens'
        changed += max(len(left), len(right))
    if changed > max(3, len(wanted) // 5):
        return 'too_many_tokens'
    for a, b, c, d in changes:
        left, right = wanted[a:b], window[c:d]
        if not left or not right:
            if any(len(word) > 3 for word in left + right):
                return 'whole_word_change'
        elif len(left) == len(right):
            for a, b in zip(left, right):
                distance = Levenshtein.distance(a, b, score_cutoff=3)
                if (min(len(a), len(b)) < 3 or max(len(a), len(b)) > 128
                        or distance > 3 or distance / max(len(a), len(b)) > .4):
                    return 'whole_word_change'
        elif ''.join(left) != ''.join(right):
            return 'whole_word_change'
    return None


def _distinct_matches(matches):
    """Nearby boundary variants belong to one occurrence, not separate matches."""
    distinct = []
    for match in sorted(matches, key=lambda m: (-m['score'], m['start'], m['end'])):
        if any(min(match['end'], other['end']) - max(match['start'], other['start'])
               >= .8 * min(match['end'] - match['start'], other['end'] - other['start'])
               and abs(match['start'] - other['start']) <= 4
               and abs(match['end'] - other['end']) <= 4 for other in distinct):
            continue
        distinct.append(match)
    return distinct


def _edit_details(wanted, window, distance):
    return {'score_metric': '1 - levenshtein_distance / max_normalized_length',
            'edit_distance': distance, 'normalized_quote': wanted, 'normalized_source': window,
            'diff_direction': 'model_quote_to_source',
            'diff_offset_unit': 'normalized_unicode_codepoints',
            'diff': [{'operation': op.tag,
                      'quote_start': op.src_start, 'quote_end': op.src_end,
                      'source_start': op.dest_start, 'source_end': op.dest_end,
                      'quote_text': wanted[op.src_start:op.src_end],
                      'source_text': window[op.dest_start:op.dest_end]}
                     for op in Levenshtein.opcodes(wanted, window) if op.tag != 'equal']}


def locate_quote(quote, text):
    base = {'accepted': False, 'method': 'not_found', 'score': None,
            'start': None, 'end': None, 'offset_unit': 'unicode_codepoints',
            'matched_quote': None, 'candidate_count': 0}
    if text is None:
        return dict(base, method='unknown_locator', reason='Source locator does not exist')
    if not quote or not quote.strip():
        return dict(base, reason='Empty quotation')
    index = text.find(quote)
    if index >= 0:
        return dict(base, accepted=True, method='exact', score=1., start=index,
                    end=index+len(quote), matched_quote=quote, candidate_count=text.count(quote))
    source, query = tokens(text), tokens(quote)
    words, wanted = [t[0] for t in source], [t[0] for t in query]
    if not wanted or sum(len(w) for w in wanted) < 3:
        return dict(base, reason='Quotation too short for approximate matching')
    positions = defaultdict(list)
    for i, word in enumerate(words):
        positions[word].append(i)
    # A normalized match must contain the full sequence, on token boundaries.
    normalized = [i for i in positions[wanted[0]] if words[i:i+len(wanted)] == wanted]

    def found(start, end, method, score):
        a, b = source[start][1], source[end-1][2]
        return dict(base, accepted=True, method=method, score=round(score, 6),
                    start=a, end=b, matched_quote=text[a:b], candidate_count=1)

    if len(normalized) == 1:
        return found(normalized[0], normalized[0] + len(wanted), 'normalized', 1.)
    if len(normalized) > 1:
        return dict(base, method='ambiguous', candidate_count=len(normalized),
                    reason='Multiple normalized occurrences in the referenced source')
    query_text = ' '.join(wanted)
    if (not 3 <= len(wanted) <= MAX_FUZZY_TOKENS or sum(map(len, wanted)) < 20
            or len(query_text) > MAX_FUZZY_CHARS):
        return dict(base, reason='Insufficient context or quotation exceeds fuzzy-search bound')
    slack = min(4, max(2, len(wanted) // 20 + 1))
    anchors = sorted(((offset, word) for offset, word in enumerate(wanted)
                      if positions[word] and not _protected(word)),
                     key=lambda pair: (len(positions[pair[1]]), -len(pair[1]), pair[0]))[:8]
    candidates = set()
    for offset, word in anchors:
        for position in positions[word]:
            for shift in range(-slack, slack + 1):
                start = position-offset+shift
                if 0 <= start < len(words):
                    candidates.add(start)
                    if len(candidates) > MAX_CANDIDATES:
                        return dict(base, reason='Too many candidate locations; use a more specific quote')
    matches, protected = [], []
    for start in sorted(candidates):
        for size in range(max(3, len(wanted)-slack), len(wanted)+slack+1):
            end = start + size
            if end > len(words):
                break
            window = words[start:end]
            window_text = ' '.join(window)
            length = max(len(query_text), len(window_text))
            cutoff = int(length * (1 - MIN_SCORE) + 1e-9)
            distance = Levenshtein.distance(query_text, window_text, score_cutoff=cutoff)
            if distance > cutoff:
                continue
            guard = _alignment_guard(wanted, window)
            if guard not in (None, 'protected_tokens'):
                continue
            match = {'start': start, 'end': end, 'score': 1 - distance / length,
                     'distance': distance, 'window_text': window_text}
            (protected if guard else matches).append(match)
    matches = _distinct_matches(matches)

    def detailed(match):
        return dict(found(match['start'], match['end'], 'fuzzy', match['score']),
                    **_edit_details(query_text, match['window_text'], match['distance']))

    if not matches:
        protected = _distinct_matches(protected)
        if protected:
            candidate = dict(detailed(protected[0]), accepted=False, candidate_count=len(protected))
            return dict(base, review_candidate=candidate,
                        reason='Similar passage changes numbers, operators or negation; manual review required')
        return dict(base, reason='No sufficiently similar contiguous passage in the referenced source')
    if len(matches) > 1 and matches[0]['score']-matches[1]['score'] < AMBIGUITY_MARGIN:
        return dict(base, method='ambiguous', candidate_count=len(matches),
                    reason='Competing passages have similar scores; manual review required')
    return detailed(matches[0])

"""Unique OCR anchoring with exact, normalized and conservative fuzzy matches.

Matching never changes reference numbers. All offsets and IDs come from Python.
"""
from difflib import SequenceMatcher
from bisect import bisect_left
from pathlib import Path
import re
import unicodedata

from tools.indexing.index_payload_evidence import normalize_evidence_text


def literal_pattern(query):
    """Match whole alphanumeric edges, so a locator 8 cannot match inside 18."""
    return (r'(?<!\w)' if query[:1].isalnum() else '') + re.escape(query) + (r'(?!\w)' if query[-1:].isalnum() else '')


def numbers(text):
    text = unicodedata.normalize('NFKC', text)
    return re.findall(r'\d+|\b[IVXLCDM]+\b', text)


def normalized_with_offsets(text, offsets):
    omitted = {i for m in re.finditer(r'(?<=\w)[-\u00ad]\s*\n\s*(?=\w)', text) for i in range(m.start(), m.end())}
    chars, mapping = [], []
    for i, char in enumerate(text):
        if i in omitted or unicodedata.combining(char):
            continue
        folded = normalize_evidence_text(char) or ' '
        for c in folded:
            if c == ' ' and (not chars or chars[-1] == ' '):
                continue
            chars.append(c)
            mapping.append(offsets[i])
    if chars and chars[-1] == ' ':
        chars.pop()
        mapping.pop()
    return ''.join(chars), mapping


class Aligner:
    def __init__(self, page):
        self.page = page
        self.raw = Path(page['ocr_file']).read_bytes().decode('utf-8')
        text, offsets = [], []
        for line in page['lines']:
            a, b = line['char_start'], line['char_end']
            text.extend(self.raw[a:b] + '\n')
            offsets.extend([*range(a, b), b])
        self.visible, self.visible_offsets = ''.join(text), offsets
        self.normal, self.offsets = normalized_with_offsets(self.visible, offsets)

    def visible_span(self, start, end):
        return self.visible[bisect_left(self.visible_offsets, start):bisect_left(self.visible_offsets, end)]

    def numeric_spans(self, start, end):
        left, right = bisect_left(self.visible_offsets, start), bisect_left(self.visible_offsets, end)
        for match in re.finditer(r'\d+', self.visible[left:right]):
            yield (match.group(), self.visible_offsets[left + match.start()],
                   self.visible_offsets[left + match.end() - 1] + 1)

    def partition_match(self, parent, start, end):
        left = bisect_left(self.visible_offsets, parent['char_start'])
        text = self.visible_span(parent['char_start'], parent['char_end'])
        if not 0 <= start < end <= len(text):
            raise ValueError('Partition lies outside anchored reference')
        return {'status': 'matched', 'method': 'literal_partition',
                'char_start': self.visible_offsets[left + start],
                'char_end': self.visible_offsets[left + end - 1] + 1,
                'numeric_tokens_preserved': True}

    def match(self, query, within=None, fuzzy=True):
        if not query or not query.strip():
            return {'status': 'not_found', 'reason': 'empty_query'}
        def allowed(a, b):
            return within is None or any(left <= a and b <= right for left, right in within)
        pattern = literal_pattern(query)
        exact = [(m.start(), m.end()) for m in re.finditer(pattern, self.raw) if allowed(m.start(), m.end())]
        # Restrict matches to visible OCR spans, excluding XML attributes/tags.
        exact = [(a, b) for a, b in exact if any(l['char_start'] <= a < l['char_end'] for l in self.page['lines'])]
        if exact:
            return self._choose(exact, 'exact')
        q, _ = normalized_with_offsets(query, list(range(len(query))))
        if not q:
            return {'status': 'not_found', 'reason': 'empty_normalized_query'}
        matches = []
        for m in re.finditer(r'(?<!\w)' + re.escape(q) + r'(?!\w)', self.normal):
            a, b = self.offsets[m.start()], self.offsets[m.end()-1]+1
            if allowed(a, b) and numbers(query) == numbers(self.visible_span(a, b)):
                matches.append((a, b))
        if matches:
            return self._choose(matches, 'normalized')
        if not fuzzy or len(q) < 12 or sum(c.isalpha() for c in q) < 6:
            return {'status': 'not_found', 'reason': 'no_unique_literal_or_normalized_match'}
        words = list(re.finditer(r'\S+', self.normal))
        size = len(q.split())
        candidates = []
        for i in range(len(words)):
            for count in range(max(1, size-1), size+2):
                if i+count > len(words):
                    continue
                start, end = words[i].start(), words[i+count-1].end()
                a, b = self.offsets[start], self.offsets[end-1]+1
                if not allowed(a, b):
                    continue
                candidate = self.normal[start:end]
                if abs(len(candidate)-len(q)) > max(3, .12*len(q)):
                    continue
                if numbers(query) != numbers(self.visible_span(a, b)):
                    continue
                score = SequenceMatcher(None, q, candidate, autojunk=False).ratio()
                if score >= .86:
                    candidates.append((score, a, b))
        candidates.sort(reverse=True)
        if not candidates:
            return {'status': 'not_found', 'reason': 'no_numeric_consistent_fuzzy_candidate'}
        score, a, b = candidates[0]
        # Overlapping windows of the same occurrence are not independent alternatives.
        alternative = max((s for s, x, y in candidates[1:] if min(b,y)-max(a,x) <= 0), default=0)
        result = {'status': 'matched' if score >= .94 and score-alternative >= .08 else 'ambiguous',
                  'method': 'fuzzy', 'score': score, 'runner_up_score': alternative,
                  'char_start': a, 'char_end': b, 'numeric_tokens_preserved': True}
        return result

    def _choose(self, matches, method):
        matches = sorted(set(matches))
        if len(matches) != 1:
            return {'status': 'ambiguous', 'method': method, 'candidate_count': len(matches), 'candidates': matches[:10]}
        a, b = matches[0]
        return {'status': 'matched', 'method': method, 'score': 1, 'char_start': a, 'char_end': b,
                'numeric_tokens_preserved': True}

    def evidence(self, match, reading=None, corrected_fields=()):
        if match['status'] != 'matched':
            return []
        a, b = match['char_start'], match['char_end']
        groups = {}
        for line in self.page['lines']:
            if line['char_start'] < b and a < line['char_end']:
                groups.setdefault(line['block_id'], []).append(line)
        full = next((r for r in self.page['regions'] if r['transformation'] == 'none'), None)
        visual = reading if corrected_fields and full else None
        result = []
        for block, lines in groups.items():
            start, end = max(a, lines[0]['char_start']), min(b, lines[-1]['char_end'])
            result.append({'page_id': self.page['page_id'], 'block_id': block,
                'line_ids': [l['id'] for l in lines], 'quote': self.raw[start:end],
                'ocr_char_start': start, 'ocr_char_end': end,
                'visual_region_id': full['id'] if visual else None, 'visual_reading': visual,
                'corrected_fields': list(corrected_fields) if visual else [], 'uncertain': match['method'] == 'fuzzy'})
        return result

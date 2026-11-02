"""Small OCR differences in an already located span; v2/v3 remain untouched.

Acceptance locates a quotation, not its meaning. Never rewrite source or quote,
guess a continuation, ignore a number, or resolve repeated passages arbitrarily.
"""
from __future__ import annotations

import re
import unicodedata as ud
from copy import deepcopy

from rapidfuzz.distance import Levenshtein

from . import evidence_locator as v3
from . import evidence_matching as legacy

POLICY = 'local-quote-v4'
VERSION = 'kg-validation-8'
LATIN_TO_GREEK = str.maketrans({'A': 'Α', 'B': 'Β', 'E': 'Ε', 'H': 'Η', 'I': 'Ι',
                               'K': 'Κ', 'M': 'Μ', 'N': 'Ν', 'O': 'Ο', 'P': 'Ρ',
                               'T': 'Τ', 'X': 'Χ', 'Y': 'Υ', 'o': 'ο', 'p': 'ρ'})
NUMBER_WORDS = set(('unus una unum duo duae tres tria quattuor quatuor quinque sex septem '
                    'octo novem decem undecim duodecim viginti triginta quadraginta quinquaginta '
                    'sexaginta septuaginta octoginta nonaginta centum ducenti mille '
                    'εις μια εν δυο τρεις τρια τεσσαρες πεντε εξ επτα οκτω εννεα δεκα '
                    'δωδεκα εικοσι τριακοντα εκατον χιλιοι').split())
SENSITIVE_STEMS = ('ομοουσι', 'ομοιουσι', 'αγεν', 'γεννη', 'γενη', 'ουσι', 'υποστασ')


def _greek_visual(word):
    """Only Latin lookalikes embedded inside an otherwise Greek token."""
    decomposed = ud.normalize('NFD', word)
    scripts = v3._scripts(decomposed)
    if scripts != {'LATIN', 'GREEK'}:
        return word
    latin = [c for c in decomposed if 'LATIN' in ud.name(c, '') and v3._letter(c)]
    if len(latin) != 1 or ord(latin[0]) not in LATIN_TO_GREEK:
        return word
    return ud.normalize('NFC', decomposed.translate(LATIN_TO_GREEK))


def _no_marks(word):
    return ''.join(c for c in ud.normalize('NFD', word)
                   if not ud.category(c).startswith('M')).casefold()


def _boundary_fragment(quote, text, candidate):
    end = candidate['end']
    return (candidate['method'] in {'exact', 'normalized'}
            and candidate.get('guard') in {'truncated_word', 'truncated_word_or_operator'}
            and bool(re.search(r'[^\W\d_][-‐]\s*$', quote))
            and bool(re.search(r'[^\W\d_][-‐]\s*$', text[candidate['start']:end]))
            and not text[end:].strip()
            and v3._boundaries(text, candidate['start'], end, quote)
            and len(v3.tokens(quote)) >= 4 and len(quote.strip()) >= 20)


def _micro_rules(quote, text, candidate, names):
    start, end = candidate['start'], candidate['end']
    matched = text[start:end]
    qt, st = v3.tokens(quote), v3.tokens(matched)
    if (len(qt) != len(st) or len(qt) < 4 or len(quote) < 30
            or not v3._boundaries(text, start, end, quote)
            or re.search(r'[-‐]\s*$', quote)):
        return None
    protected_names = {t[0] for name in names for t in v3.tokens(name)}
    rules, distance, length = [], 0, max(len(' '.join(t[0] for t in qt)), len(' '.join(t[0] for t in st)))
    for q, s in zip(qt, st):
        qr, sr = quote[q[1]:q[2]], matched[s[1]:s[2]]
        qv, sv = _greek_visual(qr), _greek_visual(sr)
        a, b = v3._fold(qv), v3._fold(sv)
        protected = legacy._protected(a) or legacy._protected(b) or a in NUMBER_WORDS or b in NUMBER_WORDS
        if protected:
            if a != b:
                return None
            # In particular, οὐ and οὗ are never equated by stripping breathings.
            if 'GREEK' in v3._scripts(qv + sv) and ud.normalize('NFC', qv).casefold() != ud.normalize('NFC', sv).casefold():
                return None
        if q[0] == s[0]:
            continue
        distance += Levenshtein.distance(q[0], s[0])
        if a == b and (qv != qr or sv != sr):
            rules.append('embedded_greek_visual_confusable')
            continue
        if (not protected and 'GREEK' in v3._scripts(qv + sv)
                and _no_marks(qv) == _no_marks(sv)):
            rules.append('greek_combining_mark_or_iota_subscript')
            continue
        if (protected or a in protected_names or b in protected_names
                or qr[0].isupper() or sr[0].isupper()
                or a.startswith(SENSITIVE_STEMS) or b.startswith(SENSITIVE_STEMS)
                or v3._scripts(a) != v3._scripts(b) or len(v3._scripts(a)) != 1
                or not all(v3._letter(c) for c in a+b)
                or min(len(a), len(b)) < 6 or Levenshtein.distance(a, b) != 1):
            return None
        # Preserve inflectional endings. Only a single internal/initial glyph is
        # tolerated in a long ordinary word surrounded by matching context.
        ops = [o for o in Levenshtein.opcodes(a, b) if o.tag != 'equal']
        if any(o.src_end > len(a)-2 or o.dest_end > len(b)-2 for o in ops):
            return None
        rules.append('single_glyph_in_long_word')
    if not rules or distance > 3 or distance / max(1, length) > .03:
        return None
    return sorted(set(rules))


def locate_quote(quote, text, *, definitions=None, names=(), previous=None):
    old = previous if previous is not None else v3.locate_quote(quote, text, definitions=definitions, names=names)
    result = deepcopy(old)
    result['policy'] = POLICY
    if old.get('accepted') and old['method'] != 'exact':
        qw = [t[0] for t in v3.tokens(quote)]
        sw = [t[0] for t in v3.tokens(old['matched_quote'])]
        for op in Levenshtein.opcodes(qw, sw):
            if op.tag != 'equal' and any(w in NUMBER_WORDS for w in qw[op.src_start:op.src_end]+sw[op.dest_start:op.dest_end]):
                candidate = {k: deepcopy(old[k]) for k in ('start', 'end', 'matched_quote', 'method', 'score',
                                                          'edit_operations', 'ignored_note_markers')}
                candidate['guard'] = 'written_number_changed'
                result.update(accepted=False, position_resolved=False, status='review', method='review_only',
                              start=None, end=None, matched_quote=None, score=None,
                              reason='written_number_changed', diagnostics=['written_number_changed'],
                              review_candidate=candidate, candidates=[candidate])
                return result
    if old.get('accepted') or old.get('method') != 'review_only' or text is None:
        return result
    candidate = old.get('review_candidate')
    if not candidate:
        return result
    fragment = _boundary_fragment(quote, text, candidate)
    rules = ['source_boundary_fragment'] if fragment else _micro_rules(quote, text, candidate, names)
    if not rules:
        return result
    result.update({k: v for k, v in candidate.items() if k != 'guard'})
    result.update(accepted=True, located=True, position_resolved=True, status='accepted',
                  method=candidate['method'] if fragment else 'fuzzy', candidate_count=1,
                  acceptance_rule='source_boundary_fragment' if fragment else 'ocr_micro_difference',
                  diagnostics=rules, previous_guard=old.get('reason'), semantic_support='not_evaluated',
                  edit_operations=v3._raw_diff(quote, text[candidate['start']:candidate['end']], candidate['start']))
    result.pop('reason', None)
    result.pop('review_candidate', None)
    result['candidates'] = []
    return result

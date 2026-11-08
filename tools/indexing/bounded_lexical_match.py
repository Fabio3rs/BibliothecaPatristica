"""Lexical clues inside an externally bounded page range, never semantic verdicts.

The caller owns the editorial range and physical-page identity. This module only
compares literal readings, preserving raw character offsets and uncertainty.
"""
import re
from functools import lru_cache

from scripts.audit_po015_names_lexical import normalize, tokens, script_of, edit_distance
from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'bounded-lexical-v1'
GENERIC = frozenset(normalize(word) for word in (
    'a ab ad ac atque aut cum de e et ex in inter nec non per pro quod qui quae '
    'quid quo sed si sine sub super ut vel est sunt esse fuit eius ejus hoc haec '
    'hujus huius idem ibid ibidem vide etiam cap caput liber libro tom tomus '
    'pag pagina col columna num numerus index rerum verba verbum deus dei '
    'του των τη την τον το τα και εν εις εκ ο η ος ως δε μεν εστι'
).split())
TAG = re.compile(r'<[^>]*>|\[/?(?:br|span)\b[^\]]*\]', re.I)
MARKUP = re.compile(r'</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>')


@lru_cache(maxsize=32768)
def _similarity(a, b, threshold):
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 2 or script_of(a) != script_of(b):
        return None
    distance = edit_distance(a, b)
    score = 1 - distance / max(len(a), len(b))
    return score if distance <= 2 and score >= threshold else None


def _read_blocks(raw):
    recognized = blocks(raw)
    if recognized:
        return [b for b in recognized if b['kind'] not in {'cabecalho', 'outro', 'unknown'}], []
    if MARKUP.search(raw):
        return [], ['unsupported_markup']
    return [{'body': raw, 'body_start': 0, 'kind': 'plain_text'}], []


@lru_cache(maxsize=128)
def _prepared_blocks(raw):
    source_blocks, flags = _read_blocks(raw)
    prepared = []
    for block in source_blocks:
        # Mask tags rather than deleting them so every token keeps its raw span.
        clean = TAG.sub(lambda m: ' ' * len(m[0]), block['body'])
        prepared.append((block, list(tokens(clean))))
    return prepared, flags


def _evidence(raw, block, window, method, score, coverage, scope):
    start = block['body_start'] + window[0]['start']
    end = block['body_start'] + window[-1]['end']
    return {'method': method, 'score': score, 'coverage': coverage, 'scope': scope,
            'start': start, 'end': end, 'char_start': start, 'char_end': end,
            'text_raw': raw[start:end], 'block_kind': block['kind'],
            'snippet': raw[max(block['body_start'], start - 80):min(
                block['body_start'] + len(block['body']), end + 80)]}


def match_text(query, raw, *, fuzzy_threshold=0.8, max_matches=20):
    """Return exact/normalized/fuzzy/partial/absent evidence for a page reading.

    Fuzzy comparisons permit at most two edits, on words of at least five
    letters, within the same script. A partial clause needs two distinct,
    nongeneric query words within a 32-token window; its coverage is explicit.
    Scores measure string similarity, not relevance or probability of OCR error.
    Headers and XML metadata are excluded. Spans always index ``raw`` itself.
    """
    if not 0 < fuzzy_threshold <= 1 or max_matches < 1:
        raise ValueError('Require 0 < fuzzy_threshold <= 1 and max_matches >= 1')
    query = query.strip()
    qs = list(tokens(query))
    informative = sorted({q['norm'] for q in qs if len(q['norm']) >= 4 and q['norm'] not in GENERIC})
    source_blocks, source_flags = _prepared_blocks(raw)
    flags = list(source_flags)
    if not qs:
        flags.append('no_letter_tokens')
    elif not informative:
        flags.append('short_or_generic_query_weak_evidence')
    if len(qs) == 1 and len(qs[0]['norm']) < 5:
        flags.append('short_query_no_fuzzy_expansion')
    found, partial = [], []
    for block, document in source_blocks:
        if not qs:
            continue
        for i in range(len(document) - len(qs) + 1):
            window = document[i:i + len(qs)]
            scores = [_similarity(q['norm'], t['norm'], fuzzy_threshold) for q, t in zip(qs, window)]
            if any(score is None for score in scores):
                continue
            literal = block['body'][window[0]['start']:window[-1]['end']]
            method = 'exact' if literal == query else 'normalized' if min(scores) == 1 else 'fuzzy'
            found.append(_evidence(raw, block, window, method, min(scores), 1.0, 'full_label'))
        if len(informative) < 2:
            continue
        hits = []
        for position, token in enumerate(document):
            possible = [(score, term) for term in informative
                        if (score := _similarity(term, token['norm'], fuzzy_threshold)) is not None]
            if possible:
                score, term = max(possible, key=lambda v: (v[0], v[1]))
                hits.append((position, term, score))
        for first, (position, _, _) in enumerate(hits):
            nearby = [hit for hit in hits[first:] if hit[0] - position < 32]
            terms = {hit[1] for hit in nearby}
            if len(terms) < 2:
                continue
            window = document[position:nearby[-1][0] + 1]
            coverage = len(terms) / len(informative)
            evidence = _evidence(raw, block, window, 'partial', min(hit[2] for hit in nearby),
                                 coverage, 'informative_terms_only')
            evidence['matched_terms'] = sorted(terms)
            evidence['token_methods'] = ['normalized' if hit[2] == 1 else 'fuzzy' for hit in nearby]
            partial.append(evidence)
    priority = {'exact': 0, 'normalized': 1, 'fuzzy': 2, 'partial': 3}
    found.sort(key=lambda h: (priority[h['method']], -h['score'], h['start']))
    partial.sort(key=lambda h: (-h['coverage'], -h['score'], h['end'] - h['start'], h['start']))
    selected = found if found else partial
    if not found and partial:
        flags.append('partial_label_match')
    if any(h['method'] == 'fuzzy' or 'fuzzy' in h.get('token_methods', []) for h in selected):
        flags.append('similar_spelling_not_proof_of_ocr_error')
    if len(selected) > 1:
        flags.append('multiple_lexical_occurrences' if found else 'overlapping_partial_windows_possible')
    return {'version': VERSION, 'query': query, 'method': selected[0]['method'] if selected else 'absent',
            'query_scope': selected[0]['scope'] if selected else 'full_label',
            'coverage': selected[0]['coverage'] if selected else 0.0,
            'informative_terms': informative, 'match_count': len(selected),
            'matches': selected[:max_matches], 'matches_truncated': len(selected) > max_matches,
            'flags': sorted(set(flags)), 'certified': False, 'ocr_error_confirmed': False,
            'score_kind': 'string_similarity_not_probability'}

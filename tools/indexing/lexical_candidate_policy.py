"""Explain and rank lexical clues without requiring the query's word order."""
import json
from pathlib import Path

from tools.indexing.bounded_lexical_match import _similarity, normalize, tokens

DEFAULT_POLICY = Path(__file__).resolve().parents[2]/'docs/fixtures/index_lexical_stopwords.json'


def load_policy(path=DEFAULT_POLICY):
    raw = json.loads(Path(path).read_text())
    low = {normalize(w) for kind in ('latin', 'greek', 'editorial') for w in raw[kind]}
    negations = {normalize(w) for w in raw['protected_negations']}
    if low & negations or not 0 <= raw['low_weight'] < 1:
        raise ValueError('Negations must remain protected; low_weight must be in [0,1)')
    return raw, low, negations


def describe(query, passage, policy):
    raw, low, negations = policy
    query_tokens = list(tokens(query))
    document = list(tokens(passage))
    words = list(dict.fromkeys(t['norm'] for t in query_tokens))
    # Distinct query terms cannot claim the same occurrence in the document.
    adjacency = {word: sorted(
        [(i, score) for i,t in enumerate(document) if (score := _similarity(word,t['norm'],.8)) is not None],
        key=lambda x: (-x[1],x[0])) for word in words}
    assigned = {}

    def visit(word, seen):
        for i, score in adjacency[word]:
            if i in seen:
                continue
            seen.add(i)
            if i not in assigned or visit(assigned[i], seen):
                assigned[i] = word
                return True
        return False

    content = [w for w in words if w not in low and w not in negations]
    # Stopwords cannot steal an occurrence from an informative term.
    for word in words:
        if word not in low:
            visit(word, set())
    for word in words:
        if word in low:
            free = [(i,s) for i,s in adjacency[word] if i not in assigned]
            if free:
                assigned[free[0][0]] = word
    positions = {word:i for i,word in assigned.items()}
    weight = lambda w: raw['low_weight'] if w in low else 1.0
    total = sum(weight(w) for w in words)
    score = sum(weight(w)*_similarity(w,document[i]['norm'],.8) for w,i in positions.items())
    ordered_positions = [positions[w] for w in content if w in positions]
    query_negations = set(words) & negations
    passage_negations = {t['norm'] for t in document} & negations
    return dict(policy=raw['version'], low_weight_terms=[w for w in words if w in low],
        informative_terms=content, matched_informative_terms=[w for w in content if w in positions],
        informative_coverage=sum(w in positions for w in content)/len(content) if content else 0,
        weighted_coverage=score/total if total else 0,
        complete_informative_terms=len(content)>=2 and all(w in positions for w in content),
        informative_order_changed=ordered_positions != sorted(ordered_positions),
        query_negations_missing=sorted(query_negations - passage_negations),
        passage_negations_extra=sorted(passage_negations - query_negations),
        meaning='lexical_clue_not_semantic_equivalence')


def annotate(candidate, policy):
    reading = candidate['best_reading']
    hits = reading['lexical']['matches']
    if not hits:
        candidate['lexical_policy'] = dict(weighted_coverage=0, informative_coverage=0,
                                           complete_informative_terms=False, no_lexical_hit=True)
        return
    query = reading['query_variant']['text']
    passage = hits[0].get('matched_joined_text',hits[0]['text_raw'])
    if reading['lexical'].get('normalization') == 'possible_soft_wrap_join':
        query = reading['lexical']['query']
    candidate['lexical_policy'] = describe(query,passage,policy)


def ranking_key(candidate):
    policy = candidate['lexical_policy']
    priority = {'exact':0,'normalized':1,'fuzzy':2,'partial':3,'absent':4}
    hit = candidate['best_reading']['lexical']['matches']
    width = hit[0]['end']-hit[0]['start'] if hit else 0
    return (priority[candidate['method']], -policy['informative_coverage'],
            -policy['weighted_coverage'], bool(policy.get('query_negations_missing')),
            width, candidate['origin'] != 'baseline', candidate['physical'])

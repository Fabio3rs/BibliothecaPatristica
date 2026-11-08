from tools.indexing.lexical_candidate_policy import describe, load_policy


def test_word_order_changes_do_not_lower_term_coverage():
    policy=load_policy()
    a=describe('βαρύφορτος γυνή','γυνὴ βαρύφορτος',policy)
    assert a['complete_informative_terms']
    assert a['informative_coverage']==1
    assert a['informative_order_changed']
    assert a['weighted_coverage']==1


def test_function_words_have_low_weight_but_names_and_negation_do_not():
    policy=load_policy()
    a=describe('Aaron est sacerdos','Aaron sacerdos',policy)
    assert a['informative_coverage']==1
    assert a['weighted_coverage']>0.95
    b=describe('Anima non est ex traditione','Anima est ex traditione',policy)
    assert b['query_negations_missing']==['non']
    assert 'non' not in b['low_weight_terms']
    assert 'deus' in describe('Deus et homo','Deus homo',policy)['informative_terms']


def test_one_document_word_cannot_match_two_query_words():
    a=describe('Aaron Aaronis','Aaronis',load_policy())
    assert not a['complete_informative_terms']
    assert len(a['matched_informative_terms'])==1


def test_only_stopwords_are_not_an_informative_match():
    a=describe('de et in','de et in',load_policy())
    assert a['informative_coverage']==0
    assert not a['complete_informative_terms']

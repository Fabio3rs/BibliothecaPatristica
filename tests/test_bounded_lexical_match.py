from tools.indexing.bounded_lexical_match import match_text


def test_exact_uses_word_boundaries_and_original_spans():
    raw = 'Praefatio. Rachel venit, Rachelis filius.'
    result = match_text('Rachel', raw)
    assert result['method'] == 'exact'
    assert result['match_count'] == 1
    hit = result['matches'][0]
    assert raw[hit['start']:hit['end']] == 'Rachel'
    assert match_text('Rachel', 'Rachelis filius')['method'] == 'absent'


def test_greek_and_latin_normalization():
    assert match_text('Ζαχαρίας', 'ΖΑΧΑΡΙΑΣ')['method'] == 'normalized'
    assert match_text('cœlum', 'COELUM')['method'] == 'normalized'
    assert match_text('ᾧ', 'ω')['method'] == 'normalized'


def test_fuzzy_is_clue_not_ocr_verdict():
    result = match_text('Ζαχαρίας', 'Ζαχρίας advenit.')
    assert result['method'] == 'fuzzy'
    assert not result['certified']
    assert not result['ocr_error_confirmed']
    assert 'similar_spelling_not_proof_of_ocr_error' in result['flags']


def test_pg043_454_transposition_and_reversed_word_order():
    # Reviewed index476/destination454: bounded_lexical_case_reviews.json.
    raw = '21 Ὅττι γυνὴ βαρύφορτος ἄχος περὶ θυμὸν ἑλίσσει,'
    result = match_text('βαρύφροτος γυνή', raw)
    assert result['method'] == 'partial'
    assert result['query_scope'] == 'informative_terms_only'
    assert result['coverage'] == 1.0
    assert not result['certified']
    assert not result['ocr_error_confirmed']
    hit = result['matches'][0]
    assert hit['text_raw'] == 'γυνὴ βαρύφορτος'
    assert raw[hit['start']:hit['end']] == hit['text_raw']
    assert set(hit['matched_terms']) == {'βαρυφροτοσ', 'γυνη'}
    assert 'fuzzy' in hit['token_methods']


def test_short_names_are_exact_only_and_weak():
    assert match_text('Noe', 'Noa venit')['method'] == 'absent'
    result = match_text('Noe', 'Noe venit')
    assert result['method'] == 'exact'
    assert 'short_or_generic_query_weak_evidence' in result['flags']


def test_generic_words_do_not_make_partial_clause_match():
    assert match_text('De ratione et natura', 'De morte et vita')['method'] == 'absent'
    result = match_text('Deus', 'Deus')
    assert 'short_or_generic_query_weak_evidence' in result['flags']


def test_partial_clause_explicit_and_local():
    result = match_text('Aaron mors significat interitum sacerdotii Judaici',
                        'De obitu Aaron. Sacerdotii finem significat.')
    assert result['method'] == 'partial'
    assert result['matches'][0]['coverage'] < 1
    assert result['query_scope'] == 'informative_terms_only'
    assert match_text('Aaron sacerdotii', 'Aaron ' + 'verbum ' * 40 + 'sacerdotii')['method'] == 'absent'


def test_markup_attributes_headers_and_notes():
    raw = ('<bloco tipo="cabecalho">Rachel</bloco>'
           '<bloco tipo="texto_principal" nome="Rachel">alia</bloco>'
           '<bloco tipo="nota">De <span class="x">Rachel</span>.</bloco>')
    result = match_text('Rachel', raw)
    assert result['match_count'] == 1
    hit = result['matches'][0]
    assert hit['block_kind'] == 'nota'
    assert raw[hit['start']:hit['end']] == 'Rachel'


def test_no_matching_across_blocks_or_from_unknown_markup():
    raw = '<texto_principal>Aaron</texto_principal><nota>sacerdotii</nota>'
    assert match_text('Aaron sacerdotii', raw)['method'] == 'absent'
    result = match_text('Rachel', '<metadata><model>Rachel</model></metadata>')
    assert result['method'] == 'absent'
    assert 'unsupported_markup' in result['flags']


def test_punctuation_difference_is_normalized_not_exact():
    assert match_text('Aaron sacerdos', 'Aaron, sacerdos')['method'] == 'normalized'


def test_deterministic_bounded_results():
    raw = 'Rachel venit. Rachel abiit. Rachel dormit.'
    a = match_text('Rachel', raw, max_matches=2)
    assert a == match_text('Rachel', raw, max_matches=2)
    assert len(a['matches']) == 2
    assert a['match_count'] == 3
    assert a['matches_truncated']

import pytest

from tools.indexing.missing_latin_region_parser import parse_region_page


def parse(text, context=None):
    return parse_region_page(text, source='fixture', volume='UNKNOWN', physical=1,
                             scopes=[{'span': [0, len(text)], 'section_id': 's'}],
                             section_context=context or {})


def assert_literal(text, result):
    for row in result['entries']:
        assert text[slice(*row['owner_span'])] == row['owner_raw']
        assert text[slice(*row['reference_span'])] == row['reference_raw']
        assert row['entry_span'][0] <= row['owner_span'][0] < row['owner_span'][1] <= row['reference_span'][0]


def test_comma_clauses_and_hard_wrap_preserve_spans():
    text = 'Abel interpretatur luctus et quare, 56. Figura fuit\nChristi, 59.\nAaron et Moyses loquentes contra\nPharaonem, 173.'
    result = parse(text)
    assert [x['reference_raw'] for x in result['entries']] == ['56', '59', '173']
    assert result['entries'][1]['owner_raw'] == 'Figura fuit\nChristi'
    assert result['entries'][2]['owner_raw'] == 'Aaron et Moyses loquentes contra\nPharaonem'
    assert_literal(text, result)


def test_terminal_without_comma_does_not_join_next_owner():
    text = 'Agilbertus Cenomanensis. 200\nAureus solidus. 111\nBasilica. 91, 92, 97'
    result = parse(text)
    assert [x['owner_raw'] for x in result['entries']] == ['Agilbertus Cenomanensis', 'Aureus solidus', 'Basilica']
    assert [x['reference_raw'] for x in result['entries']] == ['200', '111', '91, 92, 97']
    assert_literal(text, result)


def test_contents_with_ranges_ibid_and_chapter_numbers():
    text = 'IN TOMUM SEPTIMUM PRÆFATIO. 9-10\nII. Quod nulla unquam bella ita gesta sunt. 13\nIII. Quam imprudenter Romani. Ibid.'
    result = parse(text, {'title': 'INDEX RERUM QUÆ IN HOC TOMO SEPTIMO CONTINENTUR.'})
    assert [x['entry_kind'] for x in result['entries']] == ['contents'] * 3
    assert [x['reference_raw'] for x in result['entries']] == ['9-10', '13', 'Ibid.']
    assert result['entries'][1]['owner_raw'].startswith('II.')
    assert_literal(text, result)


def test_internal_number_notice_is_not_page_or_contents():
    result = parse('Aaron mors significat interitum sacerdotii Judaici, 308.', {
        'title': 'INDEX RERUM AC VERBORUM QUÆ IN HOC PRIMO OPERUM RUPERTI VOLUMINE CONTINENTUR.',
        'notice': 'Revocatur lector ad numeros crassiores textui insertos.'})
    assert result['entries'][0]['reference_namespace'] == 'internal_text_number'
    assert result['entries'][0]['entry_kind'] == 'index_entry'


def test_bracketed_roman_and_arabic_remain_distinct_literal_values():
    text = 'Andreas Cretensis, xi.\nAmethystus quæ gemma, 32.\nAnastasius, x, 8. et alibi.'
    result = parse(text, {'notice': 'Revocatur Lector ad numeros uncis conclusos. Romani Præfationem, Arabici notas designant.'})
    assert [x['reference_raw'] for x in result['entries']] == ['xi', '32', 'x, 8']
    assert {x['reference_namespace'] for x in result['entries']} == {'bracketed_preface_roman_or_note_arabic'}
    assert_literal(text, result)


def test_parallel_columns_keep_dash_and_unassigned_order():
    text = 'Aborsum quae necant mulieres, vel abortum. 152 103\nAccubitus in Ecclesia prohibiti. 216 —\nAccusare non potest calumniator. — 205'
    result = parse(text, {'title': 'INDEX RERUM IN FERRANDI BREVIATIONE ET CRISCONII BREVIARIO CANONICO'})
    assert [x['reference_raw'] for x in result['entries']] == ['152 103', '216 —', '— 205']
    assert {x['reference_namespace'] for x in result['entries']} == {'dual_canonical_numbers'}
    assert all('parallel_column_assignment_unverified' in x['flags'] for x in result['entries'])
    assert_literal(text, result)


def test_qualified_locator_lists_and_clauses():
    text = 'Adamus per arrogantiam cecidit 186 a. m. 652 p. m. Adamum cur malum creaverit Deus 595 a. m.'
    result = parse(text, {'notice': 'NOTA. Indicatur locus, ubi incipit rei tractatio.'})
    assert [x['reference_raw'] for x in result['entries']] == ['186 a. m. 652 p. m.', '595 a. m.']
    assert result['entries'][1]['owner_raw'] == 'Adamum cur malum creaverit Deus'
    assert_literal(text, result)


def test_years_and_cardinal_counts_not_fabricated_as_individual_destinations():
    text = 'Concil. Nicænum 318. PP. 35, 59. Constantinopolitanum I, 150. PP. 36, 59.'
    result = parse(text)
    # Numeric prose must remain visible as a warning, not quietly disappear.
    assert any('numeric_text_in_owner_requires_review' in x['flags'] for x in result['entries'])
    assert not any(x['reference_raw'] == '318' for x in result['entries'])


def test_scope_enforces_mixed_page_boundary_and_unknown_namespace():
    text = '<bloco tipo="texto_principal">NOT INDEX 999.\nAaron, 79.\nORDO RERUM 400.</bloco>'
    a = text.index('Aaron'); b = text.index('\nORDO')
    result = parse_region_page(text, source='x', volume='PL001', physical=2,
                              scopes=[{'span': [a, b], 'section_id': 's'}])
    assert [x['reference_raw'] for x in result['entries']] == ['79']
    assert result['entries'][0]['reference_namespace'] == 'unknown'


def test_number_alone_and_cross_reference_stay_residual():
    result = parse('123\n\nÆsculapius. Vid. Asclepius.\nBonifacius V.')
    assert not result['entries']
    assert result['residuals']


def test_invalid_scope_rejected():
    with pytest.raises(ValueError):
        parse_region_page('text', source='x', volume='v', physical=1,
                          scopes=[{'span': [0, 3]}, {'span': [2, 4]}])


def test_capitula_prefix_is_chapter_reference_not_page():
    text = 'INDEX CAPITULORUM LIBRI PRIMI.\nCAP. I. Quod in Novo Testamento sacerdotalis\nceperit ordo.\nII. De privilegio beato Petro.\nIndicis capitulorum finis.'
    result = parse(text, {'title': 'INDEX CAPITULORUM LIBRI PRIMI.'})
    assert [x['reference_raw'] for x in result['entries']] == ['I', 'II']
    assert result['entries'][0]['owner_raw'] == 'Quod in Novo Testamento sacerdotalis\nceperit ordo.'
    assert result['entries'][1]['owner_raw'] == 'De privilegio beato Petro.'
    assert {x['reference_namespace'] for x in result['entries']} == {'chapter_number'}
    assert all(text[slice(*x['owner_span'])] == x['owner_raw'] for x in result['entries'])
    assert all(text[slice(*x['reference_span'])] == x['reference_raw'] for x in result['entries'])


def test_contents_heading_without_reference_not_joined_to_chapter():
    text = 'LIBER PRIMUS. — Describit illud argumentum\nCAPUT PRIMUM. De adversariis. Ibid.'
    result = parse(text, {'title': 'INDEX RERUM QUÆ IN HOC TOMO SEPTIMO CONTINENTUR.'})
    assert result['entries'][0]['owner_raw'] == 'CAPUT PRIMUM. De adversariis'
    assert any(x['reason'] == 'unreferenced_heading_or_description' for x in result['residuals'])


def test_explicit_book_chapter_not_truncated_into_generic_number():
    text = 'Alcuinus, lib. iv, c. 2.\nBenedictus abbas Casinensis, lib. iii, c. 30.'
    result = parse(text, {'title': 'INDEX SCRIPTORUM ECCLESIASTICORUM.', 'text': text})
    assert [x['reference_raw'] for x in result['entries']] == ['lib. iv, c. 2', 'lib. iii, c. 30']
    assert [x['owner_raw'] for x in result['entries']] == ['Alcuinus', 'Benedictus abbas Casinensis']
    assert {x['reference_namespace'] for x in result['entries']} == {'book_chapter'}
    assert_literal(text, result)


def test_xml_headings_classify_contents_without_volume_hardcoding():
    text = '<bloco tipo="titulo">INDEX RERUM</bloco><bloco tipo="titulo">QUÆ IN HOC DECIMO VOLUMINE CONTINENTUR.</bloco><bloco tipo="texto_principal">CAPUT PRIMUM. Præfatio. 12</bloco>'
    result = parse(text, {'text': text})
    assert result['entries'][0]['entry_kind'] == 'contents'


def test_interleaved_columns_are_preserved_as_residual():
    text = 'CAP. 1. Si quis spontanea homicidium per- A 26. De illis qui veneficio.\npetraverit.\n2. Quid in primo anno observare debeat.'
    result = parse(text, {'title': 'INDEX CAPITULORUM LIBRI SEXTI.'})
    assert [x['reference_raw'] for x in result['entries']] == ['2']
    assert any(x['reason'] == 'possible_interleaved_columns' for x in result['residuals'])


def test_chapter_names_keep_cap_prefix_and_space_list():
    text = 'Abbo Floriacensis abbas. cap. 139\nAldhelmus Malmesburiensis. 66 132'
    result = parse(text, {'title': 'INDEX SCRIPTORUM ECCLESIASTICORUM QUI A SIGEBERTO MEMORANTUR'})
    assert [x['reference_raw'] for x in result['entries']] == ['cap. 139', '66 132']
    assert [x['owner_raw'] for x in result['entries']] == ['Abbo Floriacensis abbas', 'Aldhelmus Malmesburiensis']
    assert {x['reference_namespace'] for x in result['entries']} == {'chapter_number'}
    assert_literal(text, result)


def test_qualified_continuation_detected_without_opening_notice():
    text = 'Adamus cecidit 186 a. m.\nAngeli adsunt 545 c. m.\nAnima pulchritudinem affert 395 p. m.'
    result = parse(text)
    assert len(result['entries']) == 3
    assert {x['grammar'] for x in result['entries']} == {'qualified_locators'}


def test_unknown_markup_never_silently_disappears():
    text = '<unknown>Adamus, 186.</unknown>'
    result = parse(text)
    assert not result['entries']
    assert result['residuals'][0]['reason'] == 'unsupported_markup'
    assert result['residuals'][0]['raw'] == text


@pytest.mark.parametrize('notice', [
    'Numeri hujus indicis respondent numeris crassiioribus in textu insertis.',
    'Numeri hujus indicis respondent numeris crassioribus in textu insertis.',
    'Revocatur Lector ad numeros grandiores textui insertos.',
    'Numeri indicantur qui a col. 941 typis grandioribus in textu sunt expressi.',
])
def test_documented_internal_number_notice_variants(notice):
    text = 'Abraham, 15.\nAdamas, 77.'
    result = parse(text, {'title': 'INDEX RERUM', 'notice': notice})
    assert [x['reference_raw'] for x in result['entries']] == ['15', '77']
    assert {x['reference_namespace'] for x in result['entries']} == {'internal_text_number'}


@pytest.mark.parametrize('notice', [
    'INDEX LATINUS AD DANIELIS HEINSII EXERCITATIONES SACRAS.',
    'Numeri paginarum typis grandioribus in margine leguntur.',
    'Typis grandioribus in textu expressis titulis.',
])
def test_internal_namespace_requires_explicit_number_notice(notice):
    result = parse('Abraham, 15.', {'title': notice})
    assert result['entries'][0]['reference_namespace'] == 'unknown'


def test_subdivision_and_ibidem_are_extracted_with_their_owner():
    text='Mens oculus animi, 111, C. Oculus animi mens, ibid. C. Ratio perfecta, ibidem.'
    result=parse(text)
    assert [e['reference_raw'] for e in result['entries']]==['111, C','ibid. C','ibidem']
    assert [e['owner_raw'] for e in result['entries']]==['Mens oculus animi','Oculus animi mens','Ratio perfecta']
    assert_literal(text,result)


def test_latin_preposition_after_locator_is_not_column_letter():
    text='Imperium amisit, 17. A Phoca devictus fuit, 19.'
    result=parse(text)
    assert [e['reference_raw'] for e in result['entries']]==['17','19']
    assert result['entries'][1]['owner_raw']=='A Phoca devictus fuit'
    assert_literal(text,result)


def test_embedded_numeric_prose_stays_flagged_instead_of_becoming_page():
    text='Huniades dimittitur 1448, ibid.'
    result=parse(text)
    assert [e['reference_raw'] for e in result['entries']]==['ibid']
    assert 'numeric_text_in_owner_requires_review' in result['entries'][0]['flags']

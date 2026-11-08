from pathlib import Path

from tools.indexing.missing_po_region_parser import coalesce_region_scopes, extract_page_line_tokens, parse_region_page


def parse(body, *, kind='scripture_index', context=None, volume='PO012'):
    raw = '<pagina><bloco tipo="texto_principal">' + body + '</bloco></pagina>'
    result = parse_region_page(raw, source='fixture', volume=volume, physical=1,
                               scopes=[{'span': [0, len(raw)], 'section_id': 's', 'kind': kind}],
                               section_context=context)
    for entry in result['entries']:
        for key in ['owner', 'reference']:
            a, b = entry[key + '_span']
            assert entry[key + '_raw'] == raw[a:b]
    return result


def test_bible_dotted_leaders_preserve_book_and_local_chapter():
    r = parse('GENÈSE\nI, 9 ........ 257\n— 26 . . . . 304, 306\nEXODE\n— 14 ........ 324')
    assert len(r['entries']) == 3
    assert r['entries'][1]['owner_raw'] == '— 26'
    assert r['entries'][1]['search_label'] == 'GENÈSE I 26'
    assert r['entries'][1]['reference_raw'] == '304, 306'
    assert 'missing_chapter_context' in r['entries'][2]['flags']
    assert r['entries'][2]['scripture_context']['chapter_raw'] is None


def test_abbreviated_books_replace_context_and_other_citations_reset_it():
    r = parse('JEAN\nI, 6 .... 169\nRom.\nvii, 15 .... 105\nI Cor.\nii, 10 .... 174\nAUTRES CITATIONS\nI, 3 .... 85')
    assert [e.get('scripture_context', {}).get('book_raw') for e in r['entries']] == ['JEAN', 'Rom.', 'I Cor.', None]
    assert 'book' not in r['context']['s']


def test_typographic_subdivision_does_not_become_another_page():
    r = parse('ܐܦܣܩܦܐ 34₁₂ 35₁\nܐܚܕܐ 116_14', kind='onomastic_index')
    assert [e['page_candidates_numbers'] for e in r['entries']] == [[34, 35], [116]]
    assert r['entries'][0]['reference_raw'] == '34₁₂ 35₁'
    assert r['entries'][0]['page_line_tokens'][0]['line_raw'] == '₁₂'
    assert 'page_subdivision_typographic_candidate_line_semantics_unverified' in r['entries'][0]['flags']


def test_bible_book_repeat_differs_from_chapter_repeat():
    r = parse('Gen., III, 19. . . . . 200\n— IX, 20-23. . . . . 16',
              context={'s': {'text': 'Numeri non paginas, sed logiorum ordinem, significant.'}})
    assert r['entries'][0]['reference_namespace'] == 'logion'
    assert r['entries'][1]['search_label'] == 'Gen. IX 20-23'


def test_po003_underscores_never_treat_lines_as_pages():
    r = parse('ܐܠ 15_3 ; 31_2 ; 38_1 ; 95_4\nܐܠܐ 70_13', volume='PO003', kind='onomastic_index',
              context={'s': {'text': 'Nous renvoyons à la page et à la ligne.'}})
    assert r['entries'][0]['page_candidates_numbers'] == [15, 31, 38, 95]
    assert r['entries'][1]['page_candidates_numbers'] == [70]
    assert r['entries'][1]['page_line_tokens'][0]['line_raw'] == '13'


def test_wrap_preserves_literal_span_and_logion_suffix():
    r = parse('Abstinentia, 37, 39, 42,\n71, 102ter, 227.\nAmici Dei quinam sint, 84,\n169.',
              kind='thematic_index', context={'s': {'text': 'Numeri logiorum ordinem, non vero paginas, indicant.'}})
    assert len(r['entries']) == 2
    assert all(e['reference_namespace'] == 'logion' for e in r['entries'])
    assert r['entries'][0]['reference_raw'] == '37, 39, 42,\n71, 102ter, 227.'


def test_mixed_section_notice_overrides_namespace():
    r = parse('N. B. Numeri non paginas, sed logiorum ordinem, significant.\nGen., III, 19 . . . . 200\n5° Numerus paginarum, non vero logiorum, ordinem signat.\nAdam . . . . II, 581\nN. B. Numeri logiorum ordinem, non vero paginas, indicant.\nAbnegatio, 20, 54.', kind='index_unspecified')
    assert [e['reference_namespace'] for e in r['entries']] == ['logion', 'qualified_page', 'logion']


def test_parentheses_destinations_are_work_chapters():
    r = parse('Genèse\nI, 1 (43).\n— 27 (55).', context={'s': {'text': 'Les chiffres entre parenthèses indiquent les numéros des chapitres'}})
    assert [e['reference_namespace'] for e in r['entries']] == ['chapter', 'chapter']
    assert r['entries'][1]['reference_raw'] == '55'
    assert r['entries'][1]['search_label'] == 'Genèse I 27'


def test_parallel_columns_remain_residual():
    r = parse('Wahab, 349, 373 | Wright . . . . 337', kind='thematic_index')
    assert not r['entries']
    assert r['residuals'][0]['reason'] == 'parallel_columns_interleaved'


def test_context_is_local_to_section():
    raw = '<bloco>GENÈSE\nI, 9 .... 257\n— 26 .... 304</bloco>'
    pivot = raw.index('—')
    r = parse_region_page(raw, source='fixture', volume='PO012', physical=1, scopes=[
        {'span': [0, pivot], 'section_id': 'a', 'kind': 'scripture_index'},
        {'span': [pivot, len(raw)], 'section_id': 'b', 'kind': 'scripture_index'},
    ])
    assert 'missing_book_context' in r['entries'][1]['flags']
    assert 'missing_chapter_context' in r['entries'][1]['flags']


def test_production_sample_without_tipo_attribute():
    p = next(Path('teste/PO003/text').glob('*-127.txt'))
    raw = p.read_text()
    r = parse_region_page(raw, source=str(p), volume='PO003', physical=127,
                          scopes=[{'span': [0, len(raw)], 'section_id': 's', 'kind': 'onomastic_index'}])
    assert len(r['entries']) > 50
    assert any(e.get('page_candidates_numbers') == [70] for e in r['entries'])
    assert not any('A página é' in e['owner_raw'] for e in r['entries'])


def test_bracketed_catalogue_page_range_is_not_unqualified_page():
    r = parse('INTRODUCTION. . . . . [321-323]', kind='index_unspecified', volume='PO019')
    assert r['entries'][0]['reference_namespace'] == 'bracketed_page'


def test_numeric_wrapping_survives_ocr_period_and_quoted_ellipsis():
    r = parse('Beatitudines (« Beatus... »), 20, 21, 56, 57,\n92, 136, 217, 219.\nHumilitas, 57, 58.\n114, 115.\nHypocrisis, 5.',
              kind='thematic_index', context={'s': {'text': 'Numeri logiorum ordinem, non vero paginas, indicant.'}})
    assert len(r['entries']) == 3
    assert '92, 136' in r['entries'][0]['reference_raw']
    assert '114, 115' in r['entries'][1]['reference_raw']


def test_line_scopes_coalesce_for_wrapped_entry_without_relaxing_containment():
    raw = '<bloco>Abstinentia, 37, 39,\n  71, 102ter, 227.</bloco>'
    first = [raw.index('Abstinentia'), raw.index('\n')]
    second = [raw.index('71,'), raw.index('</bloco>')]
    scopes = [{'span': span, 'section_id': 's', 'kind': 'thematic_index'} for span in [first, second]]
    merged = coalesce_region_scopes(raw, scopes)
    assert len(merged) == 1
    assert merged[0]['component_spans'] == [first, second]
    result = parse_region_page(raw, source='fixture', volume='PO019', physical=1,
                               scopes=merged, section_context={'s': {'namespace': 'logion'}})
    entry = result['entries'][0]
    assert merged[0]['span'][0] <= entry['entry_span'][0] < entry['entry_span'][1] <= merged[0]['span'][1]
    assert entry['reference_raw'] == '37, 39,\n  71, 102ter, 227.'
    assert scopes[0]['span'] == first


def test_scope_coalescing_never_bridges_unknown_text_blocks_or_sections():
    raw = '<bloco>Alpha 12\nUNKNOWN\nBeta 13\nGamma 14</bloco>\n<bloco>Delta 15</bloco>'
    scopes = []
    for label, section in [('Alpha 12', 's'), ('Beta 13', 's'), ('Gamma 14', 'other'), ('Delta 15', 'other')]:
        a = raw.index(label)
        scopes.append({'span': [a, a + len(label)], 'section_id': section, 'kind': 'thematic_index'})
    assert len(coalesce_region_scopes(raw, scopes)) == 4


def test_real_po019_wrapped_scopes_pass_literal_and_scope_validation():
    raw = next(Path('teste/PO019/text').glob('*-631.txt')).read_text()
    start = raw.index('Abstinentia,')
    end = raw.index('Amici Dei')
    scopes, cursor = [], start
    for line in raw[start:end].splitlines(keepends=True):
        left = len(line) - len(line.lstrip())
        content = line.strip()
        if content:
            scopes.append({'span': [cursor + left, cursor + left + len(content)], 'section_id': 's', 'kind': 'thematic_index'})
        cursor += len(line)
    merged = coalesce_region_scopes(raw, scopes)
    result = parse_region_page(raw, source='fixture', volume='PO019', physical=631,
                               scopes=merged, section_context={'s': {'namespace': 'logion'}})
    assert len(result['entries']) == 1
    entry = result['entries'][0]
    assert '221, 227.' in entry['reference_raw']
    for key in ['owner', 'reference']:
        assert raw[slice(*entry[key + '_span'])] == entry[key + '_raw']
    assert any(s['span'][0] <= entry['entry_span'][0] < entry['entry_span'][1] <= s['span'][1] for s in merged)


def test_page_line_comma_lists_notes_and_dangling_locator():
    refs = '27,13 ; 28,3 ; 29, 7, 8, 10 ; 32 n. 1 ; 70 var. cf. X ; 29,'
    r = extract_page_line_tokens(refs, base_offset=120)
    assert [int(t['page_raw']) for t in r['tokens']] == [27, 28, 29, 32, 70]
    assert r['tokens'][2]['line_raw'] == '7, 8, 10'
    assert r['tokens'][3]['note_raw'] == '1'
    assert r['pending'][0]['raw'] == '29,'
    for token in r['tokens']:
        a, b = token['span']
        assert refs[a - 120:b - 120] == token['raw']


def test_page_line_does_not_split_concatenated_digits():
    r = extract_page_line_tokens('171 V. ܐܚܕܐ.')
    assert r['tokens'] == []
    assert r['pending'][0]['reason'] == 'bare_number_page_line_ambiguous'


def test_page_line_comma_continuation_and_ranges():
    refs = '75,13 ; 76,2-5\n82,11 ; 96,10,12'
    r = extract_page_line_tokens(refs)
    assert [t['page_raw'] for t in r['tokens']] == ['75', '76', '82', '96']
    assert [t['line_raw'] for t in r['tokens']] == ['13', '2-5', '11', '10,12']


def test_new_editorial_table_resets_page_line_context():
    r = parse("TABLE DES CITATIONS DE L'ÉCRITURE\nI Cor. IX, 22 ..... 83,84", volume='PO003',
              context={'s': {'namespace': 'page_line'}})
    assert r['entries'][0]['reference_namespace'] == 'page'
    assert 'page_line_tokens' not in r['entries'][0]


def test_po003_bible_book_without_comma_is_preserved():
    r = parse("Genèse XLV, 10 ...... 94\nExode III, 5 ...... 17\n— IV, 13 ..... 72")
    assert r['entries'][0]['search_label'] == 'Genèse XLV 10'
    assert r['entries'][2]['search_label'] == 'Exode IV 13'


def test_real_po003_bible_and_contents_titles_reset_names_namespace():
    for physical in [129, 130]:
        p = next(Path('teste/PO003/text').glob(f'*-{physical}.txt'))
        raw = p.read_text()
        r = parse_region_page(raw, source=str(p), volume='PO003', physical=physical,
                              scopes=[{'span': [0, len(raw)], 'section_id': 's', 'kind': 'onomastic_index'}],
                              section_context={'s': {'namespace': 'page_line'}})
        assert len(r['entries']) > 10
        assert all(e['reference_namespace'] == 'page' for e in r['entries'])
        assert any(x['reason'] == 'new_editorial_table_page_namespace' for x in r['residuals'])
        for e in r['entries']:
            assert raw[slice(*e['reference_span'])] == e['reference_raw']
            proof = e['namespace_evidence']
            assert proof['source'] == str(p)
            assert raw[slice(*proof['span'])] == proof['raw']
            assert proof['raw'].startswith('TABLE DES ')


def test_title_outside_regional_span_still_stops_wrong_notation_inheritance():
    raw = '<bloco tipo="cabecalho">TABLE DES MATIÈRES</bloco><bloco tipo="texto_principal">Introduction ..... 83,84</bloco>'
    a, b = raw.index('Introduction'), raw.rindex('</bloco>')
    result = parse_region_page(raw, source='fixture', volume='PO003', physical=130,
                               scopes=[{'span': [a, b], 'section_id': 's', 'kind': 'onomastic_index'}],
                               section_context={'s': {'namespace': 'page_line'}})
    assert result['entries'][0]['reference_namespace'] == 'page'
    assert result['context']['s']['namespace_evidence']['raw'] == 'TABLE DES MATIÈRES'
    assert result['entries'][0]['namespace_evidence']['raw'] == 'TABLE DES MATIÈRES'
    assert result['entries'][0]['namespace_evidence'] is not result['context']['s']['namespace_evidence']


def test_missing_po_scripture_period_headings_lowercase_and_verse_continuation():
    r = parse('GENÈSE.\nxii, 3 .... 58\n— xxiv, 12 .... 63\n32, 39, 40 .... 47\nECCLI.\nii, 1 .... 21',
              context={'s': {'family': 'scripture_leaders'}})
    assert [e['label'] for e in r['entries']] == [
        'GENÈSE. xii 3', 'GENÈSE. xxiv 12', 'GENÈSE. xxiv 32, 39, 40', 'ECCLI. ii 1']


def test_po_single_period_only_splits_terminal_page():
    r = parse('Daniel, iv, 24. 32\n— iii, 15. 76',
              context={'s': {'family': 'scripture_single_period'}})
    assert [e['reference_raw'] for e in r['entries']] == ['32', '76']
    assert r['entries'][1]['label'] == 'Daniel iii 15'


def test_greek_lowercase_entries_are_not_concatenated_as_wrapped_latin():
    r = parse('ἄββας, 111₆\nἀγγελοφανής, 78₂ 111₁₃\nἈρκάδιος, moine, 103₁₀',
              kind='onomastic_index', context={'s': {'family': 'names_lines'}})
    assert len(r['entries']) == 3
    assert [e['page_candidates_numbers'] for e in r['entries']] == [[111], [78, 111], [103]]


def test_name_wrapping_accepts_capitalized_name_after_incomplete_owner():
    r = parse('Ammon de Pebou, auteur de la lettre sur\nThéodore et Pacôme 393 n. 1.\nAmmon 410₁₈',
              kind='onomastic_index', context={'s': {'family': 'names_lines'}})
    assert len(r['entries']) == 2
    assert 'Théodore' in r['entries'][0]['owner_raw']


def test_greek_external_edition_gloss_does_not_supply_local_page_tokens():
    r = parse('᾿Αδδισσότας († 344) : 435,12. — S, 445; Ἀδερφάρ : 5,41.',
              kind='onomastic_index', context={'s': {'family': 'greek_colon_names', 'namespace': 'page_line'}})
    e = r['entries'][0]
    assert e['page_candidates_numbers'] == [435]
    assert e['external_gloss']['raw'] == 'S, 445; Ἀδερφάρ : 5,41.'
    assert '† 344' in e['owner_raw']


def test_parallel_scripture_columns_reset_context_and_remain_residual():
    r = parse('GENÈSE.\nI, 2 .... 11\n— 3 .... 12 XVIII, 4 .... 13\n— 4 .... 14',
              context={'s': {'family': 'scripture_leaders'}})
    assert len(r['entries']) == 2
    assert any(x['reason'] == 'parallel_columns_interleaved' for x in r['residuals'])
    assert 'missing_book_context' in r['entries'][1]['flags']


def test_inline_bracketed_scripture_preserves_each_occurrence_and_book():
    r = parse('Ps., v, 7 [51]. — vii, 16 [55].\nxxxiii, 8 [25].',
              context={'s': {'family': 'inline_scripture'}})
    assert [e['label'] for e in r['entries']] == ['Ps. v 7', 'Ps. vii 16', 'Ps. xxxiii 8']
    assert [e['reference_raw'] for e in r['entries']] == ['51', '55', '25']
    assert all(e['reference_namespace'] == 'bracketed_page' for e in r['entries'])


def test_cross_volume_notice_routes_only_recognized_page_line_tokens():
    r = parse('ܐܝܬ 47,1 418,9 ; μὴ κρατηθείς 417,7\nܐܠ 100,3',
              kind='onomastic_index', context={'s': {'family': 'mixed_volume_words', 'namespace': 'page_line',
                                                     'text': 'Ammonii epistolae (t. X, fasc. 6, p. [15] à [65])'}})
    assert [t['target_volume_candidate'] for t in r['entries'][0]['page_line_tokens']] == ['PO010', 'PO011', 'PO011']
    assert r['entries'][1]['page_line_tokens'][0]['target_volume_candidate'] is None


def test_date_prose_before_explicit_page_is_not_a_destination():
    r = parse("Isaïe moine, mort le\n11 août 488. Converse avec Pierre l'Ibère en\n458/9. p. 27 et 28.\nVers 431 va voir Paul", kind='onomastic_index')
    e = r['entries'][0]
    assert e['reference_raw'] == '27 et 28'
    assert '11 août 488' in e['owner_raw']
    assert 'date_prose_before_explicit_page' in e['flags']
    assert any(x['reason'] == 'prose_after_explicit_page_reference' for x in r['residuals'])


def test_date_without_page_remains_opaque_and_other_citations_are_preserved():
    r = parse('Isaïe mort le 11 août 488.', kind='onomastic_index')
    assert r['entries'][0]['reference_namespace'] == 'editorial_locator_unparsed'
    r = parse('AUTRES CITATIONS\nDorothée lettre à Marcien .... 71-72')
    assert r['entries'][0]['citation_kind'] == 'non_scripture_citation'
    assert r['entries'][0]['flags'] == ['non_scripture_citation']


def test_other_citations_boundary_in_running_heading_block():
    raw = '<bloco tipo="cabecalho">AUTRES CITATIONS</bloco><bloco tipo="texto_principal">Dorothée .... 71</bloco>'
    r = parse_region_page(raw, source='fixture', volume='PO013', physical=334,
                          scopes=[{'span': [0, len(raw)], 'section_id': 's', 'kind': 'scripture_index'}])
    assert r['entries'][0]['citation_kind'] == 'non_scripture_citation'

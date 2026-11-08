from scripts.audit_index_missing_volumes import historical_class, heading_family, scan


def test_historical_categories_are_not_absence_claims():
    assert historical_class({}) == 'no_historical_region_signals'
    assert historical_class({'capitula':3}) == 'other_editorial_material_outside_index_filter'
    assert historical_class({'index_unspecified':1,'capitula':3}) == 'index_signals_without_catalog_rows'


def test_lists_of_works_and_chapters_are_not_name_indices():
    assert heading_family('ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CONTINENTUR.', 'work_catalog') == 'work_catalog'
    assert heading_family('INDEX CAPITUM CATHOLICAE EPISTOLAE JACOBI.', 'capitula') == 'capitula'
    assert heading_family('INDEX OPERUM QUAE IN NONO TOMO CONTINENTUR.', 'index_unspecified') == 'work_catalog'
    assert heading_family('TABLE ALPHABETIQUE DES NOMS PROPRES', 'onomastic_index') == 'onomastic_index'


def test_bounded_scan_keeps_unseeded_headings_and_provenance(tmp_path):
    folder=tmp_path/'PO009/text';folder.mkdir(parents=True)
    raw='<bloco tipo="cabecalho">TABLE DES CITATIONS</bloco>\n<bloco tipo="texto_principal">GENESE I, 6. 169</bloco>'
    p=folder/'test-678.txt';p.write_text(raw)
    result=scan(('PO009',str(tmp_path),[678],0,0,0))
    page=result['pages'][0]
    assert page['headings'][0]['role']=='running_header'
    assert page['headings'][0]['index_filter_eligible']
    h=page['headings'][0]
    assert raw[slice(*h['title_span'])]==h['title_raw']
    assert p.read_text()==raw


def test_full_scan_finds_middle_index_without_historical_anchors(tmp_path):
    folder=tmp_path/'PL999/text';folder.mkdir(parents=True)
    for number in range(1,6):
        raw = ('<bloco tipo="cabecalho">TABLE DES CITATIONS</bloco>'
               '<bloco tipo="texto_principal">GENESE 20</bloco>') if number==3 else '<texto_principal>Ordinary text.</texto_principal>'
        (folder/f'test-{number:03}.txt').write_text(raw)
    bounded=scan(('PL999',str(tmp_path),[],1,1,0))
    complete=scan(('PL999',str(tmp_path),[],1,1,0,True))
    assert bounded['selected_files']==2
    assert not any(p['headings'] for p in bounded['pages'])
    assert complete['selected_files']==5
    assert complete['pages'][2]['headings'][0]['index_filter_eligible']


def test_multilingual_table_titles_have_specific_index_families():
    from tools.indexing.editorial_regions_experiment import material_kind
    for title in ['TABLE GRECQUE DES NOMS PROPRES ET DES MOTS REMARQUABLES',
                  'TABLE FRANÇAISE DES NOMS PROPRES',
                  'TABLE ÉTHIOPIENNE DES NOMS PROPRES', 'INDEX DES NOMS PROPRES']:
        assert material_kind(title)=='onomastic_index'
    for title in ["TABLE DES RENVOIS À L'ÉCRITURE",'TABLE DES PASSAGES DE LA BIBLE',
                  'INDEX DES PASSAGES DE LA SAINTE ÉCRITURE']:
        assert material_kind(title)=='scripture_index'
    assert material_kind('TABLE ALPHABÉTIQUE DES MATIÈRES')=='thematic_index'
    assert material_kind('Table de tous les mots syriaques du présent fascicule')=='lexical_index'
    assert material_kind('TABLE ANALYTIQUE DES MATIÈRES')=='contents_catalog'
    assert material_kind('Les renvois à la table des noms propres sont utiles.') is None

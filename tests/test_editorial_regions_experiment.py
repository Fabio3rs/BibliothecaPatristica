import argparse
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.editorial_regions_experiment import (
    analyze_page, inventory, material_kind, run, signals,
)


def block(text, kind='texto_principal'):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def test_descriptive_header_opens_but_numbered_repeat_does_not():
    raw = block('TABLE DES CITATIONS', 'cabecalho') + block('GENÈSE\nI, 6 .... 169\nII, 23 .... 170')
    page, state = analyze_page(raw, 'a', 'PO009', 678)
    assert page['signals'][0]['role'] == 'section_start'
    assert state['kind'] == 'scripture_index'
    repeated = block('[181] TABLE DES CITATIONS. 669', 'cabecalho') + block('Rom.\nvii, 15 .... 105\nviii, 15 .... 176')
    second, _ = analyze_page(repeated, 'b', 'PO009', 679, state)
    assert second['signals'][0]['role'] == 'running_header'
    assert second['regions'][-1]['section_id'] == state['section_id']


def test_greek_split_title_and_internal_number_notice():
    raw = block('ΠΙΝΑΞ', 'cabecalho') + block('ΤΩΝ ΕΝ ΤΑΙΣ ΟΜΙΛΙΑΙΣ', 'cabecalho')
    raw += block('ΚΥΡΙΩΤΕΡΩΝ ΠΡΑΓΜΑΤΩΝ ΚΑΤΑ ΑΛΦΑΒΗΤΟΝ.', 'cabecalho')
    raw += block('(Hujusce voluminis col. 9 ; sed require ad numeros crassiores textui insertos.)', 'nota')
    raw += block('Ἀβραὰμ πατὴρ πολλῶν ἐθνῶν, 54.\nἈγάπης ἔργα τίνα εἰσί, 25.')
    page, state = analyze_page(raw, 'a', 'PG151', 687)
    assert state['kind'] == 'thematic_index'
    assert state['namespace'] == 'internal_emphasized_numbers_edition_unknown'
    assert len(page['signals'][0]['opening_evidence']['title_spans']) == 3


@pytest.mark.parametrize('title', ['INDEX', 'ΠΙΝΑΞ', '1371 TABLE DES CITATIONS 1372'])
def test_bare_or_numbered_header_does_not_open_even_with_list(title):
    page, state = analyze_page(block(title, 'cabecalho') + block('Aaron, 12.\nAbel, 34.'), 'a', 'PG001', 1)
    assert state is None
    assert page['signals'][0]['role'] == 'running_header'


def test_contents_title_closes_name_index():
    _, state = analyze_page(block('TABLE DES NOMS', 'cabecalho') + block('Aaron 12\nAbel 34'), 'a', 'PO009', 685)
    raw = block('TABLE ANALYTIQUE DES MATIÈRES', 'cabecalho') + block('INTRODUCTION .... 5\nCHAPITRE I .... 15')
    page, state = analyze_page(raw, 'b', 'PO009', 686, state)
    assert state['kind'] == 'contents_catalog'
    assert all(r['kind'] != 'onomastic_index' for r in page['regions'])


@pytest.mark.parametrize('text,kind', [
    ('INDEX GRÆCITATIS.', 'lexical_index'),
    ('INDEX ONOMASTICUS PERSONARUM', 'onomastic_index'),
    ('INDEX SPURIORUM', 'work_catalog'),
    ('INDEX CAPITUM CATHOLICAE EPISTOLAE', 'capitula'),
    ('INDEX ARGUMENTORUM TOTIUS OPERIS', 'numbered_arguments'),
    ('TABLE DES PÉRICOPES', 'scripture_index'),
    ('III. INDEX IN VITAM S. STEPHANI JUNIORIS.', 'thematic_index'),
    ('Et tamen index abbatum ab Ughello editus', None),
])
def test_material_types(text, kind):
    assert material_kind(text) == kind


def test_mixed_header_and_title_and_footer_boundary():
    raw = block('1541 ORDO RERUM 1542\nIII.\nINDEX IN VITAM S. STEPHANI JUNIORIS.', 'cabecalho')
    raw += block('Acacius martyr, 481.\nZeno, 500.')
    raw += block('ORDO RERUM\nQUAE IN HOC TOMO CONTINENTUR.\nNotitia 9.', 'rodape')
    page, _ = analyze_page(raw, 'test', 'PG100', 777)
    assert [e['role'] for e in page['signals']] == ['running_header','section_start','section_start']
    assert next(r for r in page['regions'] if r['quote'].startswith('Acacius'))['kind']=='thematic_index'
    assert page['regions'][-1]['kind']=='contents_catalog'
    for r in page['regions']:
        assert raw[slice(*r['span'])]==r['quote']
        assert r['verified'] is False


def test_prose_note_and_catalog_pointer_do_not_open_sections():
    raw = block('Et tamen index abbatum ab Ughello editus', 'nota')
    raw += block('INDEX SPURIORUM quae in tomo continentur. 483-486')
    page, state = analyze_page(raw,'test','PG059',762)
    assert state is None
    assert [e['role'] for e in page['signals']]==['mention','catalog_pointer']


def test_continuation_preserved_before_running_ordo_and_reset_after_gap():
    first, state = analyze_page(block('INDEX RERUM IN ARETHAM')+block('Abyssus, 724.'),'a','PG106',715)
    raw = block('1413 ORDO RERUM 1414','cabecalho')+block('Vita duplex, 814.')
    raw += block('ORDO RERUM\nQUAE IN HOC TOMO CONTINENTUR.')+block('Notitia 9.')
    next_page,_ = analyze_page(raw,'b','PG106',716,state)
    old=next(r for r in next_page['regions'] if r['quote'].startswith('Vita'))
    assert old['section_id']==first['regions'][0]['section_id']
    gap,_=analyze_page(block('Vita duplex, 814.'),'c','PG106',718,state)
    assert gap['regions'][0]['section_id'] is None


def test_namespace_is_section_scoped_and_numbers_are_not_mapped():
    raw = block('INDEX RERUM IN CLEMENTE')
    raw += block('Numerales notae paginas editionis Sylburgianae respiciunt.','nota')
    raw += block('Aaron, 293.')+block('INDEX AUCTORUM QUI A CLEMENTE CITANTUR')
    raw += block('Numeri paginas editionis Parisiensis respiciunt.','nota')+block('Abaris, 334.')
    page,_ = analyze_page(raw,'test','PG009',784)
    assert next(r for r in page['regions'] if r['quote']=='Aaron, 293.')['namespace']=='Sylburgiana'
    assert next(r for r in page['regions'] if r['quote']=='Abaris, 334.')['namespace']=='Parisiensis'
    assert len(page['declarations'])==2
    assert all('target_file' not in r for r in page['regions'])


def test_missing_ocr_is_not_reconstructed():
    page,_=analyze_page(block('1595 ORDO RERUM 1596','cabecalho')+block('ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.'),'test','PL143',806)
    assert not any(r['pipeline_hint']=='alphabetical' for r in page['regions'])
    assert page['coverage_claim']=='present_OCR_only_not_visual_completeness'


def options(tmp_path):
    return argparse.Namespace(root=tmp_path/'texte',volume=['PG100'],sample_manifest=None,
        page=['PG100:2'],tail_pages=0,context_pages=1,expand_before=0,workers=2,
        out=tmp_path/'out',experiment_db=tmp_path/'experiment.sqlite',source_db=tmp_path/'source.sqlite')


def test_end_to_end_experiment_and_hashes(tmp_path):
    args=options(tmp_path)
    d=args.root/'PG100/text'; d.mkdir(parents=True)
    for n in [1,2,3]:
        (d/f'p-{n}.txt').write_text(block('INDEX IN VITAM STEPHANI')+block('Acacius, 481.'))
    with sqlite3.connect(args.source_db) as c:
        c.execute('CREATE TABLE sentinel(value)')
    before=args.source_db.read_bytes()
    summary=run(args)
    assert summary['pages']==3
    assert args.source_db.read_bytes()==before
    with sqlite3.connect(args.experiment_db) as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert c.execute('SELECT count(*) FROM editorial_region_pages').fetchone()[0]==3
    assert (args.out/'sections.jsonl').exists()
    with pytest.raises(ValueError,match='already contains'):
        run(args)


def test_source_alias_and_unrelated_db_rejected(tmp_path):
    args=options(tmp_path)
    with sqlite3.connect(args.source_db) as c:
        c.execute('CREATE TABLE sentinel(value)')
    args.experiment_db=args.source_db
    before=args.source_db.read_bytes()
    with pytest.raises(ValueError,match='must differ'):
        run(args)
    assert args.source_db.read_bytes()==before


def test_ambiguous_sources_are_not_selected(tmp_path):
    args=options(tmp_path)
    d=args.root/'PG100/text';d.mkdir(parents=True)
    (d/'p-2.txt').write_text('one');(d/'other-002.txt').write_text('two')
    tasks,issues=inventory(args)
    assert tasks[0][1]==[]
    assert any(i.get('physical')==2 for i in issues)


def test_entities_preserve_original_spans():
    raw=block('INDEX IN VITAM STEPHANI')+block('A &amp; B, 123.')
    page,_=analyze_page(raw,'test','PG100',777)
    assert page['regions'][-1]['quote']=='A &amp; B, 123.'
    assert raw[slice(*page['regions'][-1]['span'])]=='A &amp; B, 123.'


def test_multiline_catalog_and_normal_words_are_not_titles():
    raw=block('INDEX AUCTORUM a Clemente laudatorum,\n1549\nINDEX VERBORUM, sententiarum et rerum\nin dissertatione. 1621')
    raw+=block('Capita septem bestiae, 75. Capita cum\naliis.\nargumentum, angelus ascendens e flamma, atque in')
    _,events=signals(raw)
    assert [e['role'] for e in events]==['catalog_pointer','catalog_pointer','mention','mention']


def test_split_title_and_top_header_with_separate_page_numbers():
    raw=block('1549','nota_marginal')+block('INDEX AUCTORUM A CLEMENTE ALEX. CITATORUM.','cabecalho')
    raw+=block('1550','nota_marginal')+block('Omega, 12.')
    raw+=block('INDEX\nVERBORUM, SENTENTIARUM ET RERUM\nQUAE IN DISSERTATIONE CONTINENTUR.','cabecalho')
    _,events=signals(raw)
    assert events[0]['role']=='running_header'
    assert events[1]['kind']=='thematic_index'
    assert events[1]['role']=='section_start'


def test_incidental_numbers_do_not_replace_declaration():
    raw=block('INDEX ANALYTICUS\nRERUM ET VERBORUM\nRevocatur Lector ad numeros typis grandioribus expressos.','cabecalho')
    raw+=block('Numeri in libro leguntur, 54.\nDei judicia paginas respiciunt, 55.')
    page,_=analyze_page(raw,'test','PG070',736)
    assert len(page['declarations'])==1
    assert page['declarations'][0]['quote'].startswith('Revocatur')
    assert page['regions'][-1]['namespace']=='internal_emphasized_numbers_edition_unknown'


def test_bilingual_adjacent_titles_share_candidate_section():
    raw=block('ΕΚΘΕΣΙΣ ΤΩΝ ΚΕΦΑΛΑΙΩΝ ΤΗΣ ΕΠΙΣΤΟΛΗΣ.')+block('INDEX CAPITUM EPISTOLAE.')
    raw+=block('1. De patientia.')
    page,_=analyze_page(raw,'test','PG125',647)
    assert len({r['section_id'] for r in page['regions']})==1
    assert page['signals'][1]['role']=='section_title_alias'


def test_unassigned_parenthesized_notice_is_preserved():
    raw=block('(Revocatur Lector ad numeros typis crassioribus in texto expressos.)','nota')
    page,_=analyze_page(raw,'test','PG100',771)
    assert page['declarations'][0]['status']=='scope_unassigned'
    assert page['declarations'][0]['section_id'] is None


def test_scanner_hash_is_raw_bytes_with_crlf(tmp_path):
    from tools.indexing.editorial_regions_experiment import scan_volume,sha
    p=tmp_path/'p-1.txt'
    data=block('INDEX RERUM\r\nAaron 1.').encode()
    p.write_bytes(data)
    record=scan_volume(('PG009',[p]))[0]
    assert record['source_sha256']==sha(data)
    for region in record['regions']:
        assert data.decode()[slice(*region['span'])]==region['quote']


def test_all_volumes_and_all_pages_inventory(tmp_path):
    args=options(tmp_path)
    args.volume=None;args.page=None;args.all_volumes=True;args.all_pages=True
    args.tail_pages=1
    for v in ['PG009','PL143','PO001','not_a_volume']:
        d=args.root/v/'text';d.mkdir(parents=True)
        for n in [1,3,90]:
            (d/f'p-{n}.txt').write_text(block('Plain prose'))
    tasks,issues=inventory(args)
    assert [v for v,_ in tasks]==['PG009','PL143','PO001']
    assert all(len(paths)==3 for _,paths in tasks)
    assert not issues


def test_parallel_volume_results_match_sequential(tmp_path):
    from tools.indexing.editorial_regions_experiment import completed_volumes,scan_volume
    tasks=[]
    for v in ['PG009','PL143']:
        d=tmp_path/v;d.mkdir()
        a=d/'p-1.txt';b=d/'p-2.txt'
        a.write_text(block('INDEX RERUM IN CLEMENTE')+block('Aaron, 1.'))
        b.write_text(block('Babel, 2.'))
        tasks.append((v,[a,b]))
    expected={v:scan_volume((v,paths)) for v,paths in tasks}
    assert dict(completed_volumes(tasks,2,'process'))==expected


def test_prose_index_and_catalog_items_are_not_openings():
    raw = block('index sit opus creationis absolutum esse. Ob id et')
    raw += block('ELENCHUS', 'cabecalho') + block('OPERUM QUAE IN HOC TOMO CONTINENTUR.', 'cabecalho')
    raw += block('Index Analyticus.\nIndex Rerum.')
    page, _ = analyze_page(raw, 'a', 'PG012', 8)
    assert not any(e['role'] == 'section_start' and e['kind'].endswith('index') for e in page['signals'])
    assert page['regions'][-1]['kind'] == 'work_catalog'


def test_work_opening_stops_catalog_in_same_page_and_next_page():
    first = block('ELENCHUS OPERUM QUAE IN HOC TOMO CONTINENTUR.') + block('Dialogus, 3.')
    _, state = analyze_page(first, 'a', 'PL064', 8)
    following = block('DIALOGUS PRIMUS.', 'cabecalho') + block('Hiemantis anni tempore in montibus.')
    for raw, inherited in [(following, state), (first + following, None)]:
        page, state_out = analyze_page(raw, 'b', 'PL064', 9, inherited)
        assert state_out is None
        assert page['regions'][-1]['kind'] == 'unclassified'
        assert any(t['reason'] == 'explicit_work_opening_candidate' for t in page['transitions'])


def test_finis_is_terminal_but_errata_can_have_supported_continuation():
    _, state = analyze_page(block('ERRATA') + block('Pagina 10, lege verbum.'), 'a', 'PL001', 1)
    assert state['kind'] == 'editorial_closure'
    page, state = analyze_page(block('ERRATA', 'cabecalho') + block('Pagina 11, lege aliud.'), 'b', 'PL001', 2, state)
    assert state and page['transitions'][0]['reason'] == 'compatible_running_header'
    page, state = analyze_page(block('FINIS TOMI.') + block('Other text'), 'c', 'PL001', 3, state)
    assert state is None
    assert not any(r['section_id'] for r in page['regions'])


def test_unsupported_continuation_is_recorded_not_assigned():
    _, state = analyze_page(block('INDEX RERUM') + block('Aaron, 1.'), 'a', 'PG009', 1)
    page, state = analyze_page(block('A paragraph without index structure.'), 'b', 'PG009', 2, state)
    assert state is None
    assert page['transitions'][0]['reason'] == 'continuation_not_supported'
    assert page['regions'][0]['role'] == 'uncertain'


def test_supported_long_index_has_no_arbitrary_page_cap():
    _, state = analyze_page(block('INDEX RERUM') + block('Aaron, 1.'), 'a', 'PG009', 1)
    section_id = state['section_id']
    for n in range(2, 120):
        _, state = analyze_page(block('Babel, 42.\nCaelum, 43.'), str(n), 'PG009', n, state)
        assert state['section_id'] == section_id


def test_next_section_content_cannot_support_previous_section():
    _, state = analyze_page(block('INDEX RERUM') + block('Aaron, 1.'), 'a', 'PG009', 1)
    page, _ = analyze_page(block('Prose without numbering.') + block('ORDO RERUM\nOpera 12.\nNotitia 14.'), 'b', 'PG009', 2, state)
    assert page['regions'][0]['section_id'] is None


def test_generic_index_header_supports_dense_inline_ocr_continuation():
    _, state = analyze_page(block('INDEX RERUM IN DISSERTATIONE') + block('Aaron, 1.'), 'a', 'PG009', 829)
    raw = block('1651 INDEX 1652', 'cabecalho')
    raw += block('Homo imago, 125. Hominis nobilitas, 112. Honestum bonum, 264. V. Christiani.')
    page, following = analyze_page(raw, 'b', 'PG009', 830, state)
    assert following['section_id'] == state['section_id']
    assert page['transitions'][0]['reason'] == 'generic_index_header_with_inline_references'


def test_generic_index_header_alone_does_not_preserve_thematic_region():
    _, state = analyze_page(block('INDEX RERUM') + block('Aaron, 1.'), 'a', 'PG009', 829)
    raw = block('1651 INDEX 1652', 'cabecalho') + block('A long passage without references.')
    _, following = analyze_page(raw, 'b', 'PG009', 830, state)
    assert following is None


def test_alternate_wrappers_keep_spans_and_ignore_literal_brackets():
    raw = '[cabecalho]INDEX RERUM IN OPERE[/cabecalho]\r\n'
    raw += '<texto_principal>Aaron [quoniam], 1.</texto_principal>'
    raw += '[apparato_critico]INDEX in a note[/apparato_critico]'
    page, _ = analyze_page(raw, 'a', 'PO007', 3)
    assert page['source_formats'] == ['bracket', 'xml_named']
    assert len(page['regions']) == 3
    assert page['regions'][1]['quote'] == 'Aaron [quoniam], 1.'
    for row in page['regions']:
        assert raw[slice(*row['span'])] == row['quote']
    assert page['signals'][-1]['role'] == 'mention'


def test_cache_rebuilds_downstream_state_after_source_change(tmp_path):
    from tools.indexing.editorial_regions_experiment import scan_volume
    a, b = tmp_path/'p-1.txt', tmp_path/'p-2.txt'
    a.write_text(block('INDEX RERUM') + block('Aaron, 1.'))
    b.write_text(block('Babel, 2.'))
    opts = {'cache_dir': tmp_path/'cache', 'resume': True}
    first = scan_volume(('PG009', [a,b], opts))
    assert not any(p['local_cache_hit'] for p in first)
    second = scan_volume(('PG009', [a,b], opts))
    assert all(p['local_cache_hit'] for p in second)
    a.write_text(block('Ordinary prose'))
    third = scan_volume(('PG009', [a,b], opts))
    assert [p['local_cache_hit'] for p in third] == [False, True]
    assert third[1]['regions'][0]['section_id'] is None


def test_cache_invalidates_on_detector_change_and_recovers_corruption(tmp_path, monkeypatch):
    from tools.indexing import editorial_regions_experiment as mod
    a = tmp_path/'p-1.txt'; a.write_text(block('INDEX RERUM'))
    opts = {'cache_dir': tmp_path/'cache', 'resume': True}
    mod.scan_volume(('PG009', [a], opts))
    monkeypatch.setattr(mod, 'local_fingerprint', lambda: 'different')
    assert not mod.scan_volume(('PG009', [a], opts))[0]['local_cache_hit']
    next((tmp_path/'cache').glob('*.gz')).write_bytes(b'broken')
    result = mod.scan_volume(('PG009', [a], opts))[0]
    assert not result['local_cache_hit'] and result['cache_issue']


def test_source_error_does_not_break_run_or_reuse_old_state(tmp_path):
    from tools.indexing.editorial_regions_experiment import scan_volume
    a, b = tmp_path/'p-1.txt', tmp_path/'p-3.txt'
    a.write_text(block('INDEX RERUM'))
    b.write_text(block('Babel, 2.'))
    results = scan_volume(('PG009', [a, tmp_path/'p-2.txt', b]))
    assert len(results) == 3 and results[1]['issues']
    assert results[2]['regions'][0]['section_id'] is None

"""PO lexical and ownership regressions. No claim of visual/global accuracy."""
from pathlib import Path

import pytest

from tools.indexing.entry_parser_experiment import parse_page
from tools.indexing.entry_parser_bulk import inventory

ROOT = Path(__file__).resolve().parents[1]


def parse(text, title='TABLE DES MOTS GRECS CITÉS DANS LES MSS.'):
    raw = f'<bloco tipo="cabecalho">{title}</bloco><bloco tipo="texto_principal">{text}</bloco>'
    return raw, parse_page(raw, volume='PO022', profile='po-auto-v1')


def real(volume, n):
    paths = list((ROOT/'teste'/volume/'text').glob(f'*-{n}.txt'))
    assert len(paths) == 1
    raw = paths[0].read_bytes().decode('utf-8')
    return raw, parse_page(raw, source=str(paths[0]), volume=volume, physical=n, profile='po-auto-v1')


def test_cf_material_locator_is_not_a_name_remission():
    _, p = parse('ἀνάθεμα cf. 45₁₃\nἀξίωμα 9 n. 1.\nτόμος 66 n. 3; 73 n. 1.')
    assert len(p['entries']) == 3
    first, second, third = p['entries']
    assert first['text_raw'] == 'ἀνάθεμα'
    assert first['references_raw'] == 'cf. 45₁₃'
    assert first['entry_type'] == 'material'
    assert first['locator_parts'][0]['qualifier_raw'] == 'cf.'
    assert first['locator_parts'][0]['page_raw'] == '45'
    assert first['locator_parts'][0]['subscript_raw'] == '₁₃'
    assert second['locator_parts'][0]['note_raw'].strip() == 'n. 1'
    assert [x['page_raw'] for x in third['locator_parts']] == ['66', '73']


def test_repeated_subscripts_and_open_ranges_are_not_normalized():
    _, p = parse('ܡܢܝܢ 94₁₁ 94₁₁\nܐܠܗܐ 65₇₋\nܚܦ 69_13-70_1')
    a,b,c = p['entries']
    assert len(a['locator_parts']) == 2
    assert all(x['page_raw']=='94' for x in a['locator_parts'])
    assert b['locator_parts'][0]['subscript_raw'] == '₇₋'
    assert 'open_subscript_range' in b['flags']
    assert c['locator_parts'][0]['kind'] == 'compound_locator_range'
    assert c['locator_parts'][0]['raw'] == '69_13-70_1'
    assert c['locator_parts'][0]['page_raw'] is None


def test_numeric_wraps_and_explicit_dash_parent_candidate():
    _, p = parse('ܐܒܐ 29₈ 48₁₄\n97₁₂ 102₉\n— ܐܒܪܡ 38₁\nܐܕܡ 65₁₁')
    assert len(p['entries']) == 3
    assert p['entries'][0]['references_raw'] == '29₈ 48₁₄\n97₁₂ 102₉'
    assert p['entries'][1]['context_candidate_id'] == p['entries'][0]['id']
    assert 'explicit_repetition_requires_review' in p['entries'][1]['flags']


def test_alphabetical_letters_reset_context_and_orphan_numbers_stay_residual():
    _, p = parse('12₃\nA\nα 4₃\nB\n— β 8₁')
    assert p['residuals'][0]['reason']=='orphan_locator_continuation'
    assert p['entries'][-1]['context_candidate_id'] is None
    assert 'missing_parent_context' in p['entries'][-1]['flags']


def test_parenthesized_name_remission_is_not_external_page():
    _, p = parse('Διόνυσος 98, 11 (cf. Πτολεμαῖος 11ᵃ).')
    e=p['entries'][0]
    assert e['references_raw']=='98, 11 (cf. Πτολεμαῖος 11ᵃ).'
    assert all(x['page_raw'] is None for x in e['locator_parts'])
    assert 'locator_semantics_unverified' in e['flags']


def test_bible_left_locus_and_right_locator_remain_separate():
    _, p = parse('GEN., I, 1. . . . . 32, 4\n— — 14. . . . . 31, 10', 'TABLE DES CITATIONS BIBLIQUES')
    a,b=p['entries']
    assert a['scripture_locus_raw']=='GEN., I, 1'
    assert a['references_raw']=='32, 4'
    assert a['locator_parts'][0]['page_raw'] is None
    assert b['scripture_locus_raw']=='— — 14'
    assert b['context_candidate_id']==a['id']
    assert 'scripture_context_not_expanded' in b['flags']


@pytest.mark.parametrize('title', ['TABLE ANALYTIQUE DES MATIÈRES','TABLE DES MATIÈRES','ERRATA','TABLE ALPHABÉTIQUE DES MATIÈRES'])
def test_non_index_and_calendrical_profiles_do_not_guess_targets(title):
    _, p = parse('Un récit, 123.\nMiracle . . . . . 181-186\nAaron 28 mai, vi, 78₁₄', title)
    assert not p['entries']
    assert p['residuals']


def test_ocr_generated_notes_cannot_establish_ownership_or_conventions():
    raw='<bloco tipo="texto_principal">Aaron 23₉</bloco><notas>TABLE DES NOMS PROPRES. Les subscrits sont des lignes.</notas>'
    p=parse_page(raw,volume='PO010',profile='po-auto-v1')
    assert not p['entries'] and not p['editorial_rules']


def test_midpage_boundary_and_footer_text_are_pending():
    raw=('<bloco tipo="cabecalho">TABLE DES NOMS PROPRES</bloco>'
         '<bloco tipo="texto_principal">ܐܒܐ 23₉\nERRATA\nCorrection 44</bloco>'
         '<bloco tipo="rodape">ܐܒܐ 25₉</bloco>')
    p=parse_page(raw,volume='PO022',profile='po-auto-v1')
    assert len(p['entries'])==1
    assert any(r['reason']=='excluded_editorial' for r in p['residuals'])
    assert any(r['reason']=='possible_index_text_in_nonbody_block' for r in p['residuals'])


@pytest.mark.parametrize('volume,n', [('PO022',313),('PO022',314),('PO022',315),('PO022',316),('PO022',317),('PO022',318),('PO014',858),('PO015',293),('PO015',295),('PO015',297),('PO015',299),('PO016',771),('PO016',772)])
def test_real_po_pages_exact_source_spans(volume,n):
    raw,p=real(volume,n)
    assert p['entries'] and p['import_ready'] is False
    for e in p['entries']:
        assert raw[e['char_start']:e['char_end']]==e['entry_raw']
        s=e.get('reference_span')
        if s:
            assert raw[s['char_start']:s['char_end']]==e['references_raw']
        for part in e['locator_parts']:
            assert raw[part['char_start']:part['char_end']]==part['raw']
        assert 'target_file' not in e


def test_real_po022_greek_twenty_distinct_entries_and_no_cf_concatenation():
    _,p=real('PO022',318)
    assert len(p['entries'])==20
    assert p['entries'][0]['text_raw']=='ἀνάθεμα'
    assert p['entries'][0]['references_raw']=='cf. 45₁₃'
    assert p['entries'][-1]['text_raw']=='ὑπόστασις'
    assert not p['residuals']


def test_real_po022_editorial_note_not_extrapolated():
    _,p=real('PO022',313)
    assert any('pagination placée entre crochets' in r['raw'] for r in p['editorial_rules'])
    _,p=real('PO022',318)
    assert not p['editorial_rules']  # No invented inheritance from other pages.


def test_real_po015_names_keep_all_semicolon_groups_and_no_lost_first_number():
    _,p=real('PO015',297)
    a=p['entries'][0]
    assert a['text_raw']=='Ἀαρών'
    assert a['references_raw'].startswith('28, 2;')
    assert a['references_raw'].endswith('83, 12.')
    assert len(a['locator_parts'])==6
    assert all(x['page_raw'] is None for x in a['locator_parts'])


def test_real_po016_bible_does_not_misread_verses_as_target_pages():
    _,p=real('PO016',771)
    a=p['entries'][0]
    assert a['scripture_locus_raw']=='Genèse, 1, 1-13, 14, 16'
    assert a['references_raw']=='10'


def test_real_po014_wraps_and_embedded_dates_remain_unresolved():
    _,p=real('PO014',858)
    e=next(e for e in p['entries'] if e['text_raw']=='Eugenia, deaconess and archimandritess')
    assert e['references_raw']=='443, 444.'
    e=next(e for e in p['entries'] if e['text_raw']=='Ephesus')
    assert 'unparsed_locator_tail' in e['flags']
    assert not any(x['page_raw'] in {'431','449'} for x in e['locator_parts'])


@pytest.mark.parametrize('volume,n', [('PO010',145),('PO017',885),('PO021',850),('PO014',853),('PO014',863)])
def test_real_non_supported_or_non_owned_pages_not_promoted(volume,n):
    _,p=real(volume,n)
    assert not p['entries']
    assert p['residuals']


def test_neighbor_context_recovers_running_title_pages_with_explicit_pending():
    tasks, _ = inventory(ROOT/'data/alphabetical_indices.db', ROOT/'teste', ['PO015'])
    for n in [294, 296, 298]:
        task = next(t for t in tasks if t['physical']==n)
        raw=Path(task['source']).read_text()
        p=parse_page(raw, source=task['source'], physical=n, volume='PO015',
                     profile='po-auto-v1',context=task['context'])
        assert p['entries']
        assert any(e['kind']=='ownership_context_candidate' for e in p['events'])
        assert all('ownership_inferred_from_neighbor_titles' in e['flags'] for e in p['entries'])
        assert all(c['sha256'] and Path(c['source']).is_file() for c in task['context'])


def test_neighbor_title_does_not_activate_parser_on_prose_or_override_boundary():
    context=[{'relation':'previous','source':'previous.txt','sha256':'x','titles':['TABLE DES CITATIONS BIBLIQUES']}]
    raw='<bloco tipo="texto_principal">Récit 123.\nAutre récit 4.\nConclusion 5.</bloco>'
    assert not parse_page(raw,volume='PO015',profile='po-auto-v1',context=context)['entries']
    raw='<bloco tipo="cabecalho">ERRATA</bloco><bloco tipo="texto_principal">Gen. I . . . . 10\n— II . . . . 12\n— III . . . . 15</bloco>'
    assert not parse_page(raw,volume='PO015',profile='po-auto-v1',context=context)['entries']

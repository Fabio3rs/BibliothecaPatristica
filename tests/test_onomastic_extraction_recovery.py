from tools.indexing.onomastic_extraction_recovery import parse_block, extract_page
import pytest


def parse(text):
    return parse_block(text,0,len(text),'fixture.txt')


def test_inline_homonyms_and_multiline_lists():
    text='Abraham abbas, 517, 597. — Abraham ere-\nmita, 114,\n567.\nAcacius, 803.'
    rows=parse(text)
    assert [r['owner_raw'] for r in rows]==['Abraham abbas','Abraham ere-\nmita','Acacius']
    assert [r['value'] for r in rows[1]['locators']]==[114,567]
    for r in rows:
        for n in r['locators']:
            assert text[slice(*n['span'])]==n['raw']


def test_handwritten_number_lines_not_attached():
    rows=parse('Alexandria, 711, 912.\n509.\n600.\n623.\nAmmon, 752.')
    assert [r['reference_raw'] for r in rows]==['711, 912','752']


def test_detached_fragment_does_not_steal_next_name():
    rows=parse('presbyter, 875. — Anastasius imp., 870. et seqq. — Paphnutius, 773.')
    assert [r['owner_raw'] for r in rows]==['Anastasius imp','Paphnutius']


def test_single_letter_heading_removed_from_owner():
    assert parse('N\nNechor, 296.')[0]['owner_raw']=='Nechor'


def test_numbers_missing_comma_keep_literal_space():
    rows=parse('Ammonius, 716, 733 775,\n911.')
    assert len(rows)==1
    assert [n['value'] for n in rows[0]['locators']]==[716,733,775,911]
    assert rows[0]['reference_raw']=='716, 733 775,\n911'


def test_no_number_in_owner():
    assert parse('123 incertus, 456.')==[]


def test_mixed_first_page_uses_exact_title_not_running_header(tmp_path):
    p=tmp_path/'page.txt'
    p.write_text('<bloco tipo="cabecalho">12 INDEX NOMINUM 13</bloco>'
                 '<bloco tipo="texto_principal">Wrong, 99.</bloco>'
                 '<bloco tipo="cabecalho">INDEX NOMINUM</bloco>'
                 '<bloco tipo="texto_principal">Aaron, 33.</bloco>')
    spec={'id':'fixture','volume':'PL074','opening_quote':'INDEX NOMINUM'}
    r=extract_page((str(p),684,spec,684,688))
    assert [e['owner_raw'] for e in r['entries']]==['Aaron']
    c=r['counts']
    assert c['selected_chars']==c['consumed_chars']+c['residual_chars']


def test_no_opening_from_header_alone(tmp_path):
    p=tmp_path/'page.txt';p.write_text('<bloco tipo="cabecalho">12 INDEX NOMINUM 13</bloco>')
    with pytest.raises(ValueError):
        extract_page((str(p),1,{'opening_quote':'INDEX NOMINUM'},1,2))

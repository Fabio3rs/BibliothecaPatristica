"""Regression cases transcribed literally from bounded PO source formats."""
from pathlib import Path
import json

from tools.indexing.po_named_index_grammar import parse_region_page, parse_canon_locators
from tools.indexing.unrepresented_region_recovery import valid_entry


def parse(body, family, kind='thematic_index'):
    raw='<pagina><bloco tipo="texto_principal">'+body+'</bloco></pagina>'
    result=parse_region_page(raw,source='fixture.txt',volume='PO008',physical=1,
        scopes=[{'span':[0,len(raw)],'section_id':'s','kind':kind}],
        section_context={'s':{'family':family}})
    assert all(valid_entry(raw,e) for e in result['entries'])
    return raw,result


def test_canon_is_not_page_and_book_inheritance_is_local():
    raw,result=parse('Aaron. I. 48, 77; 51, 81; II. 2, 115.\nAbdias. I. 48, 77.', 'book_canon_page')
    entries=result['entries'];assert len(entries)==2
    loc=entries[0]['editorial_locators']
    assert [x['canon_raw'] for x in loc]==['48','51','2']
    assert [x['book_raw'] for x in loc]==['I','I','II']
    assert [p['raw'] for x in loc for p in x['page_tokens']]==['77','81','115']
    assert entries[1]['editorial_locators'][0]['book_raw']=='I'
    for x in loc:
        for p in x['page_tokens']:assert raw[slice(*p['span'])]==p['raw']


def test_canon_multiline_page_list_and_intr_pages():
    _,result=parse('Ame. I. 6, 28; 42, 63;\n47, 69; II. 30, 127.\nAchelis. Intr. 4, 7.', 'book_canon_page')
    assert len(result['entries'])==2
    assert len(result['entries'][0]['editorial_locators'])==4
    intr=result['entries'][1]['editorial_locators'][0]
    assert intr['canon_raw'] is None
    assert [x['raw'] for x in intr['page_tokens']]==['4','7']


def test_canon_orphan_numbers_are_not_pages():
    parsed=parse_canon_locators('145; II. 12; vivant en 418; I. 48, 77')
    assert [x['canon_raw'] for x in parsed['locators']]==['48']
    assert len(parsed['pending'])==3


def test_named_divisions_never_become_page_numbers():
    _,result=parse('Saint Matthieu, I, 1 . . . Amulettes J. L. P\n— IX, 10-12 . . . Textes divers 11\nSaint Luc. II, 8-14 . . . Liturgie 9', 'named_document','scripture_index')
    assert len(result['entries'])==3
    assert {e['reference_namespace'] for e in result['entries']}=={'named_document'}
    assert result['entries'][1]['named_locator']['codes'][0]['raw']=='11'
    assert result['entries'][1]['scripture_context']['book_raw']=='Saint Matthieu'
    assert result['entries'][2]['scripture_context']['book_raw']=='Saint Luc'


def test_double_dash_inherits_last_book_not_last_passage():
    _,result=parse('— S. Jacques. I, 10-18 . . . Livres canoniques 9\n— — II, 19-III, 9 . . . Livres canoniques 10','named_document')
    assert result['entries'][1]['scripture_context']['book_raw']=='S. Jacques'
    assert result['entries'][1]['scripture_context']['passage_raw']=='II, 19-III, 9'


def test_lowercase_greek_entries_are_separate_rows():
    _,result=parse('ἀγάπην ποίησον 168, 33-35\nἀκολουθία τοῦ κανόνος 180, 17.\nἀνάλωμα 49, 1.', 'names_page_or_line')
    assert len(result['entries'])==3
    assert {e['reference_namespace'] for e in result['entries']}=={'page_line_or_page_list'}


def test_manuscript_shelfmarks_are_owners_and_ditto_has_evidence():
    _,result=parse('1596 . . . . 164 à 165, 174 à 177\n— . . . . 179 à 183\n232. . . . . 164₃₆','manuscripts')
    assert len(result['entries'])==3
    assert result['entries'][0]['owner_raw']=='1596'
    assert result['entries'][1]['parent_owner_evidence']['raw']=='1596'
    assert result['entries'][2]['reference_raw']=='164₃₆'


def test_scope_excludes_adjacent_table_and_headers():
    raw='<bloco tipo="cabecalho">Fake. I. 48, 77.</bloco><bloco tipo="texto_principal">Aaron. I. 48, 77.\nTABLE DES CHAPITRES\nOther. I. 3, 9.</bloco>'
    end=raw.index('TABLE DES CHAPITRES')
    result=parse_region_page(raw,source='x',volume='PO008',physical=1,
        scopes=[{'span':[0,end],'section_id':'s','kind':'thematic_index'}],
        section_context={'s':{'family':'book_canon_page'}})
    assert [e['owner_raw'] for e in result['entries']]==['Aaron']


def test_section_plan_has_nine_nonoverlapping_exact_boundaries():
    plan=json.loads(Path('docs/fixtures/index_rebuild/po_named_index_sections.json').read_text())
    assert len(plan['sections'])==9
    from tools.corpus_utils import page_files
    for s in plan['sections']:
        sources=page_files(Path('teste')/s['volume']/'text',suffixes=['.txt'])
        assert s['opening_quote'] in sources[s['first']][0].read_text()
        if s.get('end_quote'):assert s['end_quote'] in sources[s['last']][0].read_text()
        for notice in s.get('notice_quotes',[]):assert notice['quote'] in sources[notice['physical']][0].read_text()


def test_parenthesis_is_preserved_when_present_and_flagged_when_source_omits_it():
    _,result=parse('add. 14650 (A) . . . Pages. 5, 11 à 156\nadd. 14631 (B . . . 5, 14 à 138', 'manuscripts')
    assert result['entries'][0]['owner_raw']=='add. 14650 (A)'
    assert 'unbalanced_parenthesis_in_source_owner' not in result['entries'][0]['flags']
    assert result['entries'][1]['owner_raw']=='add. 14631 (B'
    assert 'unbalanced_parenthesis_in_source_owner' in result['entries'][1]['flags']


def test_manuscript_shelfmark_prefix_inheritance_does_not_cross_library_heading():
    _,result=parse('Coislin 118 . . . 169\n257. . . . 164₃₉\n2º de Paris\n284 (D . . . 6, 11 à 31', 'manuscripts')
    entry=result['entries'][1]
    assert entry['owner_raw']=='257'
    assert entry['label']=='Coislin 257'
    assert entry['manuscript_context']['shelfmark_prefix_evidence']['raw']=='Coislin'
    assert 'shelfmark_prefix_evidence' not in result['entries'][2]['manuscript_context']

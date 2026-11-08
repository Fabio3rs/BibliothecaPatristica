import hashlib
import json
import sqlite3

from tools.indexing.unrepresented_region_recovery import collect_sections, recover_volume, valid_entry, section_context


def test_selection_uses_absent_volumes_and_index_kinds(tmp_path):
    catalog=tmp_path/'catalog.sqlite'
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE items(volume TEXT)')
        db.execute("INSERT INTO items VALUES('PL001')")
    inventory=tmp_path/'regions.jsonl'
    records=[{'volume':'PL001','kind':'onomastic_index'},
             {'volume':'PL002','kind':'onomastic_index'},
             {'volume':'PL003','kind':'work_summary'}]
    inventory.write_text(''.join(json.dumps(r)+'\n' for r in records))
    assert set(collect_sections(catalog,inventory))=={'PL002'}
    assert set(collect_sections(catalog,inventory,replay_volumes=['PL001']))=={'PL001'}
    assert set(collect_sections(None,inventory,replay_volumes=['PL001']))=={'PL001'}
    # Explicit plans must not require even a readable pre-existing catalog.
    assert set(collect_sections(tmp_path/'absent.sqlite',inventory,replay_volumes=['PL001']))=={'PL001'}


def test_recovery_preserves_multiline_reference_and_literal_evidence(tmp_path):
    source=tmp_path/'index-127.txt'
    raw='<bloco tipo="texto">Abstinence 15_3,\n18_4</bloco>'
    source.write_text(raw)
    a=raw.index('Abstinence'); b=raw.index('\n'); c=raw.index('</bloco>')
    section={'section_id':'s','volume':'PO003','kind':'onomastic_index','spans':[
        {'source':str(source),'physical':127,'span':span,'source_sha256':hashlib.sha256(raw.encode()).hexdigest()}
        for span in ([a,b],[b+1,c])]}
    result=recover_volume('PO003',[section])
    assert result['entries']
    assert all(valid_entry(raw,e) for e in result['entries'])
    assert sum(p['invalid_proposals'] for p in result['pages'])==0
    assert result['entries'][0]['region_component_spans']==[[a,b],[b+1,c]]
    assert result['entries'][0]['reference_raw']=='15_3,\n18_4'


def test_reference_can_precede_owner_but_must_match_source():
    raw='I. De incarnatione'
    entry={'entry_span':[0,len(raw)],'owner_span':[3,len(raw)],'owner_raw':'De incarnatione',
           'reference_span':[0,1],'reference_raw':'I'}
    assert valid_entry(raw,entry)
    entry['owner_raw']='De Trinitate'
    assert not valid_entry(raw,entry)


def test_split_opening_headers_are_context_without_swallowing_body():
    raw = '<bloco tipo="cabecalho">ΠΙΝΑΞ</bloco><bloco tipo="cabecalho">ΚΑΤΑ ΑΛΦΑΒΗΤΟΝ</bloco><bloco tipo="texto_principal">Aaron, 12.</bloco>'
    a = raw.index('ΠΙΝΑΞ')
    section = {'kind': 'thematic_index', 'spans': [{'source': 'x', 'span': [a, a+5]}]}
    ctx = section_context(section, {'x': raw})
    assert 'ΚΑΤΑ ΑΛΦΑΒΗΤΟΝ' in ctx['text']
    assert 'Aaron' not in ctx['text']
    assert all(raw[slice(*e['span'])] == e['quote'] for e in ctx['evidence'])

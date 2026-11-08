import json
from pathlib import Path

import pytest

from tools.indexing.index_literal_extensions import (
    abbreviation_candidates, paired_candidates, paired_compatible, xref_candidates,
)
from tools.indexing.subdivision_clause_recovery import account_range, run
from test_subdivision_clause_recovery import fixture


def test_paired_slots_dashes_ranges_age_and_wrapping():
    raw='F. C.\nRaptus. Raptum qui faciunt. 147 211\nTutores. 119-125 —\nVirgo ante annos 23. .... 50 —\nRationes nisi red-\ndunt tutores. 119 —'
    assert paired_compatible(raw,raw)
    p,_=paired_candidates(raw,0,len(raw))
    assert len(p)==4
    assert [c['raw'] for c in p[1]['reference_groups'][0]['cells']]==['119-125','—']
    assert p[2]['reference_groups'][0]['owner_text_raw']=='Virgo ante annos 23.'
    assert 'red-\ndunt' in p[3]['entry_raw']
    for item in p:
        for cell in item['reference_groups'][0]['cells']:
            assert raw[cell['char_start']:cell['char_end']]==cell['raw']
    accounting=account_range(raw,0,len(raw),p)
    assert accounting['claimed_characters']+accounting['remaining_characters']==len(raw)


@pytest.mark.parametrize('row',[
    'Tractoria. 73', 'Versus. 225 — 169', 'Spectacula. Vide Theatra. — 190',
    'Fragmentum ? 2', '— 2',
])
def test_paired_abstains_for_lost_positions_mixed_xref_and_third_cell(row):
    raw='F. C.\n'+row
    assert not paired_candidates(raw,0,len(raw))[0]


def test_internal_transition_not_running_header_stops_paired_rows():
    raw='<bloco tipo="cabecalho">CONSONANTIA CAPITULORUM\nF. C.\n</bloco>\n<bloco tipo="texto_principal">\nAlpha. 10 —\nINDEX ALTER\nBeta. 11 —\n</bloco>\n<bloco tipo="texto_principal">Gamma. 12 —</bloco>'
    p,_=paired_candidates(raw,raw.index('Alpha'),len(raw))
    assert len(p)==1 and p[0]['entry_raw']=='Alpha. 10 —'
    assert not paired_candidates(raw,raw.index('Gamma'),len(raw))[0]


def test_xref_has_a_textual_target_and_no_material_destination():
    raw='Saltare. Vide Ballare.\nChalcedonense concilium, v. Concilium.'
    p,_=xref_candidates(raw,0,len(raw))
    assert len(p)==2
    g=p[0]['reference_groups'][0]
    assert g['target_raw']=='Ballare.' and g['reference_kind']=='textual_cross_reference'
    assert raw[g['target_span']['char_start']:g['target_span']['char_end']]==g['target_raw']
    assert g['target_entry_status']=='not_resolved'


@pytest.mark.parametrize('raw',['Vide Concilium.', 'Alpha. Cf. 123.', 'Alpha, v. Beta, ibid.',
                               'Alpha. Vide Beta. Vide Gamma.', 'Alpha. Vide Beta',
                               'Nicæna fides, lxix. Vide Cyrillus.',
                               'Manichæus v. Manes : cf. Heraclianus.'])
def test_incomplete_or_mixed_xrefs_not_claimed(raw):
    assert not xref_candidates(raw,0,len(raw))[0]


def test_ibid_preserves_dependency_without_copying_previous_number():
    raw='Josephus, 52 b. Nomen mutavit, ibid. Imperium administrat, ib. a.'
    p,_=abbreviation_candidates(raw,0,len(raw))
    assert [x['reference_groups'][0]['raw'] for x in p]==['ibid.','ib. a.']
    for item in p:
        assert item['reference_groups'][0]['context_dependency']['target'] is None


@pytest.mark.parametrize('raw',['Alpha, 42. Beta, ibid.', 'Alpha\n\nBeta, ibid.',
                               'Alpha, ibid., 12 a.', 'Alpha, cod. ibid.'])
def test_unsafe_ibid_contexts_remain_residual(raw):
    assert not abbreviation_candidates(raw,0,len(raw))[0]


def test_existing_claims_protected_and_hash_checked(tmp_path):
    source,db=fixture(tmp_path)
    run(db,tmp_path/'first')
    claims=tmp_path/'first/proposals.jsonl'
    result=run(db,tmp_path/'second',exclude_proposals=[claims])
    assert result['proposed_clauses']==0
    assert result['rejection_reasons']['overlap_with_protected_proposal']==3
    item=json.loads(claims.read_text().splitlines()[0]);item['source_sha256']='invalid'
    claims.write_text(json.dumps(item)+'\n')
    with pytest.raises(ValueError,match='incompatible source hash'):
        run(db,tmp_path/'third',exclude_proposals=[claims])


def test_documented_editorial_quotes_are_traceable_to_current_source():
    root=Path(__file__).resolve().parents[1]
    from hashlib import sha256
    evidence=json.loads((root/'docs/data/index_editorial_conventions_20261105.json').read_text())
    for item in evidence['records']:
        data=(root/item['ocr_path']).read_bytes()
        # Corpus OCR is mutable: check frozen spans only for the documented version.
        if sha256(data).hexdigest()!=item['ocr_sha256']:
            continue
        assert data.decode()[item['char_start']:item['char_end']]==item['quote_raw']
        assert item['applicability']['volume_wide'] is False

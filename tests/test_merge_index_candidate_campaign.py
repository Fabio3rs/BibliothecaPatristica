import json
import pytest

from scripts.merge_index_candidate_campaign import merge


def campaign(path, volumes):
    path.mkdir()
    (path/'config.json').write_text('{}')
    for volume, marker in volumes.items():
        d = path/'volumes'/volume; d.mkdir(parents=True)
        (d/'summary.json').write_text(json.dumps({'volume':volume,'status':'completed','marker':marker}))


def test_merge_reuses_immutable_other_volumes_and_explicit_override(tmp_path, monkeypatch):
    from tools.indexing import global_target_survey
    campaign(tmp_path/'base', {'PG001':'old','PG002':'kept'})
    campaign(tmp_path/'patch', {'PG001':'new'})
    monkeypatch.setattr(global_target_survey,'aggregate',lambda out,config,summaries: summaries)
    rows = merge('survey',tmp_path/'base',tmp_path/'patch',tmp_path/'out')
    assert [r['marker'] for r in rows] == ['new','kept']
    assert (tmp_path/'out/volumes/PG002').resolve() == tmp_path/'base/volumes/PG002'
    assert json.loads((tmp_path/'base/volumes/PG001/summary.json').read_text())['marker'] == 'old'
    assert json.loads((tmp_path/'out/config.json').read_text())['overridden_volumes'] == ['PG001']


def test_merge_refuses_new_volume_before_creating_output(tmp_path):
    campaign(tmp_path/'base', {'PG001':'old'})
    campaign(tmp_path/'patch', {'PG002':'new'})
    with pytest.raises(ValueError,match='outside baseline'):
        merge('survey',tmp_path/'base',tmp_path/'patch',tmp_path/'out')
    assert not (tmp_path/'out').exists()

import json

import pytest

from scripts.experiment_pl058_victor_maps import NAMES, consolidate, compare_campaigns


def write_campaign(tmp_path, fixtures):
    for name in NAMES:
        directory = tmp_path / name
        directory.mkdir()
        (directory/'summary.json').write_text('{}')
        (directory/'links.json').write_text(json.dumps(fixtures.get(name, [])))


def link(number=55, status='bounded_window_candidate', physical=139):
    return {'occurrence': {'id': 'one', 'number': number, 'group_id': 'group',
                           'source_sha256': 'abc'},
            'owner_raw': 'Felix', 'status': status,
            'map': {'number': number, 'files': [{'file': f'page-{physical}', 'physical': physical}]}}


def test_duplicated_number_keeps_alternatives_without_double_counting(tmp_path):
    write_campaign(tmp_path, {'passio': [link(physical=135)], 'notitia': [link(physical=139)]})
    summary = consolidate(tmp_path)
    rows = json.loads((tmp_path/'combined_links.json').read_text())
    assert summary['source_occurrences'] == 1
    assert summary['duplicated_number_occurrences_with_multiple_bounded_branches'] == 1
    assert len(rows[0]['candidate_files']) == 2
    assert rows[0]['status'] == 'duplicated_number_alternatives_pending'
    assert not rows[0]['verified'] and not rows[0]['import_ready']


def test_single_bounded_branch_does_not_certify_duplicate_unique(tmp_path):
    write_campaign(tmp_path, {'notitia': [link()]})
    consolidate(tmp_path)
    row = json.loads((tmp_path/'combined_links.json').read_text())[0]
    assert len(row['candidate_files']) == 1
    assert row['status'] == 'duplicated_number_alternatives_pending'


@pytest.mark.parametrize('number', [55, 54, 92, 400])
def test_stale_source_overrides_numeric_and_gap_classification(tmp_path, number):
    stale = link(number=number, status='stale_index_extraction')
    stale['map'] = None
    write_campaign(tmp_path, {'passio': [link(number=number)], 'notitia': [stale]})
    consolidate(tmp_path)
    row = json.loads((tmp_path/'combined_links.json').read_text())[0]
    assert row['status'] == 'stale_index_extraction'
    assert not row['candidate_files']
    assert not row['source_evidence_valid']


def test_different_source_snapshots_are_rejected(tmp_path):
    changed = link()
    changed['occurrence']['source_sha256'] = 'changed'
    write_campaign(tmp_path, {'passio': [link()], 'notitia': [changed]})
    with pytest.raises(AssertionError, match='Incompatible source snapshots'):
        consolidate(tmp_path)


def test_campaign_comparison_counts_gains_without_certifying_duplicates(tmp_path):
    old, new = tmp_path/'old', tmp_path/'new'
    old.mkdir()
    new.mkdir()
    pending = link(number=60)
    pending['map'] = None
    write_campaign(old, {'notitia': [pending]})
    write_campaign(new, {'notitia': [link(number=60)]})
    consolidate(old)
    consolidate(new)
    result = compare_campaigns(old, new)
    assert result['comparable_occurrences'] == 1
    assert result['gained_candidate_window'] == 1
    assert result['lost_candidate_window'] == 0
    assert result['changes'][0]['after_status'] == 'duplicated_number_alternatives_pending'
    rows = json.loads((new/'combined_links.json').read_text())
    rows[0]['occurrence']['source_sha256'] = 'new-source'
    (new/'combined_links.json').write_text(json.dumps(rows))
    result = compare_campaigns(old, new)
    assert result['comparable_occurrences'] == 0
    assert result['gained_candidate_window'] == 0
    assert result['changed_sources'] == ['one']

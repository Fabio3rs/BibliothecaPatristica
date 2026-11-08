import json

import pytest

from tools.indexing.entry_parser_experiment import digest
from tools.indexing.source_quality_issues import VERSION, annotate, inspect_registry


def registry(tmp_path):
    page={'volume':'PG999','physical':1,'issues':[{'id':'issue-1','kind':'missing_printed_entries',
          'visual_location':'left column','description':'Missing printed entry',
          'reviewer':'assistant','recovery_status':'pending'}]}
    for field in ('source','image','review_report'):
        file=tmp_path/field;file.write_bytes(field.encode())
        page[field]=field;page[field+'_sha256']=digest(file.read_bytes())
    manifest=tmp_path/'issues.json'
    manifest.write_text(json.dumps({'version':VERSION,'pages':[page]}))
    return manifest,page


def test_omission_is_independent_of_processing_and_does_not_change_source(tmp_path):
    manifest,page=registry(tmp_path)
    result=inspect_registry(manifest,tmp_path)[0]
    assert result['source_completeness']=='known_omission'
    assert result['parser_execution']=='independent_not_assessed'
    assert not result['automatic_repair_allowed']
    assert annotate(page,result)['source_issue_status']=='known_defects'
    assert (tmp_path/'source').read_bytes()==b'source'


@pytest.mark.parametrize('artifact',['source','image','review_report'])
def test_changed_evidence_requires_recheck_not_automatic_resolution(tmp_path,artifact):
    manifest,page=registry(tmp_path)
    (tmp_path/artifact).write_bytes(b'corrected')
    result=inspect_registry(manifest,tmp_path)[0]
    assert result['evidence_status']=='stale_requires_recheck'
    assert result['source_completeness']=='requires_recheck'
    assert annotate(page,result)['source_issue_status']=='stale_requires_recheck'
    assert result['issues'][0]['recovery_status']=='pending'


def test_unlisted_source_is_not_certified_and_historical_hash_does_not_inherit_review(tmp_path):
    manifest,page=registry(tmp_path)
    result=inspect_registry(manifest,tmp_path)[0]
    assert annotate(page,None)['source_issue_status']=='not_listed_not_certified'
    page['source_sha256']='old'
    assert annotate(page,result)['source_issue_status']=='stale_requires_recheck'


def test_duplicate_issue_ids_rejected(tmp_path):
    manifest,page=registry(tmp_path)
    page['issues']*=2
    manifest.write_text(json.dumps({'version':VERSION,'pages':[page]}))
    with pytest.raises(ValueError,match='Duplicate issue'):
        inspect_registry(manifest,tmp_path)

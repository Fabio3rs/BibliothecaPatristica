import hashlib
import json
from pathlib import Path

from tools.indexing.repaired_candidate_localization import LocalizationContext, localize_pending_item, localize_volume


def checksum(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture_context(tmp_path, source_text='Alpha 12, 34', targets=((12, True), (34, False))):
    volume = 'PG001'
    folder = tmp_path / 'survey' / 'volumes' / volume
    folder.mkdir(parents=True)
    source = tmp_path / 'index-100.txt'
    source.write_text(source_text)
    markers = []
    for index, (number, supported) in enumerate(targets, 1):
        target = tmp_path / f'target-{index:03}.txt'
        literal = str(number)
        target.write_text(literal)
        markers.append({'path': str(target), 'number': number, 'physical': index,
                        'sha256': checksum(literal), 'raw': literal, 'char_start': 0,
                        'char_end': len(literal), 'sequence_support': supported})
    (folder / 'header_markers.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in markers))
    (folder / 'inventory.jsonl').write_text('')
    (folder / 'profiles.json').write_text('[]')
    (folder / 'sources.jsonl').write_text(json.dumps({'source': str(source)})+'\n')
    context = LocalizationContext(tmp_path / 'survey', volume, tmp_path)
    item = {'id': 'item', 'volume': volume, 'source': str(source), 'label': 'Alpha',
            'ref_raw': '12, 34', 'metadata': {'source_span': [6, 12], 'source_sha256': checksum(source_text)}}
    return context, item, markers


def test_numeric_list_retains_alternatives_and_flags_approximation(tmp_path):
    context, item, markers = fixture_context(tmp_path, targets=((12, True), (12, False), (34, False)))
    result = localize_pending_item(item, context=context)
    assert len(result['candidates']) == 3
    assert result['status'] == 'candidate'
    assert {r['source_span'][0] for r in result['provenance']['references']} == {6, 10}
    for candidate in result['candidates']:
        assert candidate['navigable'] and not candidate['certified']
        assert 'editorial_namespace_unconfirmed' in candidate['uncertainties']
        assert 'approximate_header_candidate' in candidate['uncertainties']
        assert Path(candidate['file']).is_file()


def test_child_only_searches_its_exact_reference_span(tmp_path):
    context, item, markers = fixture_context(tmp_path)
    item['ref_raw'] = '34'
    item['metadata'].update(source_span=[10, 12], entry={'entry_span': [0, 12], 'reference_span': [6, 12]})
    result = localize_pending_item(item, context=context)
    assert [r['number'] for r in result['provenance']['references']] == [34]
    assert [c['file'] for c in result['candidates']] == [markers[1]['path']]


def test_roman_context_keeps_numeric_child_and_never_binds_tome(tmp_path):
    raw = 'Alpha V, 12, 34'
    context, item, markers = fixture_context(tmp_path, raw)
    item['ref_raw'] = 'V, 34'
    item['metadata'].update(source_span=[13, 15], reference_display_is_contextualized=True,
        original_item={'ref_raw': '34'}, parser_result={'roman_raw': 'V',
        'expanded_reference_span': [6, 15], 'expanded_reference_raw': 'V, 12, 34',
        'corrected_owner_span': [0, 5]})
    result = localize_pending_item(item, context=context)
    refs = result['provenance']['references']
    assert len(refs) == 1 and refs[0]['number'] == 34
    assert refs[0]['source_span'] == [13, 15] and refs[0]['roman_context'] == ['V']
    assert refs[0]['namespace_status'] == 'unmapped' and 'tome' not in refs[0]
    assert 'roman_context_preserved_namespace_unmapped' in result['candidates'][0]['uncertainties']


def test_invalid_roman_span_and_stale_source_abstain(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    item['metadata']['parser_result'] = {'roman_raw': 'V', 'expanded_reference_span': [0, 12], 'expanded_reference_raw': 'V, 12, 34'}
    assert localize_pending_item(item, context=context)['abstention'] == 'roman_context_span_invalid'
    item['metadata']['source_sha256'] = 'outdated'
    assert localize_pending_item(item, context=context)['abstention'] == 'roman_context_span_invalid'


def test_missing_and_stale_targets_are_not_navigable(tmp_path):
    context, item, markers = fixture_context(tmp_path)
    Path(markers[0]['path']).unlink()
    Path(markers[1]['path']).write_text('different text')
    result = localize_pending_item(item, context=context)
    assert not result['candidates']
    assert result['abstention'] == 'no_existing_target_candidate'
    assert context.metrics['unavailable_targets'] == context.metrics['invalid_marker_spans'] == 1


def test_historical_snapshot_preserves_each_proposal_coordinates(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    item['ref_raw'] = '34'
    proposal = {'id': 'historical-a', 'version_id': 7,
                'reference': {'quote': '34', 'span': [96, 98]},
                'entry': {'quote': 'Alpha 34', 'span': [90, 98]}}
    item['metadata'].update(source_span=None, reference_coordinate_space='historical_snapshot',
                            reconciliation={'proposals': [proposal, {**proposal, 'id': 'historical-b', 'version_id': 8}]})
    result = localize_pending_item(item, context=context)
    assert len(result['candidates']) == 1
    supports = result['candidates'][0]['evidence']['localization_support']
    assert {s['reference']['version_id'] for s in supports} == {7, 8}
    assert all(s['reference']['source_span'] == [96, 98] for s in supports)
    assert all(s['reference']['coordinate_space'] == 'historical_snapshot' for s in supports)


def test_historical_child_outside_literal_entry_is_rejected(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    item['metadata'].update(reference_coordinate_space='historical_snapshot', reconciliation={'proposals': [
        {'reference': {'quote': '34', 'span': [98, 100]}, 'entry': {'quote': 'Alpha 34', 'span': [90, 98]}}]})
    assert localize_pending_item(item, context=context)['abstention'] == 'historical_reference_span_invalid'


def test_scoped_map_precedence_and_incompatible_roman_fallback(tmp_path, monkeypatch):
    context, item, markers = fixture_context(tmp_path)
    profile = {'path': 'qualified-profile', 'profile': {'notation': 'plain_page_list'}}
    monkeypatch.setattr(context, 'profiles_for', lambda physical: [profile])
    monkeypatch.setattr('tools.indexing.repaired_candidate_localization.scoped_location',
                        lambda ref, mapping: {'status': 'bounded', 'files': [markers[0]] if ref['number'] == 12 else []})
    result = localize_pending_item(item, context=context)
    assert result['candidates'][0]['score'] == 40
    assert result['candidates'][0]['evidence']['localization_support'][0]['tier'] == 'documented_profile'
    assert len(result['candidates']) == 2
    item['metadata']['source_span'] = [6, 12]
    # A plain-page map cannot silently erase a Roman qualifier.
    Path(item['source']).write_text('Alpha V, 34')
    context._sources.clear()
    item['ref_raw'] = 'V, 34'
    item['metadata'].update(source_span=[6, 11], source_sha256=checksum('Alpha V, 34'))
    result = localize_pending_item(item, context=context)
    assert result['provenance']['profile_attempts'][0]['status'] == 'notation_incompatible'
    assert result['candidates'][0]['evidence']['localization_support'][0]['tier'] == 'unmapped_namespace_screen'


def test_batch_accepts_sqlite_metadata_json_without_writes(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    item['metadata_json'] = json.dumps(item.pop('metadata'))
    result = localize_volume([item], survey_dir=context.survey_dir, volume='PG001', corpus_root=tmp_path)
    assert result['summary']['items_with_candidates'] == 1
    assert result['summary']['database_writes'] == result['summary']['inference_calls'] == 0


def test_existing_visual_transcription_uses_its_own_coordinates(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    item['ref_raw'] = 'I, 34'
    item['metadata'].update(source_span=None, reference_coordinate_space='visual_transcription',
        reconciliation={'prior_review': {'proposal_id': 'visual-1',
        'visual_transcription': {'literal': 'Alpha I, 34'},
        'reference_groups': [{'char_start': 6, 'char_end': 11, 'raw': 'I, 34'}]}, 'proposals': []})
    result = localize_pending_item(item, context=context)
    assert len(result['candidates']) == 1
    ref = result['provenance']['references'][0]
    assert ref['source_span'] == [9, 11]
    assert ref['coordinate_space'] == 'visual_transcription'
    assert ref['roman_context'] == ['I'] and ref['proposal_id'] == 'visual-1'


def test_global_hash_change_keeps_exact_local_reference_and_owner(tmp_path):
    context, item, markers = fixture_context(tmp_path)
    Path(item['source']).write_text('Alpha 12, 34\nUnrelated corrected paragraph')
    Path(markers[0]['path']).write_text('12\nCorrected body')
    result = localize_pending_item(item, context=context)
    assert len(result['candidates']) == 2
    assert all(r['source_evidence_status'] == 'source_changed_local_evidence_matches'
               for r in result['provenance']['references'])
    changed = next(c for c in result['candidates'] if c['file'] == markers[0]['path'])
    assert changed['evidence']['source_evidence_status'] == 'source_changed_local_evidence_matches'
    assert changed['evidence']['observed_sha256'] != changed['evidence']['sha256']


def test_changed_hash_does_not_allow_wrong_reference_owner(tmp_path):
    context, item, _ = fixture_context(tmp_path)
    Path(item['source']).write_text('Other 12, 34')
    result = localize_pending_item(item, context=context)
    assert result['abstention'] == 'source_changed_owner_context_unconfirmed'
    assert not result['candidates']


def test_changed_target_without_exact_marker_span_abstains(tmp_path):
    context, item, markers = fixture_context(tmp_path)
    target = {'file': markers[0]['path'], 'sha256': markers[0]['sha256']}
    Path(target['file']).write_text('12 Updated text')
    assert not context.valid_target(target)
    assert context.metrics['stale_targets_without_local_evidence'] == 1


def test_known_internal_numbers_are_not_mistaken_for_header_pages(tmp_path):
    context,item,_=fixture_context(tmp_path)
    for namespace in ('logion','chapter','chapter_number','internal_text_number','namespace_conflict'):
        item['metadata']['reference_namespace']=namespace
        result=localize_pending_item(item,context=context)
        assert not result['candidates']
        assert result['abstention']=='requires_namespace_map:'+namespace


def test_page_line_searches_page_only_and_preserves_literal_coordinates(tmp_path):
    context,item,markers=fixture_context(tmp_path,'Alpha 12_34')
    item['ref_raw']='12_34'
    item['metadata'].update(source_span=[6,11],reference_namespace='page_line',
                            page_line_tokens=[{'page_raw':'12','line_raw':'34','span':[6,11]}])
    result=localize_pending_item(item,context=context)
    assert [c['file'] for c in result['candidates']]==[markers[0]['path']]
    assert result['provenance']['references'][0]['source_span']==[6,8]
    item['metadata']['page_line_tokens'][0]['page_raw']='34'
    assert localize_pending_item(item,context=context)['abstention']=='page_line_tokens_missing_or_invalid'


def test_explicit_comma_line_and_note_tokens_only_locate_the_page(tmp_path):
    from tools.indexing.missing_po_region_parser import extract_page_line_tokens
    context,item,markers=fixture_context(tmp_path,'Alpha 12,34')
    item['ref_raw']='12,34'
    item['metadata'].update(source_span=[6,11],reference_namespace='page_line',
        page_line_tokens=extract_page_line_tokens('12,34',base_offset=6)['tokens'])
    result=localize_pending_item(item,context=context)
    assert [c['file'] for c in result['candidates']]==[markers[0]['path']]
    assert result['provenance']['references'][0]['printed_page_line']=='12,34'
    # A note number that also appears as a page header must never become a target.
    Path(item['source']).write_text('Alpha 12 n. 34'); context._sources.clear()
    item['ref_raw']='12 n. 34'
    item['metadata'].update(source_span=[6,14],source_sha256=checksum('Alpha 12 n. 34'),
        page_line_tokens=extract_page_line_tokens('12 n. 34',base_offset=6)['tokens'])
    assert [c['file'] for c in localize_pending_item(item,context=context)['candidates']]==[markers[0]['path']]

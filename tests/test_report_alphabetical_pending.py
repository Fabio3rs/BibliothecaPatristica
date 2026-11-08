from scripts.report_alphabetical_pending import identity, material, page, source, summarize
from scripts.validate_index_jev import encoded, sha


def test_log_identity_matches_existing_classifier():
    entry = {'lemma_raw': 'Ἀπόλλων', 'entry_raw': 'Ἀπόλλων, 15.'}
    ref = {'ref_raw': '15'}
    assert identity(entry, ref) == sha(encoded({**entry, **ref}))
    assert identity(entry, ref) != identity(entry, {'ref_raw': '16'})


def test_scripture_and_source_only_are_not_pending_material():
    section = dict(pipeline_owner='alphabetical', alphabetical_role='owned_section',
                   material_reference_mode='remissive')
    ref = dict(scripture_ref_order=None, ref_kind='editorial_page')
    assert material(ref, section)
    assert not material({**ref, 'ref_kind': 'scripture'}, section)
    assert not material({**ref, 'scripture_ref_order': 0}, section)
    assert not material(ref, {**section, 'material_reference_mode': 'source_only'})


def test_unknown_entry_source_does_not_become_section_start():
    entry = dict(index_entry_source_ocr_file=None, raw_json='{}', section_start_file='index-462.txt')
    assert source(entry) == ('', 'unknown_entry_page_section_range_only')
    assert page('/path/PG110-660.png') == 660
    assert page(None) is None


def test_distinguish_entries_from_references_and_partial_resolution():
    rows = [dict(entry_key='a', pending=True, locator_status='unresolved', diagnosis='x'),
            dict(entry_key='a', pending=True, locator_status='unverified', diagnosis='x'),
            dict(entry_key='a', pending=False, locator_status='resolved', diagnosis='resolved')]
    result = summarize(rows)
    assert result['pending_references'] == 2
    assert result['entries_with_pending'] == 1
    assert result['resolved_references'] == 1
    assert result['pending_statuses'] == {'unresolved': 1, 'unverified': 1}

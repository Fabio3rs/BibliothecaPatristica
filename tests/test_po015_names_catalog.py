import json

import pytest

from scripts.catalog_po015_names import groups, parse_group, run, search_name


def test_line_list_is_not_a_page_list():
    r = parse_group('98, 11, 12.')
    assert r['page'] == 98 and r['lines'] == [11, 12]
    assert parse_group('33, 14, 34, 8')['status'] == 'notation_pending'
    assert parse_group('98-99, 2')['status'] == 'notation_pending'


def test_remission_ordinals_are_preserved_outside_material_numbers():
    r = parse_group('98, 11, 12 (cf. Πτολεμαῖος 9ᵃ).')
    assert r['page'] == 98 and r['lines'] == [11, 12]
    assert '9ᵃ' in r['annotation_raw']
    r = parse_group('98, 7 cf. Πτολεμαῖος 1°).')
    assert r['lines'] == [7] and '1°' in r['annotation_raw']
    assert parse_group('cf. 99, 2, 4, 6.')['page'] == 99


def test_printed_separator_variants_keep_page_and_line():
    assert parse_group('74. 10')['page'] == 74
    assert parse_group('79 10.')['lines'] == [10]
    assert parse_group('66, 10 (Χ. Κυρίου)')['lines'] == [10]


def test_occurrence_spans_preserve_repeated_groups_and_newlines():
    raw = 'ignored 28, 2;\n 28, 2.'
    result = list(groups({'source': 'x', 'char_start': 8, 'char_end': len(raw)}, raw))
    assert len(result) == 2
    assert result[0]['char_start'] != result[1]['char_start']
    for r in result:
        assert raw[r['char_start']:r['char_end']] == r['ref_raw']
        assert r['page'] == 28


def test_stale_review_blocks_export_before_output_creation(tmp_path):
    (tmp_path / 'ocr.txt').write_text('changed')
    review = tmp_path / 'review.json'
    review.write_text(json.dumps({'sources': {'1': {'path': 'ocr.txt', 'sha256': 'old'}}, 'images': {}}))
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match='Reviewed source changed'):
        run(review, out, tmp_path)
    assert not out.exists()


def test_normalization_is_a_separate_search_field():
    assert search_name('Ἀβραάμ') == 'αβρααμ'


def test_uncertain_lines_still_offer_literal_page_candidate():
    raw = '33,14,34,8'
    ref, = groups({'source':'x','char_start':0,'char_end':len(raw)}, raw)
    assert ref['page'] == 33 and ref['lines'] == []
    assert ref['status'] == 'page_candidate_detail_pending'
    assert 'ambiguous_line_list_or_missing_page_separator' in ref['uncertainties']

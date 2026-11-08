"""Conservative metadata recovery and explicit offline visual-review annotations."""
from copy import deepcopy

import pytest
from test_multimodal_reextraction import make_sources, reading_for
from tools.indexing.multimodal.sources import prepare_task
from tools.indexing.multimodal.reading import convert
from tools.indexing.multimodal.validation import validate
from tools.indexing.multimodal.review import reviewed_response
from tools.indexing.multimodal.contract import digest


def setup_reading(tmp_path):
    root, path = make_sources(tmp_path)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    return root, path, task, reading_for(task)


@pytest.mark.parametrize('header,expected', [
    ('1213\n1214', True), ('1213\n1213\n1214', False), ('1213\n1215', False),
])
def test_discontiguous_header_labels(tmp_path, header, expected):
    root, path, _, reading = setup_reading(tmp_path)
    path.write_text(path.read_text().replace('<pagina>', '<pagina>\n<bloco tipo="cabecalho">'+header+'</bloco>'))
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    reading['sections'][0]['index_page'] = '1213-1214'
    dto, alignment = convert(reading, task)
    assert validate(dto, task)['valid']
    if expected:
        assert dto['sections'][0]['index_page_raw'] == '1213\n1214'
        assert alignment['complete'], alignment
        assert alignment['mechanical_annotations'][0]['labels'] == ['1213', '1214']
        assert len(dto['references']) == 2
    else:
        assert dto['sections'][0]['index_page_raw'] is None
        assert not alignment['complete']


def test_header_labels_not_borrowed_from_body(tmp_path):
    _, _, task, reading = setup_reading(tmp_path)
    reading['sections'][0]['index_page'] = '12-770'
    dto, alignment = convert(reading, task)
    assert dto['sections'][0]['index_page_raw'] is None
    assert not alignment['complete']


@pytest.mark.parametrize('letter,names,expected', [
    ('N', ('Naisus','Napresi'), True), ('N', ('Naisus','Roma'), False),
    ('I', ('Iona','Iacob'), False), ('R', ('Roma','Romani'), True),
])
def test_divider_requires_supported_alphabetical_group(tmp_path, letter, names, expected):
    root, path = make_sources(tmp_path, text=f'INDEX\n{names[0]}, 12.\n{names[1]}, 13.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    reading = reading_for(task)
    path.write_text(path.read_text().replace('INDEX\n', 'INDEX\n'+letter+'\n'))
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    dto, alignment = convert(reading, task)
    assert validate(dto, task)['valid']
    dividers = [m for m in alignment['mechanical_annotations'] if m['kind'] == 'alphabet_divider']
    assert bool(dividers) == expected
    assert alignment['complete'] == expected
    assert len(dto['entries']) == 2 and len(dto['references']) == 2


def review_fixture(tmp_path):
    _, _, task, reading = setup_reading(tmp_path)
    response = {'status': 'received', 'dto': reading}
    review = {'version': 1, 'task_id': task['task_id'], 'source_response_sha256': digest(response),
        'ocr_sha256': task['pages'][0]['ocr_sha256'], 'image_sha256': task['pages'][0]['regions'][0]['sha256'],
        'reviewer': 'test reviewer', 'basis': 'assistant_visual_inspection',
        'visual_evidence': [{'crop_box': [0, 0, 100, 160]}],
        'operations': [{'path': '/sections/0/entries/0/continuation', 'before': 'none', 'after': 'trailing',
                        'reason': 'Fixture annotation only'}]}
    return task, response, review


def test_review_is_explicit_and_preserves_response(tmp_path):
    task, response, review = review_fixture(tmp_path)
    original = deepcopy(response)
    derived = reviewed_response(response, task, digest(response), review)
    assert response == original
    assert derived['dto']['sections'][0]['entries'][0]['continuation'] == 'trailing'
    assert derived['offline_review']['source_response_sha256'] == digest(original)


@pytest.mark.parametrize('field', ['source_response_sha256', 'ocr_sha256', 'image_sha256'])
def test_review_rejects_stale_evidence(tmp_path, field):
    task, response, review = review_fixture(tmp_path)
    review[field] = 'changed'
    with pytest.raises(ValueError, match='hash mismatch'):
        reviewed_response(response, task, digest(response), review)


def test_review_cannot_change_reference_numbers(tmp_path):
    task, response, review = review_fixture(tmp_path)
    review['operations'][0]['path'] = '/sections/0/entries/0/references/0/page_start'
    with pytest.raises(ValueError, match='unsupported review'):
        reviewed_response(response, task, digest(response), review)


def test_review_requires_precondition_and_measured_crop(tmp_path):
    task, response, review = review_fixture(tmp_path)
    review['operations'][0]['before'] = 'wrong'
    with pytest.raises(ValueError, match='precondition'):
        reviewed_response(response, task, digest(response), review)
    review['visual_evidence'][0]['crop_box'] = [0,0,1000,1000]
    with pytest.raises(ValueError, match='crop outside'):
        reviewed_response(response, task, digest(response), review)


def test_remaining_pilot_excludes_reviewed_page():
    from scripts.reextract_alphabetical_multimodal import parser, selections, exclude_pages
    args = parser().parse_args(['--pilot', 'problematic-12', '--exclude-page', 'PG113:653', '--out', '/tmp/test-pilot'])
    selected = exclude_pages(selections(args), args.exclude_page)
    assert len(selected) == 11
    assert ('PG113', 653) not in selected
    with pytest.raises(ValueError, match='current selection'):
        exclude_pages(selected, ['PG113:653'])
    with pytest.raises(ValueError, match='requires VOLUME'):
        exclude_pages(selected, ['PG113/653'])
    with pytest.raises(ValueError, match='No pages remain'):
        exclude_pages([('PG113', 653)], ['PG113:653'])

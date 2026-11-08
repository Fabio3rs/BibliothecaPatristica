"""Chapter navigation regressions: editorial namespaces, intervals and evidence."""
from pathlib import Path

import pytest

from tools.indexing.arnobius_book_targets import build_map, chapter_markers, locate, diagnose

ROOT = Path(__file__).resolve().parents[1]


def entry(*pairs, **extra):
    return dict(volume='PL005', reference_namespace='book_chapter',
                locators=[dict(roman_value=b, arabic_value=c, raw=f'{b}, {c}') for b, c in pairs], **extra)


def xml(text, kind='texto_principal'):
    return f'<pagina><bloco tipo="{kind}">{text}</bloco></pagina>'


def test_primary_parallel_and_commentary_are_distinct():
    text = xml('XXXVI. (Oberth. XXXIX.) Ventum est ergo.\nXXXVII. Prose.') + xml('COMMENTARIUS.') + xml('XXXVIII. This is commentary.')
    got = list(chapter_markers(text))
    assert [x['chapter'] for x in got] == [36, 37]
    assert got[0]['parallel_raw'] == '(Oberth. XXXIX.)'
    assert all(text[x['span'][0]:x['span'][1]] == x['raw'] for x in got)


def test_note_lemma_and_malformed_roman_are_not_anchors():
    text = xml('XIIII. Invalid.\nXX. — Commentary lemma.\nCap. XXI. Citation.\n1. Actual start.') + xml('II. Apparatus.', 'aparato_critico')
    assert [x['chapter'] for x in chapter_markers(text)] == [1]


def test_spaced_parallel_label_retains_primary_number():
    assert [x['chapter'] for x in chapter_markers(xml('XXXVI. ( Oberth. XXXIX. ) Ventum est ergo.'))] == [36]


def test_book_boundary_resets_commentary_cutoff():
    text = xml('COMMENTARIUS.\nXX. Old lemma.\nINDEX CAPITUM.\n1. New book.\nCOMMENTARIUS.\nII. Note.')
    first = text.index('INDEX CAPITUM.') + len('INDEX CAPITUM.')
    assert [x['chapter'] for x in chapter_markers(text, first)] == [1]


@pytest.fixture(scope='module')
def real_map():
    if not (ROOT / 'teste/PL005/text').is_dir():
        pytest.skip('PL005 corpus unavailable')
    return build_map(ROOT)


def test_real_book_boundaries_and_spans(real_map):
    assert [real_map['books'][str(n)]['opening']['physical'] for n in range(1, 8)] == [361, 409, 473, 506, 544, 585, 613]
    assert real_map['books']['7']['end']['physical'] == 650
    for section in real_map['books'].values():
        numbers = [a['chapter'] for a in section['anchors']]
        assert numbers == sorted(set(numbers))
        assert numbers[0] == 1
        for anchor in section['anchors']:
            text = Path(anchor['file']).read_text()
            assert text[slice(*anchor['span'])] == anchor['raw']


def test_chapter_continuation_retains_physical_files(real_map):
    candidates = locate(entry((1, 1)), real_map)
    assert {c['physical'] for c in candidates} >= {363, 364, 365}
    assert max(c['score'] for c in candidates if c['physical'] == 363) == .93
    assert all(c['physical'] != 1 for c in candidates)


def test_bounded_missing_chapter_has_evidence(real_map):
    candidates = locate(entry((1, 2)), real_map)
    assert candidates
    assert {c['physical'] for c in candidates} >= {364, 365}
    assert all(c['score'] == .54 for c in candidates)
    assert all(c['evidence'][0]['kind'] == 'bounded_missing_chapter' for c in candidates)


def test_lists_inheritance_and_qualifiers_preserved(real_map):
    e = entry((2, 6), (4, 32))
    e['locators'][1].update(relation='ibid', inherited_reference_evidence={'raw':'IV, 32'}, qualifier_raw=' seq.')
    got = locate(e, real_map)
    assert {c['evidence'][0]['book'] for c in got} == {2, 4}
    inherited = [c for c in got if c['evidence'][0]['book'] == 4]
    assert inherited and all('following_or_repeated_extent_not_expanded' in c['uncertainties'] for c in inherited)
    assert diagnose(e, real_map)['located_locator_count'] == 2


def test_out_of_range_and_uninherited_do_not_guess(real_map):
    e = entry((7, 999), (3, 1))
    e['locators'].append(dict(raw='ibid.', relation='ibid'))
    diagnosis = diagnose(e, real_map)
    assert diagnosis['locator_count'] == 3
    assert diagnosis['located_locator_count'] == 1
    assert diagnosis['completeness'] == 'partial_or_pending'
    assert {x['reason'] for x in diagnosis['unmatched_locators']} == {'missing_inherited_context', 'chapter_outside_anchored_book_sequence'}
    assert locate(dict(e, reference_namespace='pg_column'), real_map) == []
    assert locate(dict(e, volume='PL006'), real_map) == []


def test_last_chapter_stops_before_appendix(real_map):
    got = locate(entry((7, 51)), real_map)
    assert got
    assert max(c['physical'] for c in got) == 649
    assert 650 not in {c['physical'] for c in got}


def test_no_unique_work_boundary_fails_clearly(tmp_path):
    (tmp_path / 'teste/PL005/text').mkdir(parents=True)
    with pytest.raises(ValueError, match='boundary must occur once'):
        build_map(tmp_path)


def test_rebuild_is_deterministic(real_map):
    assert build_map(ROOT) == real_map

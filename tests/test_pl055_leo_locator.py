"""
Tests for PL055 Saint Leo the Great Index Locator (Sprint 24)
"""

import pytest
from tools.indexing.pl055_leo_locator import PL055LeoLocator


def test_prior_part_quesnel_notes_resolution():
    locator = PL055LeoLocator()
    # Priore Parte Tomi (src 671) -> PL054, col 1463 -> page 740
    res = locator.resolve_item(
        'Nomen ipsius ad altare recitari Leo velit',
        '1463',
        '/corpus/PL055/text/8016bbe0-aea8-4041-a70c-d515ebcda274-671.txt'
    )
    assert res is not None
    assert res['target_volume'] == 'PL054'
    assert res['column'] == 1463
    assert res['physical_page'] == 740


def test_prior_part_sermons_epistles_resolution():
    locator = PL055LeoLocator()
    # Priore Parte Tomi (src 674) -> PL054, col 591 -> page 302
    res = locator.resolve_item(
        'Martyrologia antiqua, quæ ejus memoriam faciunt',
        '591 et seqq',
        '/corpus/PL055/text/8016bbe0-aea8-4041-a70c-d515ebcda274-674.txt'
    )
    assert res is not None
    assert res['target_volume'] == 'PL054'
    assert res['column'] == 591
    assert res['physical_page'] in [297, 302]


def test_posterior_part_resolution():
    locator = PL055LeoLocator()
    # Posteriore Parte Tomi (src 681) -> PL055, col 540
    res = locator.resolve_item(
        'Gennadius novus C. P. episcopus',
        '540 et seqq',
        '/corpus/PL055/text/8016bbe0-aea8-4041-a70c-d515ebcda274-681.txt'
    )
    assert res is not None
    assert res['target_volume'] == 'PL055'
    assert res['column'] == 540


def test_suffix_stripping():
    locator = PL055LeoLocator()
    res1 = locator.resolve_item('Test', '1405 not', '...-676.txt')
    assert res1 is not None
    assert res1['column'] == 1405

    res2 = locator.resolve_item('Test', '264 et\n    seqq', '...-684.txt')
    assert res2 is not None
    assert res2['column'] == 264


def test_pl055_dry_run_promotion():
    locator = PL055LeoLocator()
    res = locator.run(dry_run=True, force_all=False)
    if res['total_items'] > 0:
        assert res['total_items'] == 40
        assert res['promoted'] == 40
        assert res['candidates'] == 40
    else:
        assert res['promoted'] == 0

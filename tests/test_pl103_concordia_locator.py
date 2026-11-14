"""
Tests for PL103ConcordiaLocator (Sprint 43)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl103_concordia_locator import (
    PL103ConcordiaLocator,
    PL103_MAP,
    DB_PATH
)


def test_pl103_mapping_completeness():
    assert len(PL103_MAP) == 17
    locator = PL103ConcordiaLocator()

    for item_id, (target_vol, p_num, cap_or_col, reason) in PL103_MAP.items():
        assert target_vol == 'PL103'
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(target_vol, p_num)
        assert fpath is not None, f"Missing file for {target_vol} page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl103_resolution():
    locator = PL103ConcordiaLocator()

    # 1. Claves monasterii (1511 -> col 1341 -> p. 671)
    res1 = locator.resolve_item(
        '34a4754ea18d8ce14b78cfc1d428847884d6f83ea368ee1e53320ee81b33c040',
        'Claves monasterii et officinarum norte dandæ sunt abbati',
        '1511'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL103'
    assert res1[0]['physical_page'] == 671
    assert '671.txt' in res1[0]['target_file']

    # 2. Dejejunantis (1539 -> col 1351 -> p. 676)
    res2 = locator.resolve_item(
        'a0fca5ef3417ac15f7c7221fa2bcf53daa46a49ca709e50e2e56b993ffc9f6a4',
        'Dejejunantis',
        '1539'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL103'
    assert res2[0]['physical_page'] == 676
    assert '676.txt' in res2[0]['target_file']

    # 3. Oblati a parentibus (1508, 1505 -> col 1308 -> p. 654)
    res3 = locator.resolve_item(
        '2b55598aa4330c4766b231916af6ec8ed6ae62173b6a85f872ef4f9b4a7d9efd',
        'Oblati a parentibus cogebantur olim perpetuo manere in monasterio',
        '1508, 1505'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL103'
    assert res3[0]['physical_page'] == 654
    assert '654.txt' in res3[0]['target_file']

    # 4. Palmarum rami (1548 -> col 1156 -> p. 578)
    res4 = locator.resolve_item(
        'a957aeb86522178ff28a1f23ae83a24a686962809ab5ff151dbeb9a8de6dde19',
        'Palmarum rami madefacti ad conterendas mantias',
        '1548'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL103'
    assert res4[0]['physical_page'] == 578
    assert '578.txt' in res4[0]['target_file']

    # 5. Somnus frater mortis (1171, 1485 -> col 1171 -> p. 586)
    res5 = locator.resolve_item(
        '8361c819fce1616bd5e01848456f9d6be5029209b4dcc3b196a764aef3c00f49',
        'Somnus frater mortis. 1171. Meridianus',
        '1485'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL103'
    assert res5[0]['physical_page'] == 586
    assert '586.txt' in res5[0]['target_file']


def test_pl103_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL103ConcordiaLocator(db_path=DB_PATH)
    res = locator.apply_promotions(dry_run=True)
    assert res['status'] == 'dry_run_complete'
    assert res['dry_run'] is True
    if res['total_evaluated'] > 0:
        assert res['total_promoted'] == 17
    else:
        assert res['total_promoted'] == 0

"""
Tests for PG132TheophanesLocator (Sprint 38)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pg132_theophanes_locator import (
    PG132TheophanesLocator,
    PG132_MAP,
    DB_PATH
)


def test_pg132_mapping_completeness():
    assert len(PG132_MAP) == 22
    locator = PG132TheophanesLocator()
    
    for item_id, (p_num, cap_or_col, reason) in PG132_MAP.items():
        assert p_num in [166, 211, 259, 339, 355, 376, 386, 394, 437, 441, 450, 488, 514, 581]
        assert len(reason) > 0
        fpath = locator.get_file_for_page(p_num)
        assert fpath is not None, f"Missing file for PG132 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pg132_resolution():
    locator = PG132TheophanesLocator()
    
    # 1. Gadareni et Gergeseni (col 271, n. 64 -> p. 166)
    res1 = locator.resolve_item(
        '58c69c06e4dda7947565af1f9aa252afd6d6d25e27b0e48acd122145558768bd',
        'Eorum etymon an',
        'idem'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PG132'
    assert res1[0]['physical_page'] == 166
    assert '166.txt' in res1[0]['target_file']

    # 2. Jephte historia (col 362 -> p. 211)
    res2 = locator.resolve_item(
        'c02264ff203aa18b928bfb9b28de9c31456c801d0ed121b720692082e8c773e6',
        'Jephte historia narratur',
        '562 et seqq'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PG132'
    assert res2[0]['physical_page'] == 211
    assert '211.txt' in res2[0]['target_file']

    # 3. Theologi in modo loquendi (col 438, n. 79 -> p. 259)
    res3 = locator.resolve_item(
        'group:292f36b94a88192b18d767d10b867ead5e04053e691dac48a0552d04c406c9ca',
        'Theologi in modo loquendi ab eo discrepare videntur',
        'ibid'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PG132'
    assert res3[0]['physical_page'] == 259
    assert '259.txt' in res3[0]['target_file']

    # 4. Paradisi duo (col 590, n. 54 -> p. 339)
    res4 = locator.resolve_item(
        '8b16dcb8380ea09e3a58e836ee95da7c9fe3ff2f1b9278c2fc314bf405afe9a8',
        'Paradisi duo a Theophane distincti, sensibilis et intelligibilis',
        '590, n. 54'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PG132'
    assert res4[0]['physical_page'] == 339
    assert '339.txt' in res4[0]['target_file']

    # 5. Actio et contemplatio (col 1051, A -> p. 581)
    res5 = locator.resolve_item(
        'group:b00eb83580bdaf7ea4b887092dd574525e365e4902e81d1315b621e64f2b4fd2',
        'Actio et contemplatio se habent ut anima et corpus...',
        'ibid'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PG132'
    assert res5[0]['physical_page'] == 581
    assert '581.txt' in res5[0]['target_file']


def test_pg132_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PG132TheophanesLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 22
    else:
        assert result["total_promoted"] == 0

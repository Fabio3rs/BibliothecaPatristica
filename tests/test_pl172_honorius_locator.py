"""
Tests for PL172HonoriusLocator (Sprint 36)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl172_honorius_locator import (
    PL172HonoriusLocator,
    PL172_MAP,
    DB_PATH
)


def test_pl172_mapping_completeness():
    assert len(PL172_MAP) == 22
    locator = PL172HonoriusLocator()
    
    for item_id, (p_num, cap_or_col, reason) in PL172_MAP.items():
        assert p_num in [106, 117, 118, 120, 121, 122]
        assert len(reason) > 0
        fpath = locator.get_file_for_page(p_num)
        assert fpath is not None, f"Missing file for PL172 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl172_resolution():
    locator = PL172HonoriusLocator()
    
    # 1. Dionysius Areopagita (lib. i, c. 16 -> p. 106)
    res1 = locator.resolve_item(
        'region-entry:3fa8e448285c47be9be584155e0e7a0770cc223f38613cc56c1600a41ea90fcd',
        'Dionysius Areopagita',
        'lib. i, c. 16'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL172'
    assert res1[0]['physical_page'] == 106
    assert '106.txt' in res1[0]['target_file']

    # 2. Joannes Scotus (lib. iii, c. 12 -> p. 117)
    res2 = locator.resolve_item(
        'region-entry:fb23c606a84ab679fdbccb53f59f67a8a9223e22d6cf1e19a8de5ae572299c58',
        'Joannes Scotus',
        'lib. iii, c. 12'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL172'
    assert res2[0]['physical_page'] == 117
    assert '117.txt' in res2[0]['target_file']

    # 3. Cassiodorus (lib. iii, c. 21 -> p. 118)
    res3 = locator.resolve_item(
        'region-entry:ab41a7e1f716f117c0448f45459f62cf50c7c2930b5ee0e471a6b11212dbf79c',
        'Cassiodorus',
        'lib. iii, c. 21'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL172'
    assert res3[0]['physical_page'] == 118
    assert '118.txt' in res3[0]['target_file']

    # 4. Beda (lib. iv, c. 1 -> p. 120)
    res4 = locator.resolve_item(
        'region-entry:4f4bb45243feccf6248e73d20011c0c5de3f8ef06df0621c712c26eefb7194dc',
        'Beda',
        'lib. iv, c. 1'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL172'
    assert res4[0]['physical_page'] == 120
    assert '120.txt' in res4[0]['target_file']

    # 5. Rupertus Tuitiensis (lib. iv, c. 16 -> p. 122)
    res5 = locator.resolve_item(
        'region-entry:841a5d5674d7fccdbaa55e3ccb4ef318e008028c32b96e2f68e29d2831a28788',
        'Rupertus Tuitiensis',
        'lib. iv, c. 16'
    )
    assert len(res5) == 1
    assert res5[0]['target_volume'] == 'PL172'
    assert res5[0]['physical_page'] == 122
    assert '122.txt' in res5[0]['target_file']


def test_pl172_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL172HonoriusLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 22
    else:
        assert result["total_promoted"] == 0

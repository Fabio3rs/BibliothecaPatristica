"""
Tests for PL078LiturgyLocator (Sprint 33)
"""

import os
import sys
from pathlib import Path
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.pl078_liturgy_locator import (
    PL078LiturgyLocator,
    PL078_MAP,
    DB_PATH
)


def test_pl078_mapping_completeness():
    assert len(PL078_MAP) == 24
    locator = PL078LiturgyLocator()
    
    for item_id, (p_num, col_or_sec, reason) in PL078_MAP.items():
        assert p_num > 0
        assert len(reason) > 0
        fpath = locator.get_file_for_page(p_num)
        assert fpath is not None, f"Missing file for PL078 page {p_num}"
        assert Path(fpath).exists(), f"File does not exist: {fpath}"


def test_pl078_resolution():
    locator = PL078LiturgyLocator()
    
    # 1. Per omnia saecula saeculorum -> physical page 148
    res1 = locator.resolve_item(
        '58a3a8f1e3ab893283bbb32ec28d64015be0b12f3031e6bbb96a3b0cc7c187c5',
        'Per omnia scula sæculorum Antiqua hec orationis lorusuta apud Christianos',
        '2914'
    )
    assert len(res1) == 1
    assert res1[0]['target_volume'] == 'PL078'
    assert res1[0]['physical_page'] == 148
    assert res1[0]['target_column_or_sec'] == 294
    assert '148.txt' in res1[0]['target_file']

    # 2. Legatorum cardinalium vel nuntiorum creatio -> physical page 642 (sec 438)
    res2 = locator.resolve_item(
        'group:478d3ee9bdfdc4e61b7e7944758b9e3ef406c80e0313ba97fec6785641a2c476',
        'Legatorum cardinalium vel nuntiorum creatio',
        '438 et seqq'
    )
    assert len(res2) == 1
    assert res2[0]['target_volume'] == 'PL078'
    assert res2[0]['physical_page'] == 642
    assert res2[0]['target_column_or_sec'] == 438
    assert '642.txt' in res2[0]['target_file']

    # 3. Processus generales die Coenae Domini -> physical page 613 (sec 382)
    res3 = locator.resolve_item(
        '0ac985e1d6f57ad4c53900ffcaa93a518c94ac8bb16259f68dba48a958c8f3a2',
        'Processus generales die Cœnæ Domini',
        '3582'
    )
    assert len(res3) == 1
    assert res3[0]['target_volume'] == 'PL078'
    assert res3[0]['physical_page'] == 613
    assert res3[0]['target_column_or_sec'] == 382
    assert '613.txt' in res3[0]['target_file']

    # 4. Scrutinii ordo -> physical page 504 (sec 77)
    res4 = locator.resolve_item(
        'group:e1ba77f57b4c15de914a228e546856535f7449fd8e3809f0878aef3be61606c1',
        'Scrutinii ordo',
        '77 et seqq'
    )
    assert len(res4) == 1
    assert res4[0]['target_volume'] == 'PL078'
    assert res4[0]['physical_page'] == 504
    assert res4[0]['target_column_or_sec'] == 77
    assert '504.txt' in res4[0]['target_file']


def test_pl078_locator_dry_run():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"Database {DB_PATH} not found")

    locator = PL078LiturgyLocator(db_path=DB_PATH)
    result = locator.run(dry_run=True)
    assert result["status"] == "success"
    assert result["dry_run"] is True
    if result["total_evaluated"] > 0:
        assert result["total_promoted"] == 24
    else:
        assert result["total_promoted"] == 0

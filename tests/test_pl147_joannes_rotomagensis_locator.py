import os
import pytest
from tools.indexing.pl147_joannes_rotomagensis_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "579a76e012f4e2a4afd60a578d5da227fa445cf32ef7adcdf59a72ee2e8b6d0f" in items

    target = items["579a76e012f4e2a4afd60a578d5da227fa445cf32ef7adcdf59a72ee2e8b6d0f"]
    assert target["label"] == "Elevatio hostiæ et calicis solemnis alias inusitata"
    assert target["ref_raw"] == "108"
    assert len(target["destinations"]) == 3


def test_destination_files_and_content():
    items = locate_all()
    target = items["579a76e012f4e2a4afd60a578d5da227fa445cf32ef7adcdf59a72ee2e8b6d0f"]
    
    # Dest 1: Prevotius note 73 in PL147 cols 77-78 (physical p. 44)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "calix levabatur a sacerdote et diacono" in content1
    assert "hostia in patena" in content1
    assert "elevatione corporis et sanguinis Christi seorsim" in content1

    # Dest 2: Joannes Rotomagensis primary liturgical text in PL147 col. 35 (physical p. 23)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "oratione finita uterque calicem levent" in content2

    # Dest 3: Index source in PL147 col. 1515 (physical p. 665)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Elevatio hostiæ et calicis" in content3
    assert "INDEX IN JOANNEM ROTOMAGENSEM" in content3

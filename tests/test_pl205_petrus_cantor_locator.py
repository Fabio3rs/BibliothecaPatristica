import os
import pytest
from tools.indexing.pl205_petrus_cantor_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "39e8cbca231165b94d6b0ae2ab6c080a451e06bf43ff8e4c165559bc11b318d9" in items

    target = items["39e8cbca231165b94d6b0ae2ab6c080a451e06bf43ff8e4c165559bc11b318d9"]
    assert target["label"] == "Pallidi Sancti quinam sunt"
    assert target["ref_raw"] == "3519"
    assert len(target["destinations"]) == 4


def test_destination_files_and_content():
    items = locate_all()
    target = items["39e8cbca231165b94d6b0ae2ab6c080a451e06bf43ff8e4c165559bc11b318d9"]
    
    # Dest 1: PL205 physical p. 263 (cols 499-500, Galopinus note ad cap. 116)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "sanctorum Pallidorum" in content1
    assert "Pallidi dicit a mortifi" in content1

    # Dest 2: PL205 physical p. 271 (cols 515-516, Galopinus note ad cap. 136)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Sanctorum pallidorum" in content2
    assert "pallore vultus, et maceratione corporis" in content2

    # Dest 3: PL205 physical p. 171 (col. 328, primary text)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "sanctorum pallidorum" in content3
    assert "Paulus primus ere-" in content3

    # Dest 4: PL205 physical p. 517 (col. 1007, Index source)
    dest4 = target["destinations"][3]
    assert os.path.exists(dest4["file"]), f"File not found: {dest4['file']}"
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Pallidi Sancti quinam sunt, 3519" in content4

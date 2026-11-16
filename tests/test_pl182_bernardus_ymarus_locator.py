import os
import pytest
from tools.indexing.pl182_bernardus_ymarus_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "group:feed334f3f8836c3022e0c5523f8c1b5dc59940147fc50a1bbe3bd56a22e0860" in items

    target = items["group:feed334f3f8836c3022e0c5523f8c1b5dc59940147fc50a1bbe3bd56a22e0860"]
    assert target["label"] == "Igmaro seu Ymaro cardinali episcopo Tusculano"
    assert target["ref_raw"] == "219, 250-232"
    assert len(target["destinations"]) == 4


def test_destination_files_and_content():
    items = locate_all()
    target = items["group:feed334f3f8836c3022e0c5523f8c1b5dc59940147fc50a1bbe3bd56a22e0860"]
    
    # Dest 1: PL182 physical p. 196 (Epistola 219)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "EPISTOLA CCXIX" in content1
    assert "IGNATUM TUSCULANUM" in content1
    assert "Ignarus seu Ymarus" in content1

    # Dest 2: PL182 physical p. 214 (Epistolae 230-231)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "EPISTOLA CCXXX." in content2
    assert "TUSCULANUM" in content2
    assert "EPISTOLA CCXXXI." in content2

    # Dest 3: PL182 physical p. 215 (Epistola 232)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Contra abbatem Sancti Theofredi" in content3
    assert "Ymarius" in content3

    # Dest 4: PL182 physical p. 602 (col. 1195, Index source)
    dest4 = target["destinations"][3]
    assert os.path.exists(dest4["file"]), f"File not found: {dest4['file']}"
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Igmaro seu Ymaro cardinali episcopo Tusculano" in content4

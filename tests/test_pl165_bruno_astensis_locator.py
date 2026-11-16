import os
import pytest
from tools.indexing.pl165_bruno_astensis_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "6ba6f3a4a395c904ebf331d752e274d4cc4231e7853c20127b1e78842cf03847" in items

    target = items["6ba6f3a4a395c904ebf331d752e274d4cc4231e7853c20127b1e78842cf03847"]
    assert target["label"] == "Quod peccando donis Dei abutimur, ideo magnam injuriam ei irrogamus"
    assert target["ref_raw"] == "I, 244"
    assert len(target["destinations"]) == 3


def test_destination_files_and_content():
    items = locate_all()
    target = items["6ba6f3a4a395c904ebf331d752e274d4cc4231e7853c20127b1e78842cf03847"]
    
    # Dest 1: PL164 physical p. 299 (cols 593-594, bold locator 244)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "244" in content1
    assert "EXPOSITIO IN JOB" in content1
    assert "Facies autem levatur absque macula" in content1

    # Dest 2: PL164 physical p. 300 (col. 595, commentary on Job 12:6)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "enim faciunt contra Deum, quamvis nihil habeant" in content2
    assert "S. BRUNONIS" in content2

    # Dest 3: PL165 physical p. 674 (col. 1328, Index in S. Brunonem)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "Quod peccando donis Dei abutimur" in content3
    assert "INDEX IN S. BRUNONEM" in content3

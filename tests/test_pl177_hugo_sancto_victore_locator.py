import os
import pytest
from tools.indexing.pl177_hugo_sancto_victore_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "group:26d880d69d9a304a7077e18df2ea8c03c19cffbcc184c92935d8d5ab0cd96d5f" in items

    target = items["group:26d880d69d9a304a7077e18df2ea8c03c19cffbcc184c92935d8d5ab0cd96d5f"]
    assert target["label"] == "Speculum Ecclesiæ"
    assert target["ref_raw"] == "556 et seqq"
    assert len(target["destinations"]) == 4


def test_destination_files_and_content():
    items = locate_all()
    target = items["group:26d880d69d9a304a7077e18df2ea8c03c19cffbcc184c92935d8d5ab0cd96d5f"]
    
    # Dest 1: PL177 physical p. 172 (col. 335, title and incipit of Speculum de Mysteriis Ecclesiae)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "SPECULUM DE MYSTERIIS ECCLESIÆ" in content1
    assert "PROLOGUS" in content1

    # Dest 2: PL177 physical p. 183 (cols 356-358, Cap. VII De celebratione missae)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "SPECULUM ECCLESIÆ" in content2

    # Dest 3: PL177 physical p. 627 (col. 1245, Elenchus operum)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "SPECULUM DE MYSTERIIS ECCLESIÆ" in content3
    assert "335" in content3
    assert "356" in content3

    # Dest 4: PL177 physical p. 622 (col. 1236, Index source)
    dest4 = target["destinations"][3]
    assert os.path.exists(dest4["file"]), f"File not found: {dest4['file']}"
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "Speculum Ecclesiæ, 556 et seqq." in content4
    assert "INDEX RERUM ANALYTICUS" in content4

import os
import pytest
from tools.indexing.po003_ahoudemmeh_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "region-entry:301e5d51dd50fe598c968849eaed17cc428329167ca895a2594ab9f53101d3f8" in items

    target = items["region-entry:301e5d51dd50fe598c968849eaed17cc428329167ca895a2594ab9f53101d3f8"]
    assert target["label"] == "III. — De l'union de l'âme et du corps"
    assert target["ref_raw"] == "1o Classification des opérations bonnes et mau-"
    assert len(target["destinations"]) == 4


def test_destination_files_and_content():
    items = locate_all()
    target = items["region-entry:301e5d51dd50fe598c968849eaed17cc428329167ca895a2594ab9f53101d3f8"]
    
    # Dest 1: PO003 physical p. 117 (fasc. p. 107, chapter III header)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "DE L'UNION DE L'AME ET DU CORPS" in content1
    assert "[107]" in content1

    # Dest 2: PO003 physical p. 118 (fasc. p. 108, section heading)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "Classification des opérations bonnes et mauvaises" in content2

    # Dest 3: PO003 physical p. 107 (Introduction by F. Nau)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "de l'union de l'âme avec le corps" in content3

    # Dest 4: PO003 physical p. 130 (Table des matières)
    dest4 = target["destinations"][3]
    assert os.path.exists(dest4["file"]), f"File not found: {dest4['file']}"
    with open(dest4["file"], "r", encoding="utf-8") as f:
        content4 = f.read()
    assert "TABLE DES MATIÈRES" in content4
    assert "III. — De l'union de l'âme et du corps" in content4

import os
import pytest
from tools.indexing.pl212_aegidius_corboliensis_locator import locate_all

def test_locate_all_structure():
    items = locate_all()
    assert len(items) == 1
    assert "group:16a35f34a44772caf0111bd33b559acb4e269bf83d402dc30744f96c33818293" in items

    target = items["group:16a35f34a44772caf0111bd33b559acb4e269bf83d402dc30744f96c33818293"]
    assert target["label"] == "Adnectitur et Auroræ ejusdem Ægidii tractatus De pœna infernali metricus"
    assert target["ref_raw"] == "ibid"
    assert len(target["destinations"]) == 3


def test_destination_files_and_content():
    items = locate_all()
    target = items["group:16a35f34a44772caf0111bd33b559acb4e269bf83d402dc30744f96c33818293"]
    
    # Dest 1: PL212 physical p. 13 (cols 17-18, rubric citing Leyser p. 742)
    dest1 = target["destinations"][0]
    assert os.path.exists(dest1["file"]), f"File not found: {dest1['file']}"
    with open(dest1["file"], "r", encoding="utf-8") as f:
        content1 = f.read()
    assert "FRAGMENTA EX AURORA" in content1
    assert "Adnectitur et Auroræ ejusdem Ægidii tractatus De pœna infernali metricus, ibid., p. 742" in content1

    # Dest 2: PL212 physical p. 14 (cols 19-20)
    dest2 = target["destinations"][1]
    assert os.path.exists(dest2["file"]), f"File not found: {dest2['file']}"
    with open(dest2["file"], "r", encoding="utf-8") as f:
        content2 = f.read()
    assert "PÈTRUS DE RIGA ET ÆGIDIUS PARIS" in content2 or "PETRUS DE RIGA" in content2

    # Dest 3: PL212 physical p. 15 (cols 21-22)
    dest3 = target["destinations"][2]
    assert os.path.exists(dest3["file"]), f"File not found: {dest3['file']}"
    with open(dest3["file"], "r", encoding="utf-8") as f:
        content3 = f.read()
    assert "FRAGMENTA EX AURORA" in content3

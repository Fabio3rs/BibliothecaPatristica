"""Conservative bbox-only drafts and donor provenance; no source writes."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_ocr.refinement import bbox_draft, complementary_passages
from offline_ocr.migne_hints import analyse_tsv

FIXTURES = Path(__file__).parent / "fixtures/ocr_diff_v2"


def simple_layout():
    words = [{"id": f"w{i}", "text": f"verbum{i}", "bbox": [100 + (i % 5) * 50, 100 + (i // 5) * 20,
              140 + (i % 5) * 50, 110 + (i // 5) * 20]} for i in range(20)]
    return {"words": words, "blocks": [{"id": "b0", "text": " ".join(w["text"] for w in words),
                                        "word_ids": [w["id"] for w in words]}]}


def test_bbox_repair_preserves_every_other_byte_including_metadata():
    layout = simple_layout()
    text = '''<pagina estado="com_texto"><bloco data-bbox="keep" note="bbox='do not edit'" tipo="texto_principal" script="latino" bbox="600,600,900,900">''' + layout["blocks"][0]["text"] + '</bloco><notas>unchanged</notas></pagina>'
    draft, report = bbox_draft(text, layout)
    assert draft == text.replace('bbox="600,600,900,900"', 'bbox="100,100,340,170"')
    assert report["blocks"][0]["status"] == "proposed"
    assert report["blocks"][0]["source_word_ids"]
    assert report["text_changed"] is False


def test_missing_box_can_be_proposed_without_changing_text():
    layout = simple_layout()
    text = '<pagina><bloco>' + layout["blocks"][0]["text"] + '</bloco></pagina>'
    draft, report = bbox_draft(text, layout)
    assert ET.fromstring(draft).find('bloco').get('bbox') == '100,100,340,170'
    assert ET.fromstring(draft).findtext('bloco') == ET.fromstring(text).findtext('bloco')


def test_repeated_passages_and_short_markers_abstain():
    layout = simple_layout()
    body = layout["blocks"][0]["text"]
    text = f'<pagina><bloco>{body}</bloco><bloco>{body}</bloco><bloco>A</bloco></pagina>'
    draft, report = bbox_draft(text, layout)
    assert draft == text
    assert all(b["status"] == "abstain" for b in report["blocks"])


def test_real_merged_column_output_is_not_given_one_enclosing_box():
    layout = analyse_tsv((FIXTURES / "PG001_638_migne_raw.tsv").read_text(), (2480, 3509))
    path = Path(__file__).parent / "fixtures/ocr_ab_pg001_638/r001_ordered_text.xml"
    draft, report = bbox_draft(path.read_text(), layout)
    main = report["blocks"][1]
    assert main["status"] == "abstain"
    assert "text_spans_multiple_physical_regions_requires_segmentation" in main["reasons"]
    assert ET.fromstring(draft).findall('bloco')[1].get('bbox') == '225,95,635,905'


def test_real_draft_never_changes_block_text_or_notes():
    layout = analyse_tsv((FIXTURES / "PG001_638_migne_raw.tsv").read_text(), (2480, 3509))
    text = (Path(__file__).parent / "fixtures/ocr_ab_pg001_638/r001_raw_text.xml").read_text()
    draft, report = bbox_draft(text, layout)
    assert any(b["status"] == "proposed" for b in report["blocks"])
    assert [b.text for b in ET.fromstring(draft)] == [b.text for b in ET.fromstring(text)]


def test_complementary_spans_keep_exact_donor_bytes_and_do_not_merge():
    layout = {"blocks": [{"text": "primum ante multa verba certa scripta sunt deinde postea finis"}]}
    base = '<pagina><bloco>primum ante deinde postea finis</bloco></pagina>'
    donor = '<pagina><bloco>primum ante multa\nverba certa scripta sunt deinde postea finis</bloco></pagina>'
    report = complementary_passages(base, [(42, donor)], layout)
    assert report["automatic_merge"] is False
    assert len(report["proposals"]) == 1
    offer = report["proposals"][0]["donors"][0]
    assert offer["version_id"] == 42
    start, end = offer["source_span"]
    assert offer["text"] == donor[start:end] == "multa\nverba certa scripta sunt"


def test_conflicting_or_unrelated_donor_is_not_used_to_fill_gap():
    layout = {"blocks": [{"text": "primum ante multa verba certa scripta sunt deinde postea finis"}]}
    base = '<pagina><bloco>primum ante deinde postea finis</bloco></pagina>'
    other = '<pagina><bloco>primum ante alia sententia falsa adest deinde postea finis</bloco></pagina>'
    assert complementary_passages(base, [(43, other)], layout)["proposals"] == []

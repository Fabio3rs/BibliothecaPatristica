import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "export_alphabetical_indices.py"
SPEC = importlib.util.spec_from_file_location("export_alphabetical_indices", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

pick_page = MODULE.pick_page
pick_viewer_page = MODULE.pick_viewer_page
make_entry = MODULE.make_entry
public_shard_path = MODULE.public_shard_path
target_is_index_source = MODULE.target_is_index_source
classify_domain = MODULE.classify_domain
prune_stale_generated_files = MODULE.prune_stale_generated_files
VERIFIED_LOCATOR = {
    "compact_locator": {
        "status": "resolved",
        "evidence": [{"kind": "editorial_header_match"}],
    }
}


def test_viewer_page_prefers_physical_ocr_page() -> None:
    row = {
        "page_ref_int": 1341,
        "inferred_printed_page": 1341,
        "target_file": "teste/PG136/text/example-710.txt",
        "ref_raw_json": VERIFIED_LOCATOR,
        "target_file_best": None,
    }

    assert pick_page(row) == 1341
    assert pick_viewer_page(row) == 710


def test_editorial_page_never_falls_back_to_physical_filename() -> None:
    row = {
        "page_ref_int": None,
        "inferred_printed_page": None,
        "target_file": "teste/PG136/text/example-710.txt",
        "ref_raw_json": VERIFIED_LOCATOR,
        "target_file_best": "teste/PG136/text/example-711.txt",
    }

    assert pick_page(row) is None
    assert pick_viewer_page(row) == 710


def test_public_occurrence_uses_its_own_material_ref() -> None:
    row = {
        "entry_key": "PL001:entry:1",
        "ref_order": 2,
        "lemma_display": "Augustinus",
        "lemma_raw": "Augustinus",
        "sref_raw": None,
        "entry_raw": "Augustinus, 10, 20.",
        "context_raw": None,
        "book_norm": None,
        "book_raw": None,
        "collection": "PL",
        "volume_id": "PL001",
        "section_key": "PL001:section:1",
        "section_kind": "onomastic_person",
        "entry_kind": "lemma",
        "heading_raw": "INDEX NOMINUM",
        "ref_kind": "editorial_page",
        "ref_role": None,
        "page_ref_raw": "20",
        "ref_raw": "20",
        "page_ref_int": 20,
        "inferred_printed_page": 10,
        "target_file": "teste/PL001/text/page-030.txt",
        "ref_raw_json": VERIFIED_LOCATOR,
        "target_file_best": "teste/PL001/text/page-020.txt",
        "confidence": 0.9,
        "chapter_start": None,
        "verse_start": None,
        "chapter_end": None,
        "verse_end": None,
        "is_range": None,
    }

    entry = make_entry(row, "names")

    assert entry["occurrence_id"] == "PL001:entry:1:ref:0002"
    assert entry["ref_order"] == 2
    assert entry["page_ref"] == 20
    assert entry["viewer_page"] == 30
    assert entry["target_file"] == "teste/PL001/text/page-030.txt"


def test_index_source_target_is_not_exposed_as_material_link() -> None:
    row = {
        "target_file": "teste/PG084/text/page-642.txt",
        "ref_raw_json": VERIFIED_LOCATOR,
        "section_file_start": "teste/PG084/text/page-628.txt",
        "section_file_end": "teste/PG084/text/page-645.txt",
    }

    assert target_is_index_source(row)
    assert pick_viewer_page(row) is None


def test_legacy_target_without_page_specific_evidence_is_not_exposed() -> None:
    row = {
        "target_file": "teste/PL001/text/page-030.txt",
        "section_file_start": "teste/PL001/text/page-100.txt",
        "section_file_end": "teste/PL001/text/page-110.txt",
        "ref_raw_json": {"locator_kind": "editorial_page"},
    }

    assert MODULE.pick_target_file(row) is None


def test_public_shard_path_is_root_relative_to_alpha_domain() -> None:
    assert public_shard_path("scripture", "shards/1-corintios-001.json.gz") == (
        "alpha/scripture/shards/1-corintios-001.json.gz"
    )
    assert public_shard_path("subjects", "alpha/subjects/shards/a-001.json.gz") == (
        "alpha/subjects/shards/a-001.json.gz"
    )


def test_ordo_rerum_is_not_exported_as_an_alphabetical_subject() -> None:
    assert classify_domain(
        {"section_kind": "ordo_rerum", "entry_kind": "lemma"}
    ) is None


def test_export_prunes_only_stale_generated_files(tmp_path: Path) -> None:
    generated = tmp_path / "shards"
    generated.mkdir()
    (generated / "current.json.gz").write_bytes(b"current")
    (generated / "stale.json.gz").write_bytes(b"stale")
    (generated / "keep.txt").write_text("not owned", encoding="utf-8")

    removed = prune_stale_generated_files(
        generated,
        pattern="*.json.gz",
        expected_names={"current.json.gz"},
    )

    assert removed == 1
    assert (generated / "current.json.gz").is_file()
    assert not (generated / "stale.json.gz").exists()
    assert (generated / "keep.txt").is_file()

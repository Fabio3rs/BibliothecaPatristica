from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.scripture.book_catalog import (
    BOOKS,
    CANONICAL_BOOK_LABELS,
    canonical_book_key,
    historical_noncanonical_book_key,
)
from tools.scripture.evidence_locator import (
    ScriptureEvidenceConfig,
    add_scripture_evidence_candidates,
    build_scripture_table_repair_prompt,
    infer_citation_format_profiles,
)


def locator_item(
    *,
    source_root: Path,
    section_start: Path,
    section_end: Path,
    cited_page: int = 1260,
    book_raw: str = "I Cor.",
    book_norm: str = "1 Coríntios",
    chapter: int = 1,
    verse: int = 4,
    section_key: str = "PL001:index",
    entry_key: str = "PL001:index:e1",
) -> dict:
    return {
        "locator_key": f"{entry_key}::ref:000001",
        "entry_key": entry_key,
        "ref_order": 1,
        "scripture_ref_order": 1,
        "section_key": section_key,
        "section_file_start": str(section_start),
        "section_file_end": str(section_end),
        "cited_pages": [cited_page],
        "candidates": [],
        "scripture_ref": {
            "ref_order": 1,
            "ref_raw": f"{book_raw} {chapter}, {verse}",
            "book_raw": book_raw,
            "book_norm": book_norm,
            "chapter_start": chapter,
            "verse_start": verse,
        },
        "source_root": str(source_root),
    }


def test_aliases_share_one_inverted_reference_and_apparatus_scores_higher(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    (source_root / "page-001.txt").write_text(
        "Textus dicit I Cor. 1, 4 de caritate.", encoding="utf-8"
    )
    (source_root / "page-002.txt").write_text(
        "1 Cor 1:4 cod. A; ms. B; variantia.", encoding="utf-8"
    )
    (source_root / "page-003.txt").write_text(
        "Corinthios 1,4 iterum laudatur.", encoding="utf-8"
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
    )

    items, artifact = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        config=ScriptureEvidenceConfig(max_candidates=5),
    )

    candidates = items[0]["candidates"]
    assert {Path(candidate["file"]).name for candidate in candidates} == {
        "page-001.txt",
        "page-002.txt",
    }
    assert Path(candidates[0]["file"]).name == "page-002.txt"
    assert candidates[0]["probability"] > candidates[-1]["probability"]
    assert any(
        evidence["kind"] == "critical_apparatus_context"
        for evidence in candidates[0]["evidence"]
    )
    assert artifact["matched_reference_count"] == 1
    repeated_items, repeated_artifact = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        config=ScriptureEvidenceConfig(max_candidates=5),
    )
    assert repeated_items == items
    assert repeated_artifact == artifact


def test_migne_profile_uses_vulgate_regum_numbering_without_global_guess(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX SCRIPTURAE", encoding="utf-8")
    body_file = source_root / "page-001.txt"
    body_file.write_text("I Regum 3, 4", encoding="utf-8")
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        book_raw="I Regum",
        book_norm="",
        chapter=3,
        verse=4,
    )

    items, _ = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        collection="PL",
    )

    assert Path(items[0]["candidates"][0]["file"]) == body_file


def test_index_source_is_excluded_and_every_file_is_read_once(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    body_file = source_root / "page-001.txt"
    body_file.write_text("I Cor. 1, 4", encoding="utf-8")
    index_start = source_root / "page-010.txt"
    index_start.write_text("I Cor. 1, 4 ..... 1260", encoding="utf-8")
    index_end = source_root / "page-011.txt"
    index_end.write_text("I Cor. 1, 4 ..... 1261", encoding="utf-8")
    reads: dict[Path, int] = {}

    def counting_reader(path: Path) -> str:
        reads[path] = reads.get(path, 0) + 1
        return path.read_text(encoding="utf-8")

    item = locator_item(
        source_root=source_root,
        section_start=index_start,
        section_end=index_end,
    )
    items, artifact = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        read_text=counting_reader,
    )

    assert [Path(candidate["file"]).name for candidate in items[0]["candidates"]] == [
        "page-001.txt"
    ]
    assert artifact["items"][0]["excluded_index_hit_count"] == 2
    assert set(reads) == {body_file.resolve(), index_start.resolve(), index_end.resolve()}
    assert set(reads.values()) == {1}


def test_ocr_apparatus_block_tag_raises_candidate_weight(tmp_path: Path) -> None:
    source_root = tmp_path / "PO025" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    (source_root / "page-001.txt").write_text(
        '<bloco tipo="aparato_critico" script="latino">I Cor. 1, 4</bloco>',
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
    )

    items, _ = add_scripture_evidence_candidates([item], source_root=source_root)

    assert items[0]["candidates"][0]["probability"] == 0.82
    assert items[0]["candidates"][0]["evidence"][1] == {
        "kind": "critical_apparatus_context",
        "raw": "explicit apparatus block/tag",
        "weight": 0.16,
    }


def test_editorial_header_match_strengthens_body_candidate(tmp_path: Path) -> None:
    source_root = tmp_path / "PO025" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    (source_root / "page-001.txt").write_text(
        '<bloco tipo="cabecalho">[1260] HOMILIA 715</bloco>\n'
        '<bloco tipo="rodape">I Cor. 1, 4</bloco>',
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=1260,
    )

    items, _ = add_scripture_evidence_candidates([item], source_root=source_root)

    candidate = items[0]["candidates"][0]
    assert candidate["probability"] == 0.8
    assert candidate["evidence"][-1] == {
        "kind": "editorial_header_match",
        "raw": "printed page(s) [1260]",
        "weight": 0.14,
    }


def test_editorial_page_candidate_recovers_citation_with_book_and_number_cer(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    body_file = source_root / "page-001.txt"
    body_file.write_text(
        '<bloco tipo="cabecalho">[1260] HOMILIA</bloco>\n'
        "I Corlnthios l, 4 cod. A; ms. B.",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=1260,
        book_raw="I Corinthios",
        book_norm="1 Coríntios",
    )

    items, _ = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        collection="PL",
    )

    candidate = items[0]["candidates"][0]
    assert Path(candidate["file"]) == body_file
    assert candidate["candidate_role"] == "scripture_editorial_ocr_fuzzy"
    assert {
        evidence["kind"] for evidence in candidate["evidence"]
    } >= {
        "scripture_ocr_fuzzy",
        "editorial_header_match",
        "critical_apparatus_context",
    }


def test_editorial_candidate_is_not_cut_off_by_repeated_global_citation_hits(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    for number in range(1, 7):
        header = (
            '<bloco tipo="cabecalho">[1260] HOMILIA</bloco>\n'
            if number == 6
            else ""
        )
        (source_root / f"page-{number:03d}.txt").write_text(
            header + "I Cor. 1, 4",
            encoding="utf-8",
        )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=1260,
    )

    items, _ = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        config=ScriptureEvidenceConfig(max_candidates=2),
    )

    assert Path(items[0]["candidates"][0]["file"]).name == "page-006.txt"
    assert any(
        evidence["kind"] == "editorial_header_match"
        for evidence in items[0]["candidates"][0]["evidence"]
    )


def test_soft_wrapped_book_and_verse_range_are_joined_for_matching(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    wrapped_file = source_root / "page-001.txt"
    wrapped_file.write_text(
        "I Cor-\ninthios 1, 4-\n5",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
    )

    items, _ = add_scripture_evidence_candidates([item], source_root=source_root)

    assert [Path(candidate["file"]).name for candidate in items[0]["candidates"]] == [
        "page-001.txt"
    ]


def test_table_heading_parser_suggests_pages_without_overwriting_existing_ref(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    body_file = source_root / "page-001.txt"
    body_file.write_text("I Cor. 1, 4", encoding="utf-8")
    index_file = source_root / "page-010.txt"
    index_file.write_text(
        "I CORINTHIOS\n1, 4. IV, 1260.\n",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=1260,
    )

    unresolved = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=1261,
    )
    unresolved["locator_key"] = "PL001:index:e2::ref:000001"
    unresolved["entry_key"] = "PL001:index:e2"
    unresolved["entry_excerpt"] = "1, ?. 1261"
    unresolved["scripture_ref"]["verse_start"] = 5
    unresolved["scripture_ref"]["ref_raw"] = "I Cor. 1, 5"
    items, artifact = add_scripture_evidence_candidates(
        [item, unresolved], source_root=source_root
    )

    assert items[0]["cited_pages"] == [1260]
    suggestion = items[0]["table_reference_suggestions"][0]
    assert suggestion["suggested_editorial_pages"] == [1260]
    assert suggestion["agreement"] == "matches_existing"
    assert Path(items[0]["candidates"][0]["file"]).name == "page-001.txt"
    repair = artifact["table_repair_groups"][0]
    assert repair["unresolved_line_count"] == 1
    assert repair["unresolved_lines"][0]["partial_json"]["entry_key"] == "PL001:index:e2"


def test_format_profile_and_repair_prompt_are_section_local_and_bounded() -> None:
    semantic = {
        "entries": [
            {
                "entry_key": "PL001:index:e1",
                "section_key": "PL001:index",
                "entry_raw": "I Cor. 1, 4   1260, 1262",
            }
        ],
        "scripture_refs": [
            {
                "entry_key": "PL001:index:e1",
                "ref_order": 1,
                "book_raw": "I Cor.",
                "chapter_start": 1,
                "verse_start": 4,
                "ref_raw": "I Cor. 1, 4",
            }
        ],
    }
    profile = infer_citation_format_profiles(semantic)["PL001:index"]

    assert profile["observed_book_aliases"] == ["I Cor."]
    assert profile["field_order"][-1] == "editorial_pages"
    assert profile["column_style"] == "spaced"
    prompt = build_scripture_table_repair_prompt(
        volume_id="PL001",
        section_profile=profile,
        unresolved_lines=[
            {
                "line_id": "l1",
                "raw_line": "I Cor. 1, ?   1260",
                "partial_json": {
                    "entry_key": "PL001:index:e1",
                    "scripture_refs": [],
                    "refs": [{"page_ref_int": 1260}],
                    "whole_page": "must not be copied",
                },
            }
        ],
    )

    assert "CITATION_FORMAT_PROFILE" in prompt
    assert '"raw_line":"I Cor. 1, ?   1260"' in prompt
    assert "whole_page" not in prompt
    assert "`scripture_refs`, `refs`" in prompt
    json.loads(prompt.split("UNRESOLVED_ROWS\n", 1)[1].split("\n\nOUTPUT", 1)[0])


def test_po_profiles_do_not_inherit_aliases_or_layout_across_sections() -> None:
    semantic = {
        "volume": {"collection": "PO"},
        "entries": [
            {
                "entry_key": "PO025:latin:e1",
                "section_key": "PO025:latin",
                "entry_raw": "I Cor. 1, 4 | 120",
            },
            {
                "entry_key": "PO025:french:e1",
                "section_key": "PO025:french",
                "entry_raw": "I Corinthiens 1, 4   220",
            },
        ],
        "scripture_refs": [
            {
                "entry_key": "PO025:latin:e1",
                "ref_order": 1,
                "book_raw": "I Cor.",
                "chapter_start": 1,
                "verse_start": 4,
            },
            {
                "entry_key": "PO025:french:e1",
                "ref_order": 1,
                "book_raw": "I Corinthiens",
                "chapter_start": 1,
                "verse_start": 4,
            },
        ],
    }

    profiles = infer_citation_format_profiles(semantic)

    assert profiles["PO025:latin"]["collection_profile"] == "section_local_multilingual"
    assert profiles["PO025:latin"]["pattern_reuse_scope"] == "section_only"
    assert profiles["PO025:latin"]["observed_book_aliases"] == ["I Cor."]
    assert profiles["PO025:latin"]["column_style"] == "pipe"
    assert profiles["PO025:french"]["observed_book_aliases"] == ["I Corinthiens"]
    assert profiles["PO025:french"]["column_style"] == "spaced"


def test_pg_and_pl_share_migne_collection_profile() -> None:
    semantic = {
        "entries": [
            {
                "entry_key": "VOL:index:e1",
                "section_key": "VOL:index",
                "entry_raw": "Rom. I, 1. 100",
            }
        ],
        "scripture_refs": [
            {
                "entry_key": "VOL:index:e1",
                "ref_order": 1,
                "book_raw": "Rom.",
                "chapter_start": 1,
                "verse_start": 1,
            }
        ],
    }

    assert (
        infer_citation_format_profiles(semantic, collection="PG")["VOL:index"][
            "collection_profile"
        ]
        == "migne_patrologia"
    )
    assert (
        infer_citation_format_profiles(semantic, collection="PL")["VOL:index"][
            "pattern_reuse_scope"
        ]
        == "migne_volume"
    )


def test_catholic_catalog_is_complete_and_numbered_aliases_are_conservative() -> None:
    assert len(BOOKS) == 73
    assert set(CANONICAL_BOOK_LABELS) == {book.key for book in BOOKS}
    assert {
        "tobias",
        "judite",
        "sabedoria",
        "eclesiastico",
        "baruc",
        "1 macabeus",
        "2 macabeus",
    } <= {book.key for book in BOOKS}
    assert canonical_book_key("Tobiae") == "tobias"
    assert canonical_book_key("Sagesse") == "sabedoria"
    assert canonical_book_key("Ecclesiasticus") == "eclesiastico"
    assert canonical_book_key("Sir.") == "eclesiastico"
    assert canonical_book_key("II Macchabées") == "2 macabeus"
    assert canonical_book_key("I Machab.") == "1 macabeus"
    assert canonical_book_key("Machabaeorum") is None
    assert canonical_book_key("São Mateus") == "mateus"
    assert canonical_book_key("I São Pedro") == "1 pedro"
    assert canonical_book_key("I Cor.") == "1 corintios"
    assert canonical_book_key("II Corinthiens") == "2 corintios"
    assert canonical_book_key("Corinthios") is None
    assert canonical_book_key("Coríntios") is None
    assert canonical_book_key("I Regum") is None
    assert canonical_book_key("I Rois") is None
    assert canonical_book_key("Jo.") is None
    assert canonical_book_key("Jó") == "jo"
    assert canonical_book_key("Jo.", tradition="migne_patrologia") == "joao"
    assert canonical_book_key("Osè.") == "oseias"
    assert canonical_book_key("Philip.") == "filipenses"
    assert canonical_book_key("Judices") == "juizes"
    assert canonical_book_key("Psalterium") == "salmos"
    assert canonical_book_key("I Paralip.") == "1 cronicas"
    assert canonical_book_key("II Paralipomènes") == "2 cronicas"
    assert canonical_book_key("Iyob") == "jo"
    assert canonical_book_key("Liber Sapientiæ") == "sabedoria"
    assert canonical_book_key("Liber Ecclesiastici") == "eclesiastico"
    assert canonical_book_key("Evangelium secundum Matthæum") == "mateus"
    assert canonical_book_key("Epist. I ad Corinthios") == "1 corintios"
    assert canonical_book_key("Numerorum") == "numeros"
    assert canonical_book_key("Proverbiorum") == "proverbios"
    assert canonical_book_key("Psalmorum") == "salmos"
    assert canonical_book_key("Deuter.") == "deuteronomio"
    assert canonical_book_key("Levit.") == "levitico"
    assert canonical_book_key("Habac.") == "habacuc"
    assert canonical_book_key("Malach.") == "malaquias"
    assert canonical_book_key("Sap. Salom.") == "sabedoria"
    assert canonical_book_key("Mich.") == "miqueias"
    assert canonical_book_key("Zachar.") == "zacarias"
    assert canonical_book_key("Jerem.") == "jeremias"
    assert canonical_book_key("I Thessal.") == "1 tessalonicenses"
    assert canonical_book_key("Act. Apost.") == "atos"
    assert canonical_book_key("Jacobi") == "tiago"
    assert canonical_book_key("Joannes") == "joao"
    assert canonical_book_key("Ephesios") == "efesios"
    assert canonical_book_key("Esaias") == "isaias"
    assert canonical_book_key("III Regum") == "1 reis"
    assert canonical_book_key("Esdrae") is None
    assert (
        canonical_book_key("I Regum", tradition="migne_patrologia")
        == "1 samuel"
    )
    assert (
        canonical_book_key("II Regum", tradition="vulgate_migne")
        == "2 samuel"
    )
    assert canonical_book_key("I Esdrae", tradition="vulgate") == "esdras"
    assert canonical_book_key("II Esdrae", tradition="vulgate") == "neemias"
    assert canonical_book_key("I Esdr.", tradition="vulgate") == "esdras"
    assert canonical_book_key("II Esdr.", tradition="vulgate") == "neemias"
    assert canonical_book_key("I Rois", tradition="po_french_editorial") == "1 samuel"
    assert canonical_book_key("II Rois", tradition="old_french_vulgate") == "2 samuel"
    assert canonical_book_key("III Rois", tradition="PO") == "1 reis"
    assert canonical_book_key("IV Rois", tradition="patrologia_orientalis") == "2 reis"
    assert (
        canonical_book_key("I Kings", tradition="po_old_english")
        == "1 samuel"
    )
    assert (
        canonical_book_key("III Kings", tradition="old_english_vulgate")
        == "1 reis"
    )
    assert historical_noncanonical_book_key("III Esdras") == "3 esdras"
    assert historical_noncanonical_book_key("Liber IV Esdræ") == "4 esdras"


def test_table_observations_never_cross_section_boundaries(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PO025" / "text"
    source_root.mkdir(parents=True)
    first_index = source_root / "page-010.txt"
    first_index.write_text("I CORINTHIOS\n1, 4   1260\n", encoding="utf-8")
    second_index = source_root / "page-020.txt"
    second_index.write_text("I CORINTHIOS\n1, 4   2260\n", encoding="utf-8")
    first = locator_item(
        source_root=source_root,
        section_start=first_index,
        section_end=first_index,
        section_key="PO025:first",
        entry_key="PO025:first:e1",
    )
    second = locator_item(
        source_root=source_root,
        section_start=second_index,
        section_end=second_index,
        cited_page=2260,
        section_key="PO025:second",
        entry_key="PO025:second:e1",
    )

    items, _ = add_scripture_evidence_candidates(
        [first, second],
        source_root=source_root,
        collection="PO",
    )

    assert items[0]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [1260]
    assert items[1]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [2260]
    assert items[0]["candidates"] == []
    assert items[1]["candidates"] == []


def test_book_heading_state_is_kept_per_layout_column(tmp_path: Path) -> None:
    source_root = tmp_path / "PO025" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text(
        "I CORINTHIOS | II CORINTHIOS\n"
        "1, 4   1260 | 1, 4   2260\n",
        encoding="utf-8",
    )
    first = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        section_key="PO025:parallel",
        entry_key="PO025:parallel:e1",
    )
    second = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=2260,
        book_raw="II Cor.",
        book_norm="2 Coríntios",
        section_key="PO025:parallel",
        entry_key="PO025:parallel:e2",
    )
    profile = {
        "PO025:parallel": {
            "column_style": "pipe",
            "inherits_book_heading": True,
            "pattern_reuse_scope": "section_only",
            "observed_book_aliases": ["I Cor.", "II Cor."],
        }
    }

    items, _ = add_scripture_evidence_candidates(
        [first, second],
        source_root=source_root,
        collection="PO",
        citation_format_profiles=profile,
    )

    assert items[0]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [1260]
    assert items[1]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [2260]


def test_profile_can_disable_heading_inheritance(tmp_path: Path) -> None:
    source_root = tmp_path / "PO025" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("I CORINTHIOS\n1, 4   1260\n", encoding="utf-8")
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
    )

    items, artifact = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        collection="PO",
        citation_format_profiles={
            "PL001:index": {
                "column_style": "linear",
                "inherits_book_heading": False,
                "pattern_reuse_scope": "section_only",
                "observed_book_aliases": ["I Cor."],
            }
        },
    )

    assert "table_reference_suggestions" not in items[0]
    assert artifact["table_repair_groups"][0]["section_key"] == "PL001:index"


def test_psalm_apparatus_heading_supplies_book_and_chapter_state(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL036" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text(
        "EX PSALMO I.\n"
        "v. 4-5   1260\n",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        book_raw="Psal.",
        book_norm="Salmos",
        chapter=1,
        verse=4,
        section_key="PL036:psalm-apparatus",
        entry_key="PL036:psalm-apparatus:e1",
    )

    items, _ = add_scripture_evidence_candidates(
        [item],
        source_root=source_root,
        collection="PL",
        citation_format_profiles={
            "PL036:psalm-apparatus": {
                "column_style": "linear",
                "inherits_book_heading": True,
                "pattern_reuse_scope": "migne_volume",
                "observed_book_aliases": ["Psal."],
            }
        },
    )

    assert items[0]["table_reference_suggestions"][0][
        "suggested_editorial_pages"
    ] == [1260]


def test_preexisting_candidates_inside_any_index_section_are_removed(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    first_index = source_root / "page-010.txt"
    first_index.write_text("INDEX A", encoding="utf-8")
    other_index = source_root / "page-020.txt"
    other_index.write_text("I Cor. 1, 4", encoding="utf-8")
    first = locator_item(
        source_root=source_root,
        section_start=first_index,
        section_end=first_index,
    )
    first["candidates"] = [
        {
            "file": str(other_index),
            "probability": 0.95,
            "candidate_role": "helper_guess",
        }
    ]
    second = locator_item(
        source_root=source_root,
        section_start=other_index,
        section_end=other_index,
        section_key="PL001:other-index",
        entry_key="PL001:other-index:e1",
    )
    second.pop("scripture_ref")

    items, artifact = add_scripture_evidence_candidates(
        [first, second],
        source_root=source_root,
    )

    assert items[0]["candidates"] == []
    assert artifact["items"][0]["excluded_index_hit_count"] >= 1


def test_soft_wrap_offsets_keep_apparatus_detection_on_source_text(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    (source_root / "page-001.txt").write_text(
        "<apparatus>I Cor-\ninthios 1, 4-5 cod. A; ms. B</apparatus>",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
    )

    items, _ = add_scripture_evidence_candidates([item], source_root=source_root)

    candidate = items[0]["candidates"][0]
    assert candidate["probability"] == 0.82
    assert any(
        evidence["kind"] == "critical_apparatus_context"
        for evidence in candidate["evidence"]
    )


def test_unrelated_header_integer_does_not_confirm_editorial_page(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "page-010.txt"
    index_file.write_text("INDEX LOCORUM", encoding="utf-8")
    (source_root / "page-001.txt").write_text(
        '<bloco tipo="cabecalho">HOMILIA 715 ANNO 2020</bloco>\n'
        "I Cor. 1, 4",
        encoding="utf-8",
    )
    item = locator_item(
        source_root=source_root,
        section_start=index_file,
        section_end=index_file,
        cited_page=715,
    )

    items, _ = add_scripture_evidence_candidates([item], source_root=source_root)

    candidate = items[0]["candidates"][0]
    assert candidate["probability"] == 0.66
    assert all(
        evidence["kind"] != "editorial_header_match"
        for evidence in candidate["evidence"]
    )


def test_micro_prompt_preserves_row_layout() -> None:
    prompt = build_scripture_table_repair_prompt(
        volume_id="PO025",
        section_profile={"column_style": "tab"},
        unresolved_lines=[
            {
                "line_id": "row-1",
                "raw_line": "I Cor. 1, ?\t\t1260   1262",
                "partial_json": {},
            }
        ],
    )

    encoded_rows = prompt.split("UNRESOLVED_ROWS\n", 1)[1].split(
        "\n\nOUTPUT",
        1,
    )[0]
    rows = json.loads(encoded_rows)
    assert rows[0]["raw_line"] == "I Cor. 1, ?\t\t1260   1262"


def test_real_pg_pl_po_samples_match_without_scanning_whole_volumes(
    tmp_path: Path,
) -> None:
    samples = [
        (
            "PG",
            ROOT
            / "teste/PG001/text/94fbe3de-f05d-46c7-a1e8-ad445e5abaef-371.txt",
            "I Cor.",
            "1 Coríntios",
            16,
            2,
        ),
        (
            "PL",
            ROOT
            / "teste/PL001/text/512b5ee5-4320-4072-92ca-fe737e68dbd8-361.txt",
            "II Cor.",
            "2 Coríntios",
            6,
            14,
        ),
        (
            "PO",
            ROOT
            / "teste/PO022/text/b594e560-bc10-4350-813e-870ec619db6f-759.txt",
            "I Cor.",
            "1 Coríntios",
            12,
            12,
        ),
    ]
    for index, (
        collection,
        sample_path,
        book_raw,
        book_norm,
        chapter,
        verse,
    ) in enumerate(samples, start=1):
        assert sample_path.is_file()
        source_root = tmp_path / f"sample-{index}" / "text"
        source_root.mkdir(parents=True)
        body_file = source_root / sample_path.name
        body_file.write_text(
            sample_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        index_file = source_root / f"index-{900 + index:03d}.txt"
        index_file.write_text("INDEX LOCORUM", encoding="utf-8")
        item = locator_item(
            source_root=source_root,
            section_start=index_file,
            section_end=index_file,
            book_raw=book_raw,
            book_norm=book_norm,
            chapter=chapter,
            verse=verse,
            section_key=f"{collection}:index",
            entry_key=f"{collection}:index:e1",
        )

        items, artifact = add_scripture_evidence_candidates(
            [item],
            source_root=source_root,
            collection=collection,
        )

        assert Path(items[0]["candidates"][0]["file"]).name == sample_path.name
        assert artifact["files_read"] == 2

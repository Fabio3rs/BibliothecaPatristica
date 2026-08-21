from patristica_pipeline.index_pipeline_ownership import (
    alphabetical_section_ownership,
    general_section_ownership,
)


def section(scope_kind: str, index_kind: str, heading_raw: str) -> dict:
    return {
        "scope_kind": scope_kind,
        "index_kind": index_kind,
        "heading_raw": heading_raw,
        "raw_json": {},
    }


def test_general_pipeline_keeps_contents_inventories() -> None:
    owned, reason = general_section_ownership(
        section("volume_end", "ORDO RERUM", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR")
    )
    assert owned is True
    assert reason is None


def test_general_pipeline_rejects_general_alphabetical_index() -> None:
    owned, reason = general_section_ownership(
        section("volume_front", "INDEX GENERALIS", "INDEX IN OMNIA OPERA SANCTI AUGUSTINI")
    )
    assert owned is False
    assert "alphabetical" in str(reason)


def test_general_pipeline_rejects_onomastic_index() -> None:
    owned, reason = general_section_ownership(
        section("volume_end", "INDEX NOMINUM", "INDEX NOMINUM PROPRIORUM")
    )
    assert owned is False
    assert "alphabetical" in str(reason)


def test_alphabetical_pipeline_rejects_volume_works_inventory() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "author_index",
            "heading_raw": (
                "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CCIV "
                "CONTINENTUR."
            ),
            "raw_json": {
                "pipeline_owner": "alphabetical",
                "alphabetical_role": "owned_section",
            },
        }
    )

    assert owned is False
    assert "works/contents inventory" in str(reason)


def test_alphabetical_pipeline_rejects_punctuated_works_inventory() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "author_index",
            "heading_raw": (
                "ELENCHUS. AUCTORUM ET OPERUM QUI IN HOC TOMO CONTINENTUR."
            ),
            "raw_json": {
                "pipeline_owner": "alphabetical",
                "alphabetical_role": "owned_section",
            },
        }
    )

    assert owned is False
    assert "works/contents inventory" in str(reason)


def test_alphabetical_pipeline_keeps_cited_author_index() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "author_index",
            "heading_raw": "INDEX AUCTORUM QUI A CLEMENTE CITANTUR",
            "raw_json": {
                "pipeline_owner": "alphabetical",
                "alphabetical_role": "owned_section",
            },
        }
    )

    assert owned is True
    assert reason is None


def test_alphabetical_pipeline_rejects_explicit_stop_boundary() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "author_index",
            "heading_raw": "INDEX",
            "raw_json": {
                "pipeline_owner": "general",
                "alphabetical_role": "stop_boundary",
            },
        }
    )

    assert owned is False
    assert "pipeline_owner=general" in str(reason)

from tools.indexing.index_pipeline_ownership import (
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


def test_general_pipeline_keeps_capitula_heading_that_mentions_scripture() -> None:
    owned, reason = general_section_ownership(
        section(
            "work_internal",
            "CAPITULA",
            (
                "LIBER SEXTUS. — Adnotationes elucidatoriæ in Scripturam, "
                "tractatum moralium fragmenta, etc."
            ),
        )
    )

    assert owned is True
    assert reason is None


def test_general_pipeline_rejects_explicit_scripture_index_mislabeled_capitula() -> None:
    owned, reason = general_section_ownership(
        section("volume_end", "CAPITULA", "INDEX LOCORUM SCRIPTURÆ")
    )

    assert owned is False
    assert "scripture-reference" in str(reason)


def test_general_pipeline_keeps_work_about_a_psalm() -> None:
    owned, reason = general_section_ownership(
        section("work_internal", "contents", "HOMILIA X IN PSALMUM L")
    )

    assert owned is True
    assert reason is None


def test_general_pipeline_keeps_index_capitum_of_a_scripture_commentary() -> None:
    owned, reason = general_section_ownership(
        section(
            "work_internal",
            "index_capitum",
            "INDEX CAPITUM COMMENTARII IN SCRIPTURAM SACRAM",
        )
    )

    assert owned is True
    assert reason is None


def test_general_pipeline_keeps_index_capitum_scripturae_sacrae() -> None:
    owned, reason = general_section_ownership(
        section(
            "work_internal",
            "index_capitum",
            "INDEX CAPITUM SCRIPTURAE SACRAE",
        )
    )

    assert owned is True
    assert reason is None


def test_general_pipeline_keeps_inventory_of_commentaries_on_scripture() -> None:
    owned, reason = general_section_ownership(
        section(
            "volume_front",
            "INDEX COMMENTAR. IN SCRIPTURAS",
            "INDEX COMMENTAR. IN SCRIPTURAS",
        )
    )

    assert owned is True
    assert reason is None


def test_general_pipeline_rejects_explicit_bible_citation_table() -> None:
    owned, reason = general_section_ownership(
        section("volume_end", "citation_index", "TABLE DES CITATIONS DE LA BIBLE")
    )

    assert owned is False
    assert "scripture-reference" in str(reason)


def test_general_pipeline_rejects_index_sacrae_scripturae() -> None:
    owned, reason = general_section_ownership(
        section(
            "volume_end",
            "INDEX SACRAE SCRIPTURAE",
            "INDEX SACRAE SCRIPTURAE CAPITUM ET LOCORUM NOTABILIUM",
        )
    )

    assert owned is False
    assert "scripture-reference" in str(reason)


def test_general_pipeline_rejects_index_verborum_in_reverse_word_order() -> None:
    owned, reason = general_section_ownership(
        section(
            "volume_end",
            "INDEX VERBORUM, SENTENTIARUM ET RERUM",
            "INDEX VERBORUM, SENTENTIARUM ET RERUM",
        )
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


def test_alphabetical_pipeline_rejects_work_whose_subject_is_a_psalm() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "scripture_index",
            "heading_raw": "EXPOSITIO IN PSALMUM CXVIII",
            "raw_json": {
                "pipeline_owner": "alphabetical",
                "alphabetical_role": "owned_section",
            },
        }
    )

    assert owned is False
    assert "work or structural unit" in str(reason)


def test_alphabetical_pipeline_keeps_onomastic_index_with_biblical_citations() -> None:
    owned, reason = alphabetical_section_ownership(
        {
            "section_kind": "onomastic_mixed",
            "heading_raw": "INDEX RERUM ET NOMINUM",
            "raw_json": {
                "pipeline_owner": "alphabetical",
                "alphabetical_role": "owned_section",
                "scripture_mode": "incidental_mention",
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

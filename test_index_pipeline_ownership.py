from tools.indexing.index_pipeline_ownership import general_section_ownership


def section(heading: str, *, index_kind: str | None = None, scope_kind: str = "volume_end"):
    return {
        "scope_kind": scope_kind,
        "index_kind": index_kind or heading,
        "heading_raw": heading,
    }


def test_general_rejects_explicit_closing_index_headings():
    headings = [
        "INDEX LOCUPLETISSIMUS RERUM MEMORABILIUM",
        "NOMENCLATOR VOCUM EXOTICARUM",
        "INDEX TITULORUM",
        "SYLLABUS AUCTORUM IN ANNALIBUS LAUDATORUM",
        "ORDO NOVUS CUM VETERI COLLATUS",
        "SERMONUM ORDO VETUS CUM NOVO COMPARATUS",
    ]

    for heading in headings:
        owned, reason = general_section_ownership(section(heading))
        assert owned is False, (heading, reason)
        assert reason


def test_general_keeps_structural_works_and_contents_headings():
    headings = [
        "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CONTINENTUR",
        "SYLLABUS AUCTORUM ET OPERUM QUAE IN HOC VOLUMINE CONTINENTUR",
        "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR",
    ]

    for heading in headings:
        owned, reason = general_section_ownership(section(heading, scope_kind="volume_front"))
        assert owned is True, (heading, reason)
        assert reason is None

from __future__ import annotations

from pathlib import Path

from tools.indexing.index_localization_helpers import build_helper_request_artifact


def _write_page(path: Path, header: str, body: str) -> None:
    path.write_text(
        (
            "<pagina>"
            f'<bloco tipo="cabecalho">{header}</bloco>'
            f'<bloco tipo="texto_principal">{body}</bloco>'
            "</pagina>"
        ),
        encoding="utf-8",
    )


def test_helper_uses_section_neighborhood_and_rejects_frontmatter_noise(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-{number:03d}.txt"
        for number in range(1, 12)
    ]
    for page in pages:
        _write_page(page, "TITLE", "ordinary prose")
    _write_page(pages[0], "FRONT", "Paris, le 29 mai 1903.")
    _write_page(
        pages[7],
        "101 INDEX CAPITUM 102",
        "Epistola ad Corinthios,\ninterprete Cardinale ........ 199",
    )

    request = build_helper_request_artifact(
        volume_id="PL001",
        source_root=source_root,
        filtered_pages={
            "profile": "general",
            "candidate_files": [str(page) for page in pages],
            "candidate_sections": [
                {
                    "file": str(pages[7]),
                    "heading": "INDEX CAPITUM",
                    "marker": "INDEX CAPITUM",
                }
            ],
        },
        helper_request_json=tmp_path / "helper.json",
        workers=17,
    )

    assert request["options"]["workers"] == 17
    assert [entry["lemma_raw"] for entry in request["entries"]] == [
        "Epistola ad Corinthios, interprete Cardinale"
    ]

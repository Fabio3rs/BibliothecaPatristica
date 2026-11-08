import json
import sqlite3

import pytest

from scripts.study_index_openings import parse_job, run
from tools.indexing.pg104_opening_grammar import analyze_opening


def pg_raw(body, syntagma=False):
    title = (
        "INDEX ANALYTICUS RERUM NOTABILIORUM QUÆ IN SYNTAGMATE INVENIUNTUR."
        if syntagma
        else "INDEX RERUM ET VERBORUM MEMORABILIUM QUÆ IN PHOTII BIBLIOTHECA REPERIUNTUR."
    )
    notice = (
        "Revocatur lector ad numeros crassiores textui Latino insertos."
        if syntagma
        else "Revocatur lector ad numeros grandiores textui Latino intermistos."
    )
    return (
        f'<bloco tipo="cabecalho">{title}</bloco>'
        f'<bloco tipo="nota">{notice}</bloco>'
        f'<bloco tipo="texto_principal">{body}</bloco>'
    )


def test_raw_replay_preserves_sources_namespaces_and_parallel_determinism(tmp_path):
    root = tmp_path / "corpus"
    pg = root / "PG104/text/page-777.txt"
    pl = root / "PL217/text/page-585.txt"
    pg.parent.mkdir(parents=True)
    pl.parent.mkdir(parents=True)
    pg.write_text(pg_raw("Abaris, 574 a."))
    pl.write_text(
        '<bloco tipo="cabecalho">INDEX EPISTOLARUM\n'
        "PRIOR NUMERUS LIBRUM, POSTERIOR EPISTOLAM INDICAT.</bloco>"
        '<bloco tipo="texto_principal">Gravem dilecti. XV, 104.</bloco>'
    )
    before = {p: p.read_bytes() for p in (pg, pl)}
    sections = [
        dict(
            id="bibliotheca",
            volume="PG104",
            grammar="pg104-openings-v1",
            namespace="Photius:Bibliotheca:Latin-internal-number-ab",
            opening_source="PG104/text/page-777.txt",
            first=777,
            last=777,
        ),
        dict(
            id="epistles",
            volume="PL217",
            grammar="pl217-openings-v1",
            namespace="book_epistle",
            opening_source="PL217/text/page-585.txt",
            first=585,
            last=585,
        ),
    ]
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(dict(version="index-opening-study-v1", sections=sections))
    )
    a = run(plan, root, tmp_path / "serial", workers=1)
    b = run(plan, root, tmp_path / "parallel", workers=2)
    assert a["logical_sha256"] == b["logical_sha256"]
    assert a["counts"]["entries"] == a["counts"]["reference_groups"] == 2
    assert all(p.read_bytes() == contents for p, contents in before.items())
    with sqlite3.connect(tmp_path / "serial/extraction.sqlite") as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {r[0] for r in db.execute("SELECT namespace FROM reference_groups")} == {
            s["namespace"] for s in sections
        }
        assert (
            db.execute(
                "SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'Abaris'"
            ).fetchone()[0]
            == 1
        )


def test_local_opening_cannot_be_saved_under_inherited_namespace(tmp_path):
    path = tmp_path / "806.txt"
    path.write_text(pg_raw("Abbas presbyter, 135.", syntagma=True))
    opening = analyze_opening(pg_raw("Abaris, 574 a."))
    section = dict(
        id="bibliotheca", grammar="pg104-openings-v1", namespace=opening["namespace"]
    )
    with pytest.raises(ValueError, match="contradicts section namespace"):
        parse_job((section, opening, str(path), 806))

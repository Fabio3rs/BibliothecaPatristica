from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.alphabetical_material_audit import audit_extracted_material
from scripts.alphabetical_index_db import connect_db, init_schema


def _page(path: Path, header: str, body: str) -> None:
    path.write_text(
        f'<pagina><bloco tipo="cabecalho">{header}</bloco>'
        f'<bloco tipo="texto_principal">{body}</bloco></pagina>',
        encoding="utf-8",
    )


def _payload(source_root: Path, *, target_file: str | None) -> dict:
    index_file = source_root / "index-010.txt"
    return {
        "schema_version": 1,
        "generated_at": None,
        "volume": {
            "volume_id": "PL001",
            "collection": "PL",
            "source_root": str(source_root),
            "volume_label": "PL 1",
        },
        "sections": [
            {
                "section_key": "PL001:index",
                "section_kind": "onomastic_person",
                "heading_raw": "INDEX ONOMASTICUS IN CHRONICON",
                "file_start": str(index_file),
                "file_end": str(index_file),
            }
        ],
        "nodes": [],
        "entries": [
            {
                "entry_key": "PL001:index:e1",
                "section_key": "PL001:index",
                "entry_order": 1,
                "entry_kind": "lemma",
                "lemma_raw": "Gregorius II",
                "entry_raw": "Gregorius II 114",
                "context_raw": "Gregorius II 114",
            }
        ],
        "refs": [
            {
                "entry_key": "PL001:index:e1",
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": "114",
                "page_ref_raw": "114",
                "page_ref_int": 114,
                "section_start_file": str(index_file),
                "target_file": target_file,
                "target_file_probability": 0.9 if target_file else None,
                "raw_json": {},
            }
        ],
        "scripture_refs": [],
        "coverage": {},
        "notes": [],
    }


def test_structural_audit_finds_payload_db_drift_and_missing_pieces(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    index_file = source_root / "index-010.txt"
    _page(index_file, "INDEX", "Gregorius II 114; Zacharias 119")
    payload = _payload(source_root, target_file=str(source_root / "missing-001.txt"))
    payload["entries"].append(
        {
            "entry_key": "PL001:index:e2",
            "section_key": "PL001:index",
            "entry_order": 2,
            "entry_kind": "lemma",
            "lemma_raw": "Zacharias",
            "entry_raw": "Zacharias 119",
        }
    )
    db_path = tmp_path / "alphabetical.db"
    with connect_db(db_path) as con:
        init_schema(con)
        con.execute(
            """
            INSERT INTO alphabetical_volumes (
                volume_id, collection, source_root, volume_label,
                notes, created_at, updated_at
            ) VALUES ('PL001', 'PL', ?, 'PL 1', NULL, 'now', 'now')
            """,
            (str(source_root),),
        )
        con.commit()

    report = audit_extracted_material(
        payload,
        db_path=db_path,
        run_ocr=False,
    )

    assert report["issue_counts"]["target_file_missing"] == 1
    assert report["issue_counts"]["locator_text_without_refs"] == 1
    assert report["issue_counts"]["section_missing_from_db"] == 1
    assert report["issue_counts"]["entry_missing_from_db"] == 1
    assert report["issue_counts"]["ref_missing_from_db"] == 1
    assert any(
        item["category"] == "structural_issue"
        for item in report["review_queue"]
    )


def test_mechanical_audit_proposes_replacing_wrong_internal_locator_target(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    wrong = source_root / "work-a-001.txt"
    target = source_root / "work-a-002.txt"
    _page(wrong, "151 CHRONICON 152", "Aliud argumentum.")
    _page(target, "153 CHRONICON 154", "Gregorius secundus pontifex. 114")
    _page(source_root / "work-a-003.txt", "155 CHRONICON 156", "Continuatio.")
    _page(source_root / "index-010.txt", "1001 INDEX 1002", "Gregorius II 114")
    payload = _payload(source_root, target_file=str(wrong))

    report = audit_extracted_material(payload, max_refs=10, workers=1)

    mechanical = report["mechanical"]
    assert mechanical["action_counts"] == {"replace_suspicious_target": 1}
    result = mechanical["results"][0]
    assert result["proposed_target_ocr_file"] == str(target)
    assert "body_locator_name_unique" in result["top_evidence_kinds"]
    assert "internal_locator_vs_editorial_sequence" in result["top_evidence_kinds"]
    assert report["review_queue"][0]["category"] == "deterministic_target_repair"

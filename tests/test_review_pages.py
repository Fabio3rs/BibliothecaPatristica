import io
import json
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review
import sample


@pytest.fixture
def page_db(tmp_path, monkeypatch):
    db = tmp_path / "manual.db"
    image = tmp_path / "page.png"
    Image.new("RGB", (500, 700), "white").save(image)
    conn = sample.open_db(str(db))
    session = sample.create_session(conn, "gold manual", 0, ["PG002"])
    for index in range(3):
        sample.insert_line(conn, session, dict(page_id="PG002-377", volume="PG002", image_path=str(image),
            line_index=index, bbox=dict(x=20, y=50 + 40 * index, w=200, h=25),
            line_image=sample.crop_line(np.full((700, 500), 255, dtype=np.uint8), dict(x=20, y=50, w=200, h=25)),
            tesseract_text=f"Texto da linha {index + 1}", detected_lang="Latin"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(review, "DB_PATH", str(db))
    return db, image, review.app.test_client()


def test_fresh_sample_database_uses_configured_path_and_pending_queue(page_db):
    db, _, client = page_db
    response = client.get("/?status=pending")
    assert response.status_code == 200
    assert "PG002-377" in response.text
    assert "Revisar página" in response.text
    with sqlite3.connect(db) as conn:
        assert "score_llm" in {r[1] for r in conn.execute("PRAGMA table_info(lines)")}


def test_page_has_only_requested_lines_and_shared_deskew(page_db):
    _, image, client = page_db
    response = client.get("/page/PG002-377")
    assert response.status_code == 200
    assert response.text.count('class="line-card"') == 3
    assert 'viewBox="0 0 500 700"' in response.text
    assert 'data-line="2" x="20.0" y="90.0"' in response.text
    rendered = client.get("/page/PG002-377/image")
    actual = cv2.imdecode(np.frombuffer(rendered.data, np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(actual, sample.deskew_like_sample_color(cv2.imread(str(image))))
    assert client.get("/line/1/image").mimetype == "image/png"
    assert client.get("/page/PG002-999").status_code == 404
    assert client.get("/page/PG002-999/image").status_code == 404


def test_page_list_filters_and_progress(page_db):
    db, _, client = page_db
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE lines SET status='corrected', reviewed_text='texto revisado' WHERE id=1")
    response = client.get("/pages?volume=PG002&session=1&q=377")
    assert response.status_code == 200
    assert "1 / 3 linhas revisadas" in response.text
    assert "Nenhuma página" in client.get("/pages?volume=PL001").text


def test_legacy_page_without_versions_can_be_opened_and_reviewed(page_db):
    db, _, client = page_db
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE line_versions")
        conn.execute("UPDATE lines SET qwen_text='Leitura Qwen legada' WHERE id=1")
    response = client.get("/page/PG002-377")
    assert response.status_code == 200
    assert 'Texto da linha 1' in response.text
    assert 'Leitura Qwen legada' in response.text
    assert response.text.count('class="line-card"') == 3
    assert client.get("/page/PG002-377/image").status_code == 200
    saved = client.post("/submit", headers={"Accept": "application/json"}, data={
        "line_id": 1, "action": "corrected", "reviewed_text": "Texto legado corrigido",
    })
    assert saved.status_code == 200
    assert "Texto legado corrigido" in client.get("/page/PG002-377").text
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='line_versions'").fetchone() is None


def test_save_review_json_nfc_and_validation(page_db):
    db, _, client = page_db
    data = dict(line_id=1, reviewed_text="Cafe\u0301 corrigido", action="corrected")
    response = client.post("/submit", data=data, headers={"Accept": "application/json"})
    assert response.json == dict(line_id=1, reviewed_text="Café corrigido", status="corrected")
    with sqlite3.connect(db) as conn:
        assert tuple(conn.execute("SELECT status, reviewed_text FROM lines WHERE id=1").fetchone()) == ("corrected", "Café corrigido")
    assert client.post("/submit", data={**data, "action": "pending"}).status_code == 400
    assert client.post("/submit", data={**data, "reviewed_text": " "}).status_code == 400
    assert client.post("/submit", data={**data, "line_id": 999}).status_code == 404
    assert client.post("/submit", data={**data, "action": "rejected", "reviewed_text": ""}).status_code == 302


def test_missing_image_and_invalid_bbox_are_visible(page_db):
    db, image, client = page_db
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE lines SET bbox=? WHERE id=1", (json.dumps(dict(x=600, y=0, w=10, h=10)),))
    assert "BBox inválido" in client.get("/page/PG002-377").text
    image.unlink()
    assert "Facsímile indisponível" in client.get("/page/PG002-377").text
    assert client.get("/page/PG002-377/image").status_code == 404


@pytest.mark.parametrize("box", [None, "null", "[]", "{}", '{"x":NaN,"y":0,"w":2,"h":2}',
                                '{"x":0,"y":0,"w":-2,"h":2}'])
def test_invalid_bbox_is_not_rendered(box):
    assert review.valid_page_bbox(box, 500, 700) is None

"""Testes unitários para ``ocr_versions_db``."""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import ocr_versions_db as vdb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """Conexão em memória com schema inicializado."""
    con = vdb.open_versions_db(tmp_path / "test_versions.db")
    return con


@pytest.fixture()
def sample_image(tmp_path: Path) -> Path:
    """Cria uma imagem fake (basta ter bytes determinísticos)."""
    img = tmp_path / "PO009" / "images" / "page_000001.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"fake-image-content-v1")
    return img


@pytest.fixture()
def sample_image_v2(tmp_path: Path) -> Path:
    """Segunda imagem com conteúdo diferente."""
    img = tmp_path / "PO009" / "images" / "page_000001_v2.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"fake-image-content-v2")
    return img


SYSTEM_PROMPT = "Você é um assistente de OCR."
USER_PROMPT = "Transcreva a imagem a seguir."
SYSTEM_PROMPT_ALT = "Você é um especialista em paleografia."


# ---------------------------------------------------------------------------
# Testes de hash
# ---------------------------------------------------------------------------


class TestSha256:
    def test_str_deterministic(self):
        h1 = vdb.sha256_str("hello")
        h2 = vdb.sha256_str("hello")
        assert h1 == h2
        assert len(h1) == 64  # hex SHA256

    def test_str_differs_for_different_texts(self):
        assert vdb.sha256_str("abc") != vdb.sha256_str("abd")

    def test_file_matches_content(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        content = b"some binary content"
        f.write_bytes(content)
        import hashlib

        expected = hashlib.sha256(content).hexdigest()
        assert vdb.sha256_file(f) == expected


# ---------------------------------------------------------------------------
# Testes de engine_version
# ---------------------------------------------------------------------------


class TestEngineVersion:
    def test_create_idempotent(self, db: sqlite3.Connection):
        id1 = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
        )
        id2 = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
        )
        assert id1 == id2

    def test_stores_prompt_content(self, db: sqlite3.Connection):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
        )
        row = vdb.get_engine_version(db, ev_id)
        assert row is not None
        assert row["system_prompt_content"] == SYSTEM_PROMPT
        assert row["user_prompt_content"] == USER_PROMPT
        assert row["system_prompt_hash"] == vdb.sha256_str(SYSTEM_PROMPT)
        assert row["user_prompt_hash"] == vdb.sha256_str(USER_PROMPT)

    def test_new_id_when_prompt_changes(self, db: sqlite3.Connection):
        id1 = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        id2 = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT_ALT,
        )
        assert id1 != id2

    def test_hash_uses_template_not_rendered(self, db: sqlite3.Connection):
        template = "Transcreva: {tesseract_text}"
        rendered = "Transcreva: Lorem ipsum dolor sit amet..."
        id_template = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT_VERIFY_TESSERACT",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=template,
        )
        id_rendered = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT_VERIFY_TESSERACT",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=rendered,
        )
        # Chamador deve usar o template, não o valor renderizado
        assert id_template != id_rendered

    def test_model_none_vs_empty(self, db: sqlite3.Connection):
        """model=None e model='' devem coalescer para o mesmo id."""
        id1 = vdb.get_or_create_engine_version(
            db,
            engine="tesseract",
            model=None,
            prompt_key="tesseract_direct",
            system_prompt="",
        )
        id2 = vdb.get_or_create_engine_version(
            db,
            engine="tesseract",
            model="",
            prompt_key="tesseract_direct",
            system_prompt="",
        )
        # None coalesces to '' na query → mesmo registro
        # (modelo armazenado pode ser NULL ou '', ambos devem casar)
        assert id1 == id2

    def test_empty_user_prompt_defaults(self, db: sqlite3.Connection):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        row = vdb.get_engine_version(db, ev_id)
        assert row["user_prompt_hash"] == ""
        assert row["user_prompt_content"] == ""


# ---------------------------------------------------------------------------
# Testes de record_ocr_result
# ---------------------------------------------------------------------------


class TestRecordOcrResult:
    def _make_ev(self, db: sqlite3.Connection) -> int:
        return vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )

    def test_marks_previous_as_not_current(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        id1 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto v1",
            reprocess_reason="initial",
        )
        id2 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto v2",
            reprocess_reason="verify_failed",
        )
        assert id1 != id2

        r1 = db.execute(
            "SELECT is_current FROM ocr_results WHERE id = ?", (id1,)
        ).fetchone()
        r2 = db.execute(
            "SELECT is_current FROM ocr_results WHERE id = ?", (id2,)
        ).fetchone()
        assert r1["is_current"] == 0
        assert r2["is_current"] == 1

    def test_idempotency_same_image_engine_text(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        id1 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto idêntico",
        )
        id2 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto idêntico",
        )
        assert id1 == id2

        # Apenas 1 registro no banco
        count = db.execute("SELECT count(*) as c FROM ocr_results").fetchone()["c"]
        assert count == 1

    def test_text_stored_verbatim(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        original = "Conteúdo com acentos: àéîõü — «» ❧\n\ttab e newlines\n"
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content=original,
        )
        row = db.execute(
            "SELECT text_content FROM ocr_results WHERE id = ?", (rid,)
        ).fetchone()
        assert row["text_content"] == original

    def test_meta_json_stored(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        meta = {
            "reprocess_mode": "verify_tesseract",
            "tesseract_text_hash": vdb.sha256_str("tesseract draft"),
            "tesseract_lang": "lat+grc",
        }
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="resultado v1",
            meta_json=json.dumps(meta),
        )
        row = db.execute(
            "SELECT meta_json FROM ocr_results WHERE id = ?", (rid,)
        ).fetchone()
        assert json.loads(row["meta_json"]) == meta

    def test_meta_json_null_when_initial(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="resultado",
        )
        row = db.execute(
            "SELECT meta_json FROM ocr_results WHERE id = ?", (rid,)
        ).fetchone()
        assert row["meta_json"] is None

    def test_duration_ms_stored(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = self._make_ev(db)
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="txt",
            duration_ms=1234.5,
        )
        row = db.execute(
            "SELECT duration_ms FROM ocr_results WHERE id = ?", (rid,)
        ).fetchone()
        assert row["duration_ms"] == pytest.approx(1234.5)

    def test_image_hash_param_skips_file_read(
        self, db: sqlite3.Connection, tmp_path: Path
    ):
        """Quando image_hash é fornecido, o arquivo não precisa existir."""
        ev_id = self._make_ev(db)
        fake_path = tmp_path / "nonexistent.png"
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=fake_path,
            engine_version_id=ev_id,
            text_content="txt",
            image_hash="aabbccdd" * 8,  # 64 hex chars
        )
        row = db.execute(
            "SELECT image_hash FROM ocr_results WHERE id = ?", (rid,)
        ).fetchone()
        assert row["image_hash"] == "aabbccdd" * 8


# ---------------------------------------------------------------------------
# Testes de consulta
# ---------------------------------------------------------------------------


class TestQueries:
    def _setup_history(
        self, db: sqlite3.Connection, sample_image: Path
    ) -> list[int]:
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        ids = []
        for i in range(1, 4):
            rid = vdb.record_ocr_result(
                db,
                volume_id="PO009",
                page_num=1,
                image_path=sample_image,
                engine_version_id=ev_id,
                text_content=f"texto versão {i}",
                reprocess_reason="initial" if i == 1 else "verify_failed",
            )
            ids.append(rid)
        return ids

    def test_get_current_returns_latest(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ids = self._setup_history(db, sample_image)
        current = vdb.get_current_result(db, "PO009", 1)
        assert current is not None
        assert current["id"] == ids[-1]
        assert current["is_current"] == 1
        assert current["text_content"] == "texto versão 3"

    def test_get_current_returns_none_if_no_results(
        self, db: sqlite3.Connection
    ):
        assert vdb.get_current_result(db, "PO999", 99) is None

    def test_get_history_returns_all_versions_desc(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ids = self._setup_history(db, sample_image)
        history = vdb.get_history(db, "PO009", 1)
        assert len(history) == 3
        # Mais recente primeiro (ORDER BY created_at DESC, id DESC)
        history_ids = [r["id"] for r in history]
        assert history_ids == sorted(history_ids, reverse=True)

    def test_get_history_empty_for_unknown_page(
        self, db: sqlite3.Connection
    ):
        assert vdb.get_history(db, "PO999", 99) == []

    def test_multiple_reruns_produce_growing_history(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        for i in range(5):
            vdb.record_ocr_result(
                db,
                volume_id="PO001",
                page_num=10,
                image_path=sample_image,
                engine_version_id=ev_id,
                text_content=f"run {i}",
            )
        assert len(vdb.get_history(db, "PO001", 10)) == 5


# ---------------------------------------------------------------------------
# Testes de diff dinâmico
# ---------------------------------------------------------------------------


class TestDiff:
    def _two_results(
        self, db: sqlite3.Connection, sample_image: Path, t1: str, t2: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        """Insere t1 depois t2, retorna (older=t1, newer=t2)."""
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        id1 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content=t1,
        )
        id2 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content=t2,
        )
        r1 = db.execute("SELECT * FROM ocr_results WHERE id=?", (id1,)).fetchone()
        r2 = db.execute("SELECT * FROM ocr_results WHERE id=?", (id2,)).fetchone()
        return r1, r2  # older, newer

    def test_equal_texts_hash_shortcut(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        """Quando text_hash é igual, retorna text_equal=True sem calcular."""
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        # Textos iguais → idempotência → precisamos de engines diferentes
        ev_id2 = vdb.get_or_create_engine_version(
            db,
            engine="openai",
            model="gpt-5",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        same_text = "texto idêntico nas duas versões"
        id1 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content=same_text,
        )
        id2 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id2,
            text_content=same_text,
        )
        r1 = db.execute("SELECT * FROM ocr_results WHERE id=?", (id1,)).fetchone()
        r2 = db.execute("SELECT * FROM ocr_results WHERE id=?", (id2,)).fetchone()
        d = vdb.compute_diff(r1, r2)
        assert d["text_equal"] is True
        assert d["unified"] == ""
        assert d["stat"] == {}

    def test_produces_valid_unified_diff(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        r_old, r_new = self._two_results(
            db, sample_image, "linha 1\nlinha 2\n", "linha 1\nlinha 2 modificada\n"
        )
        d = vdb.compute_diff(r_old, r_new)
        assert d["text_equal"] is False
        assert "---" in d["unified"]
        assert "+++" in d["unified"]
        assert "-linha 2\n" in d["unified"]
        assert "+linha 2 modificada\n" in d["unified"]

    def test_stat_counts(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        r_old, r_new = self._two_results(
            db, sample_image, "a\nb\nc\n", "a\nc\nd\n"
        )
        d = vdb.compute_diff(r_old, r_new)
        stat = d["stat"]
        assert stat["deletions"] >= 1  # "b" removido
        assert stat["insertions"] >= 1  # "d" adicionado
        assert stat["old_len"] == 3
        assert stat["new_len"] == 3

    def test_stat_only_mode(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        r_old, r_new = self._two_results(
            db, sample_image, "alfa\n", "beta\n"
        )
        d = vdb.compute_diff(r_old, r_new, mode="stat_only")
        assert d["unified"] == ""
        assert d["stat"]["insertions"] >= 1

    def test_chain_two_versions(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        for t in ["v1\n", "v2\n", "v3\n"]:
            vdb.record_ocr_result(
                db,
                volume_id="PO009",
                page_num=1,
                image_path=sample_image,
                engine_version_id=ev_id,
                text_content=t,
            )
        chain = vdb.compute_diff_chain(db, "PO009", 1)
        assert len(chain) == 2
        assert chain[0]["from_id"] < chain[0]["to_id"]
        assert chain[1]["from_id"] < chain[1]["to_id"]

    def test_chain_skips_identical(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        # v1, v2 diferente, v3 igual a v2 (mas engine diferente para evitar idempotência)
        ev_id2 = vdb.get_or_create_engine_version(
            db,
            engine="openai",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="v1",
        )
        vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id2,
            text_content="v2",
        )
        vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="v2",  # mesmo texto que v2
        )
        chain = vdb.compute_diff_chain(db, "PO009", 1)
        assert len(chain) == 2
        assert chain[0]["text_equal"] is False  # v1 → v2
        assert chain[1]["text_equal"] is True   # v2 → v3 (mesmo texto)


# ---------------------------------------------------------------------------
# Testes de resiliência / concorrência
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_writes_exactly_one_is_current(
        self, tmp_path: Path, sample_image: Path
    ):
        """N threads escrevendo para a mesma página → exatamente 1 is_current."""
        db_path = tmp_path / "concurrent.db"
        n_threads = 8
        errors: list[str] = []

        def worker(thread_id: int) -> None:
            try:
                con = vdb.open_versions_db(db_path)
                ev_id = vdb.get_or_create_engine_version(
                    con,
                    engine="ollama",
                    model="test",
                    prompt_key="PROMPT",
                    system_prompt=SYSTEM_PROMPT,
                )
                vdb.record_ocr_result(
                    con,
                    volume_id="PO009",
                    page_num=1,
                    image_path=sample_image,
                    engine_version_id=ev_id,
                    text_content=f"resultado do thread {thread_id}",
                    reprocess_reason="verify_failed",
                )
                con.close()
            except Exception as e:
                errors.append(f"thread {thread_id}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Erros nos threads: {errors}"

        con = vdb.open_versions_db(db_path)
        current_count = con.execute(
            "SELECT count(*) as c FROM ocr_results WHERE volume_id='PO009' AND page_num=1 AND is_current=1"
        ).fetchone()["c"]
        assert current_count == 1, f"Esperado 1 is_current, encontrado {current_count}"

        total = con.execute(
            "SELECT count(*) as c FROM ocr_results WHERE volume_id='PO009' AND page_num=1"
        ).fetchone()["c"]
        # Pode ser < n_threads se alguns tiveram texto idêntico (idempotência) ou race condition
        # mas deve ter pelo menos 1 e no máximo n_threads
        assert 1 <= total <= n_threads
        con.close()


# ---------------------------------------------------------------------------
# Testes de retomada
# ---------------------------------------------------------------------------


class TestResume:
    def test_rerun_after_txt_written_but_no_db_record(
        self, db: sqlite3.Connection, sample_image: Path
    ):
        """Simula: .txt existe no disco mas banco não tem registro."""
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        # Nenhum registro ainda
        assert vdb.get_current_result(db, "PO009", 1) is None

        # "Re-run" registra normalmente
        rid = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="conteúdo do .txt existente",
            reprocess_reason="initial",
        )
        assert vdb.get_current_result(db, "PO009", 1) is not None
        assert vdb.get_current_result(db, "PO009", 1)["id"] == rid


# ---------------------------------------------------------------------------
# Testes de revert
# ---------------------------------------------------------------------------


class TestRevert:
    def test_revert_updates_is_current_and_file(
        self, db: sqlite3.Connection, sample_image: Path, tmp_path: Path
    ):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="test",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        txt_dir = tmp_path / "text"
        txt_dir.mkdir()

        id1 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto original",
            reprocess_reason="initial",
        )
        id2 = vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto revisado",
            reprocess_reason="verify_failed",
        )

        # Reverte para v1
        vdb.revert_to(db, id1, txt_dir)

        # is_current
        current = vdb.get_current_result(db, "PO009", 1)
        assert current["id"] == id1

        r2 = db.execute(
            "SELECT is_current FROM ocr_results WHERE id = ?", (id2,)
        ).fetchone()
        assert r2["is_current"] == 0

        # Arquivo restaurado
        txt_path = txt_dir / "000001.txt"
        assert txt_path.exists()
        assert txt_path.read_text(encoding="utf-8") == "texto original"

    def test_revert_raises_for_unknown_id(self, db: sqlite3.Connection, tmp_path: Path):
        with pytest.raises(ValueError, match="não encontrado"):
            vdb.revert_to(db, 999999, tmp_path)


# ---------------------------------------------------------------------------
# Testes de schema e conexão
# ---------------------------------------------------------------------------


class TestSchema:
    def test_init_schema_idempotent(self, tmp_path: Path):
        con = vdb.open_versions_db(tmp_path / "schema_test.db")
        vdb.init_versions_schema(con)  # segunda vez
        vdb.init_versions_schema(con)  # terceira vez
        # Não levanta erro

    def test_foreign_key_enforced(self, db: sqlite3.Connection, sample_image: Path):
        """INSERT com engine_version_id inexistente deve falhar."""
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO ocr_results
                   (volume_id, page_num, image_path, image_hash, engine_version_id,
                    text_content, text_hash, is_current)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                ("PO009", 1, str(sample_image), "fake_hash", 99999, "txt", "hash"),
            )
            db.execute("COMMIT")

    def test_wal_mode_active(self, tmp_path: Path):
        con = vdb.open_versions_db(tmp_path / "wal_test.db")
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        con.close()

    def test_synchronous_normal(self, tmp_path: Path):
        con = vdb.open_versions_db(tmp_path / "sync_test.db")
        val = con.execute("PRAGMA synchronous").fetchone()[0]
        # NORMAL = 1
        assert val == 1
        con.close()


# ---------------------------------------------------------------------------
# Teste de print_history (smoke test)
# ---------------------------------------------------------------------------


class TestPrintHistory:
    def test_prints_without_error(
        self, db: sqlite3.Connection, sample_image: Path, tmp_path: Path, capsys
    ):
        ev_id = vdb.get_or_create_engine_version(
            db,
            engine="ollama",
            model="qwen3.5:397b-cloud",
            prompt_key="PROMPT",
            system_prompt=SYSTEM_PROMPT,
        )
        vdb.record_ocr_result(
            db,
            volume_id="PO009",
            page_num=1,
            image_path=sample_image,
            engine_version_id=ev_id,
            text_content="texto",
            reprocess_reason="initial",
        )
        db_path = tmp_path / "test_versions.db"
        vdb.print_history("PO009", 1, db_path)
        captured = capsys.readouterr()
        assert "CURRENT" in captured.out
        assert "ollama" in captured.out

    def test_prints_empty_message(self, tmp_path: Path, capsys):
        db_path = tmp_path / "empty.db"
        vdb.print_history("PO999", 99, db_path)
        captured = capsys.readouterr()
        assert "Nenhum registro" in captured.out

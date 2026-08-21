from __future__ import annotations

import base64
import json
from pathlib import Path

from PIL import Image

import resumo_serial
from facsimile_transport import encode_facsimile_for_transport


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _make_page(tmp_path: Path, *, ocr_text: str = "Texto latino") -> tuple[Path, Path]:
    volume = tmp_path / "PGTEST"
    text_dir = volume / "text"
    images_dir = volume / "images"
    text_dir.mkdir(parents=True)
    images_dir.mkdir()
    page = text_dir / "page-001.txt"
    image = images_dir / "PGTEST-001.png"
    page.write_text(ocr_text, encoding="utf-8")
    Image.new("RGB", (24, 16), "white").save(image, format="PNG")
    return page, image


def test_facsimile_is_converted_to_jpeg_without_changing_source(tmp_path: Path) -> None:
    _page, image = _make_page(tmp_path)
    original = image.read_bytes()

    encoded, mime_type = encode_facsimile_for_transport(image)

    assert mime_type == "image/jpeg"
    assert base64.b64decode(encoded).startswith(b"\xff\xd8")
    assert image.read_bytes() == original


def test_resolves_only_paired_facsimile(tmp_path: Path) -> None:
    page, image = _make_page(tmp_path)

    assert resumo_serial.resolve_page_facsimile(page) == image.resolve()


def test_openai_sends_converted_jpeg(tmp_path: Path, monkeypatch) -> None:
    _page, image = _make_page(tmp_path)
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 41, "completion_tokens": 7},
            }
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = resumo_serial.openai_chat(
        "system", "user", "gpt-5-mini", image_path=image
    )
    assert response == "{}"
    assert response.usage == resumo_serial.TokenUsage(41, 7, 1)
    image_url = captured["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")


def test_ollama_sends_converted_jpeg_bytes(tmp_path: Path, monkeypatch) -> None:
    _page, image = _make_page(tmp_path)
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            {
                "message": {"content": "{}"},
                "prompt_eval_count": 53,
                "eval_count": 11,
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = resumo_serial.ollama_chat(
        "system", "user", "vision-model", image_path=image
    )
    assert response == "{}"
    assert response.usage == resumo_serial.TokenUsage(53, 11, 1)
    encoded = captured["messages"][1]["images"][0]
    assert base64.b64decode(encoded).startswith(b"\xff\xd8")


def test_prompt_explains_facsimile_role() -> None:
    prompt = resumo_serial.build_user_prompt(
        "contexto",
        "",
        1,
        "PGTEST",
        "Autor",
        "Obra",
        facsimile_attached=True,
    )

    assert "<facsimile_anexado>" in prompt
    assert "OCR vazio; confira o fac-símile anexado" in prompt
    assert "não invente trechos" in prompt


def test_empty_ocr_is_sent_when_facsimile_is_enabled(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    caplog.set_level("INFO", logger="resumo_serial")
    page, image = _make_page(tmp_path, ocr_text="")
    captured: dict = {}

    def fake_llm_chat(*_args, **kwargs):
        captured.update(kwargs)
        return resumo_serial.LLMResponse(
            json.dumps(
                {
                    "autor": "Autor",
                    "obra": "Obra",
                    "traducao_compacta": "Conteúdo legível recuperado visualmente no documento.",
                    "sintese_acumulada": "Síntese suficientemente longa recuperada a partir desta página.",
                },
                ensure_ascii=False,
            ),
            resumo_serial.TokenUsage(120, 30, 1),
        )

    monkeypatch.setattr(resumo_serial, "llm_chat", fake_llm_chat)

    resumo_serial.process_volume(
        volume_dir=page.parent.parent,
        con=None,
        model="vision-model",
        base_url="http://localhost",
        timeout=1,
        retries=1,
        dry_run=True,
        page_filter=1,
        include_facsimile=True,
    )

    assert captured["image_path"] == image.resolve()
    assert "<facsimile_anexado>" in captured["prompt_user"]
    assert "tokens=150 (entrada=120, saída=30)" in caplog.text
    assert "Métricas: 1 item" in caplog.text

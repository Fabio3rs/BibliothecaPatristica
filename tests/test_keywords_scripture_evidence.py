from __future__ import annotations

from pathlib import Path
import json

from PIL import Image

from keywords_serial import (
    build_user_prompt,
    collect_page_scripture_evidence,
    ollama_chat,
    openai_chat,
    resolve_page_facsimile,
    resolve_page_ocr_path,
    validate_scripture_warrant,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _page(tmp_path: Path, text: str) -> tuple[dict, Path, Path]:
    volume = tmp_path / "PGTEST"
    text_dir = volume / "text"
    image_dir = volume / "images"
    text_dir.mkdir(parents=True)
    image_dir.mkdir()
    ocr = text_dir / "sample-page-001.txt"
    image = image_dir / "PGTEST-001.png"
    ocr.write_text(text, encoding="utf-8")
    image.write_bytes(b"not-a-real-png")
    return (
        {
            "documento": "PGTEST",
            "pagina_num": 1,
            "pagina_file": ocr.name,
            "pagina_texto": text,
            "resumo_pagina": "A página comenta as bem-aventuranças.",
            "resumo_global": "Comentário bíblico.",
        },
        ocr,
        image,
    )


def test_resolves_physical_ocr_and_facsimile_without_editorial_inference(
    tmp_path: Path,
) -> None:
    row, ocr, image = _page(tmp_path, "Matth. V, 3")

    assert resolve_page_ocr_path(row, corpus_root=tmp_path) == ocr.resolve()
    assert resolve_page_facsimile(row, corpus_root=tmp_path) == image.resolve()


def test_collects_body_and_critical_apparatus_citations(tmp_path: Path) -> None:
    row, _, _ = _page(
        tmp_path,
        """<pagina estado="com_texto">
<bloco tipo="texto_principal" script="latino" bbox="0,0,500,700">
Sicut scriptum est Matth. V, 3.
</bloco>
<bloco tipo="aparato_critico" script="latino" bbox="0,700,1000,1000">
Variantia codd. confer Rom. VIII, 28.
</bloco>
</pagina>""",
    )

    evidence = collect_page_scripture_evidence(row, corpus_root=tmp_path)

    assert evidence["status"] == "ok"
    contexts = {candidate["context"] for candidate in evidence["candidates"]}
    refs = {
        occurrence["ref_norm"]
        for candidate in evidence["candidates"]
        for occurrence in candidate["occurrences"]
    }
    assert contexts == {"body", "critical_apparatus"}
    assert {"São Mateus 5,3", "Romanos 8,28"} <= refs


def test_ocr_detector_rejects_column_and_number_false_positives(tmp_path: Path) -> None:
    row, _, _ = _page(
        tmp_path,
        """<pagina estado="com_texto">
<bloco tipo="cabecalho">Origenes, lib. II, cap. 3, num. 6.</bloco>
<bloco tipo="texto_principal">Vide col. 3 huius editionis.</bloco>
</pagina>""",
    )

    evidence = collect_page_scripture_evidence(row, corpus_root=tmp_path)

    assert evidence["candidates"] == []


def test_prompt_exposes_regex_as_candidates_not_authority(tmp_path: Path) -> None:
    row, _, _ = _page(tmp_path, "Sicut scriptum est Matth. V, 3.")
    evidence = collect_page_scripture_evidence(row, corpus_root=tmp_path)

    prompt = build_user_prompt(
        row,
        "resumo_pagina",
        "PGTEST",
        scripture_evidence=evidence,
    )

    assert "<evidencia_citacoes_regex>" in prompt
    assert "não uma ordem para incluir todos" in prompt
    assert "São Mateus 5,3" in prompt


def test_scripture_warrant_reports_unsupported_reference(tmp_path: Path) -> None:
    row, _, _ = _page(tmp_path, "Sicut scriptum est Matth. V, 3.")
    evidence = collect_page_scripture_evidence(row, corpus_root=tmp_path)

    issues = validate_scripture_warrant(
        {
            "keywords": ["Mateus 5,3", "Romanos 8,28"],
            "categorias": {"obras_citadas": ["Mateus 5,3", "Romanos 8,28"]},
        },
        evidence,
    )

    assert issues == ["scripture_without_ocr_warrant[Romanos 8,28]"]


def test_openai_facsimile_uses_multimodal_message_without_debug_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.chdir(tmp_path)

    assert openai_chat("system", "user", "gpt-5-mini", image_path=image) == "{}"
    content = captured["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "user"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert not (tmp_path / "debug.json").exists()


def test_openai_facsimile_converts_valid_png_to_jpeg(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (24, 16), "white").save(image, format="PNG")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert openai_chat("system", "user", "gpt-5-mini", image_path=image) == "{}"
    image_url = captured["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")


def test_ollama_facsimile_uses_native_images_field(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert ollama_chat("system", "user", "model", image_path=image) == "{}"
    user_message = captured["messages"][1]
    assert user_message["content"] == "user"
    assert len(user_message["images"]) == 1

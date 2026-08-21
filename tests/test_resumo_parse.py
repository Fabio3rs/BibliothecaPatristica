from resumo_serial import parse_llm_response


def test_parse_llm_response_invalid_json_flags_retry():
    raw = '{"autor": "X", "traducao_compacta": "texto"'  # falta fechar
    _, _, _, _, ok = parse_llm_response(raw)
    assert ok is False


def test_parse_llm_response_non_json_text():
    raw = "autor: X\ntraducao_compacta: y"
    _, _, _, _, ok = parse_llm_response(raw)
    assert ok is False


def test_parse_llm_response_valid_json():
    raw = (
        '{"autor":"A","obra":"O","traducao_compacta":"pagina","sintese_acumulada":"s"}'
    )
    resumo, sintese, autor, obra, ok = parse_llm_response(raw)
    assert ok is True
    assert resumo == "pagina"
    assert sintese == "s"
    assert autor == "A"
    assert obra == "O"


def test_parse_llm_response_accepts_fenced_json_with_trailing_markdown():
    raw = """```json
{"autor":"A","obra":"O","traducao_compacta":"pagina","sintese_acumulada":"s"}
Observação residual.
```"""
    resumo, sintese, autor, obra, ok = parse_llm_response(raw)
    assert ok is True
    assert (resumo, sintese, autor, obra) == ("pagina", "s", "A", "O")

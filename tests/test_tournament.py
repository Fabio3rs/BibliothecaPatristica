import os
import sys
import json
import pytest

# garantir que a raiz do projeto está no path para importar modules locais
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ollama_keywords import chunked, parse_llm_response, tournament_reduce


def test_chunked_basic():
    items = [str(i) for i in range(7)]
    c = chunked(items, 3)
    assert len(c) == 3
    assert c[0] == ["0", "1", "2"]
    assert c[1] == ["3", "4", "5"]
    assert c[2] == ["6"]


def test_parse_llm_response_simple_json():
    raw = json.dumps({
        "results": [
            {"original": "kw1", "change_to": "kw_one"},
            {"original": "kw2", "change_to": None},
        ]
    })
    parsed = parse_llm_response(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["original"] == "kw1"
    assert parsed[0]["change_to"] == "kw_one"
    assert parsed[1]["change_to"] is None


def test_tournament_reduce_simple():
    # mock llm function: for any batch, map items ending with "a" -> "A", ending with "b" -> "B"
    def mock_llm(batch):
        res = []
        for k in batch:
            if k.endswith("a"):
                res.append({"original": k, "change_to": "A"})
            elif k.endswith("b"):
                res.append({"original": k, "change_to": "B"})
        return res

    items = [f"item{i}a" for i in range(5)] + [f"item{i}b" for i in range(5)]
    mapping = tournament_reduce(items, mock_llm, batch_size=3, max_rounds=5, workers=2)
    # all a's -> A, all b's -> B
    for k in items:
        if k.endswith("a"):
            assert mapping[k] == "A"
        else:
            assert mapping[k] == "B"


if __name__ == "__main__":
    pytest.main(["-q"])

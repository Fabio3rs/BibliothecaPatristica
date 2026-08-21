from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main2


@pytest.mark.parametrize(
    ("fidelidade", "usabilidade"),
    [
        ("baixa", "alta"),
        ("alta", "baixa"),
        ("descartar", "alta"),
        ("alta", "descartar"),
        (" BAIXA ", "alta"),
    ],
)
def test_bad_judge_rating_requires_reprocessing(fidelidade, usabilidade):
    result = {
        "status": "parse_ok",
        "fidelidade": fidelidade,
        "usabilidade": usabilidade,
    }

    assert main2._judge_result_is_acceptable(result) is False


@pytest.mark.parametrize(
    ("fidelidade", "usabilidade"),
    [("alta", "alta"), ("media", "alta"), ("alta", "media")],
)
def test_good_or_medium_judge_rating_is_acceptable(fidelidade, usabilidade):
    result = {
        "status": "parse_ok",
        "fidelidade": fidelidade,
        "usabilidade": usabilidade,
    }

    assert main2._judge_result_is_acceptable(result) is True


def test_judge_parse_error_requires_reprocessing():
    result = {
        "status": "parse_error",
        "fidelidade": None,
        "usabilidade": None,
    }

    assert main2._judge_result_is_acceptable(result) is False

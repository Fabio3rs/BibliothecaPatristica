from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main2


@pytest.mark.parametrize("algorithm", ["ollama", "openai"])
def test_vlm_algorithms_are_accepted(algorithm):
    main2._validate_vlm_algorithm(algorithm)


def test_tesseract_is_not_a_final_backend():
    with pytest.raises(ValueError, match="estágio auxiliar"):
        main2._validate_vlm_algorithm("tesseract")

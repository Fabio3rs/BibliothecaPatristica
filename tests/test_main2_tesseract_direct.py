from __future__ import annotations

import sys
from pathlib import Path
from shutil import which

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main2


@pytest.mark.skipif(which("tesseract") is None, reason="tesseract binary not available")
def test_ocr_tesseract_raw_direct_no_cache(tmp_path: Path):
    img_path = tmp_path / "sample.png"

    img = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 48)
    except Exception:
        font = ImageFont.load_default()

    draw.text((40, 120), "In principio erat Verbum", fill="black", font=font)
    img.save(img_path)

    text = main2.ocr_tesseract_raw(img_path, lang=main2.DEFAULT_LANG)

    assert isinstance(text, str)
    assert text.strip()


@pytest.mark.skipif(which("tesseract") is None, reason="tesseract binary not available")
def test_ocr_tesseract_adaptive_soft_direct(tmp_path: Path):
    img_path = tmp_path / "sample_soft.png"

    img = Image.new("RGB", (1200, 400), (245, 240, 220))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 48)
    except Exception:
        font = ImageFont.load_default()

    draw.text((40, 120), "In principio erat Verbum", fill=(70, 70, 70), font=font)
    img.save(img_path)

    text = main2.ocr_tesseract(
        img_path,
        lang=main2.DEFAULT_LANG,
        preprocess_mode="adaptive_soft",
    )

    assert isinstance(text, str)
    assert text.strip()

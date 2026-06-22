#!/usr/bin/env python3
"""Prototype playground: image -> Tesseract TSV -> synthetic re-render.

Goal
----
Rebuild a page as close as possible to the original scan using only the
geometry and text recovered from Tesseract TSV.

Design principles for this prototype:
- one file, but internally modular
- default background is pure white
- focus on typographic similarity, not OCR correction
- prefer visual fidelity over semantic reconstruction

Typical usage:
    python scripts/playgrounds/render_text.py input.png --output synth.png --show-debug
    python scripts/playgrounds/render_text.py input.pdf --page 1 --lang lat+grc

Dependencies:
- pytesseract
- Pillow
- OpenCV
- numpy
- optional: pdf2image for PDF input
- optional: fc-match for font resolution by family name
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import unicodedata
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageFont

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None  # type: ignore


# ---------------------------------------------------------------------------
# Font defaults
# ---------------------------------------------------------------------------

# The user provided this list as the closest available family set.
DEFAULT_FONT_CANDIDATES = [
    "GFS Didot",
    "GFS Didot Bold",
    "GFS Didot Italic",
    "Old Standard TT",
    "Old Standard TT Bold",
    "Old Standard TT Italic",
    "New Athena Unicode",
    "New Athena Unicode Bold",
    "New Athena Unicode Bold Italic",
    "New Athena Unicode Italic",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WordBox:
    """One word-level record extracted from Tesseract TSV."""

    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int
    block_num: int
    par_num: int
    line_num: int
    word_num: int
    page_num: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


@dataclass
class LineBox:
    """Word boxes grouped as a visual line."""

    words: list[WordBox]
    page_w: int
    page_h: int

    @property
    def x(self) -> int:
        return min(w.x for w in self.words)

    @property
    def y(self) -> int:
        return min(w.y for w in self.words)

    @property
    def x2(self) -> int:
        return max(w.x2 for w in self.words)

    @property
    def y2(self) -> int:
        return max(w.y2 for w in self.words)

    @property
    def w(self) -> int:
        return self.x2 - self.x

    @property
    def h(self) -> int:
        return self.y2 - self.y

    @property
    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda ww: ww.x))

    @property
    def center_x(self) -> float:
        return (self.x + self.x2) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y + self.y2) / 2.0


# ---------------------------------------------------------------------------
# Input / output helpers
# ---------------------------------------------------------------------------

def read_input(path: Path, dpi: int, page: int | None = None) -> np.ndarray:
    """Read a PNG/JPG/TIF image or a PDF page and return BGR image."""
    if path.suffix.lower() == ".pdf":
        if convert_from_path is None:
            raise RuntimeError("pdf2image is not available; cannot read PDFs")
        pages = convert_from_path(str(path), dpi=dpi)
        if not pages:
            raise FileNotFoundError(f"no page extracted from {path}")
        idx = (page or 1) - 1
        if idx < 0 or idx >= len(pages):
            raise IndexError(f"page {page} out of range (1..{len(pages)})")
        return cv2.cvtColor(np.array(pages[idx]), cv2.COLOR_RGB2BGR)

    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return img


def save_image(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


# ---------------------------------------------------------------------------
# Preprocess and OCR
# ---------------------------------------------------------------------------

def ensure_gray(img: np.ndarray) -> np.ndarray:
    return img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def preprocess_for_tesseract(img: np.ndarray) -> np.ndarray:
    """Minimal preprocessing.

    We keep this intentionally light because the goal is page geometry, not
    aggressive cleanup that might alter the detected boxes.
    """
    gray = ensure_gray(img)
    if gray.dtype != np.uint8:
        gray = gray.astype(np.uint8)
    return gray


def run_tesseract_tsv(img: np.ndarray, lang: str, psm: int, oem: int) -> str:
    cfg = f"--psm {psm} --oem {oem}"
    return pytesseract.image_to_data(
        img,
        lang=lang,
        config=cfg,
        output_type=pytesseract.Output.STRING,
    )


def parse_tsv(tsv_text: str) -> tuple[list[WordBox], int, int]:
    """Parse Tesseract TSV into word boxes and infer page dimensions."""
    rows = [row for row in tsv_text.splitlines() if row.strip()]
    if not rows:
        return [], 0, 0

    header = rows[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}

    words: list[WordBox] = []
    page_w = 0
    page_h = 0

    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) < len(header):
            continue
        text = cols[idx["text"]].strip()
        if not text:
            continue
        try:
            conf = float(cols[idx["conf"]])
        except Exception:
            conf = -1.0
        if conf < 0:
            continue

        try:
            page_num = int(cols[idx["page_num"]])
            block_num = int(cols[idx["block_num"]])
            par_num = int(cols[idx["par_num"]])
            line_num = int(cols[idx["line_num"]])
            word_num = int(cols[idx["word_num"]])
            x = int(cols[idx["left"]])
            y = int(cols[idx["top"]])
            w = int(cols[idx["width"]])
            h = int(cols[idx["height"]])
        except Exception:
            continue

        if w <= 0 or h <= 0:
            continue

        page_w = max(page_w, x + w)
        page_h = max(page_h, y + h)

        words.append(
            WordBox(
                text=text,
                conf=conf,
                x=x,
                y=y,
                w=w,
                h=h,
                block_num=block_num,
                par_num=par_num,
                line_num=line_num,
                word_num=word_num,
                page_num=page_num,
            )
        )

    return words, page_w, page_h


# ---------------------------------------------------------------------------
# Layout grouping
# ---------------------------------------------------------------------------

def group_words_into_lines(words: list[WordBox]) -> list[LineBox]:
    """Group TSV word boxes into visual lines using Tesseract ids.

    The Tesseract line ids are useful but not always sufficient for complex
    pages, so we keep the grouping simple and stable: words are grouped by
    (page, block, paragraph, line).
    """
    if not words:
        return []

    buckets: dict[tuple[int, int, int, int], list[WordBox]] = {}
    for w in words:
        key = (w.page_num, w.block_num, w.par_num, w.line_num)
        buckets.setdefault(key, []).append(w)

    lines = [LineBox(sorted(ws, key=lambda ww: ww.x), 0, 0) for ws in buckets.values()]
    lines.sort(key=lambda line: (line.y, line.x))
    return lines


def detect_columns(lines: list[LineBox], page_w: int) -> tuple[int, int]:
    """Estimate a gutter for dual-column pages.

    Returns the gutter interval [x1, x2]. If the page does not look like a
    clear two-column layout, we return a narrow dead zone around the center.
    """
    if not lines or page_w <= 0:
        mid = page_w // 2
        return mid - 2, mid + 2

    centers = np.array([line.center_x for line in lines], dtype=float)
    widths = np.array([line.w for line in lines], dtype=float)
    if len(centers) < 6:
        mid = page_w // 2
        return mid - 2, mid + 2

    left_mass = float(np.mean(centers < page_w * 0.45))
    right_mass = float(np.mean(centers > page_w * 0.55))
    if left_mass < 0.18 or right_mass < 0.18:
        mid = page_w // 2
        return mid - 2, mid + 2

    hist = np.zeros(max(page_w // 4, 100), dtype=float)
    bins = len(hist)
    for c, w in zip(centers, widths):
        start = max(0, min(bins - 1, int((c - w / 2) / page_w * bins)))
        end = max(0, min(bins - 1, int((c + w / 2) / page_w * bins)))
        hist[start : end + 1] += 1.0

    smooth = np.convolve(hist, np.ones(9) / 9.0, mode="same")
    lo = int(bins * 0.30)
    hi = int(bins * 0.70)
    center_slice = smooth[lo:hi]
    if center_slice.size == 0:
        mid = page_w // 2
        return mid - 2, mid + 2

    min_idx = int(np.argmin(center_slice)) + lo
    thr = smooth.max() * 0.20 if smooth.max() > 0 else 0.0

    gl = min_idx
    gr = min_idx
    while gl > 0 and smooth[gl - 1] < thr:
        gl -= 1
    while gr < bins - 1 and smooth[gr + 1] < thr:
        gr += 1

    gutter_x1 = int(gl / bins * page_w)
    gutter_x2 = int(gr / bins * page_w)
    if gutter_x2 - gutter_x1 < max(4, int(page_w * 0.01)):
        mid = page_w // 2
        return mid - 2, mid + 2
    return gutter_x1, gutter_x2


# ---------------------------------------------------------------------------
# Typographic estimation
# ---------------------------------------------------------------------------

def is_greek_script(text: str) -> bool:
    for ch in text:
        name = unicodedata.name(ch, "")
        if "GREEK" in name:
            return True
    return False


def clean_font_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip('"').strip("'"))


def resolve_font_path(font_name: str) -> str | None:
    """Resolve a font family name to a concrete file path.

    We prefer `fc-match` because family names from the user are the most stable
    input for this prototype.
    """
    font_name = clean_font_name(font_name)
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{file}\n", font_name],
            check=True,
            capture_output=True,
            text=True,
        )
        path = proc.stdout.strip().splitlines()[0].strip()
        if path:
            return path
    except Exception:
        pass
    return None


def select_font_family(
    text: str,
    font_candidates: list[str],
    latin_fallback: str,
    greek_fallback: str,
) -> str:
    """Choose the most likely family for the current line."""
    if is_greek_script(text):
        for candidate in font_candidates:
            if "greek" in candidate.lower() or "athena" in candidate.lower():
                return candidate
        return greek_fallback
    for candidate in font_candidates:
        if "didot" in candidate.lower() or "standard" in candidate.lower():
            return candidate
    return latin_fallback


def estimate_font_size(line: LineBox) -> int:
    """Estimate font size from the visual height of the line."""
    heights = [w.h for w in line.words if w.h > 0]
    if not heights:
        return 24
    median_h = float(np.median(heights))
    # Most scans leave a little extra vertical space inside the box.
    size = int(round(median_h * 0.90))
    return max(6, size)


def estimate_word_font_size(word: WordBox) -> int:
    """Estimate font size from a single word box.

    Word-level rendering is closer to the original page geometry, so the size
    estimate can simply follow the detected box height.
    """
    size = int(round(word.h * 0.92))
    return max(6, size)


def estimate_letter_spacing(line: LineBox) -> float:
    """Estimate tracking from the gap between adjacent word boxes.

    We keep the prototype conservative here. The line renderer uses whole
    words as a unit, so this value is mainly used as a hint for width fitting.
    """
    if len(line.words) < 2:
        return -0.2

    sorted_words = sorted(line.words, key=lambda w: w.x)
    gaps = []
    widths = []
    for a, b in zip(sorted_words, sorted_words[1:]):
        gap = b.x - a.x2
        if gap >= 0:
            gaps.append(gap)
            widths.append(max(a.w, 1))

    if not gaps:
        return -0.2

    median_gap = float(np.median(gaps))
    median_width = float(np.median(widths)) if widths else 1.0
    ratio = median_gap / median_width

    if ratio < 0.06:
        return -0.25
    if ratio < 0.12:
        return -0.15
    if ratio < 0.20:
        return -0.05
    return 0.0


def estimate_line_position(line: LineBox, page_w: int, gutter: tuple[int, int]) -> tuple[str, int]:
    """Assign a rough zone and baseline x offset for debug purposes."""
    gx1, gx2 = gutter
    if line.x2 < gx1:
        return "left", line.x
    if line.x > gx2:
        return "right", line.x
    if line.w > page_w * 0.45:
        return "full", line.x
    return "center", line.x


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def build_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size=size)


@dataclass(frozen=True)
class FontCalibration:
    """Measured metrics for one font family at one point size."""

    ascent: int
    descent: int
    height: int
    ink_height: int
    sample_text: str
    sample_bbox: tuple[int, int, int, int]

    @property
    def baseline_ratio(self) -> float:
        total = max(1, self.ascent + self.descent)
        return self.ascent / total


def sample_text_for_calibration(text: str) -> str:
    """Use a stable sample string so font measurements are comparable."""
    if is_greek_script(text):
        return "ΑΒΓΔαβγδΩωἈἘἸἨἼἮἾ"
    return "AgQyjpÉÀÂŴ"


@lru_cache(maxsize=256)
def calibrate_font(font_path: str, size: int, sample_text: str) -> FontCalibration:
    """Measure a font at a concrete point size using a real glyph sample."""
    font = build_font(font_path, size)
    ascent, descent = font.getmetrics()
    bbox = font.getbbox(sample_text)
    ink_height = max(1, bbox[3] - bbox[1])
    return FontCalibration(
        ascent=ascent,
        descent=descent,
        height=max(1, ascent + descent),
        ink_height=ink_height,
        sample_text=sample_text,
        sample_bbox=bbox,
    )


def estimate_size_from_bbox(
    text: str,
    target_h: int,
    font_path: str,
    calibration: FontCalibration,
) -> int:
    """Estimate point size from OCR height using real font metrics.

    The estimate is based on the actual string being rendered, measured at a
    known calibration size, then scaled back to the OCR box height.
    """
    if target_h <= 0:
        return 24

    measured = measure_real_text_bbox(font_path, 100, unicodedata.normalize("NFC", text))
    measured_ink = max(1, measured[3] - measured[1])

    # Tesseract boxes usually include a little vertical slack around the ink.
    # We subtract a small fraction before scaling back to points.
    slack_ratio = 0.06
    effective_target = max(1.0, target_h * (1.0 - slack_ratio))
    estimated = int(round((effective_target / float(measured_ink)) * 100))

    # Bound the result by a calibration-aware sanity range.
    min_size = max(4, int(round((target_h / max(1, calibration.height)) * 100 * 0.7)))
    max_size = max(min_size + 1, int(round((target_h / max(1, calibration.height)) * 100 * 1.6)))
    return max(min_size, min(estimated, max_size))


def measure_real_text_bbox(font_path: str, size: int, text: str) -> tuple[int, int, int, int]:
    """Measure the exact ink bbox for a real string at a concrete size."""
    font = build_font(font_path, size)
    return font.getbbox(text)


def fit_font_for_box(
    text: str,
    font_path: str,
    target_w: int,
    target_h: int,
    starting_size: int,
) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
    """Pick the largest font size that fits the detected box.

    We keep the glyph box fit conservative, but the final vertical placement
    is driven by font metrics so words do not float differently from line to
    line.
    """
    probe = Image.new("L", (max(1, target_w), max(1, target_h)), 255)
    draw = ImageDraw.Draw(probe)

    size = max(4, int(starting_size))
    best_font = build_font(font_path, 4)
    best_bbox = draw.textbbox((0, 0), text, font=best_font)

    for candidate in range(size, 3, -1):
        font = build_font(font_path, candidate)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        best_font = font
        best_bbox = bbox
        if tw <= target_w - 1 and th <= target_h - 1:
            return font, bbox

    return best_font, best_bbox


def compute_text_y(
    box_h: int,
    bbox: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont,
    text: str,
) -> int:
    """Align text using font metrics instead of glyph-box centering.

    The target baseline is estimated from the font's ascent/descent ratio,
    which gives much more stable vertical placement across mixed line content.
    """
    ascent, descent = font.getmetrics()
    metrics_total = max(1, ascent + descent)

    # Bias baseline a little above the geometric center to reflect book text.
    baseline_ratio = min(0.92, max(0.60, (ascent / metrics_total) + 0.02))
    baseline_y = int(round(box_h * baseline_ratio))

    # `textbbox` is anchored at the drawing origin, so shift by bbox top.
    return max(0, baseline_y - ascent - bbox[1])


def compute_text_x(
    box_w: int,
    bbox: tuple[int, int, int, int],
) -> int:
    """Anchor text using the actual glyph left sidebearing."""
    tw = bbox[2] - bbox[0]
    return max(0, (box_w - tw) // 2 - bbox[0])


def has_descenders(text: str) -> bool:
    """Detect whether a text sample likely needs extra bottom padding."""
    return any(ch in "gjpqy" for ch in text.lower())


def has_descender_punctuation(text: str) -> bool:
    """Detect punctuation that can visually sit below the baseline."""
    return any(ch in ",;:.?!" for ch in text)


def line_text_kind(line: LineBox) -> str:
    """Classify a line so attenuation can stay conservative on titles."""
    text = line.text.strip()
    if not text:
        return "body"
    alpha = sum(ch.isalpha() for ch in text)
    upper = sum(ch.isupper() for ch in text)
    if len(text) <= 40 and alpha > 0 and upper / max(1, alpha) > 0.65:
        return "title"
    if len(line.words) <= 4 and line.w > line.h * 5:
        return "heading"
    return "body"


def line_attenuation_factor(line: LineBox) -> float:
    """Return a gentle alpha multiplier for a line."""
    kind = line_text_kind(line)
    density = len(line.words) / max(1.0, float(line.w))
    if kind == "title":
        return 0.98
    if kind == "heading":
        return 0.95
    if density > 0.08:
        return 0.90
    if density > 0.04:
        return 0.94
    return 0.97


def glyph_ink_height(font: ImageFont.FreeTypeFont, text: str) -> int:
    """Return the glyph ink height for a piece of text."""
    bbox = font.getbbox(text)
    return max(1, bbox[3] - bbox[1])


def render_text_to_box(
    text: str,
    font_path: str,
    font_size: int,
    tracking_px: float,
    line_height: int,
    box_w: int,
    box_h: int,
) -> Image.Image:
    """Render text within a target box.

    We render the entire OCR line as a single string and fit it to the
    detected bbox. This preserves the visual rhythm much better than a
    character-by-character approximation for mixed Latin/Greek pages.
    """
    text = unicodedata.normalize("NFC", text)
    pad_x = max(4, box_w // 12)
    pad_top = max(4, box_h // 8)
    pad_bottom = max(6, box_h // 7)
    if has_descenders(text):
        pad_bottom += max(2, box_h // 10)
    if has_descender_punctuation(text):
        pad_bottom += max(1, box_h // 14)
    canvas = Image.new("L", (max(1, box_w + pad_x * 2), max(1, box_h + pad_y * 2)), 255)
    draw = ImageDraw.Draw(canvas)

    # Fit the font size to the box, but preserve the text's real ink geometry.
    size = max(4, int(font_size))
    best = None
    for candidate in range(size, 3, -1):
        font = build_font(font_path, candidate)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        if tw <= box_w - 2 and th <= box_h - 2:
            best = (font, bbox)
            break
        if best is None:
            best = (font, bbox)

    font, bbox = best if best is not None else (build_font(font_path, 4), draw.textbbox((0, 0), text, font=build_font(font_path, 4)))
    canvas = Image.new(
        "L",
        (max(1, box_w + pad_x * 2), max(1, box_h + pad_top + pad_bottom)),
        255,
    )
    draw = ImageDraw.Draw(canvas)
    x = pad_x + compute_text_x(box_w, bbox)
    y = pad_top + compute_text_y(box_h, bbox, font, text)
    draw.text((x, y), text, font=font, fill=0)
    return canvas


def render_word_to_box(
    text: str,
    font_path: str,
    font_size: int,
    box_w: int,
    box_h: int,
) -> Image.Image:
    """Render a single word inside its detected bbox.

    The returned image uses transparency outside the glyphs so the compositor
    can keep earlier text visible when boxes overlap.
    """
    text = unicodedata.normalize("NFC", text)
    pad_x = max(4, box_w // 12)
    pad_top = max(5, box_h // 7)
    pad_bottom = max(7, box_h // 6)
    if has_descenders(text):
        pad_bottom += max(3, box_h // 8)
    if has_descender_punctuation(text):
        pad_bottom += max(1, box_h // 12)
    canvas = Image.new(
        "RGBA",
        (max(1, box_w + pad_x * 2), max(1, box_h + pad_top + pad_bottom)),
        (255, 255, 255, 0),
    )
    draw = ImageDraw.Draw(canvas)

    font, bbox = fit_font_for_box(
        text=text,
        font_path=font_path,
        target_w=box_w,
        target_h=box_h,
        starting_size=font_size,
    )
    x = pad_x + compute_text_x(box_w, bbox)
    y = pad_top + compute_text_y(box_h, bbox, font, text)
    draw.text((x, y), text, font=font, fill=(0, 0, 0, 255))
    return canvas


def render_line_layer(
    line: LineBox,
    font_candidates: list[str],
    latin_fallback: str,
    greek_fallback: str,
) -> tuple[Image.Image, dict]:
    """Render a whole OCR line into an RGBA layer with transparent background."""
    words = sorted(line.words, key=lambda w: w.x)
    pad_x = max(6, line.w // 24)
    pad_top = max(6, line.h // 7)
    pad_bottom = max(8, line.h // 6)
    if any(has_descenders(w.text) for w in words):
        pad_bottom += max(3, line.h // 8)
    if any(has_descender_punctuation(w.text) for w in words):
        pad_bottom += max(1, line.h // 12)

    layer_w = max(1, line.w + pad_x * 2)
    layer_h = max(1, line.h + pad_top + pad_bottom)
    layer = Image.new("RGBA", (layer_w, layer_h), (255, 255, 255, 0))
    meta = {
        "kind": line_text_kind(line),
        "attenuation": line_attenuation_factor(line),
        "layer_bbox": (line.x, line.y, line.x2, line.y2),
        "words": len(words),
    }

    for word in words:
        text = word.text.strip()
        if not text:
            continue

        family = select_font_family(text, font_candidates, latin_fallback, greek_fallback)
        font_path = resolve_font_path(family)
        if font_path is None:
            font_path = resolve_font_path(latin_fallback) or resolve_font_path(greek_fallback)
        if font_path is None:
            raise RuntimeError("Could not resolve any usable font path")

        sample_text = sample_text_for_calibration(text)
        calibration = calibrate_font(font_path, 100, sample_text)
        size = estimate_size_from_bbox(text, word.h, font_path, calibration)
        word_img = render_word_to_box(
            text=text,
            font_path=font_path,
            font_size=size,
            box_w=max(1, word.w + 2),
            box_h=max(1, word.h + 2),
        )

        local_x = pad_x + (word.x - line.x) - 1 - max(4, (word.w + 2) // 12)
        local_y = pad_top + (word.y - line.y) - 1 - max(5, (word.h + 2) // 7)
        local_x = max(0, min(layer.width - word_img.width, local_x))
        local_y = max(0, min(layer.height - word_img.height, local_y))
        layer.alpha_composite(word_img, (local_x, local_y))

        meta.setdefault("sample_word", text)
        meta.setdefault("sample_font", family)
        meta.setdefault("sample_size", size)

    attenuation = line_attenuation_factor(line)
    if attenuation < 1.0:
        rgba = np.array(layer)
        rgba[..., 3] = np.clip(rgba[..., 3].astype(np.float32) * attenuation, 0, 255).astype(np.uint8)
        layer = Image.fromarray(rgba, mode="RGBA")

    meta["pad_x"] = pad_x
    meta["pad_top"] = pad_top
    meta["pad_bottom"] = pad_bottom
    return layer, meta


def draw_boxes_debug(base: np.ndarray, lines: list[LineBox], gutter: tuple[int, int]) -> np.ndarray:
    """Draw detected line boxes and gutter for inspection."""
    overlay = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR) if len(base.shape) == 2 else base.copy()
    gx1, gx2 = gutter
    cv2.line(overlay, (gx1, 0), (gx1, overlay.shape[0]), (0, 140, 255), 1)
    cv2.line(overlay, (gx2, 0), (gx2, overlay.shape[0]), (0, 140, 255), 1)
    for line in lines:
        cv2.rectangle(overlay, (line.x, line.y), (line.x2, line.y2), (255, 0, 0), 1)
    return overlay


def reconstruct_page(
    page_w: int,
    page_h: int,
    lines: list[LineBox],
    words: list[WordBox],
    font_candidates: list[str],
    latin_fallback: str,
    greek_fallback: str,
    paper_white: int = 255,
    padding_right: int = 90,
    padding_bottom: int = 90,
) -> tuple[np.ndarray, list[dict]]:
    """Render the synthetic page and return debug metadata."""
    canvas = Image.new(
        "RGBA",
        (page_w + padding_right, page_h + padding_bottom),
        (paper_white, paper_white, paper_white, 255),
    )
    debug_rows: list[dict] = []

    for line in sorted(lines, key=lambda l: (l.y, l.x)):
        if not line.words:
            continue
        rendered, meta = render_line_layer(
            line=line,
            font_candidates=font_candidates,
            latin_fallback=latin_fallback,
            greek_fallback=greek_fallback,
        )

        x = max(0, line.x - 1 - int(meta["pad_x"]))
        y = max(0, line.y - 1 - int(meta["pad_top"]))
        if x + rendered.width > canvas.width:
            x = max(0, canvas.width - rendered.width)
        if y + rendered.height > canvas.height:
            y = max(0, canvas.height - rendered.height)
        canvas.alpha_composite(rendered, (x, y))
        debug_rows.append(
            {
                "text": line.text[:80],
                "font_family": meta.get("sample_font", ""),
                "font_path": "",
                "font_size": meta.get("sample_size", 0),
                "bbox": meta["layer_bbox"],
                "font_ascent": 0,
                "font_descent": 0,
                "font_ink_height": 0,
                "font_baseline_ratio": 0.0,
                "sample_text": meta.get("sample_word", ""),
                "sample_bbox": (),
                "line_kind": meta["kind"],
                "attenuation": meta["attenuation"],
            }
        )

    return np.array(canvas.convert("L")), debug_rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prototype TSV-based synthetic page renderer")
    p.add_argument("input", help="Input image or PDF")
    p.add_argument("--output", default="synthetic.png", help="Output image path")
    p.add_argument("--tsv-output", default="synthetic.tsv", help="Optional TSV dump path")
    p.add_argument("--debug-output", default="synthetic_debug.png", help="Debug overlay output")
    p.add_argument("--page", type=int, default=1, help="PDF page (1-based)")
    p.add_argument("--dpi", type=int, default=300, help="PDF conversion DPI")
    p.add_argument("--lang", default="lat+grc", help="Tesseract language string")
    p.add_argument("--psm", type=int, default=6, help="Tesseract PSM")
    p.add_argument("--oem", type=int, default=1, help="Tesseract OEM")
    p.add_argument("--show-debug", action="store_true", help="Save a debug overlay")
    p.add_argument(
        "--font",
        action="append",
        default=[],
        help="Font family candidate; can be repeated. Uses provided list when empty.",
    )
    p.add_argument("--latin-fallback", default="Old Standard TT", help="Latin fallback font family")
    p.add_argument("--greek-fallback", default="GFS Didot", help="Greek fallback font family")
    return p


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    img = read_input(input_path, dpi=args.dpi, page=args.page)

    # Keep preprocessing intentionally light for layout fidelity.
    preprocessed = preprocess_for_tesseract(img)

    tsv_text = run_tesseract_tsv(preprocessed, lang=args.lang, psm=args.psm, oem=args.oem)
    if args.tsv_output:
        Path(args.tsv_output).write_text(tsv_text, encoding="utf-8")

    words, page_w, page_h = parse_tsv(tsv_text)
    if page_w <= 0 or page_h <= 0:
        page_h, page_w = preprocessed.shape[:2]

    lines = group_words_into_lines(words)
    gutter = detect_columns(lines, page_w)

    font_candidates = [clean_font_name(f) for f in (args.font or DEFAULT_FONT_CANDIDATES)]
    synthetic, debug_rows = reconstruct_page(
        page_w=page_w,
        page_h=page_h,
        lines=lines,
        words=words,
        font_candidates=font_candidates,
        latin_fallback=args.latin_fallback,
        greek_fallback=args.greek_fallback,
    )

    save_image(Path(args.output), synthetic)

    if args.show_debug:
        debug = draw_boxes_debug(preprocessed, lines, gutter)
        save_image(Path(args.debug_output), debug)

    # Plain text summary helps manual inspection in the terminal.
    print(f"input={input_path}")
    print(f"page={page_w}x{page_h}")
    print("padding_right=90 padding_bottom=90")
    print(f"words={len(words)} lines={len(lines)}")
    print(f"gutter={gutter[0]}..{gutter[1]}")
    print(f"output={args.output}")
    if args.show_debug:
        print(f"debug={args.debug_output}")
    if debug_rows:
        first = debug_rows[0]
        print(
            "first_word="
            f"font={first['font_family']} size={first['font_size']} "
            f"bbox={first['bbox']} text={first['text'][:80]}"
        )
        print(
            "first_metrics="
            f"ascent={first['font_ascent']} descent={first['font_descent']} "
            f"ink_height={first['font_ink_height']} baseline_ratio={first['font_baseline_ratio']}"
        )
        print(
            "first_sample="
            f"text={first['sample_text']} bbox={first['sample_bbox']}"
        )


if __name__ == "__main__":
    main()

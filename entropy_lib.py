import math
import zlib
from collections import Counter


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    n = len(text)
    if n == 0:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def compression_ratio(text: str) -> float:
    raw = len(text.encode("utf-8"))
    if raw == 0:
        return 0
    comp = len(zlib.compress(text.encode("utf-8")))
    return comp / raw


def alpha_ratio(text: str) -> float:
    if not text:
        return 0
    return sum(c.isalpha() for c in text) / len(text)


def detect_ocr_noise(text: str):
    H = shannon_entropy(text)
    C = compression_ratio(text)
    A = alpha_ratio(text)

    # Sinais que costumam indicar lixo de OCR:
    # - pouca proporção de caracteres alfabéticos (mistura de símbolos,
    #   diacríticos soltos, artefatos de PDF) → alpha_ratio muito baixo
    # - entropia alta com pouco texto “legível”
    # - compressão ruim (pouca redundância) combinada com alpha baixo
    flags = []

    if A < 0.45:
        flags.append("alpha_lt_0.45")
    if H > 4.7 and A < 0.55:
        flags.append("entropy_gt_4.7_and_low_alpha")
    if C > 0.8 and A < 0.60:
        flags.append("compress_ratio_gt_0.8_and_low_alpha")

    score = {
        "entropy": H,
        "compression_ratio": C,
        "alpha_ratio": A,
        "flags": flags,
    }

    noise = bool(flags)

    return noise, score

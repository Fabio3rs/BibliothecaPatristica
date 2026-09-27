"""OCR-aware sequence alignment: exact anchors + weighted edit paths and split/merge.

Equivalences affect comparison costs only; original spelling is never rewritten.
"""
from functools import lru_cache
import difflib
import unicodedata

from rapidfuzz.distance import DamerauLevenshtein

VERSION = "ocr-weighted-dp-v1"
MAX_CELLS = 250_000


@lru_cache(maxsize=32768)
def folded(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("æ", "ae").replace("œ", "oe")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


@lru_cache(maxsize=32768)
def confusion_form(text):
    return folded(text).replace("rn", "m").replace("cl", "d").replace("1", "l").replace("0", "o")


@lru_cache(maxsize=65536)
def ocr_distance(a, b):
    if a == b:
        return 0.0
    strict = DamerauLevenshtein.normalized_distance(a, b)
    typography = .12 + .88 * DamerauLevenshtein.normalized_distance(folded(a), folded(b))
    confusion = .22 + .78 * DamerauLevenshtein.normalized_distance(confusion_form(a), confusion_form(b))
    return min(strict, typography, confusion)


def edit_details(a, b):
    return [{"op": tag, "llm_span": [i, j], "tesseract_span": [k, n]}
            for tag, i, j, k, n in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]


def weighted_gap(a, b):
    """Returns token ranges. Large unanchored gaps are explicitly unresolved."""
    n, m = len(a), len(b)
    if n * m > MAX_CELLS:
        return [(0, n, 0, m, "unresolved")]
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0], trace[i][0] = i * .9, 1
    for j in range(1, m + 1):
        cost[0][j], trace[0][j] = j * .9, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            distance = ocr_distance(a[i - 1], b[j - 1])
            sub = 0 if distance == 0 else .1 + distance * 2.0
            choices = [(cost[i - 1][j - 1] + sub, 3), (cost[i - 1][j] + .9, 1), (cost[i][j - 1] + .9, 2)]
            for size in (2, 3):
                if i >= size:
                    distance = ocr_distance("".join(a[i - size:i]), b[j - 1])
                    if distance <= .3:
                        choices.append((cost[i - size][j - 1] + .18 + 2 * distance, size + 2))
                if j >= size:
                    distance = ocr_distance(a[i - 1], "".join(b[j - size:j]))
                    if distance <= .3:
                        choices.append((cost[i - 1][j - size] + .18 + 2 * distance, size + 4))
            cost[i][j], trace[i][j] = min(choices)
    result = []
    i, j = n, m
    while i or j:
        op = trace[i][j]
        di, dj = {1: (1, 0), 2: (0, 1), 3: (1, 1), 4: (2, 1), 5: (3, 1), 6: (1, 2), 7: (1, 3)}[op]
        status = "llm_only" if not dj else "tesseract_only" if not di else "split_merge" if max(di, dj) > 1 else "equal" if a[i - 1] == b[j - 1] else "replace"
        result.append((i - di, i, j - dj, j, status))
        i, j = i - di, j - dj
    return list(reversed(result))


def sequence_diff(a, b):
    """Anchor intact runs; solve noisy gaps without assuming valid dictionary words."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    i = j = 0
    result = []
    for block in matcher.get_matching_blocks():
        if block.size == 1 and (len(a[block.a]) < 5 or a.count(a[block.a]) > 1 or b.count(b[block.b]) > 1):
            continue
        for x1, x2, y1, y2, status in weighted_gap(a[i:block.a], b[j:block.b]):
            result.append((i + x1, i + x2, j + y1, j + y2, status))
        for offset in range(block.size):
            result.append((block.a + offset, block.a + offset + 1, block.b + offset, block.b + offset + 1, "equal"))
        i, j = block.a + block.size, block.b + block.size
    return result

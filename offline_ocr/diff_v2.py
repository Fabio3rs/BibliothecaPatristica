"""Experimental, auditable OCR comparison; no production acceptance policy.

Candidate is the saved transcription; reference is another OCR, not ground truth.
Reuse the reviewer's source parser, region assignment and character costs, while
keeping its v1 alignments unchanged. All offsets refer to the original inputs.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import hashlib
import unicodedata

import regex

from .diff import folded, ocr_distance
from .layout import BlockParser, assign_regions, can_join, parse_llm, parse_tsv

VERSION = "ocr-alignment-v2-experimental"
MAX_CELLS = 250_000
WORD = regex.compile(r"[\p{L}\p{N}][\p{L}\p{M}\p{N}]*")
SUPPORTED = {"equal", "normalized_equal", "typography_variant", "ocr_variant", "split_merge"}


def _key(text):
    return unicodedata.normalize("NFC", text).lower()


def _text_source(raw):
    parsed = parse_llm(raw)
    if regex.search(r"<pagina\b|<bloco\b", raw):
        parser = BlockParser(raw)
        parser.feed(raw)
        blocks = parser.blocks
    else:
        blocks = [{**parsed["blocks"][0], "char_spans": [[i, i + 1] for i in range(len(raw))]}]
    words = []
    for block in blocks:
        text = block["text"]
        spans = block["char_spans"]
        for match in WORD.finditer(text):
            ranges = []
            for start, end in spans[match.start():match.end()]:
                if ranges and ranges[-1][1] == start:
                    ranges[-1][1] = end
                elif not ranges or ranges[-1] != [start, end]:
                    ranges.append([start, end])
            tail = text[match.end():]
            hyphen = bool(regex.match(r"[-\u00ad\u2010]\h*\r?\n", tail))
            words.append({
                "id": f"w{len(words)}", "text": match.group(), "block_id": block["id"],
                "line_id": text.count("\n", 0, match.start()), "bbox": None, "conf": None,
                "source_spans": ranges, "hyphen_after": hyphen,
            })
    return {"blocks": parsed["blocks"], "words": words, "warnings": parsed["warnings"]}


def _tsv_source(raw, image_size):
    parsed = parse_tsv(raw, *image_size)
    words = []
    for word in parsed["words"]:
        matches = list(WORD.finditer(word["text"]))
        for index, match in enumerate(matches):
            words.append({**word, "id": f"{word['id']}.{index}", "text": match.group(),
                          "word_text": word["text"], "word_span": [match.start(), match.end()],
                          "source_spans": [],
                          "hyphen_after": index == len(matches) - 1 and
                          word["text"].endswith(("-", "\u00ad", "\u2010"))})
    return {**parsed, "words": words, "warnings": []}


def _tokens(source, prefix, spatial, dehyphenate):
    tokens = []
    previous = None
    for word in source["words"]:
        join = False
        if dehyphenate and previous and previous["hyphen_after"]:
            left = {**previous, "text": previous["text"] + "-"}
            join = can_join(left, word, "tesseract" if spatial else "llm")
        piece = {k: word[k] for k in (
            "id", "text", "bbox", "conf", "source_spans", "line_id", "block_id",
            "word_text", "word_span", "bbox_px", "tsv_row"
        ) if k in word}
        if join:
            tokens[-1]["text"] += word["text"]
            tokens[-1]["pieces"].append(piece)
            tokens[-1]["transform"] = "linebreak_hyphen_candidate"
        else:
            tokens.append({"id": f"{prefix}{len(tokens)}", "text": word["text"],
                           "block_id": word["block_id"], "pieces": [piece], "transform": "identity"})
        previous = word
    for token in tokens:
        token["key"] = _key(token["text"])
    return tokens


def _pair_kind(a, b):
    if a == b:
        return "equal"
    if folded(a) == folded(b):
        return "typography_variant"
    if any(c.isdigit() for c in a + b):
        return "uncertain_substitution"
    if min(len(a), len(b)) >= 3 and ocr_distance(a, b) <= .34:
        return "ocr_variant"
    return "uncertain_substitution"


def _gap(a, b, max_cells, merge_a, merge_b):
    n, m = len(a), len(b)
    if not n:
        return [(0, 0, j, j + 1, "reference_only") for j in range(m)]
    if not m:
        return [(i, i + 1, 0, 0, "candidate_only") for i in range(n)]
    if (n + 1) * (m + 1) > max_cells:
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
            substitution = 0 if distance == 0 else .1 + 2 * distance
            choices = [(cost[i - 1][j - 1] + substitution, 3),
                       (cost[i - 1][j] + .9, 1), (cost[i][j - 1] + .9, 2)]
            for size in (2, 3):
                if i >= size and merge_a(i - size, i):
                    distance = ocr_distance("".join(a[i - size:i]), b[j - 1])
                    if distance <= .25:
                        choices.append((cost[i - size][j - 1] + .18 + 2 * distance, size + 2))
                if j >= size and merge_b(j - size, j):
                    distance = ocr_distance(a[i - 1], "".join(b[j - size:j]))
                    if distance <= .25:
                        choices.append((cost[i - 1][j - size] + .18 + 2 * distance, size + 4))
            cost[i][j], trace[i][j] = min(choices)
    result = []
    i, j = n, m
    while i or j:
        di, dj = {1: (1, 0), 2: (0, 1), 3: (1, 1), 4: (2, 1), 5: (3, 1),
                  6: (1, 2), 7: (1, 3)}[trace[i][j]]
        status = ("candidate_only" if not dj else "reference_only" if not di else
                  "split_merge" if max(di, dj) > 1 else _pair_kind(a[i - 1], b[j - 1]))
        result.append((i - di, i, j - dj, j, status))
        i, j = i - di, j - dj
    return list(reversed(result))


def sequence_diff(a, b, max_cells=MAX_CELLS, *, merge_a=None, merge_b=None):
    """Anchor stable runs and solve noisy gaps with bounded weighted edits."""
    if max_cells < 1:
        raise ValueError("max_cells must be positive")
    merge_a = merge_a or (lambda start, end: True)
    merge_b = merge_b or (lambda start, end: True)
    counts_a, counts_b = Counter(a), Counter(b)
    i = j = 0
    result = []
    for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        if block.size == 1:
            word = a[block.a]
            if len(word) < 5 or counts_a[word] != 1 or counts_b[word] != 1:
                continue
        for x1, x2, y1, y2, status in _gap(
            a[i:block.a], b[j:block.b], max_cells,
            lambda start, end: merge_a(i + start, i + end),
            lambda start, end: merge_b(j + start, j + end),
        ):
            result.append((i + x1, i + x2, j + y1, j + y2, status))
        result.extend((block.a + k, block.a + k + 1, block.b + k, block.b + k + 1, "equal")
                      for k in range(block.size))
        i, j = block.a + block.size, block.b + block.size
    return result


def _nearby_pieces(tokens, start, end):
    for left, right in zip(tokens[start:end], tokens[start + 1:end]):
        a, b = left["pieces"][-1]["bbox"], right["pieces"][0]["bbox"]
        if a is None or b is None:
            if left["block_id"] != right["block_id"]:
                return False
            continue
        height = max(a[3] - a[1], b[3] - b[1])
        if abs(b[1] - a[1]) > 3 * height:
            return False
        if abs(b[1] - a[1]) < height and b[0] - a[2] > 6 * height:
            return False
    return True


def _regions(candidate, reference, blocks):
    groups, unplaced, evidence = assign_regions(candidate, reference, blocks, 25)
    owners = {t["id"]: block for block, tokens in groups.items() for t in tokens}
    # A noisy text envelope must not pull an omitted short word into another column.
    for token in reference:
        choices = token.get("region_candidates", [])
        if not choices or choices[0]["overlap"] >= .1 or choices[0]["nearby"] >= .8:
            continue
        spatial = [c for c in choices if c["overlap"] >= .5 and c["nearby"] >= .8]
        if len(spatial) == 1:
            selected = spatial[0]["block_id"]
            token["region_override"] = {"from": owners[token["id"]], "to": selected,
                                        "reason": "unique_original_box_contains_word"}
            token["region_uncertain"] = True
            owners[token["id"]] = selected
    groups = {b["id"]: [] for b in blocks}
    for token in reference:
        if token["id"] in owners:
            groups[owners[token["id"]]].append(token)
    return groups, unplaced, evidence


def _link(a, b, status, region):
    a_text, b_text = " ".join(t["text"] for t in a), " ".join(t["text"] for t in b)
    if status == "equal" and (a_text != b_text or any(t["transform"] != "identity" for t in a + b)):
        status = "normalized_equal"
    return {
        "candidate_ids": [t["id"] for t in a], "reference_ids": [t["id"] for t in b],
        "candidate_text": a_text, "reference_text": b_text, "status": status, "region": region,
        "region_uncertain": any(t.get("region_uncertain", False) for t in b),
        "character_edits": [
            {"op": op, "candidate_span": [i, j], "reference_span": [k, n]}
            for op, i, j, k, n in SequenceMatcher(None, a_text, b_text, autojunk=False).get_opcodes()
            if op != "equal"
        ],
    }


def _recover_reordered(links, candidate, reference):
    """Recover only exact, unique contiguous runs, consuming each token once."""
    indices_a = {t["id"]: i for i, t in enumerate(candidate)}
    indices_b = {t["id"]: i for i, t in enumerate(reference)}
    while True:
        aa = sorted((indices_a[x] for l in links if l["status"] == "candidate_only"
                     for x in l["candidate_ids"]))
        bb = sorted((indices_b[x] for l in links if l["status"] == "reference_only"
                     for x in l["reference_ids"]))
        keys_a = [candidate[i]["key"] for i in aa]
        keys_b = [reference[i]["key"] for i in bb]
        recovered = []
        for match in SequenceMatcher(None, keys_a, keys_b, autojunk=False).get_matching_blocks():
            if match.size < 3:
                continue
            a, b = aa[match.a:match.a + match.size], bb[match.b:match.b + match.size]
            if a[-1] - a[0] + 1 != len(a) or b[-1] - b[0] + 1 != len(b):
                continue
            phrase = keys_a[match.a:match.a + match.size]
            if sum(map(len, phrase)) < 12:
                continue
            # Repeated quotations cannot establish the location of a missing copy.
            if any(sum(tokens[k:k + len(phrase)] == phrase for k in range(len(tokens) - len(phrase) + 1)) != 1
                   for tokens in ([t["key"] for t in candidate], [t["key"] for t in reference])):
                continue
            recovered.append((a, b))
        if not recovered:
            return links
        used_a = {candidate[i]["id"] for a, _ in recovered for i in a}
        used_b = {reference[i]["id"] for _, b in recovered for i in b}
        links = [l for l in links if not (set(l["candidate_ids"]) & used_a or set(l["reference_ids"]) & used_b)]
        for a, b in recovered:
            links.append(_link([candidate[i] for i in a], [reference[i] for i in b], "reordered", None))


def _coalesce_conflicts(links, candidate, reference):
    """Opposing adjacent gaps are a disagreement, not two established absences."""
    a = {t["id"]: t for t in candidate}
    b = {t["id"]: t for t in reference}
    result = []
    i = 0
    while i < len(links):
        end = i
        while (end < len(links) and links[end]["status"] in {"candidate_only", "reference_only"}
               and links[end]["region"] == links[i]["region"]):
            end += 1
        group = links[i:end]
        if {l["status"] for l in group} == {"candidate_only", "reference_only"}:
            aa = [a[x] for l in group for x in l["candidate_ids"]]
            bb = [b[x] for l in group for x in l["reference_ids"]]
            result.append(_link(aa, bb, "uncertain_substitution", links[i]["region"]))
            i = end
        else:
            result.append(links[i])
            i += 1
    return result


def _runs(links, candidate, reference, status):
    lookup_a = {t["id"]: t for t in candidate}
    lookup_b = {t["id"]: t for t in reference}
    index_a = {t["id"]: i for i, t in enumerate(candidate)}
    index_b = {t["id"]: i for i, t in enumerate(reference)}
    side = "reference" if status == "reference_only" else "candidate"
    lookup, indices = (lookup_b, index_b) if side == "reference" else (lookup_a, index_a)
    selected = sorted((l for l in links if l["status"] == status), key=lambda l: indices[l[f"{side}_ids"][0]])
    runs = []
    for link in selected:
        ids = link[f"{side}_ids"]
        if (runs and runs[-1]["region"] == link["region"] and
                indices[runs[-1]["token_ids"][-1]] + 1 == indices[ids[0]]):
            runs[-1]["token_ids"].extend(ids)
        else:
            runs.append({"region": link["region"], "token_ids": list(ids)})
    all_tokens = reference if side == "reference" else candidate
    for run in runs:
        tokens = [lookup[x] for x in run["token_ids"]]
        first, last = indices[run["token_ids"][0]], indices[run["token_ids"][-1]]
        run.update({"text": " ".join(t["text"] for t in tokens), "token_count": len(tokens),
                    "context_before": " ".join(t["text"] for t in all_tokens[max(0, first - 5):first]),
                    "context_after": " ".join(t["text"] for t in all_tokens[last + 1:last + 6]),
                    "source_spans": [span for t in tokens for p in t["pieces"] for span in p["source_spans"]],
                    "boxes": [p["bbox"] for t in tokens for p in t["pieces"] if p["bbox"] is not None],
                    "region_uncertain": any(t.get("region_uncertain", False) for t in tokens),
                    "interpretation": "unmatched_in_candidate" if side == "reference" else "unmatched_in_reference"})
    return runs


def compare(candidate_text, reference_text=None, *, reference_tsv=None, image_size=None,
            dehyphenate=True, max_cells=MAX_CELLS):
    """Return paired variants and unmatched runs, never an automatic OCR verdict.

    TSV coordinates must refer to the original image. Text-only mode cannot prove
    column reading order. An unmatched token is evidence to review, not a repair.
    """
    if (reference_text is None) == (reference_tsv is None):
        raise ValueError("Provide exactly one of reference_text or reference_tsv")
    if reference_tsv is not None and (image_size is None or len(image_size) != 2):
        raise ValueError("TSV requires original image_size=(width, height)")
    source_a = _text_source(candidate_text)
    spatial = reference_tsv is not None
    source_b = _tsv_source(reference_tsv, image_size) if spatial else _text_source(reference_text)
    candidate = _tokens(source_a, "c", False, dehyphenate)
    reference = _tokens(source_b, "r", spatial, dehyphenate)
    links = []
    evidence = {}
    if spatial and any(b["bbox"] for b in source_a["blocks"]):
        groups, unplaced, evidence = _regions(candidate, reference, source_a["blocks"])
        regions = [(b["id"], [t for t in candidate if t["block_id"] == b["id"]], groups[b["id"]])
                   for b in source_a["blocks"]]
        mode = "text_and_geometry"
    else:
        regions, unplaced = [(None, candidate, reference)], []
        mode = "text_order_only"
    for region, a, b in regions:
        for i, j, k, n, status in sequence_diff(
            [t["key"] for t in a], [t["key"] for t in b], max_cells,
            merge_a=lambda start, end: _nearby_pieces(a, start, end),
            merge_b=lambda start, end: _nearby_pieces(b, start, end),
        ):
            links.append(_link(a[i:j], b[k:n], status, region))
    links.extend(_link([], [t], "reference_only", None) for t in unplaced)
    links = _recover_reordered(links, candidate, reference)
    links = _coalesce_conflicts(links, candidate, reference)
    warnings = list(source_a["warnings"] + source_b["warnings"])
    warnings.append("Divergence does not determine which OCR is correct; inspect the facsimile.")
    if mode == "text_order_only":
        warnings.append("Reading order is unverified: interleaved columns can hide or imitate omissions.")
    stats = Counter(l["status"] for l in links)
    coverage = {}
    for side, tokens in (("candidate", candidate), ("reference", reference)):
        counts = Counter()
        for link in links:
            counts[link["status"]] += len(link[f"{side}_ids"])
        coverage[side] = {"total_tokens": len(tokens), "by_status": dict(counts),
                          "supported_tokens": sum(counts[k] for k in SUPPORTED),
                          "unmatched_tokens": counts[f"{side}_only"]}
    return {
        "version": VERSION, "mode": mode, "dehyphenate": dehyphenate, "max_cells": max_cells,
        "comparison_scope": "Unicode words and numbers; punctuation and XML metadata excluded",
        "source_hashes": {"candidate": hashlib.sha256(candidate_text.encode()).hexdigest(),
                          "reference": hashlib.sha256((reference_tsv if spatial else reference_text).encode()).hexdigest()},
        "candidate_tokens": candidate, "reference_tokens": reference, "links": links,
        "stats": dict(stats), "coverage": coverage, "region_evidence": evidence,
        "omission_candidates": _runs(links, candidate, reference, "reference_only"),
        "addition_candidates": _runs(links, candidate, reference, "candidate_only"),
        "warnings": warnings, "automatic_acceptance": False,
    }

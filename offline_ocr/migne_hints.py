"""Compact physical OCR hints for Migne pages, using TSV geometry only.

These are hypotheses, not segmentation ground truth. Every nonempty TSV word is
retained exactly once, including low-confidence words and gutter markers.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import math
from statistics import median

from .layout import envelope, parse_tsv

VERSION = "migne-local-bands-v1"
FORMATS = ("raw_text", "ordered_text", "line_tsv", "block_tsv", "micro_xml")


def box(items):
    values = envelope([item["bbox"] for item in items])
    return [max(0, math.floor(values[0])), max(0, math.floor(values[1])),
            min(1000, math.ceil(values[2])), min(1000, math.ceil(values[3]))]


def _cy(word):
    return (word["bbox"][1] + word["bbox"][3]) / 2


def _height(word):
    return word["bbox"][3] - word["bbox"][1]


def _rows(words):
    rows = []
    for word in sorted(words, key=lambda w: (_cy(w), w["bbox"][0], w["id"])):
        chosen = None
        for row in reversed(rows[-3:]):
            height = median(_height(w) for w in row)
            if abs(_cy(word) - median(_cy(w) for w in row)) <= .6 * max(height, _height(word)):
                chosen = row
                break
        if chosen is None:
            rows.append([word])
        else:
            chosen.append(word)
    return [sorted(row, key=lambda w: (w["bbox"][0], w["id"])) for row in rows]


def _marker(word):
    return word["text"].strip(".()") in {"A", "B", "C", "D"} and word["bbox"][2] - word["bbox"][0] < 25


def _hyphen(word):
    return word["text"].endswith(("-", "\u00ad", "\u2010", "¬"))


def _cuts(rows, content_box):
    """Repeated whitespace corridors, reinforced by repeated hyphen endpoints."""
    if len(rows) < 3:
        return []
    gaps = []
    ordinary = []
    for index, row in enumerate(rows):
        evidence = [w for w in row if not _marker(w) and w["conf"] >= 20]
        for left, right in zip(evidence, evidence[1:]):
            x1, x2 = left["bbox"][2], right["bbox"][0]
            if x2 > x1:
                ordinary.append(x2 - x1)
                gaps.append((index, x1, x2, _hyphen(left)))
    typical = median(ordinary) if ordinary else 4
    minimum = max(9, min(22, typical * 2.5))
    width = content_box[2] - content_box[0]
    lo, hi = int(content_box[0] + .18 * width), int(content_box[2] - .18 * width)
    scores = []
    for x in range(lo, hi + 1):
        support = {i for i, a, b, hyphen in gaps if a <= x <= b and (b - a >= minimum or (hyphen and b - a >= 3))}
        narrow = {i for i, a, b, _ in gaps if a <= x <= b and b - a >= max(6, typical * 1.5)}
        hyphens = {i for i, a, b, hyphen in gaps if hyphen and a <= x <= b and b - a >= 3}
        crossings = sum(any(w["bbox"][0] < x < w["bbox"][2] and not _marker(w) for w in row) for row in rows)
        # Three-column Migne gutters may be narrower than justified interword gaps.
        # A persistent empty corridor supplies independent evidence in that case.
        persistent = len(narrow) >= 6 and len(narrow) / len(rows) >= .75 and crossings == 0
        if persistent:
            support |= narrow
        if len(support) < 3 or len(support) / len(rows) < .45 or crossings / len(rows) > .2:
            continue
        strength = len(support) / len(rows) + min(.3, len(hyphens) * .08) - crossings / len(rows)
        scores.append((x, strength, len(support), len(hyphens), crossings, persistent))
    corridors = []
    for value in scores:
        if corridors and value[0] == corridors[-1][-1][0] + 1:
            corridors[-1].append(value)
        else:
            corridors.append([value])
    proposals = []
    for corridor in corridors:
        top = max(v[1] for v in corridor)
        plateau = [v for v in corridor if v[1] >= top - .05]
        chosen = plateau[len(plateau) // 2]
        proposals.append({"x": chosen[0], "gap": [corridor[0][0], corridor[-1][0]],
                          "support_rows": chosen[2], "hyphen_rows": chosen[3],
                          "crossing_rows": chosen[4], "persistent_narrow_corridor": chosen[5],
                          "window_rows": len(rows), "strength": round(top, 4)})
    selected = []
    for proposal in sorted(proposals, key=lambda p: (-p["strength"], p["x"])):
        if all(abs(proposal["x"] - p["x"]) >= width * .18 for p in selected):
            selected.append(proposal)
        if len(selected) == 2:
            break
    return sorted(selected, key=lambda p: p["x"])


def _full_width_heading(row, content_box):
    letters = [c for w in row for c in w["text"] if c.isalpha()]
    upper = sum(c.isupper() for c in letters) / max(1, len(letters))
    bbox = box(row)
    width = content_box[2] - content_box[0]
    centered = abs((bbox[0] + bbox[2] - content_box[0] - content_box[2]) / 2) <= .08 * width
    return (len(row) <= 18 and upper >= .75 and
            (bbox[2] - bbox[0] >= .55 * width or (centered and bbox[2] - bbox[0] >= .18 * width)))


def analyse_tsv(raw, image_size):
    parsed = parse_tsv(raw, *image_size)
    words = parsed["words"]
    if not words:
        return {"version": VERSION, "image_size": list(image_size), "words": [], "bands": [], "blocks": [],
                "stats": {"source_words": 0, "assigned_words": 0}, "warnings": ["No words; this does not prove a blank image."]}
    rows = _rows(words)
    content_box = box(words)
    line_height = median(_height(w) for w in words)
    bands = []
    for index, row in enumerate(rows):
        proposals = _cuts(rows[max(0, index - 4):index + 5], content_box)
        heading = _full_width_heading(row, content_box)
        # A word crossing a proposed boundary is evidence for a spanning line.
        spanning = heading or any(w["bbox"][0] < p["x"] < w["bbox"][2]
                                  for p in proposals for w in row if not _marker(w))
        cuts = [] if spanning else [p["x"] for p in proposals]
        previous = bands[-1] if bands else None
        same = (previous is not None and previous["heading"] == heading and len(cuts) == len(previous["cuts"])
                and all(abs(a - b) <= 20 for a, b in zip(cuts, previous["cuts"]))
                and box(row)[1] - box(previous["rows"][-1])[3] < 3 * line_height)
        if same:
            previous["rows"].append(row)
            previous["boundary_evidence"].extend(proposals)
        else:
            bands.append({"cuts": cuts, "rows": [row], "heading": heading, "boundary_evidence": proposals})
    blocks = []
    band_details = []
    for index, band in enumerate(bands):
        cuts = band["cuts"]
        columns = defaultdict(list)
        for row in band["rows"]:
            groups = defaultdict(list)
            for word in row:
                center = (word["bbox"][0] + word["bbox"][2]) / 2
                marginal = _marker(word) and any(abs(center - cut) <= 20 for cut in cuts)
                column = "margin" if marginal else sum(center > cut for cut in cuts)
                groups[column].append(word)
            for column, content in groups.items():
                columns[column].append(content)
        for column in sorted(columns, key=lambda c: (isinstance(c, str), str(c))):
            chunks = []
            for row in columns[column]:
                if chunks and box(row)[1] - box(chunks[-1][-1])[3] < 3 * line_height:
                    chunks[-1].append(row)
                else:
                    chunks.append([row])
            for chunk in chunks:
                content = [word for row in chunk for word in row]
                lines = [{"bbox": box(row), "text": " ".join(w["text"] for w in row),
                          "word_ids": [w["id"] for w in row]} for row in chunk]
                bbox = box(content)
                area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
                density = sum((w["bbox"][2] - w["bbox"][0]) * _height(w) for w in content) / area
                blocks.append({"id": f"b{len(blocks)}", "band": index, "column": column,
                               "bbox": bbox, "kind_hint": "margin" if column == "margin" else "heading" if band["heading"] else "text",
                               "lines": lines, "text": "\n".join(l["text"] for l in lines),
                               "word_ids": [w["id"] for w in content], "word_box_density": round(min(1, density), 4)})
        band_details.append({"id": index, "bbox": box([w for row in band["rows"] for w in row]),
                             "columns": len(cuts) + 1, "cuts": cuts,
                             "boundary_evidence": band["boundary_evidence"], "heading": band["heading"]})
    assigned = [x for block in blocks for x in block["word_ids"]]
    if Counter(assigned) != Counter(w["id"] for w in words):
        raise AssertionError("Layout must conserve every source word exactly once")
    return {"version": VERSION, "image_size": list(image_size), "words": words,
            "bands": band_details, "blocks": blocks,
            "stats": {"source_words": len(words), "assigned_words": len(assigned), "blocks": len(blocks),
                      "column_counts": sorted({b["columns"] for b in band_details})},
            "warnings": ["Physical bands and reading order are hypotheses; verify against the image.",
                         "Low-confidence words are retained. No spelling correction or dehyphenation is applied."]}


def raw_text(layout):
    lines = []
    last_line = None
    for word in layout["words"]:
        if word["line_id"] != last_line:
            lines.append([])
            last_line = word["line_id"]
        lines[-1].append(word["text"])
    return "\n".join(" ".join(line) for line in lines)


def render_hint(layout, format_name):
    if format_name not in FORMATS:
        raise ValueError(f"Unknown hint format: {format_name}")
    if format_name == "raw_text":
        return raw_text(layout)
    if format_name == "ordered_text":
        return "\n\n".join(b["text"] for b in layout["blocks"])
    if format_name == "micro_xml":
        blocks = [f'<b box="{",".join(map(str, b["bbox"]))}" hint="{b["kind_hint"]}">{escape(b["text"])}</b>'
                  for b in layout["blocks"]]
        return '<ocr scale="1000" heuristic="true">\n' + "\n".join(blocks) + "\n</ocr>"
    rows = ["block\tbox\ttext" if format_name == "block_tsv" else "block\tline\tbox\ttext"]
    for block in layout["blocks"]:
        if format_name == "block_tsv":
            text = block["text"].replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
            rows.append(f'{block["id"]}\t{",".join(map(str, block["bbox"]))}\t{text}')
        else:
            for index, line in enumerate(block["lines"]):
                rows.append(f'{block["id"]}\t{index}\t{",".join(map(str, line["bbox"]))}\t{line["text"]}')
    return "\n".join(rows)

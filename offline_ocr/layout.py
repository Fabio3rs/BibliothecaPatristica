"""Lossless source tracking and a replaceable, conservative layout alignment."""
from __future__ import annotations

import csv
import html
import io
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser

from .diff import VERSION, edit_details, ocr_distance, sequence_diff

FORMAT_VERSION = "layout-v2"
NORMALIZER_VERSION = "linebreak-v2"
ALIGNER_VERSION = VERSION + "+text-geometry-v3"


def comparison_key(text):
    return unicodedata.normalize("NFC", text)


def valid_box(value):
    try:
        box = [float(x) for x in value.split(",")]
        if len(box) == 4 and 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000:
            return box
    except (ValueError, AttributeError):
        pass
    return None


class BlockParser(HTMLParser):
    """Keep raw XML character ranges, including entity references and nested text."""

    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.starts = [0] + [m.end() for m in re.finditer("\n", raw)]
        self.blocks = []
        self.current = None

    def raw_offset(self):
        line, col = self.getpos()
        return self.starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "bloco":
            attrs = dict(attrs)
            self.current = {
                "id": f"b{len(self.blocks)}", "type": attrs.get("tipo", "outro"),
                "script": attrs.get("script", "desconhecido"),
                "bbox": valid_box(attrs.get("bbox")), "attrs": attrs,
                "text": "", "char_spans": [], "raw_start": self.raw_offset(),
            }
            self.blocks.append(self.current)
        elif tag == "br" and self.current is not None:
            self.append("\n", self.raw_offset(), self.raw_offset() + len(self.get_starttag_text()))

    def handle_endtag(self, tag):
        if tag == "bloco" and self.current is not None:
            self.current["raw_end"] = self.raw_offset() + len("</bloco>")
            self.current = None

    def append(self, text, start, end=None):
        if self.current is None:
            return
        self.current["text"] += text
        self.current["char_spans"].extend(
            [[start, end]] * len(text) if end is not None
            else [[start + i, start + i + 1] for i in range(len(text))]
        )

    def handle_data(self, data):
        self.append(data, self.raw_offset())

    def handle_entityref(self, name):
        raw = "&" + name + ";"
        start = self.raw_offset()
        if not self.raw.startswith(raw, start):
            raw = raw[:-1]
        self.append(html.unescape(raw), start, start + len(raw))

    def handle_charref(self, name):
        self.handle_entityref("#" + name)


def parse_llm(raw):
    warnings = []
    if re.search(r"<pagina\b|<bloco\b", raw):
        try:
            ET.fromstring(raw.strip())
        except ET.ParseError:
            warnings.append("XML malformado: blocos recuperados; confira o original.")
        parser = BlockParser(raw)
        parser.feed(raw)
        blocks = parser.blocks
        if not blocks and not re.search(r'estado=[\"\']vazio[\"\']', raw):
            warnings.append("XML sem blocos recuperáveis; texto bruto preservado.")
    else:
        blocks = [{"id": "b0", "type": "outro", "script": "desconhecido", "bbox": None,
                   "attrs": {}, "text": raw, "char_spans": [[i, i + 1] for i in range(len(raw))]}]
        warnings.append("Texto sem layout XML: alinhamento pela ordem do Tesseract.")
    tokens = []
    for block in blocks:
        text = block["text"]
        spans = block.pop("char_spans")
        block_tokens = []
        for match in re.finditer(r"\S+", text):
            mapped = spans[match.start():match.end()]
            ranges = []
            for start, end in mapped:
                if ranges and ranges[-1][1] == start:
                    ranges[-1][1] = end
                elif not ranges or ranges[-1] != [start, end]:
                    ranges.append([start, end])
            block_tokens.append({
                "id": f"l{len(tokens) + len(block_tokens)}", "text": match.group(),
                "block_id": block["id"], "line_id": text.count("\n", 0, match.start()),
                "source_spans": ranges, "bbox": None, "conf": None,
                "local_start": match.start(), "local_end": match.end(),
            })
        tokens.extend(block_tokens)
        if block["bbox"] is None:
            warnings.append(f"{block['id']}: sem caixa válida; localização não confirmada.")
    return {"raw": raw, "blocks": blocks, "words": tokens, "warnings": warnings}


def parse_tsv(raw, width, height):
    reader = csv.DictReader(io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "page_num", "block_num", "par_num", "line_num", "word_num",
                "left", "top", "width", "height", "conf", "text"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("TSV não contém o cabeçalho completo do Tesseract")
    nodes, words = [], []
    seen_ids = set()
    for number, row in enumerate(reader, 2):
        level = int(row["level"])
        x, y, w, h = [int(row[k]) for k in ("left", "top", "width", "height")]
        if level not in range(1, 6) or w < 0 or h < 0 or not math.isfinite(float(row["conf"])):
            raise ValueError(f"TSV linha {number}: nível, dimensões ou confiança inválidos")
        if int(row["page_num"]) != 1 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise ValueError(f"TSV linha {number}: coordenadas fora da imagem original")
        if level == 1 and (w, h) != (width, height):
            raise ValueError("Dimensões do TSV não correspondem à imagem original")
        path = [int(row[k]) for k in ("page_num", "block_num", "par_num", "line_num", "word_num")]
        node = {"id": "t" + ".".join(map(str, path[:level])), "level": level,
                "bbox": [x * 1000 / width, y * 1000 / height,
                         (x + w) * 1000 / width, (y + h) * 1000 / height],
                "bbox_px": [x, y, x + w, y + h], "text": row["text"] or "",
                "conf": float(row["conf"]), "tsv_row": number,
                "block_id": f"t{path[0]}.{path[1]}",
                "paragraph_id": ".".join(map(str, path[:3])),
                "line_id": ".".join(map(str, path[:4]))}
        if node["id"] in seen_ids:
            raise ValueError(f"TSV linha {number}: identificador duplicado")
        seen_ids.add(node["id"])
        nodes.append(node)
        if level == 5 and node["text"].strip():
            words.append(node.copy())
    return {"raw_tsv": raw, "nodes": nodes, "words": words, "width": width, "height": height}


def can_join(left, right, source):
    text = left["text"]
    if not text or text[-1] not in "-\u00ad\u2010":
        return False
    stem = [c for c in text[:-1] if not unicodedata.combining(c)]
    if not stem or not stem[-1].isalpha():
        return False
    if not right["text"] or not right["text"][0].isalpha():
        return False
    if left["block_id"] != right["block_id"] or left["line_id"] == right["line_id"]:
        return False
    if source == "llm":
        return right["line_id"] == left["line_id"] + 1
    a, b = left["bbox"], right["bbox"]
    return (left["paragraph_id"] == right["paragraph_id"] and
            0 < b[1] - a[1] < 3 * max(a[3] - a[1], b[3] - b[1]) and b[0] < a[2])


def normalize_words(words, source, dehyphenate=True):
    """A flow token retains all source word IDs, spans and separate physical boxes."""
    out = []
    for word in words:
        piece = {k: word[k] for k in ("id", "text", "bbox", "conf")}
        piece["source_spans"] = word.get("source_spans", [])
        if dehyphenate and out and can_join(out[-1]["_last"], word, source):
            token = out[-1]
            token["text"] = token["text"][:-1] + word["text"]
            token["pieces"].append(piece)
            token["transform"] = "linebreak_hyphen_candidate"
            token["_last"] = word
        else:
            out.append({"id": f"{source[0]}n{len(out)}", "text": word["text"],
                        "block_id": word["block_id"], "pieces": [piece],
                        "transform": "identity", "_last": word})
    pos = 0
    for token in out:
        token.pop("_last")
        token["key"] = comparison_key(token["text"])
        token["flow_span"] = [pos, pos + len(token["text"])]
        pos += len(token["text"]) + 1
    return out


def box_overlap(word, block):
    a, b = word, block
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return area / max(0.001, (a[2] - a[0]) * (a[3] - a[1]))


def expanded(box, margin):
    return [max(0, box[0] - margin), max(0, box[1] - margin),
            min(1000, box[2] + margin), min(1000, box[3] + margin)]


def envelope(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def assign_regions(left, right, blocks, tolerance):
    """Use unique text anchors to supplement approximate LLM boxes, never replace them."""
    owners = {}
    for token in left:
        owners.setdefault(token["key"], set()).add(token["block_id"])
    counts = Counter(t["key"] for t in right)
    anchors = {b["id"]: [] for b in blocks}
    for token in right:
        matches = owners.get(token["key"], set())
        if len(matches) == 1 and counts[token["key"]] == 1 and len(token["key"]) >= 5:
            anchors[next(iter(matches))].append(token)
    evidence = {}
    for block in blocks:
        support = anchors[block["id"]]
        inferred = envelope([p["bbox"] for t in support for p in t["pieces"]]) if len(support) >= 3 else None
        original = block["bbox"]
        evidence[block["id"]] = {
            "original_bbox": original, "anchor_bbox": inferred,
            "anchor_ids": [t["id"] for t in support], "anchor_count": len(support),
            "edge_delta": [round(a - b, 2) for a, b in zip(inferred, original)] if inferred and original else None,
        }
    groups = {b["id"]: [] for b in blocks}
    unplaced = []
    spatial = any(b["bbox"] for b in blocks)
    for token in right:
        candidates = []
        matches = owners.get(token["key"], set())
        for block in blocks:
            original = block["bbox"]
            inferred = evidence[block["id"]]["anchor_bbox"]
            boxes = [p["bbox"] for p in token["pieces"]]
            overlap = sum(box_overlap(p, original) for p in boxes) / len(boxes) if original else 0
            nearby = sum(box_overlap(p, expanded(original, tolerance)) for p in boxes) / len(boxes) if original else 0
            anchored = sum(box_overlap(p, expanded(inferred, tolerance)) for p in boxes) / len(boxes) if inferred else 0
            unique = matches == {block["id"]} and len(token["key"]) >= 5 and counts[token["key"]] == 1
            lexical = block["id"] in matches
            fuzzy = 0.0
            if not lexical and max(nearby, anchored) >= .2 and len(token["key"]) >= 4:
                fuzzy = max((1 - ocr_distance(token["key"], t["key"]) for t in left
                             if t["block_id"] == block["id"] and abs(len(t["key"]) - len(token["key"])) <= 3), default=0)
                if fuzzy < .7:
                    fuzzy = 0.0
            score = 3 * unique + 1.5 * anchored + .8 * nearby + .4 * overlap + 1.8 * lexical + .8 * fuzzy
            if unique or nearby >= .35 or anchored >= .35:
                candidates.append({"block_id": block["id"], "score": round(score, 4),
                                   "overlap": round(overlap, 4), "nearby": round(nearby, 4),
                                   "anchor_overlap": round(anchored, 4), "unique_text_anchor": unique, "fuzzy_text_similarity": round(fuzzy, 4)})
        candidates.sort(key=lambda x: (-x["score"], x["block_id"]))
        token["region_candidates"] = candidates
        if candidates:
            first = candidates[0]
            token["region_uncertain"] = (len(candidates) > 1 and first["score"] - candidates[1]["score"] < .6) or (first["overlap"] < .4 and not first["unique_text_anchor"]) or first["nearby"] < .35
            groups[first["block_id"]].append(token)
        elif not spatial and blocks:
            token["region_uncertain"] = True
            groups[blocks[0]["id"]].append(token)
        else:
            unplaced.append(token)
    return groups, unplaced, evidence


def align(llm, tess, dehyphenate=True, tolerance=25):
    left = normalize_words(llm["words"], "llm", dehyphenate)
    right = normalize_words(tess["words"], "tesseract", dehyphenate)
    blocks = llm["blocks"]
    groups, unplaced, evidence = assign_regions(left, right, blocks, tolerance)
    links = []

    def emit(a, b, status, region):
        ambiguous = any(t.get("region_uncertain", False) for t in b)
        links.append({"id": f"a{len(links)}", "llm_ids": [t["id"] for t in a],
                      "tesseract_ids": [t["id"] for t in b], "status": status,
                      "region": region, "uncertain": ambiguous or status not in ("equal", "normalized_equal"),
                      "geometry": "inferred_from_tesseract" if a and b else "unlocated" if a else "tesseract",
                      "similarity": round(1 - ocr_distance(" ".join(t["key"] for t in a), " ".join(t["key"] for t in b)), 4),
                      "character_edits": edit_details(" ".join(t["text"] for t in a), " ".join(t["text"] for t in b))})


    for block in blocks:
        a = [t for t in left if t["block_id"] == block["id"]]
        b = groups[block["id"]]
        for i, j, k, n, status in sequence_diff([t["key"] for t in a], [t["key"] for t in b]):
            aa, bb = a[i:j], b[k:n]
            if status == "equal":
                raw_a = " ".join(p["text"] for t in aa for p in t["pieces"])
                raw_b = " ".join(p["text"] for t in bb for p in t["pieces"])
                status = "equal" if raw_a == raw_b else "normalized_equal"
            emit(aa, bb, status, block["id"])
    for token in unplaced:
        emit([], [token], "tesseract_only", None)
    return {"version": ALIGNER_VERSION, "normalizer": NORMALIZER_VERSION,
            "dehyphenate": dehyphenate, "bbox_tolerance": tolerance, "region_evidence": evidence, "llm_tokens": left, "tesseract_tokens": right,
            "llm_flow": " ".join(t["text"] for t in left),
            "tesseract_flow": " ".join(t["text"] for t in right), "links": links,
            "stats": dict(Counter(x["status"] for x in links)),
            "warnings": llm["warnings"] + ["Alinhamento heurístico: divergência não determina qual OCR está correto."]}

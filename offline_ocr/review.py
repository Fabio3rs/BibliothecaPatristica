"""Auditable human corrections; originals and automatic alignments remain immutable."""
from __future__ import annotations

import html
import json
import math
import re

from .layout import FORMAT_VERSION, align, parse_llm
from .store import canonical, digest, get_comparison, identity, save_artifact


class RevisionConflict(ValueError):
    pass


def latest_revision(con, key):
    row = con.execute("SELECT id,payload,created_at FROM revisions WHERE comparison_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    if row:
        return {"id": row["id"], **json.loads(row["payload"]), "created_at": row["created_at"]}
    return {"id": 0, "words": {}, "boxes": {}, "note": ""}


def save_revision(con, key, base_id, word_changes, box_changes, note=""):
    result = get_comparison(con, key)
    if result is None:
        raise ValueError("Comparação não encontrada")
    tokens = {t["id"] for t in result["alignment"]["llm_tokens"]}
    blocks = {b["id"] for b in result["llm"]["blocks"]}
    if not isinstance(word_changes, dict) or not isinstance(box_changes, dict):
        raise ValueError("Ajustes devem ser objetos")
    if set(word_changes) - tokens or set(box_changes) - blocks:
        raise ValueError("Palavra ou bloco fora desta comparação")
    for text in word_changes.values():
        if text is not None and (not isinstance(text, str) or len(text) > 10000 or any(ord(c) < 32 and c not in '\n\t\r' for c in text)):
            raise ValueError("Texto de correção inválido")
    for box in box_changes.values():
        if box is not None and (not isinstance(box, list) or len(box) != 4 or
            not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box) or
            not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000)):
            raise ValueError("BBox deve satisfazer 0 ≤ x1 < x2 ≤ 1000 e 0 ≤ y1 < y2 ≤ 1000")
    if not isinstance(note, str) or len(note) > 10000:
        raise ValueError("Nota inválida")
    con.execute("BEGIN IMMEDIATE")
    try:
        old = latest_revision(con, key)
        if old["id"] != base_id:
            raise RevisionConflict("Outra revisão foi salva. Recarregue antes de ajustar.")
        words, boxes = dict(old["words"]), dict(old["boxes"])
        for target, changes in ((words, word_changes), (boxes, box_changes)):
            for item, value in changes.items():
                if value is None:
                    target.pop(item, None)
                else:
                    target[item] = value
        payload = {"words": words, "boxes": boxes, "note": note}
        if words == old["words"] and boxes == old["boxes"] and note == old["note"]:
            con.rollback()
            return old
        con.execute("INSERT INTO revisions(comparison_key,parent_id,payload) VALUES (?,?,?)",
                    (key, base_id or None, canonical(payload)))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return latest_revision(con, key)


def corrected_xml(result, revision):
    raw = result["llm"]["raw"]
    xml = bool(re.search(r"<pagina\b|<bloco\b", raw))
    patches = []
    for token in result["alignment"]["llm_tokens"]:
        if token["id"] not in revision["words"]:
            continue
        text = revision["words"][token["id"]]
        spans = [span for p in token["pieces"] for span in p["source_spans"]]
        for index, (start, end) in enumerate(spans):
            patches.append((start, end, html.escape(text, quote=False) if xml and index == 0 else text if index == 0 else ""))
    if revision["boxes"] and not xml:
        raise ValueError("Texto sem XML não contém atributos bbox; use exportação JSON")
    for block in result["llm"]["blocks"]:
        if block["id"] not in revision["boxes"]:
            continue
        start = block["raw_start"]
        end = raw.index(">", start)
        tag = raw[start:end]
        bbox = ",".join(f"{x:g}" for x in revision["boxes"][block["id"]])
        match = re.search(r'''\bbbox\s*=\s*(["'])(.*?)\1''', tag)
        if match:
            patches.append((start + match.start(2), start + match.end(2), bbox))
        else:
            patches.append((end, end, f' bbox="{bbox}"'))
    patches.sort()
    for a, b in zip(patches, patches[1:]):
        if a[1] > b[0]:
            raise ValueError("Ajustes sobrepostos; confira as relações antes de exportar")
    for start, end, text in reversed(patches):
        raw = raw[:start] + text + raw[end:]
    return raw


def refine(con, key, expected_revision=None):
    source = get_comparison(con, key)
    if source is None:
        raise ValueError("Comparação não encontrada")
    revision = latest_revision(con, key)
    if expected_revision is not None and revision["id"] != expected_revision:
        raise RevisionConflict("Revisão alterada; recarregue")
    raw = corrected_xml(source, revision)
    provenance = {"parent_comparison": key, "revision_id": revision["id"],
                  "original_provenance": source["llm"]["provenance"]}
    llm_key = identity({"parent": source["llm_key"], "revision": revision["id"], "text": digest(raw.encode()), "provenance": provenance, "parser": FORMAT_VERSION})
    llm = save_artifact(con, llm_key, "llm", {**parse_llm(raw), "provenance": provenance})
    old = source["alignment"]
    alignment = align(llm, source["tesseract"], old["dehyphenate"], old["bbox_tolerance"])
    new_key = identity({"parent_comparison": key, "revision": revision["id"], "llm": llm_key,
                        "tess": source["tess_key"], "aligner": alignment["version"],
                        "normalizer": alignment["normalizer"], "tolerance": old["bbox_tolerance"],
                        "dehyphenate": old["dehyphenate"]})
    with con:
        con.execute("""INSERT OR IGNORE INTO comparisons
            (key,label,image_path,image_hash,llm_key,tess_key,payload) VALUES (?,?,?,?,?,?,?)""",
            (new_key, source["label"].split(" · revisão")[0] + f" · revisão {revision['id']}",
             source["image_path"], source["image_hash"], llm_key, source["tess_key"], canonical(alignment)))
    return new_key

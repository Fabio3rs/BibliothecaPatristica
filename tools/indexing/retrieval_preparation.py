"""Offline preparation/auditing for the isolated retrieval experiment."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher

from tools.ocr_embedding_ir import (
    embedding_visible_text, structural_visible_text, canonical_xml_type,
    header_fingerprint, _ONLY_LOCATOR_RE, _ARGUMENTATIVE_HEADER_RE,
)

PREPARATION_VERSION = "retrieval-preparation-v4"
MARGINAL_TYPES = {"nota_marginal", "nota marginal", "note_marginal", "nombre_marginal"}
MARGINAL_LETTERS = re.compile(r"[ABCDΑΒΓΔ](?:[\s,;/|.\-]+[ABCDΑΒΓΔ]){0,3}")
SHORT_BODY_TITLE = re.compile(
    r"(?:LIBER|CAPUT|CAPITULUM|PSALMUS|IN\s+PSALMUM)\s+(?:[IVXLCDM]+|\d+)\.?", re.I
)
TITLE_TYPES = {"cabecalho", "titulo", "titulo_secao"}
BODY_TYPES = {"texto_principal", "text_principal"}
OPEN_RANGE = re.compile(r"\d+\s*[,;:]?\s*(?:et\s+seq\w*\.?|sqq?\.|ff\.)(?!\w)", re.I)
PLACEHOLDER = re.compile(
    r"\[\s*(?:ileg[ií]vel|illegible|unreadable|lacuna|texto\s+ileg[ií]vel)\s*\]", re.I
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def text_exclusion(text):
    if not text.strip():
        return "empty_text"
    if not any(c.isalpha() for c in PLACEHOLDER.sub("", text)):
        return "placeholder_or_nonlinguistic_text"
    return None


def label_audit(query):
    texts = [query.get("entry_raw") or ""]
    texts += [r.get("ref_raw") or "" for r in query.get("references", [])]
    reasons = []
    if any(OPEN_RANGE.search(t) for t in texts):
        reasons.append("open_range_requires_complete_verified_targets")
    warnings = []
    if query.get("label_origin") in {"previous_jev", "mixed"}:
        warnings.append("previous_jev_is_not_manual_ground_truth")
    if not query.get("context_raw"):
        warnings.append("editorial_context_not_recorded")
    return {"query_id": query["query_id"], "eligible": not reasons,
            "exclusion_reasons": reasons, "warnings": warnings,
            "human_review_status": "pending",
            "query": query["query"], "entry_raw": query.get("entry_raw"),
            "context_raw": query.get("context_raw"),
            "gold_files": query["gold_files"], "references": query.get("references", [])}


def xml_exclusion(block, text, repeated_headers):
    """Only whole layout blocks; never remove letters/numbers from prose."""
    kind = canonical_xml_type(block)
    if kind in MARGINAL_TYPES:
        marker = text.strip(" \t\n[]().")
        if MARGINAL_LETTERS.fullmatch(marker):
            return "xml_marginal_letter_marker"
        if _ONLY_LOCATOR_RE.fullmatch(marker):
            return "xml_marginal_numeric_locator"
    # Repetition is evidence for running headings, but not for notes/footnotes:
    # the latter can repeat genuine scripture references or substantive content.
    if (kind == "cabecalho" or block.tag_name == "cabecalho") and len(text) <= 240:
        if (header_fingerprint(text) in repeated_headers
                and not _ARGUMENTATIVE_HEADER_RE.search(text)):
            return "xml_repeated_running_header"
    return None


def prepare_blocks(ocr, cleaning="xml-v2", repeated_headers=frozenset()):
    """Retain source text and explicit edit spans, not invented XML byte offsets.

    Mapping coordinates are Python character offsets in content_raw (the XML
    parser's decoded block text) and the embedding text. Replace spans are
    many-to-many evidence, never claimed to be exact character alignments.
    """
    blocks = []
    parts = []
    cursor = 0
    for order, block in enumerate(ocr.blocks):
        raw = block.content_raw
        structural = structural_visible_text(raw)
        # Preserve legacy embedding bytes for cache reuse where possible.
        cleaned = embedding_visible_text(block.content_clean)
        excluded = text_exclusion(cleaned)
        # Preserve numeric material (e.g. table cells) inside substantive blocks.
        # Only purely numeric running headers/footers and explicit placeholders
        # can be removed at block level. Entire numeric-only pages stay excluded.
        if (excluded == "placeholder_or_nonlinguistic_text" and not PLACEHOLDER.search(cleaned)
                and block.tipo not in {"cabecalho", "rodape"}
                and block.tag_name not in {"cabecalho", "rodape"}):
            excluded = None
        if (cleaning == "xml-v2" and not excluded
                and canonical_xml_type(block) in MARGINAL_TYPES
                and not any(c.isalnum() for c in cleaned)):
            excluded = "xml_marginal_symbol_only"
        if cleaning in {"xml-v1", "xml-v2"} and not excluded:
            excluded = xml_exclusion(block, cleaned, repeated_headers)
        start = end = None
        if not excluded:
            if parts:
                cursor += 2
            start = cursor
            parts.append(cleaned)
            cursor += len(cleaned)
            end = cursor
        mapping = [dict(operation=op, source_start=a, source_end=b,
                        clean_start=c, clean_end=d)
                   for op, a, b, c, d in SequenceMatcher(None, raw, cleaned).get_opcodes()]
        blocks.append({"block_id": f"b{order + 1}:{sha(raw)[:12]}",
                       "order": order, "type": block.tipo, "canonical_type": canonical_xml_type(block),
                       "script": block.script,
                       "source_text": raw, "source_sha256": sha(raw),
                       "structural_text": structural, "text": cleaned,
                       "cleaning_policy": cleaning,
                       "text_sha256": sha(cleaned), "char_start": start, "char_end": end,
                       "offset_space": "decoded_block_text_python_characters",
                       "source_mapping": mapping, "excluded_reason": excluded})
    return "\n\n".join(parts), blocks


def source_spans(page, start, end):
    spans = []
    for block in page.get("blocks", []):
        a, b = block["char_start"], block["char_end"]
        if a is not None and a < end and start < b:
            spans.append({"block_id": block["block_id"], "type": block["type"],
                          "script": block["script"], "source_sha256": block["source_sha256"],
                          "clean_start": max(start, a) - a, "clean_end": min(end, b) - a})
    return spans


def is_short_title(block):
    text = block["text"]
    kind = block.get("canonical_type", block["type"])
    return (len(text) <= 160 and any(c.isalpha() for c in text)
            and (kind in TITLE_TYPES or (kind in BODY_TYPES and SHORT_BODY_TITLE.fullmatch(text))))


def block_groups(page, attach_titles=False):
    """Group only consecutive surviving blocks; never cross notes or pages.

    Unattached titles remain explicit units rather than silently removing pages
    from the ranking. No inferred translation or editorial continuation.
    """
    blocks = [b for b in page["blocks"] if not b["excluded_reason"]]
    groups = []
    i = 0
    while i < len(blocks):
        first = i
        attached = False
        if attach_titles and is_short_title(blocks[i]):
            j = i
            while j < len(blocks) and is_short_title(blocks[j]):
                j += 1
            if j < len(blocks):
                body = blocks[j]
                scripts = {str(b.get("script") or "").strip().casefold()
                           for b in blocks[i:j + 1]} - {"", "unknown", "desconhecido"}
                if body.get("canonical_type", body["type"]) in BODY_TYPES and len(scripts) <= 1:
                    i = j
                    attached = True
        group = blocks[first:i + 1]
        groups.append({"char_start": group[0]["char_start"], "char_end": group[-1]["char_end"],
                       "block_ids": [b["block_id"] for b in group],
                       "title_context": "attached" if attached else (
                           "unattached" if attach_titles and is_short_title(group[0]) else "none")})
        i += 1
    return groups


def page_units(page, chunker, boundaries="block"):
    if chunker is None:
        units = [{"text": page["text"], "char_start": 0, "char_end": len(page["text"])}]
    elif boundaries in {"block", "block-context"} and page.get("blocks"):
        units = []
        for group in block_groups(page, attach_titles=boundaries == "block-context"):
            start, end = group["char_start"], group["char_end"]
            for chunk in chunker.chunks(page["text"][start:end]):
                unit = dict(chunk)
                unit["char_start"] += start
                unit["char_end"] += start
                unit["token_offset_space"] = "block_group" if len(group["block_ids"]) > 1 else "block"
                unit["group"] = group
                units.append(unit)
    else:
        units = chunker.chunks(page["text"])
        for unit in units:
            unit["token_offset_space"] = "page"
    for unit in units:
        unit["source_spans"] = source_spans(page, unit["char_start"], unit["char_end"])
        unit["char_offset_space"] = "clean_page_text_python_characters"
        unit["char_count"] = len(unit["text"])
        if "token_start" in unit:
            unit["token_count"] = unit["token_end"] - unit["token_start"]
    return units


def canonical_hash(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def freeze_candidates(queries, plans, pages, scope):
    selected = {}
    for query in queries:
        plan = plans[query["query_id"]]
        if "selected" not in plan:
            continue
        selected[query["query_id"]] = {
            "query_sha256": canonical_hash(query), "selected": plan["selected"],
            "sources": {p: {k: pages[p][k] for k in ("ocr_sha256", "text_sha256")}
                        for p in plan["selected"]}}
    value = {"version": PREPARATION_VERSION, "scope": scope, "queries": selected}
    return {**value, "sha256": canonical_hash(value)}


def restore_candidates(manifest, queries, pages, scope, refresh_texts=False):
    value = {k: v for k, v in manifest.items() if k != "sha256"}
    accepted_versions = {PREPARATION_VERSION}
    if refresh_texts:
        accepted_versions.update({"retrieval-preparation-v2", "retrieval-preparation-v3"})
    if manifest.get("sha256") != canonical_hash(value) or value.get("version") not in accepted_versions:
        raise ValueError("Candidate manifest integrity/version mismatch")
    if value["scope"] != scope or set(value["queries"]) != {q["query_id"] for q in queries}:
        raise ValueError("Candidate manifest scope/query set mismatch")
    plans = {}
    for query in queries:
        row = value["queries"][query["query_id"]]
        if row["query_sha256"] != canonical_hash(query):
            raise ValueError("Candidate manifest query/positive snapshot changed")
        if set(row["sources"]) != set(row["selected"]) or not set(query["gold_files"]) <= set(row["selected"]):
            raise ValueError("Candidate manifest must preserve every positive and source")
        refreshed = []
        for path, hashes in row["sources"].items():
            page = pages.get(path)
            if (not page or page["volume"] != query["volume"] or page.get("excluded_reason")
                    or page["ocr_sha256"] != hashes.get("ocr_sha256")
                    or (not refresh_texts and page["text_sha256"] != hashes.get("text_sha256"))):
                raise ValueError(f"Frozen candidate source changed or excluded: {path}")
            if page["text_sha256"] != hashes.get("text_sha256"):
                refreshed.append({"path": path, "old_text_sha256": hashes.get("text_sha256"),
                                  "new_text_sha256": page["text_sha256"]})
        if scope == "volume":
            expected = {p for p, d in pages.items() if d["volume"] == query["volume"] and not d.get("excluded_reason")}
            if expected != set(row["selected"]):
                raise ValueError("Frozen full-volume candidate universe changed")
        plans[query["query_id"]] = {"base": row["selected"], "selected": row["selected"],
                                   "hard_pool": [], "frozen": True,
                                   "text_refresh": refreshed,
                                   "parent_manifest_sha256": manifest["sha256"]}
    return plans

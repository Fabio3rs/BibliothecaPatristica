"""Literal region grammars for uncovered Latin index and contents families.

No volume ID selects a grammar. Explicit headings/notices select conventions;
all offsets address the original OCR. This module neither opens files nor maps
numbers to pages. Its residuals preserve text that did not yield a reference.
"""
from __future__ import annotations

import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import DEPENDENT

VERSION = "missing-latin-regions-v3"
SUBDIVISION = r"(?:[ \t]*,?[ \t]+[A-D](?:[ \t]*,[ \t]*[A-D])*(?!\w))?"
ATOM = rf"(?:\d{{1,4}}(?:\s*[-–—]\s*\d{{1,4}})?|ibid(?:em)?\.?|ib\.){SUBDIVISION}"
SUFFIX = r"(?:\s+(?:et\s+)?(?:seqq?\.?|sqq\.?|alibi|passim))?"
LIST = rf"{ATOM}{SUFFIX}(?:\s*[,;]\s*{ATOM}{SUFFIX})*"
QUALIFIER = r"(?:[apc]\s*\.\s*m\.|m\.\s*init\.|pr\.\s*f\.|init\.|fin\.)"
QUALIFIED = re.compile(rf"(?<!\w)\d{{1,4}}\s+{QUALIFIER}(?:\s*not\.)?(?:\s+\d{{1,4}}\s+{QUALIFIER}(?:\s*not\.)?)*", re.I)
DUAL = re.compile(r"(?<!\w)(?P<refs>(?:\d{1,4}(?:[-–]\d{1,4})?|[—–])\s+(?:\d{1,4}(?:[-–]\d{1,4})?|[—–]))\s*[.]?\s*$")
INTERNAL_NUMBER_NOTICE = re.compile(
    r"\bnumer\w*.{0,140}\b(?:crassi?ior|grandior)\w*"
    r".{0,80}\b(?:textui|in\s+textu)\b.{0,30}\b(?:insert|express)\w*", re.I)
TITLE = re.compile(r"^(?:INDEX|INDICES|NOTA\b|Revocatur\s+lector|Numeri\s+Arabici|QU(?:Æ|AE)\s+IN\b)", re.I)


def _trim(raw, a, b, chars=None):
    while a < b and (raw[a].isspace() if chars is None else raw[a] in chars):
        a += 1
    while a < b and (raw[b-1].isspace() if chars is None else raw[b-1] in chars):
        b -= 1
    return a, b


def _context(context, sid):
    if not context:
        return {}
    if sid in context and isinstance(context[sid], dict):
        return context[sid]
    return context


def _convention(context, observed):
    text = " ".join(str(context.get(k, "")) for k in ("title", "notice", "heading", "text")) + " " + observed
    compact = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    namespace = context.get("reference_namespace") or "unknown"
    family = "literal"
    kind = context.get("entry_kind") or "index_entry"
    if INTERNAL_NUMBER_NOTICE.search(compact):
        namespace = "internal_text_number"
    elif re.search(r"numeros\s+uncis\s+conclusos", compact, re.I):
        namespace = "bracketed_preface_roman_or_note_arabic"
    if re.search(r"Ferrand\w*.*Criscon", compact, re.I):
        family, namespace = "parallel_columns", "dual_canonical_numbers"
    elif re.search(r"Indicatur\s+locus.*incipit\s+rei\s+tractatio", compact, re.I):
        family = "qualified_locators"
    if (re.search(r"INDEX\s+RERUM\s+QU(?:Æ|AE)\s+IN\s+HOC\b", compact, re.I)
            and re.search(r"CONTINENTUR", compact, re.I)
            and re.search(r"\b(?:TOMO|VOLUMINE)\b", compact, re.I)
            and namespace not in {"internal_text_number", "bracketed_preface_roman_or_note_arabic"}):
        kind = "contents"
    if context.get("kind") in {"contents_catalog", "work_catalog", "capitula"}:
        kind = "capitula" if context["kind"] == "capitula" else "contents"
    if re.search(r"INDEX\s+CAPITULORUM", compact, re.I):
        family, namespace, kind = "numbered_capitula", "chapter_number", "capitula"
    elif re.search(r"INDEX\s+SCRIPTORUM\s+ECCLESIASTICORUM", compact, re.I):
        family, namespace = "chapter_names", "book_chapter" if re.search(r"\blib\.\s*[ivxlcdm]+[, ]+\s*(?:c|cap)\.", compact, re.I) else "chapter_number"
        kind = "onomastic_entry"
    return family, namespace, kind


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    """Parse explicit non-overlapping source scopes into literal owned references.

    ``scopes`` contains ``span: [start,end]`` and optional ``section_id``.
    ``section_context`` is a convention dictionary or section-ID->dictionary.
    No unqualified number is designated a page. Headings and unparsed text are
    returned as residuals, not deleted. Clause context remains an alternative.
    """
    contexts=(section_context or {}).values()
    families={c.get('internal_index_family') for c in contexts if isinstance(c,dict) and c.get('internal_index_family')}
    if len(families)==1:
        from tools.indexing.internal_index_formats import parse_internal_page
        return parse_internal_page(raw,family=next(iter(families)),source=source,volume=volume,
                                   physical=physical,scopes=scopes,section_context=section_context)
    intervals = sorted((s["span"][0], s["span"][1], str(s.get("section_id", ""))) for s in scopes)
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i-1][1]):
            raise ValueError("Invalid or overlapping source scopes")
    out = {"version": VERSION, "source": source, "volume": volume, "physical": physical,
           "source_sha256": hashlib.sha256(raw.encode()).hexdigest(), "entries": [], "residuals": []}
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r"</?\w+", raw):
        source_blocks = [{"body_start": 0, "body_end": len(raw), "body": raw, "kind": "texto_principal"}]

    def residue(a, b, reason, sid):
        a, b = _trim(raw, a, b)
        if a < b:
            out["residuals"].append({"span": [a, b], "raw": raw[a:b], "reason": reason, "section_id": sid})

    if not source_blocks:
        for a, b, sid in intervals:
            residue(a, b, "unsupported_markup", sid)
        return out

    for a, b, sid in intervals:
        ctx = _context(section_context, sid)
        observed = " ".join(x["body"] for x in source_blocks
                            if x["kind"] in {"cabecalho", "titulo", "nota"}
                            and a <= x["body_start"] < b)
        # Notices are also encoded as short principal blocks.
        observed += " " + " ".join(x["body"] for x in source_blocks
                                    if a <= x["body_start"] < b and TITLE.match(x["body"].strip()))
        family, namespace, entry_kind = _convention(ctx, observed)
        # Repeated literal qualifiers identify continuations even when the
        # regional detector split an index away from its opening notice.
        if family == "literal" and entry_kind == "index_entry" and len(QUALIFIED.findall(raw[a:b])) >= 3:
            family = "qualified_locators"
        atom = ATOM
        if namespace == "bracketed_preface_roman_or_note_arabic":
            atom = rf"(?:{ATOM}|[ivxlcdm]+)"
        reflist = rf"{atom}{SUFFIX}(?:\s*[,;]\s*{atom}{SUFFIX})*"
        comma = re.compile(rf",\s*(?P<refs>{reflist})[.](?=\s|$)", re.I)
        terminal = re.compile(rf"(?<!\w)(?P<refs>{reflist})[.]?\s*$", re.I)
        parent = None
        for block in source_blocks:
            start, end = max(a, block["body_start"]), min(b, block["body_end"])
            if start >= end:
                continue
            if block["kind"] != "texto_principal":
                residue(start, end, "editorial_nonbody", sid)
                continue
            if family == "numbered_capitula":
                body = raw[start:end]
                starts = list(re.finditer(r"(?m)^[ \t]*(?:CAP\.?\s*)?(?P<ref>[IVXLCDM]+|\d{1,4})[.]\s+", body, re.I))
                previous_end = start
                for index, match in enumerate(starts):
                    left, right = start + match.start(), start + (starts[index + 1].start() if index + 1 < len(starts) else len(body))
                    ra, rb = start + match.start("ref"), start + match.end("ref")
                    oa, ob = _trim(raw, start + match.end(), right)
                    closure = re.search(r"(?im)^\s*(?:Indicis capitulorum finis|FINIS\b|INDEX\b)", raw[oa:ob])
                    if closure:
                        ob = oa + closure.start()
                        oa, ob = _trim(raw, oa, ob)
                    residue(previous_end, left, "non_capitulum_text", sid)
                    if oa >= ob or not any(c.isalpha() for c in raw[oa:ob]):
                        residue(left, right, "chapter_without_description", sid)
                    elif re.search(r"\d+[.]\s+[A-Z]", raw[oa:ob]):
                        residue(left, right, "possible_interleaved_columns", sid)
                    else:
                        out["entries"].append({"section_id": sid, "source": source, "volume": volume,
                            "physical": physical, "entry_span": [left, ob], "owner_span": [oa, ob],
                            "owner_raw": raw[oa:ob], "reference_raw": raw[ra:rb], "reference_span": [ra, rb],
                            "reference_namespace": namespace, "entry_kind": entry_kind, "grammar": family,
                            "flags": ["literal_candidate_not_certified", "prefix_chapter_number"],
                            "chapter_context": ctx.get("title") or ctx.get("heading")})
                        residue(ob, right, "after_capitulum_description", sid)
                    previous_end = right
                residue(previous_end, end, "non_capitulum_text", sid)
                continue
            cursor = start
            pending = None
            for line in raw[start:end].splitlines(keepends=True):
                la, lb = _trim(raw, cursor, cursor + len(line))
                cursor += len(line)
                if la >= lb:
                    if pending is not None:
                        residue(pending, la, "unparsed_paragraph", sid)
                        pending = None
                    parent = None
                    continue
                text = raw[la:lb]
                if TITLE.match(text) or re.fullmatch(r"[^\W\d_]\.?", text):
                    if pending is not None:
                        residue(pending, la, "unparsed_before_heading", sid)
                        pending = None
                    residue(la, lb, "heading_or_notice", sid)
                    parent = None
                    continue
                if pending is not None and re.match(r"(?:CAPUT|LIBER|[IVXLCDM]+[.])\s", text):
                    residue(pending, la, "unreferenced_heading_or_description", sid)
                    pending = None
                if pending is not None:
                    la = pending
                    text = raw[la:lb]
                matches = []
                if family == "chapter_names":
                    chapter = re.search(r"(?<!\w)(?P<refs>(?:(?:lib\.\s*[ivxlcdm]+,?\s*)?(?:c|cap)\.\s*)?\d{1,4}(?:[ ,]+\d{1,4})*)[.]?\s*$", text, re.I)
                    if chapter:
                        matches = [chapter.span("refs")]
                elif family == "parallel_columns":
                    match = DUAL.search(text)
                    if match:
                        matches = [match.span("refs")]
                    else:
                        match = terminal.search(text)
                        if match:
                            matches = [match.span("refs")]
                elif family == "qualified_locators":
                    matches = [m.span() for m in QUALIFIED.finditer(text)]
                else:
                    matches = [m.span("refs") for m in comma.finditer(text)
                               if not re.match(r"\s*PP[.]", text[m.end():], re.I)]
                    tail_start = matches[-1][1] + 1 if matches else 0
                    match = terminal.search(text, tail_start)
                    if match and (not matches or match.start() >= matches[-1][1]):
                        matches.append(match.span("refs"))
                if not matches:
                    # Only hard-wrapped prose may be held for the next physical line.
                    if text.rstrip().endswith(('-', '—')) or not re.search(r"[.!?]\s*$", text):
                        pending = la
                    else:
                        residue(la, lb, "unsupported_or_cross_reference", sid)
                        pending = None
                    continue
                pending = None
                owner_cursor = 0
                last_entry = None
                for ra, rb in matches:
                    oa, ob = _trim(raw, la + owner_cursor, la + ra, " \t\r\n,.;")
                    flags = ["literal_candidate_not_certified"]
                    if not any(c.isalpha() for c in raw[oa:ob]):
                        if last_entry and not raw[oa:ob].strip(".,; "):
                            last_entry["reference_span"][1] = la + rb
                            last_entry["reference_raw"] = raw[slice(*last_entry["reference_span"])]
                            last_entry["entry_span"][1] = la + rb
                            last_entry["flags"].append("adjacent_locator_list")
                        else:
                            residue(la + owner_cursor, la + rb, "reference_without_owner", sid)
                        owner_cursor = rb
                        continue
                    owner = raw[oa:ob]
                    if re.search(r"\d", owner):
                        flags.append("numeric_text_in_owner_requires_review")
                    if "\n" in owner:
                        flags.append("hard_wrapped_owner")
                    if len(owner) > 600:
                        flags.append("long_owner_segmentation_requires_review")
                    if DEPENDENT.match(owner) or not owner[0].isupper():
                        flags.append("possible_inherited_owner_context")
                    if family == "parallel_columns":
                        flags.append("parallel_column_assignment_unverified")
                    entry = {"section_id": sid, "source": source, "volume": volume, "physical": physical,
                             "entry_span": [oa, la + rb], "owner_raw": owner, "owner_span": [oa, ob],
                             "reference_raw": raw[la + ra:la + rb], "reference_span": [la + ra, la + rb],
                             "reference_namespace": namespace, "entry_kind": entry_kind,
                             "grammar": family, "flags": flags}
                    if "possible_inherited_owner_context" in flags and parent:
                        entry["parent_owner_span_candidate"] = parent
                    out["entries"].append(entry)
                    last_entry = entry
                    parent = [oa, ob]
                    owner_cursor = rb
                tail_a, tail_b = _trim(raw, la + owner_cursor, lb, " \t\r\n.,;")
                if tail_a < tail_b:
                    pending = tail_a
            if pending is not None:
                residue(pending, end, "incomplete_or_unsupported_tail", sid)
    return out

"""Offline PG069 entry segmentation experiment; never a canonical importer."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.scan_editorial_ocr import BLOCK, ATTR
from tools.corpus_utils import page_files
from tools.ocr_xml_utils import join_linebreak_hyphenation

ROOT = Path(__file__).resolve().parents[2]
VERSION = "pg069-entry-parser-poc-v1"
ATOM = r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s+(?:et\s+)?(?:seqq?|sqq|fin|passim)\.?)?"
REF_END = re.compile(
    rf",\s*(?P<refs>(?:{ATOM}|ibid\.?)(?:\s*,\s*(?:{ATOM}|ibid\.?))*)\.", re.I)
CROSS_END = re.compile(r"\b(?:Vide|Confer|Cf\.)\s+[^.]{1,100}\.", re.I)
BOUNDARY = re.compile(r"^\s*(?:ORDO\s+RERUM|ERRATA|CORRIGENDA|ADDENDA)\b", re.I | re.M)
DEPENDENT = re.compile(r"^(?:Quid|Qui|Quæ|Cur|Quare|Ejus|Est|Ex eo|Data|Defendit|Sepulta|"
                       r"Significat|Provocat|Non vult|Duxit|Hæc|Qualis|De ejus)\b", re.I)
SEGMENTATION_WARNINGS = {
    "cross_reference_with_leading_unsegmented_text", "reference_without_lemma",
    "numeric_text_before_reference_group", "leading_continuation_requires_review",
    "unsupported_editorial_namespace", "possible_internal_or_scriptural_locator",
    "unparsed_locator_tail", "possible_trailing_continuation", "missing_parent_context",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def display(text):
    return " ".join(join_linebreak_hyphenation(text).split())


def normalized(text):
    return display(text).casefold().strip(" .")


def source_span(raw, start, end):
    return {"char_start": start, "char_end": end,
            "line_start": raw.count("\n", 0, start) + 1,
            "line_end": raw.count("\n", 0, max(start, end - 1)) + 1,
            "text_sha256": digest(raw[start:end].encode())}


def paragraphs(body):
    start = 0
    for separator in [*re.finditer(r"\n[ \t]*\n", body), None]:
        end = separator.start() if separator else len(body)
        chunk = body[start:end]
        left = len(chunk) - len(chunk.lstrip())
        if chunk.strip():
            yield start + left, chunk.strip()
        start = separator.end() if separator else end


def parse_page(raw, *, source="memory", physical=None, volume="PG069", profile="pg069", context=None):
    """Preserve exact character spans. No bbox, destination or DB evidence enters parsing."""
    if profile == "po-auto-v1":
        from tools.indexing.entry_parser_po import parse_po_page
        return parse_po_page(raw, source=source, physical=physical, volume=volume, context=context)
    if profile not in {"pg069", "generic-literal-experimental"}:
        raise ValueError("Unknown parser profile")
    if profile == "pg069" and volume != "PG069":
        raise ValueError("Only the experimental PG069 profile is implemented")
    entries, residuals, events = [], [], []
    letter, section = None, "glaphyra_analytic_candidate" if profile == "pg069" else None
    stopped = False
    eligible_chars = consumed_chars = 0
    source_hash = digest(raw.encode())

    def residual(start, end, reason, block):
        if raw[start:end].strip():
            residuals.append({"reason": reason, "block": block,
                              "raw": raw[start:end], **source_span(raw, start, end)})

    for block_index, match in enumerate(BLOCK.finditer(raw)):
        attrs = dict(ATTR.findall(match["attrs"]))
        kind = attrs.get("tipo", attrs.get("type", ""))
        body, origin = match["body"], match.start("body")
        boundary = BOUNDARY.search(body)
        if kind == "cabecalho" and boundary is None:
            boundary = re.search(r"\b(?:ORDO\s+RERUM|ERRATA|CORRIGENDA|ADDENDA)\b", body, re.I)
        if boundary:
            # The boundary may start inside a body block; keep the owned prefix below.
            events.append({"kind": "boundary", "text": body[boundary.start():],
                           **source_span(raw, origin + boundary.start(), match.end("body"))})
            if kind != "texto_principal":
                stopped = True
                continue
            body = body[:boundary.start()]
            boundary_after = True
        else:
            boundary_after = False
        if kind == "cabecalho":
            if profile == "pg069" and "INDEX AD S. CYRILLI FRAGMENTA" in display(body).upper():
                section, letter = "supplement_index_candidate", None
                events.append({"kind": "section_transition", "section": section,
                               **source_span(raw, origin, match.end("body"))})
            continue
        if kind == "nota_marginal" and re.fullmatch(r"[A-ZÆŒ]", body.strip()):
            letter = body.strip()
            events.append({"kind": "observed_letter", "letter": letter,
                           "status": "xml_order_candidate",
                           **source_span(raw, origin, match.end("body"))})
            continue
        if kind != "texto_principal":
            continue
        if stopped:
            residual(origin, match.end("body"), "outside_owned_boundary", block_index)
            continue
        if "<" in body or "&" in body:
            residual(origin, origin + len(body), "markup_requires_alignment", block_index)
            if boundary_after:
                stopped = True
            continue
        # Paragraph breaks are soft context boundaries, not proof of independent lemmas.
        for paragraph_start, text in paragraphs(body):
            base = origin + paragraph_start
            eligible_chars += sum(not ch.isspace() for ch in text)
            cursor, context_anchor = 0, None
            endings = [(m.start(), m.end(), "material", m) for m in REF_END.finditer(text)]
            endings += [(m.start(), m.end(), "cross_reference", m) for m in CROSS_END.finditer(text)]
            for _, end, entry_type, terminator in sorted(endings):
                if terminator.start() < cursor:
                    continue
                start = cursor
                while start < end and text[start].isspace():
                    start += 1
                raw_entry = text[start:end]
                if not raw_entry:
                    continue
                flags = [] if profile == "pg069" else ["unvalidated_profile"]
                reference_raw = terminator["refs"] if entry_type == "material" else None
                concept_end = terminator.start() if entry_type == "material" else end
                concept = text[start:concept_end].strip()
                if cursor == 0 and concept and concept[0].islower():
                    flags.append("leading_continuation_requires_review")
                if entry_type == "cross_reference" and terminator.start() > start:
                    flags.append("cross_reference_with_leading_unsegmented_text")
                if not any(c.isalpha() for c in concept):
                    flags.append("reference_without_lemma")
                if re.search(r"\b(?:Leo|[IVX]+)\s*,?\s*$", concept):
                    flags.append("unsupported_editorial_namespace")
                if re.search(r"\b(?:psalm\w*|cap\.|lib\.|tom\.)", concept, re.I):
                    flags.append("possible_internal_or_scriptural_locator")
                if re.search(r"\d", concept):
                    flags.append("numeric_text_before_reference_group")
                if reference_raw and re.search(r"ibid|seq|sqq|fin|passim|[-–—]", reference_raw, re.I):
                    flags.append("qualified_reference_preserved")
                dependent = profile == "pg069" and bool(DEPENDENT.match(display(concept)))
                if dependent:
                    flags.append("context_requires_review")
                entry_id = digest(f"{source}:{source_hash}:{base+start}:{base+end}".encode())[:20]
                record = {"id": entry_id, "source": source, "physical_page": physical,
                          "block": block_index, "section_candidate": section,
                          "observed_letter": letter, "letter_status": "xml_order_unverified",
                          "entry_type": entry_type, "entry_raw": raw_entry,
                          "text_raw": concept, "text_display": display(concept),
                          "references_raw": reference_raw,
                          "context_candidate_id": context_anchor if dependent else None,
                          "flags": flags, "status": "candidate_only",
                          **source_span(raw, base + start, base + end)}
                if entry_type == "material":
                    record["reference_span"] = source_span(raw, base + terminator.start("refs"),
                                                           base + terminator.end("refs"))
                entries.append(record)
                consumed_chars += sum(not ch.isspace() for ch in raw_entry)
                if not dependent:
                    context_anchor = entry_id
                cursor = end
            residual(base + cursor, base + len(text), "unclosed_or_unsupported_text", block_index)
        if boundary_after:
            stopped = True
    return {"source": source, "physical_page": physical, "sha256": source_hash, "profile": profile,
            "entries": entries, "residuals": residuals, "events": events,
            "coverage": {"eligible_nonspace_chars": eligible_chars,
                         "consumed_nonspace_chars": consumed_chars,
                         "syntactic_fraction": consumed_chars / eligible_chars if eligible_chars else None},
            "import_ready": False}



def compare_rows(page, rows):
    """Compare literal units; index adjacent groups once, without crossing sections."""
    from collections import defaultdict

    exact_index, group_index = defaultdict(list), defaultdict(list)
    for i, row in enumerate(rows):
        exact_index[normalized(row["entry_raw"])].append(row)
        for width in (2, 3, 4):
            group = rows[i:i + width]
            if len(group) == width and len({r.get("section_key") for r in group}) == 1:
                group_index[normalized(" ".join(r["entry_raw"] for r in group))].append(group)
    result = []
    for entry in page["entries"]:
        literal = normalized(entry["entry_raw"])
        exact = exact_index.get(literal, [])
        groups = group_index.get(literal, []) if not exact else []
        literal_status = ("literal_agreement" if len(exact) == 1 else
                          "ambiguous_existing_literal" if exact else
                          "split_in_database_candidate" if len(groups) == 1 else
                          "multiple_join_candidates" if groups else "no_literal_alignment")
        status = "parser_ambiguity_requires_review" if SEGMENTATION_WARNINGS.intersection(entry["flags"]) else literal_status
        linked = exact if exact else groups[0] if len(groups) == 1 else []
        result.append({"candidate_id": entry["id"], "physical_page": page["physical_page"],
                       "entry_raw": entry["entry_raw"], "status": status, "old_entries": linked,
                       "literal_alignment": literal_status, "flags": entry["flags"],
                       "letter_disagreement": bool(linked and entry["observed_letter"] and
                           any(r["heading_letter"] != entry["observed_letter"] for r in linked)),
                       "application_supported": False})
    return result


def db_diff(pages, db):
    """Proposals only, compared on source file + literal text, never destinations."""
    result = []
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        for page in pages:
            rows = [dict(r) for r in con.execute(
                "SELECT entry_key,entry_order,entry_raw,lemma_raw,context_raw,heading_letter "
                "FROM alphabetical_entries WHERE index_entry_source_ocr_file=? "
                "ORDER BY entry_order,entry_key", (page["source"],))]
            result.extend(compare_rows(page, rows))
    finally:
        con.close()
    return result


def check_examples(pages, checks_path):
    """Small visually inspected set; neither whole-page precision nor recall."""
    checks = json.loads(checks_path.read_text())
    results = []
    by_page = {p["physical_page"]: p for p in pages}
    for case in checks["cases"]:
        if case["physical_page"] not in by_page:
            continue
        page = by_page[case["physical_page"]]
        raw = Path(page["source"]).read_text()
        literal = case["source_literal"]
        if raw.count(literal) != 1:
            raise ValueError(f"Check source changed or ambiguous: {case['id']}")
        start = raw.index(literal)
        expected = (start, start + len(literal))
        matches = [e for e in page["entries"] if (e["char_start"], e["char_end"]) == expected]
        # Deliberately simple diagnostic baseline: identical recognizer, each line separately.
        baseline = []
        for line in literal.splitlines():
            baseline.extend(parse_page(f'<bloco tipo="texto_principal">{line}</bloco>')["entries"])
        results.append({"id": case["id"], "physical_page": case["physical_page"],
                        "exact_segmentation": len(matches) == 1,
                        "literal_reference_assignment": len(matches) == 1 and
                            matches[0]["references_raw"] == case["references_raw"],
                        "line_baseline_full_unit": any(normalized(e["entry_raw"]) == normalized(literal)
                                                       for e in baseline),
                        "reference_expected": case["references_raw"],
                        "candidate_ids": [e["id"] for e in matches]})
    return {"source": str(checks_path), "sha256": digest(checks_path.read_bytes()),
            "review_method": checks["review_method"], "cases": results,
            "checked": len(results), "exact_segmentation": sum(r["exact_segmentation"] for r in results),
            "literal_reference_assignment": sum(r["literal_reference_assignment"] for r in results),
            "line_baseline_full_unit": sum(r["line_baseline_full_unit"] for r in results),
            "limitation": "Selected development cases, not independent evaluation or corpus precision/recall."}


def run(args):
    paths = page_files(args.root / "PG069/text", suffixes=[".txt"])
    first, last = map(int, args.pages.split("-"))
    if not 740 <= first <= last <= 744:
        raise ValueError("This PoC is bounded to PG069 physical pages 740–744")
    selected = []
    for n in range(first, last + 1):
        if len(paths.get(n, [])) != 1:
            raise ValueError(f"Missing/ambiguous OCR for physical page {n}")
        selected.append((n, paths[n][0].resolve()))
    args.out.mkdir(parents=True, exist_ok=False)
    pages = [parse_page(p.read_text(), source=str(p), physical=n) for n, p in selected]
    diffs = db_diff(pages, args.db) if args.db else []
    checks = check_examples(pages, args.checks)
    summary = {"version": VERSION, "profile": "PG069-local-experimental", "pages": len(pages),
               "entries": sum(len(p["entries"]) for p in pages),
               "material_reference_groups": sum(e["entry_type"] == "material" for p in pages for e in p["entries"]),
               "residuals": sum(len(p["residuals"]) for p in pages),
               "flag_counts": dict(Counter(flag for p in pages for e in p["entries"] for flag in e["flags"])),
               "selected_checks": {k: checks[k] for k in ("checked", "exact_segmentation",
                    "literal_reference_assignment", "line_baseline_full_unit")},
               "diff_counts": dict(Counter(d["status"] for d in diffs)),
               "db_updates": 0, "inference_calls": 0, "import_ready": False,
               "limitation": "Syntactic coverage is not segmentation/attribution accuracy. Hierarchy and XML letter order unverified."}
    for name, value in [("pages", pages), ("diff", diffs), ("summary", summary), ("checks", checks),
                        ("config", {"pages": args.pages, "root": str(args.root.resolve()),
                                    "db": str(args.db.resolve()) if args.db else None,
                                    "script_sha256": digest(Path(__file__).read_bytes())})]:
        (args.out / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Prova de conceito: parser PG069", "", json.dumps(summary, ensure_ascii=False), "",
             "Resultados são candidatos; referências e banco não foram modificados.", ""]
    for p in pages:
        lines += [f"## Arquivo físico {p['physical_page']}", "",
                  f"{len(p['entries'])} unidades; {len(p['residuals'])} resíduos; "
                  f"cobertura sintática {p['coverage']['syntactic_fraction']}.", ""]
    lines += ["## Possíveis divisões antigas", ""]
    lines += [f"- {d['physical_page']}: {display(d['entry_raw'])} → " +
              ", ".join(e['entry_key'] for e in d['old_entries']) for d in diffs
              if d['status'] == 'split_in_database_candidate']
    lines += ["", "## Ambiguidades do parser", "",
              "Os casos abaixo não sustentam uma proposta de junção, mesmo quando os literais coincidem com registros adjacentes.", ""]
    lines += [f"- {d['physical_page']}: {display(d['entry_raw'])} ({', '.join(d['flags'])})"
              for d in diffs if d['status'] == 'parser_ambiguity_requires_review']
    (args.out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "teste")
    parser.add_argument("--pages", default="740-744")
    parser.add_argument("--db", type=Path, default=ROOT / "data/alphabetical_indices.db")
    parser.add_argument("--checks", type=Path, default=ROOT / "tests/fixtures/pg069_parser_checks.json")
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args())

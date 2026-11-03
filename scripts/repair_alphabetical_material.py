#!/usr/bin/env python3
"""Repair demonstrable index associations in a NEW SQLite copy, without an agent.

Example:
  python scripts/repair_alphabetical_material.py --volume PL010 --volume PG148 \
    --volume PG003 --output-db /tmp/alphabetical-repaired.db --report /tmp/repair.json

This is deliberately a partial repair: only source-anchored, isolated citations
are reassigned. Ambiguous groups and unsupported editions are explicitly blocked
for automatic location. Original rows and source hashes remain in the copy's
alphabetical_deterministic_repairs table. Normal mode never changes the input.
Explicit --apply-reviewed REPORT [--apply-reviewed NEWER_REPORT] applies reviewed
proposals transactionally to --db, after an exclusive --backup and source/row
checks. A later report supersedes the same entry from an earlier report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.alphabetical_index_db import refresh_volume_quality
from scripts.resolve_alphabetical_unresolved import DEFAULT_DB, connect_readonly, write_json
from tools.indexing.index_target_locator import normalize_for_search

VERSION = 2
METHOD = "source_anchored_material_v2"
WORD = r"[A-ZÀ-ÖØ-ÞÆŒ][a-zà-öø-ÿæœ]+"
CAPS = r"[A-ZÀ-ÖØ-ÞÆŒ]{3,}(?![a-zà-öø-ÿæœ])"
HEAD = re.compile(rf"^(?:{CAPS}\b|{WORD}[.,]\s*(?:[IVX]+,\s*)?\d)")
NUMBER = re.compile(r"(?<![\w])\d{1,4}(?![\w])")
INTERNAL = re.compile(r"(?:\bcap[.,]|\bc\.|§)\s*\d+(?:\s*[,;]\s*(?:§\s*)?\d+)*", re.I)
SIMPLE = re.compile(
    r"^(?P<head>[^0-9]{3,300}?)[.,]\s*"
    r"(?P<citation>(?:[IVX]+,\s*)?[0-9]{1,4}"
    r"(?:\s*[,;]\s*(?:[IVX]+,\s*)?[0-9]{1,4})*)[.]?$"
)
TOME_STATEMENT = re.compile(
    r"(?<!\w)(?P<text>(?P<head>[A-ZÀ-ÖØ-ÞÆŒ][^0-9.;:!?]{4,250}?)"
    r",\s*(?P<citation>[IVX]+,\s*[0-9]{1,4}(?:\s*[,;]\s*[0-9]{1,4})*)\.)"
)


def normalize_offsets(text):
    """Collapse whitespace/line hyphens, retaining offsets into the original XML."""
    removed = set()
    for match in re.finditer(r"(?<=[^\W\d_])[-‐]\s*\n\s*(?=[^\W\d_])", text):
        removed.update(range(match.start(), match.end()))
    chars, offsets = [], []
    for pos, char in enumerate(text):
        if pos in removed:
            continue
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                offsets.append(pos)
        else:
            chars.append(char)
            offsets.append(pos)
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


def read_source(path):
    path = Path(path).resolve()
    content = path.read_bytes()
    raw = content.decode("utf-8")
    # Mask XML, headings and apparatus while retaining exact physical line numbers.
    visible = ["\n" if c == "\n" else " " for c in raw]
    for match in re.finditer(r'<bloco\b[^>]*tipo="texto_principal"[^>]*>(.*?)</bloco>', raw, re.S):
        visible[match.start(1):match.end(1)] = match[1]
    body = "".join(visible)
    normalized, offsets = normalize_offsets(body)
    return {"path": str(path), "raw": raw, "body": body, "normalized": normalized,
            "offsets": offsets, "sha256": hashlib.sha256(content).hexdigest()}


def locate_entry(entry, source):
    text = normalize_offsets(entry["entry_raw"])[0]
    body = source["normalized"]
    if text and body.count(text) == 1:
        start = body.index(text)
        return start, start + len(text)
    if len(text) < 128:
        return None
    # Distinct prefix AND suffix anchors prevent a partial match from swallowing
    # adjacent entries. Interior differences are recorded, never silently accepted.
    for size in (64, 48, 32):
        prefix, suffix = text[:size], text[-size:]
        if body.count(prefix) != 1 or body.count(suffix) != 1:
            continue
        start, end = body.index(prefix), body.index(suffix) + size
        if 2 * size <= end - start <= 2 * len(text):
            return start, end
    return None


def isolated_entries(source, bounds):
    start, end = bounds
    first, last = source["offsets"][start], source["offsets"][end - 1] + 1
    region = source["body"][first:last]
    lines = list(re.finditer(r"[^\n]+", region))
    candidates = []
    for i, line in enumerate(lines):
        if not HEAD.match(line[0].strip()):
            continue
        for following in lines[i:i + 8]:
            if following.start() > line.start() and HEAD.match(following[0].strip()):
                break
            raw = region[line.start():following.end()]
            text = normalize_offsets(raw)[0]
            if len(text) > 500:
                break
            match = SIMPLE.fullmatch(text)
            if not match:
                continue
            head = match["head"].strip()
            if head.split()[0] in {"ET", "NON", "CUM", "QUI", "QUOD"}:
                break
            lo, hi = first + line.start(), first + following.end()
            line_start = source["raw"].count("\n", 0, lo) + 1
            line_end = source["raw"].count("\n", 0, hi) + 1
            span_text = "".join(source["raw"].splitlines(keepends=True)[line_start - 1:line_end])
            candidates.append({"lemma": head, "text": text,
                               "citation": match["citation"], "start": lo, "end": hi,
                               "line_start": line_start, "line_end": line_end,
                               "text_sha256": hashlib.sha256(span_text.encode()).hexdigest()})
            break
    return candidates


def recoverable_fragments(entry, source):
    """Recover complete, uniquely shared fragments when the legacy row overruns its source."""
    legacy = normalize_offsets(entry["entry_raw"])[0]
    body = source["normalized"]
    if not body:
        return []
    candidates = isolated_entries(source, (0, len(body)))
    # Explicit tome references delimit self-contained statements even when OCR
    # flattened several printed entries onto one line. Never inherit a tome here.
    for match in TOME_STATEMENT.finditer(body):
        text = match["text"]
        lo = source["offsets"][match.start("text")]
        line_prefix = source["body"][source["body"].rfind("\n", 0, lo) + 1:lo]
        prefix = body[:match.start("text")].rstrip()
        if line_prefix.strip() and prefix and not prefix.endswith("."):
            continue
        hi = source["offsets"][match.end("text") - 1] + 1
        line_start = source["raw"].count("\n", 0, lo) + 1
        line_end = source["raw"].count("\n", 0, hi) + 1
        span_text = "".join(source["raw"].splitlines(keepends=True)[line_start - 1:line_end])
        candidates.append({"lemma": match["head"].strip(),
                           "text": text, "citation": match["citation"], "start": lo, "end": hi,
                           "entry_kind": "concordance_item", "hierarchy_status": "unverified",
                           "line_start": line_start, "line_end": line_end,
                           "text_sha256": hashlib.sha256(span_text.encode()).hexdigest()})
    unique = {}
    for candidate in candidates:
        text = candidate["text"]
        if legacy.count(text) == 1 and body.count(text) == 1:
            unique.setdefault((candidate["start"], candidate["end"]), candidate)
    # Different overlapping parses are uncertain; do not publish either one.
    values = list(unique.values())
    return [c for c in values if not any(
        c is not other and c["start"] < other["end"] and other["start"] < c["end"]
        for other in values)]


def number_occurrences(text, number):
    return [m for m in NUMBER.finditer(text) if int(m[0]) == number]


def suspect_group(entry):
    # A single uppercase lemma is normal. Multiple explicit heads following
    # citations establish that a legacy row contains more than one entry.
    pattern = rf"(?<!\w)(?:{CAPS}\b|{WORD}[.,]\s*\d)"
    return (len(list(re.finditer(pattern, entry["entry_raw"]))) >= 2
            or len(list(TOME_STATEMENT.finditer(normalize_offsets(entry["entry_raw"])[0]))) >= 2)


def namespace_for(raw):
    if re.search(r"paginas\s+edit\.\s*Veron", raw, re.I):
        return "verona_edition_and_tome"
    if re.search(r"numeros\s+crassiores\s+textui\s+intermistos", raw, re.I):
        return "embedded_editorial_numbers"
    return None


def propose(entry, refs, source, namespace=None):
    if any(r["locator_status"] not in {"unresolved", "unverified"}
           or r["target_file"] or r["resolved_target_ocr_file"]
           or r["scripture_ref_order"] is not None for r in refs):
        return None
    bounds = locate_entry(entry, source)
    grouped = suspect_group(entry)
    decisions = []
    segment = source["normalized"][slice(*bounds)] if bounds else ""
    candidates = isolated_entries(source, bounds) if bounds and grouped else []
    if grouped and not bounds:
        candidates = recoverable_fragments(entry, source)
    internal_spans = [(m.start(), m.end()) for m in INTERNAL.finditer(segment)]
    for ref in refs:
        decision = {"ref_id": ref["ref_id"]}
        number = ref["cited_editorial_page_start_number"] or ref["page_ref_int"]
        occurrences = number_occurrences(segment, number) if number else []
        if occurrences and all(any(a <= m.start() < b for a, b in internal_spans)
                               for m in occurrences):
            decision.update(action="internal_reference", block_reason="internal_work_locator")
        elif grouped and bounds and occurrences and ref["ref_kind"] == "editorial_page":
            # A deduplicated legacy ref can have many occurrences. Reassign only
            # when EVERY occurrence belongs to the same complete source entry.
            viable = [c for c in candidates if all(
                c["start"] <= source["offsets"][bounds[0] + m.start()] < c["end"]
                for m in occurrences)]
            explicit_tome = re.match(r"([IVX]+),", ref["ref_raw"].strip())
            if explicit_tome:
                viable = [c for c in viable if c["citation"].startswith(explicit_tome[1] + ",")]
            if len(viable) == 1:
                c = viable[0]
                decision.update(action="reassign", candidate=c)
                if re.match(r"[IVX]+,", c["citation"]):
                    decision.update(namespace=namespace or "explicit_tome",
                                    block_reason="editorial_namespace_requires_mapping")
            else:
                decision.update(action="hold", block_reason="grouped_entry_requires_segmentation")
        elif grouped:
            decision.update(action="hold", block_reason="grouped_entry_requires_segmentation"
                            if bounds else "source_anchor_not_unique")
        if namespace:
            decision.setdefault("action", "hold")
            decision["namespace"] = namespace
            decision.setdefault("block_reason", "editorial_namespace_requires_mapping")
        if "action" in decision:
            decisions.append(decision)
    # Some legacy extractors deduplicated numbers across different lemmas. A
    # citation explicitly present in an isolated OCR entry can still be recovered
    # while the ambiguous legacy association stays quarantined for review.
    assigned = {(d["candidate"]["start"], next(r for r in refs if r["ref_id"] == d["ref_id"])["page_ref_int"])
                for d in decisions if d["action"] == "reassign"}
    for candidate in candidates:
        tome = None
        for token in re.finditer(r"(?:(?P<tome>[IVX]+),\s*)?(?P<number>\d{1,4})", candidate["citation"]):
            tome = token["tome"] or tome
            number = int(token["number"])
            key = (candidate["start"], number)
            if key in assigned or number < 1:
                continue
            assigned.add(key)
            decision = {"ref_id": None, "action": "recover", "candidate": candidate,
                        "ref_raw": token[0], "number": number, "tome": tome}
            if namespace or tome:
                decision.update(namespace=namespace or "explicit_tome",
                                block_reason="editorial_namespace_requires_mapping")
            decisions.append(decision)
    if not decisions:
        return None
    return {"entry_key": entry["entry_key"], "source_file": source["path"],
            "source_sha256": source["sha256"], "source_bounds": bounds,
            "recovery_scope": "bounded_entry" if bounds else "exact_shared_fragments_only",
            "source_differs_from_legacy": bool(bounds and segment != normalize_offsets(entry["entry_raw"])[0]),
            "decisions": decisions}


def update_row(con, table, key, value, changes):
    # All table/column names are internal schema identifiers, never CLI input.
    con.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in changes)
                + f" WHERE {key}=?", [*changes.values(), value])


def insert_row(con, table, row):
    con.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                list(row.values()))


def apply_proposal(con, volume, entry, refs, proposal, next_orders, method=METHOD):
    evidence = [dict(r) for r in con.execute("""SELECT ev.* FROM alphabetical_locator_evidence ev
        JOIN alphabetical_refs r USING(ref_id) WHERE r.entry_key=?""", (entry["entry_key"],))]
    insert_row(con, "alphabetical_deterministic_repairs", {
        "entry_key": entry["entry_key"], "method": method,
        "original_json": json.dumps({"entry": entry, "refs": refs, "locator_evidence": evidence}, ensure_ascii=False),
        "proposal_json": json.dumps(proposal, ensure_ascii=False)})
    by_id = {r["ref_id"]: r for r in refs}
    children = {}
    for decision in proposal["decisions"]:
        ref = by_id.get(decision["ref_id"])
        raw = json.loads(ref["raw_json"]) if ref else {}
        raw["deterministic_repair"] = {"method": method, **decision,
            "original_entry_key": entry["entry_key"], "source_file": proposal["source_file"],
            "source_sha256": proposal["source_sha256"]}
        changes = {"raw_json": json.dumps(raw, ensure_ascii=False),
                   "target_file_probability": None, "target_ocr_file_candidate_score": None}
        if decision["action"] == "internal_reference":
            changes.update(ref_kind="target_locator", cited_location_parse_status="not_applicable")
            for key in ref:
                if key.startswith("cited_editorial_") or key in {
                    "page_ref_raw", "page_ref_int", "page_ref_col", "line_ref_raw",
                    "range_start_raw", "range_end_raw"}:
                    changes[key] = None
        if decision["action"] in {"reassign", "recover"}:
            c = decision["candidate"]
            child_key = entry["entry_key"] + ":repair:" + hashlib.sha256(
                f"{proposal['source_file']}:{c['start']}:{c['end']}".encode()).hexdigest()[:12]
            if child_key not in children:
                child = dict(entry)
                next_orders[entry["section_key"]] += 1
                child.update(entry_key=child_key, entry_order=next_orders[entry["section_key"]],
                             entry_kind=c.get("entry_kind", entry["entry_kind"]),
                             lemma_raw=c["lemma"], lemma_display=c["lemma"],
                             lemma_norm=normalize_for_search(c["lemma"]),
                             lemma_sort=normalize_for_search(c["lemma"]),
                             entry_raw=c["text"], context_raw=c["text"],
                             raw_json=json.dumps({"deterministic_repair": raw["deterministic_repair"]}, ensure_ascii=False),
                             target_file_best=None, resolved_target_ocr_file=None,
                             inferred_printed_page=None, index_editorial_page_estimate=None,
                             index_entry_source_ocr_file=proposal["source_file"],
                             editorial_anchor_file=proposal["source_file"], confidence=None)
                insert_row(con, "alphabetical_entries", child)
                source_text_hash = c["text_sha256"]
                con.execute("""INSERT OR IGNORE INTO alphabetical_source_spans
                    (volume_id,ocr_file_path,line_start,line_end,block_type,text_sha256,created_at)
                    VALUES (?,?,?,?,?,?,?)""", (volume, proposal["source_file"], c["line_start"], c["line_end"],
                    "texto_principal", source_text_hash, datetime.now(timezone.utc).isoformat()))
                span = con.execute("""SELECT span_id FROM alphabetical_source_spans
                    WHERE volume_id=? AND ocr_file_path=? AND line_start=? AND line_end=?
                    AND block_type='texto_principal' AND text_sha256=?""", (
                    volume, proposal["source_file"], c["line_start"], c["line_end"], source_text_hash)).fetchone()[0]
                con.execute("INSERT INTO alphabetical_entry_source_spans(entry_key,span_id) VALUES (?,?)",
                            (child_key, span))
                children[child_key] = 0
            children[child_key] += 1
            changes.update(entry_key=child_key, ref_order=children[child_key])
        if ref:
            update_row(con, "alphabetical_refs", "ref_id", ref["ref_id"], changes)
            con.execute("DELETE FROM alphabetical_locator_evidence WHERE ref_id=?", (ref["ref_id"],))
        else:
            number = decision["number"]
            insert_row(con, "alphabetical_refs", {**changes,
                "ref_kind": "editorial_page", "ref_raw": decision["ref_raw"],
                "page_ref_raw": str(number), "page_ref_int": number,
                "cited_editorial_page_start_raw": str(number), "cited_editorial_page_start_number": number,
                "cited_location_parse_status": "parsed", "locator_status": "unresolved"})
    raw = json.loads(entry["raw_json"])
    raw["deterministic_repair_summary"] = {
        "method": method, "state": "legacy_source_retained_for_review",
        "new_entry_keys": list(children), "source_file": proposal["source_file"],
        "source_sha256": proposal["source_sha256"]}
    update_row(con, "alphabetical_entries", "entry_key", entry["entry_key"],
               {"target_file_best": None, "resolved_target_ocr_file": None,
                "raw_json": json.dumps(raw, ensure_ascii=False)})
    return len(children)


def reorder_section(con, section):
    ordered = [dict(r) for r in con.execute(
        "SELECT * FROM alphabetical_entries WHERE section_key=? ORDER BY entry_order", (section,))]
    positions = {e["entry_key"]: e["entry_order"] for e in ordered}

    def reading_order(e):
        metadata = json.loads(e["raw_json"]).get("deterministic_repair") or {}
        parent = metadata.get("original_entry_key")
        return (positions.get(parent, e["entry_order"]),
                metadata.get("candidate", {}).get("start", -1))

    ordered.sort(key=reading_order)
    offset = max(positions.values()) + len(ordered) + 1
    con.execute("UPDATE alphabetical_entries SET entry_order=entry_order+? WHERE section_key=?",
                (offset, section))
    for order, e in enumerate(ordered, 1):
        con.execute("UPDATE alphabetical_entries SET entry_order=? WHERE entry_key=?", (order, e["entry_key"]))


def repair(db, volumes, output_db, report_path, entry_keys=()):
    db, output_db = Path(db).resolve(), Path(output_db).resolve()
    if output_db.exists() or db == output_db:
        raise ValueError("output-db must be a NEW path; the input DB is never overwritten")
    if Path(report_path).resolve() in {db, output_db}:
        raise ValueError("Report and database paths must differ")
    output_db.parent.mkdir(parents=True, exist_ok=True)
    counts, proposals, skipped = Counter(), [], Counter()
    with tempfile.TemporaryDirectory(prefix="alpha-repair-", dir=output_db.parent) as temporary:
        staged = Path(temporary) / "review.sqlite"
        with closing(connect_readonly(db)) as source, closing(sqlite3.connect(staged)) as dest:
            source.backup(dest)
        with closing(sqlite3.connect(staged)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("""CREATE TABLE IF NOT EXISTS alphabetical_deterministic_repairs
                (entry_key TEXT PRIMARY KEY, method TEXT NOT NULL, original_json TEXT NOT NULL,
                 proposal_json TEXT NOT NULL)""")
            con.execute("BEGIN IMMEDIATE")
            cache = {}
            selected_entry_keys = set()
            next_orders = dict(con.execute("SELECT section_key,MAX(entry_order) FROM alphabetical_entries GROUP BY section_key"))
            for volume in sorted(set(volumes)):
                if not con.execute("SELECT 1 FROM alphabetical_volumes WHERE volume_id=?", (volume,)).fetchone():
                    raise ValueError(f"Unknown volume: {volume}")
                entries = [dict(r) for r in con.execute("""SELECT e.* FROM alphabetical_entries e
                    JOIN alphabetical_sections s USING(section_key) WHERE s.volume_id=?
                    AND s.pipeline_owner='alphabetical' AND s.alphabetical_role='owned_section'
                    AND s.scripture_mode='none' ORDER BY s.section_order,e.entry_order""", (volume,))]
                if entry_keys:
                    entries = [entry for entry in entries if entry["entry_key"] in entry_keys]
                selected_entry_keys.update(entry["entry_key"] for entry in entries)
                for entry in entries:
                    if con.execute("SELECT 1 FROM alphabetical_deterministic_repairs WHERE entry_key=?",
                                   (entry["entry_key"],)).fetchone() or ":repair:" in entry["entry_key"]:
                        skipped["already_repaired"] += 1
                        continue
                    refs = [dict(r) for r in con.execute("SELECT * FROM alphabetical_refs WHERE entry_key=? ORDER BY ref_order",
                                                       (entry["entry_key"],))]
                    if not refs:
                        continue
                    path = entry["index_entry_source_ocr_file"] or entry["editorial_anchor_file"]
                    if not path or not Path(path).is_file():
                        skipped["missing_source"] += 1
                        continue
                    if path not in cache:
                        cache[path] = read_source(path)
                    start = entry["index_section_start_ocr_file"] or entry["section_start_file"]
                    if start and Path(start).is_file() and start not in cache:
                        cache[start] = read_source(start)
                    namespace = namespace_for(cache[start]["raw"]) if start in cache else None
                    proposal = propose(entry, refs, cache[path], namespace)
                    if not proposal:
                        continue
                    counts["new_entries"] += apply_proposal(con, volume, entry, refs, proposal, next_orders)
                    for decision in proposal["decisions"]:
                        counts[decision["action"]] += 1
                        if decision.get("block_reason"):
                            counts["blocked_references"] += 1
                    proposals.append(proposal)
                affected = {p["entry_key"] for p in proposals}
                sections = {e["section_key"] for e in entries if e["entry_key"] in affected}
                for section in sections:
                    reorder_section(con, section)
                refresh_volume_quality(con, volume)
            if set(entry_keys) - selected_entry_keys:
                raise ValueError("Requested entry keys are missing or outside the eligible volume sections")
            if list(con.execute("PRAGMA foreign_key_check")):
                raise ValueError("Foreign key validation failed")
            for source in cache.values():
                if hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest() != source["sha256"]:
                    raise ValueError("OCR changed during repair")
            con.commit()
        # Exclusive hard link prevents a racing process from overwriting an output.
        output_db.hardlink_to(staged)
    report = {"version": VERSION, "method": METHOD, "input_db": str(db), "output_db": str(output_db),
              "volumes": sorted(set(volumes)), "entry_keys": sorted(set(entry_keys)),
              "counts": dict(counts), "skipped": dict(skipped),
              "proposals": proposals}
    write_json(report_path, report)
    return report


def apply_reviewed(db, report_paths, backup_path, audit_path):
    """Apply exact reviewed proposals, guarding against changed rows and OCR."""
    db, backup_path, audit_path = map(lambda p: Path(p).resolve(), (db, backup_path, audit_path))
    if len({db, backup_path, audit_path}) != 3 or backup_path.exists() or audit_path.exists():
        raise ValueError("Use distinct NEW backup and audit paths")
    selected, report_hashes = {}, []
    for path in report_paths:
        path = Path(path).resolve()
        report = json.loads(path.read_text())
        if report["method"] not in {"source_anchored_material_v1", METHOD}:
            raise ValueError("Unsupported reviewed repair method")
        with closing(connect_readonly(report["output_db"])) as review:
            for proposal in report["proposals"]:
                archive = review.execute("SELECT * FROM alphabetical_deterministic_repairs WHERE entry_key=?",
                                         (proposal["entry_key"],)).fetchone()
                if not archive or json.loads(archive["proposal_json"]) != proposal:
                    raise ValueError("Report does not match the reviewed database")
                selected[proposal["entry_key"]] = (proposal, json.loads(archive["original_json"]), report["method"])
        report_hashes.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    counts, skipped, sections, volumes = Counter(), 0, set(), set()
    with closing(sqlite3.connect(db)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        has_archive = con.execute("SELECT 1 FROM sqlite_master WHERE name='alphabetical_deterministic_repairs'").fetchone()
        pending = []
        for key, (proposal, original, method) in selected.items():
            existing = con.execute("SELECT proposal_json FROM alphabetical_deterministic_repairs WHERE entry_key=?", (key,)).fetchone() if has_archive else None
            if existing:
                if json.loads(existing[0]) != proposal:
                    raise ValueError("Entry already has a different repair; review required")
                skipped += 1
                continue
            entry = con.execute("SELECT * FROM alphabetical_entries WHERE entry_key=?", (key,)).fetchone()
            refs = [dict(r) for r in con.execute("SELECT * FROM alphabetical_refs WHERE entry_key=? ORDER BY ref_order", (key,))]
            evidence = [dict(r) for r in con.execute("""SELECT ev.* FROM alphabetical_locator_evidence ev
                JOIN alphabetical_refs r USING(ref_id) WHERE r.entry_key=?""", (key,))]
            if not entry or dict(entry) != original["entry"] or refs != original["refs"] or evidence != original["locator_evidence"]:
                raise ValueError(f"Canonical rows changed since review: {key}")
            if any(r["locator_status"] == "resolved" or r["target_file"] or r["resolved_target_ocr_file"] for r in refs):
                raise ValueError("Refusing to rewrite resolved references")
            if hashlib.sha256(Path(proposal["source_file"]).read_bytes()).hexdigest() != proposal["source_sha256"]:
                raise ValueError("OCR changed since review")
            volume = con.execute("SELECT volume_id FROM alphabetical_sections WHERE section_key=?", (entry["section_key"],)).fetchone()[0]
            pending.append((volume, dict(entry), refs, proposal, method))
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("xb"):
            pass
        # The write reservation excludes concurrent writers, while a second
        # read-only connection can back up the exact state before any mutation.
        with closing(connect_readonly(db)) as source, closing(sqlite3.connect(backup_path)) as backup:
            source.backup(backup)
        con.execute("""CREATE TABLE IF NOT EXISTS alphabetical_deterministic_repairs
            (entry_key TEXT PRIMARY KEY, method TEXT NOT NULL, original_json TEXT NOT NULL,
             proposal_json TEXT NOT NULL)""")
        next_orders = dict(con.execute("SELECT section_key,MAX(entry_order) FROM alphabetical_entries GROUP BY section_key"))
        for volume, entry, refs, proposal, method in pending:
            counts["new_entries"] += apply_proposal(con, volume, entry, refs, proposal, next_orders, method)
            for decision in proposal["decisions"]:
                counts[decision["action"]] += 1
                counts["blocked_references"] += bool(decision.get("block_reason"))
            sections.add(entry["section_key"])
            volumes.add(volume)
        for section in sections:
            reorder_section(con, section)
        for volume in volumes:
            refresh_volume_quality(con, volume)
        if list(con.execute("PRAGMA foreign_key_check")) or con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database validation failed")
        for _, _, _, proposal, _ in pending:
            if hashlib.sha256(Path(proposal["source_file"]).read_bytes()).hexdigest() != proposal["source_sha256"]:
                raise ValueError("OCR changed during application")
        con.commit()
    result = {"db": str(db), "backup": str(backup_path), "reviewed_reports": report_hashes,
              "applied_entries": len(pending), "already_applied": skipped, "counts": dict(counts),
              "volumes": sorted(volumes), "integrity": "ok", "foreign_keys": "ok"}
    write_json(audit_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--volume", action="append")
    parser.add_argument("--apply-reviewed", type=Path, action="append")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--entry-key", action="append", default=[],
                        help="Restrict repair to these legacy entries; repeat for a small pilot")
    parser.add_argument("--output-db", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.apply_reviewed:
        if not args.backup or args.output_db or args.volume or args.entry_key:
            parser.error("--apply-reviewed requires --backup and excludes volume/output-db/entry-key")
        print(json.dumps(apply_reviewed(args.db, args.apply_reviewed, args.backup, args.report)))
        return
    if not args.volume or not args.output_db:
        parser.error("Normal review mode requires --volume and --output-db")
    report = repair(args.db, args.volume, args.output_db, args.report, args.entry_key)
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()

"""Rebuild index-source section relationships from current OCR, mechanically.

The historical region inventory selects pages only. Its old spans and hashes
never establish membership. Current region reconstruction supplies exact spans;
running headings and ambiguous page-only matches remain explicit alternatives.
"""

import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools.corpus_utils import page_files, page_number
from tools.indexing import editorial_regions_experiment as regions
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.search_catalog_consolidation import connect, stats

VERSION = "regional-section-provenance-v1"
UNKNOWN = {"unknown", "index_unspecified", "unclassified", "context"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ranges(numbers):
    result = []
    for n in sorted(numbers):
        if result and n == result[-1][1] + 1:
            result[-1][1] = n
        else:
            result.append([n, n])
    return result


def select_pages(catalog, inventory, context=2):
    """Freeze a small replayable selection, independent of old region offsets."""
    wanted, selected = defaultdict(set), defaultdict(set)
    with closing(readonly(catalog)) as db:
        for volume, source in db.execute("SELECT DISTINCT volume,source FROM items"):
            physical = page_number(Path(source))
            if physical is not None:
                wanted[volume].add(physical)
    if inventory:
        for line in inventory.open():
            section = json.loads(line)
            pages = {s["physical"] for s in section["spans"]}
            if pages & wanted.get(section["volume"], set()):
                selected[section["volume"]].update(pages)
    for volume, pages in wanted.items():
        for p in pages:
            selected[volume].update(range(max(1, p - context), p + context + 1))
    return {
        "version": VERSION,
        "selection_basis": "catalog_source_pages_plus_intersecting_historical_regions_and_context",
        "context_pages": context,
        "volumes": {v: ranges(ps) for v, ps in sorted(selected.items())},
    }


def classify_title(title, fallback="index_unspecified"):
    """Classify only a heading already identified as editorial evidence.

    Names of saints/martyrs denote indexed material, not an inferred identity for
    individual entries. A homily catalog and a thematic index to homilies differ.
    """
    text = regions.fold(title)
    text = re.sub(r"^\d+\s+|\s+\d+$", "", text).strip(" .")
    text = re.sub(r"^INDICES\b", "INDEX", text)
    material = None
    for pattern, value in [
        (r"MARTYR|MARTIR", "martyrs"),
        (r"SANCTOR|SAINTS", "saints"),
        (r"HOMILI", "homilies"),
        (r"SERMON", "sermons"),
        (r"EPISTOL|LETTRES", "letters"),
        (r"SCRIPTUR|BIBLIQUE", "scripture"),
    ]:
        if re.search(pattern, text):
            material = value
            break
    if re.search(r"AUCTORUM ET OPERUM", text) and re.search(r"PERSONARUM|RERUM", text):
        kind = "mixed_index"
    elif re.search(r"RERUM (?:ET|AC) NOMINUM|NOMINUM (?:ET|AC) RERUM", text):
        kind = "subject_and_name_index"
    elif re.search(r"AUCTORUM ET OPERUM", text):
        kind = "author_and_work_index"
    elif re.search(
        r"\bINITIA(?:LIS)?\b|\bINITIORUM\b|INCIPIT|LITTERAM INITIALEM", text
    ):
        kind = "incipit_index"
    elif re.search(
        r"(?:INDEX|TABULA|TABLE|CATALOGUS).*?(?:NOMIN|NOMS|PERSONARUM|SANCTORUM|MARTYRUM)",
        text,
    ):
        kind = "onomastic_index"
    elif re.search(
        r"INDEX.*(?:BIBLIC|SCRIPTUR|LOCORUM SACR)|TABLE.*(?:CITATIONS|PERICOPES)", text
    ):
        kind = "scripture_index"
    elif re.search(
        r"INDEX.*RERUM (?:ET|AC) VERBORUM|INDEX.*VERBORUM (?:ET|AC) RERUM", text
    ):
        kind = "subject_and_word_index"
    elif re.search(r"INDEX.*(?:RERUM|ANALYTIC|SENTENTIARUM)", text):
        kind = "thematic_index"
    elif re.search(
        r"INDEX.*(?:VERBORUM|VOCABUL|GRAECITATIS|GRAMMATIC)|^TABLE DES (?:MOTS|EXPRESSIONS)\b",
        text,
    ):
        kind = "lexical_index"
    elif re.search(r"INDEX.*(?:AUCTORUM|POETARUM|SCRIPTORUM)|ΠΙΝΑΞ.*ΣΥΓΓΡΑΦΕΩΝ", text):
        kind = "author_index"
    elif re.search(r"INDEX.*GEOGRAPHIC", text):
        kind = "geographic_index"
    elif re.search(r"^(?:ELENCHUS|CATALOGUS).*(?:HOMILI|SERMON|EPISTOL|OPERUM)", text):
        kind = "work_catalog"
    elif re.search(r"^INDEX.*(?:HOMILI|SERMON|EPISTOL|OPERUM)", text):
        # INDEX OPERUM can be an alphabetical subject index *to* those works
        # (PL065/496); INDEX IN ... HOMILIAS is not a list of homilies (PL076/754).
        # Preserve the indexed material without inventing the list's organization.
        kind = "index_unspecified"
    else:
        kind = fallback or "unknown"
    return kind, material


def title_at(raw, rows, event):
    """Extend a heading only through adjacent uppercase lines of the same block."""
    a, b = event["span"]
    following = [r for r in rows if r["block"] == event["block"] and r["span"][0] >= a]
    for row in following[1:5]:
        text = regions.fold(row["quote"])
        if (
            not regions.is_typographic_title(row["quote"])
            or len(text) < 4
            or re.search(r"\d\s*[.,;]?$", text)
            or row["span"][1] - a > 700
        ):
            break
        # A separate index or catalog heading is a boundary, not a subtitle.
        if regions.material_kind(text) and re.match(r"INDEX|TABLE|ORDO|FINIS", text):
            break
        b = row["span"][1]
    return {"title_raw": raw[a:b], "title_span": [a, b]}


def locate_span(item, raw):
    span = item["span"]
    ref = item["ref_raw"]
    if (
        isinstance(span, list)
        and len(span) == 2
        and all(isinstance(n, int) for n in span)
        and 0 <= span[0] < span[1] <= len(raw)
        and raw[span[0] : span[1]].strip() == ref.strip()
    ):
        return span, "literal_reference_at_stored_span"
    if ref.strip():
        needle = ref.strip()
        pattern = (
            (r"(?<!\w)" if needle[0].isalnum() else "")
            + re.escape(needle)
            + (r"(?!\w)" if needle[-1].isalnum() else "")
        )
        matches = list(re.finditer(pattern, raw))
        if len(matches) == 1:
            return list(matches[0].span()), "unique_literal_reference_relocated"
    return None, "reference_span_unresolved_page_candidates_only"


def assign(item, raw, record, sections, running):
    span, method = locate_span(item, raw)
    overlaps = defaultdict(list)
    for region in record["regions"]:
        sid = region["section_id"]
        if sid and (
            span is None
            or max(span[0], region["span"][0]) < min(span[1], region["span"][1])
        ):
            overlaps[sid].append(region["span"])
    fallback = not overlaps
    if fallback:
        # Header-only candidates stay local to this page. ORDO RERUM must not
        # relabel an index continuation preceding an internal contents boundary.
        for sid in running:
            overlaps[sid] = []
    result = []
    for sid, intervals in sorted(overlaps.items()):
        strength = (
            "page_heading_candidate"
            if fallback
            else "page_region_candidate" if span is None else "reference_region_overlap"
        )
        result.append(
            {
                "item_id": item["id"],
                "section_id": sid,
                "assignment_kind": strength,
                "evidence": {
                    "version": VERSION,
                    "source": record["source"],
                    "source_sha256": record["source_sha256"],
                    "stored_span": item["span"],
                    "current_reference_span": span,
                    "alignment": method,
                    "region_spans": intervals,
                    "alternatives": len(overlaps),
                    "ambiguous": len(overlaps) > 1,
                    "source_changed": item["source_sha256"] != record["source_sha256"],
                    "classification_certified": False,
                },
            }
        )
    return result, method


def process_volume(job):
    volume, selected, root, items = job
    inventory = page_files(Path(root) / volume / "text", suffixes=[".txt"])
    by_page = defaultdict(list)
    for item in items:
        by_page[page_number(Path(item["source"]))].append(item)
    sections, assignments, outcomes, pages = {}, [], [], []
    state = None
    page_context = {}
    for physical in sorted({p for a, b in selected for p in range(a, b + 1)}):
        paths = inventory.get(physical, [])
        if len(paths) != 1:
            state = None
            pages.append(
                {
                    "physical": physical,
                    "status": "missing_or_ambiguous_file",
                    "files": list(map(str, paths)),
                }
            )
            for item in by_page[physical]:
                outcomes.append(
                    {"item_id": item["id"], "reason": "missing_or_ambiguous_file"}
                )
            continue
        path = paths[0].resolve()
        try:
            raw = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            state = None
            pages.append(
                {"physical": physical, "status": "read_error", "error": str(exc)}
            )
            for item in by_page[physical]:
                outcomes.append({"item_id": item["id"], "reason": "read_error"})
            continue
        facts = regions.local_facts(raw)
        # Refine recognized headings, while keeping the detector's boundary roles.
        for event in facts["events"]:
            if event["role"] in {"section_start", "running_header"}:
                title = title_at(raw, facts["rows"], event)
                event["kind"] = classify_title(title["title_raw"], event["kind"])[0]
        record, state = regions.analyze_page(
            raw, str(path), volume, physical, state, facts
        )
        running = []
        for event in record["signals"]:
            if event["role"] not in {"section_start", "running_header"}:
                continue
            title = title_at(raw, facts["rows"], event)
            kind, material = classify_title(title["title_raw"], event["kind"])
            if event["role"] == "running_header":
                if kind not in {
                    "onomastic_index",
                    "thematic_index",
                    "lexical_index",
                    "author_index",
                    "scripture_index",
                    "incipit_index",
                    "subject_and_word_index",
                    "index_unspecified",
                    "subject_and_name_index",
                    "author_and_work_index",
                    "mixed_index",
                    "geographic_index",
                    "work_catalog",
                }:
                    continue
                sid = "heading:" + sha(encoded([volume, str(path), event["span"]]))[:24]
                running.append(sid)
            else:
                sid = next(
                    (
                        r["section_id"]
                        for r in record["regions"]
                        if r["span"] == event["span"]
                    ),
                    None,
                )
                if not sid:
                    continue
            sections[sid] = {
                "section_id": sid,
                "volume": volume,
                "index_type": kind,
                "indexed_material": material,
                **title,
                "source": str(path),
                "evidence": {
                    "source_sha256": record["source_sha256"],
                    **title,
                    "opening_role": event["role"],
                    "physical": physical,
                    "method": "current_OCR_editorial_region",
                    "boundary_status": "candidate",
                    "namespace_declarations": record["declarations"],
                },
            }
        pages.append(
            {
                "physical": physical,
                "source": str(path),
                "source_sha256": record["source_sha256"],
                "status": "read",
                "transitions": record["transitions"],
                "sections": sorted(
                    {r["section_id"] for r in record["regions"] if r["section_id"]}
                ),
            }
        )
        for item in by_page[physical]:
            found, alignment = assign(item, raw, record, sections, running)
            assignments.extend(found)
            outcomes.append(
                {
                    "item_id": item["id"],
                    "reason": "assigned" if found else "no_section_evidence",
                    "alignment": alignment,
                    "alternatives": len(found),
                }
            )
        page_context[physical] = (raw, facts, running, str(path))
    bracket_running_headers(by_page, page_context, sections, assignments, outcomes)
    seen = {o["item_id"] for o in outcomes}
    outcomes.extend(
        {"item_id": i["id"], "reason": "outside_frozen_selection"}
        for i in items
        if i["id"] not in seen
    )
    return {
        "volume": volume,
        "sections": sections,
        "assignments": assignments,
        "outcomes": outcomes,
        "pages": pages,
    }


def header_key(title):
    """Compare printed running titles independent of page numbers/known markup."""
    return re.sub(r"^\d+\s+|\s+\d+$", "", regions.fold(title)).strip(" .")


def bracket_running_headers(by_page, pages, sections, assignments, outcomes):
    """Infer a one-page candidate only from two matching adjacent running heads.

    Alternating titles such as INDEX VOCABULORUM / INDICES AD LIBROS ... do
    not establish an unlimited inherited section. Both flanks must be present,
    with the same specific index title, and the middle must contain list rows.
    A new internal title or conflicting specific heading prohibits the bridge.
    """
    linked = defaultdict(list)
    for a in assignments:
        linked[a["item_id"]].append(a)
    outcomes_by_id = {o["item_id"]: o for o in outcomes}
    for physical, items in sorted(by_page.items()):
        if not all(p in pages for p in (physical - 1, physical, physical + 1)):
            continue
        raw, facts, _, source = pages[physical]
        left, right = pages[physical - 1], pages[physical + 1]

        def heads(page):
            return {
                (header_key(sections[s]["title_raw"]), sections[s]["index_type"]): s
                for s in page[2]
                if sections[s]["index_type"].endswith("_index")
            }

        lh, rh = heads(left), heads(right)
        common = sorted(lh.keys() & rh.keys())
        if len(common) != 1:
            continue
        key = common[0]
        kind = key[1]
        blockers = []
        for event in facts["events"]:
            if event["role"] in {"section_end", "work_opening"}:
                blockers.append(event)
            elif event["role"] in {"section_start", "running_header"}:
                generic_top = (
                    event["kind"] in UNKNOWN
                    and event["block_type"] == "cabecalho"
                    and not any(
                        r["block_type"] == "texto_principal"
                        and r["span"][0] < event["span"][0]
                        for r in facts["rows"]
                    )
                )
                if not generic_top and (
                    event["role"] == "section_start" or event["kind"] != kind
                ):
                    blockers.append(event)
        if blockers:
            continue
        # Ignore the generic top heading here; it is not a new internal section.
        support = regions.continuation_evidence(facts["rows"], [], {"kind": kind})
        if not support:
            continue
        body = [
            r["span"] for r in facts["rows"] if r["block_type"] == "texto_principal"
        ]
        for item in items:
            existing = linked[item["id"]]
            if any(
                sections[a["section_id"]]["index_type"] not in UNKNOWN for a in existing
            ):
                continue
            span, alignment = locate_span(item, raw)
            if span is None or not any(
                max(span[0], a) < min(span[1], b) for a, b in body
            ):
                continue
            a = {
                "item_id": item["id"],
                "section_id": lh[key],
                "assignment_kind": "bracketed_running_headers_candidate",
                "evidence": {
                    "version": VERSION,
                    "source": source,
                    "source_sha256": sha(raw),
                    "stored_span": item["span"],
                    "current_reference_span": span,
                    "alignment": alignment,
                    "classification_certified": False,
                    "left_section_id": lh[key],
                    "right_section_id": rh[key],
                    "left_source": left[3],
                    "right_source": right[3],
                    "right_title": sections[rh[key]],
                    "support": support,
                    "physical_window": [physical - 1, physical, physical + 1],
                    "ambiguous": True,
                    "alternatives": len(existing) + 1,
                },
            }
            assignments.append(a)
            existing.append(a)
            outcomes_by_id[item["id"]].update(
                reason="assigned", alternatives=len(existing)
            )


def build(catalog, root, selection, out, workers=20):
    if selection.get("version") != VERSION:
        raise ValueError("Unsupported selection")
    out.mkdir(parents=True, exist_ok=False)
    (out / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n"
    )
    grouped = defaultdict(list)
    with closing(readonly(catalog)) as db:
        for row in db.execute(
            "SELECT id,volume,source,ref_raw,metadata_json FROM items ORDER BY volume,source,id"
        ):
            m = json.loads(row["metadata_json"])
            grouped[row["volume"]].append(
                {
                    "id": row["id"],
                    "source": row["source"],
                    "ref_raw": row["ref_raw"],
                    "span": m.get("source_span"),
                    "source_sha256": m.get("source_sha256"),
                }
            )
    jobs = [
        (v, selection["volumes"].get(v, []), str(root.resolve()), items)
        for v, items in sorted(grouped.items())
    ]
    counts, types = Counter(), Counter()
    logical = hashlib.sha256()
    with closing(sqlite3.connect(out / "sections.sqlite")) as db, (
        out / "pages.jsonl"
    ).open("w") as pages:
        db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE sections(section_id TEXT PRIMARY KEY,volume,index_type,indexed_material,title_raw,source,evidence_json);
            CREATE TABLE assignments(item_id,section_id,assignment_kind,evidence_json,PRIMARY KEY(item_id,section_id));
            CREATE TABLE outcomes(item_id TEXT PRIMARY KEY,reason,evidence_json);
            CREATE TABLE item_identity(item_id TEXT PRIMARY KEY,source,ref_raw);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json);
        """)
        for items in grouped.values():
            db.executemany(
                "INSERT INTO item_identity VALUES(?,?,?)",
                [(i["id"], i["source"], i["ref_raw"]) for i in items],
            )
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(process_volume, jobs):
                for s in sorted(
                    result["sections"].values(), key=lambda s: s["section_id"]
                ):
                    values = tuple(
                        s[k]
                        for k in (
                            "section_id",
                            "volume",
                            "index_type",
                            "indexed_material",
                            "title_raw",
                            "source",
                        )
                    ) + (encoded(s["evidence"]),)
                    db.execute("INSERT INTO sections VALUES(?,?,?,?,?,?,?)", values)
                    logical.update((encoded(values) + "\n").encode())
                    types[s["index_type"]] += 1
                for a in result["assignments"]:
                    values = (
                        a["item_id"],
                        a["section_id"],
                        a["assignment_kind"],
                        encoded(a["evidence"]),
                    )
                    db.execute("INSERT INTO assignments VALUES(?,?,?,?)", values)
                    logical.update((encoded(values) + "\n").encode())
                    counts[a["assignment_kind"]] += 1
                for o in result["outcomes"]:
                    db.execute(
                        "INSERT INTO outcomes VALUES(?,?,?)",
                        (o["item_id"], o["reason"], encoded(o)),
                    )
                    counts[o["reason"]] += 1
                    counts["items_with_alternatives"] += o.get("alternatives", 0) > 1
                for page in result["pages"]:
                    pages.write(encoded({"volume": result["volume"], **page}) + "\n")
                    counts["pages_" + page["status"]] += 1
                db.commit()
                print(
                    encoded(
                        {
                            "volume": result["volume"],
                            "items": len(result["outcomes"]),
                            "assigned": sum(
                                o["reason"] == "assigned" for o in result["outcomes"]
                            ),
                        }
                    ),
                    flush=True,
                )
        summary = {
            "version": VERSION,
            "catalog": str(catalog.resolve()),
            "workers": workers,
            "selection_sha256": sha(encoded(selection)),
            "logical_sha256": logical.hexdigest(),
            "algorithm_sha256": sha(Path(__file__).read_text()),
            "region_detector_fingerprint": regions.reconstruction_fingerprint(),
            "region_local_fingerprint": regions.local_fingerprint(),
            "counts": counts,
            "section_types": types,
            "total_items": sum(map(len, grouped.values())),
            "source_writes": 0,
            "inference_calls": 0,
        }
        db.execute("INSERT INTO metadata VALUES(?,?)", ("summary", encoded(summary)))
        db.commit()
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return report(out / "sections.sqlite")


def report(staged):
    """Export section-level counts and reproducible examples, not accuracy claims."""
    out = staged.parent
    with closing(sqlite3.connect(staged)) as db:
        db.row_factory = sqlite3.Row
        summary = json.loads(
            db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0]
        )
        unknown = ",".join("?" for _ in UNKNOWN)
        summary["items_with_specific_type_candidates"] = db.execute(
            f"""SELECT count(DISTINCT a.item_id)
            FROM assignments a JOIN sections s USING(section_id) WHERE s.index_type NOT IN ({unknown})""",
            sorted(UNKNOWN),
        ).fetchone()[0]
        summary["linked_section_count"] = db.execute(
            "SELECT count(DISTINCT section_id) FROM assignments"
        ).fetchone()[0]
        summary["volumes_with_assignments"] = db.execute(
            "SELECT count(DISTINCT s.volume) FROM assignments a JOIN sections s USING(section_id)"
        ).fetchone()[0]
        orphan = db.execute(
            """SELECT count(*) FROM assignments a WHERE NOT EXISTS(SELECT 1 FROM sections s WHERE s.section_id=a.section_id)
            OR NOT EXISTS(SELECT 1 FROM item_identity i WHERE i.item_id=a.item_id)"""
        ).fetchone()[0]
        invalid_titles = []
        for s in db.execute(
            "SELECT * FROM sections WHERE section_id IN (SELECT section_id FROM assignments)"
        ):
            e = json.loads(s["evidence_json"])
            a, b = e["title_span"]
            raw = Path(s["source"]).read_bytes().decode("utf-8")
            if raw[a:b] != s["title_raw"]:
                invalid_titles.append(s["section_id"])
        summary["verification"] = {
            "orphan_assignments": orphan,
            "titles_not_matching_current_source_spans": invalid_titles,
        }
        if orphan or invalid_titles:
            raise ValueError("Section conservation check failed")
        with (out / "sections.csv").open("w") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["section_id", "volume", "type", "material", "title", "source", "items"]
            )
            for s in db.execute(
                """SELECT s.*,count(a.item_id) n FROM sections s JOIN assignments a USING(section_id)
                GROUP BY s.section_id ORDER BY s.volume,s.source,s.section_id"""
            ):
                writer.writerow(
                    [
                        s[k]
                        for k in (
                            "section_id",
                            "volume",
                            "index_type",
                            "indexed_material",
                            "title_raw",
                            "source",
                        )
                    ]
                    + [s["n"]]
                )
        sample_counts = Counter()
        with (out / "samples.jsonl").open("w") as handle:
            for a in db.execute(
                """SELECT a.*,s.title_raw,s.index_type,s.source AS title_source,i.ref_raw
                FROM assignments a JOIN sections s USING(section_id) JOIN item_identity i USING(item_id)
                ORDER BY a.item_id,a.section_id"""
            ):
                key = (a["assignment_kind"], a["index_type"])
                if sample_counts[key] >= 2:
                    continue
                sample_counts[key] += 1
                sample = dict(a)
                e = json.loads(sample.pop("evidence_json"))
                sample["evidence"] = e
                raw = Path(e["source"]).read_bytes().decode("utf-8")
                span = e["current_reference_span"]
                sample["source_excerpt"] = (
                    raw[max(0, span[0] - 180) : span[1] + 180] if span else None
                )
                handle.write(encoded(sample) + "\n")
        db.execute(
            "UPDATE metadata SET json=? WHERE key='summary'", (encoded(summary),)
        )
        db.commit()
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    counts = summary["counts"]
    lines = [
        "# Proveniência regional dos verbetes",
        "",
        "| Medida | Quantidade |",
        "| --- | ---: |",
        f"| Itens examinados | {summary['total_items']} |",
        f"| Itens com seção/título candidato | {counts.get('assigned',0)} |",
        f"| Itens com tipo específico candidato | {summary['items_with_specific_type_candidates']} |",
        f"| Sem evidência de seção | {counts.get('no_section_evidence',0)} |",
        f"| Itens com alternativas | {counts.get('items_with_alternatives',0)} |",
        f"| Vínculos por sobreposição do trecho | {counts.get('reference_region_overlap',0)} |",
        f"| Vínculos por cabeçalho local | {counts.get('page_heading_candidate',0)} |",
        f"| Vínculos por região da página, span incerto | {counts.get('page_region_candidate',0)} |",
        f"| Vínculos por dois cabeçalhos vizinhos concordantes | {counts.get('bracketed_running_headers_candidate',0)} |",
        "",
        "Vínculos podem ser múltiplos por item. Título disponível não implica tipo específico, segmentação certificada ou destino correto.",
        "A origem histórica seleciona páginas; os títulos, limites e associações são recalculados no OCR atual. Páginas mistas conservam intervalos e alternativas.",
        "",
        "A seleção congelada está em `selection.json`; `sections.csv` lista títulos e fontes; `samples.jsonl` oferece exemplos determinísticos por tipo/força de evidência.",
        "As saídas preservam causas de abstenção e não alteram textos, verbetes ou destinos. A integração opcional acrescenta relações às seções no catálogo experimental.",
        "",
        "Tipos específicos não são inventados para títulos como INDEX. Cabeçalhos correntes não propagam estado; a ponte de uma página exige dois vizinhos concordantes e estrutura de lista, sem abertura conflitante.",
        "",
        f"SHA lógico: `{summary['logical_sha256']}`.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def apply(catalog, staged):
    origin = str(staged.resolve())
    with closing(readonly(staged)) as src, closing(connect(catalog)) as db:
        summary = json.loads(
            src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0]
        )
        if Path(summary["catalog"]).resolve() != catalog.resolve():
            raise ValueError("Another catalog was used for staging")
        before = stats(db)
        with db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS editorial_sections(section_id TEXT,origin TEXT,volume TEXT,index_type TEXT,indexed_material TEXT,title_raw TEXT,source TEXT,evidence_json TEXT,PRIMARY KEY(section_id,origin)) WITHOUT ROWID"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS item_editorial_sections(item_id TEXT,section_id TEXT,origin TEXT,assignment_kind TEXT,PRIMARY KEY(item_id,section_id,origin)) WITHOUT ROWID"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS editorial_section_members ON item_editorial_sections(section_id,origin,item_id)"
            )
            db.execute("DROP VIEW IF EXISTS index_source_section_candidates")
            db.execute("""CREATE VIEW index_source_section_candidates AS
                SELECT r.item_id,r.section_id,r.origin,r.assignment_kind,s.volume,s.index_type,
                       s.indexed_material,s.title_raw,s.source,
                       CASE r.assignment_kind WHEN 'opening_grammar_and_occurrence_span' THEN 100
                       WHEN 'reference_region_overlap' THEN 80 WHEN 'page_heading_candidate' THEN 40
                       WHEN 'bracketed_running_headers_candidate' THEN 30
                       WHEN 'page_region_candidate' THEN 20 ELSE 0 END AS evidence_priority
                FROM item_editorial_sections r JOIN editorial_sections s
                ON s.section_id=r.section_id AND s.origin=r.origin""")
            db.execute(
                """CREATE TABLE IF NOT EXISTS item_editorial_section_evidence(item_id TEXT,section_id TEXT,origin TEXT,evidence_json TEXT,PRIMARY KEY(item_id,section_id,origin)) WITHOUT ROWID"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS regional_section_outcomes(item_id TEXT,origin TEXT,reason TEXT,evidence_json TEXT,PRIMARY KEY(item_id,origin)) WITHOUT ROWID"""
            )
            for i in src.execute("SELECT * FROM item_identity"):
                current = db.execute(
                    "SELECT source,ref_raw FROM items WHERE id=?", (i["item_id"],)
                ).fetchone()
                if not current or tuple(current) != (i["source"], i["ref_raw"]):
                    raise ValueError(
                        "Catalog item changed since staging: " + i["item_id"]
                    )
            for s in src.execute(
                "SELECT * FROM sections WHERE section_id IN (SELECT section_id FROM assignments)"
            ):
                db.execute(
                    "INSERT OR REPLACE INTO editorial_sections VALUES(?,?,?,?,?,?,?,?)",
                    (s["section_id"], origin)
                    + tuple(
                        s[k]
                        for k in (
                            "volume",
                            "index_type",
                            "indexed_material",
                            "title_raw",
                            "source",
                            "evidence_json",
                        )
                    ),
                )
            equivalent = {}
            for s in src.execute("SELECT * FROM sections"):
                equivalent[s["section_id"]] = {
                    r["origin"]
                    for r in db.execute(
                        """SELECT origin FROM editorial_sections WHERE section_id=? AND volume=?
                        AND index_type=? AND indexed_material IS ? AND title_raw=? AND source=? AND evidence_json=?""",
                        tuple(
                            s[k]
                            for k in (
                                "section_id",
                                "volume",
                                "index_type",
                                "indexed_material",
                                "title_raw",
                                "source",
                                "evidence_json",
                            )
                        ),
                    )
                }
            added, reused = 0, 0
            for a in src.execute("SELECT * FROM assignments"):
                # A fresh run is provenance, not a new relationship when the same
                # literal section and assignment evidence are already recorded.
                same = db.execute(
                    """SELECT r.origin,e.evidence_json FROM item_editorial_sections r
                    LEFT JOIN item_editorial_section_evidence e USING(item_id,section_id,origin)
                    WHERE r.item_id=? AND r.section_id=? AND r.assignment_kind=?""",
                    (a["item_id"], a["section_id"], a["assignment_kind"]),
                )
                if any(
                    r["origin"] in equivalent[a["section_id"]]
                    and r["evidence_json"] == a["evidence_json"]
                    for r in same
                ):
                    reused += 1
                    continue
                added += 1
                db.execute(
                    "INSERT OR IGNORE INTO item_editorial_sections VALUES(?,?,?,?)",
                    (a["item_id"], a["section_id"], origin, a["assignment_kind"]),
                )
                db.execute(
                    "INSERT OR REPLACE INTO item_editorial_section_evidence VALUES(?,?,?,?)",
                    (a["item_id"], a["section_id"], origin, a["evidence_json"]),
                )
            for o in src.execute("SELECT * FROM outcomes"):
                db.execute(
                    "INSERT OR REPLACE INTO regional_section_outcomes VALUES(?,?,?,?)",
                    (o["item_id"], origin, o["reason"], o["evidence_json"]),
                )
            after = stats(db)
            if before != after:
                raise ValueError("Unexpected item or destination mutation")
            result = {
                "origin": origin,
                "before": before,
                "after": after,
                "relationships": src.execute(
                    "SELECT count(*) FROM assignments"
                ).fetchone()[0],
                "relationships_added": added,
                "relationships_reused": reused,
            }
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                ("regional_section_provenance:" + sha(origin), encoded(result)),
            )
    return result


def compare(staged, baseline):
    """Measure assignment gains/losses on identical items, not extraction recall."""

    def states(path):
        result = defaultdict(set)
        with closing(readonly(path)) as db:
            for r in db.execute(
                "SELECT a.item_id,s.index_type FROM assignments a JOIN sections s USING(section_id)"
            ):
                result[r["item_id"]].add(r["index_type"])
            identity = {
                r["item_id"]: (r["source"], r["ref_raw"])
                for r in db.execute("SELECT * FROM item_identity")
            }
        return result, identity

    old, old_ids = states(baseline)
    new, new_ids = states(staged)
    common = {i for i in old_ids.keys() & new_ids.keys() if old_ids[i] == new_ids[i]}
    counts, by_volume = Counter(), defaultdict(Counter)
    changes = []
    for i in sorted(common):
        before, after = old.get(i, set()), new.get(i, set())
        flags = []
        if not before and after:
            flags.append("new_section_candidate")
        if before and not after:
            flags.append("lost_section_candidate")
        if not before - UNKNOWN and after - UNKNOWN:
            flags.append("new_specific_type")
        if before - UNKNOWN and not after - UNKNOWN:
            flags.append("lost_specific_type")
        if before - UNKNOWN and after - UNKNOWN and before - UNKNOWN != after - UNKNOWN:
            flags.append("specific_type_changed_or_alternative")
        source = new_ids[i][0]
        volume = Path(source).parent.parent.name
        for flag in flags:
            counts[flag] += 1
            by_volume[volume][flag] += 1
            changes.append(
                (i, source, flag, "|".join(sorted(before)), "|".join(sorted(after)))
            )
    with (staged.parent / "comparison.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "source", "change", "before_types", "after_types"])
        w.writerows(changes)
    result = {
        "baseline": str(baseline.resolve()),
        "staged": str(staged.resolve()),
        "same_identity_items": len(common),
        "new_or_changed_identity_items": len(new_ids) - len(common),
        "counts": dict(counts),
        "by_volume": dict(sorted(by_volume.items())),
        "scope": "Candidate section/type coverage, not certified accuracy or extraction recall.",
    }
    (staged.parent / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("teste"))
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--region-inventory", type=Path)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-staged", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Prior sections.sqlite for deterministic per-item comparison",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.apply_staged:
        result = apply(args.catalog, args.apply_staged)
        (args.apply_staged.parent / "integration.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
    else:
        if not args.out:
            parser.error("--out is required")
        selection = (
            json.loads(args.selection.read_text())
            if args.selection
            else select_pages(args.catalog, args.region_inventory)
        )
        result = build(args.catalog, args.root, selection, args.out, args.workers)
        if args.baseline:
            result["comparison"] = compare(args.out / "sections.sqlite", args.baseline)
        if args.apply:
            result["integration"] = apply(args.catalog, args.out / "sections.sqlite")
            (args.out / "integration.json").write_text(
                json.dumps(result["integration"], indent=2) + "\n"
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

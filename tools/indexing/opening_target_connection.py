"""Connect opening-guided grammars to physical files, preserving occurrence identity.

Only the optional apply operation writes the experimental search catalog. All
source OCR and prior catalog reads use immutable text / readonly connections.
No editorial number is treated as a physical page number.
"""

import argparse
import csv
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.audit_alphabetical_numbering import supported_sequence_nodes
from scripts.scan_editorial_ocr import ATTR
from scripts.study_index_openings import run as extract
from tools.corpus_utils import page_files
from tools.indexing.candidate_search import normalize
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.epistle_catalog_recovery import ranked_openings
from tools.indexing.epistle_continuation_context import continuation_context
from tools.indexing.epistle_destination_map import build_destination_map
from tools.indexing.epistle_index_locators import locator_key, parse_epistle_locators
from tools.indexing.experiment_storage import pack_json
from tools.indexing import literal_target_links as ab
from tools.indexing.search_catalog_consolidation import connect, stats

VERSION = "opening-target-connection-v1"
INTERNAL_NUMBER = re.compile(r"(?<![\w\[(])\b([1-9]\d{0,2})\s+(?=[A-Za-zÀ-ž0-9])")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def compact(value):
    return " ".join(value.replace("\\n", " ").split()).strip(" .,;:")


def syntagma_markers(raw, maximum, end=None):
    """Unpunctuated Latin body numbers; canon labels like ``49.`` are excluded.

    These are observations, not accepted anchors until a sequence or a reviewed
    literal quote supports them. Greek parallels, notes and headers are excluded.
    """
    end = len(raw) if end is None else end
    for block in blocks(raw):
        attrs = dict(ATTR.findall(raw[block["start"] : block["body_start"]]))
        if block["kind"] != "texto_principal" or attrs.get("script") != "latino":
            continue
        for match in INTERNAL_NUMBER.finditer(
            raw, block["body_start"], min(end, block["body_end"])
        ):
            if int(match[1]) <= maximum:
                yield {
                    "number": int(match[1]),
                    "char_start": match.start(1),
                    "char_end": match.end(1),
                    "raw": match[1],
                    "excerpt": raw[
                        max(block["body_start"], match.start() - 80) : min(
                            end, block["body_end"], match.end() + 160
                        )
                    ],
                }


def build_syntagma(spec, root):
    grouped = page_files(root / spec["volume"] / "text", suffixes=[".txt"])
    markers, pages, nodes = [], [], defaultdict(set)
    for physical in range(spec["first"], spec["last"] + 1):
        paths = grouped.get(physical, [])
        if len(paths) != 1:
            raise ValueError(f"Syntagma source missing or ambiguous: {physical}")
        path = paths[0].resolve()
        raw = path.read_text(encoding="utf-8")
        if physical == spec["first"] and spec["start_quote"] not in raw:
            raise ValueError("Syntagma opening evidence absent")
        end = len(raw)
        if physical == spec["last"]:
            end = raw.find(spec["end_quote"])
            if end < 0:
                raise ValueError("Nomocanon boundary absent")
        page = {
            "path": str(path),
            "physical": physical,
            "volume": spec["volume"],
            "sha256": digest(raw),
        }
        pages.append(page)
        reviewed = {}
        for proof in spec["reviewed_markers"]:
            if proof["physical"] == physical:
                if raw.count(proof["quote"]) != 1:
                    raise ValueError("Reviewed marker quote absent or ambiguous")
                reviewed[(proof["number"], raw.index(proof["quote"]))] = proof
        for marker in syntagma_markers(raw, spec["maximum"], end):
            marker = {**page, **marker}
            marker["reviewed_quote"] = reviewed.get(
                (marker["number"], marker["char_start"])
            )
            markers.append(marker)
            nodes[marker["number"]].add(physical)
    support = supported_sequence_nodes(nodes, **spec["sequence"])
    for marker in markers:
        marker["sequence_support"] = support.get((marker["number"], marker["physical"]))
    return pages, markers


def build_maps(plan, repo, out):
    root = repo / "teste"
    profile = json.loads((repo / plan["bibliotheca_profile"]).read_text())
    proofs = []
    for proof in profile["evidence"]:
        path = root / proof["path"]
        raw = path.read_text(encoding="utf-8")
        # Hashes record provenance. A re-extraction does not invalidate a still
        # present editorial notice or named-work title merely by changing bytes.
        if not proof.get("quote") or proof["quote"] not in raw:
            raise ValueError("Bibliotheca editorial quote absent: " + str(path))
        proofs.append(
            {
                **proof,
                "current_sha256": digest(raw),
                "hash_changed": digest(raw) != proof["sha256"],
            }
        )
    pages, markers, _, skipped = ab.build_map(profile, root)
    supported, observed = defaultdict(list), defaultdict(list)
    for marker in markers:
        observed[marker["rank"]].append(marker)
        if marker["sequence_support"]:
            supported[marker["rank"]].append(marker)
    spages, smarkers = build_syntagma(plan["syntagma"], root)
    syntagma = defaultdict(list)
    for marker in smarkers:
        if marker["sequence_support"] or marker["reviewed_quote"]:
            syntagma[marker["number"]].append(marker)
    openings = continuation_context(
        build_destination_map(repo, tuple(plan["epistle_volumes"])), repo
    )
    epistles = defaultdict(list)
    for anchor in openings:
        epistles[locator_key(anchor)].append(anchor)
    for name, records in [
        ("bibliotheca_markers", markers),
        ("syntagma_markers", smarkers),
        ("epistle_openings", openings),
    ]:
        with (out / (name + ".jsonl")).open("w") as handle:
            for record in records:
                handle.write(encoded(record) + "\n")
    (out / "map_inventory.json").write_text(
        encoded(
            {
                "bibliotheca_pages": pages,
                "syntagma_pages": spages,
                "proofs": proofs,
                "bibliotheca_skipped": skipped,
                "plan": plan,
            }
        )
        + "\n"
    )
    return {
        "pages": pages,
        "supported": supported,
        "observed": observed,
        "syntagma": syntagma,
        "epistles": epistles,
        "max_files": profile["max_files"],
    }


def tokens_for(section, group):
    if "Bibliotheca" in section:
        return ab.interpret_group(group["raw"], group["char_start"]) or []
    if "Syntagma" in section:
        if not re.fullmatch(r"\s*\d+(?:\s*[,;]\s*\d+)*\s*\.?\s*", group["raw"]):
            return []
        return [
            {
                "number": int(m[0]),
                "raw": m[0],
                "char_start": group["char_start"] + m.start(),
                "char_end": group["char_start"] + m.end(),
            }
            for m in re.finditer(r"\d+", group["raw"])
        ]
    return parse_epistle_locators(group["raw"], offset=group["char_start"])["locators"]


def candidates_for(section, tokens, label, maps, repo):
    if "epistle" in section:
        return ranked_openings(tokens, label, maps["epistles"], repo)[0]
    found = {}
    for token in tokens:
        if "Bibliotheca" in section:
            result = ab.locate(
                token, maps["pages"], maps["supported"], maps["max_files"]
            )
            r = (
                ab.rank(token["number"], token["letter_raw"])
                if token["number"] is not None
                else None
            )
            anchors = maps["supported"].get(r, []) or maps["observed"].get(r, [])
            files = result["files"] or anchors
            score = (
                65
                if result["files"]
                else (48 if any(a["sequence_support"] for a in anchors) else 30)
            )
        else:
            anchors = maps["syntagma"].get(token["number"], [])
            files = anchors
            score = 60
            result = {
                "status": "internal_marker_file_candidate",
                "extent_status": "marker_file_only",
            }
        for page in files:
            path = page["path"]
            support = {"token": token, "location": result, "markers": anchors}
            warnings = ["destination_not_certified"]
            if len({a["path"] for a in anchors}) > 1:
                warnings.append("multiple_marker_files")
            if score == 30:
                warnings.append("marker_without_sequence_support")
            candidate = {
                "file": path,
                "physical": page["physical"],
                "score": score,
                "score_kind": "heuristic_not_probability",
                "reasons": [section],
                "uncertainties": warnings,
                "navigable": Path(path).is_file(),
                "certified": False,
                "snippet": next(
                    (a["excerpt"] for a in anchors if a["path"] == path), ""
                ),
                "evidence": {"supports": [support]},
            }
            if path in found:
                found[path]["evidence"]["supports"].append(support)
                found[path]["uncertainties"] = sorted(
                    set(found[path]["uncertainties"] + warnings)
                )
                found[path]["score"] = max(found[path]["score"], score)
            else:
                found[path] = candidate
    ranked = sorted(found.values(), key=lambda c: (-c["score"], c["file"]))
    for rank, candidate in enumerate(ranked, 1):
        candidate["rank"] = rank
    return ranked


def reconcile(group, label, tokens, prior, raw):
    """Reuse only the same literal occurrence and owner; isolate split tokens.

    Any overlapping prior interpretation with a different owner stays in the
    reconciliation queue. Previously reviewed joins are never overwritten.
    """
    a, b = group["char_start"], group["char_end"]
    overlaps, matches = [], []
    for item in prior:
        meta = json.loads(item["metadata_json"])
        span = meta.get("source_span")
        if not isinstance(span, list) or len(span) != 2:
            continue
        x, y = span
        if max(a, x) >= min(b, y):
            continue
        overlaps.append(item["id"])
        if (
            a <= x < y <= b
            and compact(item["label"]) == compact(label)
            and compact(raw[x:y]) == compact(item["ref_raw"])
        ):
            # A split prior record receives only locators within its own span.
            selected = [
                t for t in tokens if x <= t["char_start"] and t["char_end"] <= y
            ]
            matches.append((item, selected))
    matched = {i["id"] for i, _ in matches}
    return matches, [i for i in overlaps if i not in matched], overlaps


def stage(plan_path, catalog, repo, out, workers=4):
    plan = json.loads(plan_path.read_text())
    if plan["version"] != VERSION:
        raise ValueError("Unsupported connection plan")
    out.mkdir(parents=True, exist_ok=False)
    extract(repo / plan["extraction_plan"], repo / "teste", out / "extraction", workers)
    print(encoded({"stage": "building_destination_maps"}), flush=True)
    maps = build_maps(plan, repo, out)
    counts, sections = Counter(), defaultdict(Counter)
    with closing(readonly(catalog)) as old, closing(
        readonly(out / "extraction/extraction.sqlite")
    ) as extraction, closing(sqlite3.connect(out / "connections.sqlite")) as db:
        db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE items(id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,label TEXT,ref_raw TEXT,source TEXT,state TEXT,metadata_json TEXT);
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE reconciliation(entry_id TEXT,section TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);""")
        prior = defaultdict(list)
        for row in old.execute("SELECT * FROM items WHERE volume IN ('PG104','PL217')"):
            prior[str(Path(row["source"]).resolve())].append(dict(row))
        sources = {}
        query = """SELECT e.*,p.section_id,p.source,p.source_sha256 FROM entries e
                   JOIN pages p ON p.id=e.page_id ORDER BY p.section_id,p.physical,e.char_start,e.id"""
        for row in extraction.execute(query):
            section, source = row["section_id"], row["source"]
            if source not in sources:
                sources[source] = Path(source).read_text(encoding="utf-8")
            raw = sources[source]
            if digest(raw) != row["source_sha256"]:
                raise ValueError("Source changed during connection: " + source)
            entry = json.loads(row["entry_json"])
            for group in entry["reference_groups"]:
                tokens = tokens_for(section, group)
                matched, conflicts, overlaps = reconcile(
                    group, row["label"], tokens, prior[source], raw
                )
                status = (
                    "reused" if matched else ("overlap_review" if overlaps else "new")
                )
                selected = matched
                if status == "new":
                    identity = [
                        VERSION,
                        section,
                        source,
                        group["char_start"],
                        group["char_end"],
                        row["label"],
                        group["raw"],
                    ]
                    iid = "opening:" + digest(encoded(identity))
                    meta = {
                        "source_span": [group["char_start"], group["char_end"]],
                        "source_sha256": row["source_sha256"],
                        "section": section,
                        "entry_span": [row["char_start"], row["char_end"]],
                        "owner_span": group.get("owner_span"),
                        "extraction_entry_id": row["id"],
                        "selection": VERSION,
                        "quality": entry.get("quality", {}),
                        "uncertainties": entry.get("flags", []),
                    }
                    item = {
                        "id": iid,
                        "group_id": iid,
                        "volume": section.split(":")[0],
                        "label": row["label"],
                        "ref_raw": group["raw"],
                        "source": source,
                        "state": "source_only",
                        "metadata_json": encoded(meta),
                    }
                    selected = [(item, tokens)]
                covered = {encoded(t) for _, subset in selected for t in subset}
                remaining = [t for t in tokens if encoded(t) not in covered]
                evidence = {
                    "source": source,
                    "source_sha256": row["source_sha256"],
                    "group": group,
                    "matches": [i["id"] for i, _ in matched],
                    "conflicts": conflicts,
                    "unattached_tokens": remaining,
                }
                db.execute(
                    "INSERT INTO reconciliation VALUES(?,?,?,?)",
                    (row["id"], section, status, encoded(evidence)),
                )
                sections[section]["groups"] += 1
                sections[section][status] += 1
                sections[section]["groups_with_unattached_tokens"] += bool(remaining)
                for item, subset in selected:
                    db.execute(
                        "INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?)",
                        tuple(
                            item[k]
                            for k in (
                                "id",
                                "group_id",
                                "volume",
                                "label",
                                "ref_raw",
                                "source",
                                "state",
                                "metadata_json",
                            )
                        ),
                    )
                    candidates = candidates_for(
                        section, subset, row["label"], maps, repo
                    )
                    if candidates:
                        sections[section]["connected_occurrences"] += 1
                    for candidate in candidates:
                        if candidate["file"] == source:
                            raise ValueError("Index points to itself")
                        db.execute(
                            """INSERT INTO candidates VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,file)
                            DO UPDATE SET rank=excluded.rank,score=excluded.score,candidate_json=excluded.candidate_json
                            WHERE excluded.score>candidates.score""",
                            (
                                item["id"],
                                candidate["file"],
                                candidate["rank"],
                                candidate["score"],
                                int(candidate["navigable"]),
                                encoded(candidate),
                            ),
                        )
                counts[status] += 1
        summary = {
            "version": VERSION,
            "catalog": str(catalog.resolve()),
            "plan": str(plan_path.resolve()),
            "plan_sha256": digest(encoded(plan)),
            "sections": dict(sections),
            "reconciliation": counts,
            "selected_items": db.execute("SELECT count(*) FROM items").fetchone()[0],
            "connected_items": db.execute(
                "SELECT count(DISTINCT item_id) FROM candidates WHERE navigable=1"
            ).fetchone()[0],
            "candidate_links": db.execute("SELECT count(*) FROM candidates").fetchone()[
                0
            ],
        }
        db.execute("INSERT INTO metadata VALUES(?,?)", ("summary", encoded(summary)))
        db.commit()
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return write_report(out)


def write_report(out, integration=None):
    """Count distinct catalog items separately from freshly extracted groups."""
    with closing(sqlite3.connect(out / "connections.sqlite")) as db:
        db.row_factory = sqlite3.Row
        summary = json.loads(
            db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0]
        )
        memberships, additions = defaultdict(set), defaultdict(set)
        new_by_entry = defaultdict(list)
        items = {r["id"]: dict(r) for r in db.execute("SELECT * FROM items")}
        linked = {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT item_id FROM candidates WHERE navigable=1"
            )
        }
        for item in items.values():
            meta = json.loads(item["metadata_json"])
            if meta.get("extraction_entry_id"):
                new_by_entry[meta["extraction_entry_id"]].append(item["id"])
        for row in db.execute("SELECT * FROM reconciliation"):
            evidence = json.loads(row["evidence_json"])
            ids = (
                evidence["matches"]
                if row["status"] != "new"
                else new_by_entry[row["entry_id"]]
            )
            memberships[row["section"]].update(ids)
            if row["status"] == "new":
                additions[row["section"]].update(ids)
        for section, ids in memberships.items():
            summary["sections"][section].update(
                selected_items=len(ids),
                connected_items=len(ids & linked),
                new_items=len(additions[section]),
                new_items_with_candidates=len(additions[section] & linked),
            )
        db.execute("""CREATE TABLE IF NOT EXISTS editorial_sections(
            section_id TEXT PRIMARY KEY,volume TEXT,index_type TEXT,indexed_material TEXT,
            title_raw TEXT,source TEXT,evidence_json TEXT)""")
        db.execute(
            """CREATE TABLE IF NOT EXISTS item_editorial_sections(
            item_id TEXT,section_id TEXT,assignment_kind TEXT,PRIMARY KEY(item_id,section_id))"""
        )
        with closing(readonly(out / "extraction/extraction.sqlite")) as extraction:
            for section in extraction.execute("SELECT id,evidence_json FROM sections"):
                evidence = json.loads(section["evidence_json"])
                descriptor = section_descriptor(
                    evidence,
                    Path(evidence["opening"]["source"]).read_text(encoding="utf-8"),
                )
                db.execute(
                    "INSERT OR REPLACE INTO editorial_sections VALUES(?,?,?,?,?,?,?)",
                    (
                        section["id"],
                        descriptor["volume"],
                        descriptor["index_type"],
                        descriptor["indexed_material"],
                        descriptor["title_raw"],
                        descriptor["source"],
                        encoded(descriptor),
                    ),
                )
                for iid in memberships[section["id"]]:
                    db.execute(
                        "INSERT OR REPLACE INTO item_editorial_sections VALUES(?,?,?)",
                        (iid, section["id"], "opening_grammar_and_occurrence_span"),
                    )
        algorithm_paths = [Path(__file__), Path(ab.__file__)]
        for function in (
            extract,
            build_destination_map,
            continuation_context,
            ranked_openings,
            parse_epistle_locators,
            supported_sequence_nodes,
        ):
            import inspect

            algorithm_paths.append(Path(inspect.getfile(function)))
        summary["algorithm_sha256"] = {
            str(p.resolve()): digest(p.read_text())
            for p in sorted(set(algorithm_paths))
        }
        if integration is not None:
            if "integration" not in summary or integration["changes"]:
                summary["integration"] = integration
            else:
                summary["idempotence_check"] = integration
        db.execute(
            "UPDATE metadata SET json=? WHERE key='summary'", (encoded(summary),)
        )
        db.commit()
        with (out / "items.csv").open("w") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "section",
                    "item_id",
                    "new",
                    "label",
                    "reference",
                    "source",
                    "candidate_files",
                ]
            )
            for section, ids in sorted(memberships.items()):
                for iid in sorted(ids):
                    item = items[iid]
                    files = [
                        r[0]
                        for r in db.execute(
                            "SELECT file FROM candidates WHERE item_id=? AND navigable=1 ORDER BY score DESC,file",
                            (iid,),
                        )
                    ]
                    writer.writerow(
                        [
                            section,
                            iid,
                            iid in additions[section],
                            item["label"],
                            item["ref_raw"],
                            item["source"],
                            encoded(files),
                        ]
                    )
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# Conexão das gramáticas às páginas físicas",
        "",
        "Resultados mecânicos experimentais; presença de candidato não certifica atribuição ou destino.",
        "",
        "| Seção | Grupos extraídos | Itens reconciliados | Com candidato desta etapa | Novos itens | Novos com candidato |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for section, s in summary["sections"].items():
        lines.append(
            f"| {section} | {s['groups']} | {s['selected_items']} | {s['connected_items']} | {s['new_items']} | {s['new_items_with_candidates']} |"
        )
    lines += [
        "",
        "Grupos e itens têm denominadores diferentes: um grupo pode corresponder a vários registros anteriores, cada qual com seu próprio span e subconjunto de referências.",
        "",
        f"Conflitos de proprietário preservados em staging: {summary['reconciliation'].get('overlap_review', 0)}.",
        "Não foram sobrescritas as junções históricas revisadas. Referências sem ligação permanecem estruturadas e pesquisáveis no catálogo quando novas.",
        "",
        "`items.csv` lista itens, fontes e arquivos candidatos. `connections.sqlite` preserva a reconciliação; os JSONL de marcadores preservam evidências e alternativas.",
        "Os hashes registram proveniência. Mudança do hash de um arquivo não invalida, por si, a citação editorial ainda presente.",
        "",
        "## Integração",
        "",
    ]
    if "integration" in summary:
        lines += [
            "```json",
            json.dumps(summary["integration"], ensure_ascii=False, indent=2),
            "```",
        ]
    else:
        lines += ["Somente staging; catálogo ainda não alterado por esta execução."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def section_descriptor(section, raw=None):
    """Classify the source index from its recognized opening, never the lemma.

    ``index_type`` describes organization; ``indexed_material`` describes the
    subject corpus (e.g. letters). Both remain separate from destination namespace.
    Exact printed title and its evidence survive alongside normalized categories.
    """
    opening, selection = section["opening"], section["selection"]
    family = opening.get("family")
    if family == "photius_bibliotheca_ab":
        kind, material = "subject_and_word_index", "Bibliotheca of Photius"
    elif family == "syntagma_internal_numbers":
        kind, material = "thematic_index", "Syntagma canonum"
    elif opening.get("entry_kind") == "epistle_incipit":
        kind, material = "incipit_index", "letters"
    else:
        kind, material = "unknown", None
    title, span = opening.get("title_raw"), opening.get("title_span")
    if title is None:
        title_evidence = next(
            (e for e in opening.get("evidence", []) if e.get("role") == "title"), {}
        )
        title = title_evidence.get("raw")
        span = {
            k: title_evidence[k]
            for k in ("char_start", "char_end")
            if k in title_evidence
        }
        ordering = next(
            (e for e in opening.get("evidence", []) if e.get("role") == "ordering"),
            None,
        )
        if (
            raw is not None
            and digest(raw) == opening["source_sha256"]
            and span
            and ordering
        ):
            a, b = span["char_start"], ordering["char_start"]
            if a < b:
                title = raw[a:b].rstrip()
                span = {"char_start": a, "char_end": a + len(title)}
    return {
        "volume": selection["volume"],
        "index_type": kind,
        "indexed_material": material,
        "title_raw": title,
        "title_span": span,
        "source": opening["source"],
        "source_sha256": opening["source_sha256"],
        "namespace": selection["namespace"],
        "physical_range": [selection["first"], selection["last"]],
        "classification_basis": "recognized_printed_opening",
        "opening_evidence": opening,
        "scope_status": "selected_region_not_exhaustively_certified",
    }


def apply(catalog, staged):
    """Add occurrences and candidate evidence atomically, idempotently.

    Conflicting interpretations never enter this transaction. Existing candidates
    and item identities are conserved; separate support rows preserve weaker new
    evidence even when it does not outrank an existing candidate for the same file.
    """
    origin = str(staged.resolve())
    with closing(readonly(staged)) as src, closing(connect(catalog)) as db:
        summary = json.loads(
            src.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0]
        )
        if Path(summary["catalog"]).resolve() != catalog.resolve():
            raise ValueError("Connection belongs to another catalog")
        before = stats(db)
        changes = Counter()
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """CREATE TABLE IF NOT EXISTS opening_candidate_support(
                origin TEXT,item_id TEXT,file TEXT,evidence_json BLOB,PRIMARY KEY(origin,item_id,file)) WITHOUT ROWID"""
            )
            if src.execute(
                "SELECT 1 FROM sqlite_master WHERE name='editorial_sections'"
            ).fetchone():
                db.execute(
                    """CREATE TABLE IF NOT EXISTS editorial_sections(
                    section_id TEXT,origin TEXT,volume TEXT,index_type TEXT,indexed_material TEXT,
                    title_raw TEXT,source TEXT,evidence_json TEXT,PRIMARY KEY(section_id,origin)) WITHOUT ROWID"""
                )
                db.execute("""CREATE TABLE IF NOT EXISTS item_editorial_sections(
                    item_id TEXT,section_id TEXT,origin TEXT,assignment_kind TEXT,
                    PRIMARY KEY(item_id,section_id,origin)) WITHOUT ROWID""")
                for section in src.execute("SELECT * FROM editorial_sections"):
                    db.execute(
                        "INSERT OR REPLACE INTO editorial_sections VALUES(?,?,?,?,?,?,?,?)",
                        (section["section_id"], origin)
                        + tuple(
                            section[k]
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
                for relation in src.execute("SELECT * FROM item_editorial_sections"):
                    if not src.execute(
                        "SELECT 1 FROM items WHERE id=?", (relation["item_id"],)
                    ).fetchone():
                        raise ValueError("Section relation has no staged item")
                    db.execute(
                        "INSERT OR IGNORE INTO item_editorial_sections VALUES(?,?,?,?)",
                        (
                            relation["item_id"],
                            relation["section_id"],
                            origin,
                            relation["assignment_kind"],
                        ),
                    )
            for row in src.execute("SELECT * FROM items ORDER BY id"):
                old = db.execute(
                    "SELECT * FROM items WHERE id=?", (row["id"],)
                ).fetchone()
                if old:
                    if any(
                        old[k] != row[k]
                        for k in ("source", "label", "ref_raw", "metadata_json")
                    ):
                        raise ValueError(
                            "Catalog occurrence changed since staging: " + row["id"]
                        )
                else:
                    db.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?)", tuple(row))
                    rowid = db.execute(
                        "SELECT rowid FROM items WHERE id=?", (row["id"],)
                    ).fetchone()[0]
                    db.execute(
                        "INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)",
                        (
                            rowid,
                            normalize(row["label"]),
                            normalize(row["ref_raw"]),
                            row["volume"],
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO localization_queue VALUES(?,?)",
                        (row["id"], "opening_grammar_pending"),
                    )
                    changes["new_items"] += 1
                db.execute(
                    "INSERT OR IGNORE INTO item_origins VALUES(?,?,?)",
                    (row["id"], origin, "opening_grammar"),
                )
            for row in src.execute("SELECT * FROM candidates ORDER BY item_id,file"):
                if row["navigable"] and not Path(row["file"]).is_file():
                    raise ValueError("Destination disappeared: " + row["file"])
                if not db.execute(
                    "SELECT 1 FROM candidate_storage WHERE item_id=? AND file=?",
                    (row["item_id"], row["file"]),
                ).fetchone():
                    changes["new_links"] += 1
                db.execute(
                    "INSERT OR REPLACE INTO opening_candidate_support VALUES(?,?,?,?)",
                    (
                        origin,
                        row["item_id"],
                        row["file"],
                        pack_json(row["candidate_json"]),
                    ),
                )
                db.execute(
                    "INSERT OR IGNORE INTO candidate_origins VALUES(?,?,?)",
                    (row["item_id"], row["file"], origin),
                )
                db.execute(
                    """INSERT INTO candidate_storage VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,file)
                    DO UPDATE SET rank=excluded.rank,score=excluded.score,navigable=excluded.navigable,candidate_json=excluded.candidate_json
                    WHERE (excluded.navigable,excluded.score)>(candidate_storage.navigable,candidate_storage.score)""",
                    tuple(
                        row[k]
                        for k in ("item_id", "file", "rank", "score", "navigable")
                    )
                    + (pack_json(row["candidate_json"]),),
                )
            db.execute(
                "UPDATE items SET state='with_destinations' WHERE state!='with_destinations' AND EXISTS(SELECT 1 FROM candidate_storage c WHERE c.item_id=items.id AND c.navigable=1)"
            )
            db.execute(
                "DELETE FROM localization_queue WHERE item_id IN (SELECT id FROM items WHERE state='with_destinations')"
            )
            config = json.loads(
                db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[
                    0
                ]
            )
            config["incremental_supplements"] = sorted(
                set(config.get("incremental_supplements", []) + [origin])
            )
            db.execute(
                "UPDATE metadata SET json=? WHERE key='summary'", (encoded(config),)
            )
            after = stats(db)
            result = {"before": before, "after": after, "changes": changes}
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES(?,?)",
                ("opening_connection:" + digest(origin), encoded(result)),
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("docs/fixtures/index_rebuild/connect_opening_grammars.json"),
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--apply-staged",
        type=Path,
        help="Apply an already generated connections.sqlite without re-extraction",
    )
    args = parser.parse_args()
    if args.apply_staged:
        result = apply(args.catalog.resolve(), args.apply_staged)
        write_report(args.apply_staged.parent, result)
    else:
        if not args.out:
            parser.error("--out is required for extraction")
        result = stage(
            args.plan.resolve(),
            args.catalog.resolve(),
            args.root.resolve(),
            args.out.resolve(),
            args.workers,
        )
        if args.apply:
            result["integration"] = apply(
                args.catalog.resolve(), args.out / "connections.sqlite"
            )
            result = write_report(args.out, result["integration"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

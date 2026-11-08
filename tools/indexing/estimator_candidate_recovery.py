"""Read-only source adapter: editorial page hypotheses and work/section context.

The output is a candidate overlay, not a replacement for a canonical resolver.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.editorial_page_estimator import estimate_editorial_pages

VERSION = "estimator-candidate-recovery-v1"
ROOT = Path(__file__).resolve().parents[2]
PAGE_NAMESPACES = {"unknown", "page", "page_line", "qualified_page", "bracketed_page"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def metadata_object(raw):
    """Legacy rows may contain JSON strings/lists; retain raw data separately."""
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}, "invalid_metadata_json"
    if not isinstance(value, dict):
        return {}, "metadata_json_not_object"
    return value, None


def contiguous_segments(files):
    """Never transfer an offset across missing physical files or duplicate suffixes."""
    segments = []
    for path in files:
        previous = page_number(segments[-1][-1]) if segments else None
        number = page_number(path)
        if not segments or previous is None or number is None or number != previous + 1:
            segments.append([])
        segments[-1].append(path)
    return segments


def reference_numbers(item, meta):
    namespace = meta.get("reference_namespace") or "unknown"
    old_status = meta.get("original_status") or meta.get("status") or ""
    if namespace not in PAGE_NAMESPACES:
        return [], "requires_namespace_map:" + namespace
    if "namespace_not_mapped" in old_status or "requires_namespace_map" in old_status:
        return [], "requires_namespace_map:" + old_status
    raw = item.get("ref_raw") or ""
    if namespace == "page_line":
        tokens = meta.get("page_line_tokens") or []
        numbers = [int(t["page_raw"]) for t in tokens
                   if str(t.get("page_raw", "")).isdecimal()
                   and (t.get("raw") or str(t["page_raw"]) + "_" + str(t.get("line_raw", ""))) in raw]
        return sorted(set(numbers)), "pagination" if numbers else "page_line_tokens_missing"
    if re.search(r"(?i)\b(?:ibid|ibidem|eod|idem)\b", raw):
        return [], "requires_editorial_inheritance"
    # The parser deliberately keeps book/chapter qualifiers out of page arithmetic.
    cleaned = re.sub(r"(?i)\b(?:pag(?:ina)?|col(?:umna)?|p)\.?\s*", "", raw)
    roman_scan = re.sub(r"(?<=\d)\s*[ABCDabcd]\b", "", cleaned)
    if re.search(r"(?i)\b[ivxlcdm]+\b", roman_scan):
        return [], "requires_roman_context"
    if not re.fullmatch(r"[\d\s,;.:()\[\]–—\-ABCDabcd]+", cleaned):
        return [], "requires_notation_parser"
    if re.search(r"\d{5,}", cleaned):
        return [], "requires_notation_parser"
    numbers = {int(n) for n in re.findall(r"\d+", cleaned) if int(n) > 0}
    for start, end in re.findall(r"(\d+)\s*[-–—]\s*(\d+)", cleaned):
        a, b = int(start), int(end)
        if not a <= b <= a + 200:
            return [], "requires_range_interpretation"
        numbers.update(range(a, b + 1))
    return sorted(numbers), "pagination_hypothesis" if namespace == "unknown" else "pagination"


def load_work_context(db, volume, files):
    with closing(readonly(db)) as conn:
        works = [dict(r) for r in conn.execute("SELECT * FROM works WHERE volume_id=? ORDER BY work_key", (volume,))]
        sections = [dict(r) for r in conn.execute("SELECT * FROM index_sections WHERE volume_id=? ORDER BY section_key", (volume,))]
        entries = [dict(r) for r in conn.execute(
            "SELECT e.* FROM index_entries e JOIN index_sections s USING(section_key) "
            "WHERE s.volume_id=? ORDER BY e.id", (volume,))]
    available = {str(p.resolve()): page_number(p) for p in files}
    by_key = {w["work_key"]: w for w in works}
    for work in works:
        work["physical_start"] = available.get(work.get("start_file"))
        work["physical_end"] = available.get(work.get("end_file"))
        work["bounded"] = (work["physical_start"] is not None and work["physical_end"] is not None
                           and work["physical_start"] <= work["physical_end"])
    for section in sections:
        section["physical_start"] = available.get(section.get("file_start"))
        section["physical_end"] = available.get(section.get("file_end"))
        section["linked_work_present"] = section.get("work_key") in by_key
    for entry in entries:
        metadata, warning = metadata_object(entry["raw_json"])
        entry["metadata_warning"] = warning
        entry["entry_kind"] = metadata.get("entry_kind", "unknown")
        entry["reference_kind"] = metadata.get("reference_kind", "unknown")
        entry["target_physical"] = available.get(entry.get("target_file"))
    return works, sections, entries


def add_catalog_anchors(mapping, entries):
    """Only explicit editorial page/column semantics; never internal ordinals."""
    for entry in entries:
        literal = entry.get("page_ref_raw") or ""
        if entry["target_physical"] is None or not entry.get("target_file"):
            continue
        if entry["reference_kind"] not in {"editorial_column", "editorial_page"}:
            continue
        match = re.fullmatch(r"\s*(?:(?:col|pag|p)\.\s*)?([1-9][0-9]{0,3})\s*", literal, re.I)
        if not match:
            continue
        number = int(match[1])
        mapping[number].append({"file": entry["target_file"], "physical": entry["target_physical"],
                                "number": number, "hypothesis_order": 5, "score": 0,
                                "evidence": [{"kind": "work_index_anchor", "entry_id": entry["id"],
                                              "section_key": entry["section_key"], "literal": literal,
                                              "reference_kind": entry["reference_kind"]}],
                                "sequence_validation": None,
                                "warnings": ["catalog_anchor_not_independently_verified"]})


def source_context(item, meta, sections):
    physical = page_number(Path(item["source"]))
    matches = []
    for section in sections:
        explicit = meta.get("section_key") == section["section_key"]
        a, b = section["physical_start"], section["physical_end"]
        inside = physical is not None and a is not None and b is not None and a <= physical <= b
        if explicit or inside:
            matches.append({"section_key": section["section_key"], "work_key": section["work_key"],
                            "index_kind": section["index_kind"], "heading_raw": section["heading_raw"],
                            "basis": "explicit_section_key" if explicit else "source_file_in_section_range",
                            "uncertainty": "section_boundaries_and_scope_not_certified"})
    return matches


def invert_estimates(estimates):
    result = defaultdict(list)
    for file in estimates:
        for order, hypothesis in enumerate(file["candidate_editorial_pages"]):
            # A body numeral alone is commonly a citation. Keep it in estimates,
            # but do not advertise it as physical pagination.
            kinds = {e["kind"] for e in hypothesis["evidence"]}
            if not any("header" in k or "footer" in k or "neighbor" in k
                       or "sequence" in k or "override" in k for k in kinds):
                continue
            for number in hypothesis["pages"]:
                result[number].append({"file": file["file"], "physical": file["file_seq"],
                                       "number": number, "hypothesis_order": order,
                                       "score": hypothesis["score"], "evidence": hypothesis["evidence"],
                                       "sequence_validation": file["sequence_validation"] if order == 0 else None,
                                       "warnings": file["warnings"]})
    return result


def rank_candidates(numbers, mapping, item, contexts, works):
    scoped = {c["work_key"] for c in contexts if c["work_key"]}
    by_file = {}
    for number in numbers:
        for evidence in mapping.get(number, []):
            path = evidence["file"]
            physical = evidence["physical"]
            matching_works = sorted(w["work_key"] for w in works if w["work_key"] in scoped
                                    and w["bounded"] and physical is not None
                                    and w["physical_start"] <= physical <= w["physical_end"])
            row = by_file.setdefault(path, {"file": path, "physical": physical, "matches": [],
                                           "work_scope_matches": matching_works,
                                           "target_work_keys": sorted(w["work_key"] for w in works
                                               if w["bounded"] and physical is not None
                                               and w["physical_start"] <= physical <= w["physical_end"]),
                                           "source_file_itself": path == item["source"]})
            row["matches"].append(evidence)
    # Scope contributes to ranking only. Overlapping works and other candidates survive.
    result = sorted(by_file.values(), key=lambda r: (
        r["source_file_itself"], not bool(r["work_scope_matches"]),
        min(m["hypothesis_order"] for m in r["matches"]),
        -max(m["score"] for m in r["matches"]), r["file"]))
    for rank, row in enumerate(result, 1):
        row["rank"] = rank
    return result


def process_volume(task):
    volume, config = task
    with closing(readonly(config["catalog"])) as conn:
        sql = "SELECT * FROM items WHERE volume=? AND state='source_only' ORDER BY id"
        args = [volume]
        if config["limit_per_volume"]:
            sql += " LIMIT ?"
            args.append(config["limit_per_volume"])
        items = [dict(r) for r in conn.execute(sql, args)]
    text_root = Path(config["corpus_root"]) / volume / "text"
    files = discover_preferred_pages(text_root, volume_id=volume) if text_root.is_dir() else []
    works, sections, entries = load_work_context(config["works_db"], volume, files)
    estimates, stats = [], Counter()
    prepared = []
    for item in items:
        meta, warning = metadata_object(item["metadata_json"])
        item["metadata_warning"] = warning
        prepared.append((item, meta))
    if volume[:2] in {"PG", "PL"} and any(reference_numbers(i, m)[0] for i, m in prepared):
        for segment in contiguous_segments(files):
            payload = estimate_editorial_pages(volume_id=volume, source_root=text_root, files=segment,
                                               db_path=Path(config["estimator_db"]), readonly_cache=True,
                                               window=config["window"])
            estimates.extend(payload["files"])
            stats.update({k: payload[k] for k in ("cache_hits", "observations_recomputed")})
            stats["contiguous_segments"] += 1
    mapping = invert_estimates(estimates)
    if volume[:2] in {"PG", "PL"}:
        add_catalog_anchors(mapping, entries)
    rows, source_cache = [], {}
    for item, meta in prepared:
        contexts = source_context(item, meta, sections)
        numbers, route = reference_numbers(item, meta)
        if volume[:2] not in {"PG", "PL"} and numbers:
            route, numbers = "requires_collection_page_map", []
        candidates = rank_candidates(numbers, mapping, item, contexts, works)
        source = item["source"]
        if source not in source_cache:
            p = Path(source)
            raw = p.read_text(encoding="utf-8") if p.is_file() else None
            source_cache[source] = raw
        raw = source_cache[source]
        span = meta.get("reference_span") or meta.get("source_span")
        span_matches = (raw is not None and isinstance(span, list) and len(span) == 2
                        and all(isinstance(v, int) for v in span) and 0 <= span[0] <= span[1] <= len(raw)
                        and raw[span[0]:span[1]] == item["ref_raw"])
        rows.append({"id": item["id"], "volume": volume, "label": item["label"],
                     "ref_raw": item["ref_raw"], "source": source, "route": route,
                     "reference_namespace": meta.get("reference_namespace", "unknown"),
                     "numbers": numbers, "unmatched_numbers": [n for n in numbers if n not in mapping],
                     "source_span": span, "source_span_matches": span_matches,
                     "source_sha256": hashlib.sha256(raw.encode()).hexdigest() if raw is not None else None,
                     "source_evidence_warning": None if span_matches else "source_span_unconfirmed_candidate_only",
                     "contexts": contexts, "candidates": candidates, "certified": False})
        rows[-1]["metadata_warning"] = item["metadata_warning"]
    evidence_counts = Counter()
    for row in rows:
        if not row["candidates"]:
            continue
        kinds = {e["kind"] for m in row["candidates"][0]["matches"] for e in m["evidence"]}
        category = ("direct_header" if kinds & {"header_pair", "header_single"} else
                    "sequence_consensus" if "sequence_consensus" in kinds else "other_neighbor_or_anchor")
        evidence_counts[category] += 1
    summary = {"volume": volume, "items": len(rows), "with_candidates": sum(bool(r["candidates"]) for r in rows),
               "with_section_context": sum(bool(r["contexts"]) for r in rows),
               "with_work_context": sum(any(c["work_key"] for c in r["contexts"]) for r in rows),
               "with_target_work_context": sum(any(c["target_work_keys"] for c in r["candidates"]) for r in rows),
               "routes": dict(sorted(Counter(r["route"] for r in rows).items())),
               "source_span_unconfirmed": sum(not r["source_span_matches"] for r in rows),
               "metadata_warnings": sum(bool(r["metadata_warning"]) for r in rows),
               "work_entry_metadata_warnings": sum(bool(e["metadata_warning"]) for e in entries),
               "works": len(works), "sections": len(sections), "work_index_entries": len(entries),
               "ocr_files": len(files), "top_candidate_evidence": dict(sorted(evidence_counts.items())), **stats}
    snapshots = [{"file": str(p), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in files]
    return {"summary": summary, "rows": rows, "works": works, "sections": sections, "work_index_entries": entries,
            "estimates": estimates, "source_inventory": snapshots}


def run(args):
    if args.catalog is None:
        pointer = json.loads((ROOT / "data/index_catalog/current_search.json").read_text())
        args.catalog = ROOT / pointer["database"]
    config = {k: str(v.resolve()) if isinstance(v, Path) else v for k, v in vars(args).items()}
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output directory must be empty; choose a new --out to preserve the prior experiment")
    if any(p.resolve().is_relative_to(out) for p in [args.catalog, args.works_db, args.estimator_db]):
        raise ValueError("Output cannot contain an input database")
    with closing(readonly(args.catalog)) as conn:
        available = {r[0] for r in conn.execute("SELECT DISTINCT volume FROM items WHERE state='source_only'")}
    volumes = sorted(available if args.all_volumes else set(args.volume))
    if not set(volumes) <= available:
        raise ValueError("Unknown volumes or volumes without pending records: " + str(set(volumes) - available))
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out / "candidates.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE runs(version TEXT, config_json TEXT);
        CREATE TABLE results(item_id TEXT PRIMARY KEY, volume TEXT, route TEXT, payload_json TEXT);
        CREATE TABLE candidates(item_id TEXT, file TEXT, rank INTEGER, evidence_json TEXT, PRIMARY KEY(item_id,file));
        CREATE TABLE volume_context(volume TEXT PRIMARY KEY, payload_json TEXT);
    """)
    conn.execute("INSERT INTO runs VALUES (?,?)", (VERSION, encoded(config)))
    summaries, digest = [], hashlib.sha256()
    tasks = [(v, config) for v in volumes]
    with ProcessPoolExecutor(max_workers=args.workers) as pool, (out / "results.jsonl").open("w") as detail, (out / "results.csv").open("w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["item_id", "volume", "label", "ref_raw", "route", "candidate_count", "first_file", "source_span_matches"])
        for result in pool.map(process_volume, tasks):
            summary = result.pop("summary")
            rows = result.pop("rows")
            summaries.append(summary)
            conn.execute("INSERT INTO volume_context VALUES (?,?)", (summary["volume"], encoded(result)))
            digest.update(encoded(result).encode())
            for row in rows:
                payload = encoded(row)
                digest.update(payload.encode())
                detail.write(payload + "\n")
                writer.writerow([row["id"], row["volume"], row["label"], row["ref_raw"], row["route"], len(row["candidates"]),
                                 row["candidates"][0]["file"] if row["candidates"] else "", row["source_span_matches"]])
                conn.execute("INSERT INTO results VALUES (?,?,?,?)", (row["id"], row["volume"], row["route"], payload))
                conn.executemany("INSERT INTO candidates VALUES (?,?,?,?)", [(row["id"], c["file"], c["rank"], encoded(c)) for c in row["candidates"]])
            conn.commit()
            print(encoded(summary), flush=True)
    conn.close()
    summary = {"version": VERSION, "config": config, "volumes": summaries,
               "items": sum(s["items"] for s in summaries),
               "with_candidates": sum(s["with_candidates"] for s in summaries),
               "logical_sha256": digest.hexdigest(), "canonical_write_allowed": False,
               "quality": "unreviewed_candidates_not_certified_destinations"}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Recuperação por paginação estimada e contexto de obras", "",
             "Candidatos experimentais. Bancos de origem abertos em mode=ro; nenhuma promoção ao catálogo ativo.", "",
             "| Volume | Pendentes examinados | Com candidatos | Obra do destino sugerida | Spans não confirmados |",
             "|---|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {s['volume']} | {s['items']} | {s['with_candidates']} | {s['with_target_work_context']} | {s['source_span_unconfirmed']} |")
    lines += ["", "Os números desconhecidos são hipóteses de paginação. Livros, capítulos, ibid. e sistemas internos permanecem em filas específicas.",
              "Os vínculos de obras alteram apenas o ranking; páginas alternativas são preservadas. Não há medição de precisão sem amostra revisada.",
              "Consulte results.jsonl para alternativas e evidências; volume_context no SQLite preserva obras, seções, estimativas e inventário das fontes."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, help="Default: database in data/index_catalog/current_search.json")
    parser.add_argument("--works-db", type=Path, default=ROOT / "data/patristic_indices.db")
    parser.add_argument("--estimator-db", type=Path, default=ROOT / "data/editorial_page_estimator.db")
    parser.add_argument("--corpus-root", type=Path, default=ROOT / "teste")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--volume", action="append", default=[])
    selection.add_argument("--all-volumes", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--window", type=int, default=4)
    parser.add_argument("--limit-per-volume", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.workers < 1 or args.window < 1 or args.limit_per_volume < 0:
        parser.error("workers/window must be positive and limit-per-volume nonnegative")
    run(args)

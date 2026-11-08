"""Materialize numeric/ibid candidates in the experimental search catalog.

No canonical database or OCR is written. The transaction is additive, and each
input interpretation remains available independently of the displayed winner.
"""

import argparse
from collections import Counter
from contextlib import ExitStack, closing
import csv
import hashlib
import json
from pathlib import Path

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.experiment_storage import pack_json
from tools.indexing.search_catalog_consolidation import connect, encoded, stats

VERSION = "search-recovery-merge-v1"


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numeric_candidate(row):
    evidence = json.loads(row["evidence_json"])
    warnings = {"estimated_pagination_not_certified", "no_lexical_support"}
    for match in evidence.get("matches", []):
        warnings.update(match.get("warnings", []))
    # Preserve the estimator's ordering, including work scope. Keep its score
    # below an existing direct numeric match (10); raw estimator scores are not
    # calibrated against lexical scores and must not outrank those accidentally.
    return dict(
        file=row["file"],
        physical=evidence.get("physical"),
        rank=row["rank"],
        score=9 + 1 / (row["rank"] + 1),
        score_kind="heuristic_not_probability",
        reasons=["estimated_pagination_candidate"],
        uncertainties=sorted(warnings),
        certified=False,
        snippet="",
        evidence=evidence,
    )


def pending_reason(payload):
    route = payload["route"]
    if route in {"pagination", "pagination_hypothesis"}:
        return (
            "numeric_without_map_candidate"
            if payload["numbers"]
            else "no_page_number_extracted"
        )
    if route.startswith("requires_namespace_map:"):
        return "internal_or_specialized_namespace"
    return route


def merge(plan_path, out):
    plan = json.loads(plan_path.read_text())
    if plan.get("version") != VERSION:
        raise ValueError("Unsupported recovery merge recipe")
    catalog = Path(plan["catalog"]).resolve()
    inputs = [(kind, Path(plan[kind]).resolve()) for kind in ("estimator", "relations")]
    if any(path == catalog for _, path in inputs):
        raise ValueError("Output catalog must differ from recovery inputs")
    provenance = {
        kind: {"path": str(path), "sha256": file_hash(path)} for kind, path in inputs
    }
    identity = {"version": VERSION, "catalog": str(catalog), "inputs": provenance}
    run_id = hashlib.sha256(encoded(identity).encode()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        sources = {
            kind: stack.enter_context(closing(readonly(path))) for kind, path in inputs
        }
        config = json.loads(
            sources["estimator"].execute("SELECT config_json FROM runs").fetchone()[0]
        )
        relation_summary = json.loads(
            sources["relations"]
            .execute("SELECT json FROM metadata WHERE key='summary'")
            .fetchone()[0]
        )
        if any(
            Path(c["catalog"]).resolve() != catalog for c in (config, relation_summary)
        ):
            raise ValueError("Recovery input belongs to a different catalog")
        db = stack.enter_context(closing(connect(catalog)))
        existing = db.execute(
            "SELECT json FROM metadata WHERE key=?", ("recovery_merge:" + run_id,)
        ).fetchone()
        if existing:
            summary = json.loads(existing[0])
        else:
            summary = apply(db, sources, run_id, identity)
        export_report(db, sources["estimator"], summary, out)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(encoded(summary), flush=True)
    return summary


def apply(db, sources, run_id, identity):
    """One atomic transaction: retries cannot leave half an overlay applied."""
    before = stats(db)
    counts = Counter()
    items = {
        r["id"]: dict(r)
        for r in db.execute("SELECT id,volume,label,ref_raw,source,state FROM items")
    }
    touched, gained = set(), {"estimator": set(), "relations": set()}
    paths = {}
    seen = {"estimator": set(), "relations": set()}
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS recovery_candidate_support(
            run_id TEXT,kind TEXT,item_id TEXT,file TEXT,eligible INTEGER,
            evidence_json BLOB,PRIMARY KEY(run_id,kind,item_id,file)) WITHOUT ROWID""")
        db.execute("""CREATE TABLE IF NOT EXISTS recovery_item_context(
            run_id TEXT,item_id TEXT,route TEXT,compatible INTEGER,payload_json BLOB,
            PRIMARY KEY(run_id,item_id)) WITHOUT ROWID""")
        compatible = set()
        warnings = {}
        for r in sources["estimator"].execute(
            "SELECT item_id,payload_json FROM results ORDER BY item_id"
        ):
            p = json.loads(r["payload_json"])
            item = items.get(r["item_id"])
            ok = bool(
                item and all(item[k] == p[k] for k in ("source", "label", "ref_raw"))
            )
            if ok:
                compatible.add(r["item_id"])
            else:
                counts["changed_or_missing_source_items"] += 1
            # Keep source-span warnings without turning a hash mismatch into a
            # search prohibition. Changed attribution is a different occurrence.
            warnings[r["item_id"]] = p.get("source_evidence_warning")
            compact = {k: v for k, v in p.items() if k != "candidates"}
            db.execute(
                "INSERT INTO recovery_item_context VALUES(?,?,?,?,?)",
                (
                    run_id,
                    r["item_id"],
                    p["route"],
                    int(ok),
                    pack_json(encoded(compact)),
                ),
            )
        for kind in ("estimator", "relations"):
            for r in sources[kind].execute(
                "SELECT * FROM candidates ORDER BY item_id,rank,file"
            ):
                item = items.get(r["item_id"])
                if item is None:
                    raise ValueError(
                        "Candidate references missing catalog item: " + r["item_id"]
                    )
                # Relations were generated from the same catalog. If this item
                # also occurs in the estimator, enforce its literal snapshot.
                ok = (
                    r["item_id"] in compatible
                    if r["item_id"] in warnings
                    else kind == "relations"
                )
                c = (
                    numeric_candidate(r)
                    if kind == "estimator"
                    else json.loads(r["candidate_json"])
                )
                if c["file"] != r["file"]:
                    raise ValueError("Candidate payload/file mismatch")
                path = c["file"]
                if path not in paths:
                    paths[path] = Path(path).is_file()
                self_link = Path(path).resolve() == Path(item["source"]).resolve()
                c.setdefault("uncertainties", [])
                c["uncertainties"] = list(c["uncertainties"])
                if warnings.get(r["item_id"]):
                    c["uncertainties"].append(str(warnings[r["item_id"]]))
                if self_link:
                    c["uncertainties"].append("candidate_is_source_index_file")
                if not paths[path]:
                    c["uncertainties"].append("file_unavailable")
                if not ok:
                    c["uncertainties"].append(
                        "catalog_attribution_changed_since_recovery"
                    )
                c["uncertainties"] = sorted(set(c["uncertainties"]))
                c["navigable"] = bool(ok and paths[path] and not self_link)
                c["certified"] = False
                c["recovery_database"] = identity["inputs"][kind]["path"]
                payload = pack_json(encoded(c))
                db.execute(
                    "INSERT INTO recovery_candidate_support VALUES(?,?,?,?,?,?)",
                    (run_id, kind, r["item_id"], path, int(c["navigable"]), payload),
                )
                counts[kind + "_pairs"] += 1
                if not c["navigable"]:
                    counts[
                        (
                            "excluded_self_pairs"
                            if self_link
                            else (
                                "excluded_missing_file_pairs"
                                if not paths[path]
                                else "excluded_changed_item_pairs"
                            )
                        )
                    ] += 1
                    continue
                seen[kind].add(r["item_id"])
                old = db.execute(
                    "SELECT navigable,score FROM candidate_storage WHERE item_id=? AND file=?",
                    (r["item_id"], path),
                ).fetchone()
                # Existing interpretations remain byte-for-byte intact unless
                # the prior file was nonnavigable. New evidence lives separately.
                if old is None:
                    db.execute(
                        "INSERT INTO candidate_storage VALUES(?,?,?,?,?,?)",
                        (r["item_id"], path, c["rank"], c["score"], 1, payload),
                    )
                    counts["new_pairs"] += 1
                elif not old["navigable"]:
                    db.execute(
                        "UPDATE candidate_storage SET rank=?,score=?,navigable=1,candidate_json=? WHERE item_id=? AND file=?",
                        (c["rank"], c["score"], payload, r["item_id"], path),
                    )
                    counts["reactivated_pairs"] += 1
                db.execute(
                    "INSERT OR IGNORE INTO candidate_origins VALUES(?,?,?)",
                    (r["item_id"], path, identity["inputs"][kind]["path"]),
                )
                touched.add(r["item_id"])
                if item["state"] != "with_destinations":
                    gained[kind].add(r["item_id"])
        db.executemany(
            "UPDATE items SET state='with_destinations' WHERE id=?",
            [(i,) for i in sorted(touched)],
        )
        after = stats(db)
        if (
            after["items"] != before["items"]
            or after["candidate_links"] < before["candidate_links"]
        ):
            raise ValueError("Catalog conservation failed")
        if after["with_destinations"] - before["with_destinations"] != len(
            gained["estimator"] | gained["relations"]
        ):
            raise ValueError("Candidate gain accounting mismatch")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Catalog quick_check failed")
        summary = {
            **identity,
            "run_id": run_id,
            "before": before,
            "after": after,
            "counts": dict(counts),
            "gains_by_layer": {k: len(v) for k, v in gained.items()},
            "overlapping_gains": len(gained["estimator"] & gained["relations"]),
            "code_sha256": file_hash(Path(__file__)),
            "canonical_updates": 0,
            "inference_calls": 0,
            "quality": "Search candidates, not certified destinations or measured precision.",
        }
        db.execute(
            "INSERT INTO metadata VALUES(?,?)",
            ("recovery_merge:" + run_id, encoded(summary)),
        )
    return summary


def export_report(db, estimator, summary, out):
    """Report the actual remaining population, not the old pre-merge queue."""
    pending = {
        r["id"]: dict(r)
        for r in db.execute(
            "SELECT id,volume,label,ref_raw,source FROM items WHERE state='source_only'"
        )
    }
    reasons, volumes = Counter(), Counter()
    with (out / "pending.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "volume", "label", "ref_raw", "source", "reason"]
        )
        writer.writeheader()
        for r in estimator.execute(
            "SELECT item_id,payload_json FROM results ORDER BY item_id"
        ):
            if r["item_id"] not in pending:
                continue
            item = pending.pop(r["item_id"])
            payload = json.loads(r["payload_json"])
            reason = pending_reason(payload)
            if payload.get("candidates"):
                reason = "candidates_not_eligible_after_merge"
            writer.writerow({**item, "reason": reason})
            reasons[reason] += 1
            volumes[item["volume"]] += 1
        for item in sorted(pending.values(), key=lambda i: i["id"]):
            writer.writerow({**item, "reason": "not_in_estimator_campaign"})
            reasons["not_in_estimator_campaign"] += 1
            volumes[item["volume"]] += 1
    current = stats(db)
    result = {
        **summary,
        "current": current,
        "pending_reasons": dict(reasons.most_common()),
        "pending_volumes": dict(volumes.most_common()),
    }
    (out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# Consolidação de candidatos numéricos e herança",
        "",
        "Dados experimentais pesquisáveis; contagens não medem acerto dos destinos.",
        "",
        "| Medida | Antes | Depois |",
        "| --- | ---: | ---: |",
    ]
    for k in ("items", "with_destinations", "pending", "candidate_links"):
        lines.append(f'| {k} | {summary["before"][k]} | {summary["after"][k]} |')
    lines += ["", "## Fila atual", "", "| Motivo | Itens |", "| --- | ---: |"]
    lines += [f"| {k} | {v} |" for k, v in reasons.most_common()]
    lines += [
        "",
        "Evidências completas em recovery_candidate_support; contexto em recovery_item_context.",
        "Apontamentos ao próprio índice, arquivos ausentes e atribuições alteradas ficam preservados fora da lista de destinos.",
        "Nenhuma alternativa anterior foi removida. Receita e hashes de insumos em summary.json.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    merge(args.plan, args.out)

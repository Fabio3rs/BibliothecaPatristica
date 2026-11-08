"""Retrieval benchmark; production databases are opened read-only, never migrated.

Labels are frozen before retrieval. Non-gold pages are unjudged distractors,
not assertions of semantic absence. No generative model judges the results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

from scripts.playgrounds.embedding_playground import (
    DEFAULT_MODEL, DEFAULT_OLLAMA_URL, get_detailed_instruct,
)
from tools.generate_ocr_embeddings import discover_source_pages
from tools.ocr_xml_utils import parse_ocr_xml_page
from tools.ocr_embedding_ir import repeated_running_fingerprints
from tools.resumo_embedding_utils import pick_page_text
from tools.indexing.retrieval_preparation import (
    PREPARATION_VERSION, OPEN_RANGE, label_audit, prepare_blocks, text_exclusion,
    page_units, block_groups, freeze_candidates, restore_candidates,
)

VERSION = "index-retrieval-v1"
RETRIEVAL_CUTOFFS = (1, 5, 10, 20, 30)
INSTRUCTION = (
    "Retrieve passages discussing the subject of this historical patristic index entry, "
    "including semantic equivalents and paraphrases across Latin and Greek."
)
ROOT = Path(__file__).resolve().parents[2]


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def fold(text):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text).casefold()
                            if not unicodedata.combining(c)).split())


def existing_file(value, volume, root):
    """Only actual paths supplied by the DB; never reconstruct a UUID filename."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file() or path.parent != (root / volume / "text").resolve():
        raise ValueError(f"Missing or out-of-volume OCR: {value}")
    return path


def load_labels(db, root, volumes=(), kinds=(), include_jev=False, ref_ids=()):
    exclusions = Counter()
    groups = {}
    incomplete_entries = set()
    with closing(readonly(db)) as con:
        query = """SELECT r.*, e.lemma_raw, e.entry_raw, e.context_raw,
                   e.section_key, e.entry_kind, s.volume_id, s.section_kind
                   FROM alphabetical_refs r JOIN alphabetical_entries e USING(entry_key)
                   JOIN alphabetical_sections s USING(section_key)
                   WHERE r.locator_status='resolved' ORDER BY s.volume_id,e.entry_key,r.ref_order"""
        for row in con.execute(query):
            r = dict(row)
            if volumes and r["volume_id"] not in volumes:
                continue
            if kinds and r["section_kind"] not in kinds:
                continue
            raw = json.loads(r["raw_json"])
            locator = raw.get("compact_locator", {})
            jev = raw.get("jev_validation") or locator.get("semantic_validation")
            if jev and not include_jev:
                exclusions["jev_label_excluded"] += 1
                incomplete_entries.add(r["entry_key"])
                continue
            if not jev and not locator.get("evidence"):
                exclusions["resolved_without_evidence"] += 1
                incomplete_entries.add(r["entry_key"])
                continue
            # Canonical schema stores a single target per occurrence. A range's
            # first file does not establish the complete set of relevant pages.
            if (r.get("range_end_raw") or r["ref_kind"] == "editorial_range"
                    or OPEN_RANGE.search(r.get("entry_raw") or "")
                    or OPEN_RANGE.search(r.get("ref_raw") or "")):
                exclusions["range_targets_not_exhaustively_verified"] += 1
                incomplete_entries.add(r["entry_key"])
                continue
            if not r["lemma_raw"] or not any(c.isalpha() for c in r["lemma_raw"]) or r["entry_kind"] not in {"lemma", "sublemma"}:
                exclusions["not_a_concept"] += 1
                continue
            try:
                path = existing_file(r["target_file"] or "", r["volume_id"], root)
            except ValueError:
                exclusions["target_missing_or_wrong_volume"] += 1
                incomplete_entries.add(r["entry_key"])
                continue
            sha = digest(path.read_bytes())
            if jev and jev.get("ocr_sha256") and jev["ocr_sha256"] != sha:
                exclusions["changed_verified_ocr"] += 1
                incomplete_entries.add(r["entry_key"])
                continue
            key = r["entry_key"]
            group = groups.setdefault(key, {
                "query_id": key, "entry_key": key, "volume": r["volume_id"],
                "section_key": r["section_key"], "section_kind": r["section_kind"],
                "query": r["lemma_raw"], "entry_raw": r["entry_raw"],
                "context_raw": r["context_raw"], "references": [], "gold_files": {},
            })
            group["gold_files"][str(path)] = sha
            group["references"].append({
                "ref_id": r["ref_id"], "ref_raw": r["ref_raw"], "target_file": str(path),
                "source_file": r["editorial_anchor_file"],
                "label_origin": "previous_jev" if jev else "locator_evidence",
                "record_sha256": digest(dump(r)), "evidence": locator,
            })
    # Do not present an incomplete known-positive set as exhaustive: if a
    # resolved occurrence was rejected, exclude its whole concept from metrics.
    for key in incomplete_entries:
        if key in groups:
            exclusions["concept_with_excluded_resolved_occurrence"] += 1
            del groups[key]
    # Preserve entry identity: identical lemmas (e.g. "Ejus libri") can belong
    # to different authors. Union occurrences only within the same DB entry.
    for group in groups.values():
        group["entry_keys"] = [group["entry_key"]]
        origins = {r["label_origin"] for r in group["references"]}
        group["label_origin"] = next(iter(origins)) if len(origins) == 1 else "mixed"
    labels = sorted(groups.values(), key=lambda q: q["query_id"])
    if ref_ids:
        labels = [q for q in labels if set(ref_ids) & {r["ref_id"] for r in q["references"]}]
        found = {r["ref_id"] for q in labels for r in q["references"]}
        if not set(ref_ids) <= found:
            raise ValueError(f"Requested references are not eligible: {sorted(set(ref_ids) - found)}")
    return labels, dict(exclusions)


def sample_labels(labels, size, seed):
    """Round-robin volumes so a single large index does not consume the sample."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for q in labels:
        groups[q["volume"]].append(q)
    keys = sorted(groups)
    rng.shuffle(keys)
    for group in groups.values():
        rng.shuffle(group)
    result = []
    while any(groups.values()) and len(result) < size:
        for key in keys:
            if groups[key] and len(result) < size:
                result.append(groups[key].pop())
    return result


def audit_label_sources(query, root):
    audit = label_audit(query)
    audit["source_excerpts"] = []
    sources = sorted({r["source_file"] for r in query.get("references", []) if r.get("source_file")})
    for source in sources:
        try:
            path = existing_file(source, query["volume"], root)
            content = path.read_bytes()
            text = content.decode("utf-8")
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            audit["source_excerpts"].append({"path": source, "error": str(exc)})
            audit["warnings"].append("index_source_unavailable")
            continue
        needle = query["query"]
        matches, start = [], 0
        while len(matches) < 3:
            position = text.find(needle, start)
            if position < 0:
                break
            a, b = max(0, position - 250), min(len(text), position + len(needle) + 250)
            matches.append({"start": a, "end": b, "quote": text[a:b]})
            start = position + len(needle)
        audit["source_excerpts"].append({"path": str(path), "ocr_sha256": digest(content),
                                         "offset_space": "original_ocr_file_python_characters",
                                         "matches": matches, "maximum_matches": 3,
                                         "query_found_literally": bool(matches)})
    return audit


def load_pages(root, volumes, cleaning="xml-v2"):
    pages = {}
    for volume in sorted(volumes):
        parsed = []
        for choice in discover_source_pages(root, volume):
            path = choice.path.resolve()
            raw_bytes = path.read_bytes()
            ocr = parse_ocr_xml_page(raw_bytes.decode("utf-8"), repair_non_xml_ampersands=True)
            parsed.append((choice, path, digest(raw_bytes), ocr))
        repeated = (repeated_running_fingerprints([p[3] for p in parsed], min_pages=3, min_ratio=0)
                    if cleaning in {"xml-v1", "xml-v2"} else set())
        for choice, path, ocr_sha256, ocr in parsed:
            text, blocks = prepare_blocks(ocr, cleaning, repeated)
            excluded = text_exclusion(text)
            if excluded == "empty_text" and any(b["text"].strip() for b in blocks):
                excluded = ("no_substantive_text_after_xml_cleaning"
                            if any((b["excluded_reason"] or "").startswith("xml_") for b in blocks)
                            else "placeholder_or_nonlinguistic_text")
            pages[str(path)] = {
                "path": str(path), "volume": volume, "physical_page": choice.page_num,
                "ocr_sha256": ocr_sha256, "text_sha256": digest(text),
                "text": text, "parse_ok": ocr.parse_ok, "is_xml": ocr.is_xml,
                "blocks": blocks, "preparation_version": PREPARATION_VERSION,
                "excluded_reason": excluded,
                "cleaning_policy": cleaning,
            }
    return pages


def cleaning_report(pages):
    by_volume = {}
    for page in pages.values():
        row = by_volume.setdefault(page["volume"], {"pages": 0, "blocks": 0,
                    "excluded_blocks": Counter(), "removed_chars": 0, "events": []})
        row["pages"] += 1
        for block in page["blocks"]:
            row["blocks"] += 1
            if block["excluded_reason"]:
                row["excluded_blocks"][block["excluded_reason"]] += 1
                row["removed_chars"] += len(block["text"])
                row["events"].append({"path": page["path"], "physical_page": page["physical_page"],
                                      "block_id": block["block_id"], "type": block["type"],
                                      "reason": block["excluded_reason"],
                                      "text_sha256": block["text_sha256"], "preview": block["text"][:240]})
    return by_volume


def candidate_plan(query, pages, args):
    gold = set(query["gold_files"])
    volume_pages = sorted(p for p, d in pages.items() if d["volume"] == query["volume"]
                          and not d.get("excluded_reason") and not text_exclusion(d["text"]))
    if not gold <= set(volume_pages):
        raise ValueError(f"Gold page missing, excluded, empty, or not canonically discoverable: {query['query_id']}")
    for p in gold:
        if pages[p]["ocr_sha256"] != query["gold_files"][p]:
            raise ValueError(f"Gold OCR changed since label snapshot: {p}")
    reasons = {p: ["gold"] for p in sorted(gold)}
    if args.scope == "volume":
        for p in volume_pages:
            reasons.setdefault(p, ["full_volume"])
        return {"base": reasons, "hard_pool": [], "random_count": 0}
    gold_numbers = {pages[p]["physical_page"] for p in gold}
    for p in volume_pages:
        if p not in gold and any(abs(pages[p]["physical_page"] - n) <= args.neighbors for n in gold_numbers):
            reasons[p] = ["neighbor"]
    rng = random.Random(f"{args.seed}:{query['query_id']}")
    random_files = rng.sample([p for p in volume_pages if p not in gold],
                              min(args.random_pages, len(volume_pages) - len(gold)))
    for p in random_files:
        reasons.setdefault(p, []).append("random")
    remaining = [p for p in volume_pages if p not in reasons]
    pool = rng.sample(remaining, min(args.hard_pool_size, len(remaining))) if args.hard_negatives else []
    return {"base": reasons, "hard_pool": pool, "random_count": len(random_files)}


class ExperimentDB:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, timeout=30)
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names and "retrieval_meta" not in names:
            self.con.close()
            raise ValueError("Refusing to use an existing non-experiment database")
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=30000")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS retrieval_meta (version TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS embeddings (
                cache_key TEXT PRIMARY KEY, config_json TEXT NOT NULL,
                text_sha256 TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                config_json TEXT NOT NULL, summary_json TEXT);
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT NOT NULL, kind TEXT NOT NULL, artifact_key TEXT NOT NULL,
                data_json TEXT NOT NULL, PRIMARY KEY(run_id,kind,artifact_key));
            CREATE TABLE IF NOT EXISTS http_batches (
                run_id TEXT NOT NULL, data_json TEXT NOT NULL);
        """)
        versions = [r[0] for r in self.con.execute("SELECT version FROM retrieval_meta")]
        if versions and versions != [VERSION]:
            self.con.close()
            raise ValueError("Unsupported experiment DB version")
        self.con.execute("INSERT OR IGNORE INTO retrieval_meta VALUES (?)", (VERSION,))
        self.con.commit()

    def artifact(self, run, kind, key, data):
        self.con.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)", (run, kind, key, dump(data)))
        self.con.commit()

    def close(self):
        self.con.close()


class OllamaCache:
    def __init__(self, db, run_id, args, session=None):
        self.db, self.run_id, self.args = db, run_id, args
        self.session = session or requests.Session()
        self.url = args.ollama_url.rstrip("/")
        if not self.url.endswith("/api/embed"):
            self.url += "/api/embed"
        response = self.session.get(self.url.removesuffix("/embed") + "/tags", timeout=args.timeout)
        response.raise_for_status()
        models = response.json().get("models", [])
        match = next((m for m in models if m.get("name") == args.model or m.get("model") == args.model), None)
        if not match or not match.get("digest"):
            raise ValueError(f"Model {args.model!r} not installed or lacks digest; no automatic pull")
        self.config = {"model": args.model, "model_digest": match["digest"],
                       "dimensions": args.dimensions, "endpoint": self.url,
                       "options": {"num_ctx": args.num_ctx}, "truncate": False, "version": VERSION}
        self.stats = Counter()

    def vectors(self, texts):
        keys = [digest(dump([self.config, digest(t)])) for t in texts]
        vectors, missing = {}, {}
        for key, text in zip(keys, texts):
            if key in vectors or key in missing:
                continue
            row = self.db.con.execute("SELECT vector FROM embeddings WHERE cache_key=?", (key,)).fetchone()
            if row:
                vector = np.frombuffer(row[0], dtype="<f4").copy()
                self._validate(vector)
                vectors[key] = vector
                self.stats["cache_hits"] += 1
            else:
                missing[key] = text
        items = list(missing.items())
        for start in range(0, len(items), self.args.batch_size):
            batch = items[start:start + self.args.batch_size]
            payload = {"model": self.args.model, "input": [t for _, t in batch],
                       "dimensions": self.args.dimensions, "truncate": False,
                       "options": self.config["options"]}
            result = None
            for attempt in range(self.args.retries + 1):
                began = time.monotonic()
                self.stats["http_calls"] += 1
                status = None
                try:
                    response = self.session.post(self.url, json=payload, timeout=self.args.timeout)
                    status = response.status_code
                    response.raise_for_status()
                    result = response.json()
                except requests.RequestException:
                    if attempt >= self.args.retries or (status is not None and status not in {429, 500, 502, 503, 504}):
                        raise
                finally:
                    record = {"attempt": attempt + 1, "items": len(batch), "http_status": status,
                              "latency_ms": (time.monotonic() - began) * 1000,
                              "input_hashes": [digest(t) for _, t in batch]}
                    if isinstance(result, dict):
                        record.update({k: result.get(k) for k in ("model", "total_duration", "prompt_eval_count")})
                    self.db.con.execute("INSERT INTO http_batches VALUES (?,?)", (self.run_id, dump(record)))
                    self.db.con.commit()
                if result is not None:
                    break
                time.sleep(min(8, 2 ** attempt))
            if not isinstance(result, dict) or result.get("model") != self.args.model:
                raise ValueError("Ollama returned an unexpected model")
            values = result.get("embeddings")
            if not isinstance(values, list) or len(values) != len(batch):
                raise ValueError("Ollama embedding count mismatch")
            validated = []
            for (key, text), value in zip(batch, values):
                vector = np.asarray(value, dtype=np.float32)
                self._validate(vector)
                vector /= np.linalg.norm(vector)
                validated.append((key, text, vector))
            with self.db.con:
                for key, text, vector in validated:
                    self.db.con.execute("INSERT OR IGNORE INTO embeddings VALUES (?,?,?,?,?)",
                                        (key, dump(self.config), digest(text), len(vector), vector.astype("<f4").tobytes()))
                    vectors[key] = vector
            self.stats["embedded_texts"] += len(batch)
            if self.args.verbose:
                print(dump({"embedding_progress": dict(self.stats)}), flush=True)
        return np.stack([vectors[key] for key in keys]) if keys else np.empty((0, self.args.dimensions))

    def _validate(self, vector):
        if vector.shape != (self.args.dimensions,) or not np.isfinite(vector).all() or np.linalg.norm(vector) <= 0:
            raise ValueError("Invalid embedding: dimensions, nonfinite values or zero norm")


class QwenChunks:
    def __init__(self, args):
        from transformers import AutoTokenizer
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, use_fast=True,
                                                           local_files_only=not args.download_tokenizer,
                                                           trust_remote_code=False)
        except OSError as exc:
            raise ValueError("Qwen tokenizer unavailable: provide --tokenizer LOCAL_PATH or explicitly "
                             "allow --download-tokenizer. Word counts are not model token counts.") from exc
        if not self.tokenizer.is_fast:
            raise ValueError("A fast tokenizer with character offsets is required")
        self.hash = digest(self.tokenizer.backend_tokenizer.to_str())
        self.size, self.overlap = args.chunk_tokens, args.overlap_tokens

    def chunks(self, text):
        encoded = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        offsets = encoded["offset_mapping"]
        result = []
        for start in range(0, len(offsets), self.size - self.overlap):
            end = min(start + self.size, len(offsets))
            a, b = offsets[start][0], offsets[end - 1][1]
            result.append({"text": text[a:b], "char_start": a, "char_end": b,
                           "token_start": start, "token_end": end})
            if end == len(offsets):
                break
        return result


def ranking_metrics(ranked, gold):
    ranks = {r["path"]: i for i, r in enumerate(ranked, 1)}
    if not gold or not set(gold) <= set(ranks):
        raise ValueError("All positive pages must participate in evaluation")
    positive_ranks = sorted(ranks[p] for p in gold)
    positive_scores = [r["score"] for r in ranked if r["path"] in gold]
    others = [r["score"] for r in ranked if r["path"] not in gold]
    return {"gold_ranks": {p: ranks[p] for p in sorted(gold)},
            "first_gold_rank": min(positive_ranks), "last_gold_rank": max(positive_ranks),
            "reciprocal_rank": 1 / min(positive_ranks),
            **{f"recall@{k}": sum(r <= k for r in positive_ranks) / len(gold) for k in RETRIEVAL_CUTOFFS},
            **{f"hit@{k}": int(min(positive_ranks) <= k) for k in RETRIEVAL_CUTOFFS},
            **{f"all_gold@{k}": int(max(positive_ranks) <= k) for k in RETRIEVAL_CUTOFFS},
            **{f"candidate_fraction@{k}": min(k, len(ranked)) / len(ranked) for k in RETRIEVAL_CUTOFFS},
            "margin_best_gold_vs_best_distractor": max(positive_scores) - max(others) if others else None,
            "margin_worst_gold_vs_best_distractor": min(positive_scores) - max(others) if others else None}


def evaluate(query, pages, candidates, client, chunker, variants, summaries,
             chunk_boundaries="block", include_context=False):
    query_modes = ["original", "instruct"]
    query_texts = [query["query"], get_detailed_instruct(INSTRUCTION, query["query"])]
    if include_context and (query.get("context_raw") or "").strip():
        contextual = query["context_raw"].strip() + "\n" + query["query"]
        query_modes += ["contextual", "contextual_instruct"]
        query_texts += [contextual, get_detailed_instruct(INSTRUCTION, contextual)]
    query_vectors = client.vectors(query_texts)
    results = []
    gold = set(query["gold_files"])
    for variant in variants:
        paths = sorted(candidates)
        if variant == "summary" and any(p not in summaries for p in paths):
            # Never improve apparent recall by removing candidates lacking summaries.
            results.append({"query_id": query["query_id"], "variant": variant,
                            "status": "summary_incomplete_candidate_set",
                            "missing_files": [p for p in paths if p not in summaries]})
            continue
        units, owners = [], []
        for path in paths:
            text = summaries[path]["text"] if variant == "summary" else pages[path]["text"]
            chunks = ([{"text": text, "char_start": 0, "char_end": len(text),
                        "char_offset_space": "summary_text_python_characters"}]
                      if variant == "summary" else page_units(
                          pages[path], chunker if variant == "chunks" else None, chunk_boundaries))
            for unit in chunks:
                owners.append(path)
                units.append(unit)
            if hasattr(client, "db"):
                client.db.artifact(client.run_id, "units", f"{path}:{variant}", {
                    "path": path, "variant": variant, "ocr_sha256": pages[path]["ocr_sha256"],
                    "units": [{**{k: v for k, v in u.items() if k != "text"},
                               "text_sha256": digest(u["text"])} for u in chunks]})
        matrix = client.vectors([u["text"] for u in units])
        scores = matrix @ query_vectors.T
        unit_counts = Counter(owners)
        unit_sizes = defaultdict(list)
        for owner, unit in zip(owners, units):
            unit_sizes[owner].append({"chars": len(unit["text"]), "tokens": unit.get("token_count")})
        for column, query_mode in enumerate(query_modes):
            best = {}
            for i, path in enumerate(owners):
                score = float(scores[i, column])
                if path not in best or score > best[path][0]:
                    best[path] = (score, i)
            ranked = []
            for path, (score, index) in sorted(best.items(), key=lambda item: (-item[1][0], item[0])):
                unit = units[index]
                ranked.append({"path": path, "physical_page": pages[path]["physical_page"], "score": score,
                               "cosine_distance": 1.0 - score,
                               "is_gold": path in gold, "candidate_reasons": candidates[path],
                               "unit_count": unit_counts[path],
                               "unit_sizes": unit_sizes[path],
                               "snippet": unit["text"][:500], "winning_unit": {k: v for k, v in unit.items() if k != "text"}})
            result = {"status": "evaluated", "query_id": query["query_id"], "query": query["query"],
                      "volume": query["volume"], "label_origin": query["label_origin"],
                      "variant": variant, "query_mode": query_mode,
                      "query_text_sent": query_texts[column],
                      "chunk_boundaries": chunk_boundaries if variant == "chunks" else None,
                      "preparation_version": PREPARATION_VERSION,
                      "candidate_count": len(paths), "gold_count": len(gold),
                      "literal_in_any_gold": any(fold(query["query"]) in fold(pages[p]["text"]) for p in gold),
                      "metrics": ranking_metrics(ranked, gold), "ranking": ranked}
            results.append(result)
    return results


def load_summaries(db, pages):
    result = {}
    if not db:
        return result
    with closing(readonly(db)) as con:
        for volume in sorted({p["volume"] for p in pages.values()}):
            by_num = {p["physical_page"]: key for key, p in pages.items() if p["volume"] == volume}
            for row in con.execute("SELECT * FROM resumos WHERE documento=? ORDER BY id", (volume,)):
                path = by_num.get(row["pagina_num"])
                if not path or Path(row["pagina_file"]).name != Path(path).name:
                    continue
                text = pick_page_text(row)
                if text:
                    if path in result:
                        raise ValueError(f"Multiple summaries for physical page: {path}")
                    result[path] = {"text": text, "summary_sha256": digest(text), "row_id": row["id"],
                                    "source": str(db.resolve()), "model": row["modelo"],
                                    "source_ocr_verified": False}
    return result


def aggregate(results):
    groups = defaultdict(list)
    for r in results:
        if r["status"] != "evaluated":
            continue
        for scope in ("all", "label:" + r["label_origin"], "literal:" + str(r["literal_in_any_gold"]), "volume:" + r["volume"]):
            groups[(r["variant"], r["query_mode"], scope)].append(r)
    out = []
    for (variant, query_mode, scope), rows in sorted(groups.items()):
        out.append({"variant": variant, "query_mode": query_mode, "stratum": scope, "queries": len(rows),
                    "MRR": float(np.mean([r["metrics"]["reciprocal_rank"] for r in rows])),
                    **{f"Recall@{k}": float(np.mean([r["metrics"][f"recall@{k}"] for r in rows])) for k in RETRIEVAL_CUTOFFS},
                    **{f"Hit@{k}": float(np.mean([r["metrics"][f"hit@{k}"] for r in rows])) for k in RETRIEVAL_CUTOFFS},
                    **{f"AllGold@{k}": float(np.mean([r["metrics"][f"all_gold@{k}"] for r in rows])) for k in RETRIEVAL_CUTOFFS},
                    **{f"CandidateFraction@{k}": float(np.mean([r["metrics"][f"candidate_fraction@{k}"] for r in rows])) for k in RETRIEVAL_CUTOFFS}})
    return out


def reports(out, results, summary):
    write_json(out / "results.json", results)
    write_json(out / "summary.json", summary)
    with (out / "rankings.csv").open("w", newline="") as f:
        fields = ["query_id", "query", "volume", "variant", "query_mode", "rank", "path", "physical_page", "is_gold", "score", "cosine_distance", "snippet", "unit_count"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            for rank, page in enumerate(r.get("ranking", []), 1):
                writer.writerow({**{k: r[k] for k in fields[:5]}, "rank": rank,
                                 **{k: page[k] for k in fields[6:]}})
    with (out / "metrics.csv").open("w", newline="") as f:
        rows = summary.get("metrics", [])
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# Experimento de recuperação de índices", "", f"Estado: {summary['status']}", "",
             "Rótulos preexistentes, não julgamentos produzidos neste experimento. "
             "Distratores não foram julgados negativos: uma página não citada pode tratar do mesmo tema. "
             "Recall mede recuperação dos arquivos citados conhecidos. Evidências do localizador não equivalem a revisão humana.", "",
             "Recall@k = fração dos arquivos positivos recuperados; Hit@k = pelo menos um positivo. "
             "AllGold@k = fração das consultas com todos os positivos conhecidos no corte. "
             "CandidateFraction@k = fração de candidatos retida. As médias são por consulta. "
             "MRR usa o primeiro positivo. Empates são ordenados pelo caminho físico.", "",
             "Os negativos difíceis são minerados com página inteira + consulta instruct; "
             "isso condiciona a amostra. Compare também o volume inteiro.", "",
             "| Variante | Consulta | N | Recall@1 | Recall@5 | Recall@10 | Recall@20 | Recall@30 | MRR |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m in summary.get("metrics", []):
        if m["stratum"] == "all":
            scores = " | ".join(f"{m[f'Recall@{k}']:.3f}" for k in RETRIEVAL_CUTOFFS)
            lines.append(f"| {m['variant']} | {m['query_mode']} | {m['queries']} | {scores} | {m['MRR']:.3f} |")
    lines += ["", "| Variante | Consulta | Corte | Hit | AllGold | Candidatos retidos |",
              "|---|---|---:|---:|---:|---:|"]
    for m in summary.get("metrics", []):
        if m["stratum"] == "all":
            for k in (20, 30):
                lines.append(f"| {m['variant']} | {m['query_mode']} | {k} | {m[f'Hit@{k}']:.3f} | "
                             f"{m[f'AllGold@{k}']:.3f} | {m[f'CandidateFraction@{k}']:.3f} |")
    for r in results:
        if r["status"] != "evaluated":
            lines.extend(["", f"Variante não avaliada: {r['query_id']} / {r['variant']}: {r['status']}"])
            continue
        metrics = r["metrics"]
        lines.extend(["", f"## {r['query_id']} — {r['variant']} / {r['query_mode']}", "",
                      r["query"], "", f"Primeiro positivo: rank {metrics['first_gold_rank']}; "
                      f"todos os ranks: {sorted(metrics['gold_ranks'].values())}; "
                      f"Hit@20/30: {metrics['hit@20']}/{metrics['hit@30']}; "
                      f"AllGold@20/30: {metrics['all_gold@20']}/{metrics['all_gold@30']}.", ""])
        selected = [p for i, p in enumerate(r["ranking"]) if i < 3 or p["is_gold"]]
        for p in selected:
            lines.extend([f"- Arquivo `{Path(p['path']).name}`; score {p['score']:.6f}; "
                          f"{'positivo conhecido' if p['is_gold'] else 'distrator não julgado'}; {p['unit_count']} unidades.",
                          "  " + p["snippet"].replace("\n", " ")])
    (out / "report.md").write_text("\n".join(lines) + "\n")


def run(args, client_factory=OllamaCache, chunker_factory=QwenChunks):
    args.root = args.root.resolve()
    if args.experiment_db.resolve() in {args.db.resolve(), *(p.resolve() for p in [args.summaries_db] if p)}:
        raise ValueError("Experiment database must differ from source databases")
    args.out.mkdir(parents=True, exist_ok=False)
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    run_id = uuid.uuid4().hex
    db = ExperimentDB(args.experiment_db)
    summary = {"run_id": run_id, "status": "preparing", "metrics": [], "version": VERSION}
    results = []
    db.con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (run_id, datetime.now(timezone.utc).isoformat(), "running", dump(config), None))
    db.con.commit()
    try:
        if args.sample_manifest:
            sample = json.loads(args.sample_manifest.read_text())
            if sample.get("version") != VERSION:
                raise ValueError("Incompatible sample manifest")
            queries = sample["queries"]
            if sample.get("snapshot_sha256") != digest(dump(queries)):
                raise ValueError("Sample manifest changed: positive labels or query snapshot differ")
            labels = queries
        else:
            labels, excluded = load_labels(args.db, args.root, args.volume, args.section_kind, args.include_jev_labels, args.ref_id)
            queries = sample_labels(labels, args.sample_size, args.seed)
            sample = {"version": VERSION, "seed": args.seed, "source_db": str(args.db.resolve()),
                      "eligible_concepts": len(labels), "exclusions": excluded, "queries": queries}
        if not queries:
            raise ValueError("No eligible concepts; inspect label policy/volume/section filters")
        if len({q["query_id"] for q in queries}) != len(queries):
            raise ValueError("Duplicate query IDs in sample")
        audits = [audit_label_sources(q, args.root) for q in queries]
        write_json(args.out / "label_audit.json", audits)
        for audit in audits:
            db.artifact(run_id, "label_audit", audit["query_id"], audit)
        blocked = {a["query_id"] for a in audits if not a["eligible"]}
        queries = [q for q in queries if q["query_id"] not in blocked]
        # Re-audit old frozen manifests; never silently retain a known open range.
        sample = {**sample, "queries": queries, "preparation_version": PREPARATION_VERSION,
                  "audit_excluded_queries": sorted(blocked)}
        if not queries:
            raise ValueError("No eligible concepts after label audit; inspect label_audit.json")
        pages = load_pages(args.root, {q["volume"] for q in queries}, args.text_cleaning)
        cleaning = cleaning_report(pages)
        write_json(args.out / "cleaning.json", cleaning)
        db.artifact(run_id, "cleaning", "report", cleaning)
        plans = {q["query_id"]: candidate_plan(q, pages, args) for q in queries}
        if args.candidate_manifest:
            plans = restore_candidates(json.loads(args.candidate_manifest.read_text()), queries, pages,
                                       args.scope, args.refresh_candidate_texts)
        summaries = load_summaries(args.summaries_db, pages)
        sample["snapshot_sha256"] = digest(dump(queries))
        write_json(args.out / "sample.json", sample)
        write_json(args.out / "config.json", {**config, "instruction": INSTRUCTION, "version": VERSION,
                                              "preparation_version": PREPARATION_VERSION})
        db.artifact(run_id, "config", "experiment", {**config, "instruction": INSTRUCTION, "version": VERSION})
        db.artifact(run_id, "sample", "snapshot", sample)
        write_json(args.out / "candidate_plan.json", plans)
        groups = {p: block_groups(pages[p], attach_titles=True)
                  for plan in plans.values() for p in set(plan["base"]) | set(plan["hard_pool"])} if args.chunk_boundaries == "block-context" else {}
        if groups:
            write_json(args.out / "block_groups.json", groups)
            db.artifact(run_id, "block_groups", "plan", groups)
        inventory = [{k: v for k, v in p.items() if k != "text"} for p in pages.values()]
        write_json(args.out / "sources.json", inventory)
        write_json(args.out / "summaries.json", {p: {k: v for k, v in s.items() if k != "text"} for p, s in summaries.items()})
        for q in queries:
            db.artifact(run_id, "query", q["query_id"], q)
        for p in inventory:
            db.artifact(run_id, "source", p["path"], p)
        summary.update({"queries": len(queries), "volume_pages": len(pages),
                        "eligible_concepts": sample["eligible_concepts"], "label_exclusions": sample["exclusions"],
                        "scope": args.scope, "sample_sha256": sample["snapshot_sha256"]})
        summary.update(preparation_version=PREPARATION_VERSION, audit_excluded_queries=sorted(blocked),
                       excluded_pages=dict(Counter(p["excluded_reason"] for p in pages.values() if p["excluded_reason"])),
                       labels_human_reviewed=0,
                       missing_query_context=sum(not (q.get("context_raw") or "").strip() for q in queries),
                       chunk_boundaries=args.chunk_boundaries)
        summary["title_context_groups"] = dict(Counter(g["title_context"] for gs in groups.values() for g in gs))
        summary.update(text_cleaning=args.text_cleaning,
                       cleaning={v: {k: x for k, x in r.items() if k != "events"} for v, r in cleaning.items()},
                       refreshed_candidate_texts=len({r["path"] for p in plans.values() for r in p.get("text_refresh", [])}))
        if args.no_api:
            summary.update(status="prepared_no_api", embedding_calls=0,
                           hard_mining_pending=any(plan["hard_pool"] for plan in plans.values()))
            for plan in plans.values():
                if not plan["hard_pool"]:
                    plan["selected"] = plan["base"]
        else:
            chunker = chunker_factory(args) if "chunks" in args.variants else None
            client = client_factory(db, run_id, args)
            embedding_config = {**client.config, "tokenizer_sha256": chunker.hash if chunker else None}
            write_json(args.out / "embedding_config.json", embedding_config)
            db.artifact(run_id, "config", "embedding", embedding_config)
            for q in queries:
                plan = plans[q["query_id"]]
                candidates = dict(plan["base"])
                if plan["hard_pool"]:
                    pool = plan["hard_pool"]
                    query_vector = client.vectors([get_detailed_instruct(INSTRUCTION, q["query"])])[0]
                    scores = client.vectors([pages[p]["text"] for p in pool]) @ query_vector
                    ranked_pool = sorted(zip(pool, map(float, scores)), key=lambda x: (-x[1], x[0]))
                    plan["hard_scores"] = ranked_pool
                    for p, score in ranked_pool[:args.hard_negatives]:
                        candidates[p] = ["semantic_hard_unjudged"]
                plan["selected"] = candidates
                db.artifact(run_id, "candidates", q["query_id"], plan)
                write_json(args.out / "candidate_manifest.json", freeze_candidates(queries, plans, pages, args.scope))
                observations = evaluate(q, pages, candidates, client, chunker, args.variants, summaries,
                                        args.chunk_boundaries, args.include_context)
                results.extend(observations)
                for r in observations:
                    db.artifact(run_id, "result", f"{q['query_id']}:{r['variant']}:{r.get('query_mode', '')}", r)
                summary["embedding_stats"] = dict(client.stats)
                summary["metrics"] = aggregate(results)
                reports(args.out, results, {**summary, "status": "running"})
                print(dump({"completed_concepts": sum(r.get("query_mode") == "original" and r["variant"] == args.variants[0] for r in results),
                            "query_id": q["query_id"], **dict(client.stats)}), flush=True)
            write_json(args.out / "candidate_plan.json", plans)
            summary["status"] = "completed"
        write_json(args.out / "candidate_manifest.json", freeze_candidates(queries, plans, pages, args.scope))
        write_json(args.out / "candidate_plan.json", plans)
    except BaseException as exc:
        summary.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        reports(args.out, results, summary)
        db.con.execute("UPDATE runs SET status=?,summary_json=? WHERE run_id=?", (summary["status"], dump(summary), run_id))
        db.con.commit()
        db.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=ROOT / "data/alphabetical_indices.db")
    p.add_argument("--root", type=Path, default=ROOT / "teste")
    p.add_argument("--experiment-db", type=Path, default=ROOT / "data/index_retrieval/experiments.sqlite")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--volume", action="append", default=[])
    p.add_argument("--section-kind", action="append", default=[])
    p.add_argument("--ref-id", action="append", type=int, default=[], help="Select a known reference; keep all positives of its concept")
    p.add_argument("--include-jev-labels", action="store_true")
    p.add_argument("--sample-size", type=int, default=20)
    p.add_argument("--sample-manifest", type=Path, help="Reuse exact concepts/positive pages from a previous run")
    p.add_argument("--candidate-manifest", type=Path, help="Freeze final candidate pages; verify query and source hashes, skip hard mining")
    p.add_argument("--refresh-candidate-texts", action="store_true",
                   help="Explicitly refresh prepared-text hashes, preserving candidates and original OCR hashes")
    p.add_argument("--text-cleaning", choices=["legacy-v2", "xml-v1", "xml-v2"], default="xml-v2",
                   help="Filter isolated marginal markers and repeated running headers using XML roles")
    p.add_argument("--include-context", action="store_true", help="Also evaluate context_raw + lemma, where context is recorded; never infer it")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--scope", choices=["sampled", "volume"], default="sampled")
    p.add_argument("--random-pages", type=int, default=50)
    p.add_argument("--neighbors", type=int, default=2)
    p.add_argument("--hard-negatives", type=int, default=5)
    p.add_argument("--hard-pool-size", type=int, default=100)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--dimensions", type=int, default=4096)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--num-ctx", type=int, default=32768)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--chunk-tokens", type=int, default=250)
    p.add_argument("--overlap-tokens", type=int, default=50)
    p.add_argument("--chunk-boundaries", choices=["page", "block", "block-context"], default="block",
                   help="Block windows (default), titles with adjacent body (block-context), or page-wide windows")
    p.add_argument("--tokenizer", default="Qwen/Qwen3-Embedding-8B")
    p.add_argument("--download-tokenizer", action="store_true", help="Allow Hugging Face tokenizer download (no weights)")
    p.add_argument("--variants", nargs="+", choices=["page", "chunks", "summary"], default=["page", "chunks"])
    p.add_argument("--summaries-db", type=Path)
    p.add_argument("--no-api", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.refresh_candidate_texts and not args.candidate_manifest:
        p.error("--refresh-candidate-texts requires --candidate-manifest")
    for name in ("sample_size", "dimensions", "batch_size", "num_ctx", "timeout", "chunk_tokens"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            p.error(f"--{name.replace('_', '-')} must be positive and finite")
    if not 0 <= args.overlap_tokens < args.chunk_tokens:
        p.error("overlap must be nonnegative and smaller than chunk size")
    if min(args.random_pages, args.neighbors, args.hard_negatives, args.hard_pool_size, args.retries) < 0:
        p.error("sampling counts and retries must be nonnegative")
    if args.hard_pool_size < args.hard_negatives:
        p.error("hard pool must be at least as large as the requested hard negatives")
    if "summary" in args.variants and not args.summaries_db:
        p.error("--variants summary requires --summaries-db")
    try:
        run(args)
    except (ValueError, OSError, requests.RequestException) as exc:
        p.exit(1, f"Error: {exc}\n")

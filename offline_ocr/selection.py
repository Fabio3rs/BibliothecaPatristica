"""Manual, auditable selection of historical OCR versions for filesystem export.

The source database is always read-only. A scan creates an immutable proposal;
apply and rollback are explicit operations on that proposal, never on is_current.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from .diff_v2 import SUPPORTED, VERSION as ALIGNER_VERSION, compare
from .layout import parse_tsv, valid_box
from .migne_hints import VERSION as LAYOUT_VERSION, analyse_tsv
from .quality import BLOCK_TYPES, BLOCK_SCRIPTS, content_reference, content_profile, diagnostic_flags
from .store import engine_config, identity
from tools.corpus_utils import resolve_page_file

LEGACY_POLICY_VERSION = "ocr-version-selection-v1-experimental"
POLICY_VERSION = "ocr-version-selection-v2-experimental"
POLICY = {"version": POLICY_VERSION, "min_reference_confidence": 60,
          "min_reference_tokens": 20, "min_supported_fraction": .8,
          "min_improvement_tokens": 3, "profiles": ["profile", "content_profile"],
          "review_flags": ["spatial_alignment_confounded_check_boxes_and_reading_order",
                           "near_verbatim_tesseract_agreement_requires_image_review"],
          "risk_dimensions": ["missing_reference_tokens", "uncertain_reference_tokens",
                              "unmatched_candidate_tokens", "uncertain_candidate_tokens"]}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def read_only(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def journal(run_dir, event, **fields):
    with (Path(run_dir) / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(canonical({"time": now(), "event": event, **fields}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_bytes(path, data, mode=0o644):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_xml(text):
    try:
        root = ET.fromstring(text.strip())
        blocks = root.findall("bloco")
        if root.tag != "pagina" or root.get("estado") != "com_texto" or not blocks:
            return False
        if (root.text and root.text.strip()) or any(n.tag not in {"bloco", "notas"} or
                                                   (n.tail and n.tail.strip()) for n in root):
            return False
        return all("".join(b.itertext()).strip() and not list(b) and
                   b.get("tipo") in BLOCK_TYPES and b.get("script") in BLOCK_SCRIPTS for b in blocks)
    except (ET.ParseError, ValueError):
        return False


def _history(con, volume, page):
    return [dict(row) for row in con.execute(
        """SELECT r.*, e.engine, e.model, e.prompt_key FROM ocr_results r
           JOIN ocr_engine_versions e ON e.id=r.engine_version_id
           WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id""", (volume, page))]


def _history_fingerprint(rows):
    return identity([{**{k: row[k] for k in ("id", "image_hash", "text_hash", "engine_version_id", "is_current",
                                           "engine", "model", "prompt_key")},
                      "actual_text_hash": digest(row["text_content"].encode())}
                     for row in rows])


def _page_ids(con, volumes, pages, limit, after=None):
    conditions, values = [], []
    if volumes:
        conditions.append("volume_id IN (" + ",".join("?" for _ in volumes) + ")")
        values.extend(volumes)
    if pages:
        conditions.append("page_num IN (" + ",".join("?" for _ in pages) + ")")
        values.extend(pages)
    if after:
        volume, page = after.rsplit(":", 1)
        conditions.append("(volume_id, page_num) > (?, ?)")
        values.extend([volume, int(page)])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = "SELECT volume_id,page_num FROM ocr_results" + where + " GROUP BY volume_id,page_num ORDER BY volume_id,page_num LIMIT ?"
    yield from con.execute(query, [*values, limit])


def reference_tsv(image_path, cache_dir, config):
    """Cache original-image TSV by image, executable, model and all OCR settings."""
    from PIL import Image

    image_hash = digest(image_path.read_bytes())
    with Image.open(image_path) as image:
        dimensions = list(image.size)
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("multi_frame_image")
    key = identity({"image_hash": image_hash, "config": config})
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if (cached["key"] != key or cached["image_hash"] != image_hash or
                cached["tsv_sha256"] != digest(cached["tsv"].encode()) or cached["config"] != config):
            raise ValueError("reference_cache_integrity_error")
        raw = cached["tsv"]
    else:
        argv = ["tesseract", str(image_path), "stdout", "--tessdata-dir", config["tessdata"],
                "-l", config["lang"], "--psm", str(config["psm"]), "--oem", str(config["oem"])]
        if config["no_rejects"]:
            argv.extend(["-c", "tessedit_zero_kelvin_rejection=1", "-c", "textord_no_rejects=1"])
        env = dict(os.environ)
        env.setdefault("OMP_THREAD_LIMIT", "2")
        proc = subprocess.run([*argv, "tsv"], check=True, capture_output=True,
                              text=True, timeout=300, env=env)
        raw = proc.stdout
        cached = {"key": key, "image_hash": image_hash, "image_size": dimensions,
                  "tsv_sha256": digest(raw.encode()), "config": config, "tsv": raw,
                  "stderr": proc.stderr, "created_at": now()}
        if digest(image_path.read_bytes()) != image_hash:
            raise ValueError("image_changed_during_tesseract")
        parse_tsv(raw, *dimensions)
        _atomic_bytes(path, (canonical(cached) + "\n").encode())
    parse_tsv(raw, *dimensions)
    return raw, {"key": key, "path": str(path.resolve()), "image_hash": image_hash,
                 "image_size": dimensions, "tsv_sha256": digest(raw.encode()), "config": config}


def quality_profile(result, policy=POLICY):
    reliable = {t["id"] for t in result["reference_tokens"]
                if all(p["conf"] is not None and p["conf"] >= policy["min_reference_confidence"]
                       for p in t["pieces"])}
    reference_counts, candidate_counts = Counter(), Counter()
    for link in result["links"]:
        reference_counts[link["status"]] += len(set(link["reference_ids"]) & reliable)
        candidate_counts[link["status"]] += len(link["candidate_ids"])
    supported = sum(reference_counts[k] for k in SUPPORTED | {"reordered"})
    return {
        "reference_tokens": len(result["reference_tokens"]), "reliable_reference_tokens": len(reliable),
        "supported_reference_tokens": supported, "supported_fraction": supported / max(1, len(reliable)),
        "missing_reference_tokens": reference_counts["reference_only"],
        "uncertain_reference_tokens": reference_counts["uncertain_substitution"] + reference_counts["unresolved"],
        "unmatched_candidate_tokens": candidate_counts["candidate_only"],
        "uncertain_candidate_tokens": candidate_counts["uncertain_substitution"] + candidate_counts["unresolved"],
        "longest_omission_run": max((r["token_count"] for r in result["omission_candidates"]), default=0),
        "reference_status_counts": dict(reference_counts), "candidate_status_counts": dict(candidate_counts),
        "geometry_uncertain_links": sum(l["region_uncertain"] for l in result["links"]),
    }


def choose_version(candidates, current_hash, policy=POLICY):
    """Require one profile to dominate all alternatives; ties/tradeoffs need review."""
    if not candidates:
        return {"status": "review_required", "selected_id": None, "reasons": ["no_eligible_version"]}
    by_hash = {}
    for candidate in candidates:
        by_hash.setdefault(candidate["text_hash"], candidate)
    unique = list(by_hash.values())
    dimensions = policy["risk_dimensions"]

    def dominates(a, b):
        pairs = [(a[name][k], b[name][k]) for name in policy["profiles"] for k in dimensions]
        return all(left <= right for left, right in pairs) and any(left < right for left, right in pairs)

    if any(any(name not in c for name in policy["profiles"]) for c in unique):
        return {"status": "review_required", "selected_id": None, "reasons": ["missing_quality_diagnostics"]}

    winners = [a for a in unique if all(a is b or dominates(a, b) for b in unique)]
    if len(winners) != 1:
        undecided = [a["id"] for a in unique if not any(a is not b and dominates(b, a) for b in unique)]
        return {"status": "review_required", "selected_id": None,
                "undecided_ids": undecided, "dominated_ids": [a["id"] for a in unique if a["id"] not in undecided],
                "reasons": ["tie_or_tradeoff_between_quality_profiles"]}
    winner = winners[0]
    profile = winner["profile"]
    result = {"selected_id": winner["id"], "selected_text_hash": winner["text_hash"],
              "status": "review_required", "reasons": []}
    if profile["reliable_reference_tokens"] < policy["min_reference_tokens"]:
        result["reasons"].append("insufficient_reference_text")
    if profile["supported_fraction"] < policy["min_supported_fraction"]:
        result["reasons"].append("insufficient_reference_agreement")
    current = next((c for c in unique if c["text_hash"] == current_hash), None)
    if current_hash is not None and current is None:
        result["reasons"].append("filesystem_text_not_in_eligible_history")
    if result["reasons"]:
        return result
    if winner["text_hash"] == current_hash:
        result.update(status="keep_current", reasons=["filesystem_already_matches_best_profile"])
        return result
    result["reasons"].extend(flag for flag in winner.get("diagnostic_flags", []) if flag in policy["review_flags"])
    if winner.get("invalid_bbox_blocks"):
        result["reasons"].append("candidate_requires_bbox_review")
    if result["reasons"]:
        return result
    if current:
        improvements = {k: current["profile"][k] - profile[k] for k in dimensions}
        result["improvements_vs_filesystem"] = improvements
        content_improvements = {k: current["content_profile"][k] - winner["content_profile"][k] for k in dimensions}
        result["content_improvements_vs_filesystem"] = content_improvements
        if max(content_improvements.values()) < policy["min_improvement_tokens"]:
            result["reasons"].append("content_improvement_below_policy_minimum")
            return result
    result.update(status="recommended", reasons=["dominates_all_eligible_profiles" if len(unique) > 1
                  else "only_eligible_version_supported_by_reference",
                  "no_risk_dimension_worsens", "reference_support_meets_policy"])
    return result


def inspect_page(con, base_dir, volume, page, run_dir, reference_provider, policy=POLICY):
    if not re.fullmatch(r"(?:PG|PL|PO)\d+(?:[._-]\d+)*", volume) or page < 0:
        raise ValueError("invalid_corpus_page_identifier")
    image, image_ambiguous = resolve_page_file(base_dir / volume / "images", volume_id=volume,
                                             page_num=page, suffixes=(".png",))
    target, text_ambiguous = resolve_page_file(base_dir / volume / "text", volume_id=volume,
                                             page_num=page, suffixes=(".txt",))
    entry = {"page_key": f"{volume}:{page}", "volume_id": volume, "page_num": page,
             "status": "review_required", "selected_id": None, "reasons": [], "candidates": [], "excluded": []}
    if image_ambiguous or text_ambiguous or image is None:
        entry["reasons"] = ["ambiguous_files" if image_ambiguous or text_ambiguous else "image_missing"]
        return entry
    target = target or base_dir / volume / "text" / f"{volume}-{page:03d}.txt"
    if image.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(base_dir):
        raise ValueError("unsafe_source_path")
    current_hash = digest(target.read_bytes()) if target.exists() else None
    entry.update(target=str(target.resolve()), before_hash=current_hash, image_path=str(image.resolve()))
    raw_tsv, reference = reference_provider(image)
    reference = dict(reference)
    reference_dir = run_dir / "references"
    reference_dir.mkdir(exist_ok=True)
    snapshot = reference_dir / f"{volume}_{page}.tsv"
    snapshot.write_text(raw_tsv, encoding="utf-8")
    reference.update(snapshot=str(snapshot.relative_to(run_dir)), snapshot_sha256=digest(raw_tsv.encode()),
                     layout_version=LAYOUT_VERSION)
    entry["reference"] = reference
    ordered_reference = content_reference(analyse_tsv(raw_tsv, reference["image_size"]))
    history = _history(con, volume, page)
    entry["history_fingerprint"] = _history_fingerprint(history)
    for row in history:
        reason = None
        if row["engine"] == "tesseract":
            reason = "reference_engine_not_export_candidate"
        elif row["image_hash"] != reference["image_hash"]:
            reason = "different_image_hash"
        elif digest(row["text_content"].encode()) != row["text_hash"]:
            reason = "text_hash_mismatch"
        elif not _valid_xml(row["text_content"]):
            reason = "not_valid_nonempty_page_xml"
        if reason:
            entry["excluded"].append({"id": row["id"], "reason": reason})
            continue
        result = compare(row["text_content"], reference_tsv=raw_tsv, image_size=reference["image_size"])
        content_result = compare(row["text_content"], ordered_reference)
        evidence_path = run_dir / "evidence" / f"{volume}_{page}_{row['id']}.json"
        evidence_path.parent.mkdir(exist_ok=True)
        evidence_bytes = (canonical(result) + "\n").encode()
        evidence_path.write_bytes(evidence_bytes)
        content_path = evidence_path.with_name(evidence_path.stem + ".content.json")
        content_bytes = (canonical(content_result) + "\n").encode()
        content_path.write_bytes(content_bytes)
        profile, content = quality_profile(result, policy), content_profile(content_result)
        entry["candidates"].append({
            "id": row["id"], "engine": row["engine"], "model": row["model"],
            "created_at": row["created_at"], "is_current_in_db": bool(row["is_current"]),
            "text_hash": row["text_hash"], "profile": profile, "content_profile": content,
            "diagnostic_flags": diagnostic_flags(profile, content),
            "invalid_bbox_blocks": sum(valid_box(b.get("bbox")) is None for b in ET.fromstring(row["text_content"]).findall("bloco")),
            "evidence": str(evidence_path.relative_to(run_dir)), "evidence_sha256": digest(evidence_bytes),
            "content_evidence": str(content_path.relative_to(run_dir)), "content_evidence_sha256": digest(content_bytes),
        })
    entry.update(choose_version(entry["candidates"], current_hash, policy))
    if digest(image.read_bytes()) != reference["image_hash"]:
        raise ValueError("image_changed_during_scan")
    return entry


def scan(*, db_path, base_dir, run_dir, volumes=None, pages=None, limit=25, after=None, cache_dir=None,
         lang="migne", psm=3, oem=1, tessdata=None, no_rejects=False, reference_provider=None):
    if limit < 1:
        raise ValueError("limit must be positive")
    if after and not re.fullmatch(r"(?:PG|PL|PO)\d+(?:[._-]\d+)*:\d+", after):
        raise ValueError("after must be a page cursor such as PG001:638")
    db_path, base_dir, run_dir = Path(db_path).resolve(), Path(base_dir).resolve(), Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    journal(run_dir, "scan_started", db_path=str(db_path), base_dir=str(base_dir),
            volumes=volumes, pages=pages, limit=limit, after=after, policy=POLICY, aligner=ALIGNER_VERSION)
    if reference_provider is None:
        try:
            config = {**engine_config(lang, psm, oem, tessdata), "no_rejects": bool(no_rejects)}
        except Exception as error:
            journal(run_dir, "scan_failed", reason=str(error))
            raise
        cache_dir = Path(cache_dir or run_dir.parent / "reference-cache").resolve()
        reference_provider = lambda image: reference_tsv(image, cache_dir, config)
    statuses = Counter()
    con = read_only(db_path)
    try:
        page_ids = list(_page_ids(con, volumes, pages, limit + 1, after))
        more_available = len(page_ids) > limit
        page_ids = page_ids[:limit]
        with (run_dir / "proposals.jsonl").open("x", encoding="utf-8") as proposals:
            for volume, page in page_ids:
                try:
                    entry = inspect_page(con, base_dir, volume, page, run_dir, reference_provider)
                except Exception as error:
                    entry = {"page_key": f"{volume}:{page}", "volume_id": volume, "page_num": page,
                             "status": "error", "selected_id": None,
                             "reasons": [type(error).__name__ + ": " + str(error)]}
                statuses[entry["status"]] += 1
                proposals.write(canonical(entry) + "\n")
                proposals.flush()
                journal(run_dir, "page_evaluated", page_key=entry["page_key"],
                        status=entry["status"], selected_id=entry["selected_id"], reasons=entry["reasons"])
    finally:
        con.close()
    manifest = {"schema": "ocr-selection-run-v1", "created_at": now(), "db_path": str(db_path),
                "base_dir": str(base_dir), "policy": POLICY, "aligner": ALIGNER_VERSION,
                "scope": {"volumes": volumes, "pages": pages, "limit": limit, "after": after}, "summary": dict(statuses),
                "more_available": more_available,
                "next_after": f"{page_ids[-1][0]}:{page_ids[-1][1]}" if more_available else None,
                "proposals_sha256": digest((run_dir / "proposals.jsonl").read_bytes()),
                "quality_warning": "Heuristic relative ranking, not verified transcription quality. Manual execution only."}
    (run_dir / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    entries = [json.loads(line) for line in (run_dir / "proposals.jsonl").read_text().splitlines()]
    (run_dir / "report.txt").write_text(render_report(manifest, entries), encoding="utf-8")
    journal(run_dir, "scan_completed", summary=dict(statuses), proposals_sha256=manifest["proposals_sha256"])
    return manifest


def render_report(manifest, entries):
    lines = ["Seleção manual de versões OCR", "",
             "Ranking heurístico relativo ao Tesseract; não é certificação da transcrição.",
             f"Banco (somente leitura): {manifest['db_path']}",
             f"Política: {manifest['policy']['version']} / {manifest['aligner']}",
             f"Escopo: {canonical(manifest['scope'])}", f"Resumo: {canonical(manifest['summary'])}",
             f"Próximo lote (--after): {manifest.get('next_after')}", ""]
    for entry in entries:
        lines.extend([f"Página {entry['page_key']}: {entry['status']}",
                      f"Versão proposta: {entry.get('selected_id')}",
                      f"Destino: {entry.get('target', 'não resolvido')}",
                      f"Motivos: {', '.join(entry['reasons'])}"])
        if entry.get("undecided_ids"):
            lines.append("Candidatos entre os quais a política não decide: " + canonical(entry["undecided_ids"]))
            lines.append("Candidatos superados nos dois perfis: " + canonical(entry["dominated_ids"]))
        if entry.get("improvements_vs_filesystem"):
            lines.append("Reduções frente ao arquivo atual: " + canonical(entry["improvements_vs_filesystem"]))
        lines.append("ID | modelo | referência sem par | referência incerta | candidato sem par | candidato incerto")
        for candidate in entry.get("candidates", []):
            profile = candidate["profile"]
            lines.append(" | ".join(str(x) for x in [candidate["id"], candidate["model"] or candidate["engine"],
                         *[profile[k] for k in manifest["policy"]["risk_dimensions"]]]))
            lines.append(f"  Evidência: {candidate['evidence']}")
            if candidate.get("content_profile"):
                lines.append("  Conteúdo (todas as palavras, sem usar caixas da resposta): " +
                             canonical({k: candidate["content_profile"][k] for k in manifest["policy"]["risk_dimensions"]}))
                lines.append("  Diagnósticos: " + canonical(candidate.get("diagnostic_flags", [])))
        if entry.get("excluded"):
            lines.append("Versões excluídas: " + canonical(entry["excluded"]))
        lines.append("")
    lines.extend(["O scan não modifica o corpus. apply usa apenas status recommended.",
                  "Empates, conflitos, fonte insuficiente e alterações locais ficam para revisão.",
                  "O banco e is_current permanecem inalterados, inclusive em apply/rollback.", ""])
    return "\n".join(lines)


def report_run(run_dir):
    manifest, entries = _load_run(Path(run_dir).resolve())
    return render_report(manifest, entries)


def _load_run(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    raw = (run_dir / "proposals.jsonl").read_bytes()
    if digest(raw) != manifest["proposals_sha256"]:
        raise ValueError("proposal_integrity_error")
    if manifest["schema"] != "ocr-selection-run-v1" or manifest["policy"]["version"] not in {POLICY_VERSION, LEGACY_POLICY_VERSION}:
        raise ValueError("unsupported_run_version")
    entries = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    return manifest, entries


@contextmanager
def _operation(run_dir):
    """Different runs exporting to the same corpus share an advisory directory lock."""
    with (run_dir / ".operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest, entries = _load_run(run_dir)
        corpus_fd = os.open(manifest["base_dir"], os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(corpus_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _events(run_dir)
            yield manifest, entries
        finally:
            os.close(corpus_fd)


def _reference_snapshot(run_dir, entry):
    path = (run_dir / entry["reference"]["snapshot"]).resolve()
    if not path.is_relative_to(run_dir / "references"):
        raise ValueError("unsafe_reference_path")
    raw = path.read_bytes()
    if digest(raw) != entry["reference"]["snapshot_sha256"]:
        raise ValueError("reference_changed")
    return raw.decode("utf-8")


def _check_evidence(run_dir, candidate):
    results = []
    for field in ("evidence", "content_evidence"):
        path = (run_dir / candidate[field]).resolve()
        if not path.is_relative_to(run_dir / "evidence"):
            raise ValueError("unsafe_evidence_path")
        raw = path.read_bytes()
        if digest(raw) != candidate[field + "_sha256"]:
            raise ValueError("evidence_changed")
        result = json.loads(raw)
        if result["source_hashes"]["candidate"] != candidate["text_hash"]:
            raise ValueError("evidence_source_mismatch")
        results.append(result)
    spatial, content = quality_profile(results[0]), content_profile(results[1])
    if (spatial != candidate["profile"] or content != candidate["content_profile"] or
            diagnostic_flags(spatial, content) != candidate["diagnostic_flags"]):
        raise ValueError("quality_profile_changed")
    return results


def _events(run_dir):
    """A crash may leave only the final journal record incomplete."""
    lines = (run_dir / "events.jsonl").read_bytes().splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                with (run_dir / "events.jsonl").open("ab") as stream:
                    stream.write(b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        except (ValueError, UnicodeDecodeError):
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise ValueError("journal_corruption") from None
            backup = run_dir / ("journal-tail-" + digest(line) + ".bin")
            _atomic_bytes(backup, line)
            with (run_dir / "events.jsonl").open("r+b") as stream:
                stream.truncate(sum(len(part) for part in lines[:index]))
                stream.flush()
                os.fsync(stream.fileno())
            journal(run_dir, "journal_tail_recovered", backup=backup.name, bytes=len(line))
    return events


def _target(entry, manifest):
    base = Path(manifest["base_dir"]).resolve()
    target = Path(entry["target"])
    if target.is_symlink() or not target.resolve().is_relative_to(base):
        raise ValueError("unsafe_target_path")
    expected, ambiguous = resolve_page_file(base / entry["volume_id"] / "text",
                                            volume_id=entry["volume_id"], page_num=entry["page_num"], suffixes=(".txt",))
    if ambiguous or (expected is not None and expected.resolve() != target.resolve()):
        raise ValueError("target_resolution_changed")
    return target


def apply_run(run_dir):
    """Apply only recommendations, checking the frozen plan and current sources."""
    run_dir = Path(run_dir).resolve()
    with _operation(run_dir) as (manifest, entries):
        if manifest["policy"] != POLICY or manifest["aligner"] != ALIGNER_VERSION:
            raise ValueError("policy_changed_rescan_required")
        con = read_only(manifest["db_path"])
        pending = []
        try:
            for entry in entries:
                if entry["status"] != "recommended":
                    continue
                recomputed = choose_version(entry["candidates"], entry["before_hash"])
                if (recomputed["status"] != "recommended" or recomputed["selected_id"] != entry["selected_id"] or
                        recomputed["selected_text_hash"] != entry["selected_text_hash"]):
                    raise ValueError("selection_decision_changed")
                _reference_snapshot(run_dir, entry)
                target = _target(entry, manifest)
                before = target.read_bytes() if target.exists() else None
                current = digest(before) if before is not None else None
                if current == entry["selected_text_hash"]:
                    journal(run_dir, "already_matches", page_key=entry["page_key"])
                    continue
                if current != entry["before_hash"]:
                    raise ValueError(f"filesystem_changed:{entry['page_key']}")
                image = Path(entry["image_path"])
                if digest(image.read_bytes()) != entry["reference"]["image_hash"]:
                    raise ValueError(f"image_changed:{entry['page_key']}")
                if _history_fingerprint(_history(con, entry["volume_id"], entry["page_num"])) != entry["history_fingerprint"]:
                    raise ValueError(f"history_changed:{entry['page_key']}")
                row = con.execute("SELECT * FROM ocr_results WHERE id=?", (entry["selected_id"],)).fetchone()
                if (row is None or row["volume_id"] != entry["volume_id"] or row["page_num"] != entry["page_num"]
                        or row["image_hash"] != entry["reference"]["image_hash"]
                        or row["text_hash"] != entry["selected_text_hash"]
                        or digest(row["text_content"].encode()) != entry["selected_text_hash"]
                        or not _valid_xml(row["text_content"])):
                    raise ValueError(f"selected_version_changed:{entry['page_key']}")
                for candidate in entry["candidates"]:
                    _check_evidence(run_dir, candidate)
                pending.append((entry, target, before, row["text_content"].encode()))
        except Exception as error:
            journal(run_dir, "apply_preflight_failed", reason=str(error))
            raise
        finally:
            con.close()
        for entry, target, before, after in pending:
            target = _target(entry, manifest)
            current = digest(target.read_bytes()) if target.exists() else None
            if current != entry["before_hash"]:
                journal(run_dir, "apply_conflict", page_key=entry["page_key"])
                raise ValueError(f"filesystem_changed:{entry['page_key']}")
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
            backup = None
            if before is not None:
                backup = run_dir / "backups" / f"{entry['volume_id']}_{entry['page_num']}_{entry['before_hash']}.txt"
                backup.parent.mkdir(exist_ok=True)
                if backup.exists() and backup.read_bytes() != before:
                    raise ValueError("backup_integrity_error")
                if not backup.exists():
                    _atomic_bytes(backup, before, mode)
            journal(run_dir, "write_intent", page_key=entry["page_key"], target=str(target),
                    before_hash=entry["before_hash"], after_hash=entry["selected_text_hash"],
                    backup=str(backup.relative_to(run_dir)) if backup else None,
                    original_mode=mode, selected_id=entry["selected_id"], reasons=entry["reasons"])
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_bytes(target, after, mode)
            journal(run_dir, "written", page_key=entry["page_key"], selected_id=entry["selected_id"],
                    after_hash=entry["selected_text_hash"])
        journal(run_dir, "apply_completed", written=len(pending))
        return {"written": len(pending)}


def rollback_run(run_dir):
    """Restore only this run's writes, refusing to discard later filesystem edits."""
    run_dir = Path(run_dir).resolve()
    with _operation(run_dir) as (manifest, entries):
        by_key = {e["page_key"]: e for e in entries}
        events = _events(run_dir)
        intents = {e["page_key"]: e for e in events if e["event"] == "write_intent"}
        pending = []
        try:
            for key, intent in intents.items():
                entry = by_key[key]
                target = _target(entry, manifest)
                current = digest(target.read_bytes()) if target.exists() else None
                if current == intent["before_hash"]:
                    continue
                if current != intent["after_hash"]:
                    raise ValueError(f"filesystem_changed:{key}")
                before = None
                if intent["backup"]:
                    backup = (run_dir / intent["backup"]).resolve()
                    if not backup.is_relative_to(run_dir / "backups"):
                        raise ValueError("unsafe_backup_path")
                    before = backup.read_bytes()
                    if digest(before) != intent["before_hash"]:
                        raise ValueError("backup_integrity_error")
                pending.append((key, target, before, intent))
        except Exception as error:
            journal(run_dir, "rollback_preflight_failed", reason=str(error))
            raise
        for key, target, before, intent in pending:
            target = _target(by_key[key], manifest)
            if digest(target.read_bytes()) != intent["after_hash"]:
                raise ValueError(f"filesystem_changed:{key}")
            journal(run_dir, "rollback_intent", page_key=key)
            if before is None:
                target.unlink()
            else:
                _atomic_bytes(target, before, intent["original_mode"])
            journal(run_dir, "rolled_back", page_key=key, restored_hash=intent["before_hash"])
        journal(run_dir, "rollback_completed", restored=len(pending))
        return {"restored": len(pending)}

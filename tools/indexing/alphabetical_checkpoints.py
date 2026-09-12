"""Content-verified checkpoints for the alphabetical-index pipelines.

The compact and staged orchestrators intentionally share this module.  A checkpoint
is reusable only when both its declared input fingerprint and the hash of its output
still match.  OCR snapshots reuse per-file hashes when size and nanosecond mtime are
unchanged, avoiding a complete rehash on normal reruns while still detecting edits.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.corpus_utils import page_sort_key


CHECKPOINT_SCHEMA_VERSION = 1
SOURCE_SNAPSHOT_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_json_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_source_snapshot(
    source_root: Path,
    *,
    previous_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a content snapshot of one OCR volume.

    Paths are relative to ``source_root`` so moving a checkout does not invalidate
    semantic work.  The root fingerprint covers path, size, mtime and content hash.
    Content hashes from a prior snapshot are reused only when path, size and mtime all
    agree.
    """

    root = source_root.expanduser().resolve()
    previous_by_path = {
        str(record.get("ocr_file_path") or ""): record
        for record in (previous_snapshot or {}).get("files") or []
        if isinstance(record, Mapping)
    }
    records: list[dict[str, Any]] = []
    reused_hash_count = 0
    hashed_file_count = 0
    total_bytes = 0
    for path in sorted(root.glob("*.txt"), key=page_sort_key):
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        previous = previous_by_path.get(relative)
        content_sha256: str
        if (
            previous is not None
            and previous.get("source_size") == stat.st_size
            and previous.get("source_mtime_ns") == stat.st_mtime_ns
            and isinstance(previous.get("content_sha256"), str)
            and len(str(previous["content_sha256"])) == 64
        ):
            content_sha256 = str(previous["content_sha256"])
            reused_hash_count += 1
        else:
            content_sha256 = file_sha256(path)
            hashed_file_count += 1
        total_bytes += stat.st_size
        records.append(
            {
                "ocr_file_path": relative,
                "source_size": stat.st_size,
                "source_mtime_ns": stat.st_mtime_ns,
                "content_sha256": content_sha256,
            }
        )
    fingerprint_payload = {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "files": records,
    }
    return {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "stage": "ocr_source_snapshot",
        "source_root": str(root),
        "generated_at": _now_iso(),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "hashed_file_count": hashed_file_count,
        "reused_hash_count": reused_hash_count,
        "source_snapshot_fingerprint": stable_json_fingerprint(
            fingerprint_payload
        ),
        "files": records,
    }


def update_source_snapshot(path: Path, source_root: Path) -> dict[str, Any]:
    previous: Mapping[str, Any] | None = None
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(loaded, Mapping)
                and Path(str(loaded.get("source_root") or "")).resolve()
                == source_root.resolve()
            ):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = None
    snapshot = build_source_snapshot(
        source_root,
        previous_snapshot=previous,
    )
    _write_json_atomic(path, snapshot)
    return snapshot


def checkpoint_path_for(artifact_path: Path) -> Path:
    return artifact_path.with_suffix(artifact_path.suffix + ".checkpoint.json")


def write_checkpointed_json(
    artifact_path: Path,
    payload: Any,
    *,
    stage: str,
    input_fingerprint: str,
    dependencies: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _write_json_atomic(artifact_path, payload)
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "stage": stage,
        "status": "complete",
        "input_fingerprint": input_fingerprint,
        "output_file": str(artifact_path.resolve()),
        "output_sha256": file_sha256(artifact_path),
        "dependencies": dict(dependencies or {}),
        "summary": dict(summary or {}),
        "completed_at": _now_iso(),
    }
    _write_json_atomic(checkpoint_path_for(artifact_path), checkpoint)
    return checkpoint


def read_checkpointed_json(
    artifact_path: Path,
    *,
    stage: str,
    input_fingerprint: str,
) -> Any | None:
    checkpoint_path = checkpoint_path_for(artifact_path)
    if not artifact_path.is_file() or not checkpoint_path.is_file():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(checkpoint, Mapping):
            return None
        if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            return None
        if checkpoint.get("stage") != stage or checkpoint.get("status") != "complete":
            return None
        if checkpoint.get("input_fingerprint") != input_fingerprint:
            return None
        if Path(str(checkpoint.get("output_file") or "")).resolve() != artifact_path.resolve():
            return None
        if checkpoint.get("output_sha256") != file_sha256(artifact_path):
            return None
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


__all__ = [
    "build_source_snapshot",
    "checkpoint_path_for",
    "file_sha256",
    "read_checkpointed_json",
    "stable_json_fingerprint",
    "update_source_snapshot",
    "write_checkpointed_json",
]

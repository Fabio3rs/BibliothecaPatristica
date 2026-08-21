#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


REVIEW_STATUSES = {"resolved", "resolved_unlocated"}


def _resolved_path(source_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = source_root / path
    return path.resolve()


def apply_repairs_to_payload(
    payload: dict[str, Any],
    repairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    volume = payload.get("volume") or {}
    volume_id = str(volume.get("volume_id") or "")
    source_root = Path(str(volume.get("source_root") or "")).resolve()
    works = {
        str(work.get("work_key")): work
        for work in payload.get("works") or []
        if isinstance(work, dict) and work.get("work_key")
    }
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for repair in repairs:
        if str(repair.get("volume_id") or "") != volume_id:
            raise ValueError(f"repair volume mismatch for {repair.get('work_key')}")
        work_key = str(repair.get("work_key") or "")
        if not work_key or work_key in seen:
            raise ValueError(f"missing or duplicate work_key: {work_key!r}")
        seen.add(work_key)
        if work_key not in works:
            raise KeyError(f"work_key not found in {volume_id}: {work_key}")
        status = str(repair.get("status") or "")
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid status for {work_key}: {status!r}")
        start_page = repair.get("start_page")
        start_file = _resolved_path(source_root, repair.get("start_file"))
        if status == "resolved":
            if not isinstance(start_page, int) or isinstance(start_page, bool) or start_page < 1:
                raise ValueError(f"resolved repair requires positive start_page: {work_key}")
            if start_file is None or not start_file.is_file():
                raise ValueError(f"resolved repair requires existing start_file: {work_key}")
            if source_root not in start_file.parents:
                raise ValueError(f"start_file outside source_root for {work_key}: {start_file}")
        else:
            if start_page is not None or start_file is not None:
                raise ValueError(
                    f"resolved_unlocated repair requires null page/file: {work_key}"
                )

        work = works[work_key]
        before = {
            "start_page": work.get("start_page"),
            "start_file": work.get("start_file"),
        }
        work["start_page"] = start_page
        work["start_file"] = str(start_file) if start_file is not None else None
        raw_json = work.get("raw_json")
        if not isinstance(raw_json, dict):
            raw_json = {"value": raw_json} if raw_json is not None else {}
        raw_json.pop("work_anchor_rerun", None)
        raw_json["manual_anchor_review"] = {
            "status": status,
            "method": str(
                repair.get("method")
                or "OCR neighbors and paired facsimile inspection"
            ),
            "image_files_opened": list(repair.get("image_files_opened") or []),
            "confidence": str(repair.get("confidence") or ""),
            "evidence": str(repair.get("evidence") or ""),
        }
        work["raw_json"] = raw_json
        changes.append(
            {
                "volume_id": volume_id,
                "work_key": work_key,
                "before": before,
                "after": {
                    "start_page": work["start_page"],
                    "start_file": work["start_file"],
                    "status": status,
                },
            }
        )
    return changes


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply reviewed work-anchor repairs to canonical index payloads."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--payload-dir", type=Path, default=Path("data/index_payloads")
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    repairs = manifest.get("repairs") or []
    if not isinstance(repairs, list):
        raise ValueError("manifest.repairs must be an array")
    by_volume: dict[str, list[dict[str, Any]]] = {}
    global_keys: set[tuple[str, str]] = set()
    for repair in repairs:
        if not isinstance(repair, dict):
            raise ValueError("every repair must be an object")
        key = (str(repair.get("volume_id") or ""), str(repair.get("work_key") or ""))
        if key in global_keys:
            raise ValueError(f"duplicate repair in manifest: {key}")
        global_keys.add(key)
        by_volume.setdefault(key[0], []).append(repair)

    all_changes: list[dict[str, Any]] = []
    staged: list[tuple[Path, dict[str, Any]]] = []
    for volume_id, volume_repairs in sorted(by_volume.items()):
        payload_path = args.payload_dir / f"{volume_id}_indices.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        all_changes.extend(apply_repairs_to_payload(payload, volume_repairs))
        staged.append((payload_path, payload))
    if args.apply:
        for payload_path, payload in staged:
            _atomic_json_write(payload_path, payload)

    report = {
        "schema_version": 1,
        "apply": args.apply,
        "repair_count": len(all_changes),
        "volume_count": len(staged),
        "volumes": [path.stem.removesuffix("_indices") for path, _ in staged],
        "changes": all_changes,
    }
    if args.report_json:
        _atomic_json_write(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

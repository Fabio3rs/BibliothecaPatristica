#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import selectors
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.common import parse_volume_info

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = PROJECT_ROOT / ".codex" / "skills" / "patristic-index-extractor"
SCAN_SCRIPT = SKILL_DIR / "scripts" / "scan_volume.py"
PROMPT_SCRIPT = SKILL_DIR / "scripts" / "build_volume_prompt.py"
IMPORT_SCRIPT = SKILL_DIR / "scripts" / "import_index_json.py"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "index_payloads"
DEFAULT_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "index_logs"


def run_cmd(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def select_volume_ids(root: Path, blob: str, limit: int | None) -> list[str]:
    patterns = [part.strip().upper() for part in blob.split(",") if part.strip()]
    prefixes = [part[:-1] if part.endswith("*") else part for part in patterns]
    volume_ids: list[str] = []
    if not root.exists():
        return volume_ids
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        info = parse_volume_info(child)
        if info is None:
            continue
        if prefixes and not any(info.volume_id.upper().startswith(prefix) for prefix in prefixes):
            continue
        if not (child / "text").exists():
            continue
        volume_ids.append(info.volume_id)
    if limit is not None and limit > 0:
        volume_ids = volume_ids[:limit]
    return volume_ids


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def volume_already_imported(db_path: Path, volume_id: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM volumes WHERE volume_id = ? LIMIT 1",
            (volume_id,),
        ).fetchone()
        if row is not None:
            return True
        row = con.execute(
            "SELECT 1 FROM runs WHERE volume_id = ? AND status = 'imported' LIMIT 1",
            (volume_id,),
        ).fetchone()
        return row is not None


def scan_volume(volume_id: str, root: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCAN_SCRIPT),
        "--volume",
        volume_id,
        "--root",
        str(root),
        "--json",
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"scan_volume.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def build_prompt(volume_id: str, source_root: Path, collection: str, prescan_json: Path, output_dir: Path) -> str:
    cmd = [
        sys.executable,
        str(PROMPT_SCRIPT),
        "--volume",
        volume_id,
        "--source-root",
        str(source_root),
        "--collection",
        collection,
        "--prescan-json",
        str(prescan_json),
        "--output-dir",
        str(output_dir),
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_volume_prompt.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout


def run_codex(
    codex_bin: str,
    prompt: str,
    last_message_path: Path,
    cwd: Path,
    use_json: bool,
    model: str | None,
    verbose: bool,
    stdout_log_path: Path | None = None,
    stderr_log_path: Path | None = None,
    stream_log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        codex_bin,
        "exec",
        "--full-auto",
        "--output-last-message",
        str(last_message_path),
    ]
    if model:
        command.extend(["--model", model])
    if use_json:
        command.append("--json")
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.write(prompt)
    proc.stdin.close()

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_log = stdout_log_path.open("w", encoding="utf-8") if stdout_log_path else None
    stderr_log = stderr_log_path.open("w", encoding="utf-8") if stderr_log_path else None
    stream_log = stream_log_path.open("w", encoding="utf-8") if stream_log_path else None

    try:
        while selector.get_map():
            for key, _ in selector.select():
                stream = key.fileobj
                label = key.data
                line = stream.readline()
                if line == "":
                    selector.unregister(stream)
                    continue
                if label == "stdout":
                    stdout_parts.append(line)
                    if stdout_log is not None:
                        stdout_log.write(line)
                else:
                    stderr_parts.append(line)
                    if stderr_log is not None:
                        stderr_log.write(line)
                if stream_log is not None:
                    stream_log.write(f"[{label}] {line}")
                if verbose:
                    sys.stdout.write(f"[codex {label}] {line}")
                    sys.stdout.flush()
    finally:
        if stdout_log is not None:
            stdout_log.close()
        if stderr_log is not None:
            stderr_log.close()
        if stream_log is not None:
            stream_log.close()

    return subprocess.CompletedProcess(
        args=command,
        returncode=proc.wait(),
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def validate_payload(payload: dict[str, Any], volume_id: str, expected_file: Path) -> None:
    if not isinstance(payload, dict):
        raise SystemExit("Payload is not a JSON object.")
    if payload.get("volume", {}).get("volume_id") != volume_id:
        raise SystemExit(f"Payload volume_id mismatch: expected {volume_id}")
    for key in ("volume", "works", "sections", "notes"):
        if key not in payload:
            raise SystemExit(f"Missing top-level key: {key}")
    if not isinstance(payload.get("works"), list):
        raise SystemExit("Payload 'works' must be a list.")
    if not isinstance(payload.get("sections"), list):
        raise SystemExit("Payload 'sections' must be a list.")
    if not expected_file.exists():
        raise SystemExit(f"Expected output file not found: {expected_file}")


def import_payload(payload_file: Path, replace: bool) -> None:
    cmd = [
        sys.executable,
        str(IMPORT_SCRIPT),
        "--input",
        str(payload_file),
    ]
    if replace:
        cmd.append("--replace")
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"import_index_json.py failed for {payload_file.name}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(result.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one Codex extraction pass for one PG/PL volume.")
    ap.add_argument("--volume-id", help="Volume id, e.g. PG001 or PL099")
    ap.add_argument("--all-volumes", action="store_true", help="Run one Codex pass for every matching volume under --root")
    ap.add_argument("--blob", help="Prefix/glob-style filter for --all-volumes, e.g. PL or PL*")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste", help="Root directory containing volume folders")
    ap.add_argument("--limit", type=int, default=None, help="Optional max volume count for --all-volumes")
    ap.add_argument("--codex-bin", default="codex", help="Codex CLI binary")
    ap.add_argument("--model", default=None, help="Optional Codex model override")
    ap.add_argument("--use-json", action="store_true", help="Pass --json to codex exec")
    ap.add_argument("--replace", action="store_true", help="Replace existing rows for the volume on import")
    ap.add_argument("--skip-done", action="store_true", help="Skip volumes already imported into the SQLite DB")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database used to detect already imported volumes")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for the payload JSON file")
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for persistent Codex logs")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the temporary prescan JSON file")
    ap.add_argument("--dry-run", action="store_true", help="Build prescan and prompt, then stop before calling Codex")
    ap.add_argument("--verbose", action="store_true", help="Stream Codex stdout/stderr live to the terminal")
    args = ap.parse_args()

    if args.blob and not args.all_volumes:
        args.all_volumes = True

    if args.all_volumes:
        blob = args.blob or "PG*,PL*,PO*"
        volume_ids = select_volume_ids(args.root, blob, args.limit)
        if not volume_ids:
            raise SystemExit(f"No volumes selected for blob {blob!r}.")
    else:
        if not args.volume_id:
            raise SystemExit("Use --volume-id or --all-volumes/--blob.")
        volume_ids = [args.volume_id]

    for idx, volume_id in enumerate(volume_ids, start=1):
        volume_root = args.root / volume_id
        text_root = volume_root / "text"
        if not text_root.exists():
            raise SystemExit(f"Text directory not found: {text_root}")

        info = parse_volume_info(volume_root)
        if info is None:
            raise SystemExit(f"Could not parse volume info from {volume_root}")
        collection = info.series

        if args.skip_done and not args.replace and volume_already_imported(args.db, volume_id):
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "already_imported",
                        "volume_id": volume_id,
                        "collection": collection,
                        "db": str(args.db),
                    },
                    ensure_ascii=False,
                )
            )
            continue

        args.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] volume {idx}/{len(volume_ids)}: {volume_id} ({collection})")
        prescan = scan_volume(volume_id, args.root)
        prescan_path = args.output_dir / f"{volume_id}_prescan.json"
        prescan_path.write_text(json.dumps(prescan, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.verbose:
            print(f"[INFO] prescan written to {prescan_path}")

        prompt = build_prompt(
            volume_id=volume_id,
            source_root=text_root,
            collection=collection,
            prescan_json=prescan_path,
            output_dir=args.output_dir,
        )

        payload_file = args.output_dir / f"{volume_id}_indices.json"
        last_message_path = args.output_dir / f"{volume_id}_last_message.txt"
        args.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log_path = args.log_dir / f"{volume_id}_codex_stdout.log"
        stderr_log_path = args.log_dir / f"{volume_id}_codex_stderr.log"
        stream_log_path = args.log_dir / f"{volume_id}_codex_stream.log"

        if args.dry_run:
            prompt_path = args.output_dir / f"{volume_id}_prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": "dry-run",
                        "volume_id": volume_id,
                        "collection": collection,
                        "blob": args.blob,
                        "prescan_file": str(prescan_path),
                        "prompt_file": str(prompt_path),
                        "payload_file": str(payload_file),
                        "last_message_file": str(last_message_path),
                        "stdout_log_file": str(stdout_log_path),
                        "stderr_log_file": str(stderr_log_path),
                        "stream_log_file": str(stream_log_path),
                        "would_run_codex": True,
                        "would_import": True,
                    },
                    ensure_ascii=False,
                )
            )
            if not args.keep_temp:
                prescan_path.unlink(missing_ok=True)
            continue

        if args.verbose:
            print(f"[INFO] payload file: {payload_file}")
            print(f"[INFO] last message file: {last_message_path}")
        result = run_codex(
            codex_bin=args.codex_bin,
            prompt=prompt,
            last_message_path=last_message_path,
            cwd=PROJECT_ROOT,
            use_json=args.use_json,
            model=args.model,
            verbose=args.verbose,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            stream_log_path=stream_log_path,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"codex exec failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        last_message = last_message_path.read_text(encoding="utf-8").strip()
        try:
            ack = json.loads(last_message)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Last message is not valid JSON: {exc}\n{last_message}") from exc

        if ack.get("status") != "ok":
            raise SystemExit(f"Codex did not return ok: {ack}")
        if ack.get("volume_id") != volume_id:
            raise SystemExit(f"volume_id mismatch in ack: {ack}")
        if ack.get("written_file") != str(payload_file):
            raise SystemExit(f"written_file mismatch in ack: {ack}")

        payload = read_json(payload_file)
        validate_payload(payload, volume_id, payload_file)
        import_payload(payload_file, replace=args.replace)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "volume_id": volume_id,
                    "collection": collection,
                    "blob": args.blob,
                    "payload_file": str(payload_file),
                    "prescan_file": str(prescan_path),
                    "last_message_file": str(last_message_path),
                    "stdout_log_file": str(stdout_log_path),
                    "stderr_log_file": str(stderr_log_path),
                    "stream_log_file": str(stream_log_path),
                    "imported": True,
                },
                ensure_ascii=False,
            )
        )

        if not args.keep_temp:
            prescan_path.unlink(missing_ok=True)
            last_message_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

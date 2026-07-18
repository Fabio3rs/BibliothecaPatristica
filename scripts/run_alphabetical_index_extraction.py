#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import selectors
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphabetical_index_db import DEFAULT_DB, volume_already_imported
from patristica_pipeline.common import parse_volume_info
from patristica_pipeline.index_target_locator import parse_ocr_page_xml, resolve_index_targets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTERED_PAGES_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_filtered_pages.py"
PROMPT_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_prompt.py"
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_alphabetical_index_json.py"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "alphabetical_index_payloads"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "alphabetical_index_logs"
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2
INDEX_LINE_RE = re.compile(r"\d{1,4}")
PAGE_HINT_TOKEN_RE = re.compile(r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\b(?:\s*(?:seq\.?|seqq\.?))?", re.IGNORECASE)


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


def resolve_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve()


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _is_index_line(stripped: str) -> bool:
    if not stripped or len(stripped) > 240:
        return False
    if not re.search(r"[A-Za-zÆŒÀ-ÿ]", stripped):
        return False
    if not INDEX_LINE_RE.search(stripped):
        return False
    if re.match(r"^\s*\d{1,4}\s+[A-ZÆŒ]", stripped):
        return False
    if stripped.upper().startswith(("INDEX ", "TABLE ", "ELENCHUS ", "ORDO ", "NOTES ", "OBS ", "OCR ")):
        return False
    return True


def _extract_page_hints(text: str) -> list[str]:
    return [match.group(0).strip() for match in PAGE_HINT_TOKEN_RE.finditer(text or "")]


def _derive_lemma_raw(text: str) -> str:
    lemma = re.split(r"\b\d{1,4}\b", text, maxsplit=1)[0].strip()
    lemma = re.sub(r"\s+", " ", lemma)
    lemma = lemma.strip(" ,;:.–—-")
    return lemma


def _derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned:
        candidates.append(cleaned)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    first_clause = re.split(r"\s*[;,]\s*|\s{2,}", cleaned or lemma_raw, maxsplit=1)[0].strip()
    if first_clause and first_clause not in candidates:
        candidates.append(first_clause)
    context_prefix = context_raw.strip()
    if context_prefix and context_prefix not in candidates:
        candidates.append(context_prefix)
    return _unique_preserve_order(candidates)[:4]


def _build_context_window(lines: list[str], index: int, *, width: int = 1) -> str:
    start = max(0, index - width)
    end = min(len(lines), index + width + 1)
    window = [line.strip() for line in lines[start:end] if line.strip()]
    return "\n".join(window)


def build_helper_request_artifact(
    *,
    volume_id: str,
    source_root: Path,
    filtered_pages: dict[str, Any],
    helper_request_json: Path,
) -> dict[str, Any]:
    candidate_files = [Path(item) for item in (filtered_pages.get("candidate_files") or [])]
    if not candidate_files:
        candidate_files = [Path(item) for item in (filtered_pages.get("tail_files") or [])]
    if not candidate_files:
        candidate_files = sorted(source_root.glob("*.txt"))

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for file_path in candidate_files:
        if not file_path.exists():
            continue
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_ocr_page_xml(raw_text)
        lines = [line.strip() for line in parsed["all_text"].splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            if not _is_index_line(line):
                continue
            if not _extract_page_hints(line):
                continue
            lemma_raw = _derive_lemma_raw(line)
            if not lemma_raw:
                continue
            context_raw = _build_context_window(lines, idx, width=1)
            request_key = (str(file_path), idx + 1, lemma_raw)
            if request_key in seen:
                continue
            seen.add(request_key)
            entries.append(
                {
                    "entry_id": f"{volume_id.lower()}_{file_path.stem}_{idx + 1:04d}",
                    "lemma_raw": lemma_raw,
                    "query_names": _derive_query_names(lemma_raw, context_raw),
                    "page_hints": _extract_page_hints(line),
                    "page_hint_ints": [int(match.group(0)) for match in INDEX_LINE_RE.finditer(line)],
                    "context_raw": context_raw or line,
                }
            )

    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
        },
        "entries": entries,
    }
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def run_helper_locator(request: dict[str, Any], helper_output_json: Path) -> dict[str, Any]:
    result = resolve_index_targets(request)
    helper_output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


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


def resolve_previous_payload_path(
    *,
    args: argparse.Namespace,
    volume_id: str,
    default_payload_file: Path,
) -> tuple[Path, str]:
    if args.previous_result_json is not None:
        return args.previous_result_json, "explicit_file"
    if args.previous_result_dir is not None:
        candidate = args.previous_result_dir / f"{volume_id}_alphabetical_indices.json"
        if candidate.exists():
            return candidate, "explicit_dir"
        return candidate, "explicit_dir_missing"
    return default_payload_file, "default_output"


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


def validate_payload_file(payload_file: Path) -> None:
    cmd = [
        sys.executable,
        str(IMPORT_SCRIPT),
        "--input",
        str(payload_file),
        "--validate-only",
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"import_alphabetical_index_json.py validation failed for {payload_file.name}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def inspect_output_checkpoint(payload_file: Path) -> tuple[str, str | None]:
    if not payload_file.exists():
        return "missing", None
    try:
        validate_payload_file(payload_file)
    except SystemExit as exc:
        return "invalid", str(exc)
    return "done", None


def build_filtered_pages_artifact(
    *,
    args: argparse.Namespace,
    volume_id: str,
    canonical_path: Path,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(FILTERED_PAGES_SCRIPT),
        "--volume",
        volume_id,
        "--root",
        str(args.root),
        "--output",
        str(canonical_path),
        "--pretty",
    ]
    if args.filtered_pages_json is not None:
        cmd.extend(["--filtered-pages-json", str(args.filtered_pages_json)])
    if args.filtered_pages_dir is not None:
        cmd.extend(["--filtered-pages-dir", str(args.filtered_pages_dir)])
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_alphabetical_filtered_pages.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return read_json(canonical_path)


def build_prompt(
    *,
    volume_id: str,
    source_root: Path,
    collection: str,
    filtered_pages_file: Path,
    previous_payload: Path,
    previous_result_source: str,
    output_checkpoint_status: str,
    helper_request_json: Path,
    helper_output_json: Path,
    output_file: Path,
) -> str:
    cmd = [
        sys.executable,
        str(PROMPT_SCRIPT),
        "--volume",
        volume_id,
        "--source-root",
        str(source_root),
        "--collection",
        collection,
        "--filtered-pages-json",
        str(filtered_pages_file),
        "--previous-payload",
        str(previous_payload),
        "--previous-result-source",
        previous_result_source,
        "--output-checkpoint-status",
        output_checkpoint_status,
        "--helper-request-json",
        str(helper_request_json),
        "--helper-output-json",
        str(helper_output_json),
        "--output-file",
        str(output_file),
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_alphabetical_prompt.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout


def import_payload(payload_file: Path, db_path: Path, replace: bool) -> None:
    cmd = [
        sys.executable,
        str(IMPORT_SCRIPT),
        "--input",
        str(payload_file),
        "--db",
        str(db_path),
    ]
    if replace:
        cmd.append("--replace")
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"import_alphabetical_index_json.py failed for {payload_file.name}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(result.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run one Codex extraction pass for one PG/PL/PO volume focused on alphabetical indexes."
    )
    ap.add_argument("--volume-id", help="Volume id, e.g. PG003 or PO025")
    ap.add_argument("--all-volumes", action="store_true", help="Run one Codex pass for every matching volume under --root")
    ap.add_argument("--blob", help="Prefix/glob-style filter for --all-volumes, e.g. PL or PL*")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste", help="Root directory containing volume folders")
    ap.add_argument("--limit", type=int, default=None, help="Optional max volume count for --all-volumes")
    ap.add_argument("--filtered-pages-json", type=Path, help="Pre-filtered pages JSON for a single-volume run")
    ap.add_argument("--filtered-pages-dir", type=Path, help="Directory with <VOLUME>_filtered_pages.json files for batch runs")
    ap.add_argument("--previous-result-json", type=Path, help="Previous payload JSON for a single-volume rerun")
    ap.add_argument("--previous-result-dir", type=Path, help="Directory with prior <VOLUME>_alphabetical_indices.json payloads")
    ap.add_argument("--codex-bin", default="codex", help="Codex CLI binary")
    ap.add_argument("--model", default=None, help="Optional Codex model override")
    ap.add_argument("--use-json", action="store_true", help="Pass --json to codex exec")
    ap.add_argument("--replace", action="store_true", help="Allow overwriting an existing output payload file and replacing existing DB rows")
    ap.add_argument(
        "--skip-done",
        action="store_true",
        help="Skip volumes already imported into the alphabetical SQLite DB",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database used to detect and store imported alphabetical payloads")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for payload and helper JSON artifacts")
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for persistent Codex logs")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the last-message artifact after a successful run")
    ap.add_argument("--dry-run", action="store_true", help="Build filtered pages and prompt, then stop before calling Codex")
    ap.add_argument("--verbose", action="store_true", help="Stream Codex stdout/stderr live to the terminal")
    args = ap.parse_args()

    args.root = resolve_path(args.root) or args.root
    args.filtered_pages_json = resolve_path(args.filtered_pages_json)
    args.filtered_pages_dir = resolve_path(args.filtered_pages_dir)
    args.previous_result_json = resolve_path(args.previous_result_json)
    args.previous_result_dir = resolve_path(args.previous_result_dir)
    args.db = resolve_path(args.db) or args.db
    args.output_dir = resolve_path(args.output_dir) or args.output_dir
    args.log_dir = resolve_path(args.log_dir) or args.log_dir

    if args.filtered_pages_json and args.all_volumes:
        raise SystemExit("--filtered-pages-json is only valid for a single-volume run.")
    if args.previous_result_json and args.all_volumes:
        raise SystemExit("--previous-result-json is only valid for a single-volume run.")

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

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    for idx, volume_id in enumerate(volume_ids, start=1):
        volume_root = args.root / volume_id
        text_root = volume_root / "text"
        if not text_root.exists():
            raise SystemExit(f"Text directory not found: {text_root}")

        info = parse_volume_info(volume_root)
        if info is None:
            raise SystemExit(f"Could not parse volume info from {volume_root}")
        collection = info.series

        payload_file = args.output_dir / f"{volume_id}_alphabetical_indices.json"
        previous_payload_file, previous_result_source = resolve_previous_payload_path(
            args=args,
            volume_id=volume_id,
            default_payload_file=payload_file,
        )
        output_checkpoint_status, output_checkpoint_error = inspect_output_checkpoint(payload_file)
        db_imported = volume_already_imported(args.db, volume_id)

        if args.skip_done and db_imported and not args.replace:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "already_imported",
                        "volume_id": volume_id,
                        "collection": collection,
                        "db": str(args.db),
                        "payload_file": str(payload_file),
                        "output_checkpoint_status": output_checkpoint_status,
                    },
                    ensure_ascii=False,
                )
            )
            continue

        if payload_file.exists() and output_checkpoint_status == "invalid" and not args.replace and not args.dry_run:
            if args.verbose:
                print(
                    f"[WARN] existing payload for {volume_id} is invalid but will be reused as previous-result checkpoint: "
                    f"{output_checkpoint_error}"
                )

        print(f"[INFO] volume {idx}/{len(volume_ids)}: {volume_id} ({collection})")

        filtered_pages_file = args.output_dir / f"{volume_id}_filtered_pages.json"
        filtered_pages = build_filtered_pages_artifact(
            args=args,
            volume_id=volume_id,
            canonical_path=filtered_pages_file,
        )
        filtered_source = str(filtered_pages.get("source") or "unknown")
        if args.verbose:
            print(f"[INFO] filtered pages source: {filtered_source}")
            print(f"[INFO] filtered pages written to {filtered_pages_file}")
            print(f"[INFO] previous result source: {previous_result_source}")
            print(f"[INFO] previous result path: {previous_payload_file}")
            print(f"[INFO] output checkpoint status: {output_checkpoint_status}")
            print(f"[INFO] db imported status: {db_imported}")
        if (
            filtered_source == "fallback_internal"
            and args.filtered_pages_dir is not None
            and args.filtered_pages_json is None
        ):
            print(
                f"[WARN] no external filtered-pages file found for {volume_id} in {args.filtered_pages_dir}; "
                "falling back to internal heuristic prefilter."
            )

        helper_request_json = args.output_dir / f"{volume_id}_helper_request.json"
        helper_output_json = args.output_dir / f"{volume_id}_helper_output.json"
        last_message_path = args.output_dir / f"{volume_id}_last_message.txt"
        stdout_log_path = args.log_dir / f"{volume_id}_codex_stdout.log"
        stderr_log_path = args.log_dir / f"{volume_id}_codex_stderr.log"
        stream_log_path = args.log_dir / f"{volume_id}_codex_stream.log"

        prompt = build_prompt(
            volume_id=volume_id,
            source_root=text_root,
            collection=collection,
            filtered_pages_file=filtered_pages_file,
            previous_payload=previous_payload_file,
            previous_result_source=previous_result_source,
            output_checkpoint_status=output_checkpoint_status,
            helper_request_json=helper_request_json,
            helper_output_json=helper_output_json,
            output_file=payload_file,
        )

        if args.dry_run:
            prompt_path = args.output_dir / f"{volume_id}_prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": "dry-run",
                        "volume_id": volume_id,
                        "collection": collection,
                        "filtered_pages_source": filtered_source,
                        "previous_result_source": previous_result_source,
                        "previous_result_file": str(previous_payload_file),
                        "previous_result_exists": previous_payload_file.exists(),
                        "output_checkpoint_status": output_checkpoint_status,
                        "db": str(args.db),
                        "db_imported": db_imported,
                        "filtered_pages_file": str(filtered_pages_file),
                        "prompt_file": str(prompt_path),
                        "payload_file": str(payload_file),
                        "helper_request_file": str(helper_request_json),
                        "helper_output_file": str(helper_output_json),
                        "last_message_file": str(last_message_path),
                        "stdout_log_file": str(stdout_log_path),
                        "stderr_log_file": str(stderr_log_path),
                        "stream_log_file": str(stream_log_path),
                        "would_run_codex": True,
                    },
                    ensure_ascii=False,
                )
            )
            continue

        if args.verbose:
            print(f"[INFO] payload file: {payload_file}")
            print(f"[INFO] last message file: {last_message_path}")
            if previous_payload_file.exists():
                print(f"[INFO] previous payload checkpoint will be available to Codex: {previous_payload_file}")
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

        validate_payload_file(payload_file)
        import_payload(payload_file, args.db, replace=args.replace)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "volume_id": volume_id,
                    "collection": collection,
                    "db": str(args.db),
                    "imported": True,
                    "filtered_pages_source": filtered_source,
                    "previous_result_source": previous_result_source,
                    "previous_result_file": str(previous_payload_file),
                    "output_checkpoint_status_before_run": output_checkpoint_status,
                    "filtered_pages_file": str(filtered_pages_file),
                    "payload_file": str(payload_file),
                    "helper_request_file": str(helper_request_json),
                    "helper_output_file": str(helper_output_json),
                    "last_message_file": str(last_message_path),
                    "stdout_log_file": str(stdout_log_path),
                    "stderr_log_file": str(stderr_log_path),
                    "stream_log_file": str(stream_log_path),
                },
                ensure_ascii=False,
            )
        )

        if not args.keep_temp:
            last_message_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

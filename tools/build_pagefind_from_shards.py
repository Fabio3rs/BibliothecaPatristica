#!/usr/bin/env python3
"""Build a Pagefind index from shard metadata using the native service protocol.

This script mirrors the JavaScript builder but talks directly to the Pagefind
binary over its `--service` IPC protocol:

  - outgoing messages: JSON -> base64 -> comma-delimited
  - incoming messages: base64 -> JSON -> comma-delimited

Heavy pre-processing (raw text prefix reads + title extraction) is parallelized
with multiprocessing, while Pagefind indexing itself is kept synchronous with
the service protocol.
"""

from __future__ import annotations

import argparse
import base64
import json
import multiprocessing as mp
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time
import signal
import faulthandler
import threading
from queue import Queue, Empty
from dataclasses import dataclass
from typing import Any, Iterable


ROOT = pathlib.Path(__file__).resolve().parents[1]

faulthandler.enable()


def _handle_sigint(signum, frame):
    print("\n[debug] SIGINT received, dumping stacks...", file=sys.stderr)
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, _handle_sigint)


def read_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--public", default="web/public")
    p.add_argument("--out", default="web/public/pagefind")
    p.add_argument("--base", default="/BibliothecaPatristica")
    p.add_argument("--min-count", type=int, default=1)
    p.add_argument("--bench-volume", default=os.environ.get("BENCH_VOLUME"))
    p.add_argument("--io-concurrency", type=int, default=int(os.environ.get("IO_CONCURRENCY", "64")))
    p.add_argument("--raw-title-bytes", type=int, default=int(os.environ.get("RAW_TITLE_BYTES", "65536")))
    return p.parse_args()


def resolve_pagefind_binary() -> pathlib.Path:
    env_paths = [
        os.environ.get("PAGEFIND_EXTENDED_BINARY_PATH"),
        os.environ.get("PAGEFIND_BINARY_PATH"),
    ]
    for candidate in env_paths:
        if candidate:
            p = pathlib.Path(candidate)
            if p.exists():
                return p

    arch = os.environ.get("npm_config_arch") or platform.machine()
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(arch, arch)
    node_platform = "windows" if sys.platform == "win32" else sys.platform
    candidates = [
        ROOT / "web" / "node_modules" / f"@pagefind/{node_platform}-{arch}" / "bin" / ("pagefind_extended.exe" if sys.platform == "win32" else "pagefind_extended"),
        ROOT / "web" / "node_modules" / f"@pagefind/{node_platform}-{arch}" / "bin" / ("pagefind.exe" if sys.platform == "win32" else "pagefind"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Failed to locate Pagefind binary for {node_platform}-{arch}. "
        "Set PAGEFIND_EXTENDED_BINARY_PATH or PAGEFIND_BINARY_PATH."
    )


def escape_html(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_new_index_config() -> dict[str, Any]:
    return {
        "keep_index_url": False,
        "write_playground": False,
        "force_language": "pt",
    }


def normalize_whitespace(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def strip_noise_lines(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if re.fullmatch(r"[.\-_=*·•\s]+", line):
            continue
        if re.fullmatch(r"\.{3,}", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def extract_xml_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"<bloco\b[^>]*>([\s\S]*?)</bloco>", text, re.I):
        body = normalize_whitespace(re.sub(r"<[^>]+>", " ", match.group(1)))
        if body:
            blocks.append(body)
    return blocks


def is_header_like(line: str) -> bool:
    if not line or re.fullmatch(r"\d+", line):
        return False
    if re.fullmatch(r"[.\-_=*·•\s]+", line):
        return False
    letters = len(re.findall(r"[A-Za-zÀ-ÿΑ-Ωα-ω]", line))
    if letters < 4:
        return False
    upper_count = len(re.findall(r"[A-ZÀ-ÝΑ-Ω]", line))
    lower_count = len(re.findall(r"[a-zà-ÿα-ω]", line))
    upper_ratio = upper_count / max(1, letters)
    lower_ratio = lower_count / max(1, letters)
    score = 0
    score += min(letters, 80)
    score += min(len(line), 120) / 6
    score += upper_ratio * 20
    if upper_ratio > 0.6:
        score += 20
    if lower_ratio < 0.2 and letters > 8:
        score += 10
    return score >= 45


def extract_title_from_text(raw_text: str | None) -> str | None:
    if not raw_text:
        return None
    trimmed = raw_text.lstrip()
    if re.match(r"^<\s*pagina\b", trimmed, re.I) or re.match(r"^<\?xml\b", trimmed, re.I):
        blocks = extract_xml_blocks(raw_text)
        title = normalize_whitespace(" ".join(blocks[:2]))
        return title or None

    cleaned = strip_noise_lines(raw_text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    heading_lines = []
    heading_phase = True
    for line in lines:
        if heading_phase and is_header_like(line):
            heading_lines.append(line)
            continue
        heading_phase = False
    return normalize_whitespace(" ".join(heading_lines[:3])) if heading_lines else None


def extract_book_name(label: str | None) -> str | None:
    if not label or not isinstance(label, str):
        return None
    s = re.sub(r"\s*\([^)]*\)\s*$", "", label)
    s = re.sub(r"[:;,-]+\s*$", "", s).strip()
    s = re.split(r"/|-|—", s, maxsplit=1)[0].strip()
    return s or None


def usable(meta: dict[str, Any] | None, min_count: int) -> bool:
    if not meta:
        return False
    if meta.get("iscit"):
        return bool(meta.get("label"))
    if meta.get("count") is not None and meta["count"] < min_count:
        return False
    return bool(meta.get("label"))


def read_raw_text_prefix(public_dir: str, vol_id: str, raw_info: dict[str, Any] | None, max_bytes: int) -> str | None:
    raw_file = (raw_info or {}).get("file")
    if not raw_file:
        return None
    public_root = pathlib.Path(public_dir).resolve()
    txt_path = public_root.parents[1] / "teste" / vol_id / "text" / raw_file
    try:
        with txt_path.open("rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return None


@dataclass
class PageRecord:
    url: str
    html: str
    page: int


def build_record_html(url: str, meta: dict[str, Any], filters: dict[str, list[str]] | None, body_content: str) -> str:
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="pt-BR">')
    parts.append("<head>")
    parts.append('  <meta charset="utf-8">')
    if meta.get("title"):
        title = escape_html(meta["title"]) + escape_html((" - " + meta["work"]) if meta.get("work") else "")
        parts.append(f"  <title>{title}</title>")
    for k, v in meta.items():
        if k == "title":
            continue
        parts.append(f'  <meta data-pagefind-meta="{escape_html(k)}" content="{escape_html(v)}">')
    if filters:
        for fk, values in filters.items():
            for item in values:
                parts.append(f'  <meta data-pagefind-meta="filter:{escape_html(fk)}" content="{escape_html(item)}">')
    if meta.get("author"):
        parts.append(f'  <meta data-pagefind-meta="author" content="{escape_html(meta["author"])}">')
    if meta.get("work"):
        parts.append(f'  <meta data-pagefind-meta="title" content="{escape_html(meta["work"])}">')
    parts.append(f'  <meta data-pagefind-meta="url" content="{escape_html(url)}">')
    parts.append("</head>")
    parts.append("<body>")
    parts.append('  <main data-pagefind-body>')
    parts.append(f"    <p>{escape_html(body_content)}</p>")
    parts.append("  </main>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def build_page_record(args: tuple[dict[str, Any], str, dict[str, Any], dict[str, Any] | None, str, int, int]) -> PageRecord:
    vol, vid, p, raw_info, public_dir, min_count, raw_title_bytes = args
    raw_text = read_raw_text_prefix(public_dir, vid, raw_info, raw_title_bytes)
    extracted_title = extract_title_from_text(raw_text)

    kw_map = vol["_kw_map"]
    kw_metas = [
        kw_map.get(str(kid))
        for kid in p.get("keyword_ids", [])
    ]
    kw_metas = [kw for kw in kw_metas if usable(kw, min_count)]
    kw_labels = [kw["label"] for kw in kw_metas]
    book_names: list[str] = []
    book_set: set[str] = set()
    for m in kw_metas:
        if m.get("iscit"):
            bn = extract_book_name(m.get("label"))
            if bn and bn not in book_set:
                book_set.add(bn)
                book_names.append(bn)

    top_keywords = kw_labels[:3]
    enriched_title = f'{vid} p.{p["page"]} — {" • ".join(top_keywords)}' if top_keywords else f'{vid} p.{p["page"]}'
    content_pieces = [p.get("summary_page") or ""]
    if p.get("author"):
        content_pieces.append(p["author"])
    if p.get("work"):
        content_pieces.append(p["work"])
    if extracted_title:
        content_pieces.append(extracted_title)
    if top_keywords:
        content_pieces.append(" ".join(top_keywords))
    if kw_labels:
        content_pieces.append(" ".join(kw_labels))
    if book_names:
        content_pieces.append(" ".join(book_names))
    content = normalize_whitespace(re.sub(r"<[^>]*>", " ", " ".join(content_pieces)))

    filters = {
        "collection": [vol["collection_id"]],
        "volume": [vid],
    }
    if book_names:
        filters["book"] = book_names

    page_meta = {
        "title": extracted_title or enriched_title,
        "volume": vid,
        "page": str(p["page"]),
        "collection": vol["collection_id"],
        "author": p.get("author"),
        "work": p.get("work"),
    }
    if book_names:
        page_meta["books"] = " • ".join(book_names)

    url = f'{PUBLIC_BASE}/viewer?doc={vid}&page={p["page"]}'
    html = build_record_html(url, page_meta, filters, content)
    return PageRecord(url=url, html=html, page=int(p["page"]))


class PagefindServiceClient:
    def __init__(self, binary_path: pathlib.Path) -> None:
        self.proc = subprocess.Popen(
            [str(binary_path), "--service"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            bufsize=0,
        )
        assert self.proc.stdin and self.proc.stdout
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        self.message_id = 0
        self.pending: dict[int, Queue[dict[str, Any]]] = {}
        self.lock = threading.Lock()
        self.reader_exc: Exception | None = None
        self.stop_event = threading.Event()
        self.reader = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader.start()

    @staticmethod
    def encode_message(message: dict[str, Any]) -> bytes:
        return base64.b64encode(json.dumps(message, separators=(",", ":")).encode("utf-8")) + b","

    def _reader_loop(self) -> None:
        try:
            buffer = b""
            while not self.stop_event.is_set():
                comma = buffer.find(b",")
                if comma != -1:
                    chunk = buffer[:comma]
                    buffer = buffer[comma + 1 :]
                    if not chunk:
                        continue
                    decoded = base64.b64decode(chunk)
                    parsed = json.loads(decoded.decode("utf-8"))
                    message_id = parsed.get("message_id")
                    if isinstance(message_id, int) and message_id in self.pending:
                        self.pending[message_id].put(parsed)
                    elif os.environ.get("PAGEFIND_PROTOCOL_DEBUG") == "1":
                        print(f"[pagefind:python] unmatched message: {parsed}", file=sys.stderr)
                    continue

                data = self.stdout.read(4096)
                if not data:
                    return
                buffer += data
        except Exception as exc:
            self.reader_exc = exc
            for q in self.pending.values():
                q.put({"__reader_exc__": exc})

    def send(self, payload: dict[str, Any], expected_type: str | None = None) -> dict[str, Any]:
        with self.lock:
            self.message_id += 1
            message_id = self.message_id
            q: Queue[dict[str, Any]] = Queue(maxsize=1)
            self.pending[message_id] = q

        message = {"message_id": message_id, "payload": payload}
        encoded = self.encode_message(message)
        self.stdin.write(encoded)
        self.stdin.flush()

        if os.environ.get("PAGEFIND_PROTOCOL_DEBUG") == "1":
            print(
                f"[pagefind:python] send message_id={message_id} type={payload.get('type')} bytes={len(encoded)}",
                file=sys.stderr,
            )

        timeout_s = float(os.environ.get("PAGEFIND_IPC_TIMEOUT", "30"))
        try:
            response = q.get(timeout=timeout_s)
        except Empty as exc:
            raise TimeoutError(f"Timed out waiting for Pagefind response to message_id={message_id}") from exc
        finally:
            with self.lock:
                self.pending.pop(message_id, None)

        if "__reader_exc__" in response:
            raise RuntimeError(f"Pagefind reader failed: {response['__reader_exc__']}")

        payload = response.get("payload") or {}
        if payload.get("type") == "Error":
            raise RuntimeError(payload.get("message") or "Pagefind error")
        if expected_type and payload.get("type") != expected_type:
            raise RuntimeError(
                f"Unexpected Pagefind response type: expected {expected_type}, got {payload.get('type')}"
            )
        return payload

    def close(self) -> None:
        try:
            self.stop_event.set()
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
            if self.stdin:
                self.stdin.close()
            if self.stdout:
                self.stdout.close()
            if self.stderr:
                self.stderr.close()
        except OSError:
            pass


def parallel_map(items: list[Any], concurrency: int, fn) -> list[Any]:
    if not items:
        return []
    with mp.Pool(processes=min(concurrency, len(items))) as pool:
        return list(pool.imap(fn, items, chunksize=1))


def main() -> int:
    global PUBLIC_BASE
    args = parse_args()
    public_dir = args.public
    PUBLIC_BASE = args.base.rstrip("/")
    public_path = pathlib.Path(public_dir)
    out_dir = pathlib.Path(args.out)

    volumes_data = read_json(public_path / "volumes.json")
    volumes = volumes_data.get("volumes", [])
    keywords_data = read_json(public_path / "dict" / "keywords.json")
    keywords = keywords_data.get("items", [])
    kw_map = {str(k["id"]): k for k in keywords}

    if out_dir.exists():
        print(f"Limpando índice anterior em {out_dir}...")
        shutil.rmtree(out_dir)
    ensure_dir(out_dir)

    binary_path = resolve_pagefind_binary()
    service = PagefindServiceClient(binary_path)
    start_time = time.time()
    total_records = 0
    report_every = 5000

    init = service.send({"type": "NewIndex", "config": build_new_index_config()}, expected_type="NewIndex")
    index_id = init["index_id"]

    for vol in volumes:
        if args.bench_volume and vol["id"] != args.bench_volume:
            continue
        meta_path = public_path / vol["meta_url"]
        try:
            meta_obj = read_json(meta_path)
        except OSError as e:
            print(f"Erro lendo meta de {vol['id']}: {e}", file=sys.stderr)
            continue
        vol["_kw_map"] = kw_map
        for pb in meta_obj.get("page_blocks", []):
            try:
                block = read_json(public_path / pb["file"])
            except OSError:
                continue
            pages = block.get("pages", [])
            if not pages:
                continue

            records = parallel_map(
                [(vol, vol["id"], p, p.get("raw"), public_dir, args.min_count, args.raw_title_bytes) for p in pages],
                args.io_concurrency,
                build_page_record,
            )

            for rec in records:
                service.send(
                    {
                        "type": "AddFile",
                        "index_id": index_id,
                        "url": rec.url,
                        "file_contents": rec.html,
                    },
                    expected_type="IndexedFile",
                )
                total_records += 1
                if total_records % report_every == 0:
                    elapsed = time.time() - start_time
                    rps = total_records / max(0.001, elapsed)
                    print(
                        f"[{elapsed:.1f}s] {total_records} records indexed ({rps:.1f} r/s) — last: {vol['id']} p.{rec.page}"
                    )

    if not total_records:
        print("Nenhum record adicionado ao índice Pagefind. Abortando.", file=sys.stderr)
        return 1

    print("Escrevendo arquivos do índice (aguarde)...")
    service.send({"type": "WriteFiles", "index_id": index_id, "output_path": str(out_dir)}, expected_type="WriteFiles")
    service.close()
    elapsed = time.time() - start_time
    print(f"[OK] Pagefind index written to {out_dir} (total records: {total_records}, time: {elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

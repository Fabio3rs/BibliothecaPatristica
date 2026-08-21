#!/usr/bin/env python3
"""Submit every URL from a sitemap (including sitemap indexes) to IndexNow.

Examples:
    python tools/submit_indexnow.py
    python tools/submit_indexnow.py --submit
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_SITEMAP = (
    "https://fabio3rs.github.io/BibliothecaPatristica/sitemap-index.xml"
)
DEFAULT_KEY = "d8dfbdf837a54390af78ad1b79844e16"
DEFAULT_KEY_LOCATION = (
    "https://fabio3rs.github.io/BibliothecaPatristica/"
    "d8dfbdf837a54390af78ad1b79844e16.txt"
)
DEFAULT_ENDPOINT = "https://api.indexnow.org/IndexNow"
MAX_BATCH_SIZE = 10_000


@dataclass(frozen=True)
class Download:
    content: bytes
    content_encoding: str = ""


Downloader = Callable[[str, float], Download]


def download(url: str, timeout: float) -> Download:
    request = Request(url, headers={"User-Agent": "BibliothecaPatristica-IndexNow/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return Download(
            content=response.read(),
            content_encoding=response.headers.get("Content-Encoding", ""),
        )


def parse_sitemap(document: Download, source_url: str) -> tuple[str, list[str]]:
    content = document.content
    if source_url.lower().endswith(".gz") or document.content_encoding.lower() == "gzip":
        content = gzip.decompress(content)

    root = ET.fromstring(content)
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name not in {"sitemapindex", "urlset"}:
        raise ValueError(f"XML inesperado em {source_url}: raiz <{root_name}>")

    entry_name = "sitemap" if root_name == "sitemapindex" else "url"
    locations = []
    for entry in root:
        if entry.tag.rsplit("}", 1)[-1] != entry_name:
            continue
        for child in entry:
            if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                locations.append(child.text.strip())
                break
    return root_name, locations


def collect_sitemap_urls(
    sitemap_url: str,
    timeout: float,
    downloader: Downloader = download,
) -> list[str]:
    pending = [sitemap_url]
    visited_sitemaps: set[str] = set()
    urls: list[str] = []
    seen_urls: set[str] = set()

    while pending:
        current = pending.pop(0)
        if current in visited_sitemaps:
            continue
        visited_sitemaps.add(current)

        kind, locations = parse_sitemap(downloader(current, timeout), current)
        if kind == "sitemapindex":
            pending.extend(
                location
                for location in locations
                if location not in visited_sitemaps
            )
            continue

        for location in locations:
            if location not in seen_urls:
                seen_urls.add(location)
                urls.append(location)

    return urls


def validate_urls(urls: Iterable[str], host: str) -> None:
    invalid = [url for url in urls if (urlsplit(url).hostname or "").lower() != host]
    if invalid:
        examples = ", ".join(invalid[:3])
        raise ValueError(f"URLs fora do host {host}: {examples}")


def normalize_page_url(url: str) -> str:
    """Use the effective GitHub Pages URL for extensionless HTML routes."""
    parts = urlsplit(url)
    last_segment = parts.path.rsplit("/", 1)[-1]
    if parts.path.endswith("/") or "." in last_segment:
        return url
    return urlunsplit(parts._replace(path=f"{parts.path}/"))


def batches(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def submit_batch(
    endpoint: str,
    host: str,
    key: str,
    key_location: str,
    urls: list[str],
    timeout: float,
) -> int:
    payload = json.dumps(
        {
            "host": host,
            "key": key,
            "keyLocation": key_location,
            "urlList": urls,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "BibliothecaPatristica-IndexNow/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        response.read()
        return response.status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lê todas as URLs de um sitemap e as envia à API IndexNow."
    )
    parser.add_argument("--sitemap", default=DEFAULT_SITEMAP)
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--key-location", default=DEFAULT_KEY_LOCATION)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_BATCH_SIZE,
        help=f"URLs por requisição (máximo: {MAX_BATCH_SIZE})",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--keep-sitemap-urls",
        action="store_true",
        help="Não adiciona / às rotas HTML sem extensão.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Faz o POST real; sem esta opção, apenas simula o envio.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        print(
            f"Erro: --batch-size deve estar entre 1 e {MAX_BATCH_SIZE}.",
            file=sys.stderr,
        )
        return 2

    host = (urlsplit(args.key_location).hostname or "").lower()
    if not host:
        print("Erro: --key-location precisa ser uma URL absoluta.", file=sys.stderr)
        return 2

    try:
        sitemap_urls = collect_sitemap_urls(args.sitemap, args.timeout)
        urls = (
            sitemap_urls
            if args.keep_sitemap_urls
            else list(dict.fromkeys(normalize_page_url(url) for url in sitemap_urls))
        )
        if not urls:
            raise ValueError("nenhuma URL encontrada nos sitemaps")
        validate_urls(urls, host)
    except (ET.ParseError, ValueError, HTTPError, URLError, OSError) as error:
        print(f"Erro ao ler os sitemaps: {error}", file=sys.stderr)
        return 1

    all_batches = list(batches(urls, args.batch_size))
    print(f"Encontradas {len(urls)} URLs em {len(all_batches)} lote(s).")
    normalized_count = sum(
        original != normalize_page_url(original) for original in sitemap_urls
    )
    if normalized_count and not args.keep_sitemap_urls:
        print(f"Normalizadas {normalized_count} rotas com / final.")
    print(f"Host: {host}")
    print(f"Chave: {args.key_location}")

    if not args.submit:
        print("Simulação concluída. Use --submit para enviar ao IndexNow.")
        return 0

    for number, url_batch in enumerate(all_batches, start=1):
        try:
            status = submit_batch(
                endpoint=args.endpoint,
                host=host,
                key=args.key,
                key_location=args.key_location,
                urls=url_batch,
                timeout=args.timeout,
            )
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace").strip()
            suffix = f": {details}" if details else ""
            print(
                f"Falha no lote {number}: HTTP {error.code}{suffix}",
                file=sys.stderr,
            )
            return 1
        except (URLError, OSError) as error:
            print(f"Falha no lote {number}: {error}", file=sys.stderr)
            return 1

        print(
            f"Lote {number}/{len(all_batches)} enviado: "
            f"HTTP {status} ({len(url_batch)} URLs)."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

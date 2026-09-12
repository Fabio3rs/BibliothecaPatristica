#!/usr/bin/env python3
"""Validate the committed Scripture publication and its generated site."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PARSER = "2.1.4-poc"
EXPECTED_DETECTOR = "7"
EXPECTED_VERSIFICATION = "vulgate-clementine"
EXPECTED_BOOKS = {
    "1 corintios",
    "1 cronicas",
    "1 joao",
    "1 macabeus",
    "1 pedro",
    "1 reis",
    "1 samuel",
    "1 tessalonicenses",
    "1 timoteo",
    "2 corintios",
    "2 cronicas",
    "2 joao",
    "2 macabeus",
    "2 pedro",
    "2 reis",
    "2 samuel",
    "2 tessalonicenses",
    "2 timoteo",
    "3 joao",
    "abdias",
    "ageu",
    "amos",
    "apocalipse",
    "atos",
    "baruc",
    "cantico dos canticos",
    "colossenses",
    "daniel",
    "deuteronomio",
    "eclesiastes",
    "eclesiastico",
    "efesios",
    "esdras",
    "ester",
    "exodo",
    "ezequiel",
    "filemon",
    "filipenses",
    "galatas",
    "genesis",
    "habacuc",
    "hebreus",
    "isaias",
    "jeremias",
    "jo",
    "joao",
    "joel",
    "jonas",
    "josue",
    "judas",
    "judite",
    "juizes",
    "lamentacoes",
    "levitico",
    "lucas",
    "malaquias",
    "marcos",
    "mateus",
    "miqueias",
    "naum",
    "neemias",
    "numeros",
    "oseias",
    "proverbios",
    "romanos",
    "rute",
    "sabedoria",
    "salmos",
    "sofonias",
    "tiago",
    "tito",
    "tobias",
    "zacarias",
}


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_gzip_json(path: Path) -> tuple[object, bytes]:
    raw = gzip.decompress(path.read_bytes())
    return json.loads(raw), raw


def validate_book_shards(public_dir: Path) -> tuple[int, int]:
    scripture_dir = public_dir / "scripture" / "v3"
    manifest_path = scripture_dir / "manifest.json"
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("scripture manifest must be an object")
    routes = manifest.get("routes")
    if not isinstance(routes, dict) or set(routes) != EXPECTED_BOOKS:
        missing = sorted(EXPECTED_BOOKS - set(routes or {}))
        extra = sorted(set(routes or {}) - EXPECTED_BOOKS)
        raise ValueError(f"book route mismatch: missing={missing}, extra={extra}")
    expected_metadata = {
        "parser": EXPECTED_PARSER,
        "detector": EXPECTED_DETECTOR,
        "v11n": EXPECTED_VERSIFICATION,
    }
    for key, expected in expected_metadata.items():
        if str(manifest.get(key)) != expected:
            raise ValueError(
                f"manifest {key}={manifest.get(key)!r}; expected {expected!r}"
            )

    reference_count = 0
    posting_count = 0
    for book_key, route in routes.items():
        if not isinstance(route, dict):
            raise ValueError(f"invalid route for {book_key}")
        filename = str(route.get("url") or "")
        if Path(filename).name != filename or not filename.endswith(".json.gz"):
            raise ValueError(f"unsafe shard URL for {book_key}: {filename!r}")
        payload, raw = load_gzip_json(scripture_dir / filename)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid shard payload for {book_key}")
        if payload.get("book", [None])[0] != book_key:
            raise ValueError(f"book mismatch in {filename}")
        for key, expected in expected_metadata.items():
            if str(payload.get(key)) != expected:
                raise ValueError(f"{filename}: {key} does not match manifest")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != route.get("sha256"):
            raise ValueError(f"sha256 mismatch for {filename}")
        references = payload.get("references")
        if not isinstance(references, list) or len(references) != route.get("references"):
            raise ValueError(f"reference count mismatch for {filename}")
        reference_count += len(references)
        postings = sum(
            sum(len(volume_postings[1]) // 2 for volume_postings in reference[2])
            for reference in references
        )
        if postings != route.get("reference_page_pairs"):
            raise ValueError(f"posting count mismatch for {filename}")
        posting_count += postings
    return reference_count, posting_count


def validate_reference_payloads(public_dir: Path) -> tuple[int, int]:
    reference_dir = public_dir / "scripture" / "references" / "v1"
    manifest = load_json(reference_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("reference manifest must be an object")
    files = sorted(reference_dir.glob("**/*.json.gz"))
    if len(files) != int(manifest.get("payload_files") or -1):
        raise ValueError("reference payload file count does not match manifest")
    locations = 0
    for path in files:
        payload, _raw = load_gzip_json(path)
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError(f"invalid reference payload: {path}")
        data = payload.get("d")
        if not isinstance(data, list) or len(data) != 2 or not isinstance(data[1], list):
            raise ValueError(f"invalid reference rows: {path}")
        locations += len(data[1])
    if locations != int(manifest.get("location_occurrences") or -1):
        raise ValueError("reference location count does not match manifest")
    return len(files), locations


def validate_built_site(dist_dir: Path) -> None:
    scripture_root = dist_dir / "indices-alfabeticos" / "scripture"
    html_files = sorted(scripture_root.glob("**/index.html"))
    if not html_files:
        raise ValueError("built Scripture pages were not found")
    double_route = re.compile(r"indices-alfabeticos/scripture//")
    for path in html_files:
        html = path.read_text(encoding="utf-8")
        if double_route.search(html):
            raise ValueError(f"double-slash Scripture URL in {path}")
    reference_html = (scripture_root / "reference" / "index.html").read_text(
        encoding="utf-8"
    )
    if 'name="robots" content="noindex,follow"' not in reference_html:
        raise ValueError("parameterized reference route must be noindex,follow")
    if 'rel="canonical"' in reference_html:
        raise ValueError("parameterized reference route must not have a generic canonical")
    sitemap = (dist_dir / "sitemap-scripture.xml").read_text(encoding="utf-8")
    if "/scripture/reference/" in sitemap:
        raise ValueError("parameterized reference route must not appear in sitemap")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=ROOT / "web" / "public")
    parser.add_argument("--dist", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    references, postings = validate_book_shards(args.public.resolve())
    payloads, locations = validate_reference_payloads(args.public.resolve())
    if args.dist:
        validate_built_site(args.dist.resolve())
    print(
        json.dumps(
            {
                "books": len(EXPECTED_BOOKS),
                "references": references,
                "reference_page_pairs": postings,
                "detail_payloads": payloads,
                "detail_locations": locations,
                "built_site": bool(args.dist),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

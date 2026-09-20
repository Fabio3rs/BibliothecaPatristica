#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_LOCATIONS_MAGIC = b"PIDLOC1\0"
SIDECAR_MAGIC = b"BSDI1"
MAX_FORMAT_VALUE = 0x7FFF_FFFF
MAX_VARUINT = 0xFFFF_FFFF

_WORKER_LOCATIONS: dict[tuple[str, int], int] | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gera mapas compactos pagina biblica -> doc_id usando o artefato "
            "nativo document_locations.bin.gz."
        )
    )
    parser.add_argument("--public", dest="public_dir", type=Path, default=ROOT / "web/public")
    parser.add_argument("--search-manifest", type=Path)
    parser.add_argument("--scripture-manifest", type=Path)
    parser.add_argument("--out", dest="output_dir", type=Path)
    parser.add_argument("--min-global-coverage", type=float, default=0.99)
    parser.add_argument("--min-book-coverage", type=float, default=0.95)
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    args.public_dir = args.public_dir.resolve()
    args.search_manifest = (
        args.search_manifest or args.public_dir / "indexador/search/manifest.json"
    ).resolve()
    args.scripture_manifest = (
        args.scripture_manifest or args.public_dir / "scripture/v3/manifest.json"
    ).resolve()
    args.output_dir = (
        args.output_dir or args.search_manifest.parent / "scripture-docids"
    ).resolve()
    for option, value in (
        ("--min-global-coverage", args.min_global_coverage),
        ("--min-book-coverage", args.min_book_coverage),
    ):
        if not 0 <= value <= 1:
            parser.error(f"{option} deve estar entre 0 e 1")
    if args.jobs < 1:
        parser.error("--jobs deve ser um inteiro maior que zero")
    return args


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON deve conter um objeto: {path}")
    return value


def read_maybe_gzip_json(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"JSON deve conter um objeto: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_inside(root: Path, relative: str, label: str) -> Path:
    root = root.resolve()
    target = (root / str(relative)).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f"Caminho de {label} invalido: {relative}")
    return target


def assert_safe_output_dir(output_dir: Path, search_manifest_path: Path) -> None:
    search_root = search_manifest_path.parent.resolve()
    target = output_dir.resolve()
    if target == search_root or search_root not in target.parents:
        raise ValueError("O diretorio de sidecars deve ficar dentro da pasta do indice de busca")


def read_varuint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(5):
        if offset >= len(payload):
            raise ValueError("Varuint truncado")
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > MAX_VARUINT:
                raise ValueError("Varuint excede uint32")
            return value, offset
        shift += 7
    raise ValueError("Varuint invalido")


def append_varuint(output: bytearray, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_VARUINT:
        raise ValueError(f"Varuint fora do intervalo: {value}")
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)


def decode_document_locations_payload(
    payload: bytes,
) -> tuple[dict[tuple[str, int], int], str, int]:
    if len(payload) > MAX_FORMAT_VALUE or not payload.startswith(DOCUMENT_LOCATIONS_MAGIC):
        raise ValueError("Formato de document_locations.bin.gz nao suportado")
    offset = len(DOCUMENT_LOCATIONS_MAGIC)
    volume_count, offset = read_varuint(payload, offset)
    if volume_count > MAX_FORMAT_VALUE:
        raise ValueError("Quantidade de volumes excede int32")
    volumes: list[str] = []
    for _ in range(volume_count):
        length, offset = read_varuint(payload, offset)
        if length < 1 or length > MAX_FORMAT_VALUE or offset + length > len(payload):
            raise ValueError("Identificador de volume invalido")
        try:
            volume_id = payload[offset : offset + length].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Identificador de volume nao e UTF-8") from error
        if not volume_id or any(delimiter in volume_id for delimiter in ("\0", ":", "\r", "\n")):
            raise ValueError(f"Identificador de volume invalido: {volume_id!r}")
        if volume_id in volumes:
            raise ValueError(f"Identificador de volume duplicado: {volume_id}")
        volumes.append(volume_id)
        offset += length

    document_count, offset = read_varuint(payload, offset)
    if document_count > MAX_FORMAT_VALUE:
        raise ValueError("Quantidade de documentos excede int32")
    locations: dict[tuple[str, int], int] = {}
    build_hash = hashlib.sha256()
    for doc_id in range(document_count):
        volume_index, offset = read_varuint(payload, offset)
        page, offset = read_varuint(payload, offset)
        if volume_index >= len(volumes) or not 1 <= page <= MAX_FORMAT_VALUE:
            raise ValueError(f"Registro de localizacao invalido no doc_id {doc_id}")
        key = (volumes[volume_index], page)
        previous = locations.get(key)
        if previous is not None and previous != doc_id:
            raise ValueError(f"Localizacao duplicada: {key[0]}:{key[1]}")
        locations[key] = doc_id
        build_hash.update(f"{doc_id}\0{key[0]}:{page}\n".encode())
    if offset != len(payload):
        raise ValueError("document_locations.bin.gz contem bytes excedentes")
    return locations, f"sha256:{build_hash.hexdigest()}", len(volumes)


def load_document_locations(
    path: Path,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[tuple[str, int], int], str, int, int]:
    compressed = path.read_bytes()
    payload = gzip.decompress(compressed)
    locations, build_id, volume_count = decode_document_locations_payload(payload)
    if metadata:
        expected = {
            "build_id": build_id,
            "documents": len(locations),
            "volumes": volume_count,
            "raw_bytes": len(payload),
            "gzip_bytes": len(compressed),
        }
        for field, actual in expected.items():
            if metadata.get(field) != actual:
                raise ValueError(
                    f"document_locations.bin.gz: {field} incompativel "
                    f"({metadata.get(field)!r} != {actual!r})"
                )
    return locations, build_id, len(payload), len(compressed)


def collect_shard_locations(shard: dict[str, Any]) -> set[tuple[str, int]]:
    volumes = shard.get("volumes") or []
    locations: set[tuple[str, int]] = set()
    for reference in shard.get("references") or []:
        postings_by_volume = reference[2] if len(reference) > 2 else []
        for volume_posting in postings_by_volume or []:
            volume_index = volume_posting[0]
            if not isinstance(volume_index, int) or not 0 <= volume_index < len(volumes):
                raise ValueError("Shard biblico referencia volume inexistente")
            page = 0
            postings = volume_posting[1] or []
            if len(postings) % 2:
                raise ValueError("Postings biblicos possuem tamanho impar")
            for index in range(0, len(postings), 2):
                page += postings[index]
                if not isinstance(page, int) or page < 1:
                    raise ValueError("Shard biblico contem pagina invalida")
                locations.add((str(volumes[volume_index]), page))
    return locations


def zigzag_encode(value: int) -> int:
    if not -MAX_FORMAT_VALUE <= value <= MAX_FORMAT_VALUE:
        raise ValueError(f"Delta de doc_id fora do intervalo: {value}")
    return (value << 1) ^ (value >> 31)


def encode_scripture_docid_sidecar(
    volumes: list[Any],
    documents: list[tuple[str, int, int]],
) -> bytes:
    volume_indexes = {str(volume_id): index for index, volume_id in enumerate(volumes)}
    groups: dict[int, dict[int, int]] = {}
    for volume_id, page, doc_id in documents:
        volume_index = volume_indexes.get(str(volume_id))
        if volume_index is None:
            continue
        if not 1 <= page <= MAX_FORMAT_VALUE or not 0 <= doc_id <= MAX_FORMAT_VALUE:
            raise ValueError(f"Mapeamento biblico invalido: {volume_id}:{page} -> {doc_id}")
        pages = groups.setdefault(volume_index, {})
        previous = pages.get(page)
        if previous is not None and previous != doc_id:
            raise ValueError(f"doc_id conflitante para {volume_id}:{page}")
        pages[page] = doc_id

    output = bytearray(SIDECAR_MAGIC)
    append_varuint(output, len(groups))
    for volume_index in sorted(groups):
        pages = groups[volume_index]
        append_varuint(output, volume_index)
        append_varuint(output, len(pages))
        previous_page = 0
        previous_doc_id = 0
        for page, doc_id in sorted(pages.items()):
            append_varuint(output, page - previous_page)
            append_varuint(output, zigzag_encode(doc_id - previous_doc_id))
            previous_page = page
            previous_doc_id = doc_id
    return bytes(output)


def coverage_ratio(mapped: int, total: int) -> float:
    return mapped / total if total else 1.0


def coverage_summary(books: dict[str, dict[str, Any]]) -> dict[str, int | float]:
    summary = {
        "pages_total": sum(int(book.get("pages_total", 0)) for book in books.values()),
        "pages_mapped": sum(int(book.get("pages_mapped", 0)) for book in books.values()),
        "pages_missing": sum(int(book.get("pages_missing", 0)) for book in books.values()),
    }
    return {**summary, "ratio": coverage_ratio(summary["pages_mapped"], summary["pages_total"])}


def validate_sidecar_publication(
    search_manifest: dict[str, Any],
    scripture_manifest: dict[str, Any],
    sidecar_manifest: dict[str, Any],
    output_dir: Path,
    min_global_coverage: float = 0.99,
    min_book_coverage: float = 0.95,
) -> dict[str, int | float]:
    if sidecar_manifest.get("schema") != "bibliotheca-scripture-docids-v1":
        raise ValueError("Schema de sidecar biblico invalido")
    search_entries = search_manifest.get("indexes") or []
    sidecar_indexes = sidecar_manifest.get("indexes") or {}
    if len(sidecar_indexes) != len(search_entries):
        raise ValueError("Quantidade de indices diverge entre busca e sidecars biblicos")
    expected_books = list((scripture_manifest.get("routes") or {}).keys())
    publication: dict[str, int | float] = {
        "indexes": 0,
        "books": 0,
        "pages_total": 0,
        "pages_mapped": 0,
        "pages_missing": 0,
    }

    for entry in search_entries:
        index = sidecar_indexes.get(entry.get("id"))
        if not entry.get("build_id") or not index or entry["build_id"] != index.get("build_id"):
            raise ValueError(f"Indice {entry.get('id')}: build_id ausente ou incompativel")
        if int(index.get("documents", -1)) != int(entry.get("documents", -2)):
            raise ValueError(f"Indice {entry.get('id')}: contagem de documentos incompativel")
        books = index.get("books") or {}
        if len(books) != len(expected_books):
            raise ValueError(f"Indice {entry.get('id')}: quantidade de livros biblicos incompativel")
        for book_key in expected_books:
            book = books.get(book_key)
            if not book or not book.get("url"):
                raise ValueError(f"Indice {entry.get('id')}: sidecar ausente para {book_key}")
            fields = ("pages_total", "pages_mapped", "pages_missing", "gzip_bytes", "raw_bytes")
            if any(isinstance(book.get(field), bool) or not isinstance(book.get(field), int) for field in fields):
                raise ValueError(f"Indice {entry.get('id')}/{book_key}: metricas invalidas")
            total = book["pages_total"]
            mapped = book["pages_mapped"]
            missing = book["pages_missing"]
            if min(total, mapped, missing) < 0 or mapped + missing != total:
                raise ValueError(f"Indice {entry.get('id')}/{book_key}: cobertura inconsistente")
            ratio = coverage_ratio(mapped, total)
            if total and ratio < min_book_coverage:
                raise ValueError(
                    f"Indice {entry.get('id')}/{book_key}: cobertura {ratio * 100:.3f}% abaixo "
                    f"do minimo {min_book_coverage * 100:.3f}%"
                )
            file_path = resolve_inside(output_dir, book["url"], "sidecar")
            compressed = file_path.read_bytes()
            raw = gzip.decompress(compressed)
            if (
                len(compressed) != book["gzip_bytes"]
                or len(raw) != book["raw_bytes"]
                or not raw.startswith(SIDECAR_MAGIC)
            ):
                raise ValueError(f"Indice {entry.get('id')}/{book_key}: arquivo publicado incompativel")

        actual_coverage = coverage_summary(books)
        declared_coverage = index.get("coverage") or actual_coverage
        for field in ("pages_total", "pages_mapped", "pages_missing"):
            if declared_coverage.get(field) != actual_coverage[field]:
                raise ValueError(f"Indice {entry.get('id')}: resumo de cobertura incompativel em {field}")
        if actual_coverage["ratio"] < min_global_coverage:
            raise ValueError(
                f"Indice {entry.get('id')}: cobertura global {actual_coverage['ratio'] * 100:.3f}% "
                f"abaixo do minimo {min_global_coverage * 100:.3f}%"
            )
        publication["indexes"] += 1
        publication["books"] += len(expected_books)
        for field in ("pages_total", "pages_mapped", "pages_missing"):
            publication[field] += actual_coverage[field]
    publication["ratio"] = coverage_ratio(
        int(publication["pages_mapped"]), int(publication["pages_total"])
    )
    return publication


def _initialize_worker(document_locations_path: str) -> None:
    global _WORKER_LOCATIONS
    _WORKER_LOCATIONS = load_document_locations(Path(document_locations_path))[0]


def _build_book(task: tuple[str, str, str]) -> tuple[str, dict[str, Any]]:
    book_key, route_path, index_dir = task
    if _WORKER_LOCATIONS is None:
        raise RuntimeError("Worker de sidecars nao foi inicializado")
    shard = read_maybe_gzip_json(Path(route_path))
    locations = collect_shard_locations(shard)
    mapped = [
        (volume_id, page, _WORKER_LOCATIONS[(volume_id, page)])
        for volume_id, page in locations
        if (volume_id, page) in _WORKER_LOCATIONS
    ]
    raw = encode_scripture_docid_sidecar(shard.get("volumes") or [], mapped)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()[:16]
    filename = f"{book_key.replace(' ', '-')}.{digest}.bin.gz"
    target = resolve_inside(Path(index_dir), filename, "sidecar")
    target.write_bytes(compressed)
    return book_key, {
        "url": f"{Path(index_dir).name}/{filename}",
        "raw_bytes": len(raw),
        "gzip_bytes": len(compressed),
        "pages_total": len(locations),
        "pages_mapped": len(mapped),
        "pages_missing": len(locations) - len(mapped),
    }


def document_locations_path(search_root: Path, entry: dict[str, Any]) -> Path:
    metadata = entry.get("document_locations") or {}
    relative = metadata.get("url")
    if not relative:
        raise ValueError(f"Indice {entry.get('id')}: document_locations.url ausente")
    return resolve_inside(search_root, relative, "document_locations")


def build_sidecars(args: argparse.Namespace) -> dict[str, int | float]:
    search_manifest = read_json(args.search_manifest)
    scripture_manifest = read_json(args.scripture_manifest)
    assert_safe_output_dir(args.output_dir, args.search_manifest)
    temporary_output = Path(
        tempfile.mkdtemp(prefix=f"{args.output_dir.name}.tmp-", dir=args.output_dir.parent)
    )
    sidecar_manifest: dict[str, Any] = {
        "schema": "bibliotheca-scripture-docids-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "scripture_manifest": args.scripture_manifest.relative_to(args.public_dir).as_posix(),
        "indexes": {},
    }

    try:
        for entry in search_manifest.get("indexes") or []:
            artifact_metadata = entry.get("document_locations") or {}
            artifact_path = document_locations_path(args.search_manifest.parent, entry)
            locations, build_id, _, _ = load_document_locations(artifact_path, artifact_metadata)
            if entry.get("build_id") != build_id or int(entry.get("documents", -1)) != len(locations):
                raise ValueError(f"Indice {entry.get('id')}: manifesto diverge de document_locations.bin.gz")
            print(
                f"[docids] {entry.get('id')}: {len(locations)} localizacoes nativas; "
                f"gerando livros com {args.jobs} processo(s)...",
                flush=True,
            )
            index_dir = temporary_output / str(entry["id"]).lower()
            index_dir.mkdir(parents=True)
            tasks = [
                (
                    book_key,
                    str(resolve_inside(args.scripture_manifest.parent, route["url"], "shard biblico")),
                    str(index_dir),
                )
                for book_key, route in (scripture_manifest.get("routes") or {}).items()
            ]
            if args.jobs == 1:
                _initialize_worker(str(artifact_path))
                results = map(_build_book, tasks)
                books = dict(results)
            else:
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=min(args.jobs, len(tasks) or 1),
                    initializer=_initialize_worker,
                    initargs=(str(artifact_path),),
                ) as executor:
                    books = dict(executor.map(_build_book, tasks))
            sidecar_manifest["indexes"][entry["id"]] = {
                "build_id": build_id,
                "documents": len(locations),
                "books": books,
                "coverage": coverage_summary(books),
            }

        write_json(temporary_output / "manifest.json", sidecar_manifest)
        publication = validate_sidecar_publication(
            search_manifest,
            scripture_manifest,
            sidecar_manifest,
            temporary_output,
            args.min_global_coverage,
            args.min_book_coverage,
        )
        if args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        os.replace(temporary_output, args.output_dir)
        return publication
    except BaseException:
        shutil.rmtree(temporary_output, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.validate_only:
        publication = validate_sidecar_publication(
            read_json(args.search_manifest),
            read_json(args.scripture_manifest),
            read_json(args.output_dir / "manifest.json"),
            args.output_dir,
            args.min_global_coverage,
            args.min_book_coverage,
        )
        action = "validacao concluida"
    else:
        publication = build_sidecars(args)
        action = "pronto"
    print(
        f"[docids] {action}: {int(publication['pages_mapped'])}/"
        f"{int(publication['pages_total'])} associacoes ({publication['ratio'] * 100:.3f}%)."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        raise SystemExit(1) from error

#!/usr/bin/env python3
"""Consulta por cosseno o banco experimental de embeddings do OCR."""
from __future__ import annotations

import argparse
import heapq
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.playgrounds.embedding_playground import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    INSTRUCT,
    embed_queries,
)
from patristica_pipeline.ocr_xml_utils import parse_ocr_xml_page
from tools.generate_ocr_embeddings import (
    DEFAULT_DB,
    ParsedSource,
    SourceChoice,
    prepare_page,
    sha256_text,
)


QUERY_PROFILES = {
    "general": INSTRUCT,
    "argumentative": (
        "Retrieve passages relevant to the query by argumentative function and "
        "reasoning, not only by shared vocabulary. Prioritize theses, premises, "
        "objections, refutations, distinctions, inferences, consequences, and "
        "conclusions in patristic and theological texts. The query may be in "
        "Portuguese while the documents may be in Latin or Greek."
    ),
}


def hydrate_text(result: dict[str, object]) -> str:
    """Reconstrói o chunk do arquivo-fonte; o DB nao duplica texto limpo."""
    source_dir = result.pop("_source_dir")
    source_hash = str(result.pop("_source_hash"))
    config = result.pop("_chunk_config")
    repeated = set(result.pop("_repeated_headers"))
    text_hash = str(result.pop("_text_hash"))
    source_path = Path(str(source_dir)) / str(result["source_file"])
    if not source_path.is_file():
        return "[arquivo-fonte ausente]"
    raw = source_path.read_text(encoding="utf-8", errors="replace")
    if sha256_text(raw) != source_hash:
        return "[arquivo-fonte mudou; regenere o embedding]"
    page_num = int(result["page_num"])
    prepared = prepare_page(
        ParsedSource(
            choice=SourceChoice(page_num, source_path, (source_path,)),
            raw=raw,
            source_hash=sha256_text(raw),
            page=parse_ocr_xml_page(raw),
        ),
        repeated,
        min_page_chars=int(config["min_page_chars"]),
        max_chunk_chars=int(config["max_chunk_chars"]),
        overlap_chars=int(config["overlap_chars"]),
    )
    chunk_index = int(result["chunk_index"])
    if chunk_index >= len(prepared.chunks):
        return "[chunk nao pode ser reconstruido; regenere o embedding]"
    text = prepared.chunks[chunk_index]
    if sha256_text(text) != text_hash:
        return "[hash do chunk divergiu; regenere o embedding]"
    return text


def search(
    db_path: Path,
    query: str,
    *,
    model: str,
    ollama_url: str,
    volumes: Sequence[str],
    top_k: int,
    task_description: str = INSTRUCT,
    fetch_size: int = 512,
) -> list[dict[str, object]]:
    query_vectors = embed_queries(
        [query],
        model=model,
        ollama_url=ollama_url,
        task_description=task_description,
    )
    if len(query_vectors) != 1:
        raise RuntimeError(f"Esperado um embedding de consulta; recebidos {len(query_vectors)}")
    query_vector = np.asarray(query_vectors[0], dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0.0 or not np.isfinite(query_vector).all():
        raise RuntimeError("Embedding de consulta invalido")
    query_vector /= query_norm

    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    clauses = ["e.model=?"]
    params: list[object] = [model]
    if volumes:
        placeholders = ",".join("?" for _ in volumes)
        clauses.append(f"p.volume_id IN ({placeholders})")
        params.extend(volumes)
    cursor = con.execute(
        f"""
        SELECT e.embedding_dim,e.embedding,c.id AS chunk_id,c.chunk_index,c.text_hash,
               p.volume_id,p.page_num,p.source_file,p.source_hash,
               v.source_dir,v.repeated_headers_json,v.chunk_config_json
        FROM embeddings e
        JOIN chunks c ON c.id=e.chunk_id
        JOIN pages p ON p.id=c.page_id
        JOIN volumes v ON v.volume_id=p.volume_id
        WHERE {' AND '.join(clauses)}
        ORDER BY c.id
        """,
        params,
    )

    heap: list[tuple[float, int, dict[str, object]]] = []
    sequence = 0
    while True:
        rows = cursor.fetchmany(fetch_size)
        if not rows:
            break
        for row in rows:
            dim = int(row["embedding_dim"])
            if dim != len(query_vector):
                raise RuntimeError(
                    f"Dimensao incompativel no chunk {row['chunk_id']}: {dim} != {len(query_vector)}"
                )
            vector = np.frombuffer(row["embedding"], dtype=np.float32, count=dim)
            norm = float(np.linalg.norm(vector))
            if norm == 0.0:
                continue
            score = float(np.dot(query_vector, vector / norm))
            item: dict[str, object] = {
                "score": score,
                "volume_id": row["volume_id"],
                "page_num": int(row["page_num"]),
                "chunk_index": int(row["chunk_index"]),
                "source_file": row["source_file"],
                "_text_hash": row["text_hash"],
                "_source_hash": row["source_hash"],
                "_source_dir": row["source_dir"],
                "_repeated_headers": [
                    item["fingerprint"]
                    for item in json.loads(row["repeated_headers_json"])
                ],
                "_chunk_config": json.loads(row["chunk_config_json"]),
            }
            ranked = (score, sequence, item)
            sequence += 1
            if len(heap) < top_k:
                heapq.heappush(heap, ranked)
            elif score > heap[0][0]:
                heapq.heapreplace(heap, ranked)
    con.close()
    results = [item for _score, _seq, item in sorted(heap, reverse=True)]
    for result in results:
        result["text"] = hydrate_text(result)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--volumes", nargs="*", default=[])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--profile",
        choices=sorted(QUERY_PROFILES),
        default="general",
        help="Perfil do Instruct aplicado somente a consulta.",
    )
    parser.add_argument(
        "--instruction",
        help="Instruct livre; quando informado, substitui --profile.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--excerpt-chars", type=int, default=500)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.db.is_file():
        raise SystemExit(f"DB nao encontrado: {args.db}")
    if args.top_k <= 0:
        raise SystemExit("--top-k deve ser positivo")
    results = search(
        args.db,
        args.query,
        model=args.model,
        ollama_url=args.ollama_url,
        volumes=args.volumes,
        top_k=args.top_k,
        task_description=args.instruction or QUERY_PROFILES[args.profile],
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for rank, result in enumerate(results, start=1):
        text = " ".join(str(result["text"]).split())
        if len(text) > args.excerpt_chars:
            text = text[: args.excerpt_chars].rstrip() + "..."
        print(
            f"{rank:02d}. score={result['score']:.4f} "
            f"{result['volume_id']}:{result['page_num']} chunk={result['chunk_index']}"
        )
        print(f"    {text}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Gera artefatos do site (volumes.json, manifests, snapshots, dicionário de keywords)
a partir dos shards de enriquecimento em data/shards/enrichment/.

Uso:
    python tools/render_publication_from_shards.py \
        --index data/shards/enrichment/index.json \
        --out web/public \
        --db data/patristica_keywords.db \
        --keywords-json web/public/dict/keywords_lookup.json \
        --export-authors-json web/public/dict/authors.json \
        --page-block-size 100 \
        --raw-base-url "https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste"

O catálogo de keywords vem do SQLite (patristica_keywords.db) +
keywords_lookup.json gerado por export_keywords_dicts.py. Cada keyword bruta dos
shards e de `keyword_occurrence` preserva seu texto em `keyword_labels`; as labels
selecionadas no shard ficam primeiro e `keyword_display_count` limita o que o
viewer exibe. Quando houver um líder publicado, `keyword_ids` também guarda o ID
canônico do grupo para busca e filtros.

Cada bloco de páginas guarda `raw_base_url` uma única vez. Em cada página,
`raw.file` identifica o OCR original e `raw.url` fica reservado a overrides
excepcionais. O frontend resolve normalmente
`{raw_base_url}/{raw.file}`.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


@dataclass
class PageRecord:
    doc: str
    page: int
    file: str
    summary_page: str
    summary_global: str
    author: str
    work: str
    keywords: List[str]
    keyword_categories: Dict[str, List[str]]
    created_at: str
    search_text_pt: str = ""
    embedding_text: str = ""
    header_original: str = ""
    summary_generation: str = "legacy"
    page_kinds: List[str] = field(default_factory=list)
    segments: List[dict] = field(default_factory=list)
    translations: Dict[str, dict] = field(default_factory=dict)
    raw_url: str = ""


@dataclass(frozen=True)
class KeywordResolution:
    label: str
    canonical_id: Optional[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_DB = Path("data/patristica_keywords.db")
DEFAULT_KEYWORDS_JSON = Path("web/public/dict/keywords_lookup.json")


authors_global_lookup: set[str] = (
    set()
)  # Para coletar autores detectados e exportar authors.json


def load_shard(shard_path: Path) -> List[PageRecord]:
    recs = []
    with shard_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            kw_cats = r.get("categorias") or r.get("keyword_categories") or {}
            # Normaliza: só listas de strings
            kw_cats_norm: Dict[str, List[str]] = {}
            for cat, items in kw_cats.items():
                if not isinstance(items, list):
                    continue
                clean = [str(it).strip() for it in items if str(it).strip()]
                if clean:
                    kw_cats_norm[str(cat).strip()] = clean

            author_clean = r.get("author", "").split("(")[0].split(".")[0].strip()

            recs.append(
                PageRecord(
                    doc=r["doc"],
                    page=int(r["page"]),
                    file=r.get("file", ""),
                    summary_page=r.get("summary_page", ""),
                    summary_global=r.get("summary_global", ""),
                    author=author_clean,
                    work=r.get("work", ""),
                    keywords=r.get("keywords") or [],
                    keyword_categories=kw_cats_norm,
                    created_at=r.get("created_at", ""),
                    search_text_pt=r.get("search_text_pt", ""),
                    embedding_text=r.get("embedding_text", ""),
                    header_original=r.get("header_original", ""),
                    summary_generation=r.get("summary_generation", "legacy"),
                    page_kinds=r.get("page_kinds") or [],
                    segments=r.get("segments") or [],
                    translations=r.get("translations") or {},
                    raw_url=(
                        r.get("raw_url", "")
                        or (
                            (r.get("raw") or {}).get("url", "")
                            if isinstance(r.get("raw"), dict)
                            else ""
                        )
                    ),
                )
            )

            if len(author_clean) > 2:
                authors_global_lookup.add(author_clean[:100])  # Limita tamanho para evitar lixo
    return recs


def ensure_dirs(out: Path) -> None:
    for sub in [out, out / "meta", out / "snapshots", out / "dict"]:
        sub.mkdir(parents=True, exist_ok=True)


def build_keyword_lookup(
    db_path: Path, keywords_json_path: Path
) -> Dict[str, KeywordResolution]:
    """Preserva a label bruta e associa, quando existir, seu ID canônico."""

    def slugify(label: str) -> str:
        norm = (
            unicodedata.normalize("NFKD", label or "")
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        norm = norm.lower().strip()
        out = []
        for ch in norm:
            if ch.isalnum():
                out.append(ch)
            elif ch in [" ", "-", "_", "/", "."]:
                out.append("-")
        slug = "".join(out).strip("-")
        slug = "-".join([p for p in slug.split("-") if p])
        return slug or "kw"

    def canonical_slug_id(
        nome_canonico: str, label_norm: str, label: str, group_id: int
    ) -> str:
        for base in (nome_canonico, label_norm, label):
            s = slugify(base or "")
            if s and s != "kw":
                return f"k:{s}"
        return f"k:g{group_id}"

    # Carrega o conjunto de IDs válidos do lookup publicado para garantir
    # que só emitimos IDs que realmente existem no dicionário publicado.
    valid_ids: set[str] = set()
    canonical_by_group: Dict[int, str] = {}
    if keywords_json_path.exists():
        data = json.loads(keywords_json_path.read_text(encoding="utf-8"))
        for item in data.get("items") or []:
            if item.get("id"):
                valid_ids.add(item["id"])
            if item.get("group_id") is not None and not item.get("iscit", False):
                canonical_by_group[int(item["group_id"])] = item["id"]
    else:
        raise FileNotFoundError(
            f"keywords lookup não encontrado em {keywords_json_path}. "
            "Execute export_keywords_dicts.py primeiro."
        )

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")

    sql = """
        SELECT
            k.keyword_original,
            k.keyword_norm,
            k.hdbscan_group_id,
            k.is_scripture_citation,
            cc.nome_canonico,
            cc.nome_canonico_original
        FROM keywords k
        LEFT JOIN cluster_canonical_names cc ON cc.group_id = k.hdbscan_group_id
        WHERE k.keyword_original IS NOT NULL
    """

    lookup: Dict[str, KeywordResolution] = {}
    without_canonical = 0
    for row in con.execute(sql):
        original = row["keyword_original"]
        if not original:
            continue

        is_scripture = bool(row["is_scripture_citation"] == 1)

        if is_scripture:
            kid = f"k:{slugify(row['keyword_norm'] or original)}"
        elif row["hdbscan_group_id"] is None or row["hdbscan_group_id"] < 0:
            kid = None
        else:
            gid = int(row["hdbscan_group_id"])
            kid = canonical_by_group.get(
                gid,
                canonical_slug_id(
                    row["nome_canonico"] or "",
                    row["keyword_norm"] or "",
                    original,
                    gid,
                ),
            )

        if kid not in valid_ids:
            kid = None
            without_canonical += 1

        lookup[original] = KeywordResolution(
            label=original,
            canonical_id=kid,
        )

    con.close()

    if without_canonical:
        log(
            f"[INFO] {without_canonical} keywords-fonte não têm líder publicado; "
            "seus labels serão preservados."
        )

    return lookup


def load_keyword_occurrences(db_path: Path, volume_id: str) -> Dict[int, List[str]]:
    """Carrega o pacote fechado de labels por página sem reter o corpus inteiro."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")
    rows = con.execute(
        """
        SELECT o.pagina_num, k.keyword_original
        FROM keyword_occurrence o
        JOIN keywords k ON k.id = o.keyword_id
        WHERE o.documento = ?
          AND o.pagina_num IS NOT NULL
          AND k.keyword_original IS NOT NULL
        ORDER BY
          o.pagina_num,
          COALESCE(o.rank_in_page, o.rank_position, 2147483647),
          o.id
        """,
        (volume_id,),
    )
    by_page: Dict[int, List[str]] = {}
    for row in rows:
        label = str(row["keyword_original"] or "").strip()
        if label:
            by_page.setdefault(int(row["pagina_num"]), []).append(label)
    con.close()
    return by_page


# ---------------------------------------------------------------------------
# KNN sobre embeddings UMAP de página (cross-volume related pages)
# ---------------------------------------------------------------------------


class PageKNN:
    """Índice KNN sobre os embeddings UMAP reduzidos de resumo_pagina.

    Carrega a tabela `resumo_pagina_embedding_reduced` do SQLite de resumos
    (patristica_resumos.db) e monta um NearestNeighbors sklearn (euclidean).
    Uso: knn.query(doc, page, topk) → lista de {"doc", "page", "dist"}.

    Páginas do mesmo documento são excluídas dos resultados para garantir
    diversidade cross-volume nas sugestões do front-end.
    """

    def __init__(self, db_path: Path, topk_internal: int = 30, source: str = "legacy") -> None:
        """topk_internal: vizinhos buscados internamente antes de filtrar
        mesmo-doc; deve ser maior que o topk real para absorver exclusões."""
        from sklearn.neighbors import NearestNeighbors

        log(f"[KNN] Carregando embeddings UMAP de página de {db_path} ...")
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.execute("PRAGMA busy_timeout = 30000;")
        if source == "v2":
            latest = con.execute(
                "SELECT MAX(id) FROM resumo_cluster_runs WHERE kind='v2-page' AND status='completed'"
            ).fetchone()[0]
            if latest is None:
                raise RuntimeError("Nenhum resumo_cluster_run v2 concluído")
            rows = con.execute(
                """
                SELECT c.documento,c.pagina_num,c.reduced_dim,c.reduced_embedding
                  FROM resumo_generation_clusters c
                  JOIN resumo_generations g ON g.id=c.generation_id AND g.is_current=1
                 WHERE c.cluster_run_id=? ORDER BY c.rowid
                """,
                (latest,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT documento, pagina_num, n_components, embedding "
                "FROM resumo_pagina_embedding_reduced "
                "ORDER BY rowid"
            ).fetchall()
        con.close()

        if not rows:
            raise RuntimeError(
                "resumo_pagina_embedding_reduced está vazia ou não existe."
            )

        n_components = rows[0][2]
        self._rows_meta: List[Tuple[str, int]] = [(r[0], r[1]) for r in rows]
        self._pos: Dict[Tuple[str, int], int] = {
            k: i for i, k in enumerate(self._rows_meta)
        }
        X = np.frombuffer(b"".join(r[3] for r in rows), dtype=np.float32).reshape(
            len(rows), n_components
        )

        log(f"[KNN] {len(rows)} vetores ({n_components}d). Construindo índice ...")
        self._nn = NearestNeighbors(
            n_neighbors=min(topk_internal, len(rows)),
            metric="euclidean",
            algorithm="auto",
            n_jobs=-1,
        )
        self._nn.fit(X)
        self._X = X
        self._topk_internal = topk_internal
        log("[KNN] Índice pronto.")

    def query(
        self,
        doc: str,
        page: int,
        topk: int,
        cross_doc_only: bool = True,
        min_dist: float = 0.01,
    ) -> List[Dict]:
        """Retorna até `topk` vizinhos mais próximos para uma única página.

        Se cross_doc_only=True (padrão) exclui páginas do mesmo documento.
        min_dist filtra vizinhos com distância < threshold (padrão 0.01) —
        evita que páginas de conteúdo administrativo/folhas de rosto que
        colapsaram no mesmo ponto UMAP apareçam como "relacionadas".
        Resultado: [{"doc": str, "page": int, "dist": float}, ...]
        """
        result = self.query_batch(
            [(doc, page)], topk=topk, cross_doc_only=cross_doc_only, min_dist=min_dist
        )
        return result.get((doc, page), [])

    def query_batch(
        self,
        keys: List[Tuple[str, int]],
        topk: int,
        cross_doc_only: bool = True,
        min_dist: float = 0.01,
    ) -> Dict[Tuple[str, int], List[Dict]]:
        """Calcula vizinhos para um lote de páginas em uma única chamada KNN.

        Muito mais eficiente que chamar query() individualmente — executa um
        único kneighbors em batch (shape: [N_keys, topk_internal]).

        Retorna dict (doc, page) → [{"doc", "page", "dist"}, ...]
        As chaves sem embedding no índice são omitidas do resultado.
        """
        # Filtrar apenas chaves presentes no índice e registrar posições
        valid: List[Tuple[Tuple[str, int], int]] = []  # (key, qi)
        for key in keys:
            qi = self._pos.get(key)
            if qi is not None:
                valid.append((key, qi))
        if not valid:
            return {}

        query_idxs = [qi for _, qi in valid]
        X_query = self._X[query_idxs]  # shape (N, n_components)

        k = min(self._topk_internal + 1, len(self._rows_meta))
        all_dists, all_idxs = self._nn.kneighbors(X_query, n_neighbors=k)

        out: Dict[Tuple[str, int], List[Dict]] = {}
        for (key, qi), dists_row, idxs_row in zip(valid, all_dists, all_idxs):
            doc, _ = key
            results: List[Dict] = []
            for idx, dist in zip(idxs_row, dists_row):
                if idx == qi:
                    continue
                if dist < min_dist:
                    continue
                ndoc, npage = self._rows_meta[idx]
                if cross_doc_only and ndoc == doc:
                    continue
                results.append(
                    {"doc": ndoc, "page": npage, "dist": round(float(dist), 4)}
                )
                if len(results) >= topk:
                    break
            if results:
                out[key] = results
        return out


def page_blocks(
    recs: List[PageRecord],
    block_size: int,
    volume_id: str,
    keyword_ids: Dict[str, KeywordResolution | str],
    keyword_occurrences: Optional[Dict[int, List[str]]] = None,
    raw_base_url: str = "",
    knn: Optional["PageKNN"] = None,
    related_topk: int = 5,
    knn_min_dist: float = 0.01,
) -> Tuple[List[dict], List[dict]]:
    recs_sorted = sorted(recs, key=lambda r: r.page)
    blocks = []
    page_files = []

    # Pré-calcula KNN para TODAS as páginas do volume em uma única chamada batch,
    # eliminando o overhead de 288k chamadas individuais kneighbors.
    knn_results: Dict[Tuple[str, int], List[Dict]] = {}
    if knn is not None:
        all_keys = [(volume_id, r.page) for r in recs_sorted]
        knn_results = knn.query_batch(
            all_keys, topk=related_topk, cross_doc_only=True, min_dist=knn_min_dist
        )

    for idx, start in enumerate(range(0, len(recs_sorted), block_size), start=1):
        chunk = recs_sorted[start : start + block_size]
        file_name = f"meta/{volume_id}-pages-{idx:03d}.json.gz"
        block = {
            "schema_version": 2,
            "volume_id": volume_id,
            "block_type": "pages",
            "block_index": idx,
            "page_first": chunk[0].page,
            "page_last": chunk[-1].page,
            "dict_refs": {"keywords": "dict/keywords_manifest.json"},
            "pages": [],
        }
        base = raw_base_url.rstrip("/") if raw_base_url else ""
        if base:
            block["raw_base_url"] = f"{base}/{volume_id}/text"
        for r in chunk:
            selected_labels = list(
                dict.fromkeys(
                    label
                    for keyword in r.keywords
                    if (label := str(keyword or "").strip())
                )
            )
            keyword_labels = list(
                dict.fromkeys(
                    selected_labels
                    + list((keyword_occurrences or {}).get(r.page, []))
                )
            )
            resolved = [keyword_ids[k] for k in keyword_labels if k in keyword_ids]
            kws = list(
                dict.fromkeys(
                    canonical_id
                    for value in resolved
                    if (
                        canonical_id := (
                            value.canonical_id
                            if isinstance(value, KeywordResolution)
                            else value
                        )
                    )
                )
            )
            kw_cats_ids: Dict[str, List[str]] = {}
            for cat, items in r.keyword_categories.items():
                ids = []
                for keyword in items:
                    value = keyword_ids.get(keyword)
                    canonical_id = (
                        value.canonical_id
                        if isinstance(value, KeywordResolution)
                        else value
                    )
                    if canonical_id and canonical_id not in ids:
                        ids.append(canonical_id)
                if ids:
                    kw_cats_ids[cat] = ids

            page_entry: dict = {
                "page": r.page,
                "label": str(r.page),
                "summary_page": r.summary_page,
                "summary_global": r.summary_global,
                "author": r.author,
                "work": r.work,
                "created_at": r.created_at,
                "summary_generation": r.summary_generation,
                "keyword_ids": kws,
                "keyword_labels": keyword_labels,
                "keyword_display_count": len(selected_labels),
                "keyword_categories": kw_cats_ids,
                "snapshot_ids": [f"snap:{volume_id}:global"],
                # HDBSCAN group IDs ficam comentados: são dados de pipeline intermediário.
                # O front-end usa related_pages (KNN) em vez de lookups por cluster_id.
                # "resumo_pagina_hdbscan_group_id": r.resumo_pagina_hdbscan_group_id,
                # "resumo_global_hdbscan_group_id": r.resumo_global_hdbscan_group_id,
            }
            if r.search_text_pt:
                page_entry["search_text_pt"] = r.search_text_pt
            if r.embedding_text:
                page_entry["embedding_text"] = r.embedding_text
            if r.header_original:
                page_entry["header_original"] = r.header_original
            if r.page_kinds:
                page_entry["page_kinds"] = r.page_kinds
            if r.segments:
                page_entry["segments"] = r.segments
            if r.translations:
                page_entry["translations"] = r.translations

            # Páginas relacionadas: resultado pré-calculado no batch acima
            related = knn_results.get((volume_id, r.page))
            if related:
                page_entry["related_pages"] = related

            # O caminho comum guarda somente o arquivo. URL completa é override
            # explícito para páginas que não seguem o template do bloco.
            if r.file or r.raw_url:
                raw: Dict[str, str] = {}
                if r.file:
                    raw["file"] = r.file
                if r.raw_url:
                    raw["url"] = r.raw_url
                page_entry["raw"] = raw

            block["pages"].append(page_entry)
        blocks.append(block)
        page_files.append(
            {
                "index": idx,
                "file": file_name,
                "page_first": chunk[0].page,
                "page_last": chunk[-1].page,
                "count": len(chunk),
            }
        )
    return blocks, page_files


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Grava JSON compacto para economizar espaço em disco/banda
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
        return
    path.write_bytes(payload)


def dump_authors_lookup(path: Path) -> None:
    authors = sorted(authors_global_lookup)
    write_json(path, authors)


def main():
    ap = argparse.ArgumentParser(
        description="Renderiza artefatos do site a partir dos shards de enriquecimento."
    )
    ap.add_argument(
        "--index", type=Path, default=Path("data/shards/enrichment/index.json")
    )
    ap.add_argument("--out", type=Path, default=Path("web/public"))
    ap.add_argument(
        "--volumes",
        type=str,
        default="",
        help="Lista CSV opcional de volumes; vazia processa todos.",
    )
    ap.add_argument(
        "--keyword-scope",
        choices=["closed", "selected"],
        default="closed",
        help=(
            "closed incorpora keyword_occurrence completo; selected preserva "
            "somente as labels escolhidas nos shards."
        ),
    )
    ap.add_argument("--page-block-size", type=int, default=100)
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite patristica_keywords.db (para resolver keywords → IDs canônicos)",
    )
    ap.add_argument(
        "--keywords-json",
        type=Path,
        default=DEFAULT_KEYWORDS_JSON,
        help="keywords.json gerado por export_keywords_dicts.py",
    )
    ap.add_argument(
        "--resumos-db",
        type=Path,
        default=None,
        help=(
            "SQLite patristica_resumos.db com tabela resumo_pagina_embedding_reduced. "
            "Se fornecido, emite related_pages em cada página via KNN (cross-volume). "
            "Omita para desativar o cálculo de páginas relacionadas."
        ),
    )
    ap.add_argument(
        "--related-topk",
        type=int,
        default=5,
        help="Número de páginas relacionadas a emitir por página (padrão: 5).",
    )
    ap.add_argument(
        "--related-source",
        choices=["legacy", "v2"],
        default="legacy",
        help=(
            "Espaço reduzido usado no KNN. O default legacy mantém as relações "
            "atuais durante a migração; use v2 após gerar embeddings e clustering v2."
        ),
    )
    ap.add_argument(
        "--related-knn-pool",
        type=int,
        default=40,
        help=(
            "Vizinhos buscados internamente antes de filtrar mesmo-doc "
            "(deve ser > related-topk; padrão: 40)."
        ),
    )
    ap.add_argument(
        "--related-min-dist",
        type=float,
        default=0.01,
        help=(
            "Distância mínima euclidiana (espaço UMAP) para aceitar um vizinho. "
            "Filtra páginas administrativas/folhas-de-rosto que colapsam em dist≈0. "
            "Padrão: 0.01."
        ),
    )
    ap.add_argument(
        "--raw-base-url",
        type=str,
        default="https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste",
        help=(
            "URL base para os arquivos de texto OCR originais no GitHub raw content. "
            "Formato esperado: https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/teste  "
            "Cada bloco salvará {raw_base_url}/{DOC}/text uma vez e cada página "
            "manterá somente raw.file. Passe string vazia para omitir a base."
        ),
    )
    ap.add_argument(
        "--export-authors-json",
        type=Path,
        default=Path("web/public/dict/authors.json"),
        help="Caminho para o arquivo JSON de saída com o dicionário de autores.",
    )

    args = ap.parse_args()

    ensure_dirs(args.out)
    idx = load_index(args.index)
    requested_volumes = {
        value.strip() for value in args.volumes.split(",") if value.strip()
    }
    selected_volumes = [
        volume
        for volume in idx.get("volumes", [])
        if not requested_volumes or volume.get("id") in requested_volumes
    ]
    if requested_volumes:
        found = {volume.get("id") for volume in selected_volumes}
        missing = sorted(requested_volumes - found)
        if missing:
            ap.error(f"volumes ausentes do índice: {', '.join(missing)}")

    # KNN para páginas relacionadas (opcional)
    knn: Optional[PageKNN] = None
    if args.resumos_db is not None:
        try:
            knn = PageKNN(
                args.resumos_db,
                topk_internal=args.related_knn_pool,
                source=args.related_source,
            )
        except Exception as e:
            raise RuntimeError(
                f"KNN solicitado por --resumos-db, mas não pôde ser carregado de "
                f"{args.resumos_db}: {e}"
            ) from e

    # Carregar todos os records por volume
    volumes_pages: Dict[str, List[PageRecord]] = {}
    base_dir = args.index.parent
    shard_total = sum(len(vol.get("shards", [])) for vol in selected_volumes)
    shard_done = 0
    for vol in selected_volumes:
        vid = vol["id"]
        recs: List[PageRecord] = []
        for shard in vol.get("shards", []):
            shard_path = base_dir / shard["file"]
            shard_done += 1
            pct = (shard_done / max(1, shard_total)) * 100
            log(f"[{shard_done}/{shard_total} | {pct:5.1f}%] Lendo shard {shard_path}")
            recs.extend(load_shard(shard_path))
        volumes_pages[vid] = recs
        log(f"Volume {vid}: {len(recs)} páginas carregadas.")

    # Lookup keyword_original → canonical_id via SQLite + keywords.json
    log(f"Construindo lookup de keywords via {args.db} + {args.keywords_json} ...")
    kw_map = build_keyword_lookup(args.db, args.keywords_json)
    log(f"Lookup pronto: {len(kw_map)} entradas.")

    dump_authors_lookup(args.export_authors_json)

    volumes_out = []
    for vol in selected_volumes:
        vid = vol["id"]
        recs = volumes_pages.get(vid, [])
        if not recs:
            continue
        pages_sorted = sorted([r.page for r in recs])
        page_first, page_last = pages_sorted[0], pages_sorted[-1]
        # snapshot global
        summary_global = next((r.summary_global for r in recs if r.summary_global), "")
        snapshot_path = args.out / "snapshots" / f"{vid}.json.gz"
        snapshot_obj = {
            "schema_version": 1,
            "volume_id": vid,
            "block_type": "snapshots",
            "snapshots": [
                {
                    "id": f"snap:{vid}:global",
                    "kind": "volume_summary",
                    "label": "Resumo global",
                    "summary": summary_global or "",
                    "keyword_ids": [],
                }
            ],
        }
        write_json(snapshot_path, snapshot_obj)

        # blocos de páginas
        keyword_occurrences = (
            load_keyword_occurrences(args.db, vid)
            if args.keyword_scope == "closed"
            else {}
        )
        blocks, page_files = page_blocks(
            recs,
            args.page_block_size,
            vid,
            kw_map,
            keyword_occurrences,
            args.raw_base_url,
            knn=knn,
            related_topk=args.related_topk,
            knn_min_dist=args.related_min_dist,
        )
        for b in blocks:
            out_path = args.out / "meta" / f"{vid}-pages-{b['block_index']:03d}.json.gz"
            write_json(out_path, b)

        # manifesto do volume
        meta_obj = {
            "schema_version": 1,
            "id": vid,
            "collection_id": vid[:2],
            "page_first": page_first,
            "page_last": page_last,
            "page_count": len(recs),
            "page_block_size": args.page_block_size,
            "page_blocks": page_files,
            "snapshot_blocks": [
                {"index": 1, "file": f"snapshots/{vid}.json.gz", "snapshot_count": 1}
            ],
            "stats": {
                "pages_with_summary": len(recs),
                "has_keywords": any(r.keywords for r in recs),
            },
        }
        write_json(args.out / "meta" / f"{vid}.json.gz", meta_obj)

        # volumes.json entry
        volumes_out.append(
            {
                "id": vid,
                "collection_id": vid[:2],
                "page_first": page_first,
                "page_last": page_last,
                "page_count": len(recs),
                "meta_url": f"meta/{vid}.json.gz",
                "search_bundle": "indexador/search",
                "viewer_url_template": f"/pdfocr/viewer?doc={vid}&page={{page}}",
            }
        )
        log(f"Volume {vid}: snapshots/meta/page-blocks escritos.")

    volumes_json = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volumes": volumes_out,
    }
    write_json(args.out / "volumes.json", volumes_json)
    log(f"[OK] Renderização concluída. Volumes: {len(volumes_out)}")


if __name__ == "__main__":
    main()

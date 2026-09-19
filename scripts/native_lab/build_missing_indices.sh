#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
indexador_binary=${INDEXADOR_BINARY:-"$repo_root/.work/PatrologiaIndexer/build-codex-clang19/indexador"}
force_structural=0
force_search=0

for option in "$@"; do
  case "$option" in
    --force-structural) force_structural=1 ;;
    --force-search) force_search=1 ;;
    --force-all) force_structural=1; force_search=1 ;;
    *) echo "Opção desconhecida: $option" >&2; exit 2 ;;
  esac
done

cd "$repo_root"

if [ ! -x "$indexador_binary" ]; then
  echo "Indexador não encontrado ou não executável: $indexador_binary" >&2
  exit 1
fi

if [ "$force_structural" -eq 1 ] || [ ! -s web/public/indexador/indices/manifest.json ]; then
  node tools/build_indices_indexador_from_json.mjs \
    --source-manifest web/public/indices/manifest.json \
    --out web/public/indexador/indices \
    --binary "$indexador_binary" \
    --base /BibliothecaPatristica \
    --documents-per-shard 500 \
    --smoke-query 'clemens::volume=PG001' \
    --smoke-query 'clementis::volume=PG001' \
    --smoke-query 'corinthios::volume=PG001' \
    --smoke-query 'Primeira Epístola aos Coríntios::volume=PG001' \
    --smoke-query 'First Epistle to the Corinthians::volume=PG001' \
    --smoke-query 'Prima Lettera ai Corinzi::volume=PG001' \
    --smoke-query 'Première épître aux Corinthiens::volume=PG001' \
    --all
else
  echo '[SKIP] Índice estrutural já possui manifest.'
fi

if [ "$force_search" -eq 1 ] || [ ! -s web/public/indexador/search/manifest.json ]; then
  node tools/build_search_indexador_from_shards.mjs \
    --public web/public \
    --source-manifest web/public/volumes.json \
    --out web/public/indexador/search \
    --binary "$indexador_binary" \
    --base /BibliothecaPatristica \
    --layout unified \
    --documents-per-shard 500 \
    --all
else
  echo '[SKIP] Índice de busca já possui manifest.'
fi

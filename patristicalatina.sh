#!/usr/bin/env bash

# patristicalatina.sh (versão com GNU parallel)
# Executa `main2.py` em paralelo sobre os PDFs PL*.pdf e gera logs por volume em ./teste/

set -u

# Configuráveis via env: JOBS, PROCS, OMP_THREADS, LLM_MODEL
JOBS=${JOBS:-11}
PROCS=${PROCS:-1}
OMP_THREADS=${OMP_THREADS:-2}
LLM_MODEL=${LLM_MODEL:-qwen3.5:397b-cloud}
ALGORITHM=${ALGORITHM:-ollama}

# Diretório de saída de logs
LOG_DIR=${LOG_DIR:-teste}
mkdir -p "${LOG_DIR}"

# Coleta arquivos (tratando ausência de matches)
shopt -s nullglob
VOLUMES=(/homessddata/patristica/PL*.pdf)
shopt -u nullglob

if [ ${#VOLUMES[@]} -eq 0 ]; then
  echo "Nenhum arquivo PL*.pdf encontrado em /homessddata/patristica"
  exit 0
fi

echo "📦  Usando GNU parallel (jobs=${JOBS}) — processando ${#VOLUMES[@]} volumes"

# Exportar variáveis que queremos que apareçam expandidas na linha de comando
export PROCS OMP_THREADS LLM_MODEL LOG_DIR

printf '%s
' "${VOLUMES[@]}" | \
parallel --bar --jobs "${JOBS}" --halt soon,fail=10% \
  "python './main2.py' \
      --algorithm '${ALGORITHM}' \
      --llm-model '${LLM_MODEL}' \
      --procs '${PROCS}' \
      --omp-threads '${OMP_THREADS}' \
      --verify-fix \
      --out '${LOG_DIR}/' \
      --lang 'migne' '{}' \
      > '${LOG_DIR}/{/}.log' 2>&1"

echo "" 

# lat+grc

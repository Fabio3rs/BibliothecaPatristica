#!/usr/bin/env bash
# --------------------------------------------------------------------------
# keywords_parallel.sh – Lança keywords_serial.py em paralelo para extracao
# de dados de modo similar ao resumo_parallel.sh.
#
# Cada volume roda como um processo independente com limite controlado.
#
# Uso:
#   ./keywords_parallel.sh                       # defaults: gpt-5-mini, high, 40 jobs, source tudo
#   ./keywords_parallel.sh --jobs 20             # limitar paralelismo
#   ./keywords_parallel.sh --source resumo_pagina # altera nivel de extração
#   ./keywords_parallel.sh --provider ollama --model qwen3:30b --jobs 4
#   ./keywords_parallel.sh --pattern "PL*"       # só PL (baseado no nome do doc no BD e nao dir)
#   ./keywords_parallel.sh --dry-run             # mostra comandos sem executar
# --------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYWORDS_SCRIPT="${SCRIPT_DIR}/keywords_serial.py"

# ── Defaults ──────────────────────────────────────────────────────────────
PROVIDER="openai"
MODEL="gpt-5-mini"
REASONING="low"
SOURCE="tudo"
JOBS=40
PATTERN="%"
TIMEOUT=300
RETRIES=3
NUM_CTX=16384
DB="${SCRIPT_DIR}/data/patristica_resumos.db"
DRY_RUN=0
LOG_DIR="${SCRIPT_DIR}/logs/keywords_parallel"
WRITE_FLAG="--write"

# ── Parse args ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider)       PROVIDER="$2";  shift 2 ;;
        --model)          MODEL="$2";     shift 2 ;;
        --reasoning-effort) REASONING="$2"; shift 2 ;;
        --source)         SOURCE="$2";    shift 2 ;;
        --jobs|-j)        JOBS="$2";      shift 2 ;;
        --pattern)        PATTERN="$2";   shift 2 ;;
        --timeout)        TIMEOUT="$2";   shift 2 ;;
        --retries)        RETRIES="$2";   shift 2 ;;
        --num-ctx)        NUM_CTX="$2";   shift 2 ;;
        --db)             DB="$2";        shift 2 ;;
        --dry-run)        DRY_RUN=1; WRITE_FLAG=""; shift ;;
        --help|-h)
            head -18 "$0" | tail -14
            exit 0 ;;
        *) echo "Argumento desconhecido: $1"; exit 1 ;;
    esac
done

# ── Coleta volumes do DB (pois o script processa do banco) ───────────
echo "Buscando documentos no banco de dados com padrão '$PATTERN'..."
mapfile -t VOLUMES < <(sqlite3 "$DB" "SELECT DISTINCT documento FROM resumos WHERE documento LIKE '$PATTERN' ORDER BY documento;")

if [[ ${#VOLUMES[@]} -eq 0 ]]; then
    echo "❌  Nenhum volume encontrado no banco de dados com padrão: $PATTERN"
    exit 1
fi

TOTAL=${#VOLUMES[@]}
echo "═══════════════════════════════════════════════════════════════"
echo "  Keywords Paralelo – Patrística"
echo "═══════════════════════════════════════════════════════════════"
echo "  Provider:          $PROVIDER"
echo "  Modelo:            $MODEL"
echo "  Reasoning effort:  $REASONING"
echo "  Source Extracao:   $SOURCE"
echo "  Num ctx (Ollama):  $NUM_CTX"
echo "  Volumes (DB):      $TOTAL"
echo "  Jobs paralelos:    $JOBS"
echo "  DB:                $DB"
echo "  Timeout:           ${TIMEOUT}s"
echo "  Retries:           $RETRIES"
echo "  Writes:            $(if [[ -n "$WRITE_FLAG" ]]; then echo "Sim"; else echo "Nao (Dry Run)"; fi)"
echo "  Logs:              $LOG_DIR/"
echo "═══════════════════════════════════════════════════════════════"

# ── Cria dir de logs ──────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Monta comando base ───────────────────────────────────────────────────
build_cmd() {
    local vol="$1"
    local logfile="${LOG_DIR}/${vol}.log"

    echo "python '${KEYWORDS_SCRIPT}' \
        --provider '${PROVIDER}' \
        --model '${MODEL}' \
        --reasoning-effort '${REASONING}' \
        --num-ctx '${NUM_CTX}' \
        --source '${SOURCE}' \
        --timeout '${TIMEOUT}' \
        --retries '${RETRIES}' \
        --resumos-db '${DB}' \
        --doc '${vol}' \
        --skip-done \
        ${WRITE_FLAG} \
        > '${logfile}' 2>&1"
}

# ── Dry run ───────────────────────────────────────────────────────────────
if [[ $DRY_RUN -eq 1 && -z "$WRITE_FLAG" ]]; then
    echo ""
    echo "🔍  DRY RUN MODO BASH – Comandos que seriam executados:"
    echo "    (Nota: Rodando todos eles sem --write exibirá saídas do LLM no raw)"
    echo ""
    for vol in "${VOLUMES[@]:0:5}"; do
        build_cmd "$vol"
    done
    echo "... e mais $((TOTAL - 5)) comandos"
    echo ""
    read -p "Deseja testar a execucao real para esses primeiros 5? (s/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Ss]$ ]]; then
        TOTAL=5
        VOLUMES=("${VOLUMES[@]:0:5}")
    else
        exit 0
    fi
fi

# ── Execução paralela ────────────────────────────────────────────────────
STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
echo ""
echo "🚀  Iniciando $TOTAL volumes com até $JOBS em paralelo..."
echo "    Início: $STARTED_AT"
echo "    Logs individuais em: $LOG_DIR/<volume>.log"
echo ""

run_with_bash_jobs() {
    local running=0
    local finished=0
    local failed=0
    local pids=()
    local vol_names=()

    for vol in "${VOLUMES[@]}"; do
        local logfile="${LOG_DIR}/${vol}.log"

        python "${KEYWORDS_SCRIPT}" \
            --provider "${PROVIDER}" \
            --model "${MODEL}" \
            --reasoning-effort "${REASONING}" \
            --num-ctx "${NUM_CTX}" \
            --source "${SOURCE}" \
            --timeout "${TIMEOUT}" \
            --retries "${RETRIES}" \
            --resumos-db "${DB}" \
            --doc "${vol}" \
            --skip-done \
            ${WRITE_FLAG} \
            > "${logfile}" 2>&1 &

        pids+=($!)
        vol_names+=("$vol")
        running=$((running + 1))

        # Limita paralelismo
        while [[ $running -ge $JOBS ]]; do
            # Espera qualquer processo filho terminar
            if wait -n 2>/dev/null; then
                : # sucesso
            else
                failed=$((failed + 1))
            fi
            running=$((running - 1))
            finished=$((finished + 1))
            printf "  ⏳ Progresso: %d/%d concluídos (%d erros)\r" "$finished" "$TOTAL" "$failed"
        done
    done

    # Espera todos os restantes
    for pid in "${pids[@]}"; do
        if wait "$pid" 2>/dev/null; then
            : # sucesso
        else
            failed=$((failed + 1))
        fi
    done
    finished=$TOTAL
    echo ""
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  ✅ Concluído: $((TOTAL - failed))/$TOTAL com sucesso"
    if [[ $failed -gt 0 ]]; then
        echo "  ⚠️  $failed volumes com erro – verifique os logs em $LOG_DIR/"
    fi
    echo "  Início:  $STARTED_AT"
    echo "  Fim:     $(date '+%Y-%m-%d %H:%M:%S')"
    echo "═══════════════════════════════════════════════════════════════"
}

# Escolhe método: GNU parallel se disponível, senão bash nativo
if command -v parallel &>/dev/null; then
    echo "📦  Usando GNU parallel"
    printf '%s\n' "${VOLUMES[@]}" | \
        parallel --bar --jobs "$JOBS" --halt soon,fail=10% \
        "python '${KEYWORDS_SCRIPT}' \
            --provider '${PROVIDER}' \
            --model '${MODEL}' \
            --reasoning-effort '${REASONING}' \
            --num-ctx '${NUM_CTX}' \
            --source '${SOURCE}' \
            --timeout '${TIMEOUT}' \
            --retries '${RETRIES}' \
            --resumos-db '${DB}' \
            --doc '{}' \
            --skip-done \
            ${WRITE_FLAG} \
            > '${LOG_DIR}/{}.log' 2>&1"

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  ✅ Concluído"
    echo "  Início:  $STARTED_AT"
    echo "  Fim:     $(date '+%Y-%m-%d %H:%M:%S')"
    echo "═══════════════════════════════════════════════════════════════"
else
    echo "📦  GNU parallel não encontrado – usando bash job control"
    run_with_bash_jobs
fi

# ── Resumo de erros nos logs ──────────────────────────────────────────────
echo ""
ERRORS=$(grep -rl "Erro\|ERROR\|Traceback\|esgotou tentativas" "$LOG_DIR"/*.log 2>/dev/null | wc -l || echo 0)
if [[ "$ERRORS" -gt 0 ]]; then
    echo "⚠️  $ERRORS logs contêm erros. Verifique com:"
    echo "    grep -l 'ERROR\|Traceback' $LOG_DIR/*.log"
fi

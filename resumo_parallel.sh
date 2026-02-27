#!/usr/bin/env bash
# --------------------------------------------------------------------------
# resumo_parallel.sh – Lança resumo_serial.py em paralelo para todos os
# volumes encontrados em teste/P*/
#
# Usa GNU parallel (se disponível) ou xargs -P como fallback.
# Cada volume roda como um processo independente; o SQLite (WAL + busy_timeout)
# suporta bem escritas concorrentes de documentos diferentes.
#
# Uso:
#   ./resumo_parallel.sh                       # defaults: gpt-5-mini, high, 40 jobs
#   ./resumo_parallel.sh --jobs 20             # limitar paralelismo
#   ./resumo_parallel.sh --model gpt-5-mini --reasoning-effort medium
#   ./resumo_parallel.sh --provider ollama --model qwen3:30b --jobs 4
#   ./resumo_parallel.sh --pattern "teste/PL*" # só PL
#   ./resumo_parallel.sh --dry-run             # mostra comandos sem executar
# --------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESUMO_SCRIPT="${SCRIPT_DIR}/resumo_serial.py"

# ── Defaults ──────────────────────────────────────────────────────────────
PROVIDER="openai"
MODEL="gpt-5-mini"
REASONING="high"
JOBS=40
PATTERN="teste/P*"
TIMEOUT=300
RETRIES=3
NUM_CTX=16384
DB="${SCRIPT_DIR}/data/patristica_resumos.db"
DRY_RUN=0
LOG_DIR="${SCRIPT_DIR}/logs/resumo_parallel"

# ── Parse args ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider)       PROVIDER="$2";  shift 2 ;;
        --model)          MODEL="$2";     shift 2 ;;
        --reasoning-effort) REASONING="$2"; shift 2 ;;
        --jobs|-j)        JOBS="$2";      shift 2 ;;
        --pattern)        PATTERN="$2";   shift 2 ;;
        --timeout)        TIMEOUT="$2";   shift 2 ;;
        --retries)        RETRIES="$2";   shift 2 ;;
        --num-ctx)        NUM_CTX="$2";   shift 2 ;;
        --db)             DB="$2";        shift 2 ;;
        --dry-run)        DRY_RUN=1;      shift   ;;
        --help|-h)
            head -18 "$0" | tail -14
            exit 0 ;;
        *) echo "Argumento desconhecido: $1"; exit 1 ;;
    esac
done

# ── Coleta volumes ────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
mapfile -t VOLUMES < <(find $PATTERN -maxdepth 0 -type d 2>/dev/null | sort)

if [[ ${#VOLUMES[@]} -eq 0 ]]; then
    echo "❌  Nenhum volume encontrado com padrão: $PATTERN"
    exit 1
fi

TOTAL=${#VOLUMES[@]}
echo "═══════════════════════════════════════════════════════════════"
echo "  Resumo Paralelo – Patrística"
echo "═══════════════════════════════════════════════════════════════"
echo "  Provider:          $PROVIDER"
echo "  Modelo:            $MODEL"
echo "  Reasoning effort:  $REASONING"
echo "  Num ctx (Ollama):  $NUM_CTX"
echo "  Volumes:           $TOTAL"
echo "  Jobs paralelos:    $JOBS"
echo "  DB:                $DB"
echo "  Timeout:           ${TIMEOUT}s"
echo "  Retries:           $RETRIES"
echo "  Logs:              $LOG_DIR/"
echo "═══════════════════════════════════════════════════════════════"

# ── Cria dir de logs ──────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Monta comando base ───────────────────────────────────────────────────
build_cmd() {
    local vol="$1"
    local volname
    volname="$(basename "$vol")"
    local logfile="${LOG_DIR}/${volname}.log"

    echo "python '${RESUMO_SCRIPT}' \
        --provider '${PROVIDER}' \
        --model '${MODEL}' \
        --reasoning-effort '${REASONING}' \
        --num-ctx '${NUM_CTX}' \
        --timeout '${TIMEOUT}' \
        --retries '${RETRIES}' \
        --db '${DB}' \
        --volume-dir '${vol}' \
        > '${logfile}' 2>&1"
}

# ── Dry run ───────────────────────────────────────────────────────────────
if [[ $DRY_RUN -eq 1 ]]; then
    echo ""
    echo "🔍  DRY RUN – Comandos que seriam executados:"
    echo ""
    for vol in "${VOLUMES[@]}"; do
        build_cmd "$vol"
    done
    echo ""
    echo "(total: $TOTAL comandos, $JOBS em paralelo)"
    exit 0
fi

# ── Execução paralela ────────────────────────────────────────────────────
STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
echo ""
echo "🚀  Iniciando $TOTAL volumes com até $JOBS em paralelo..."
echo "    Início: $STARTED_AT"
echo "    Logs individuais em: $LOG_DIR/<volume>.log"
echo ""

# Gera lista de comandos e despacha
run_parallel() {
    local cmd_file
    cmd_file=$(mktemp /tmp/resumo_cmds.XXXXXX)

    for vol in "${VOLUMES[@]}"; do
        build_cmd "$vol" >> "$cmd_file"
    done

    if command -v parallel &>/dev/null; then
        # GNU parallel – melhor controle de jobs, progress bar
        echo "📦  Usando GNU parallel"
        parallel --bar --jobs "$JOBS" --halt soon,fail=10% < "$cmd_file"
    else
        # Fallback: xargs -P
        echo "📦  Usando xargs -P (instale GNU parallel para progress bar)"
        cat "$cmd_file" | xargs -I CMD -P "$JOBS" bash -c 'CMD'
        # Fallback alternativo direto com bash
        local running=0
        local finished=0
        local pids=()

        while IFS= read -r cmd; do
            bash -c "$cmd" &
            pids+=($!)
            running=$((running + 1))

            # Limita paralelismo
            if [[ $running -ge $JOBS ]]; then
                # Espera qualquer um terminar
                wait -n 2>/dev/null || true
                running=$((running - 1))
                finished=$((finished + 1))
                echo -ne "  ⏳ Progresso: $finished/$TOTAL concluídos\r"
            fi
        done < "$cmd_file"

        # Espera restantes
        for pid in "${pids[@]}"; do
            wait "$pid" 2>/dev/null || true
        done
    fi

    rm -f "$cmd_file"
}

# Fallback mais simples e confiável sem GNU parallel
run_with_bash_jobs() {
    local running=0
    local finished=0
    local failed=0
    local pids=()
    local vol_names=()

    for vol in "${VOLUMES[@]}"; do
        local volname
        volname="$(basename "$vol")"
        local logfile="${LOG_DIR}/${volname}.log"

        python "${RESUMO_SCRIPT}" \
            --provider "${PROVIDER}" \
            --model "${MODEL}" \
            --reasoning-effort "${REASONING}" \
            --num-ctx "${NUM_CTX}" \
            --timeout "${TIMEOUT}" \
            --retries "${RETRIES}" \
            --db "${DB}" \
            --volume-dir "${vol}" \
            > "${logfile}" 2>&1 &

        pids+=($!)
        vol_names+=("$volname")
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
        "python '${RESUMO_SCRIPT}' \
            --provider '${PROVIDER}' \
            --model '${MODEL}' \
            --reasoning-effort '${REASONING}' \
            --num-ctx '${NUM_CTX}' \
            --timeout '${TIMEOUT}' \
            --retries '${RETRIES}' \
            --db '${DB}' \
            --volume-dir '{}' \
            > '${LOG_DIR}/{/}.log' 2>&1"

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

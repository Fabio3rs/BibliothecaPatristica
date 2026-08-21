#!/usr/bin/env bash
# --------------------------------------------------------------------------
# resumo_parallel.sh – Lança resumo_serial.py em paralelo por volume.
#
# Uso:
#   ./resumo_parallel.sh                              # defaults: v2, gemma4:cloud, 10 jobs
#   ./resumo_parallel.sh --jobs 20                    # limitar paralelismo
#   ./resumo_parallel.sh --pipeline legacy --provider openai --model gpt-5-mini
#   ./resumo_parallel.sh --pattern "teste/PL*"        # processar somente PL
#   ./resumo_parallel.sh --facsimile-mode always      # imagem em todas as páginas
#   ./resumo_parallel.sh --fail-fast                   # parar de agendar após a primeira falha
#   ./resumo_parallel.sh --dry-run                    # somente mostrar comandos
# --------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESUMO_SCRIPT="${SCRIPT_DIR}/resumo_serial.py"

PIPELINE="v2"
PROVIDER="ollama"
MODEL="gemma4:cloud"
REASONING="high"
JOBS=10
PATTERN="teste/P*"
TIMEOUT=300
RETRIES=3
NUM_CTX=""
OLLAMA_URL="http://localhost:11434"
OPENAI_URL="https://api.openai.com/v1"
API_KEY_ENV="OPENAI_API_KEY"
DB="${SCRIPT_DIR}/data/patristica_resumos.db"
INDICES_DB="${SCRIPT_DIR}/data/patristic_indices.db"
DRY_RUN=0
LOG_DIR="${RESUMO_PARALLEL_LOG_DIR:-${SCRIPT_DIR}/logs/resumo_parallel}"
FACSIMILE_MODE="auto"
FACSIMILE_THRESHOLD=2
LOOKAHEAD_PAGES=3
PROMOTE=0
FORCE_REPLACE_FROM=""
FORCE_REPLACE_THROUGH="next-work"
FILL_GAPS=0
FILL_GAPS_OVERLAP=10
PAGE=""
VERBOSE=0
FAIL_FAST=0
FORCE_REPLACE_THROUGH_SET=0

usage() {
    sed -n '5,12{s/^# \{0,1\}//;p;}' "$0"
}

require_value() {
    local option="$1"
    local value="${2-}"
    if [[ -z "$value" || "$value" == --* ]]; then
        echo "Erro: ${option} requer um valor." >&2
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pipeline)          require_value "$1" "${2-}"; PIPELINE="$2"; shift 2 ;;
        --provider)          require_value "$1" "${2-}"; PROVIDER="$2"; shift 2 ;;
        --model)             require_value "$1" "${2-}"; MODEL="$2"; shift 2 ;;
        --reasoning-effort)  require_value "$1" "${2-}"; REASONING="$2"; shift 2 ;;
        --jobs|-j)           require_value "$1" "${2-}"; JOBS="$2"; shift 2 ;;
        --pattern)           require_value "$1" "${2-}"; PATTERN="$2"; shift 2 ;;
        --timeout)           require_value "$1" "${2-}"; TIMEOUT="$2"; shift 2 ;;
        --retries)           require_value "$1" "${2-}"; RETRIES="$2"; shift 2 ;;
        --num-ctx)           require_value "$1" "${2-}"; NUM_CTX="$2"; shift 2 ;;
        --ollama-url)        require_value "$1" "${2-}"; OLLAMA_URL="$2"; shift 2 ;;
        --openai-url)        require_value "$1" "${2-}"; OPENAI_URL="$2"; shift 2 ;;
        --api-key-env)       require_value "$1" "${2-}"; API_KEY_ENV="$2"; shift 2 ;;
        --db)                require_value "$1" "${2-}"; DB="$2"; shift 2 ;;
        --indices-db)        require_value "$1" "${2-}"; INDICES_DB="$2"; shift 2 ;;
        --facsimile-mode)    require_value "$1" "${2-}"; FACSIMILE_MODE="$2"; shift 2 ;;
        --facsimile-threshold) require_value "$1" "${2-}"; FACSIMILE_THRESHOLD="$2"; shift 2 ;;
        --lookahead-pages)   require_value "$1" "${2-}"; LOOKAHEAD_PAGES="$2"; shift 2 ;;
        --force-replace-from) require_value "$1" "${2-}"; FORCE_REPLACE_FROM="$2"; shift 2 ;;
        --force-replace-through)
            require_value "$1" "${2-}"
            FORCE_REPLACE_THROUGH="$2"
            FORCE_REPLACE_THROUGH_SET=1
            shift 2
            ;;
        --promote)           PROMOTE=1; shift ;;
        --fill-gaps)         FILL_GAPS=1; shift ;;
        --fill-gaps-overlap)
            require_value "$1" "${2-}"
            FILL_GAPS=1
            FILL_GAPS_OVERLAP="$2"
            shift 2
            ;;
        --page)              require_value "$1" "${2-}"; PAGE="$2"; shift 2 ;;
        --facsimile)         FACSIMILE_MODE="always"; shift ;;
        --verbose|-v)        VERBOSE=1; shift ;;
        --fail-fast)         FAIL_FAST=1; shift ;;
        --dry-run)           DRY_RUN=1; shift ;;
        --help|-h)           usage; exit 0 ;;
        *) echo "Argumento desconhecido: $1" >&2; exit 1 ;;
    esac
done

if [[ ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --jobs deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ "$JOBS" -gt 20 ]]; then
    echo "Erro: --jobs está limitado a 20 para preservar CPU para outros processos." >&2
    exit 1
fi
if [[ "$PIPELINE" != "v2" && "$PIPELINE" != "legacy" ]]; then
    echo "Erro: --pipeline deve ser v2 ou legacy." >&2
    exit 1
fi
if [[ "$FACSIMILE_MODE" != "auto" && "$FACSIMILE_MODE" != "always" && "$FACSIMILE_MODE" != "never" ]]; then
    echo "Erro: --facsimile-mode deve ser auto, always ou never." >&2
    exit 1
fi
if [[ ! "$FACSIMILE_THRESHOLD" =~ ^[0-9]+$ || ! "$LOOKAHEAD_PAGES" =~ ^[0-9]+$ ]]; then
    echo "Erro: thresholds/lookahead devem ser inteiros não negativos." >&2
    exit 1
fi
if [[ ! "$TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --timeout deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ ! "$RETRIES" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --retries deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ -n "$NUM_CTX" && ! "$NUM_CTX" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --num-ctx deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ ! "$FILL_GAPS_OVERLAP" =~ ^[0-9]+$ ]]; then
    echo "Erro: --fill-gaps-overlap deve ser um inteiro não negativo." >&2
    exit 1
fi
if [[ -n "$PAGE" && ! "$PAGE" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --page deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ -n "$FORCE_REPLACE_FROM" && ! "$FORCE_REPLACE_FROM" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --force-replace-from deve ser um inteiro maior que zero." >&2
    exit 1
fi
if [[ "$FORCE_REPLACE_THROUGH" != "next-work" && "$FORCE_REPLACE_THROUGH" != "end" && ! "$FORCE_REPLACE_THROUGH" =~ ^[1-9][0-9]*$ ]]; then
    echo "Erro: --force-replace-through deve ser next-work, end ou uma página positiva." >&2
    exit 1
fi
if [[ $FORCE_REPLACE_THROUGH_SET -eq 1 && -z "$FORCE_REPLACE_FROM" ]]; then
    echo "Erro: --force-replace-through requer --force-replace-from." >&2
    exit 1
fi
if [[ "$PIPELINE" == "v2" && $FILL_GAPS -eq 1 ]]; then
    echo "Erro: --fill-gaps pertence ao pipeline legacy; no v2 use --force-replace-from." >&2
    exit 1
fi
if [[ "$PIPELINE" == "v2" && -n "$PAGE" ]]; then
    echo "Erro: --page isolada não preserva a cadeia v2; use --force-replace-from." >&2
    exit 1
fi
if [[ "$PIPELINE" == "legacy" && -n "$FORCE_REPLACE_FROM" ]]; then
    echo "Erro: --force-replace-from pertence ao pipeline v2." >&2
    exit 1
fi
if [[ "$PIPELINE" == "legacy" && $PROMOTE -eq 1 ]]; then
    echo "Erro: --promote pertence ao pipeline v2." >&2
    exit 1
fi
cd "$SCRIPT_DIR"
VOLUMES=()
while IFS= read -r volume; do
    if [[ -d "$volume" ]]; then
        VOLUMES+=("$volume")
    fi
done < <(compgen -G "$PATTERN" | sort)

if [[ ${#VOLUMES[@]} -eq 0 ]]; then
    echo "❌  Nenhum volume encontrado com padrão: $PATTERN"
    exit 1
fi

TOTAL=${#VOLUMES[@]}
echo "═══════════════════════════════════════════════════════════════"
echo "  Resumo Paralelo – Patrística"
echo "═══════════════════════════════════════════════════════════════"
echo "  Pipeline:          $PIPELINE"
echo "  Provider:          $PROVIDER"
echo "  Modelo:            $MODEL"
echo "  Reasoning effort:  $REASONING"
echo "  Ollama URL:        $OLLAMA_URL"
echo "  OpenAI URL:        $OPENAI_URL"
echo "  API key env:       $API_KEY_ENV"
echo "  Volumes:           $TOTAL"
echo "  Jobs paralelos:    $JOBS"
echo "  DB:                $DB"
echo "  Índices (read-only): $INDICES_DB"
echo "  Timeout:           ${TIMEOUT}s"
echo "  Retries:           $RETRIES"
echo "  Janela de contexto: ${NUM_CTX:-automática por pipeline/modelo}"
echo "  Retomada:          automática pelo manifesto/run v2"
echo "  Fill gaps:         $(if [[ $FILL_GAPS -eq 1 ]]; then echo "Sim (overlap=${FILL_GAPS_OVERLAP})"; else echo "Não"; fi)"
echo "  Página:            ${PAGE:-todas}"
echo "  Fac-símile:        ${FACSIMILE_MODE} (score≥${FACSIMILE_THRESHOLD})"
echo "  Lookahead:         ${LOOKAHEAD_PAGES} página(s) sob ambiguidade contextual"
echo "  Promoção:          $(if [[ $PROMOTE -eq 1 ]]; then echo "segmentos seguros"; else echo "shadow somente"; fi)"
echo "  Force replace:     ${FORCE_REPLACE_FROM:-não} → ${FORCE_REPLACE_THROUGH}"
echo "  Verbose:           $(if [[ $VERBOSE -eq 1 ]]; then echo "Sim"; else echo "Não"; fi)"
echo "  Falha do lote:     $(if [[ $FAIL_FAST -eq 1 ]]; then echo "parar de agendar"; else echo "continuar outros volumes"; fi)"
echo "  Logs:              $LOG_DIR/"
echo "═══════════════════════════════════════════════════════════════"

mkdir -p "$LOG_DIR"
RUN_TOKEN="$(date '+%Y%m%d-%H%M%S')-$$"
JOB_LOG="${LOG_DIR}/jobs-${RUN_TOKEN}.tsv"

build_cmd() {
    local volume="$1"
    local volume_name
    local log_file
    local -a args

    volume_name="$(basename "$volume")"
    log_file="${LOG_DIR}/${volume_name}.log"
    args=(
        python "$RESUMO_SCRIPT"
        --pipeline "$PIPELINE"
        --provider "$PROVIDER"
        --model "$MODEL"
        --reasoning-effort "$REASONING"
        --ollama-url "$OLLAMA_URL"
        --openai-url "$OPENAI_URL"
        --api-key-env "$API_KEY_ENV"
        --timeout "$TIMEOUT"
        --retries "$RETRIES"
        --db "$DB"
        --indices-db "$INDICES_DB"
        --facsimile-mode "$FACSIMILE_MODE"
        --facsimile-threshold "$FACSIMILE_THRESHOLD"
        --lookahead-pages "$LOOKAHEAD_PAGES"
    )

    if [[ -n "$NUM_CTX" ]]; then
        args+=(--num-ctx "$NUM_CTX")
    fi

    if [[ $FILL_GAPS -eq 1 ]]; then
        args+=(--fill-gaps --fill-gaps-overlap "$FILL_GAPS_OVERLAP")
    fi
    if [[ -n "$PAGE" ]]; then
        args+=(--page "$PAGE")
    fi
    if [[ -n "$FORCE_REPLACE_FROM" ]]; then
        args+=(--force-replace-from "$FORCE_REPLACE_FROM" --force-replace-through "$FORCE_REPLACE_THROUGH")
    fi
    if [[ $PROMOTE -eq 1 ]]; then
        args+=(--promote)
    fi
    if [[ "$PIPELINE" == "legacy" && "$FACSIMILE_MODE" == "always" ]]; then
        args+=(--facsimile)
    fi
    if [[ $VERBOSE -eq 1 ]]; then
        args+=(--verbose)
    fi
    args+=(--volume-dir "$volume")

    printf '%q ' "${args[@]}"
    printf '> %q 2>&1\n' "$log_file"
}

if [[ $DRY_RUN -eq 1 ]]; then
    echo ""
    echo "🔍  DRY RUN – Comandos que seriam executados:"
    echo ""
    for volume in "${VOLUMES[@]}"; do
        build_cmd "$volume"
    done
    echo ""
    echo "(total: $TOTAL comandos, $JOBS em paralelo)"
    exit 0
fi

COMMAND_FILE="$(mktemp /tmp/resumo_parallel_commands.XXXXXX)"
cleanup_command_file() {
    rm -f -- "$COMMAND_FILE"
}
trap cleanup_command_file EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for volume in "${VOLUMES[@]}"; do
    build_cmd "$volume" >> "$COMMAND_FILE"
done

STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
echo ""
echo "🚀  Iniciando $TOTAL volumes com até $JOBS em paralelo..."
echo "    Início: $STARTED_AT"
echo "    Logs individuais em: $LOG_DIR/<volume>.log"
echo ""

RUN_STATUS=0
if [[ "${RESUMO_PARALLEL_DISABLE_GNU:-0}" != "1" ]] && command -v parallel &>/dev/null; then
    echo "📦  Usando GNU parallel"
    parallel_args=(--jobs "$JOBS" --joblog "$JOB_LOG")
    if [[ $FAIL_FAST -eq 1 ]]; then
        parallel_args+=(--halt soon,fail=1)
    fi
    if [[ -t 2 ]]; then
        parallel_args=(--bar "${parallel_args[@]}")
    fi
    if parallel "${parallel_args[@]}" < "$COMMAND_FILE"; then
        RUN_STATUS=0
    else
        RUN_STATUS=$?
    fi
else
    echo "📦  GNU parallel não encontrado – usando bash job control"
    running=0
    finished=0
    failed=0

    while IFS= read -r command_line; do
        bash -c "$command_line" &
        running=$((running + 1))

        while [[ $running -ge $JOBS ]]; do
            if ! wait -n; then
                failed=$((failed + 1))
            fi
            running=$((running - 1))
            finished=$((finished + 1))
            printf "  ⏳ Progresso: %d/%d concluídos (%d erros)\r" "$finished" "$TOTAL" "$failed"
        done
    done < "$COMMAND_FILE"

    while [[ $running -gt 0 ]]; do
        if ! wait -n; then
            failed=$((failed + 1))
        fi
        running=$((running - 1))
        finished=$((finished + 1))
        printf "  ⏳ Progresso: %d/%d concluídos (%d erros)\r" "$finished" "$TOTAL" "$failed"
    done
    echo ""

    if [[ $failed -gt 0 ]]; then
        RUN_STATUS=1
    fi
fi

PROBLEM_LOGS=()
for volume in "${VOLUMES[@]}"; do
    volume_name="$(basename "$volume")"
    log_file="${LOG_DIR}/${volume_name}.log"
    if [[ -f "$log_file" ]] && grep -Eq ' ERROR |Erro fatal|esgotou tentativas|mantida em shadow; cadeia pausada' "$log_file"; then
        PROBLEM_LOGS+=("$log_file")
    fi
done

echo ""
echo "═══════════════════════════════════════════════════════════════"
if [[ $RUN_STATUS -eq 0 && ${#PROBLEM_LOGS[@]} -eq 0 ]]; then
    echo "  ✅ Concluído sem erros finais"
else
    echo "  ⚠️  Execução concluída com problemas"
    echo "  Status do executor: $RUN_STATUS"
    echo "  Logs desta execução com erro ou cadeia pausada: ${#PROBLEM_LOGS[@]}"
    for log_file in "${PROBLEM_LOGS[@]}"; do
        echo "    - $log_file"
    done
fi
echo "  Início:  $STARTED_AT"
echo "  Fim:     $(date '+%Y-%m-%d %H:%M:%S')"
if [[ -f "$JOB_LOG" ]]; then
    echo "  Job log: $JOB_LOG"
fi
echo "═══════════════════════════════════════════════════════════════"

if [[ $RUN_STATUS -ne 0 || ${#PROBLEM_LOGS[@]} -gt 0 ]]; then
    exit 1
fi

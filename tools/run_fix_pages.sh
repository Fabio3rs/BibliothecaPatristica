#!/usr/bin/env bash
# set -euo pipefail

# Reprocessa páginas marcadas com possíveis problemas de OCR na tabela `resumos`.
# Agrupa por documento e chama main2.py com --refresh-pages para evitar múltiplas
# conversões de um mesmo PDF.
#
# Ajuste os caminhos abaixo se necessário.
DB_PATH="${DB_PATH:-data/patristica_resumos.db}"
PDF_ROOT="${PDF_ROOT:-/homessddata/patristica}"
OUT_DIR="${OUT_DIR:-teste}"
LANGS="${LANGS:-lat+grc+ell}"
PROCS="${PROCS:-12}"
OMP_THREADS="${OMP_THREADS:-2}"

SQL_COND="(summary_page_clean LIKE '%OCR%' OR summary_page_clean LIKE '%digitaliza%' OR summary_page_clean LIKE '%impressão%' OR summary_page_clean LIKE '%corromp%' OR summary_page_clean LIKE '%fac-simile%') AND documento NOT LIKE 'PO%'"

readarray -t rows < <(sqlite3 -readonly -separator '|' "$DB_PATH" "SELECT documento,pagina_num FROM resumos WHERE $SQL_COND ORDER BY documento, pagina_num;")

if [[ ${#rows[@]} -eq 0 ]]; then
  echo "Nada a processar (query vazia)."
  exit 0
fi

declare -A pages_by_doc
for row in "${rows[@]}"; do
  IFS='|' read -r doc page <<<"$row"
  [[ -z "$doc" || -z "$page" ]] && continue
  if [[ -n "${pages_by_doc[$doc]:-}" ]]; then
    pages_by_doc[$doc]+=",$page"
  else
    pages_by_doc[$doc]="$page"
  fi
done

mapfile -t doc_list < <(printf "%s\n" "${!pages_by_doc[@]}" | sort)

for doc in "${doc_list[@]}"; do
  pdf="$PDF_ROOT/${doc}.pdf"
  if [[ ! -f "$pdf" ]]; then
    echo "[WARN] PDF não encontrado: $pdf — pulando"
    continue
  fi

  refresh="${pages_by_doc[$doc]}"
  echo "[RUN] $doc páginas: $refresh"
  python main2.py "$pdf" \
    --algorithm ollama \
    --out "$OUT_DIR" \
    --lang "$LANGS" \
    --verify-judge-llm \
    --procs "$PROCS" \
    --omp-threads "$OMP_THREADS" \
    --refresh-pages "$refresh"

  
  # python main2.py "$pdf" \
  #   --algorithm ollama \
  #   --out "$OUT_DIR" \
  #   --lang "$LANGS" \
  #   --verify-fix \
  #   --procs "$PROCS" \
  #   --omp-threads "$OMP_THREADS" \
  #   --refresh-pages "$refresh"
done

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python do projeto não encontrado: $PYTHON_BIN" >&2
    exit 1
fi

exec "$PYTHON_BIN" "${SCRIPT_DIR}/cleanup_defective_resumos_v2.py" "$@"

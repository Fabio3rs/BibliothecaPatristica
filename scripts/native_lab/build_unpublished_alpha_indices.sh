#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

cd "$repo_root"
python3 tools/export_alphabetical_indices.py \
  --db data/alphabetical_indices.db \
  --out web/public/alpha

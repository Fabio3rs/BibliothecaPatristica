#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
site_port=${SITE_PORT:-4322}

cd "$repo_root/web"
exec npm run preview -- --host 127.0.0.1 --port "$site_port"

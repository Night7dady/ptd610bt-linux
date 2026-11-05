#!/usr/bin/env bash
set -euo pipefail
project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec /usr/bin/python3 -I -B "$project_dir/scripts/ptd610bt_gui.py" "$@"

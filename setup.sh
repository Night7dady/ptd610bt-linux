#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  --help|-h)
    printf '%s\n' 'Usage: ./setup.sh [--check|--desktop|--help]' \
      'Check existing dependencies only; no downloads, installs, pairing or printing.' \
      '--desktop also checks KDE association commands.'
    exit 0
    ;;
  ''|--check|--desktop) ;;
  *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  printf '%s\n' 'Pass at most one option.' >&2
  exit 2
fi
command -v python3 >/dev/null || { printf '%s\n' 'Python 3.10+ is required.' >&2; exit 1; }
python3 -I -B - <<'PY'
import re
import sys
from pathlib import Path
if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10+ is required.')
try:
    import PIL
    from PIL import ImageFont
except ImportError:
    raise SystemExit('Install Pillow first; see docs/DEPLOYMENT.md.')
version = tuple(map(int, re.findall(r'\d+', PIL.__version__)[:2]))
if version < (10, 1):
    raise SystemExit('Pillow 10.1+ is required.')
for filename in ['LiberationSans-Regular.ttf', 'LiberationSans-Bold.ttf']:
    path = Path('/usr/share/fonts/liberation-sans-fonts') / filename
    if not path.is_file():
        raise SystemExit('LBX needs Fedora liberation-sans-fonts; see docs/DEPLOYMENT.md.')
print(f'Python {sys.version.split()[0]}, Pillow {PIL.__version__}, LBX fonts: OK')
PY
if [[ "${1:-}" == --desktop ]]; then
  for tool in konsole update-mime-database update-desktop-database desktop-file-validate xdg-mime; do
    command -v "$tool" >/dev/null || { printf 'Missing desktop command: %s\n' "$tool" >&2; exit 1; }
  done
  printf '%s\n' 'Desktop commands: OK'
fi
printf '%s\n' 'Checks passed. See docs/USAGE.md and docs/DEPLOYMENT.md.' \
  'No configuration was installed and no printer was contacted.'

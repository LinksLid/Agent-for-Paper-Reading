#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
export PYTHONUTF8=1
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys;sys.exit(sys.version_info[:2] < (3,11))'; then
  exec python3 launch.py "$@"
fi
printf '%s\n' 'Python 3.11+ is required for the server; opening the offline website instead.'
if [ "${1:-}" = '--no-browser' ]; then exit 2; fi
if command -v open >/dev/null 2>&1; then exec open index.html; fi
if command -v xdg-open >/dev/null 2>&1; then exec xdg-open index.html; fi
printf '%s\n' 'Open index.html in a browser, or install Python 3.11+.'

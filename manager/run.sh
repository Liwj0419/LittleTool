#!/usr/bin/env bash
# Own virtual environment; source entrypoint; all app arguments are preserved.
set -euo pipefail
cd -- "$(dirname -- "$0")"
CONSOLE=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    -c|--console) CONSOLE=1 ;;
    *) ARGS+=("$arg"); case "$arg" in --selftest|--quit|--reload|--where|--dump|--build-only|--bench=*) CONSOLE=1 ;; esac ;;
  esac
done
if [[ -x .venv/Scripts/python.exe ]]; then
  PY=.venv/Scripts/python.exe
elif [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  if [[ -n "${PYTHON:-}" ]]; then
    "$PYTHON" -m venv .venv
  elif command -v py >/dev/null 2>&1; then
    py -3 -m venv .venv
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m venv .venv
  else
    python -m venv .venv
  fi
  if [[ -x .venv/Scripts/python.exe ]]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
  "$PY" -m pip install -r requirements.txt --disable-pip-version-check
fi
if ! "$PY" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(m) for m in ('webview','pynput','pystray','PIL',)) else 1)"; then
  "$PY" -m pip install -r requirements.txt --disable-pip-version-check
fi
PYW="${PY%/*}/pythonw.exe"
if [[ $CONSOLE -eq 1 || ! -x "$PYW" ]]; then
  exec "$PY" app.py "${ARGS[@]}"
fi
exec "$PYW" app.py "${ARGS[@]}"

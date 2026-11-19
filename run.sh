#!/bin/sh
# Start EpiAtlas on this computer, then open http://127.0.0.1:8000
# First run installs the Python packages (a few minutes) and creates the demo patient.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -d data/raw ] || .venv/bin/python scripts/make_demo_data.py
export EPIATLAS_DATA="$PWD/data" PYTHONDONTWRITEBYTECODE=1
echo "EpiAtlas running: open http://127.0.0.1:8000  (Ctrl+C to stop)"
cd src && exec ../.venv/bin/uvicorn epiatlas.server:app --host 127.0.0.1 --port 8000

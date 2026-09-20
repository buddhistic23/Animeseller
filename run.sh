#!/usr/bin/env sh
# Local dev: ./run.sh   (reads .env; copy .env.example first)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn arbitrage.main:app --reload --port "${PORT:-8000}"

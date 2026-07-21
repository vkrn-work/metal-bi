#!/usr/bin/env bash
# Локальный запуск: ./run_local.sh  → http://127.0.0.1:8000
set -e
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
export APP_PASSWORD="${APP_PASSWORD:-emk2026}"
uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir app --reload

#!/bin/bash
# AWS 등 Linux 서버에서 서버 실행 (테스트용)
# 사용: ./run_server.sh   또는  bash run_server.sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo ".venv 없음. 먼저 실행: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000

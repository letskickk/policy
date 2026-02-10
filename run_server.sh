#!/bin/bash
# AWS 등 Linux 서버에서 서버 실행 (테스트용)
# 사용: ./run_server.sh   또는  bash run_server.sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo ".venv 없음. 먼저 실행: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
# faiss 등 의존성 미설치 시 자동 설치
if ! .venv/bin/python -c "import faiss" 2>/dev/null; then
    echo "faiss 미설치. requirements.txt 설치 중..."
    .venv/bin/pip install -r requirements.txt
fi
# 단일 워커로 실행 (멀티워커 레이스 회피. gunicorn 사용 시에는 WEB_CONCURRENCY=1 또는 -w 1 권장)
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 80

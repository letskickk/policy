#!/bin/bash
# AWS 서버에서 서버 재시작 스크립트
cd "$(dirname "$0")"

echo "기존 서버 프로세스 종료 중..."
pkill -f "uvicorn backend.main:app" || echo "실행 중인 프로세스 없음"

sleep 2

echo "서버 시작 중..."
if [ ! -d .venv ]; then
    echo ".venv 없음. 먼저 실행: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 백그라운드에서 실행
nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &

echo "서버가 백그라운드에서 시작되었습니다."
echo "로그 확인: tail -f server.log"
echo "프로세스 확인: ps aux | grep uvicorn"

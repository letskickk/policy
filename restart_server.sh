#!/bin/bash
# AWS 서버에서 서버 재시작 스크립트 (sudo 불필요)
# 사용: ./restart_server.sh   또는  bash restart_server.sh
cd "$(dirname "$0")"

echo "[1/2] 기존 서버 프로세스 종료 중..."
pkill -f "uvicorn backend.main:app" 2>/dev/null && echo "  기존 프로세스 종료됨" || echo "  실행 중인 프로세스 없음"
sleep 2

echo "[2/2] 서버 시작 중..."
if [ ! -d .venv ]; then
    echo "  .venv 없음. 먼저 실행: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 80 > server.log 2>&1 &
sleep 1

echo ""
echo "서버 재시작 완료."
echo "  로그: tail -f server.log"
echo "  확인: ps aux | grep uvicorn"

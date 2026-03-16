#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo ".venv 없음. 먼저 가상환경을 준비하세요."
    exit 1
fi

mkdir -p data

LOCK_FILE="data/amaranth-sync.lock"
LOG_FILE="data/amaranth-sync.log"
TIMEOUT_SECONDS="${AMARANTH_TIMEOUT_SECONDS:-900}"
LIMIT="${AMARANTH_LIMIT:-6}"
HEADLESS_FLAG="${AMARANTH_HEADLESS_FLAG:---headless}"
KIND="${AMARANTH_KIND:-meetings}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "이미 실행 중입니다: $LOCK_FILE"
    exit 1
fi

echo "[$(date '+%F %T')] starting amaranth sync kind=$KIND limit=$LIMIT" >> "$LOG_FILE"

ionice -c3 nice -n 15 \
timeout --signal=TERM --kill-after=60 "$TIMEOUT_SECONDS" \
    .venv/bin/python scripts/sync_amaranth_meetings.py \
    "$HEADLESS_FLAG" \
    --kind "$KIND" \
    --limit "$LIMIT" \
    >> "$LOG_FILE" 2>&1

echo "[$(date '+%F %T')] finished amaranth sync" >> "$LOG_FILE"

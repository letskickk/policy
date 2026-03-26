#!/bin/bash
# 이슈 레이더 자동 스캔 cron 설정
# 매주 월요일 06:00 KST에 실행
#
# 사용법:
#   bash scripts/setup_cron.sh          # cron 등록
#   bash scripts/setup_cron.sh --remove # cron 제거

PROJECT_DIR="/home/ubuntu/Policy"
VENV="$PROJECT_DIR/.venv/bin/python"
SCRIPT="$PROJECT_DIR/scripts/run_issue_radar.py"
LOG="$PROJECT_DIR/logs/issue-radar-cron.log"
CRON_TAG="# policy-issue-radar-auto"

mkdir -p "$(dirname "$LOG")"

if [ "$1" = "--remove" ]; then
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    echo "이슈 레이더 cron 제거 완료."
    exit 0
fi

# Remove old entry if exists, then add new
EXISTING=$(crontab -l 2>/dev/null | grep -v "$CRON_TAG")
NEW_CRON="0 6 * * 1 cd $PROJECT_DIR && $VENV $SCRIPT --json >> $LOG 2>&1 $CRON_TAG"

echo "$EXISTING
$NEW_CRON" | crontab -

echo "이슈 레이더 cron 등록 완료."
echo "  주기: 매주 월요일 06:00"
echo "  로그: $LOG"
echo ""
echo "확인: crontab -l | grep issue-radar"

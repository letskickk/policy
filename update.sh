#!/bin/bash
# 배포 서버에서: 코드 받아오기 + 서버 재시작 (매번 이거만 실행)
# 사용: ./update.sh   또는  bash update.sh
cd "$(dirname "$0")"

echo "코드 업데이트 중..."
git pull

echo ""
# 1) systemd 사용 시 (sudo 있을 때)
if sudo systemctl restart policy-app 2>/dev/null; then
    echo "서버 재시작됨 (systemd)"
    exit 0
fi

# 2) systemd 없거나 sudo 안 될 때: pkill + nohup
echo "재시작 스크립트 실행..."
bash ./restart_server.sh

#!/bin/bash
# 배포 서버에서: 코드 받아오기 + 서버 재시작 (매번 올릴 때 이거만 실행)
cd "$(dirname "$0")"
git pull
# systemd 쓸 때:
sudo systemctl restart policy-app 2>/dev/null && echo "서버 재시작됨 (systemd)" && exit 0
# systemd 없으면 프로세스는 수동으로 Ctrl+C 후 ./run_server.sh 다시 실행하세요
echo "git pull 완료. 서버는 수동으로 재시작하세요 (./run_server.sh)"

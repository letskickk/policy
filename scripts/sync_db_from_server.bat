@echo off
REM 서버 DB를 로컬 data\policy_server.db 로 복사
REM 아래 변수를 실제 서버 정보로 수정 후 실행
set SERVER_HOST=your-server-ip-or-domain
set SERVER_USER=ubuntu
set SERVER_DB_PATH=/home/ubuntu/policy/data/policy.db

cd /d "%~dp0.."
python scripts/sync_db_from_server.py
pause

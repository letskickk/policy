"""
서버의 policy.db를 로컬로 복사해, 로컬에서 서버와 동일한 DB로 테스트할 수 있게 합니다.

사용법:
  1) 환경변수로 서버 정보 지정 후 실행:
     set SERVER_HOST=your-ec2-or-domain
     set SERVER_USER=ubuntu
     set SERVER_DB_PATH=/home/ubuntu/policy/data/policy.db
     python scripts/sync_db_from_server.py

  2) 또는 인자로 지정:
     python scripts/sync_db_from_server.py [USER@]HOST [REMOTE_PATH]

로컬 저장 경로: data/policy_server.db (덮어씀)
실행 후 .env에 다음 한 줄 추가 후 서버 실행:
  DATABASE_PATH=data/policy_server.db
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = ROOT / "data" / "policy_server.db"


def main() -> int:
    # .env에서 SERVER_HOST 등 읽기
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
        except ImportError:
            pass

    if len(sys.argv) >= 3:
        host = sys.argv[1]  # user@host or host
        remote_path = sys.argv[2]
    else:
        host = os.environ.get("SERVER_USER", "") + ("@" if os.environ.get("SERVER_USER") else "") + os.environ.get("SERVER_HOST", "")
        remote_path = os.environ.get("SERVER_DB_PATH", "/home/ubuntu/policy/data/policy.db")

    if not host or host == "@":
        print("Usage: python scripts/sync_db_from_server.py [USER@]HOST [REMOTE_PATH]", file=sys.stderr)
        print("  or set SERVER_HOST, SERVER_USER (optional), SERVER_DB_PATH", file=sys.stderr)
        return 1

    LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    remote = f"{host}:{remote_path}"
    print(f"Copying {remote} -> {LOCAL_DB}")
    r = subprocess.call(["scp", remote, str(LOCAL_DB)])
    if r != 0:
        print("scp failed. Ensure OpenSSH (scp) is available and you have access to the server.", file=sys.stderr)
        return r
    print(f"Saved to {LOCAL_DB}")
    print("In .env set: DATABASE_PATH=data/policy_server.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())

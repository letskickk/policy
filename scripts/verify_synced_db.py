"""로컬 policy_server.db가 서버에서 제대로 복사됐는지 검증."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_SERVER = ROOT / "data" / "policy_server.db"
DB_LOCAL = ROOT / "data" / "policy.db"


def main():
    if not DB_SERVER.exists():
        print(f"파일 없음: {DB_SERVER}")
        return 1
    size = DB_SERVER.stat().st_size
    print(f"파일: {DB_SERVER.name}")
    print(f"크기: {size:,} bytes")
    print()

    conn = sqlite3.connect(str(DB_SERVER))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print("테이블:", ", ".join(tables))
    print()

    for t in ["users", "candidates", "usage_logs"]:
        if t in tables:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n:,} rows")
    print()

    cur = conn.execute(
        "SELECT id, email, status, role FROM users ORDER BY id LIMIT 10"
    )
    rows = cur.fetchall()
    print("회원 (최대 10명):")
    for r in rows:
        print(" ", dict(r))
    conn.close()

    # 결론
    conn2 = sqlite3.connect(str(DB_SERVER))
    user_count = conn2.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn2.close()

    print()
    if user_count == 0:
        print(">>> 로그인 불가: users 테이블에 계정이 없습니다.")
        print(">>> 해결: 배치(2_서버실행_xxx.bat)를 실행해 서버에서 DB를 다시 받으세요.")
        print(">>>     (SSH 키가 서버에 등록돼 있어야 합니다)")
        return 1
    print("검증 완료: policy_server.db에 회원 있음, 로그인 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

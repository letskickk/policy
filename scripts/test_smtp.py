#!/usr/bin/env python3
"""
SMTP 연결·발송 테스트. 실제 오류 메시지를 터미널에 출력합니다.
실행: 프로젝트 루트에서  python scripts/test_smtp.py [받을이메일]
"""
import sys
from pathlib import Path

# 프로젝트 루트를 path에 넣어 backend 로드
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def main():
    from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL
    to = (sys.argv[1:] or ["test@example.com"])[0]

    print("설정 확인:")
    print(f"  SMTP_HOST = {SMTP_HOST!r}")
    print(f"  SMTP_PORT = {SMTP_PORT}")
    print(f"  SMTP_USER = {SMTP_USER!r} (길이 {len(SMTP_USER) if SMTP_USER else 0})")
    print(f"  SMTP_PASS = {'(설정됨)' if SMTP_PASS else '(비어있음)'}")
    print(f"  FROM_EMAIL = {FROM_EMAIL!r}")
    print(f"  수신 테스트 주소 = {to}")
    print()

    if not SMTP_HOST or not SMTP_USER:
        print("오류: SMTP_HOST 또는 SMTP_USER가 비어 있습니다. .env를 확인하세요.")
        return 1

    import smtplib
    from email.mime.text import MIMEText

    try:
        print("1) SMTP 연결 중...")
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        print("   연결됨.")
        print("2) STARTTLS...")
        s.starttls()
        print("   OK.")
        print("3) 로그인...")
        s.login(SMTP_USER, SMTP_PASS)
        print("   로그인 성공.")
        msg = MIMEText("SMTP 테스트 메일입니다.", "plain", "utf-8")
        msg["Subject"] = "[테스트] 정책 멘토링 SMTP"
        msg["From"] = FROM_EMAIL
        msg["To"] = to
        print("4) 메일 발송 시도...")
        s.sendmail(FROM_EMAIL, [to], msg.as_string())
        s.quit()
        print("   발송 성공. 받은편지함(스팸함)을 확인하세요.")
        return 0
    except smtplib.SMTPAuthenticationError as e:
        print(f"   인증 실패: {e}")
        print("   → SMTP_USER/SMTP_PASS 확인. AWS SES는 IAM이 아닌 'SES SMTP 인증 정보'를 써야 합니다.")
        return 1
    except smtplib.SMTPException as e:
        print(f"   SMTP 오류: {e}")
        return 1
    except OSError as e:
        print(f"   연결/네트워크 오류: {e}")
        return 1
    except Exception as e:
        print(f"   오류: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

import os
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing env: {name}")
    return value


def send_once(subject: str, body: str) -> None:
    host = _required("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = _required("SMTP_USER")
    password = _required("SMTP_PASS")
    from_email = _required("FROM_EMAIL")
    to_email = _required("REPORT_TO_EMAIL")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.sendmail(from_email, [to_email], msg.as_string())


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: progress_mailer.py <status_file>")
        return 2

    status_path = Path(sys.argv[1]).resolve()
    interval = int(os.getenv("REPORT_INTERVAL_SECONDS", "1800"))
    subject_prefix = os.getenv("REPORT_SUBJECT_PREFIX", "[policy] 작업 진행 보고").strip()

    while True:
        body = status_path.read_text(encoding="utf-8") if status_path.exists() else "(status file missing)"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        send_once(f"{subject_prefix} {timestamp}", body)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

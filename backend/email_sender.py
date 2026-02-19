"""
이메일 발송 (인증 링크 등).
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL, APP_BASE_URL

logger = logging.getLogger(__name__)


def send_verification_email(to_email: str, token: str) -> bool:
    """이메일 인증 링크 발송."""
    if not SMTP_HOST or not SMTP_USER:
        logger.warning("SMTP 미설정. 이메일 발송 건너뜀.")
        return False
    link = f"{APP_BASE_URL}/verify-email?token={token}"
    subject = "[개혁신당] 지방선거 정책 멘토링 이메일 인증을 완료해 주세요"
    body = f"""안녕하세요.

회원가입을 완료하려면 아래 링크를 클릭해 이메일 인증을 완료해 주세요.

{link}

(이 링크는 24시간 후 만료됩니다. 본인이 요청하지 않았다면 무시하세요.)

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다. 문의는 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.
"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        logger.info("인증 이메일 발송: %s", to_email)
        return True
    except Exception as e:
        logger.exception("이메일 발송 실패: %s", e)
        return False

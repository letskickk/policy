"""
이메일 발송 (인증 링크 등).
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL, APP_BASE_URL, ADMIN_NOTIFY_EMAIL

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


ELECTION_LABELS = {
    "metro_mayor": "광역단체장",
    "local_mayor": "기초단체장",
    "regional_council": "광역의원",
    "local_council": "기초의원",
    "party_official": "당직자",
}


def send_signup_notification(
    user_email: str,
    name: str = "",
    phone: str = "",
    election_position: str = "",
    region_name: str = "",
    district_name: str = "",
) -> bool:
    """새 회원가입 시 관리자에게 알림 메일 발송."""
    if not SMTP_HOST or not SMTP_USER or not ADMIN_NOTIFY_EMAIL:
        logger.warning("SMTP 또는 ADMIN_NOTIFY_EMAIL 미설정. 가입 알림 건너뜀.")
        return False

    pos_label = ELECTION_LABELS.get(election_position, election_position or "미선택")
    location = region_name or ""
    if district_name:
        location = f"{location} {district_name}".strip()

    subject = f"[정책멘토링] 새 회원가입: {name or user_email}"
    body = f"""새로운 회원이 가입했습니다.

이름: {name or '(미입력)'}
이메일: {user_email}
전화번호: {phone or '(미입력)'}
가입 유형: {pos_label}
지역: {location or '(미선택)'}

관리자 페이지에서 승인/거절하세요:
{APP_BASE_URL}/admin/users
"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ADMIN_NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(FROM_EMAIL, [ADMIN_NOTIFY_EMAIL], msg.as_string())
        logger.info("가입 알림 메일 발송: %s → %s", user_email, ADMIN_NOTIFY_EMAIL)
        return True
    except Exception as e:
        logger.exception("가입 알림 메일 발송 실패: %s", e)
        return False

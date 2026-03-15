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

(이 링크는 72시간 후 만료됩니다. 본인이 요청하지 않았다면 무시하세요.)

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


def send_approval_status_email(to_email: str, status: str, name: str = "") -> bool:
    """회원 승인/거절/정지 시 당사자에게 알림 메일 발송.

    status: 'APPROVED' | 'REJECTED' | 'SUSPENDED'
    """
    if not SMTP_HOST or not SMTP_USER:
        logger.warning("SMTP 미설정. 승인 알림 메일 건너뜀.")
        return False

    greeting = f"{name}님" if name else "안녕하세요"

    if status == "APPROVED":
        subject = "[개혁신당] 정책 멘토링 서비스 가입이 승인되었습니다"
        body = f"""{greeting}, 안녕하세요.

개혁신당 지방선거 정책 멘토링 서비스 가입 신청이 승인되었습니다.

아래 링크에서 로그인 후 바로 이용하실 수 있습니다.

{APP_BASE_URL}

공약 점검, 정강정책 부합 여부 분석, 지역 비교 등 다양한 기능을 활용해 좋은 공약을 만드시길 응원합니다.

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다. 문의는 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.
"""
    elif status == "REJECTED":
        subject = "[개혁신당] 정책 멘토링 서비스 가입 신청 결과 안내"
        body = f"""{greeting}, 안녕하세요.

개혁신당 지방선거 정책 멘토링 서비스 가입 신청을 검토한 결과, 이번에는 승인이 어렵게 되었습니다.

문의 사항이 있으시면 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다.
"""
    elif status == "SUSPENDED":
        subject = "[개혁신당] 정책 멘토링 서비스 계정 이용 제한 안내"
        body = f"""{greeting}, 안녕하세요.

개혁신당 지방선거 정책 멘토링 서비스 계정이 일시 정지되었습니다.

문의 사항이 있으시면 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다.
"""
    else:
        logger.warning("send_approval_status_email: 알 수 없는 status=%s", status)
        return False

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
        logger.info("승인 알림 메일 발송: %s (status=%s)", to_email, status)
        return True
    except Exception as e:
        logger.exception("승인 알림 메일 발송 실패: %s", e)
        return False


def send_candidate_approval_status_email(
    to_email: str,
    status: str,
    name: str = "",
    candidate_name: str = "",
    rejection_reason: str = "",
) -> bool:
    """공약 등록 후보 승인/거절 시 당사자에게 알림 메일 발송.

    status: 'APPROVED' | 'REJECTED'
    name: 회원 이름(users.name)
    candidate_name: 후보 등록명(candidates.name), 없으면 name 사용
    """
    if not SMTP_HOST or not SMTP_USER:
        logger.warning("SMTP 미설정. 공약 승인 알림 메일 건너뜀.")
        return False

    display_name = candidate_name or name or ""
    greeting = f"{display_name}님" if display_name else "안녕하세요"

    if status == "APPROVED":
        subject = "[개혁신당] 공약 등록이 승인되었습니다"
        body = f"""{greeting}, 안녕하세요.

개혁신당 지방선거 정책 멘토링 서비스에 등록하신 공약이 승인되었습니다.

이제 공약 지도에서 공약이 공개되며, 정책 점검 기능을 계속 이용하실 수 있습니다.

{APP_BASE_URL}/my-pledges

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다. 문의는 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.
"""
    elif status == "REJECTED":
        subject = "[개혁신당] 공약 등록 검토 결과 안내"
        reason_line = f"\n[거절 사유]\n{rejection_reason}\n" if rejection_reason else ""
        body = f"""{greeting}, 안녕하세요.

개혁신당 지방선거 정책 멘토링 서비스에 등록하신 공약을 검토한 결과, 이번에는 승인이 어렵게 되었습니다.
{reason_line}
공약을 수정하신 후 다시 저장하시면 재심사가 진행됩니다.

문의 사항이 있으시면 개혁신당 정책국(letskick@reformparty.kr)으로 연락해 주세요.

---
이 메일은 발신 전용 주소에서 보내는 것으로, 회신은 되지 않습니다.
"""
    else:
        logger.warning("send_candidate_approval_status_email: 알 수 없는 status=%s", status)
        return False

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
        logger.info("공약 승인 알림 메일 발송: %s (status=%s)", to_email, status)
        return True
    except Exception as e:
        logger.exception("공약 승인 알림 메일 발송 실패: %s", e)
        return False


def send_pledge_registration_notification(
    user_email: str,
    name: str = "",
    candidate_name: str = "",
    election_position: str = "",
    region_name: str = "",
    district_name: str = "",
    pledge_count: int = 0,
    pledges_summary: str = "",
) -> bool:
    """공약 등록/수정 시 관리자에게 알림 메일 발송."""
    if not SMTP_HOST or not SMTP_USER or not ADMIN_NOTIFY_EMAIL:
        logger.warning("SMTP 또는 ADMIN_NOTIFY_EMAIL 미설정. 공약 등록 알림 건너뜀.")
        return False

    pos_label = ELECTION_LABELS.get(election_position, election_position or "미선택")
    location = region_name or ""
    if district_name:
        location = f"{location} {district_name}".strip()

    display = candidate_name or name or user_email
    subject = f"[정책멘토링] 공약 등록/수정: {display} ({pledge_count}개)"
    body = f"""공약이 등록/수정되었습니다.

이름: {display}
이메일: {user_email}
출마 유형: {pos_label}
지역: {location or '(미선택)'}
공약 수: {pledge_count}개

{pledges_summary}

관리자 페이지에서 승인/거절하세요:
{APP_BASE_URL}/admin/candidates
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
        logger.info("공약 등록 알림 메일 발송: %s → %s", user_email, ADMIN_NOTIFY_EMAIL)
        return True
    except Exception as e:
        logger.exception("공약 등록 알림 메일 발송 실패: %s", e)
        return False


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

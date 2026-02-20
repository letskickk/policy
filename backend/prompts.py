"""
당 부합 점검용 프롬프트 로드 및 치환.
정강·정책(이념·취지) 컨텍스트, 우리당 공약 컨텍스트를 구분해 넣는다.
회원가입 시 저장한 출마지역·선거유형을 프롬프트 메타필드로 매핑한다.
"""
from pathlib import Path
from typing import Optional

from backend.config import PROMPTS_DIR

# 회원가입 election_position 값 → 프롬프트용 한글 라벨 (openai_vector_store 매핑과 동일)
from backend.openai_vector_store import ELECTION_TYPE_KEY_TO_LABEL

ELECTION_POSITION_TO_TYPE = dict(ELECTION_TYPE_KEY_TO_LABEL)  # metro_mayor, local_mayor, education, regional_council, local_council
ELECTION_POSITION_TO_LEVEL = {
    "metro_mayor": "광역",
    "regional_council": "광역",
    "local_mayor": "기초",
    "local_council": "기초",
    "education": "광역",  # 교육감
}


def build_pledge_meta_from_user(user: Optional[dict]) -> dict:
    """
    회원가입 시 저장한 user 레코드로 프롬프트 메타필드 값을 만든다.
    Returns: election_type, region_level, region_province, region_city, district_name
    """
    if not user:
        return {
            "election_type": "",
            "region_level": "",
            "region_province": "",
            "region_city": "",
            "district_name": "",
        }
    ep = (user.get("election_position") or "").strip().lower()
    election_type = ELECTION_POSITION_TO_TYPE.get(ep, ep or "")
    region_level = ELECTION_POSITION_TO_LEVEL.get(ep, "")
    region_province = (user.get("region_name") or "").strip()
    district_full = (user.get("district_name") or "").strip()
    # district_name: "강남구 제1선거구" → region_city="강남구", district_name="제1선거구"
    if " " in district_full:
        parts = district_full.split(" ", 1)
        region_city = (parts[0] or "").strip()
        district_name = (parts[1] or "").strip()
    else:
        region_city = district_full
        district_name = ""
    return {
        "election_type": election_type,
        "region_level": region_level,
        "region_province": region_province,
        "region_city": region_city,
        "district_name": district_name,
    }


def load_system_prompt() -> str:
    path = PROMPTS_DIR / "당_부합_점검_시스템.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "당 정책 전문가로서 출마자 공약을 정강·정책(이념·취지)과 우리당 공약 기준으로만 평가하세요."


def load_user_prompt_template() -> str:
    path = PROMPTS_DIR / "당_부합_점검_유저.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return (
        "[정강·정책]\n{{PLATFORM_CONTEXT}}\n\n[우리당 공약]\n{{PLEDGES_CONTEXT}}\n\n"
        "출마자 공약:\n{{PLEDGE}}\n\n위 형식으로 부합 여부, 근거, 체크리스트를 답변하세요."
    )


def build_user_message(
    platform_context: str,
    pledges_context: str,
    pledge: str,
    winners2022_pledges_context: str = "",
    candidates_pledges_context: str = "",
    election_type: str = "",
    region_level: str = "",
    region_province: str = "",
    region_city: str = "",
    district_name: str = "",
    user_meta: Optional[dict] = None,
) -> str:
    """user_meta가 있으면 그 값으로 메타필드를 채운다(개별 인자보다 우선)."""
    if user_meta:
        election_type = user_meta.get("election_type") or election_type
        region_level = user_meta.get("region_level") or region_level
        region_province = user_meta.get("region_province") or region_province
        region_city = user_meta.get("region_city") or region_city
        district_name = user_meta.get("district_name") or district_name
    template = load_user_prompt_template()
    platform = platform_context.strip() or "(정강·정책 문서 없음. data/pdf/정강정책/ 폴더에 PDF를 넣어 주세요.)"
    pledges = pledges_context.strip() or "(우리당 공약 문서 없음. data/pdf/공약/ 폴더에 PDF를 넣어 주세요.)"
    winners2022 = (winners2022_pledges_context or "").strip() or "(2022 당선인 공약 문서 없음)"
    candidates_raw = (candidates_pledges_context or "").strip()
    candidates = candidates_raw or "(등록된 출마자 공약 없음)"
    out = (
        template.replace("{{PLATFORM_CONTEXT}}", platform)
        .replace("{{PLEDGES_CONTEXT}}", pledges)
        .replace("{{WINNERS2022_PLEDGES_CONTEXT}}", winners2022)
        .replace("{{CANDIDATES_PLEDGES_CONTEXT}}", candidates)
        .replace("{{PLEDGE}}", pledge)
        .replace("{{ELECTION_TYPE}}", election_type or "")
        .replace("{{REGION_LEVEL}}", region_level or "")
        .replace("{{REGION_PROVINCE}}", region_province or "")
        .replace("{{REGION_CITY}}", region_city or "")
        .replace("{{DISTRICT_NAME}}", district_name or "")
        # 구버전 템플릿 호환
        .replace("{{REGION_NAME}}", " ".join([x for x in [region_province, region_city] if x]).strip())
    )
    if not candidates_raw:
        out += "\n\n【필수】 [등록된 출마자 공약] 문서가 없음. '4. 등록된 타 출마자 공약과의 비교'에서는 반드시 '유사·참고 공약: 없음'만 표기."
    return out

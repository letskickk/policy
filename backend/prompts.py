"""Prompt loading and pledge-check message construction."""

from pathlib import Path
from typing import Optional

from backend.config import PROMPTS_DIR
from backend.openai_vector_store import ELECTION_TYPE_KEY_TO_LABEL

ELECTION_POSITION_TO_TYPE = dict(ELECTION_TYPE_KEY_TO_LABEL)
ELECTION_POSITION_TO_LEVEL = {
    "metro_mayor": "광역",
    "regional_council": "광역",
    "local_mayor": "기초",
    "local_council": "기초",
    "education": "광역",
}

CHECK_SYSTEM_PROMPT_FILENAME = "당_부합_점검_시스템.txt"
CHECK_USER_PROMPT_FILENAME = "당_부합_점검_유저.txt"


DEFAULT_CHECK_SYSTEM_PROMPT = """당 부합 점검 전문가로서 아래 규칙만 따르세요.

반드시 일반 텍스트로만 답변하고, 마크다운 문법(#, **, 표)을 쓰지 마세요.
반드시 아래 6개 섹션 제목을 정확히 같은 순서로 출력하세요.
1. 개혁신당 정강정책과의 부합성
2. 개혁신당 중앙당 공약과의 유사성
3. 제8회 전국동시지방선거 당선인 공약과의 비교
4. 타 후보 및 출마자 공약 비교
5. 총평
6. 수정·보완 제안

각 비교 섹션에서는 반드시 `결과:`, `강점:`, `보완 핵심:` 라벨을 그대로 쓰세요.
5번 총평에서는 반드시 아래 다섯 줄을 모두 포함하세요.
정강정책 정합성(0-20):
정책 설계 완성도(0-30):
실현 가능성(0-20):
구체성(0-15):
전달력(0-15):

그 다음 반드시 `종합 점수:`와 `종합해석 등급:`을 출력하세요.
종합해석 등급은 S, A+, A, B+, B, C+, C, D+, D, F 중 하나만 쓰세요.
섹션 6은 반드시 `- [제안 제목] 설명` 형식의 항목을 3개 이상 쓰세요.

문서에 없는 내용을 지어내지 말고, 비교 문서에 정보가 부족하면 `결과: 없음` 또는 정보 부족으로 명확히 적으세요.
중앙당 공약 유사성 섹션에서 순수 슬로건형 공약은 `결과: 없음`으로 처리하세요.
권한 밖 공약이면 실현 가능성과 종합 등급을 보수적으로 평가하고, 왜 권한 범위를 벗어나는지 짧게 설명하세요.

섹션별 참조 문서 규칙 (반드시 준수):
1번 섹션: [정강정책 문서]만 참고. [중앙당 공약] 내용을 인용하지 마세요.
2번 섹션: [중앙당 공약]만 참고. [정강정책 문서]의 이념·방향을 인용하지 마세요. 해당 공약과 직접 대응되는 구체적 공약 항목이 있으면 제목이나 내용을 명시하세요."""


DEFAULT_CHECK_USER_PROMPT = """다음 자료만 근거로 평가하세요.

[정강정책 문서]
{{PLATFORM_CONTEXT}}

[중앙당 공약]
{{PLEDGES_CONTEXT}}

[제8회 전국동시지방선거 당선인 공약]
{{WINNERS2022_PLEDGES_CONTEXT}}

[타 후보 및 출마자 공약]
{{CANDIDATES_PLEDGES_CONTEXT}}

[추가 참고 자료 - 공식 메시지]
{{MESSAGES_CONTEXT}}

[추가 참고 자료 - 지방의회]
{{ASSEMBLY_CONTEXT}}

[추가 참고 자료 - 공공데이터]
{{PUBLIC_DATA_CONTEXT}}

[추가 참고 자료 - 리서치]
{{RESEARCH_CONTEXT}}

[출마자 정보]
선거유형: {{ELECTION_TYPE}}
지역수준: {{REGION_LEVEL}}
지역: {{REGION_PROVINCE}} {{REGION_CITY}} {{DISTRICT_NAME}}

[출마 공약]
{{PLEDGE}}

출력 규칙:
1. 반드시 6개 섹션 제목을 아래와 똑같이 사용하세요.
1. 개혁신당 정강정책과의 부합성
2. 개혁신당 중앙당 공약과의 유사성
3. 제8회 전국동시지방선거 당선인 공약과의 비교
4. 타 후보 및 출마자 공약 비교
5. 총평
6. 수정·보완 제안

2. 1~4번 섹션에서는 각각 `결과:`, `강점:`, `보완 핵심:` 세 줄을 반드시 넣으세요.
3. 2번 섹션에서 유사 공약이 없으면 `결과: 없음`만 쓰고, 과장된 유사 판정을 하지 마세요.
4. 3번과 4번 섹션은 비교 가능한 공약이 없으면 `결과: 없음`으로 적고, 강점과 보완 핵심은 한두 문장으로 짧게 정리하세요.
5. 5번 섹션에서는 아래 다섯 평가축을 모두 숫자로 채점하세요.
정강정책 정합성(0-20):
정책 설계 완성도(0-30):
실현 가능성(0-20):
구체성(0-15):
전달력(0-15):
6. 종합 점수는 다섯 축의 합계와 반드시 같게 쓰세요.
7. 종합해석 등급은 총점을 바탕으로 보수적으로 정하세요.
8. 구호만 있는 짧은 공약이면 총점 45 이하, 등급 C 이하로 제한하세요.
9. 권한 밖 공약이면 총점과 실현 가능성을 보수적으로 낮추고 이유를 적으세요.
10. 6번 섹션은 `- [제안 제목]`으로 시작하는 항목을 3개 이상 작성하세요.
11. 출력은 일반 텍스트만 사용하세요."""


def _read_prompt_file(filename: str, default: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8-sig").strip()
    return default


def build_pledge_meta_from_user(user: Optional[dict]) -> dict:
    if not user:
        return {
            "election_type": "",
            "region_level": "",
            "region_province": "",
            "region_city": "",
            "district_name": "",
        }

    election_position = (user.get("election_position") or "").strip().lower()
    election_type = ELECTION_POSITION_TO_TYPE.get(election_position, election_position or "")
    region_level = ELECTION_POSITION_TO_LEVEL.get(election_position, "")
    region_province = (user.get("region_name") or "").strip()
    district_full = (user.get("district_name") or "").strip()

    if " " in district_full:
        region_city, district_name = district_full.split(" ", 1)
        region_city = region_city.strip()
        district_name = district_name.strip()
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
    return _read_prompt_file(CHECK_SYSTEM_PROMPT_FILENAME, DEFAULT_CHECK_SYSTEM_PROMPT)


def load_user_prompt_template() -> str:
    return _read_prompt_file(CHECK_USER_PROMPT_FILENAME, DEFAULT_CHECK_USER_PROMPT)


def build_user_message(
    platform_context: str,
    pledges_context: str,
    pledge: str,
    winners2022_pledges_context: str = "",
    candidates_pledges_context: str = "",
    messages_context: str = "",
    assembly_context: str = "",
    public_data_context: str = "",
    research_context: str = "",
    election_type: str = "",
    region_level: str = "",
    region_province: str = "",
    region_city: str = "",
    district_name: str = "",
    user_meta: Optional[dict] = None,
) -> str:
    if user_meta:
        election_type = user_meta.get("election_type") or election_type
        region_level = user_meta.get("region_level") or region_level
        region_province = user_meta.get("region_province") or region_province
        region_city = user_meta.get("region_city") or region_city
        district_name = user_meta.get("district_name") or district_name

    template = load_user_prompt_template()
    replacements = {
        "{{PLATFORM_CONTEXT}}": platform_context.strip() or "(정강정책 문서 없음)",
        "{{PLEDGES_CONTEXT}}": pledges_context.strip() or "(중앙당 공약 문서 없음)",
        "{{WINNERS2022_PLEDGES_CONTEXT}}": (winners2022_pledges_context or "").strip() or "(제8회 전국동시지방선거 당선인 공약 문서 없음)",
        "{{CANDIDATES_PLEDGES_CONTEXT}}": (candidates_pledges_context or "").strip() or "(타 후보 및 출마자 공약 문서 없음)",
        "{{MESSAGES_CONTEXT}}": (messages_context or "").strip() or "(공식 메시지 자료 없음)",
        "{{ASSEMBLY_CONTEXT}}": (assembly_context or "").strip() or "(지방의회 자료 없음)",
        "{{PUBLIC_DATA_CONTEXT}}": (public_data_context or "").strip() or "(공공데이터 자료 없음)",
        "{{RESEARCH_CONTEXT}}": (research_context or "").strip() or "(리서치 자료 없음)",
        "{{PLEDGE}}": pledge,
        "{{ELECTION_TYPE}}": election_type or "",
        "{{REGION_LEVEL}}": region_level or "",
        "{{REGION_PROVINCE}}": region_province or "",
        "{{REGION_CITY}}": region_city or "",
        "{{DISTRICT_NAME}}": district_name or "",
        "{{REGION_NAME}}": " ".join(part for part in [region_province, region_city] if part).strip(),
    }

    message = template
    for placeholder, value in replacements.items():
        message = message.replace(placeholder, value)
    return message

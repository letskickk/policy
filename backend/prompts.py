"""
당 부합 점검용 프롬프트 로드 및 치환.
정강·정책(이념·취지) 컨텍스트, 우리당 공약 컨텍스트, 타지역 공약 컨텍스트를 구분해 넣는다.
"""
from pathlib import Path

from backend.config import PROMPTS_DIR


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
        "[타지역 공약]\n{{REGIONAL_PLEDGES_CONTEXT}}\n\n"
        "출마자 공약:\n{{PLEDGE}}\n\n위 형식으로 부합 여부, 근거, 체크리스트를 답변하세요."
    )


def build_user_message(platform_context: str, pledges_context: str, regional_pledges_context: str, pledge: str) -> str:
    template = load_user_prompt_template()
    platform = platform_context.strip() or "(정강·정책 문서 없음. data/pdf/정강정책/ 폴더에 PDF를 넣어 주세요.)"
    pledges = pledges_context.strip() or "(우리당 공약 문서 없음. data/pdf/공약/ 폴더에 PDF를 넣어 주세요.)"
    regional_raw = regional_pledges_context.strip()
    regional = regional_raw or "(타지역 공약 문서 없음. data/pdf/지역별 공약/ 폴더에 PDF를 넣어 주세요.)"
    out = (
        template.replace("{{PLATFORM_CONTEXT}}", platform)
        .replace("{{PLEDGES_CONTEXT}}", pledges)
        .replace("{{REGIONAL_PLEDGES_CONTEXT}}", regional)
        .replace("{{PLEDGE}}", pledge)
    )
    # 지역별 공약 문서가 없으면 타지역 유사성은 반드시 '없음'. 우리당 공약을 타지역으로 착각하지 말 것.
    if not regional_raw:
        out += "\n\n【필수】 [타지역 공약] 문서가 없음. '3. 타지역 공약과 유사성'에서는 반드시 '유사 공약: 없음', '유사성 분석: 없음'만 표기. [우리당 공약] 내용을 타지역으로 착각해 넣지 말 것."
    return out

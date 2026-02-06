"""
당 부합 점검: PDF 기준 문서 + GPT API 호출.
"""
from openai import OpenAI

from backend.config import OPENAI_API_KEY, OPENAI_MODEL
from backend.pdf_loader import load_platform_context, load_pledges_context
from backend.prompts import build_user_message, load_system_prompt


def check_pledge_alignment(pledge: str) -> str:
    """
    출마자 공약(pledge)에 대해:
    1) 정강·정책 문서와 대조해 이념·취지 부합 여부를 판단하고,
    2) 우리당 공약과 비교해 취지에 맞는지 판단한 결과를 GPT로 생성해 반환한다.
    """
    if not OPENAI_API_KEY:
        return "오류: OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    platform_context = load_platform_context()
    pledges_context = load_pledges_context()
    if not platform_context.strip() and not pledges_context.strip():
        return "오류: 기준 문서가 없습니다. data/pdf/ 에 정강·정책 PDF와 공약 PDF를 넣어 주세요."

    system = load_system_prompt()
    user = build_user_message(platform_context, pledges_context, pledge)

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""

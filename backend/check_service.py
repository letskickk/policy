"""
당 부합 점검: PDF 기준 문서 + GPT API 호출.
"""
import logging

from openai import OpenAI

from backend.config import OPENAI_API_KEY, OPENAI_MODEL
from backend.pdf_loader import load_platform_context, load_pledges_context
from backend.prompts import build_user_message, load_system_prompt

logger = logging.getLogger(__name__)


def check_pledge_alignment(pledge: str) -> str:
    """
    출마자 공약(pledge)에 대해:
    1) 정강·정책 문서와 대조해 이념·취지 부합 여부를 판단하고,
    2) 우리당 공약과 비교해 취지에 맞는지 판단한 결과를 GPT로 생성해 반환한다.
    """
    if not OPENAI_API_KEY:
        return "오류: OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    logger.info("PDF 컨텍스트 로드 시작...")
    platform_context = load_platform_context()
    pledges_context = load_pledges_context()
    
    logger.info(f"정강정책 컨텍스트 길이: {len(platform_context)}자")
    logger.info(f"공약 컨텍스트 길이: {len(pledges_context)}자")
    
    if not platform_context.strip() and not pledges_context.strip():
        return "오류: 기준 문서가 없습니다. data/pdf/ 에 정강·정책 PDF와 공약 PDF를 넣어 주세요."

    if not pledges_context.strip():
        logger.warning("공약 컨텍스트가 비어있습니다. GPT가 공약 비교를 제대로 할 수 없습니다.")
    
    system = load_system_prompt()
    user = build_user_message(platform_context, pledges_context, pledge)
    
    # 디버깅: 컨텍스트가 제대로 전달되는지 확인
    logger.info(f"시스템 프롬프트 길이: {len(system)}자")
    logger.info(f"사용자 메시지 길이: {len(user)}자")
    logger.info(f"공약 컨텍스트 길이: {len(pledges_context)}자")
    logger.info(f"공약 컨텍스트 미리보기: {pledges_context[:500]}...")
    
    # 공약 컨텍스트에 실제 내용이 있는지 확인
    if pledges_context.strip():
        # 공약 컨텍스트에서 "---" 구분자로 파일 개수 확인
        file_count = pledges_context.count("---")
        logger.info(f"공약 컨텍스트에 {file_count}개 파일이 포함됨")
        
        # 실제 텍스트가 있는지 확인 (파일명 제외)
        text_only = pledges_context.replace("---", "").strip()
        if len(text_only) < 100:
            logger.warning(f"공약 컨텍스트의 실제 텍스트가 너무 짧음: {len(text_only)}자")
    else:
        logger.error("공약 컨텍스트가 완전히 비어있습니다!")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    result = response.choices[0].message.content or ""
    logger.info(f"GPT 응답 길이: {len(result)}자")
    return result

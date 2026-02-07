"""
당 부합 점검: PDF 기준 문서 + GPT API 호출.
"""
import logging
import re

from openai import OpenAI

from backend.config import OPENAI_API_KEY, OPENAI_MODEL
from backend.pdf_loader import load_platform_context, load_pledges_context, load_regional_pledges_context
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
    regional_pledges_context = load_regional_pledges_context()
    
    logger.info(f"정강정책 컨텍스트 길이: {len(platform_context)}자")
    logger.info(f"공약 컨텍스트 길이: {len(pledges_context)}자")
    logger.info(f"지역별 공약 컨텍스트 길이: {len(regional_pledges_context)}자")
    
    if not platform_context.strip() and not pledges_context.strip():
        return "오류: 기준 문서가 없습니다. data/pdf/정강정책/ 와 data/pdf/공약/ 폴더에 PDF를 넣어 주세요."

    if not pledges_context.strip():
        logger.warning("공약 컨텍스트가 비어있습니다. GPT가 공약 비교를 제대로 할 수 없습니다.")
    
    system = load_system_prompt()
    user = build_user_message(platform_context, pledges_context, regional_pledges_context, pledge)
    
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

    # 섹션 2 제목·결과 형식 보정 (GPT가 구 형식을 내는 경우 대비)
    result = result.replace("2. 개혁신당 공약과의 비교", "2. 개혁신당 중앙당 공약과의 유사성")
    # 섹션 2 "결과: 적합/부분적 적합/부적합 (XX점)" → "결과: 유사도(XX점)"
    # "2. 개혁신당" 이후 첫 "결과:"를 찾아 변환 (섹션 2 블록)
    _idx = result.find("2. 개혁신당")
    if _idx != -1:
        _rest = result[_idx:]
        _m = re.search(r"결과:\s*(?:적합|부분적 적합|부적합)\s*\((\d{1,3})점\)", _rest)
        if _m:
            _start = _idx + _m.start()
            _end = _idx + _m.end()
            result = result[:_start] + f"결과: 유사도({_m.group(1)}점)" + result[_end:]

    # 지역별 공약 폴더에 파일이 없으면 타지역 유사성은 무조건 '없음'으로 서버 보정
    if not regional_pledges_context.strip():
        result = result.replace("유사 공약: 있음", "유사 공약: 없음")
        result = re.sub(r"유사성 분석:\s*[^\n]+", "유사성 분석: 없음", result, count=1)

    # 명칭·제목만 제시된 경우(80자 미만): 점수·유사·중복 설명 보정
    if len(pledge.strip()) < 80:
        # "2. 개혁신당 중앙당 공약과의 유사성" 섹션: 95점 이상이면 50점으로 보정
        result = re.sub(
            r"(2\. 개혁신당 중앙당 공약과의 유사성\s*\n결과:\s*유사도\s*\()(\d{2,3})(점\))",
            lambda m: m.group(1) + ("50" if int(m.group(2)) >= 95 else m.group(2)) + m.group(3),
            result,
            count=1,
        )
        # "90% 이상", "거의 동일", "사실상 동일" 등 잘못된 표현 → 구체적 지적으로 치환
        _fix = "제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 우리당 공약의 구체적 방안을 참고해 보완 필요."
        result = re.sub(r"일치율은\s*90% 이상[^.]*판단한다?", f"{_fix}", result)
        result = re.sub(r"90% 이상\s*\(거의 동일\)", _fix, result)
        result = result.replace("거의 동일", "구체적 방안 부족")
        result = result.replace("사실상 동일한", "명칭만 동일한")
        result = result.replace("동일하여", "명칭은 같으나")
        result = result.replace("동일로 판단", "구체성 부족으로 판단")
    return result

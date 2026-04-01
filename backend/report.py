"""
검색 결과를 기반으로 LLM 리포트를 생성하는 모듈.
"""
import json
import logging
import re
from typing import Dict, List, Tuple

from openai import OpenAI

from backend.chunking import DocChunk, normalize_text
from backend.config import CHAT_MODEL
from backend.embeddings import embed_texts, get_openai_client
from backend.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# exact/fuzzy match용 (선택)
try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    rapidfuzz_fuzz = None

# exact-match에서 fuzzy로 인정할 최소 부분 유사도 (0~100)
EXACT_FUZZY_THRESHOLD = 85
EXACT_TOP_K = 3
SPECIFIC_ACTION_KEYWORDS = (
    "설치", "도입", "확대", "신설", "운영", "지원", "조성", "건립",
    "개편", "추진", "배치", "확충", "정비", "개소", "확보",
)
SPECIFIC_TARGET_KEYWORDS = (
    "센터", "시설", "돌봄", "주간보호", "장애인", "청년", "노인",
    "소상공인", "가구", "학생", "주민", "교통", "주택", "보건",
)
AUTHORITY_MISMATCH_KEYWORDS = (
    "fta", "교육부", "검찰", "국방부", "외교부", "통일부", "대통령",
    "국회", "헌법", "관세", "병역", "외교", "국방", "통상",
)


def _chunk_key(chunk: DocChunk) -> tuple:
    return (chunk.doc_id, chunk.chunk_id)


def _compute_input_signal_adjustment(user_pledge: str) -> float:
    """Reward concrete pledges and penalize slogans or authority mismatches."""
    text = re.sub(r"\s+", " ", (user_pledge or "").strip())
    if not text:
        return 0.0

    lowered = text.lower()
    char_len = len(text)
    token_count = len(text.split())
    number_hits = len(re.findall(r"\d+", text))
    unit_hits = len(re.findall(r"(?|??|?|%|??|??|??|?)", text))
    action_hits = sum(1 for kw in SPECIFIC_ACTION_KEYWORDS if kw in text)
    target_hits = sum(1 for kw in SPECIFIC_TARGET_KEYWORDS if kw in text)
    mismatch_hits = sum(1 for kw in AUTHORITY_MISMATCH_KEYWORDS if kw in lowered)

    specificity_bonus = 0.0
    if char_len >= 100:
        specificity_bonus += 3.0
    elif char_len >= 70:
        specificity_bonus += 2.0
    elif char_len >= 50:
        specificity_bonus += 1.5
    specificity_bonus += min(number_hits, 2) * 3.0
    specificity_bonus += min(unit_hits, 2) * 2.0
    specificity_bonus += min(action_hits, 3) * 2.0
    specificity_bonus += min(target_hits, 2) * 1.5
    if number_hits >= 1 and action_hits >= 1 and target_hits >= 1:
        specificity_bonus += 6.0
    specificity_bonus = min(specificity_bonus, 20.0)

    slogan_penalty = 0.0
    if char_len < 25:
        slogan_penalty += 20.0
    elif char_len < 50:
        slogan_penalty += 10.0
    if token_count <= 4:
        slogan_penalty += 8.0
    if number_hits == 0 and action_hits <= 1:
        slogan_penalty += 8.0
    if "!" in text or lowered.endswith("?!") or lowered.endswith("???"):
        slogan_penalty += 6.0

    authority_penalty = min(24.0, mismatch_hits * 12.0)
    if mismatch_hits and action_hits == 0:
        authority_penalty += 4.0

    return specificity_bonus - slogan_penalty - authority_penalty

def exact_match_search(
    query_text: str,
    index: VectorIndex,
    top_k_exact: int = EXACT_TOP_K,
) -> List[Tuple[DocChunk, float]]:
    """
    정규화된 exact substring 일치 청크를 전부 먼저 넣고, 부족분만 fuzzy로 채운다.
    문서 복붙 시 해당 청크가 무조건 HIT 되도록 embedding 결과 앞에 강제 포함된다.
    """
    if not query_text or not index.chunks:
        return []
    norm_q = normalize_text(query_text)
    if len(norm_q) < 5:
        return []

    exact_hits: List[Tuple[DocChunk, float]] = []
    fuzzy_hits: List[Tuple[DocChunk, float]] = []
    for chunk in index.chunks:
        norm_c = normalize_text(chunk.text)
        if norm_q in norm_c:
            exact_hits.append((chunk, 1.0))
            continue
        if HAS_RAPIDFUZZ and len(norm_q) >= 10:
            ratio = rapidfuzz_fuzz.partial_ratio(norm_q, norm_c)
            if ratio >= EXACT_FUZZY_THRESHOLD:
                fuzzy_hits.append((chunk, ratio / 100.0))
    fuzzy_hits.sort(key=lambda x: -x[1])
    # exact 전부 포함 후, fuzzy로 top_k_exact까지 채움
    combined = exact_hits + fuzzy_hits[: max(0, top_k_exact - len(exact_hits))]
    return combined


def _merge_exact_and_embedding(
    exact_hits: List[Tuple[DocChunk, float]],
    embedding_hits: List[Tuple[DocChunk, float]],
    top_k: int,
) -> List[Tuple[DocChunk, float]]:
    """exact 우선, 그 다음 embedding 순으로 중복 제거하여 최대 top_k 반환."""
    seen = set()
    result: List[Tuple[DocChunk, float]] = []
    for chunk, score in exact_hits + embedding_hits:
        key = _chunk_key(chunk)
        if key in seen:
            continue
        seen.add(key)
        result.append((chunk, score))
        if len(result) >= top_k:
            break
    return result[:top_k]


def truncate_quote(text: str, max_length: int = 250) -> str:
    """
    인용문을 최대 길이로 자른다.
    
    Args:
        text: 원본 텍스트
        max_length: 최대 길이
    
    Returns:
        잘린 텍스트
    """
    if len(text) <= max_length:
        return text
    # 문장 경계에서 자르기 시도
    truncated = text[:max_length]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')
    cut_pos = max(last_period, last_newline)
    if cut_pos > max_length * 0.7:  # 너무 앞에서 자르지 않도록
        return truncated[:cut_pos + 1] + "..."
    return truncated + "..."


def search_all_indexes(
    query_text: str,
    platform_index: VectorIndex,
    pledge_index: VectorIndex,
    regional_index: VectorIndex,
    top_k_platform: int = 6,
    top_k_pledge: int = 6,
    top_k_regional: int = 8,
) -> Dict[str, List[Tuple[DocChunk, float]]]:
    """
    모든 인덱스에서 하이브리드 검색: exact/fuzzy 매치를 먼저 찾고, 그 다음 임베딩 검색.
    threshold로 결과를 버리지 않으며, 항상 top_k 후보를 반환한다.
    """
    query_embeddings = embed_texts([query_text], batch_size=1)
    if not query_embeddings:
        logger.error("쿼리 임베딩 생성 실패")
        return {"platform": [], "pledge": [], "regional": []}

    query_embedding = query_embeddings[0]

    def hybrid_search(index: VectorIndex, top_k: int) -> List[Tuple[DocChunk, float]]:
        # exact substring match(정규화 포함)를 먼저 수행하고, exact hit을 검색 결과 맨 앞에 강제 포함 (문서 복붙 시 해당 청크 필수 포함)
        exact = exact_match_search(query_text, index, top_k_exact=EXACT_TOP_K)
        emb = index.search(query_embedding, k=top_k)
        return _merge_exact_and_embedding(exact, emb, top_k)

    platform_hits = hybrid_search(platform_index, top_k_platform)
    pledge_hits = hybrid_search(pledge_index, top_k_pledge)
    regional_hits = hybrid_search(regional_index, top_k_regional)

    logger.info(
        f"검색 완료: 정강정책 {len(platform_hits)}개, 공약 {len(pledge_hits)}개, 지역별 {len(regional_hits)}개"
    )

    return {
        "platform": platform_hits,
        "pledge": pledge_hits,
        "regional": regional_hits,
    }


def _snippet_texts_for_evidence_ids(evidence_ids: List[str], evidence_map: Dict[str, Dict]) -> str:
    """evidence ID 목록에 해당하는 스니펫 텍스트를 합쳐 반환 (검증용)."""
    parts = []
    for eid in evidence_ids or []:
        if eid in evidence_map and evidence_map[eid].get("snippet"):
            parts.append(evidence_map[eid]["snippet"])
    return " ".join(parts)


def _validate_and_sanitize_rubric(rubric: Dict, evidence_map: Dict[str, Dict]) -> None:
    """
    rubric 항목의 note/quote가 evidence_map 스니펫의 substring인지 검증한다.
    모든 quote는 반드시 해당 evidence snippet의 substring이어야 하며, 서버에서 검증·보정한다.
    정강·공약은 전체 문서 기반이므로 evidence=[]인 항목은 검증 생략.
    """
    import re
    for category in ("platform", "pledges", "conflicts"):
        items = rubric.get(category, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ev_ids = item.get("evidence") or []
            # 정강·공약은 전체 문서 기반 판단으로 evidence=[] 가능 → 검증 생략
            if category in ("platform", "pledges") and not ev_ids:
                continue
            allowed_text = _snippet_texts_for_evidence_ids(ev_ids, evidence_map)
            note = (item.get("note") or "").strip()
            if note:
                quoted = re.findall(r'"([^"]{20,})"', note)
                for q in quoted:
                    if q not in allowed_text and normalize_text(q) not in normalize_text(allowed_text):
                        item["note"] = note.replace(f'"{q}"', "[근거 부족]")
                        note = item["note"]
            if "quote" in item:
                q = item["quote"]
                if q and allowed_text and q not in allowed_text and normalize_text(q) not in normalize_text(allowed_text):
                    del item["quote"]
                    item["note"] = (item.get("note") or "") + " [근거 부족]"
                    note = item["note"]


def _sanitize_improvements_quotes(improvements: List[Dict], evidence_map: Dict[str, Dict]) -> None:
    """improvements 항목의 detail 내 인용이 evidence snippet의 substring인지 검증·보정."""
    import re
    for imp in improvements or []:
        if not isinstance(imp, dict):
            continue
        ev_ids = imp.get("evidence") or []
        allowed_text = _snippet_texts_for_evidence_ids(ev_ids, evidence_map)
        detail = (imp.get("detail") or "").strip()
        if not detail or not allowed_text:
            continue
        quoted = re.findall(r'"([^"]{20,})"', detail)
        for q in quoted:
            if q not in allowed_text and normalize_text(q) not in normalize_text(allowed_text):
                imp["detail"] = detail.replace(f'"{q}"', "[근거 부족]")
                detail = imp["detail"]


def _sanitize_no_duplicate_claim(
    rubric: Dict, improvements: List[Dict], regional_hits_count: int
) -> None:
    """
    "유사·중복 공약: 없음"은 타지역 검색 결과가 0건일 때만 허용.
    검색 결과가 1건 이상이면 "관련도 낮음/근거 약함"으로 서버에서 치환한다.
    """
    if regional_hits_count == 0:
        return
    replacement = "관련도 낮음/근거 약함"
    phrase = "유사·중복 공약: 없음"
    for category in ("platform", "pledges", "conflicts"):
        for item in (rubric.get(category) or []):
            if not isinstance(item, dict):
                continue
            note = item.get("note") or ""
            if phrase in note:
                item["note"] = note.replace(phrase, replacement)
    for imp in (improvements or []):
        if not isinstance(imp, dict):
            continue
        detail = imp.get("detail") or ""
        if phrase in detail:
            imp["detail"] = detail.replace(phrase, replacement)


# 공약/지역별 혼동 방지: source별 폴더명·한글 라벨 (출력/프롬프트에 항상 명시)
SOURCE_FOLDER = {"platform": "정강정책", "pledge": "공약", "regional": "지역별 공약"}
SOURCE_LABEL_KR = {"platform": "정강정책", "pledge": "우리당 공약", "regional": "타지역 공약"}


def _evidence_path(source: str, chunk_path: str) -> str:
    """출력용 path: 폴더명을 항상 앞에 붙여 공약/지역별 공약이 뒤바뀌어 보이지 않도록 한다."""
    folder = SOURCE_FOLDER.get(source, "")
    if not folder or (chunk_path.startswith(folder + "/") or chunk_path.startswith(folder + "\\")):
        return chunk_path
    return f"{folder}/{chunk_path}"


def build_evidence_map(
    platform_hits: List[Tuple[DocChunk, float]],
    pledge_hits: List[Tuple[DocChunk, float]],
    regional_hits: List[Tuple[DocChunk, float]],
) -> Dict[str, Dict]:
    """
    검색 결과를 evidence_map 구조로 변환한다.
    path에는 항상 폴더명(정강정책/공약/지역별 공약)을 포함해 공약·지역별이 뒤바뀌어 보이지 않도록 한다.

    Evidence ID 규칙:
    - P1, P2, ... : platform (정강정책)
    - G1, G2, ... : pledge (우리당 공약)
    - R1, R2, ... : regional (지역별 공약)
    """
    evidence_map: Dict[str, Dict] = {}

    for i, (chunk, score) in enumerate(platform_hits, start=1):
        evid_id = f"P{i}"
        src = "platform"
        evidence_map[evid_id] = {
            "source": src,
            "source_label_kr": SOURCE_LABEL_KR[src],
            "path": _evidence_path(src, chunk.path),
            "chunk_id": chunk.chunk_id,
            "snippet": truncate_quote(chunk.text, max_length=250),
            "score": score,
        }

    for i, (chunk, score) in enumerate(pledge_hits, start=1):
        evid_id = f"G{i}"
        src = "pledge"
        evidence_map[evid_id] = {
            "source": src,
            "source_label_kr": SOURCE_LABEL_KR[src],
            "path": _evidence_path(src, chunk.path),
            "chunk_id": chunk.chunk_id,
            "snippet": truncate_quote(chunk.text, max_length=250),
            "score": score,
        }

    for i, (chunk, score) in enumerate(regional_hits, start=1):
        evid_id = f"R{i}"
        src = "regional"
        evidence_map[evid_id] = {
            "source": src,
            "source_label_kr": SOURCE_LABEL_KR[src],
            "path": _evidence_path(src, chunk.path),
            "chunk_id": chunk.chunk_id,
            "snippet": truncate_quote(chunk.text, max_length=250),
            "score": score,
        }

    return evidence_map


def build_rubric_prompt(
    user_pledge: str,
    evidence_map: Dict[str, Dict],
    platform_full_text: str = "",
    pledges_full_text: str = "",
    platform_cards: List[Dict] = None,
    pledge_cards: List[Dict] = None,
) -> str:
    """
    LLM이 rubric + score_0_5 + evidence ID 배열만 생성하도록 하는 프롬프트.
    정강정책·공약은 전체 문서(platform_full_text, pledges_full_text)를 학습·파악한 뒤 적합도 판단.
    타지역 공약은 Evidence 목록만 참고.
    """
    # 타지역(R) Evidence만 텍스트로. 한글 라벨(정강정책/우리당 공약/타지역 공약)을 명시해 공약·지역별이 뒤바뀌지 않도록 함.
    evid_lines = []
    for evid_id, info in evidence_map.items():
        label = info.get("source_label_kr") or SOURCE_LABEL_KR.get(info.get("source", ""), info.get("source", ""))
        evid_lines.append(
            f"[{evid_id}] ({label} | {info['path']} | chunk {info['chunk_id']})\n{info['snippet']}"
        )
    evidence_block = "\n\n".join(evid_lines) if evid_lines else "(제공된 Evidence 없음)"

    platform_block = (platform_full_text or "(정강정책 문서 없음)").strip()
    pledges_block = (pledges_full_text or "(공약 문서 없음)").strip()
    platform_cards = platform_cards or []
    pledge_cards = pledge_cards or []

    cards_instruction = ""
    if platform_cards or pledge_cards:
        cards_instruction = """
[구조화 카드] 아래 원칙 카드/공약 카드는 문서를 GPT가 요약·구조화한 것이다. 채점 시 이 카드(근거 chunk_ids 포함)와 위 원문을 함께 참고하여 점수화한다.
"""
    platform_cards_block = ""
    if platform_cards:
        platform_cards_block = "\n[원칙 카드]\n" + json.dumps(platform_cards, ensure_ascii=False, indent=2)
    pledge_cards_block = ""
    if pledge_cards:
        pledge_cards_block = "\n[공약 카드]\n" + json.dumps(pledge_cards, ensure_ascii=False, indent=2)

    prompt = f"""너는 정책 정합성 채점관이다.

[채점 방식]
- **정강정책·공약**: 아래 [정강정책 전체]와 [공약 전체]를 **전체 읽고 이해한 뒤**, 그 내용을 바탕으로 출마자 공약의 적합도를 판단한다. 검색 스니펫이 아니라 문서 전체를 학습·파악한 후 판단한다.
- **타지역 공약**: 아래 [타지역 공약 Evidence]만 참고한다. Evidence ID(R1, R2, ...) 범위 안에서만 인용한다.
{cards_instruction}

[채점 원칙]
- 문자열·단어 일치가 아니다. 핵심 이념·가치·정책 방향의 부합으로 판단한다.
- 표현이 다르더라도 이념·가치·방향이 맞으면 높은 점수, 표현이 비슷해도 가치가 어긋나면 낮은 점수를 준다.
- **제목·표제만 적은 경우 = 2점 이하 고정**: "다자녀 핑크번호판", "지역경제 활성화" 같이 명칭만 적고 구체적 방안이 없으면 2점 이하. note에는 "90% 일치", "거의 동일" 대신 → "우리당 공약은 [검색된 내용 요약: 누구에게 무엇을 어떻게 주겠다]. 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요." 형식으로 작성.
- **구체성 없으면 높은 점수 금지**: 내용이 짧거나(한 문장·한 줄 수준) 구체적 방안이 없으면 3점 이하. 4~5점은 구체적 수단·수치·이행 계획이 있을 때만.
- 출마자 공약이 우리당 공약(pledges)과 유사할 때는 정강정책(platform) 부합 점수도 그에 맞춰 소폭 높게 줄 수 있다. 단, 제목만·구체성 부족 시 위 규칙 우선.
- **모호한 방향/구체성 부족**: 방향만 제시하고 구체적 수단·수치·이행 계획이 없으면 improvements에 반드시 짚어라. 예: "지역경제 활성화"만 쓰고 어떻게 할지 없음 → "구체적 방안·수치·이행 계획 보완 필요".

[정강정책 전체]
{platform_block}
{platform_cards_block}

[공약 전체]
{pledges_block}
{pledge_cards_block}

[출마자 공약]
{user_pledge}

[타지역 공약 Evidence] (타지역 판단 시 이 목록만 사용, R1·R2·… ID만 인용)
{evidence_block}

각 rubric 항목은 0~5점으로 채점한다. (위 채점 원칙에 따라 이념·가치·방향 기준으로 판단)
- 0: 상충 또는 근거 전무
- 1~2: 대체로 부적합 / 일부만 맞음
- 3: 부분부합 (긍정/부정 요소가 섞여 있음)
- 4: 대체로 부합
- 5: 강한 부합, 매우 잘 맞음

Evidence 규칙:
- **platform·pledges 항목**: 위 [정강정책 전체]·[공약 전체]를 읽고 판단했으므로 evidence=[] 가능. note에 전체 내용 기반 판단 근거를 쓴다.
- **conflicts·타지역 관련**: evidence에는 R1, R2 등 타지역 ID만 사용. 해당 없으면 evidence=[] + note에 "근거 부족".
- **인용 규칙**: evidence snippet 밖 문장 생성 금지. 모든 quote와 note 내 따옴표 인용은 반드시 해당 evidence 스니펫의 substring이어야 하며, 서버에서 검증한다.
- "유사·중복 공약: 없음"은 타지역 검색 결과가 0건일 때만 쓸 수 있다. 1건 이상이면 "관련도 낮음/근거 약함"으로 표현.
- 유사 공약을 나열할 때는 대표 2~3건만. 모든 공약을 나열하지 말 것.
- regional Evidence가 없으면 conflicts에서 우리당 공약(P, G)을 인용하지 말 것. R1,R2 등 타지역 ID만 사용.

출력 JSON 스키마 (이 구조만 반환). platform·pledges는 위 전체 문서 기반 판단(evidence=[] 가능), conflicts·improvements는 R1,R2 등만 사용.
{{
  "confidence": 0-100,
  "rubric": {{
    "platform": [
      {{"item":"가치 정합성","score_0_5":0-5,"evidence":[],"note":"정강정책 전체 내용 기반 판단 근거"}},
      {{"item":"정책 방향 일치","score_0_5":0-5,"evidence":[],"note":"..."}},
      {{"item":"수단 적합성","score_0_5":0-5,"evidence":[],"note":"..."}},
      {{"item":"일관성","score_0_5":0-5,"evidence":[],"note":"..."}}
    ],
    "pledges": [
      {{"item":"중복/연계 가능","score_0_5":0-5,"evidence":[],"note":"공약 전체 내용 기반 판단 근거"}},
      {{"item":"차별성","score_0_5":0-5,"evidence":[],"note":"..."}},
      {{"item":"정책 언어 호환","score_0_5":0-5,"evidence":[],"note":"..."}}
    ],
    "conflicts": [
      {{"item":"명시적 상충","score_0_5":0-5,"evidence":["R1"],"note":"..."}},
      {{"item":"잠재 리스크","score_0_5":0-5,"evidence":["R2"],"note":"..."}}
    ]
  }},
  "improvements":[
    {{"title":"...","detail":"...","evidence":[]}}
  ]
}}

중요:
- fit_score, breakdown은 계산하지 않는다. rubric.score_0_5, evidence, note, confidence, improvements만 작성.
- platform·pledges의 evidence는 [] 가능. conflicts 등에서만 R1,R2 등 타지역 ID 사용.
- improvements: 구체적 방안·수치·이행 계획이 없으면 "구체성 보완 필요" 항목을 반드시 포함.
- 제목만·한 줄만 적은 경우: platform/pledges 2점 이하. note는 "우리당 공약은 [검색된 내용]. 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요." 형식으로. "90% 일치", "거의 동일" 사용 금지.
- JSON만 반환하고 다른 설명은 붙이지 마라.
"""
    return prompt


def generate_report(
    user_pledge: str,
    platform_index: VectorIndex,
    pledge_index: VectorIndex,
    regional_index: VectorIndex,
    top_k_platform: int = 6,
    top_k_pledge: int = 6,
    top_k_regional: int = 8
) -> Dict:
    """
    검색 결과를 기반으로 LLM 리포트를 생성한다.
    
    Args:
        user_pledge: 사용자 공약 텍스트
        platform_index: 정강정책 인덱스
        pledge_index: 공약 인덱스
        regional_index: 지역별 공약 인덱스
        top_k_platform: 정강정책 검색 개수
        top_k_pledge: 공약 검색 개수
        top_k_regional: 지역별 공약 검색 개수
    
    Returns:
        리포트 JSON 딕셔너리
    """
    # 인덱스 유효성 검사
    if platform_index is None or pledge_index is None or regional_index is None:
        logger.error("인덱스가 None입니다.")
        return {
            "summary": {
                "fit_score": 0,
                "fit_verdict": "오류"
            },
            "platform": [],
            "pledges": [],
            "regional_similarity": [],
            "conflicts": [],
            "improvements": [],
            "error": "인덱스가 초기화되지 않았습니다."
        }
    
    # 검색
    hits = search_all_indexes(
        user_pledge,
        platform_index,
        pledge_index,
        regional_index,
        top_k_platform,
        top_k_pledge,
        top_k_regional,
    )

    platform_hits = hits["platform"]
    pledge_hits = hits["pledge"]
    regional_hits = hits["regional"]

    # evidence_map 생성 (타지역 포함, 정강·공약 스니펫도 참고용으로 유지)
    evidence_map = build_evidence_map(platform_hits, pledge_hits, regional_hits)

    # 정강정책·공약 전체 문서 로드 (한도 없이 폴더 안 모든 PDF 전부 → GPT가 모두 학습·파악)
    from backend.config import PDF_DIR, _nfc
    from backend.pdf_loader import load_full_text_from_dir
    platform_full_text = load_full_text_from_dir(PDF_DIR / _nfc("정강정책"))
    pledges_full_text = load_full_text_from_dir(PDF_DIR / _nfc("공약"))

    # 구조화 카드(원칙 카드/공약 카드) 로드 — 있으면 검증 시 카드+원문 기반 점수화에 활용
    platform_cards: List[Dict] = []
    pledge_cards: List[Dict] = []
    try:
        from backend.cards import load_platform_cards, load_pledge_cards
        platform_cards = load_platform_cards()
        pledge_cards = load_pledge_cards()
    except Exception as e:
        logger.debug(f"카드 로드 스킵: {e}")

    # GPT API 한도 점검 (128k 토큰 ≈ 한글 기준 약 20만~25만자)
    total_doc_chars = len(platform_full_text) + len(pledges_full_text)
    prompt_overhead = 2000 + len(user_pledge) + sum(len(v.get("snippet", "")) for v in evidence_map.values())
    total_prompt_chars = total_doc_chars + prompt_overhead
    logger.info(f"[PROMPT] 정강+공약={total_doc_chars}자, 예상 전체 프롬프트≈{total_prompt_chars}자 (128k토큰 한도≈20만자)")
    if total_prompt_chars > 180000:
        logger.warning(f"[PROMPT] GPT 컨텍스트 한도(128k 토큰)에 근접할 수 있음. PDF 추가 시 한도 초과 가능.")

    # 프롬프트 생성: 정강·공약 전체 + (선택) 카드 + 타지역 Evidence
    prompt = build_rubric_prompt(
        user_pledge,
        evidence_map,
        platform_full_text=platform_full_text,
        pledges_full_text=pledges_full_text,
        platform_cards=platform_cards,
        pledge_cards=pledge_cards,
    )

    # LLM 호출 (채점/근거정리만 수행, temperature=0)
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 정책 정합성 채점관이다. "
                        "정강정책·공약은 반드시 제공된 [정강정책 전체]·[공약 전체]를 읽고 이해한 뒤 적합도를 판단한다. "
                        "타지역 공약은 Evidence(R1,R2,…) 범위 안에서만 인용한다. "
                        "각 rubric 항목에 대해 0~5점 score_0_5와 evidence 배열, note만 작성하라."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        raw = json.loads(result_text)

        logger.info("rubric 생성 완료")

        # rubric 구조 추출
        rubric = raw.get("rubric", {})
        # Evidence 밖 인용 검증: quote/note 내 인용은 evidence_map 스니펫의 substring이어야 함 (서버 검증)
        _validate_and_sanitize_rubric(rubric, evidence_map)
        confidence = int(raw.get("confidence", 0)) if isinstance(raw.get("confidence", 0), (int, float)) else 0
        improvements = raw.get("improvements", [])
        _sanitize_improvements_quotes(improvements, evidence_map)
        # "유사·중복 공약: 없음"은 타지역 검색 0건일 때만 허용, 그 외는 서버에서 "관련도 낮음/근거 약함"으로 보정
        _sanitize_no_duplicate_claim(rubric, improvements, len(regional_hits))

        # 점수 산식 적용
        def avg_score(items: List[Dict]) -> float:
            if not items:
                return 0.0
            vals = []
            for it in items:
                try:
                    vals.append(float(it.get("score_0_5", 0)))
                except Exception:
                    vals.append(0.0)
            if not vals:
                return 0.0
            return sum(vals) / len(vals)

        platform_items = rubric.get("platform", [])
        pledge_items = rubric.get("pledges", [])
        conflict_items = rubric.get("conflicts", [])

        # 제목·한 줄만 적은 경우: rubric 점수 강제 상한 (80자 미만)
        _short_input = len(user_pledge.strip()) < 80
        if _short_input:
            for item in platform_items + pledge_items:
                if isinstance(item, dict):
                    item["score_0_5"] = min(item.get("score_0_5", 0), 2)
                    n = (item.get("note") or "").strip()
                    if any(x in n for x in ("90%", "거의 동일", "사실상 동일", "동일하여", "동일로")):
                        item["note"] = "우리당 공약에 구체적 방안이 있으나, 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요."
            has_concreteness = any("구체" in str(t.get("title", "") or t.get("detail", "")) for t in improvements)
            if not has_concreteness:
                improvements = [{"title": "구체성 보완 필요", "detail": "제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 우리당 공약의 구체적 방안을 참고해 보완하세요.", "evidence": []}] + improvements

        platform_avg_0_5 = avg_score(platform_items)
        pledge_avg_0_5 = avg_score(pledge_items)
        conflict_avg_0_5 = avg_score(conflict_items)

        platform_score = max(0.0, min(100.0, platform_avg_0_5 * 20.0))
        pledge_score = max(0.0, min(100.0, pledge_avg_0_5 * 20.0))
        conflict_penalty = max(0.0, min(100.0, conflict_avg_0_5 * 20.0))

        # 유사한 공약이 있으면(pledges 점수 높을 때) 정강정책 평가를 소폭 상향
        if pledge_avg_0_5 >= 3.5:
            platform_score = min(100.0, platform_score + 5.0)

        fit_score = 0.50 * platform_score + 0.35 * pledge_score - 0.15 * conflict_penalty
        fit_score += _compute_input_signal_adjustment(user_pledge)
        fit_score = max(0.0, min(100.0, fit_score))

        # 제목·한 줄만 적은 경우: 서버 측 상한 적용
        # query-like 입력은 더 낮게 유지해 검증용 경고 신호를 분명히 한다.
        if len(user_pledge.strip()) < 80 and fit_score > 25:
            fit_score = min(fit_score, 25.0)

        # fit_verdict 간단 규칙 (원하면 나중에 조정 가능)
        if fit_score >= 80:
            fit_verdict = "부합"
        elif fit_score >= 60:
            fit_verdict = "부분부합"
        elif fit_score >= 40:
            fit_verdict = "보완필요"
        else:
            fit_verdict = "상충우려"

        report = {
            "fit_score": round(fit_score, 1),
            "confidence": max(0, min(100, confidence)),
            "breakdown": {
                "platform_score": round(platform_score, 1),
                "pledge_score": round(pledge_score, 1),
                "conflict_penalty": round(conflict_penalty, 1),
            },
            "rubric": rubric,
            "evidence_map": evidence_map,
            "improvements": improvements,
        }

        return report

    except Exception as e:
        logger.error(f"LLM 리포트 생성 실패: {e}", exc_info=True)
        # 기본 리포트 반환
        return {
            "fit_score": 0,
            "confidence": 0,
            "breakdown": {
                "platform_score": 0,
                "pledge_score": 0,
                "conflict_penalty": 0,
            },
            "rubric": {
                "platform": [],
                "pledges": [],
                "conflicts": [],
            },
            "evidence_map": {},
            "improvements": [],
            "error": str(e),
        }

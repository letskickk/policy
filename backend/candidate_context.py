"""
DB에 등록된 출마자 공약을 AI 분석 컨텍스트 텍스트로 변환한다.
GPT가 타 후보 공약을 레퍼런스로 참조·비교할 수 있도록
구조화된 텍스트 블록을 생성한다.
"""
import logging
from typing import Optional

from backend.database import get_connection

logger = logging.getLogger(__name__)

REGION_NAME_MAP = {
    "11": "서울특별시",
    "26": "부산광역시",
    "27": "대구광역시",
    "28": "인천광역시",
    "29": "광주광역시",
    "30": "대전광역시",
    "31": "울산광역시",
    "36": "세종특별자치시",
    "41": "경기도",
    "42": "강원특별자치도",
    "43": "충청북도",
    "44": "충청남도",
    "45": "전북특별자치도",
    "46": "전라남도",
    "47": "경상북도",
    "48": "경상남도",
    "50": "제주특별자치도",
}

ELECTION_TYPE_LABELS = {
    "metro_mayor": "광역단체장",
    "local_mayor": "기초단체장",
    "regional_council": "광역의원",
    "local_council": "기초의원",
    "party_official": "당직자",
}


_JUNK_MIN_UNIQUE_RATIO = 0.15   # 고유 문자 비율 15% 미만이면 반복 텍스트로 간주
_JUNK_MIN_CONTENT_LEN = 15      # 제목+내용 합산 최소 의미있는 길이
_JUNK_REPEAT_UNIT_MAX = 10      # 이 길이 이하 단위가 전체의 70% 이상 차지하면 반복


def _is_junk_text(text: str) -> bool:
    """반복 문자열·너무 짧은·의미 없는 텍스트 여부 판별."""
    t = (text or "").strip()
    if not t or len(t) < _JUNK_MIN_CONTENT_LEN:
        return True
    # 고유 문자 비율 검사
    if len(set(t)) / len(t) < _JUNK_MIN_UNIQUE_RATIO:
        return True
    # 짧은 단위 반복 검사: 앞 2~10자 패턴이 전체에서 반복되는지
    for unit_len in range(2, min(_JUNK_REPEAT_UNIT_MAX + 1, len(t) // 2 + 1)):
        unit = t[:unit_len]
        repeat_count = t.count(unit)
        if repeat_count * unit_len >= len(t) * 0.70:
            return True
    return False


def _is_junk_pledge(title: str, content: str) -> bool:
    """공약 제목+내용이 쓰레기 데이터인지 판별."""
    combined = f"{title} {content}".strip()
    return _is_junk_text(combined)


def load_candidates_pledges_context(max_chars: int = 40000) -> str:
    """
    DB의 전체 candidates + candidate_pledges를 GPT 컨텍스트용 텍스트로 변환.
    max_chars 초과 시 공약 content(세부내용)부터 잘라내고 제목은 유지한다.
    후보 0명이면 빈 문자열 반환.
    반복 문자열·너무 짧은 공약은 품질 필터로 제외한다.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                c.id AS candidate_id,
                c.name,
                c.region_code,
                c.district_name,
                c.election_type,
                cp.title AS pledge_title,
                cp.content AS pledge_content,
                cp.priority
            FROM candidates c
            LEFT JOIN candidate_pledges cp ON cp.candidate_id = c.id
            WHERE c.approval_status = 'APPROVED'
            ORDER BY c.region_code, c.election_type, c.name, cp.priority ASC, cp.id ASC
        """).fetchall()
    except Exception as e:
        logger.warning("출마자 공약 로드 실패: %s", e)
        return ""
    finally:
        conn.close()

    if not rows:
        return ""

    candidates: dict[int, dict] = {}
    skipped_pledges = 0
    for r in rows:
        cid = r["candidate_id"]
        if cid not in candidates:
            region = REGION_NAME_MAP.get(r["region_code"] or "", r["region_code"] or "")
            etype = ELECTION_TYPE_LABELS.get(r["election_type"] or "", r["election_type"] or "")
            district = (r["district_name"] or "").strip()
            location = region
            if district:
                location = f"{region} {district}"
            candidates[cid] = {
                "name": r["name"],
                "location": location,
                "election_type": etype,
                "pledges": [],
            }
        if r["pledge_title"]:
            title = (r["pledge_title"] or "").strip()
            content = (r["pledge_content"] or "").strip()
            if _is_junk_pledge(title, content):
                skipped_pledges += 1
                logger.debug("품질 필터 제외: 후보=%s 제목=%r", r["name"], title[:40])
                continue
            candidates[cid]["pledges"].append({"title": title, "content": content})

    if skipped_pledges:
        logger.info("출마자 공약 품질 필터: %d건 제외", skipped_pledges)

    # 공약이 하나도 없는 후보는 컨텍스트에서 제외
    candidates = {cid: info for cid, info in candidates.items() if info["pledges"]}

    if not candidates:
        return ""

    return _format_context(candidates, max_chars)


def _format_context(candidates: dict[int, dict], max_chars: int) -> str:
    """후보 딕셔너리를 텍스트로 포맷. max_chars 초과 시 content부터 잘라냄."""
    blocks = []
    for cid, info in candidates.items():
        header = f"--- [{info['location']} / {info['election_type']}] {info['name']} ---"
        pledge_lines = []
        for i, p in enumerate(info["pledges"], 1):
            if p["content"]:
                pledge_lines.append(f"[{i}] {p['title']}: {p['content']}")
            else:
                pledge_lines.append(f"[{i}] {p['title']}")
        if not pledge_lines:
            pledge_lines.append("(공약 정보 없음)")
        blocks.append({"header": header, "pledges": pledge_lines, "cid": cid})

    full_text = _build_text(blocks, include_content=True)
    if len(full_text) <= max_chars:
        return full_text

    # content 잘라내기: 제목만 유지
    blocks_title_only = []
    for b in blocks:
        title_only_lines = []
        cinfo = candidates[b["cid"]]
        for i, p in enumerate(cinfo["pledges"], 1):
            title_only_lines.append(f"[{i}] {p['title']}")
        if not title_only_lines:
            title_only_lines.append("(공약 정보 없음)")
        blocks_title_only.append({"header": b["header"], "pledges": title_only_lines})

    return _build_text(blocks_title_only, include_content=False)[:max_chars]


def _build_text(blocks: list[dict], include_content: bool = True) -> str:
    parts = []
    for b in blocks:
        parts.append(b["header"])
        parts.extend(b["pledges"])
        parts.append("")
    return "\n".join(parts).strip()

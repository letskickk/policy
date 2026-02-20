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
    "42": "강원도",
    "43": "충청북도",
    "44": "충청남도",
    "45": "전라북도",
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


def load_candidates_pledges_context(max_chars: int = 40000) -> str:
    """
    DB의 전체 candidates + candidate_pledges를 GPT 컨텍스트용 텍스트로 변환.
    max_chars 초과 시 공약 content(세부내용)부터 잘라내고 제목은 유지한다.
    후보 0명이면 빈 문자열 반환.
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
            candidates[cid]["pledges"].append({
                "title": (r["pledge_title"] or "").strip(),
                "content": (r["pledge_content"] or "").strip(),
            })

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

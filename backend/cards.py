"""
정강정책·공약을 GPT로 구조화한 '원칙 카드/공약 카드' JSON 생성 및 로드.
검증 시 이 구조화 데이터 + 원문 스니펫 기반으로 점수화할 수 있다.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from backend.chunking import DocChunk
from backend.config import CHAT_MODEL, ROOT_DIR
from backend.embeddings import get_openai_client

logger = logging.getLogger(__name__)

CARDS_DIR = ROOT_DIR / "data" / "cards"
PLATFORM_CARDS_PATH = CARDS_DIR / "platform_cards.json"
PLEDGE_CARDS_PATH = CARDS_DIR / "pledge_cards.json"


def _chunk_ref(chunk: DocChunk) -> str:
    """청크를 원문 참조용 문자열로 (path:chunk_id)."""
    return f"{chunk.path}:{chunk.chunk_id}"


def _build_cards_prompt(chunks: List[DocChunk], card_type: str) -> str:
    label = "원칙 카드" if card_type == "platform" else "공약 카드"
    prefix = "PC" if card_type == "platform" else "GC"
    lines = []
    for i, c in enumerate(chunks):
        ref = _chunk_ref(c)
        snippet = (c.text[:800] + "..." if len(c.text) > 800 else c.text).strip()
        lines.append(f"[{ref}]\n{snippet}")
    chunks_block = "\n\n".join(lines)
    return f"""아래는 {label}로 구조화할 청크들이다. 각 청크는 [path:chunk_id] 형식의 참조와 본문을 가진다.

[청크 목록]
{chunks_block}

위 청크들을 요약·통합하여 {label} JSON 배열을 만들어라.
- 각 카드: id({prefix}1, {prefix}2, ...), title(제목), summary(요약), chunk_ids(근거가 되는 청크 참조 문자열 배열, 예: ["정강정책.pdf:0","정강정책.pdf:1"])
- 중복·유사 주제는 하나의 카드로 묶고 chunk_ids에 해당 청크 참조를 모두 나열하라.
- 반드시 JSON 객체 하나로 반환하고, "cards" 키에 카드 배열을 넣어라. 예시:
{{"cards": [
  {{"id":"{prefix}1","title":"...","summary":"...","chunk_ids":["파일명.pdf:0"]}},
  {{"id":"{prefix}2","title":"...","summary":"...","chunk_ids":["파일명.pdf:1","파일명.pdf:2"]}}
]}}
"""


def build_cards_from_chunks(chunks: List[DocChunk], card_type: str) -> List[Dict[str, Any]]:
    """
    청크 리스트를 GPT에 넘겨 원칙 카드(platform) 또는 공약 카드(pledge) JSON 배열을 생성한다.
    각 카드에는 근거 chunk_id(chunk_ids)가 포함된다.
    """
    if not chunks:
        logger.warning(f"build_cards_from_chunks: {card_type} 청크 없음")
        return []

    prompt = _build_cards_prompt(chunks, card_type)
    client = get_openai_client()
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": "당 문서 청크를 원칙/공약 카드 JSON 배열로 구조화한다. JSON만 반환한다."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        cards = data.get("cards", data.get("items", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not isinstance(cards, list):
            cards = []
        logger.info(f"[CARDS] {card_type} 카드 {len(cards)}개 생성")
        return cards
    except Exception as e:
        logger.error(f"[CARDS] {card_type} 생성 실패: {e}", exc_info=True)
        return []


def save_cards(cards: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
    logger.info(f"[CARDS] 저장: {path}, {len(cards)}개")


def load_cards(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[CARDS] 로드 실패 {path}: {e}")
        return []


def build_and_save_platform_cards(chunks: List[DocChunk]) -> List[Dict[str, Any]]:
    cards = build_cards_from_chunks(chunks, "platform")
    if cards:
        save_cards(cards, PLATFORM_CARDS_PATH)
    return cards


def build_and_save_pledge_cards(chunks: List[DocChunk]) -> List[Dict[str, Any]]:
    cards = build_cards_from_chunks(chunks, "pledge")
    if cards:
        save_cards(cards, PLEDGE_CARDS_PATH)
    return cards


def load_platform_cards() -> List[Dict[str, Any]]:
    return load_cards(PLATFORM_CARDS_PATH)


def load_pledge_cards() -> List[Dict[str, Any]]:
    return load_cards(PLEDGE_CARDS_PATH)

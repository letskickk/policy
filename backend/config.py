import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트 (backend 기준 상위)
ROOT_DIR = Path(__file__).resolve().parent.parent
# .env는 항상 프로젝트 루트에서 로드 (실행 위치와 무관)
load_dotenv(ROOT_DIR / ".env")

PDF_DIR = ROOT_DIR / "data" / "pdf"
# 정강·정책(이념·취지) 문서 / 우리당 공약 문서 구분용 하위 폴더
PDF_DIR_PLATFORM = PDF_DIR / "정강정책"
PDF_DIR_PLEDGES = PDF_DIR / "공약"
PROMPTS_DIR = ROOT_DIR / "prompts"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# GPT 컨텍스트 한도(대략). 초과 시 잘라냄 (예: 4o-mini 128k, 보수적으로 30000자)
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "30000"))

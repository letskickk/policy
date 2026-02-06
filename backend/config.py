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

# GPT 컨텍스트 한도(대략). 초과 시 잘라냄 (예: 4o-mini 128k, 보수적으로 50000자로 증가)
# 각 컨텍스트(정강정책, 공약)는 절반씩 사용하므로 각각 25000자까지 가능
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "50000"))

# 벡터 검색 설정
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
INDEX_CACHE_DIR = ROOT_DIR / "data" / "index_cache"
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
MAX_CHUNKS_PER_FILE = int(os.getenv("MAX_CHUNKS_PER_FILE", "120"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

# 인덱스 강제 재빌드 플래그 (1이면 캐시 삭제 후 재빌드)
REBUILD_INDEX = os.getenv("REBUILD_INDEX", "0") == "1"

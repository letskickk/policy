import os
import sys
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
# /check(당 부합 점검)에서 사용
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2")

# GPT 컨텍스트 한도(대략). 초과 시 잘라냄 (예: 4o-mini 128k, 보수적으로 50000자로 증가)
# 각 컨텍스트(정강정책, 공약)는 절반씩 사용하므로 각각 25000자까지 가능
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "50000"))

# 벡터 검색 설정
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
# /api/pledge/verify·카드 생성에서 사용. 미설정 시 OPENAI_MODEL(gpt-5.2)과 동일
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or OPENAI_MODEL

# FAISS는 한글 등 비-ASCII 경로에서 "Illegal byte sequence" 오류 발생 → ASCII 전용 경로 사용
_def_cache = ROOT_DIR / "data" / "index_cache"
def _path_ascii_only(p: Path) -> bool:
    try:
        return all(ord(c) < 128 for c in str(p.resolve()))
    except Exception:
        return False

if os.getenv("INDEX_CACHE_DIR"):
    INDEX_CACHE_DIR = Path(os.getenv("INDEX_CACHE_DIR")).resolve()
else:
    INDEX_CACHE_DIR = _def_cache
    if not _path_ascii_only(INDEX_CACHE_DIR):
        if sys.platform == "win32":
            # Windows: C:\ProgramData는 ASCII 전용 (FAISS 호환)
            _base = os.environ.get("ProgramData")
            if not _base or not _path_ascii_only(Path(_base)):
                _base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
        else:
            _base = os.environ.get("XDG_CACHE_HOME") or (os.environ.get("HOME", ".") + "/.cache")
        INDEX_CACHE_DIR = Path(_base) / "Policy" / "index_cache"
        INDEX_CACHE_DIR = INDEX_CACHE_DIR.resolve()
        INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
MAX_CHUNKS_PER_FILE = int(os.getenv("MAX_CHUNKS_PER_FILE", "120"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

# 인덱스 강제 재빌드 플래그 (1이면 캐시 삭제 후 재빌드)
REBUILD_INDEX = os.getenv("REBUILD_INDEX", "0") == "1"
# 정강/공약 원칙·공약 카드 JSON 생성 (1이면 인덱스 빌드 후 카드 생성)
BUILD_CARDS = os.getenv("BUILD_CARDS", "0") == "1"

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

# PDF 텍스트 추출: "pdfplumber" | "pypdf" | "auto". 로컬/AWS 출력 일치를 위해 pdfplumber 권장.
PDF_EXTRACTOR = (os.getenv("PDF_EXTRACTOR", "pdfplumber") or "pdfplumber").strip().lower()
if PDF_EXTRACTOR not in ("pdfplumber", "pypdf", "auto"):
    PDF_EXTRACTOR = "pdfplumber"

# 벡터 검색 설정
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
# /api/pledge/verify·카드 생성에서 사용. 미설정 시 OPENAI_MODEL(gpt-5.2)과 동일
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or OPENAI_MODEL

# 인덱스 캐시: AWS/컨테이너에서 read-only /app/data 이면 쓰기 실패 → env 또는 /tmp 권장
_def_cache_env = os.getenv("INDEX_CACHE_DIR", "").strip()
if _def_cache_env:
    INDEX_CACHE_DIR = Path(_def_cache_env).resolve()
else:
    # 기본: Linux는 /tmp/index_cache (쓰기 보장), Windows는 프로젝트 하위
    if sys.platform == "win32":
        INDEX_CACHE_DIR = (ROOT_DIR / "data" / "index_cache").resolve()
    else:
        INDEX_CACHE_DIR = Path("/tmp/index_cache").resolve()
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
MAX_CHUNKS_PER_FILE = int(os.getenv("MAX_CHUNKS_PER_FILE", "120"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

# 인덱스 강제 재빌드 플래그 (1이면 캐시 삭제 후 재빌드)
REBUILD_INDEX = os.getenv("REBUILD_INDEX", "0") == "1"
# 정강/공약 원칙·공약 카드 JSON 생성 (1이면 인덱스 빌드 후 카드 생성)
BUILD_CARDS = os.getenv("BUILD_CARDS", "0") == "1"
# AWS: PDF 폴더가 비었을 때 S3에서 동기화할 URI (예: s3://bucket/pdf/)
PDF_S3_URI = os.getenv("PDF_S3_URI", "").strip()

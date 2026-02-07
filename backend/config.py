import os
import sys
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트 (backend 기준 상위)
ROOT_DIR = Path(__file__).resolve().parent.parent
# .env는 항상 프로젝트 루트에서 로드 (실행 위치와 무관)
load_dotenv(ROOT_DIR / ".env")


def _nfc(s: str) -> str:
    """mac/linux 호환: 경로 문자열을 NFC로 정규화."""
    return unicodedata.normalize("NFC", s) if s else s


PDF_DIR = ROOT_DIR / "data" / "pdf"
# 정강·정책(이념·취지) 문서 / 우리당 공약 문서 구분용 하위 폴더 (NFC 정규화)
PDF_DIR_PLATFORM = PDF_DIR / _nfc("정강정책")
PDF_DIR_PLEDGES = PDF_DIR / _nfc("공약")
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
# 임베딩 차원 (text-embedding-3-large=3072, text-embedding-3-small=1536). 모델 변경 시 이것도 변경 필요.
_embed_dim = os.getenv("EMBEDDING_DIMENSION", "").strip()
if _embed_dim:
    EMBEDDING_DIMENSION = int(_embed_dim)
else:
    EMBEDDING_DIMENSION = 3072 if "large" in EMBEDDING_MODEL.lower() else 1536
# /api/pledge/verify·카드 생성에서 사용. 미설정 시 OPENAI_MODEL(gpt-5.2)과 동일
# Responses API 사용 시 gpt-5.2 등 최신 모델 지원
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or OPENAI_MODEL

# 인덱스 캐시: AWS/컨테이너에서 /tmp는 재시작 시 휘발 → INDEX_CACHE_DIR로 영구 경로 지정 권장
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
# /api/debug/* 엔드포인트 활성화 (프로덕션: 0으로 비활성화)
DEBUG_ENDPOINTS_ENABLED = os.getenv("DEBUG_ENDPOINTS_ENABLED", "1") == "1"
# OpenAI Vector Store 사용 (1=사용, FAISS 대신). AWS 인프라 복잡도 제거.
USE_OPENAI_VECTOR_STORE = os.getenv("USE_OPENAI_VECTOR_STORE", "0") == "1"
# 서버 시작 시 PDF 스캔 생략. 1이면 scripts/index_pdfs_to_vector_store.py로 별도 인덱싱 후 .env의 ID만 사용.
SKIP_PDF_SCAN_ON_STARTUP = os.getenv("SKIP_PDF_SCAN_ON_STARTUP", "0") == "1"
# Vector Store ID (scripts/index_pdfs_to_vector_store.py 실행 후 .env에 저장)
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
# 지역별 공약 전용 (타지역 유사성 검토 시 이 store만 검색)
OPENAI_REGIONAL_VECTOR_STORE_ID = os.getenv("OPENAI_REGIONAL_VECTOR_STORE_ID", "").strip()
# file_search 결과 개수 제한 (full phase 기본 6)
FILE_SEARCH_MAX_RESULTS = int(os.getenv("FILE_SEARCH_MAX_RESULTS", "6"))
# quick phase용 (속도 우선, 기본 3)
FILE_SEARCH_MAX_RESULTS_QUICK = int(os.getenv("FILE_SEARCH_MAX_RESULTS_QUICK", "3"))

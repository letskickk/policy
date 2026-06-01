import os
import sys
import unicodedata
from pathlib import Path

# 프로젝트 루트 (backend 기준 상위)
ROOT_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env", override=True)
except ImportError:
    # dotenv 없으면 .env 수동 로드 (scripts/verify_winners_api.py 등)
    _env = ROOT_DIR / ".env"
    if _env.exists():
        with open(_env, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip().replace("\r", "")
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip().lstrip("\ufeff").strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        os.environ.setdefault(k, v)


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
# /check(당 부합 점검)에서 사용 — 5축 채점·보정 규칙 정확도가 핵심
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

# GPT 컨텍스트 한도(자). 초과 시 잘라냄. 0이면 앱 한도 없음(전부 로드, GPT API 토큰 한도는 별도)
# 각 컨텍스트(정강정책, 공약)는 절반씩 사용. 기본 50000 → 폴더당 25000자
_raw = int(os.getenv("MAX_CONTEXT_CHARS", "50000"))
MAX_CONTEXT_CHARS = (2**28) if _raw <= 0 else _raw  # 0 이하 = 무제한(실질적으로 전부 로드)

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
# 챗봇·카드·verify 등 대화형에서 사용. 점검(OPENAI_MODEL)보다 가벼운 모델.
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or "gpt-5.4-mini"
# 이미지 저장용 카드뉴스 요약 전용 모델
CARD_SUMMARY_MODEL = os.getenv("CARD_SUMMARY_MODEL", "").strip() or "gpt-5.4-mini"

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
DEBUG_ENDPOINTS_ENABLED = os.getenv("DEBUG_ENDPOINTS_ENABLED", "0") == "1"
# OpenAI Vector Store 사용 (1=사용, FAISS 대신). AWS 인프라 복잡도 제거.
USE_OPENAI_VECTOR_STORE = os.getenv("USE_OPENAI_VECTOR_STORE", "0") == "1"
# 서버 시작 시 PDF 스캔 생략. 1이면 scripts/index_pdfs_to_vector_store.py로 별도 인덱싱 후 .env의 ID만 사용.
SKIP_PDF_SCAN_ON_STARTUP = os.getenv("SKIP_PDF_SCAN_ON_STARTUP", "0") == "1"
# Vector Store ID (scripts/index_pdfs_to_vector_store.py 실행 후 .env에 저장)
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
# 지역별 공약 전용 (타지역 유사성 검토 시 이 store만 검색)
OPENAI_REGIONAL_VECTOR_STORE_ID = os.getenv("OPENAI_REGIONAL_VECTOR_STORE_ID", "").strip()
# 2022(제8회) 당선인 공약 전용 (유사/벤치마킹 비교용)
OPENAI_WINNERS2022_VECTOR_STORE_ID = os.getenv("OPENAI_WINNERS2022_VECTOR_STORE_ID", "").strip()
# file_search 결과 개수 제한 (full phase 기본 6)
FILE_SEARCH_MAX_RESULTS = int(os.getenv("FILE_SEARCH_MAX_RESULTS", "6"))
# quick phase용 (속도 우선, 기본 3)
FILE_SEARCH_MAX_RESULTS_QUICK = int(os.getenv("FILE_SEARCH_MAX_RESULTS_QUICK", "3"))

# 접근제어 / 쿼터 / 레이트리밋 (내부 정책 도구용)
ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
POLICY_DRAFTER_TEST_EMAILS = [e.strip().lower() for e in os.getenv("POLICY_DRAFTER_TEST_EMAILS", "").split(",") if e.strip()]
# 이메일 인증 (1=활성화 시 가입 후 인증 메일 발송, 인증 완료 후 로그인 가능)
EMAIL_VERIFICATION_ENABLED = os.getenv("EMAIL_VERIFICATION_ENABLED", "0") == "1"
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1").rstrip("/")
# SMTP (이메일 인증 시 필요)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER or "noreply@example.com")
ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL", "").strip()

# 이메일 인증 사용 시 SMTP 미설정이면 가입 시 메일이 안 나감 → 시작 시 한 번 경고
if EMAIL_VERIFICATION_ENABLED and (not SMTP_HOST or not SMTP_USER):
    import logging
    logging.getLogger(__name__).warning(
        "EMAIL_VERIFICATION_ENABLED=1 이지만 SMTP_HOST/SMTP_USER가 비어 있어 인증 메일이 발송되지 않습니다. .env에 SMTP 설정을 추가하세요."
    )

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or "change-me-in-production"
QUOTA_DAILY = int(os.getenv("QUOTA_DAILY", "30"))
QUOTA_MONTHLY = int(os.getenv("QUOTA_MONTHLY", "300"))
# 토큰 기반 쿼터 (공약분석 + 공약코치 통합). 10만/일(≈$1), 1.5M/월
QUOTA_DAILY_TOKENS = int(os.getenv("QUOTA_DAILY_TOKENS", "100000"))
QUOTA_MONTHLY_TOKENS = int(os.getenv("QUOTA_MONTHLY_TOKENS", "1500000"))
RATE_LIMIT_IP_PER_MIN = int(os.getenv("RATE_LIMIT_IP_PER_MIN", "30"))
RATE_LIMIT_USER_PER_MIN = int(os.getenv("RATE_LIMIT_USER_PER_MIN", "10"))
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))

# DB 경로 (로컬에서 서버 DB 복사본 쓰려면 .env에 DATABASE_PATH=data/policy_server.db 등으로 지정)
_db_env = os.getenv("DATABASE_PATH", "").strip()
if _db_env:
    _db_p = Path(_db_env)
    DATABASE_PATH = _db_p.resolve() if _db_p.is_absolute() else (ROOT_DIR / _db_env).resolve()
else:
    DATABASE_PATH = ROOT_DIR / "data" / "policy.db"

# 공공데이터포털 중앙선거관리위원회 API
# - 당선인 정보: https://apis.data.go.kr/9760000/WinnerInfoInqireService2 (15000864)
# - 선거공약 정보: https://apis.data.go.kr/9760000/ElecPrmsInfoInqireService (15040587)
# - 코드정보(시·도/시군구): CommonCodeService
_data_key = os.getenv("DATA_GO_KR_API_KEY", "").strip().replace("\r", "").replace("\n", "")
DATA_GO_KR_API_KEY = _data_key
DATA_GO_KR_WINNER_API_KEY = os.getenv("DATA_GO_KR_WINNER_API_KEY", "").strip().replace("\r", "").replace("\n", "") or _data_key
DATA_GO_KR_PLEDGE_API_KEY = os.getenv("DATA_GO_KR_PLEDGE_API_KEY", "").strip().replace("\r", "").replace("\n", "") or _data_key

# 소상공인시장진흥공단 상가(상권)정보 API (data.go.kr 서비스)
SEMAS_API_KEY = os.getenv("SEMAS_API_KEY", "").strip().replace("\r", "").replace("\n", "") or _data_key
# 도로교통공단 TAAS 교통사고 다발지역 API (data.go.kr 서비스)
TAAS_API_KEY = os.getenv("TAAS_API_KEY", "").strip().replace("\r", "").replace("\n", "") or _data_key
# KOSIS 국가통계포털 API (kosis.kr, 별도 인증)
KOSIS_API_KEY = os.getenv("KOSIS_API_KEY", "").strip().replace("\r", "").replace("\n", "")
# 서울 열린데이터 광장 API (data.seoul.go.kr, 별도 인증)
SEOUL_OPEN_API_KEY = os.getenv("SEOUL_OPEN_API_KEY", "").strip().replace("\r", "").replace("\n", "")

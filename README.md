# AI 공약 멘토링 시스템

출마자가 공약을 입력하면 **당 방향 부합 여부**, **중복·유사 여부**, **보완점**을 즉시 제공하는 AI 기반 공약 멘토링 환경입니다.

## 목적

- **출마자**: 당 정강·정책에 맞는 공약을 스스로 점검하고, 타 후보와의 차별화 포인트를 참고
- **정책국**: 기초 검토 자동화로 전략·메시지·공약 완성도에 집중

## 핵심 기능

| 기능 | 설명 |
|------|------|
| **당 방향 부합 점검** | 정강·정책·과거 공약 기준으로 부합 여부 점검, 수정·보완 체크리스트 제공 |
| **벡터 검색 기반 검증** | FAISS 인덱스를 활용한 정확한 근거 인용 및 리포트 생성 (POST /api/pledge/verify) |
| **중복·유사 탐지** | 후보 공약 DB와 비교해 유사 공약 제시, 차별화 포인트 제안 |

## 추진 범위

- 본 시스템: **즉각 피드백** 및 **당 정체성 가이드**에 집중
- 지방의회 회의록 기반 지역 현안 발굴/데이터 분석(참치상사 등)과는 **역할 분리**

## 1차 구현 결정사항 (MVP)

| 항목 | 결정 |
|------|------|
| **데이터** | PDF 방식으로 제공 (정강·정책·과거 공약) |
| **분석 엔진** | GPT API 이용 |
| **우선 기능** | **당 부합 점검** 먼저 구현 → 이후 중복·유사 탐지 확장 |

## 문서

- [요구사항 명세서](docs/요구사항_명세서.md) — 추진배경, 목적, 범위, 핵심 기능
- [기술 명세 및 구현 방향](docs/기술_명세_구현방향.md) — PDF 처리, GPT API, 당 부합 점검 플로우

## 프로젝트 구조

```
Policy/
├── README.md
├── docs/
│   ├── 요구사항_명세서.md
│   └── 기술_명세_구현방향.md
├── data/
│   ├── pdf/              # 정강·정책·과거 공약 PDF 원본
│   │   ├── 정강정책/     # 정강정책 PDF들
│   │   ├── 공약/         # 우리당 공약 PDF들
│   │   └── 지역별 공약/  # 타지역 공약 PDF들
│   └── index_cache/      # FAISS 인덱스 캐시 (자동 생성)
├── backend/              # PDF 파싱, GPT API 호출, 당 부합 점검 API
│   ├── pdf_loader.py     # PDF 텍스트 추출
│   ├── pdf_loader_chunks.py  # PDF 청크 분할
│   ├── chunking.py       # 텍스트 청킹 로직
│   ├── embeddings.py     # OpenAI 임베딩 생성
│   ├── vector_index.py   # FAISS 벡터 인덱스
│   ├── index_builder.py  # 인덱스 빌드 및 캐시 관리
│   ├── report.py         # 검색 결과 기반 리포트 생성
│   └── main.py           # FastAPI 앱
├── frontend/             # 출마자 공약 입력·결과 표시 UI (추후)
└── prompts/              # GPT 시스템/유저 프롬프트
```

## 접근제어 (내부 정책 도구)

공개 시 API 비용 폭증을 막기 위해 회원가입·관리자 승인·쿼터·레이트리밋이 적용됩니다.

| 항목 | 설명 |
|------|------|
| **회원가입** | /signup에서 이메일·비밀번호로 가입. ADMIN_EMAILS에 해당 이메일이 있으면 자동 승인. |
| **관리자 승인** | /admin/users에서 PENDING 사용자를 APPROVED/REJECTED/SUSPENDED로 변경 |
| **쿼터** | 일일(QUOTA_DAILY, 기본 30회), 월간(QUOTA_MONTHLY, 기본 300회). `.env`에서 변경 가능 |
| **레이트리밋** | IP당 분당(RATE_LIMIT_IP_PER_MIN, 기본 30회), 사용자당 분당(RATE_LIMIT_USER_PER_MIN, 기본 10회) |
| **캐싱** | 동일 입력 24시간 내 재호출 시 OpenAI 미호출, 저장된 결과 반환 |

**최초 실행 흐름**
1. `python scripts/init_db.py` — DB 초기화
2. `.env`에 `ADMIN_EMAILS=admin@example.com` 설정 (관리자 이메일)
3. 회원가입 → ADMIN_EMAILS에 있으면 자동 승인, 없으면 관리자가 /admin/users에서 승인
4. 승인 후 /pledge에서 분석 가능

**쿼터/레이트리밋 변경**: `.env`에 `QUOTA_DAILY=50`, `QUOTA_MONTHLY=500` 등 추가

## 실행 방법

1. **가상환경 및 패키지**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

2. **API 키 설정**  
   `.env.example`을 복사해 `.env`를 만들고 `OPENAI_API_KEY`에 키를 넣는다.

3. **문서 배치**  
   다음 폴더 구조로 PDF 또는 TXT를 배치한다:
   ```
   data/pdf/
   ├── 정강정책/     # 정강정책 문서 (.pdf, .txt)
   ├── 공약/         # 우리당 공약 문서 (.pdf, .txt)
   └── 지역별 공약/  # 타지역 공약 문서 (.pdf, .txt)
   ```
   `.txt`는 UTF-8로 읽는다. 같은 이름의 .pdf와 .txt가 있으면 .pdf 우선.
   (폴더가 없어도 API는 동작하며, 해당 섹션은 비어있음으로 표시된다.)

4. **DB 초기화** (최초 1회)
   ```bash
   python scripts/init_db.py
   ```

5. **서버 실행** (프로젝트 루트에서)
   ```bash
   uvicorn backend.main:app --reload
   ```
   브라우저: http://127.0.0.1:8000  
   API 문서: http://127.0.0.1:8000/docs
   
   **참고**:  
   - FAISS 모드: 서버 시작 시 PDF 스캔 → 청크 분할 → 임베딩 → 인덱스 구축. 첫 실행 시 시간이 걸릴 수 있음.  
   - Vector Store 모드: **서버 시작 시 ingest 없음**. 반드시 `scripts/ingest_vector_store.py`로 사전 인덱싱 후 서버를 실행.

6. **API 호출 예시** (로그인·승인 필요)

   **기존 방식 (전체 컨텍스트 사용)**:
   ```bash
   curl -X POST http://127.0.0.1:8000/check \
     -H "Content-Type: application/json" \
     -d '{"pledge": "지역 청년 일자리 1000개 창출"}'
   ```
   
   **벡터 검색 기반 검증 (근거 인용)**:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/pledge/verify \
     -H "Content-Type: application/json" \
     -d '{
       "text": "지역 청년 일자리 1000개 창출",
       "top_k_platform": 6,
       "top_k_pledge": 6,
       "top_k_regional": 8
     }'
   ```

   **디버그 검색 (GET, query params)** — `source`, `q` 필수, `top_k` 선택(기본 10, 1~50):
   ```bash
   curl -G "http://localhost:8000/api/debug/search" --data-urlencode "source=pledge" --data-urlencode "q=신구연금 분리" --data-urlencode "top_k=10"
   ```
   **디버그 검색 (POST, JSON 바디)**:
   ```bash
   curl -X POST "http://localhost:8000/api/debug/search" -H "Content-Type: application/json" -d "{\"source\":\"pledge\",\"q\":\"신구연금 분리\",\"top_k\":10}"
   ```
   (Windows PowerShell에서는 `-d '{"source":"pledge","q":"신구연금 분리","top_k":10}'` 형태로 사용 가능.)
   
   응답은 JSON 형식으로 다음을 포함합니다:
   - `summary`: 적합도 점수 및 판정
   - `platform`: 정강정책 근거 스니펫 (인용 포함)
   - `pledges`: 우리당 공약 근거 스니펫 (인용 포함)
   - `regional_similarity`: 타지역 공약 유사성 분석
   - `conflicts`: 상충 이슈 및 제안
   - `improvements`: 개선 제안

## OpenAI Vector Store 모드 (AWS 단순화)

FAISS 대신 OpenAI File Search를 쓰면 인덱스 경로·EBS/EFS 등 AWS 설정이 필요 없습니다.

### 설정

```env
USE_OPENAI_VECTOR_STORE=1
OPENAI_VECTOR_STORE_ID=vs_xxx
OPENAI_REGIONAL_VECTOR_STORE_ID=vs_yyy
FILE_SEARCH_MAX_RESULTS=6
```

### PDF ingest 스크립트 (1회 실행)

서버 시작 시 **ingest가 실행되지 않도록** 분리되어 있습니다. 아래 스크립트로 PDF를 업로드·인덱싱합니다:

```bash
# 프로젝트 루트에서 실행
python scripts/ingest_vector_store.py
```

- `data/pdf/정강정책/`, `data/pdf/공약/` → 정강·공약 Vector Store 생성
- `data/pdf/지역별 공약/` → 지역별 공약 Vector Store 생성
- 중복 판정: **sha256(file bytes)** 기반으로 동일 파일은 재업로드하지 않음
- Vector Store ID는 `.rag/registry.json` 및 `.rag/vector_store_id*.txt`에 저장

자세한 사용법: [docs/Vector_Store_업로드_스크립트.md](docs/Vector_Store_업로드_스크립트.md)

### 검증 API (2단계)

- `phase=quick`: file_search 결과 3~5개, 짧은 판정(JSON)만 반환
- `phase=full`: 결과 6~8개, 근거 인용·상충 분석 포함

### git push 시 자동 동기화

`push to main` 시 GitHub Actions가 `scripts/sync_vector_store.py`를 실행해 **변경된 PDF만** Vector Store에 반영합니다.  
초기 설정: [docs/Vector_Store_업로드_스크립트.md §8](docs/Vector_Store_업로드_스크립트.md) 참고.

---

## 환경 변수 설정

`.env` 파일에 다음 변수를 설정할 수 있습니다:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-large
CHAT_MODEL=gpt-4o-mini
CHUNK_SIZE=1200
CHUNK_OVERLAP=200
MAX_CHUNKS_PER_FILE=120
EMBEDDING_BATCH_SIZE=64
```

## 인덱스 캐시 관리

- 인덱스는 `INDEX_CACHE_DIR`에 저장됩니다 (Windows: `data/index_cache`, Linux: `/tmp/index_cache`).
- **AWS 배포 시**: Linux 기본값 `/tmp`는 재시작 시 삭제되므로 `.env`에 `INDEX_CACHE_DIR=/app/data/index_cache` 등 **영구 경로**를 지정하세요.
- PDF 파일이 변경되면 (파일명, 수정시간, 크기 기준) 자동으로 재빌드됩니다.
- 강제 재빌드가 필요한 경우 캐시 파일을 삭제하거나 `REBUILD_INDEX=1`로 서버를 재시작하세요.
- RAG 검색 시 **멀티워커**는 인덱스 미공유 이슈가 있으므로 `uvicorn --workers 1` 권장.

## 다음 단계

1. **프롬프트 정교화**: 실제 PDF로 테스트하며 부합/부분부합/미부합·체크리스트 형식 조정
2. **프론트/챗**: 웹 폼 또는 챗봇으로 연동
3. **중복·유사 탐지**: 후보 공약 DB 구축 후 2차 기능 추가

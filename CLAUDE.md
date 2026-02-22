# 개혁신당 정책 멘토링 시스템 — Claude 작업 가이드

## 프로젝트 목적
출마자 공약을 AI로 점검: 당 부합 여부 판단, 중복·유사 탐지, 보완점 제시.
도메인: https://policy.reformparty.kr/

## 스택
- **백엔드**: FastAPI(Python) + OpenAI API(GPT + Vector Store) + SQLite
- **프론트**: 바닐라 JS + HTML
- **인프라**: AWS EC2 + Docker + nginx (로컬은 Windows)

---

## 핵심 파일 역할 (자주 편집하는 파일 우선)

| 파일 | 역할 |
|---|---|
| `prompts/당_부합_점검_유저.txt` | GPT user 프롬프트 템플릿 (출력 형식 규칙 포함) |
| `prompts/당_부합_점검_시스템.txt` | GPT system 프롬프트 |
| `backend/prompts.py` | 프롬프트 파일 로드 및 `{{변수}}` 치환 |
| `backend/main.py` | FastAPI 앱, 모든 API 라우터 |
| `backend/openai_vector_store.py` | OpenAI Vector Store 기반 RAG |
| `backend/check_service.py` | 당 부합 점검 서비스 로직 |
| `backend/analysis_service.py` | 공약 분석 서비스 |
| `backend/auth.py` | 회원가입/로그인/세션 |
| `backend/database.py` | SQLite 초기화·쿼리 |
| `backend/quota_rate.py` | 일일30회·월300회 쿼터 |
| `static/js/check-result-render.js` | 점검 결과 프론트 렌더링 |
| `static/pledge.html` | 공약 입력/점검 페이지 |
| `static/map.html` | 지역별 출마자 지도 페이지 (지도 클릭·지역/선거타입/선거구 필터, 후보 목록·공약 상세·점검 링크) |

---

## 프롬프트 템플릿 변수 (`당_부합_점검_유저.txt`)

```
{{PLATFORM_CONTEXT}}          ← 정강·정책 문서 (이념 판단용)
{{PLEDGES_CONTEXT}}           ← 우리당 공약 문서
{{WINNERS2022_PLEDGES_CONTEXT}} ← 2022 당선인 공약
{{CANDIDATES_PLEDGES_CONTEXT}} ← 등록된 타 출마자 공약
{{PLEDGE}}                    ← 입력된 출마자 공약 (점검 대상)
{{ELECTION_TYPE}}             ← 선거유형 (광역단체장 등)
{{REGION_LEVEL}}              ← 광역/기초
{{REGION_PROVINCE}}           ← 시·도
{{REGION_CITY}}               ← 시·군·구
{{DISTRICT_NAME}}             ← 선거구명
```

---

## GPT 출력 형식 규칙 (현재 적용된 주요 규칙)

- 마크다운(**볼드** 등) 사용 금지, 일반 텍스트만
- 모든 결과 라벨은 `결과:` (다른 표현 금지)
- 섹션 2·4: `유사·중복 공약:` 헤더 라벨 출력 금지
- 섹션 2·3·4: `유사 -`, `동일 -`, `참고 -` 접두어 출력 금지
- 섹션 2·3·4: 별도 `분류:` 줄 출력 금지
- 섹션 3: 당선인 공약 지칭 시 "2022 공약" 대신 "[당선인명] [직책약칭] 공약" 형식 (예: "오세훈 서울시장 공약")

### 출력 섹션 구조
1. 개혁신당 정강정책과의 부합성
2. 개혁신당 중앙당 공약과의 유사성
3. 제8회 지방선거(2022) 당선인 공약과의 비교
4. 우리 당 출마자 공약 비교
5. 총평 (종합점수, 5개 축 점수)
6. 수정·보완 제안

---

## 주요 API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/check` | 당 부합 점검 (전체 컨텍스트) |
| POST | `/api/pledge/verify` | 벡터 검색 기반 공약 검증 |
| GET | `/api/debug/search` | 벡터 검색 디버그 |
| GET | `/api/regions` | 지역 목록 (지도 페이지 지역 셀렉트·툴팁 후보 수) |
| GET | `/api/districts` | 선거구 목록 |
| GET | `/api/candidates` | 후보자 목록 (region_code, district_code, election_type 쿼리) |
| GET | `/api/candidates/{id}` | 후보 상세·공약 전체 (지도 페이지 모달용) |
| GET | `/api/stats/election-types` | 선거 타입별 후보 수 (지도 페이지 선거타입 셀렉트 카운팅) |

---

## 환경변수 키 목록 (`.env`)

```
OPENAI_API_KEY
OPENAI_MODEL / CHAT_MODEL          # 현재: gpt-5.2
EMBEDDING_MODEL                    # text-embedding-3-large
USE_OPENAI_VECTOR_STORE            # 0=FAISS, 1=OpenAI
OPENAI_VECTOR_STORE_ID             # vs_xxx (정강·공약)
OPENAI_REGIONAL_VECTOR_STORE_ID   # vs_yyy (지역별)
OPENAI_WINNERS2022_VECTOR_STORE_ID # vs_zzz (2022 당선인)
SKIP_PDF_SCAN_ON_STARTUP
DATA_GO_KR_API_KEY                 # 공공데이터포털
ADMIN_EMAILS
QUOTA_DAILY / QUOTA_MONTHLY        # 30 / 300
```

---

## 로컬 서버 실행

```bash
.venv\Scripts\activate
uvicorn backend.main:app --reload
# 또는 배치파일: 2_서버실행_집노트북.bat
```

## RAG 모드

- **FAISS** (로컬): 서버 시작 시 PDF → 청크 → 임베딩 자동 처리. `--workers 1` 필수.
- **OpenAI Vector Store** (AWS): `USE_OPENAI_VECTOR_STORE=1`, ingest는 `python scripts/ingest_vector_store.py`

---

## 주의사항

- Windows 로컬은 cp949 인코딩 주의 (파일 읽기 시 `encoding="utf-8"` 명시)
- Linux(Docker)는 `ENV LANG=C.UTF-8`
- INDEX_CACHE_DIR: AWS `/tmp`는 휘발 → `.env`에 영구 경로 지정
- git push → GitHub Actions가 `scripts/sync_vector_store.py` 자동 실행

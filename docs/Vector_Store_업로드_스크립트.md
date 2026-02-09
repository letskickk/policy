# Vector Store ingest 스크립트

서버 시작 시 ingest를 실행하지 않기 위해, **별도 스크립트로만 인덱싱**합니다.  
Vector Store ID는 `.rag/registry.json` 및 `.rag/vector_store_id*.txt`에 저장되어 재시작 시 재사용됩니다.

---

## 1. 사전 준비

- `.env`에 `OPENAI_API_KEY` 설정
- `data/pdf/` 아래에 폴더 구조:
  - `정강정책/` – 우리당 강령 PDF
  - `공약/` – 우리당 중앙 공약 PDF
  - `지역별 공약/` – 타지역 출마자 공약 PDF (선택)

---

## 2. 스크립트 실행

**프로젝트 루트**에서 실행:

```bash
# 기본: 정강+공약 / 지역별 공약 각각 Vector Store 생성
python scripts/ingest_vector_store.py

# 업로드 없이 변경 사항만 보기
python scripts/ingest_vector_store.py --dry-run
```

---

## 3. 출력 예시

```
[1/2] 정강+공약 Vector Store 생성 중...
  Vector Store 생성: vs_xxx, 인덱싱 대기 중...
  완료 (대기 12초)

[2/2] 지역별 공약 Vector Store 생성 중...
  Vector Store 생성: vs_yyy, 인덱싱 대기 중...
  완료 (대기 8초)

=== 완료 ===
.rag/registry.json 저장
.rag/vector_store_id.txt 저장
.rag/vector_store_regional_id.txt 저장 (지역별 공약이 있는 경우)
```

---

## 4. .env 설정

스크립트 실행 후 `.env`에 다음이 추가됩니다:

```env
OPENAI_API_KEY=sk-proj-...
USE_OPENAI_VECTOR_STORE=1
OPENAI_VECTOR_STORE_ID=vs_xxx
OPENAI_REGIONAL_VECTOR_STORE_ID=vs_yyy
```

`.env` 값은 **fallback**이며, 실제 런타임은 `.rag`의 ID를 우선 사용합니다.

---

## 5. 서버 시작

```bash
# 런타임은 ingest를 실행하지 않고 Vector Store ID만 사용
./restart_server.sh
# 또는
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

---

## 6. AWS 배포 시

1. **로컬**에서 스크립트 실행 → Vector Store 생성
2. `.rag/`에 저장된 ID를 서버 환경에 전달 (또는 `.env`에 ID 설정)
3. 서버 시작 → PDF 없이 즉시 검증 API 사용 가능

---

## 7. 한글 파일명

- OpenAI Files에는 영문 파일명으로 업로드 (예: `pledge_1___.txt`)
- 본문에 `원본파일: 1. 이준석 공약.pdf` 형태로 원본명 저장
- 한글 경로 이슈 없이 동작

---

## 8. GitHub Actions 자동 동기화 (git push 시)

`push to main` 시 변경된 PDF만 자동으로 Vector Store에 반영됩니다.

### 사전 설정 (1회)

1. **로컬**에서 `python scripts/ingest_vector_store.py` 실행
2. GitHub 저장소 → Settings → Secrets and variables → Actions 에 추가:
   - `OPENAI_API_KEY`: OpenAI API 키
   - `OPENAI_VECTOR_STORE_ID`: 정강+공약 Vector Store ID
   - `OPENAI_REGIONAL_VECTOR_STORE_ID`: 지역별 공약 Vector Store ID (선택)

### 동작

- `data/pdf/`, `scripts/`, `backend/` 변경 시 workflow 실행
- `python scripts/ingest_vector_store.py` 실행 → 변경된 PDF만 업로드

# Vector Store 업로드 스크립트

서버 시작 시 PDF 스캔 없이, **별도 스크립트로 1회 인덱싱** 후 `.env`에 저장된 Vector Store ID만 사용하는 방식입니다.

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
python scripts/index_pdfs_to_vector_store.py

# .env에 ID 자동 저장
python scripts/index_pdfs_to_vector_store.py --output-env

# 단일 Vector Store (정강+공약+지역별 통합)
python scripts/index_pdfs_to_vector_store.py --single-store --output-env
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
OPENAI_VECTOR_STORE_ID=vs_xxx
OPENAI_REGIONAL_VECTOR_STORE_ID=vs_yyy

.env에 저장됨: G:\...\Policy\.env
```

---

## 4. .env 설정

스크립트 실행 후 `.env`에 다음이 추가됩니다:

```env
OPENAI_API_KEY=sk-proj-...
USE_OPENAI_VECTOR_STORE=1
SKIP_PDF_SCAN_ON_STARTUP=1
OPENAI_VECTOR_STORE_ID=vs_xxx
OPENAI_REGIONAL_VECTOR_STORE_ID=vs_yyy
```

`--output-env` 없이 실행한 경우, 출력된 ID를 수동으로 `.env`에 추가합니다.

---

## 5. 서버 시작

```bash
# SKIP_PDF_SCAN_ON_STARTUP=1 이면 서버 시작 시 PDF 스캔 없음
# .env의 Vector Store ID만 사용
./restart_server.sh
# 또는
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

---

## 6. AWS 배포 시

1. **로컬**에서 스크립트 실행 → Vector Store 생성
2. 출력된 `OPENAI_VECTOR_STORE_ID`, `OPENAI_REGIONAL_VECTOR_STORE_ID`를 `.env`에 저장
3. `.env`를 AWS 서버에 복사 (`scp` 등)
4. `.env`에 `SKIP_PDF_SCAN_ON_STARTUP=1` 추가
5. 서버 시작 → PDF 없이 즉시 검증 API 사용 가능

---

## 7. 한글 파일명

- OpenAI Files에는 영문 파일명으로 업로드 (예: `pledge_1___.txt`)
- 본문에 `원본파일: 1. 이준석 공약.pdf` 형태로 원본명 저장
- 한글 경로 이슈 없이 동작

---

## 8. GitHub Actions 자동 동기화 (git push 시)

`push to main` 시 변경된 PDF만 자동으로 Vector Store에 반영됩니다.

### 사전 설정 (1회)

1. **로컬**에서 `python scripts/index_pdfs_to_vector_store.py --output-env` 실행
2. 생성된 `data/vector_store_manifest.json`, `data/vector_store_regional_manifest.json` **커밋**
3. GitHub 저장소 → Settings → Secrets and variables → Actions 에 추가:
   - `OPENAI_API_KEY`: OpenAI API 키
   - `OPENAI_VECTOR_STORE_ID`: 정강+공약 Vector Store ID
   - `OPENAI_REGIONAL_VECTOR_STORE_ID`: 지역별 공약 Vector Store ID (선택)

### 동작

- `data/pdf/`, `scripts/`, `backend/`, manifest 변경 시 workflow 실행
- `python scripts/sync_vector_store.py` 실행 → 변경된 PDF만 업로드
- manifest 갱신 시 자동 커밋·푸시 (`[skip ci]`로 재실행 방지)

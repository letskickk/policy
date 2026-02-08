# OpenAI Vector Store 사용법

FAISS 대신 OpenAI File Search를 사용하면 **인덱스 경로, EBS/EFS, INDEX_CACHE_DIR** 등 AWS 설정이 필요 없습니다.

---

## 1. .env 설정

```env
USE_OPENAI_VECTOR_STORE=1
```

**참고:** Vector Store 모드에서는 **Responses API**의 file_search를 사용합니다. `CHAT_MODEL`(기본값 gpt-5.2)을 그대로 사용할 수 있어 Assistants API와 달리 최신 모델을 지원합니다.

---

## 2. 서버 실행

```bash
# Windows
2_서버실행.bat

# 또는
uvicorn backend.main:app --reload
```

**첫 실행 시:**
- PDF 폴더(`data/pdf/정강정책`, `공약`, `지역별 공약`)를 스캔
- 각 PDF 텍스트 추출 후 OpenAI에 업로드
- Vector Store 생성 (Responses API file_search에서 사용)
- **2~5분** 정도 걸릴 수 있음 (PDF 개수에 따라)

로그 예시:
```
[VECTOR_STORE] PDF 30개 수집 중...
[VECTOR_STORE] Files API 업로드 완료: 30개
[VECTOR_STORE] 생성: vs_xxx
[VECTOR_STORE] 대기 중... status=in_progress
[VECTOR_STORE] 준비 완료
```

---

## 3. 재시작 시 빠르게 (선택)

매번 Vector Store를 새로 만들면 시작 시간이 오래 걸립니다.  
**첫 실행 로그에 나온 `vs_xxx`** 를 `.env`에 저장하면, 다음부터는 재생성 없이 바로 시작합니다.

```env
USE_OPENAI_VECTOR_STORE=1
OPENAI_VECTOR_STORE_ID=vs_xxxxxxxxxxxxxxxxxxxx
```

> **주의**: PDF를 추가/수정했다면 `OPENAI_VECTOR_STORE_ID`·`OPENAI_REGIONAL_VECTOR_STORE_ID`를 지우고 서버를 재시작해 새로 만들어야 합니다.

---

## 3-0. 공약·지역별 공약 분리 (타지역 유사성 검토 수정)

**공약** 폴더와 **지역별 공약** 폴더는 서로 다른 Vector Store에 저장됩니다.

- **정강+공약**: platform·pledges 채점 시 검색
- **지역별 공약**: 타지역 유사성·중복 검토(conflicts) 시 **이 store만** 검색

이전에 지역별 유사성 검토 시 공약 폴더를 잘못 읽던 문제가 해결됩니다.

---

## 3-1. 증분 업데이트 (변경된 PDF만 반영)

`OPENAI_VECTOR_STORE_ID`가 설정된 상태에서 서버를 시작하면, **새로 추가·수정된 PDF만** 업로드하고 **삭제된 PDF**는 Vector Store에서 제거합니다.

- `data/vector_store_manifest.json`에 파일별 해시가 저장됨 (내용 변경 감지)
- 로컬·AWS 상관없이 변경분만 동기화 → 전체 재업로드 불필요
- manifest가 없으면(첫 배포 등) 증분 동기화는 생략됨

**새 서버로 이전 시**: 이전 서버의 `data/vector_store_manifest.json`을 복사하면 증분 동기화가 동작합니다. 없으면 전체 재생성(`OPENAI_VECTOR_STORE_ID` 지우고 재시작)하면 됩니다.

---

## 4. API 사용

- **POST /check** : Vector Store 모드에서는 로컬 PDF 대신 file_search로 점검. `data/pdf/지역별 공약/` 폴더 의존 제거.
- **POST /api/pledge/verify** : 기존과 동일.

```bash
curl -X POST http://localhost:8000/api/pledge/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "지역 청년 일자리 1000개 창출"}'
```

### strict judge 모드 (`judge: true`)

evidence·specificity cap·QUERY/VERIFY 모드 적용:

```bash
curl -X POST http://localhost:8000/api/pledge/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "지역 청년 일자리 1000개 창출", "judge": true}'
```

응답 예시:
```json
{
  "status": "OK",
  "mode": "VERIFY",
  "duplication_score": 85,
  "ideology_fit_score": 80,
  "specificity_score": 65,
  "final_score": 75,
  "confidence": "MED",
  "missing_fields": [],
  "evidence": {"input_quotes": [...], "reference_quotes": [...]}
}
```

- 입력 ≤10자 또는 ≤3 토큰 → `mode: "QUERY"`, `final_score`/`ideology_fit_score` null
- `specificity_score < 30` → `final_score` 최대 70
- `specificity_score < 15` → `final_score` 최대 55

브라우저: http://127.0.0.1:8000/pledge

---

## 5. 비용

| 항목 | 비용 |
|------|------|
| **1 GB까지** | 무료 |
| **1 GB 초과** | $0.10 / GB / 일 |

PDF 30~200개(보통 수백 MB)면 **무료** 범위에서 사용 가능합니다.

---

## 6. FAISS로 되돌리기

```env
USE_OPENAI_VECTOR_STORE=0
```

또는 해당 줄을 삭제/주석 처리하면 됩니다.

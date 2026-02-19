## 문제 원인 (직책 safety vs recall 충돌)
- winners2022 비교(4번 섹션)에서 공약 유사도 검색 리콜이 낮아 실제 유사 공약도 `유사 공약: 없음`으로 떨어지는 오탐이 발생함.
- strict/region_only 단계 실패 시 빈 컨텍스트로 끝나, `공약 hit는 있었지만 메타 필터에서 탈락` 케이스를 구조적으로 놓침.
- 메타(이름/직책)가 비는 경우 근거 기반 복원이 부족해 출력 정확도가 낮아짐.

## 재현 케이스
- `경상남도 남해군수 장충남 / 망운산 산림휴양벨리 조성`
- 기존 로직에서 누락/없음 오탐이 발생 가능했음.

## 해결 전략
### 1) 검색(retrieval) — 공약 우선
- 5종+ 쿼리 생성:
  - 원문 pledge
  - 첫 줄/핵심 구문
  - 명사 중심 축약 키워드
  - region + 키워드
  - region + election_type + 키워드
  - 직책+지역 보조 쿼리
  - 백업 고정 쿼리(`제8회 지방선거 당선인 공약`)
- 다중 쿼리 결과를 텍스트 fingerprint 기반 dedup 후 재정렬(re-rank):
  - 벡터 score + 토큰 유사도 + 키워드 포함률

### 2) 필터/선택 — role-safe but recall-first
- 단계 순서 고정:
  1. 유사도 후보군 확보(직책 필터 미적용)
  2. 메타 보강(`_enhance_winners2022_hits`)
  3. role-safe 필터(`strict -> region_only`)
- strict/region_only 모두 비어도, 유사도 상위 중 명백한 직책 충돌이 없는 항목 1~2건은 컨텍스트로 유지.
- 교육감 사용자에게 시장/군수 등 명백한 직책 충돌은 최종 출력 금지.

### 3) 메타 복원
- canonical 메타 우선
- 근거발췌에서 `[직책 + 이름]` 정규식 추출
- 그래도 없으면 이름/직책 `확인불가`
- 최종 라인 포맷 보강:
  - `2022 / [직책] / [당선인명] / "[공약제목 또는 핵심 문구]"`

### 4) 없음 규칙 강화
- 유사 hit가 있고 role-safe 후보가 남으면 `유사 공약: 없음` 금지.
- 임계치 미달 + 보강 실패 + role-safe 후보 없음일 때만 `없음`.

## 변경 파일
- `backend/openai_vector_store.py`
- `tests/test_winners2022_meta_filter.py` (신규)

## 테스트 목록
- [A] 망운산 쿼리 생성에 region/election_type 힌트 포함
- [B] 다중 쿼리 dedup 후 공약 중심 후보 유지
- [C] education user_meta에서 mayor/local_mayor strict 탈락
- [D] strict/region_only 실패 시 role-safe 후보가 있으면 context 비지 않음
- [E] 유사 hit>=1이면 없음 금지 규칙
- [F] 이름/직책 미확보 시 `확인불가` fallback

## 실행 결과
- `python -m py_compile backend/openai_vector_store.py tests/test_winners2022_meta_filter.py` ✅
- `PYTHONPATH=. pytest -q tests/test_winners2022_meta_filter.py` ✅ (6 passed)
- `PYTHONPATH=. pytest -q tests/test_winners2022_*.py` ✅ (17 passed; PowerShell glob은 파일 목록 확장 방식으로 실행)
- `PYTHONPATH=. pytest -q` ⚠️ 5 failed / 23 passed
  - 실패 위치: `tests/test_candidates_api.py`
  - 실패 유형: `Query(None).strip()`, district_code 형식, sqlite FK
  - 본 PR 변경 범위와 무관(기존 실패)

## 리스크 및 후속 모니터링
- winners2022 단계별 로그 추가:
  - `query_count, raw_hits, dedup_hits, enhanced_hits, strict_hits, region_only_hits, final_selected`
- 남해군수/망운산 케이스 포함해 `final_selected=0` 샘플을 모니터링해 누락 단계 파악 권장.

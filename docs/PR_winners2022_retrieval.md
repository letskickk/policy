# PR: Fix winners2022 retrieval to improve role-safe recall

## PR 제목
Fix winners2022 retrieval to improve role-safe recall

---

## 문제 원인 (직책 safety vs recall 충돌)

- **직책 미스매칭**: 이전에 직책 필터를 강화한 뒤 교육감 사용자에게 시장/지사 공약이 섞이는 현상은 제거되었으나,
- **유사 공약 누락**: 동시에 "직책은 맞지만 유사 공약을 놓치는" 문제가 발생.
- 원인: (1) **region_only**에서 교육감/광역 선거인데도 city·sgg 조건으로 과도하게 탈락. (2) **단일 쿼리**만 사용해 벡터 검색 recall 부족. (3) strict/region_only 0건일 때 **no_filter** 대신 **같은 직책 재조회**를 시도하지 않고 곧바로 빈 문구만 사용.

---

## 해결 전략 (필터 / 폴백 / 쿼리 확장)

1. **필터**
   - strict: 교육감이면 `hit_position`에 "교육감" 포함된 경우만 통과 (기존 유지).
   - region_only: **광역단위**(교육감/광역단체장/광역의원)는 시도(province) 정합만 요구, sgg 비어 있어도 통과.
   - 기초단체장/기초의원은 기존처럼 city·sgg 정합 유지.

2. **폴백**
   - strict=0, region_only=0이고 **election_type이 있는 경우**: no_filter로 타 직책을 채우지 않음.
   - 대신 **같은 직책군 재조회** 1회: `직책+지역` 결합 쿼리로 벡터 검색 → strict 필터 적용.
   - 그래도 0건이면 "유사 공약: 없음"만 표시.
   - election_type이 **없는** 경우에만 기존처럼 no_filter fallback 허용.

3. **쿼리 확장**
   - winners2022 벡터 검색 시 **2~3개 변형 쿼리** 사용: 원문+당선인 공약, 직책+지역 결합.
   - 다중 쿼리 hit merge → dedup → 점수 정렬 후 **role-safe 필터** 유지.
   - API 경로·벡터 전용 경로 모두 적용.

---

## 회귀 테스트 목록

| ID | 시나리오 | 테스트명 |
|----|----------|----------|
| [A] | 교육감 user_meta + 시장/구청장 hit => strict False | `test_A_education_user_meta_mayor_gucheongjang_hit_strict_false` |
| [B] | 교육감 user_meta + 교육감 hit(시도 일치, sgg 비어있음) => region_only True | `test_B_education_hit_province_only_region_only_true` |
| [C] | election_type 존재 + strict/region_only 0건 => no_filter 미적용 | `test_C_election_type_present_no_filter_fallback_not_applied`, `test_C_position_region_query_built_when_election_type_exists` |
| [D] | 기초단체장/의원 city 정합 유지 | `test_D_local_mayor_council_city_match_required` |
| [E] | 다중 쿼리·광역 감지·최소 쿼리 수 | `test_E_region_level_election_detected_for_relaxed_region_only`, `test_E_min_queries_configured` |
| 재현 | 교육감에 시장 hit 미통과 / 교육감 hit region_only 통과 | `test_repro_education_gets_no_mayor_hit`, `test_repro_education_province_only_region_only_pass` |

---

## 리스크 및 후속 모니터링 포인트

- **리스크**: region_only 완화로 인해 광역 선거에서 “다른 시도” 후보가 드물게 섞일 수 있음. 현재는 시도(province) exact match 유지하므로 영향 제한적.
- **모니터링**: (1) 교육감 사용자에게 시장/지사 공약이 노출되는 사례 0건 유지. (2) 교육감·광역 사용자에서 "유사 공약: 없음" 비율이 과도하게 높지 않은지 로그/피드백 확인.

---

## 검증 커맨드 및 결과

```bash
python -m py_compile backend/openai_vector_store.py tests/test_winners2022_filtering.py
PYTHONPATH=. pytest tests/test_winners2022_filtering.py -v
```

- **test_winners2022_filtering.py**: 11 passed.
- **전체 pytest -q**: 17 passed, 5 failed. 실패 5건은 `test_candidates_api.py` (Query 인자/ district_code 형식/FK 제약)으로, 본 변경과 무관.

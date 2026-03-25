# TODOS

## P2: policy_ssot.py 백엔드 리팩토링
- **What:** N+1 풀스캔(list_policy_document_people) 수정, God Object 분리, API 엔드포인트 정리
- **Why:** 허브 프론트엔드 정리 후 백엔드가 다음 병목. 인코딩은 수정했지만 구조적 문제 잔존.
- **Effort:** L (human) → M (CC+gstack)
- **Priority:** P2
- **Depends on:** 허브 프론트엔드 재설계 완료
- **Context:** policy_ssot.py가 단일 파일에 모든 SSOT 로직을 담고 있음. list_policy_document_people()은 필터 없이 전체 테이블 반복 로드. 서비스 분리(positions, documents, people, polls)가 필요.

## P2: admin/candidates.html 페이징
- **What:** 전체 후보자 한 번에 로드 → limit/offset 기반 페이징
- **Why:** 후보자 1000+ 시 성능 문제. 현재 7명이라 당장은 안 급함.
- **Effort:** M (human) → S (CC+gstack)
- **Priority:** P2
- **Depends on:** 백엔드 API에 limit/offset 파라미터 추가 필요

## P3: DRY — escape 함수 통합
- **What:** safe(), esc(), escapeHtml() 등 6개 페이지에 중복 정의된 escape 함수를 공통 JS로 추출
- **Why:** hub-shared.js에 이미 safe() 있음. 나머지 페이지도 공유하면 중복 제거.
- **Effort:** M (human) → S (CC+gstack)
- **Priority:** P3

## P3: 인라인 CSS 과다 정리
- **What:** index.html(2328줄), map.html(3084줄), pledge.html(1751줄)의 인라인 CSS를 reform-redesign.css로 이관
- **Why:** CSS 유지보수 어려움, 일관성 부족
- **Effort:** L (human) → M (CC+gstack)
- **Priority:** P3

## P2: check_service.py 캐시 무효화
- **What:** 새 후보자 공약 추가 시 결과 캐시 무효화 로직 추가
- **Why:** 같은 공약 텍스트가 다른 후보자 공약 추가 후에도 이전 결과를 반환
- **Effort:** S (human) → S (CC+gstack)
- **Priority:** P2

## P3: quota_rate.py 인메모리 레이트리밋 개선
- **What:** 서버 재시작 시 레이트리밋 카운터 초기화되는 문제
- **Why:** 현재 사용량 적어 당장은 문제 없지만, 서버 재시작 직후 악용 가능
- **Effort:** M (human) → S (CC+gstack)
- **Priority:** P3

## P3: 비밀번호 재설정 기능
- **What:** 비밀번호 분실 시 이메일 기반 재설정 플로우 추가
- **Why:** 현재 비밀번호 분실 시 복구 불가
- **Effort:** M (human) → S (CC+gstack)
- **Priority:** P3

## P3: 접근성 개선
- **What:** ARIA 라벨, 키보드 네비게이션, 명암비 개선
- **Why:** 전체 페이지에 접근성 부족
- **Effort:** XL (human) → L (CC+gstack)
- **Priority:** P3

## P3: main.py God Object 분리
- **What:** 4,738줄 단일 파일을 기능별 라우터 모듈로 분리
- **Why:** 유지보수 어려움, 테스트 어려움
- **Effort:** XL (human) → L (CC+gstack)
- **Priority:** P3
- **Depends on:** 다른 작업과 충돌하지 않는 시점에

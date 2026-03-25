# TODOS

## P2: policy_ssot.py 백엔드 리팩토링
- **What:** N+1 풀스캔(list_policy_document_people) 수정, God Object 분리, API 엔드포인트 정리
- **Why:** 허브 프론트엔드 정리 후 백엔드가 다음 병목. 인코딩은 수정했지만 구조적 문제 잔존.
- **Effort:** L (human) → M (CC+gstack)
- **Priority:** P2
- **Depends on:** 허브 프론트엔드 재설계 완료
- **Context:** policy_ssot.py가 단일 파일에 모든 SSOT 로직을 담고 있음. list_policy_document_people()은 필터 없이 전체 테이블 반복 로드. 서비스 분리(positions, documents, people, polls)가 필요.

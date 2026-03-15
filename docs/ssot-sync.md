# SSOT Sync

운영 서버에서는 아래 순서로 SSOT 데이터를 갱신한다.

1. RallyPoint 논평 동기화
2. 국회 법안 동기화
3. 공약 PDF 동기화
4. 필요 시 논평 자동 연결

단일 실행:

```bash
./.venv/bin/python scripts/run_ssot_sync.py --auto-link --min-score 5
```

본문 없는 빠른 실행:

```bash
./.venv/bin/python scripts/run_ssot_sync.py --no-commentary-body
```

권장 운영 방식:

```bash
0 */6 * * * cd /home/ubuntu/Policy && ./.venv/bin/python scripts/run_ssot_sync.py --auto-link --min-score 5 >> /home/ubuntu/Policy/ssot-sync.log 2>&1
```

실행 후 확인할 엔드포인트:

```bash
curl -s http://127.0.0.1:8000/api/policy/hub
curl -s http://127.0.0.1:8000/api/policy/documents/21
curl -s http://127.0.0.1:8000/api/policy/people/%EC%9D%B4%EC%A4%80%EC%84%9D
```

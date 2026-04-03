# Planner Brief

- Round: 2
- Overall score: 97.25
- Pass: yes
- Loop: evaluator -> planner -> generator -> evaluator
- Strategy mode: narrow
- Architecture pattern: pipeline

## Priorities

- T1 Fix verify scoring thresholds
  action: Lower scores for query-like (8->3), authority-mismatch (18->5), vague-numeric (45->18); fix bridge UTF-8 encoding
  files: backend/analysis_service.py, harness/scripts/local_api_bridge.py

## Changes Made

1. `backend/analysis_service.py` - `_quick_verify_result()`:
   - query-like score: 8 -> 3 (bare topic query is not a pledge)
   - slogan score: 16 -> 8 (slogan without detail)
   - authority-mismatch score: 18 -> 5 (FTA/교육부 for local council)
   - vague-numeric score: 45 -> 18 (round number without execution detail)
   - concrete-numeric: already 68 (previous round fix)

2. `harness/scripts/local_api_bridge.py`:
   - Fixed stdin encoding: `sys.stdin.read()` -> `sys.stdin.buffer.read().decode("utf-8")`
   - Root cause: Node.js sends UTF-8, Python defaults to cp949 on Windows

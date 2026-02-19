"""
run_check를 직접 호출해 winners2022_context가 실제로 뭔지 로그로 확인.
"""
import logging, sys, os, re
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.exists():
    with open(env, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip().replace("\r", "")
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip().lstrip("\ufeff").strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    os.environ.setdefault(k, v)

from backend.rag_registry import get_vector_store_ids
from backend.openai_vector_store import run_check

p, r, w = get_vector_store_ids()
print("winners2022_id:", w)
print()

result = run_check(
    p, "서울 안심소득",
    regional_vector_store_id=r,
    winners2022_vector_store_id=w,
    max_results=10
)

m = re.search(r"4\..{0,20}당선인", result)
if m:
    start = m.start()
    end5 = result.find("5. 총평", start)
    sec4 = result[start: end5 if end5 > 0 else start + 800]
    print("===4번섹션===")
    print(sec4[:800])
else:
    print("4번 섹션 없음. 전체 결과 앞부분:")
    print(result[:500])

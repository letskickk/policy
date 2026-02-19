"""
winners2022 벡터 검색 결과와 최종 컨텍스트를 직접 출력해 4번 섹션 채워지는지 확인.
"""
import os, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

from openai import OpenAI
from backend.rag_registry import get_vector_store_ids
from backend.openai_vector_store import (
    _build_winners2022_queries_for_vector,
    _dedup_winners_vector_hits,
    rerank_winners_hits_by_similarity,
    RUN_CHECK_K_WINNERS,
    RUN_CHECK_MAX_WORKERS,
    RUN_CHECK_WINNERS_RAW_CAP,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
_, _, winners2022_id = get_vector_store_ids()
print("winners2022 store:", winners2022_id or "(없음)")
print()


def raw_search(vsid: str, query: str, k: int = 10):
    try:
        resp = client.vector_stores.search(vector_store_id=vsid, query=query, max_num_results=k)
        results = []
        for r in (getattr(resp, "data", None) or []):
            score = getattr(r, "score", 0.0) or 0.0
            filename = getattr(getattr(r, "attributes", None) or {}, "filename", "") or ""
            content = ""
            for blk in (getattr(r, "content", None) or []):
                t = getattr(blk, "text", None)
                if t:
                    content += (getattr(t, "value", None) or t if isinstance(t, str) else "")
            results.append((score, filename, content))
        return results
    except Exception as e:
        print("  검색 오류:", e)
        return []


for pledge in ["망운산 산림휴양밸리 조성", "서울 안심소득", "안심소득"]:
    print("=" * 60)
    print("공약:", pledge)
    print("=" * 60)
    if not winners2022_id:
        print("winners2022 store ID 없음")
        continue

    queries = _build_winners2022_queries_for_vector(pledge, None, max_queries=8)
    print(f"쿼리 {len(queries)}개:")
    for q in queries[:4]:
        print(f"  > {q}")

    all_hits = []
    with ThreadPoolExecutor(max_workers=RUN_CHECK_MAX_WORKERS) as ex:
        futs = [ex.submit(raw_search, winners2022_id, q, RUN_CHECK_K_WINNERS) for q in queries]
        for f in as_completed(futs):
            all_hits.extend(f.result())

    dedup = _dedup_winners_vector_hits(all_hits)[:RUN_CHECK_WINNERS_RAW_CAP]
    ranked = rerank_winners_hits_by_similarity(dedup, pledge)[:10]
    print(f"\n검색 결과 {len(all_hits)}건 → dedup {len(dedup)}건 → 상위 {len(ranked)}건")
    if ranked:
        for i, (score, fn, txt) in enumerate(ranked[:5]):
            print(f"  [{i+1}] score={score:.3f}  {txt[:100].replace(chr(10),' ')}")
    else:
        print("  결과 없음!")
    print()

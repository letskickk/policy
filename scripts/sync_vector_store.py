#!/usr/bin/env python3
"""
OpenAI Vector Store 증분 동기화.
변경된 PDF만 업로드, 삭제된 PDF는 Vector Store에서 제거.

사용법 (프로젝트 루트에서):
  python scripts/sync_vector_store.py

요구사항:
- .env 또는 환경변수: OPENAI_API_KEY, OPENAI_VECTOR_STORE_ID, [OPENAI_REGIONAL_VECTOR_STORE_ID]
- data/vector_store_manifest.json, data/vector_store_regional_manifest.json (index_pdfs_to_vector_store.py --output-env 실행 후 생성)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    vs_policy = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
    vs_regional = os.getenv("OPENAI_REGIONAL_VECTOR_STORE_ID", "").strip()

    if not api_key:
        print("오류: OPENAI_API_KEY가 설정되지 않았습니다.")
        return 1
    if not vs_policy:
        print("오류: OPENAI_VECTOR_STORE_ID가 설정되지 않았습니다.")
        print("  python scripts/index_pdfs_to_vector_store.py --output-env 를 먼저 실행하고 GitHub secrets에 ID를 추가하세요.")
        return 1

    from backend.openai_vector_store import (
        MANIFEST_PATH,
        MANIFEST_REGIONAL_PATH,
        sync_vector_store_incremental,
    )
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    print("[1/2] 정강+공약 Vector Store 동기화 중...")
    sync_vector_store_incremental(vs_policy, MANIFEST_PATH, ("platform", "pledge"))

    if vs_regional and MANIFEST_REGIONAL_PATH.exists():
        print("[2/2] 지역별 공약 Vector Store 동기화 중...")
        sync_vector_store_incremental(vs_regional, MANIFEST_REGIONAL_PATH, ("regional",))
    else:
        print("[2/2] 지역별 공약 Vector Store 생략 (ID 또는 manifest 없음)")

    # Vector Store 상태 출력
    print("\n=== Vector Store 상태 ===")
    for label, vs_id in [("정강+공약", vs_policy), ("지역별 공약", vs_regional)]:
        if not vs_id:
            continue
        try:
            vs = client.vector_stores.retrieve(vs_id)
            status = getattr(vs, "status", "unknown")
            file_count = getattr(vs, "file_counts", None)
            info = f"  {label}: {vs_id} | status={status}"
            if file_count:
                info += f" | files={getattr(file_count, 'completed', '?')}"
            print(info)
        except Exception as e:
            print(f"  {label}: {vs_id} | 오류: {e}")

    print("\n동기화 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

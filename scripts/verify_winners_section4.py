"""
run_check를 직접 호출해 4번 섹션(이전 당선인 공약)이 채워지는지 검증.
사용: 프로젝트 루트에서 python scripts/verify_winners_section4.py
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env 로드
env_path = ROOT / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8-sig") as f:
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
import backend.prompts as _prompts_mod

# 컨텍스트 인터셉터: build_user_message가 받는 winners2022 컨텍스트를 출력
_orig_build = _prompts_mod.build_user_message
def _patched_build(*args, **kwargs):
    w = kwargs.get("winners2022_pledges_context") or (args[3] if len(args) > 3 else "")
    print("  [winners2022_context 전달값 첫200자]:", repr((w or "")[:200]))
    return _orig_build(*args, **kwargs)
_prompts_mod.build_user_message = _patched_build


def extract_section4(text: str) -> str:
    """결과 텍스트에서 '4. 이전 당선인' ~ '5. 총평' 또는 '6. 수정' 사이 추출."""
    if not text:
        return ""
    start = re.search(r"4\.\s*이전\s*당선인.*?(?:공약\s*과의\s*비교)?", text, re.IGNORECASE)
    if not start:
        return ""
    begin = start.start()
    end_m5 = text.find("5. 총평", begin)
    end_m6 = text.find("6. 수정", begin)
    end = len(text)
    if end_m5 >= 0:
        end = end_m5
    if end_m6 >= 0 and end_m6 < end:
        end = end_m6
    return text[begin:end].strip()


def main():
    policy_id, regional_id, winners2022_id = get_vector_store_ids()
    print("Vector store IDs:")
    print("  policy:", policy_id or "(empty)")
    print("  regional:", regional_id or "(empty)")
    print("  winners2022:", winners2022_id or "(empty)")
    print()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음. .env 확인 후 재실행.")
        return 1

    test_pledges = [
        "망운산 산림휴양밸리 조성",
        "서울 안심소득",
    ]

    for pledge in test_pledges:
        print("=" * 60)
        print("공약:", pledge)
        print("=" * 60)
        try:
            result = run_check(
                policy_id or "",
                pledge,
                regional_vector_store_id=regional_id or "",
                winners2022_vector_store_id=winners2022_id or "",
                max_results=10,
                user_meta=None,
            )
            sec4 = extract_section4(result)
            def _safe(s):
                return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            if "유사 공약 : 없음" in sec4 or (sec4 and "없음" in sec4 and "2022 /" not in sec4):
                print("[4번 섹션] *** 유사 공약: 없음 으로 나옴 ***")
            else:
                print("[4번 섹션] 내용 있음:")
                chunk = sec4[:1200] if len(sec4) > 1200 else sec4
                print(_safe(chunk))
                if len(sec4) > 1200:
                    print("... (생략)")
            print()
        except Exception as e:
            print("오류:", e)
            import traceback
            traceback.print_exc()
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

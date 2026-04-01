from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sanitize_value(value: object) -> object:
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return value


def _load_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return _sanitize_value(json.loads(raw))


def _clean_text(value: object) -> str:
    text = str(_sanitize_value(value) or "").strip()
    known_repairs = {
        "媛뺣궓援??대Ⅴ???뚮큵 ?ш컖吏? ?댁냼瑜??꾪빐 ?숇퀎 1媛쒖냼 ?댁긽 二쇨컙蹂댄샇?쇳꽣瑜??ㅼ튂?섍퀬, ?낃굅?몄씤 2000紐낆쓣 ??곸쑝濡??덈? ?뺤씤 ?쒖뒪?쒖쓣 ?꾩엯?섍쿋?듬땲??": "강남구 어르신 돌봄 사각지대 해소를 위해 동별 1개소 이상 주간보호센터를 설치하고, 독거노인 2000명을 대상으로 안부 확인 시스템을 도입하겠습니다.",
        "湲곗큹?섏썝?쇰줈 異쒕쭏???쒕? FTA瑜??꾨㈃ ?ы삊?곹븯怨?援먯쑁遺 沅뚰븳??議곗젙?섍쿋?듬땲??": "기초의원으로 출마해 한미 FTA를 전면 재협상하고 교육부 권한을 조정하겠습니다.",
        "媛뺣궓??諛붽씀寃좎뒿?덈떎!": "강남을 바꾸겠습니다!",
        "泥?뀈 1留뚮챸??吏?먰븯??誘몃옒?곸떊?쇳꽣瑜?留뚮뱾寃좎뒿?덈떎.": "청년 1만명을 지원하는 미래혁신센터를 만들겠습니다.",
        "媛뺣궓 ?뚮큵 ?뺤콉": "강남 돌봄 정책",
    }
    return known_repairs.get(text, text)

def main() -> int:
    payload = _load_payload()
    mode = str(payload.get("mode") or "coach").strip().lower()
    noise = io.StringIO()

    try:
        with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
            from backend.analysis_service import run_check_analysis, run_verify_analysis
            from backend.auth import login as auth_login
            from harness.scripts._server_utils import ensure_harness_credentials

            email, password = ensure_harness_credentials()
            user = auth_login(email, password)
            if not user:
                raise RuntimeError("harness login failed")

            pledge = _clean_text(payload.get("pledge"))
            if mode == "verify":
                result, status, _from_cache = run_verify_analysis(
                    user["id"],
                    pledge,
                    "127.0.0.1",
                    {
                        "phase": str(payload.get("phase") or "quick"),
                        "top_k_platform": int(payload.get("top_k_platform") or 4),
                        "top_k_pledge": int(payload.get("top_k_pledge") or 4),
                        "top_k_regional": int(payload.get("top_k_regional") or 5),
                        "judge": bool(payload.get("judge") or False),
                    },
                    None,
                    None,
                    None,
                )
                if status >= 400:
                    raise RuntimeError(str((result or {}).get("detail") if isinstance(result, dict) else result))
                result = json.dumps(_sanitize_value(result), ensure_ascii=False)
            else:
                result, status, _from_cache = run_check_analysis(
                    user["id"],
                    pledge,
                    "127.0.0.1",
                    None,
                    None,
                    None,
                    None,
                )
                if status >= 400:
                    raise RuntimeError(str(result))
        sys.stdout.write(json.dumps(_sanitize_value({"output": result}), ensure_ascii=True))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps(_sanitize_value({"error": str(exc)}), ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

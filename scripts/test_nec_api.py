#!/usr/bin/env python3
"""선관위 API 키 테스트 (당선인정보 v3.11 / 선거공약).

.env에 DATA_GO_KR_API_KEY=인증키 설정 후 실행.
당선인 API: https://apis.data.go.kr/9760000/WinnerInfoInqireService2
"""
import urllib.request
import urllib.parse
import json
import sys
from pathlib import Path

# 프로젝트 루트에서 .env 로드
ROOT = Path(__file__).resolve().parent.parent
if (ROOT / ".env").exists():
    with open(ROOT / ".env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATA_GO_KR_API_KEY=") and "=" in line:
                KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        else:
            KEY = ""
else:
    KEY = ""

if not KEY:
    KEY = __import__("os").environ.get("DATA_GO_KR_API_KEY", "").strip()


def _require_key():
    if not KEY:
        print("DATA_GO_KR_API_KEY가 없습니다. .env에 추가하거나 환경변수로 설정하세요.", file=sys.stderr)
        sys.exit(1)


def call(url, params, desc=""):
    full = url + "?" + urllib.parse.urlencode(params)
    print(f"\n[요청] {desc or url[:80]}")
    try:
        req = urllib.request.Request(
            full,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0", "Referer": "https://www.data.go.kr/"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8")
            print(f"[응답] HTTP {r.status}")
            data = json.loads(body) if body.strip().startswith("{") else body
            if isinstance(data, dict):
                body = (data.get("response") or {}).get("body") or data
                if isinstance(body, dict) and "totalCount" in body:
                    print(f"[totalCount] {body.get('totalCount')}")
            print(body[:2000])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[오류] HTTP {e.code}: {e.reason}")
        print(body[:500])
    except Exception as e:
        print(f"[오류] {type(e).__name__}: {e}")


if __name__ == "__main__":
    _require_key()

    print("=" * 60)
    print("1. 당선인 정보 API (WinnerInfoInqireService2) — 제8회 지방선거 시도지사")
    call(
        "https://apis.data.go.kr/9760000/WinnerInfoInqireService2/getWinnerInfoInqire",
        {
            "serviceKey": KEY,
            "pageNo": "1",
            "numOfRows": "5",
            "sgId": "20220601",
            "sgTypecode": "3",
            "_type": "json",
        },
        desc="당선인 조회 sgId=20220601, sgTypecode=3",
    )

    print("\n" + "=" * 60)
    print("2. 선거공약 API (ElecPrmsInfoInqireService) — cnddtId 필요")
    call(
        "https://apis.data.go.kr/9760000/ElecPrmsInfoInqireService/getCnddtElecPrmsInfoInqire",
        {
            "serviceKey": KEY,
            "pageNo": "1",
            "numOfRows": "3",
            "sgId": "20220601",
            "sgTypecode": "3",
            "cnddtId": "1",
            "_type": "json",
        },
        desc="공약 조회 (cnddtId=1은 예시, 실제로는 당선인 API huboid 사용)",
    )

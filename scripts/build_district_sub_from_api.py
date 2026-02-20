#!/usr/bin/env python3
"""
지방선거(제8회 전국동시지방선거) 광역의원·기초의원 세부선거구를
공공데이터포털 '중앙선거관리위원회_코드정보' API로 받아 district_sub_map.json 을 생성합니다.

필요: .env 에 DATA_GO_KR_API_KEY=발급받은인증키 설정
API 활용신청: https://www.data.go.kr/data/15000897/openapi.do
401 발생 시: 마이페이지 인증키 관리에서 '일반 인증키(Decoding)' 사용, 활용승인 직후면 수 분 후 재시도.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# 프로젝트 루트
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SG_ID = "20220601"  # 제8회 전국동시지방선거
# sgTypecode: 4=기초자치단체장, 5=시도의원(광역의원), 6=구시군의원(기초의원)
SG_TYPE_CODES = (4, 5, 6)
API_URL = "https://apis.data.go.kr/9760000/CommonCodeService/getCommonSggCodeList"


def load_env_key() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "DATA_GO_KR_API_KEY":
            return v.strip().strip('"').strip("'").replace("\r", "").replace("\n", "")
    return ""


def sd_name_to_region_code(region_map: dict) -> dict[str, str]:
    """시도명/별칭 -> region_code 매핑."""
    out = {}
    for r in region_map.get("regions", []):
        code = str(r.get("region_code", "")).strip()
        name = str(r.get("region_name", "")).strip()
        if code and name:
            out[name] = code
            for a in r.get("aliases", []) or []:
                out[str(a).strip()] = code
    return out


def fetch_page(service_key: str, sg_id: str, sg_typecode: int, page_no: int = 1, num_of_rows: int = 1000) -> dict:
    # 공공데이터포털: 인증키를 쿼리 파라미터에 포함 (영문/숫자만 있으면 인코딩 생략)
    rest = urlencode({
        "sgId": sg_id,
        "sgTypecode": sg_typecode,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "resultType": "json",
    })
    url = f"{API_URL}?ServiceKey={service_key}&{rest}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.data.go.kr/",
        },
    )
    try:
        with urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401 and e.fp:
            try:
                msg = e.fp.read().decode("utf-8", errors="replace")[:400]
                print(f"[401 응답] {msg}", file=sys.stderr)
            except Exception:
                pass
        raise
    except OSError:
        raise


def get_items(data: dict) -> list[dict]:
    body = data.get("response", {}).get("body", {}) or data.get("body", {})
    items = body.get("items") or body.get("item")
    if items is None:
        return []
    if isinstance(items, dict):
        items = items.get("item")
    if items is None:
        return []
    return items if isinstance(items, list) else [items]


def normalize_wiw(s: str) -> str:
    """구시군명 공백 제거 (district_map.json 키와 맞추기)."""
    return "".join((s or "").split())


def extract_sub_name(sgg_name: str, wiw_name: str) -> str:
    """선거구명에서 시군구명 뒤 세부선거구(가/나/다 또는 제1선거구 등) 추출."""
    sgg = (sgg_name or "").strip()
    wiw = (wiw_name or "").strip()
    if not sgg:
        return "단독"
    if not wiw or wiw == sgg:
        return "단독"
    if sgg.startswith(wiw):
        sub = sgg[len(wiw) :].strip()
        return sub if sub else "단독"
    # wiw가 약칭일 수 있음 (예: 수원시 장안구 -> sgg "수원시장안구 제1선거구")
    if wiw in sgg:
        sub = sgg.replace(wiw, "", 1).strip()
        return sub if sub else "단독"
    return sgg


def main() -> int:
    key = os.environ.get("DATA_GO_KR_API_KEY") or load_env_key()
    if not key:
        print("DATA_GO_KR_API_KEY가 없습니다. .env에 추가하거나 환경변수로 설정하세요.", file=sys.stderr)
        print("API 활용신청: https://www.data.go.kr/data/15000897/openapi.do", file=sys.stderr)
        return 1

    region_path = DATA_DIR / "region_map.json"
    if not region_path.exists():
        print(f"region_map.json 없음: {region_path}", file=sys.stderr)
        return 1
    region_map = json.loads(region_path.read_text(encoding="utf-8"))
    sd_to_code = sd_name_to_region_code(region_map)

    # key: "region_code:구시군명(공백제거)", value: set of sub names
    subs_by_key: dict[str, set[str]] = defaultdict(set)

    for idx, sg_typecode in enumerate(SG_TYPE_CODES):
        if idx > 0:
            time.sleep(1.5)  # 요청 간격 두어 401/제한 완화
        page = 1
        while True:
            try:
                data = fetch_page(key, SG_ID, sg_typecode, page_no=page, num_of_rows=500)
            except Exception as e:
                print(f"API 오류 (sgTypecode={sg_typecode}, page={page}): {e}", file=sys.stderr)
                break
            items = get_items(data)
            if not items:
                break
            for it in items:
                sd_name = (it.get("sdName") or it.get("SD_NAME") or "").strip()
                wiw_name = (it.get("wiwName") or it.get("WIW_NAME") or "").strip()
                sgg_name = (it.get("sggName") or it.get("SGG_NAME") or "").strip()
                region_code = sd_to_code.get(sd_name)
                if not region_code:
                    continue
                wiw_norm = normalize_wiw(wiw_name) or wiw_name
                if not wiw_norm:
                    continue
                key_str = f"{region_code}:{wiw_norm}"
                sub = extract_sub_name(sgg_name, wiw_name)
                subs_by_key[key_str].add(sub)
            # 다음 페이지: 500개 미만 받으면 끝, 500개면 다음 페이지 요청
            if len(items) < 500:
                break
            page += 1
            time.sleep(0.3)  # 연속 요청 간격

    # API 응답만 사용. API에 없는 시군구는 채우지 않음 (백엔드에서 해당 시군구 선택 시 기본값 반환)
    # 단독선거구는 "단독" 1개만 두기
    out_subs = {}
    for k, v in sorted(subs_by_key.items()):
        arr = sorted(v)
        if not arr:
            arr = ["단독"]
        elif len(arr) == 1 and arr[0] in ("", "단독"):
            arr = ["단독"]
        out_subs[k] = arr

    out = {
        "note": "지방선거(제8회 전국동시지방선거) 기초자치단체장·시도의원·구시군의원 세부선거구. 공공데이터 API getCommonSggCodeList(sgId=20220601, sgTypecode 4·5·6)로 생성.",
        "subs": out_subs,
    }
    out_path = DATA_DIR / "district_sub_map.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"총 {len(out_subs)}개 시군구, {sum(len(v) for v in out_subs.values())}개 세부선거구 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
선거구-동 매핑 데이터 전수조사 스크립트
district_dong_map.json의 오류를 검증합니다.

검증 항목:
1. 동 중복 배정 검사: 같은 구/시/군 내에서 같은 동이 2개 이상 선거구에 배정
2. 선거구명 검증: 중앙선관위 API의 공식 선거구 목록과 비교
3. 빈 선거구/동 리스트 검사
4. regional_council vs local_council 정합성 검사
"""

import json
import urllib.request
import urllib.parse
import sys
import io
import time
from collections import defaultdict

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DATA_PATH = "C:/policy/data/district_dong_map.json"
API_KEY = "53927682678011d850b5f1b4cc7ccec79013b08082301bf831a4f369b19fe974"
API_BASE = "https://apis.data.go.kr/9760000/CommonCodeService/getCommonSggCodeList"


def load_data():
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_api_districts(sg_typecode, page_size=200):
    """중앙선관위 API에서 선거구 목록을 가져옵니다."""
    all_items = []
    page = 1
    while True:
        params = urllib.parse.urlencode({
            "ServiceKey": API_KEY,
            "sgId": "20220601",
            "sgTypecode": str(sg_typecode),
            "pageNo": str(page),
            "numOfRows": str(page_size),
            "resultType": "json",
        })
        url = f"{API_BASE}?{params}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            body = data["response"]["body"]
            items = body["items"]["item"]
            if isinstance(items, dict):
                items = [items]
            all_items.extend(items)
            total = body["totalCount"]
            if len(all_items) >= total:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  API 오류 (sgTypecode={sg_typecode}, page={page}): {e}")
            break
    return all_items


def check_dong_duplicates_local(data):
    """local_council: 같은 구/시/군 내에서 동이 여러 선거구에 중복 배정되었는지 검사"""
    issues = []
    for sido, gugun_dict in data["local_council"].items():
        for gugun, districts in gugun_dict.items():
            dong_to_districts = defaultdict(list)
            for district_name, dong_list in districts.items():
                for dong in dong_list:
                    dong_to_districts[dong].append(district_name)
            for dong, assigned_districts in dong_to_districts.items():
                if len(assigned_districts) > 1:
                    issues.append({
                        "type": "동 중복 배정 (기초의원)",
                        "sido": sido,
                        "gugun": gugun,
                        "dong": dong,
                        "districts": assigned_districts,
                    })
    return issues


def check_dong_duplicates_regional(data):
    """regional_council: 같은 시도 내에서 동이 여러 선거구에 중복 배정되었는지 검사"""
    issues = []
    for sido, districts in data["regional_council"].items():
        dong_to_districts = defaultdict(list)
        for district_name, dong_list in districts.items():
            for dong in dong_list:
                dong_to_districts[dong].append(district_name)
        for dong, assigned_districts in dong_to_districts.items():
            if len(assigned_districts) > 1:
                issues.append({
                    "type": "동 중복 배정 (광역의원)",
                    "sido": sido,
                    "dong": dong,
                    "districts": assigned_districts,
                })
    return issues


def check_empty_entries(data):
    """빈 선거구나 빈 동 리스트 검사"""
    issues = []
    for sido, gugun_dict in data["local_council"].items():
        for gugun, districts in gugun_dict.items():
            if not districts:
                issues.append({"type": "빈 선거구 목록 (기초)", "sido": sido, "gugun": gugun})
            for dname, dongs in districts.items():
                if not dongs:
                    issues.append({"type": "빈 동 리스트 (기초)", "sido": sido, "gugun": gugun, "district": dname})

    for sido, districts in data["regional_council"].items():
        if not districts:
            issues.append({"type": "빈 선거구 목록 (광역)", "sido": sido})
        for dname, dongs in districts.items():
            if not dongs:
                issues.append({"type": "빈 동 리스트 (광역)", "sido": sido, "district": dname})
    return issues


def normalize_district_name(sgg_name, wiw_name):
    """API의 sggName에서 구/시/군 이름을 제거하고 선거구명만 추출"""
    # e.g. "종로구가선거구" -> "가선거구"
    if wiw_name and sgg_name.startswith(wiw_name):
        return sgg_name[len(wiw_name):]
    return sgg_name


def validate_against_api_local(data):
    """기초의원(sgTypecode=6) 선거구명을 API와 비교"""
    print("\n[API] 기초의원 선거구 목록 로딩 중...")
    api_items = fetch_api_districts(6)
    print(f"  API에서 {len(api_items)}개 선거구 로드됨")

    # Build API lookup: sido -> wiw -> set of district names
    api_lookup = defaultdict(lambda: defaultdict(set))
    api_raw = defaultdict(lambda: defaultdict(list))
    for item in api_items:
        sd = item["sdName"]
        wiw = item["wiwName"]
        sgg = item["sggName"]
        district_suffix = normalize_district_name(sgg, wiw)
        api_lookup[sd][wiw].add(district_suffix)
        api_raw[sd][wiw].append(sgg)

    issues = []

    # Compare
    for sido, gugun_dict in data["local_council"].items():
        if sido not in api_lookup:
            issues.append({"type": "시도 불일치 (기초)", "sido": sido, "detail": "API에 없는 시도"})
            continue
        for gugun, districts in gugun_dict.items():
            if gugun not in api_lookup[sido]:
                issues.append({
                    "type": "구시군 불일치 (기초)",
                    "sido": sido,
                    "gugun": gugun,
                    "detail": f"API에 없는 구시군. API에 있는 구시군: {sorted(api_lookup[sido].keys())}",
                })
                continue

            api_districts = api_lookup[sido][gugun]
            json_districts = set(districts.keys())

            # JSON에는 있지만 API에는 없는 선거구
            for d in json_districts - api_districts:
                issues.append({
                    "type": "선거구명 불일치 (기초)",
                    "sido": sido,
                    "gugun": gugun,
                    "district": d,
                    "detail": f"JSON에만 존재. API 선거구: {sorted(api_districts)}",
                })

            # API에는 있지만 JSON에는 없는 선거구
            for d in api_districts - json_districts:
                issues.append({
                    "type": "선거구 누락 (기초)",
                    "sido": sido,
                    "gugun": gugun,
                    "district": d,
                    "detail": f"API에는 있으나 JSON에 없음",
                })

    # Check API districts not in JSON at sido level
    for sd in api_lookup:
        if sd not in data["local_council"]:
            issues.append({"type": "시도 누락 (기초)", "sido": sd, "detail": "API에는 있으나 JSON에 없음"})

    return issues, api_lookup


def validate_against_api_regional(data):
    """광역의원(sgTypecode=4) 선거구명을 API와 비교"""
    print("\n[API] 광역의원 선거구 목록 로딩 중...")
    api_items = fetch_api_districts(4)
    print(f"  API에서 {len(api_items)}개 선거구 로드됨")

    # Build API lookup: sido -> set of sggName
    api_lookup = defaultdict(set)
    api_details = defaultdict(list)
    for item in api_items:
        sd = item["sdName"]
        sgg = item["sggName"]
        api_lookup[sd].add(sgg)
        api_details[sd].append(sgg)

    issues = []

    for sido, districts in data["regional_council"].items():
        if sido not in api_lookup:
            issues.append({"type": "시도 불일치 (광역)", "sido": sido, "detail": "API에 없는 시도"})
            continue

        json_districts = set(districts.keys())
        api_districts = api_lookup[sido]

        for d in json_districts - api_districts:
            # Try partial match
            partial = [a for a in api_districts if d in a or a in d]
            detail = f"JSON에만 존재. API 선거구 목록에 없음."
            if partial:
                detail += f" 유사: {partial}"
            issues.append({
                "type": "선거구명 불일치 (광역)",
                "sido": sido,
                "district": d,
                "detail": detail,
            })

        for d in api_districts - json_districts:
            partial = [j for j in json_districts if d in j or j in d]
            detail = f"API에는 있으나 JSON에 없음."
            if partial:
                detail += f" 유사: {partial}"
            issues.append({
                "type": "선거구 누락 (광역)",
                "sido": sido,
                "district": d,
                "detail": detail,
            })

    for sd in api_lookup:
        if sd not in data["regional_council"]:
            issues.append({"type": "시도 누락 (광역)", "sido": sd, "detail": "API에는 있으나 JSON에 없음"})

    return issues


def check_regional_local_consistency(data):
    """regional_council의 동이 local_council에도 존재하는지 교차 확인"""
    issues = []

    for sido, regional_districts in data["regional_council"].items():
        # Collect all local dongs for this sido
        local_dongs = set()
        if sido in data["local_council"]:
            for gugun, districts in data["local_council"][sido].items():
                for dname, dongs in districts.items():
                    local_dongs.update(dongs)

        # Check each regional dong
        for district_name, dong_list in regional_districts.items():
            for dong in dong_list:
                if local_dongs and dong not in local_dongs:
                    # Only flag if we have local data for this sido
                    issues.append({
                        "type": "광역-기초 동명 불일치",
                        "sido": sido,
                        "regional_district": district_name,
                        "dong": dong,
                        "detail": "광역의원 선거구의 동이 기초의원 선거구에 없음",
                    })

    return issues


def check_local_coverage(data):
    """local_council 내에서 가/나/다... 선거구가 연속적인지, 빈 선거구가 있는지"""
    issues = []
    ga_na_da = list("가나다라마바사아자차카타파하")

    for sido, gugun_dict in data["local_council"].items():
        for gugun, districts in gugun_dict.items():
            # Extract district letters
            district_letters = []
            for dname in districts.keys():
                if dname.endswith("선거구") and len(dname) >= 4:
                    letter = dname.replace("선거구", "")
                    if letter in ga_na_da:
                        district_letters.append(letter)

            if not district_letters:
                continue

            # Check continuity
            indices = sorted([ga_na_da.index(l) for l in district_letters])
            expected = list(range(indices[0], indices[-1] + 1))
            if indices != expected:
                missing = [ga_na_da[i] + "선거구" for i in expected if i not in indices]
                if missing:
                    issues.append({
                        "type": "선거구 연번 불연속 (기초)",
                        "sido": sido,
                        "gugun": gugun,
                        "existing": sorted(districts.keys()),
                        "missing": missing,
                    })

    return issues


def main():
    print("=" * 70)
    print("선거구-동 매핑 데이터 전수조사")
    print("=" * 70)

    data = load_data()

    all_issues = []

    # 1. 동 중복 배정 검사
    print("\n[1] 동 중복 배정 검사 (기초의원)...")
    issues = check_dong_duplicates_local(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    print("\n[2] 동 중복 배정 검사 (광역의원)...")
    issues = check_dong_duplicates_regional(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # 2. 빈 항목 검사
    print("\n[3] 빈 선거구/동 리스트 검사...")
    issues = check_empty_entries(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # 3. 선거구 연번 연속성 검사
    print("\n[4] 선거구 연번 연속성 검사 (기초)...")
    issues = check_local_coverage(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # 4. 광역-기초 동명 교차 검증
    print("\n[5] 광역-기초 동명 교차 검증...")
    issues = check_regional_local_consistency(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # 5. API 검증: 기초의원
    print("\n[6] 중앙선관위 API 검증 (기초의원)...")
    issues, api_local = validate_against_api_local(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # 6. API 검증: 광역의원
    print("\n[7] 중앙선관위 API 검증 (광역의원)...")
    issues = validate_against_api_regional(data)
    print(f"  발견: {len(issues)}건")
    all_issues.extend(issues)

    # Summary
    print("\n" + "=" * 70)
    print(f"총 발견된 이슈: {len(all_issues)}건")
    print("=" * 70)

    if not all_issues:
        print("문제 없음!")
        return

    # Group by type
    by_type = defaultdict(list)
    for issue in all_issues:
        by_type[issue["type"]].append(issue)

    for issue_type, items in sorted(by_type.items()):
        print(f"\n{'─' * 60}")
        print(f"[{issue_type}] — {len(items)}건")
        print(f"{'─' * 60}")
        for item in items:
            parts = []
            for k, v in item.items():
                if k == "type":
                    continue
                parts.append(f"{k}={v}")
            print(f"  • {', '.join(parts)}")


if __name__ == "__main__":
    main()

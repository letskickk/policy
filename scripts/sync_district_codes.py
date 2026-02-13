#!/usr/bin/env python3
"""
district_map.json을 district_codes 테이블로 동기화.

실행:
    python scripts/sync_district_codes.py
    python scripts/sync_district_codes.py --map-path data/district_map.json
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import DB_PATH, init_db  # noqa: E402


DEFAULT_MAP_PATH = ROOT / "data" / "district_map.json"
DEFAULT_REGION_MAP_PATH = ROOT / "data" / "region_map.json"


def _normalize_text(value: str) -> str:
    return "".join(ch for ch in (value or "").strip().lower() if ch not in " \t\r\n-_/().,")


def _build_region_name_to_code(region_map_path: Path) -> dict[str, str]:
    data = json.loads(region_map_path.read_text(encoding="utf-8"))
    regions = data.get("regions", [])
    mapping: dict[str, str] = {}
    for item in regions:
        code = str(item["region_code"]).strip()
        names = [str(item.get("region_name", "")).strip()] + [str(a).strip() for a in item.get("aliases", [])]
        for name in names:
            if name:
                mapping[_normalize_text(name)] = code
    return mapping


def _to_district_code(region_code: str, district_name: str) -> str:
    compact = "".join(ch for ch in district_name if ch not in " \t\r\n")
    return f"{region_code}:{compact}"


def load_map(path: Path, region_map_path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # format A: explicit flat districts
    items = data.get("districts", [])
    if items:
        return items

    # format B: cosmosfarm style {"data":[{"서울특별시":["종로구", ...]}, ...]}
    raw_groups = data.get("data", [])
    if not raw_groups:
        raise ValueError(f"district_map.json 포맷이 올바르지 않습니다: {path}")

    region_name_to_code = _build_region_name_to_code(region_map_path)
    expanded: list[dict] = []
    for row in raw_groups:
        if not isinstance(row, dict) or len(row) != 1:
            continue
        region_name, district_names = next(iter(row.items()))
        region_code = region_name_to_code.get(_normalize_text(str(region_name)))
        if not region_code:
            continue
        if not district_names:
            # 세종처럼 하위 시군구가 없는 경우 자기 자신을 선거구로 취급
            district_name = str(region_name).replace("특별자치시", "시")
            expanded.append(
                {
                    "district_code": _to_district_code(region_code, district_name),
                    "district_name": district_name,
                    "region_code": region_code,
                    "election_type": "local",
                    "aliases": [district_name, f"{region_name} {district_name}"],
                }
            )
            continue
        for district_name in district_names:
            dname = str(district_name).strip()
            if not dname:
                continue
            expanded.append(
                {
                    "district_code": _to_district_code(region_code, dname),
                    "district_name": dname,
                    "region_code": region_code,
                    "election_type": "local",
                    "aliases": [dname, f"{region_name} {dname}"],
                }
            )
    if not expanded:
        raise ValueError(f"district_map.json에서 확장 가능한 데이터가 없습니다: {path}")
    return expanded


def sync(map_path: Path, db_path: Path, region_map_path: Path) -> dict[str, int]:
    init_db()
    districts = load_map(map_path, region_map_path)

    conn = sqlite3.connect(str(db_path))
    try:
        inserted_or_updated = 0
        for item in districts:
            district_code = str(item["district_code"]).strip()
            district_name = str(item["district_name"]).strip()
            region_code = str(item["region_code"]).strip()
            election_type = str(item.get("election_type", "local")).strip() or "local"
            aliases_json = json.dumps(item.get("aliases", []), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO district_codes (
                    district_code, district_name, region_code, election_type, aliases_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(district_code) DO UPDATE SET
                    district_name = excluded.district_name,
                    region_code = excluded.region_code,
                    election_type = excluded.election_type,
                    aliases_json = excluded.aliases_json,
                    updated_at = datetime('now')
                """,
                (district_code, district_name, region_code, election_type, aliases_json),
            )
            inserted_or_updated += 1
        conn.commit()
        return {"rows_synced": inserted_or_updated}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="district_codes 동기화")
    parser.add_argument("--map-path", type=Path, default=DEFAULT_MAP_PATH)
    parser.add_argument("--region-map-path", type=Path, default=DEFAULT_REGION_MAP_PATH)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()

    result = sync(args.map_path, args.db_path, args.region_map_path)
    print(f"district_codes 동기화 완료: {result['rows_synced']}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

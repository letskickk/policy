#!/usr/bin/env python3
"""
후보 데이터의 region_code 정규화 스크립트.

기능:
1) region_map.json 기준 매핑 테이블(region_codes) 동기화
2) 후보 데이터(candidates)의 문자열 지역값(region/region_name/district_name)을 분석해 region_code 채우기
3) 검증 리포트(성공률/미매핑 목록) 생성

실행:
    python scripts/normalize_candidate_regions.py
    python scripts/normalize_candidate_regions.py --dry-run
"""
import argparse
import difflib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import DB_PATH, init_db  # noqa: E402

DEFAULT_MAP_PATH = ROOT / "data" / "region_map.json"
DEFAULT_REPORT_PATH = ROOT / "data" / "reports" / "region_normalization_report.json"
DEFAULT_UNMAPPED_PATH = ROOT / "data" / "reports" / "region_unmapped_items.json"


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    # 지역 문자열 매칭용 최소 정규화
    text = re.sub(r"[\s\-\._,/()]+", "", text)
    return text


def _load_region_map(map_path: Path) -> list[dict[str, Any]]:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    regions = data.get("regions", [])
    if not regions:
        raise ValueError(f"region_map.json이 비어 있습니다: {map_path}")
    return regions


def _ensure_region_codes_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS region_codes (
            region_code TEXT PRIMARY KEY,
            region_name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _sync_region_codes(conn: sqlite3.Connection, regions: list[dict[str, Any]]) -> None:
    _ensure_region_codes_table(conn)
    for region in regions:
        code = str(region["region_code"]).strip()
        name = str(region["region_name"]).strip()
        aliases = region.get("aliases", [])
        conn.execute(
            """
            INSERT INTO region_codes (region_code, region_name, aliases_json, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(region_code) DO UPDATE SET
              region_name=excluded.region_name,
              aliases_json=excluded.aliases_json,
              updated_at=datetime('now')
            """,
            (code, name, json.dumps(aliases, ensure_ascii=False)),
        )


def _get_candidate_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(candidates)").fetchall()
    return {row[1] for row in rows}


def _build_alias_rules(regions: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], set[str]]:
    alias_rules: list[tuple[str, str]] = []
    valid_codes: set[str] = set()
    for region in regions:
        code = str(region["region_code"]).strip()
        valid_codes.add(code)
        aliases = [str(a).strip() for a in region.get("aliases", []) if str(a).strip()]
        aliases.append(str(region["region_name"]).strip())
        aliases.append(code)
        dedup = {_normalize_text(a) for a in aliases if a}
        for alias in dedup:
            alias_rules.append((alias, code))
    # "경기도" vs "경기" 같은 케이스를 위해 긴 문자열 우선
    alias_rules.sort(key=lambda x: len(x[0]), reverse=True)
    return alias_rules, valid_codes


def _infer_region_code(raw_region_text: str, alias_rules: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    norm = _normalize_text(raw_region_text)
    if not norm:
        return None, None
    for alias, code in alias_rules:
        if not alias:
            continue
        if alias == norm or alias in norm:
            return code, alias
    return None, None


def _suggest_region_codes(
    raw_region_text: str,
    alias_rules: list[tuple[str, str]],
    max_items: int = 3,
) -> list[dict[str, Any]]:
    norm = _normalize_text(raw_region_text)
    if not norm:
        return []

    best_by_code: dict[str, tuple[str, float]] = {}
    for alias, code in alias_rules:
        if not alias:
            continue
        score = difflib.SequenceMatcher(None, norm, alias).ratio()
        prev = best_by_code.get(code)
        if prev is None or score > prev[1]:
            best_by_code[code] = (alias, score)

    ranked = sorted(best_by_code.items(), key=lambda x: x[1][1], reverse=True)[:max_items]
    return [
        {
            "region_code": code,
            "matched_alias": alias,
            "score": round(score, 4),
        }
        for code, (alias, score) in ranked
    ]


def run(db_path: Path, map_path: Path, report_path: Path, unmapped_path: Path, dry_run: bool) -> dict[str, Any]:
    init_db()
    regions = _load_region_map(map_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _sync_region_codes(conn, regions)
        columns = _get_candidate_columns(conn)
        alias_rules, valid_codes = _build_alias_rules(regions)

        source_cols = [c for c in ("region", "region_name", "district_name") if c in columns]
        select_cols = ["id", "name", "region_code"] + source_cols
        query = f"SELECT {', '.join(select_cols)} FROM candidates ORDER BY id ASC"
        rows = conn.execute(query).fetchall()

        total = len(rows)
        already_valid = 0
        mapped_from_text = 0
        updated_rows = 0
        unchanged_valid = 0
        unmapped_items: list[dict[str, Any]] = []

        for row in rows:
            candidate_id = int(row["id"])
            name = row["name"]
            current_code = (row["region_code"] or "").strip()

            # 분석 대상 문자열: region/region_name 우선, 없으면 district_name
            raw_region_text = ""
            for col in source_cols:
                value = (row[col] or "").strip()
                if value:
                    raw_region_text = value
                    break

            if current_code in valid_codes:
                already_valid += 1
                unchanged_valid += 1
                continue

            mapped_code, matched_alias = _infer_region_code(raw_region_text, alias_rules)
            if mapped_code:
                mapped_from_text += 1
                if current_code != mapped_code:
                    updated_rows += 1
                    if not dry_run:
                        conn.execute(
                            "UPDATE candidates SET region_code = ?, updated_at = datetime('now') WHERE id = ?",
                            (mapped_code, candidate_id),
                        )
            else:
                suggestions = _suggest_region_codes(raw_region_text, alias_rules, max_items=3)
                unmapped_items.append(
                    {
                        "candidate_id": candidate_id,
                        "name": name,
                        "region_code_current": current_code,
                        "region_text_raw": raw_region_text,
                        "matched_alias": matched_alias,
                        "suggested_region_codes": suggestions,
                    }
                )

        if not dry_run:
            conn.commit()
        else:
            conn.rollback()

        success_count = already_valid + mapped_from_text
        success_rate = round((success_count / total) * 100, 2) if total else 100.0
        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "db_path": str(db_path),
            "map_path": str(map_path),
            "candidate_total": total,
            "already_valid_region_code": already_valid,
            "mapped_from_region_text": mapped_from_text,
            "updated_rows": updated_rows,
            "mapping_success_rate_percent": success_rate,
            "unmapped_count": len(unmapped_items),
            "source_columns_used": source_cols,
            "unmapped_items_path": str(unmapped_path),
        }

        report_path.parent.mkdir(parents=True, exist_ok=True)
        unmapped_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        unmapped_path.write_text(json.dumps(unmapped_items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"report": report, "unmapped_items": unmapped_items}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="후보 region_code 정규화")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="대상 SQLite DB 경로")
    parser.add_argument("--map-path", type=Path, default=DEFAULT_MAP_PATH, help="매핑 JSON 경로")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH, help="리포트 저장 경로")
    parser.add_argument("--unmapped-path", type=Path, default=DEFAULT_UNMAPPED_PATH, help="미매핑 목록 저장 경로")
    parser.add_argument("--dry-run", action="store_true", help="DB 업데이트 없이 분석만 수행")
    args = parser.parse_args()

    result = run(
        db_path=args.db_path,
        map_path=args.map_path,
        report_path=args.report_path,
        unmapped_path=args.unmapped_path,
        dry_run=args.dry_run,
    )
    report = result["report"]
    print("=== region_code 정규화 리포트 ===")
    print(f"대상 후보 수: {report['candidate_total']}")
    print(f"이미 유효 코드: {report['already_valid_region_code']}")
    print(f"문자열 매핑 성공: {report['mapped_from_region_text']}")
    print(f"업데이트 행 수: {report['updated_rows']}")
    print(f"매핑 성공률: {report['mapping_success_rate_percent']}%")
    print(f"미매핑 수: {report['unmapped_count']}")
    print(f"리포트: {args.report_path}")
    print(f"미매핑 목록: {args.unmapped_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

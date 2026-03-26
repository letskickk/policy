#!/usr/bin/env python3
"""
이슈 레이더 배치 실행 스크립트.

사용법:
    python scripts/run_issue_radar.py
    python scripts/run_issue_radar.py --days 30
    python scripts/run_issue_radar.py --doc-type bill
    python scripts/run_issue_radar.py --json  # JSON 출력

cron 예시:
    0 6 * * 1 cd /home/ubuntu/Policy && ./.venv/bin/python scripts/run_issue_radar.py >> /home/ubuntu/Policy/issue-radar.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.issue_radar import run_issue_scan, save_scan_result


def main() -> None:
    parser = argparse.ArgumentParser(description="이슈 레이더 — 정책 사각지대 탐지")
    parser.add_argument("--days", type=int, default=None, help="최근 N일 문서만 스캔")
    parser.add_argument("--doc-type", type=str, default=None, help="특정 문서 유형만 (bill, statement 등)")
    parser.add_argument("--json", action="store_true", help="JSON 형식으로 출력")
    parser.add_argument("--no-cache", action="store_true", help="결과를 캐시에 저장하지 않음")
    args = parser.parse_args()

    print("=== 이슈 레이더 실행 ===")
    print()

    report = run_issue_scan(
        days_back=args.days,
        doc_type_filter=args.doc_type,
    )

    if not args.no_cache:
        save_scan_result(report)
        print("[캐시 저장 완료]")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Human-readable output
    summary = report["coverage_summary"]
    print(f"스캔 시각: {report['scan_time']}")
    print(f"분석 문서: {report['total_documents']}건")
    print(f"승인 포지션: {report['total_positions']}건")
    print()
    print("── 커버리지 요약 ──")
    print(f"  충분히 커버됨: {summary['well_covered']}건")
    print(f"  약한 커버리지: {summary['weak_coverage']}건")
    print(f"  사각지대:      {summary['no_position']}건")
    print()

    if not report["gaps"]:
        print("사각지대가 발견되지 않았습니다.")
        return

    print(f"── 발견된 사각지대 ({len(report['gaps'])}개 주제) ──")
    print()

    for i, gap in enumerate(report["gaps"], 1):
        gap_label = "사각지대" if gap["gap_type"] == "no_position" else "약한 커버리지"
        print(f"  {i}. [{gap_label}] {gap['topic']} (우선순위: {gap['priority_score']:.0f})")
        print(f"     관련 문서 {gap['document_count']}건", end="")
        if gap["recent_bill_count"]:
            print(f", 최근 법안 {gap['recent_bill_count']}건", end="")
        if gap["recent_commentary_count"]:
            print(f", 최근 논평 {gap['recent_commentary_count']}건", end="")
        print()

        for doc in gap["documents"][:5]:
            score_info = f"(매칭 {doc['best_match_score']}점"
            if doc["best_match_title"]:
                score_info += f", → {doc['best_match_title']}"
            score_info += ")"
            print(f"     - [{doc['doc_type']}] {doc['title']} {score_info}")
        if gap["document_count"] > 5:
            print(f"     ... 외 {gap['document_count'] - 5}건")
        print()


if __name__ == "__main__":
    main()

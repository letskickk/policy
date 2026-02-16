#!/usr/bin/env python3
"""2022 당선인 PDF 텍스트 정리 테스트"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.pdf_loader import extract_text_from_file, clean_text_noise

pdf_dir = ROOT / "data" / "pdf" / "8회 당선인 공약"
files = list(pdf_dir.glob("*.pdf"))

if not files:
    print("PDF 파일이 없습니다.")
    sys.exit(1)

f = files[0]
print(f"파일: {f.name}")
print(f"경로: {f}")

# 추출
text_raw = extract_text_from_file(f)
print(f"\n추출 길이(정리 전): {len(text_raw):,}자")

# 정리
text_clean = clean_text_noise(text_raw)
print(f"추출 길이(정리 후): {len(text_clean):,}자")
print(f"제거된 문자 수: {len(text_raw) - len(text_clean):,}자")

# 샘플 출력
print("\n=== 헤더 샘플 (처음 500자) ===")
print(text_clean[:500] if text_clean else "(없음)")

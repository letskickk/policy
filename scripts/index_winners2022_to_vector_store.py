#!/usr/bin/env python3
"""
2022(제8회 지방선거) 당선인 공약 PDF만 업로드 → OpenAI Vector Store 생성.

대상 폴더:
  data/pdf/8회 당선인 공약/

사용법 (프로젝트 루트에서):
  python scripts/index_winners2022_to_vector_store.py
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from openai import OpenAI

PDF_DIR = ROOT / "data" / "pdf"
TARGET_FOLDER_NAME = "8회 당선인 공약"
TARGET_DIR = PDF_DIR / TARGET_FOLDER_NAME
MANIFEST_PATH = ROOT / "data" / "vector_store_winners2022_manifest.json"


def _nfc(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", s) if s else s


def _safe_filename(name: str) -> str:
    base = Path(name).stem[:60]
    safe = re.sub(r"[^\w\-]", "_", base)
    return (safe or "doc") + ".txt"


def _compact_spaced_hangul(text: str) -> str:
    if not text:
        return text
    return re.sub(
        r"((?:[가-힣]\s+){1,}[가-힣])",
        lambda m: re.sub(r"\s+", "", m.group(1)),
        text,
    )


def _create_txt_content(doc_path: Path) -> str | None:
    try:
        from backend.pdf_loader import extract_text_from_file, clean_text_noise

        text = extract_text_from_file(doc_path)
        text = clean_text_noise(text)  # OCR/목차 노이즈 제거
        text = (text or "").strip()
        if len(text) < 10:
            return None
        try:
            rel = doc_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = doc_path.name
        
        # 문서 메타 섹션 강화: 검색 시 메타 hit 가능성 증가
        # 텍스트 앞부분에서 이름/직책/지역 추출 시도
        name_candidates = []
        position_candidates = []
        region_candidates = []
        
        # 간단한 패턴으로 메타 추출 시도 (첫 9000자 + 한글 간격 정규화본)
        preview = text[:9000]
        preview_compact = _compact_spaced_hangul(preview)
        preview_scan = f"{preview}\n{preview_compact}"

        # 이름 후보: 시도명+이름, 직책 인접 이름, 당선인 표기
        name_matches = re.findall(
            r"(?:서울특별시|서울시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)\s*([가-힣]{2,4})",
            preview_scan,
        )
        if not name_matches:
            name_matches = re.findall(
                r"([가-힣]{2,4})\s*(?:특별시장|광역시장|시장|도지사|구청장|군수|교육감|의원|당선인)",
                preview_scan,
            )
        if not name_matches:
            name_matches = re.findall(r"(?:당선인|후보|성명|이름)\s*[:：]?\s*([가-힣]{2,4})", preview_scan)
        if name_matches:
            name_candidates = list(set(name_matches[:5]))  # 중복 제거 후 최대 5개
        
        # 직책 후보
        position_matches = re.findall(
            r"(서울특별시장|부산광역시장|대구광역시장|인천광역시장|광주광역시장|대전광역시장|울산광역시장|세종특별자치시장|경기도지사|강원도지사|충청북도지사|충청남도지사|전라북도지사|전라남도지사|경상북도지사|경상남도지사|제주특별자치도지사|특별시장|광역시장|시장|도지사|구청장|군수|교육감|의원)",
            preview_scan,
        )
        if position_matches:
            position_candidates = list(set(position_matches[:5]))
        
        # 지역 후보
        region_matches = re.findall(
            r"(서울|서울특별시|부산|부산광역시|대구|대구광역시|인천|인천광역시|광주|광주광역시|대전|대전광역시|울산|울산광역시|세종|세종특별자치시|경기|경기도|강원|강원도|충북|충청북도|충남|충청남도|전북|전라북도|전남|전라남도|경북|경상북도|경남|경상남도|제주|제주특별자치도|[가-힣]+(?:구|시|군))",
            preview_scan,
        )
        if region_matches:
            region_candidates = list(set(region_matches[:5]))
        
        # 메타 헤더 구성
        meta_lines = [
            "[문서 메타]",
            "선거: 제8회 전국동시지방선거 (2022)",
            "직책 후보군: " + (", ".join(position_candidates) if position_candidates else "확인 필요"),
            "지역 후보군: " + (", ".join(region_candidates) if region_candidates else "확인 필요"),
            "이름 후보: " + (", ".join(name_candidates) if name_candidates else "확인 필요"),
            ""
        ]
        
        header = "[2022당선인공약] 제8회 지방선거 당선인 공약 (비교/벤치마킹)"
        return f"{header}\n출처: {source_path}\n원본파일: {doc_path.name}\n\n" + "\n".join(meta_lines) + f"\n{text}"
    except Exception as e:
        print(f"  [WARN] extract failed {doc_path.name}: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="2022 당선인 공약 → OpenAI Vector Store 인덱싱")
    parser.add_argument("--store-name", default="winners-2022-pledges-store", help="Vector Store name")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("오류: OPENAI_API_KEY가 .env에 없습니다.")
        return 1

    target = PDF_DIR / _nfc(TARGET_FOLDER_NAME)
    if not target.exists():
        print(f"오류: 폴더가 없습니다: {target}")
        return 1

    from backend.pdf_loader import _iter_doc_files

    files = list(_iter_doc_files(target))
    if not files:
        print(f"오류: 폴더에 문서가 없습니다: {target}")
        return 1

    print(f"[1/4] 문서 {len(files)}개 발견: {[p.name for p in files]}")

    client = OpenAI(api_key=api_key)

    # build temp txt files
    import tempfile

    entries: list[tuple[str, str, str]] = []  # (rel, content, upload_name)
    t0 = time.time()
    for i, p in enumerate(files):
        print(f"      [{i+1}/{len(files)}] 텍스트 추출 중: {p.name} ...", end="", flush=True)
        content = _create_txt_content(p)
        elapsed = int(time.time() - t0)
        if not content:
            print(f" 스킵 (빈 내용)")
            continue
        try:
            rel = str(p.relative_to(PDF_DIR)).replace("\\", "/")
        except ValueError:
            rel = p.name
        entries.append((rel, content, _safe_filename(p.name)))
        print(f" 완료 ({len(content):,}자, 누적 {elapsed}초)")

    if not entries:
        print("오류: 텍스트 추출 결과가 비어 있습니다. (스캔 PDF거나 추출 실패)")
        return 1

    print(f"[2/4] OpenAI 업로드 중 ({len(entries)}개) ...", flush=True)
    file_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdirp = Path(tmpdir)
        for j, (rel, content, filename) in enumerate(entries):
            path = tmpdirp / filename
            path.write_text(content, encoding="utf-8")
            with open(path, "rb") as f:
                fobj = client.files.create(file=f, purpose="assistants")
                file_ids.append(fobj.id)
            print(f"      [{j+1}/{len(entries)}] 업로드 완료: {filename}", flush=True)

    print(f"[3/4] Vector Store 생성 중 ...", flush=True)
    vs = client.vector_stores.create(name=args.store_name, file_ids=file_ids)
    vs_id = vs.id
    print(f"      생성됨: {vs_id}")
    print(f"[4/4] Vector Store 인덱싱 대기 중 (최대 30분) ...", flush=True)

    # wait up to ~30min (900 * 2s)
    for i in range(900):
        vs = client.vector_stores.retrieve(vs_id)
        status = getattr(vs, "status", "unknown")
        if status == "completed":
            print(f"      완료 (대기 {i*2}초)")
            break
        if status == "failed":
            raise RuntimeError(f"Vector Store 인덱싱 실패: {vs_id}")
        if i > 0 and i % 15 == 0:
            print(f"      ... {i*2}초 경과 (status={status})", flush=True)
        time.sleep(2)
    else:
        raise RuntimeError("Vector Store 처리 타임아웃 (1800초)")

    # manifest 저장
    manifest = {"vector_store_id": vs_id, "files": {}}
    for idx, (rel, content, _) in enumerate(entries):
        if idx < len(file_ids):
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            manifest["files"][rel] = {"file_id": file_ids[idx], "content_hash": ch}
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest 저장: {MANIFEST_PATH.name}")

    # .rag/에 저장 (policy, regional과 동일 형식)
    from backend.rag_registry import write_vector_store_ids

    write_vector_store_ids(policy_id="", regional_id="", winners2022_id=vs_id)
    print(f".rag/vector_store_winners2022_id.txt 저장")

    print("\n=== 완료 ===")
    print(f"Vector Store ID: {vs_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


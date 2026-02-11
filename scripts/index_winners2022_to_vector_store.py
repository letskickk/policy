#!/usr/bin/env python3
"""
2022(제8회 지방선거) 당선인 공약 PDF만 업로드 → OpenAI Vector Store 생성.

대상 폴더:
  data/pdf/8회 당선인 공약/

사용법 (프로젝트 루트에서):
  python scripts/index_winners2022_to_vector_store.py
  python scripts/index_winners2022_to_vector_store.py --output-env
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


def _create_txt_content(doc_path: Path) -> str | None:
    try:
        from backend.pdf_loader import extract_text_from_file

        text = extract_text_from_file(doc_path)
        text = (text or "").strip()
        if len(text) < 10:
            return None
        try:
            rel = doc_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = doc_path.name
        header = "[2022당선인공약] 제8회 지방선거 당선인 공약 (비교/벤치마킹)"
        return f"{header}\n출처: {source_path}\n원본파일: {doc_path.name}\n\n{text}"
    except Exception as e:
        print(f"  [WARN] extract failed {doc_path.name}: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="2022 당선인 공약 → OpenAI Vector Store 인덱싱")
    parser.add_argument("--output-env", action="store_true", help=".env에 ID 자동 추가")
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

    print("\n=== 완료 ===")
    print(f"OPENAI_WINNERS2022_VECTOR_STORE_ID={vs_id}")

    if args.output_env:
        env_path = ROOT / ".env"
        text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        key = "OPENAI_WINNERS2022_VECTOR_STORE_ID"
        if f"{key}=" in text:
            lines = []
            for line in text.splitlines():
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={vs_id}")
                else:
                    lines.append(line)
            text = "\n".join(lines) + "\n"
        else:
            text = text.rstrip() + f"\n\n{key}={vs_id}\n"
        env_path.write_text(text, encoding="utf-8")
        print(f".env에 저장됨: {env_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


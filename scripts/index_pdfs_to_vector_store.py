#!/usr/bin/env python3
"""
PDF 일괄 업로드 → OpenAI Vector Store 생성 → .env에 ID 저장.

사용법 (프로젝트 루트에서):
  python scripts/index_pdfs_to_vector_store.py

또는:
  python -m scripts.index_pdfs_to_vector_store

요구사항:
- .env에 OPENAI_API_KEY 설정
- data/pdf/ 정강정책, 공약, 지역별 공약 폴더에 PDF 배치

한글 파일명 이슈 회피: OpenAI Files에는 영문 파일명으로 업로드 (메타데이터에 원명 보관).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# 프로젝트 루트를 path에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from openai import OpenAI

MANIFEST_PATH = ROOT / "data" / "vector_store_manifest.json"
MANIFEST_REGIONAL_PATH = ROOT / "data" / "vector_store_regional_manifest.json"


def _nfc(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s) if s else s


PDF_DIR = ROOT / "data" / "pdf"
FOLDERS = [
    ("platform", "정강정책"),
    ("pledge", "공약"),
    ("regional", "지역별 공약"),
]
CATEGORY_HEADER = {
    "platform": "[정강정책] 우리당 강령·정책 원칙",
    "pledge": "[공약] 우리당 중앙 공약 (일반공약)",
    "regional": "[지역별공약] 타지역 출마자 공약 (비교·중복 검토용)",
}


def _safe_filename(name: str) -> str:
    """한글/특수문자 제거, 영문+숫자+하이픈만."""
    base = Path(name).stem[:60]
    safe = re.sub(r"[^\w\-]", "_", base)
    return (safe or "doc") + ".txt"


def _collect_pdf_paths():
    from backend.pdf_loader import _iter_doc_files
    result = []
    for cat, folder_name in FOLDERS:
        dir_path = PDF_DIR / _nfc(folder_name)
        if not dir_path.exists():
            continue
        for p in _iter_doc_files(dir_path):
            result.append((cat, p, folder_name))
    return result


def _create_txt_content(doc_path: Path, category: str) -> str | None:
    try:
        from backend.pdf_loader import extract_text_from_file
        text = extract_text_from_file(doc_path)
        if not (text or "").strip() or len(text.strip()) < 10:
            return None
        header = CATEGORY_HEADER.get(category, "")
        try:
            rel = doc_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = doc_path.name
        return f"{header}\n출처: {source_path}\n원본파일: {doc_path.name}\n\n{text.strip()}"
    except Exception as e:
        print(f"  [경고] 문서 추출 실패 {doc_path.name}: {e}")
        return None


def _upload_and_create(client: OpenAI, pairs: list, store_name: str, manifest_path: Path) -> str:
    """업로드 후 Vector Store 생성. manifest에 (rel, file_id, content_hash) 기록."""
    entries = []  # (rel, content, safe_name)
    for cat, p, folder_name in pairs:
        content = _create_txt_content(p, cat)
        if content:
            try:
                rel = str(p.relative_to(PDF_DIR)).replace("\\", "/")
            except ValueError:
                rel = p.name
            safe_name = _safe_filename(p.name)
            entries.append((rel, content, f"{cat}_{safe_name}"))
    if not entries:
        return ""

    import tempfile
    file_ids = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for rel, content, filename in entries:
            path = Path(tmpdir) / filename
            path.write_text(content, encoding="utf-8")
            with open(path, "rb") as f:
                fobj = client.files.create(file=f, purpose="assistants")
                file_ids.append(fobj.id)

    vs = client.vector_stores.create(name=store_name, file_ids=file_ids)
    vs_id = vs.id
    print(f"  Vector Store 생성: {vs_id}, 인덱싱 대기 중...")
    for i in range(90):
        vs = client.vector_stores.retrieve(vs_id)
        if vs.status == "completed":
            print(f"  완료 (대기 {i*2}초)")
            break
        if vs.status == "failed":
            raise RuntimeError(f"Vector Store 인덱싱 실패: {vs_id}")
        time.sleep(2)
    else:
        raise RuntimeError("Vector Store 처리 타임아웃 (180초)")

    manifest = {"vector_store_id": vs_id, "files": {}}
    for idx, (rel, content, _) in enumerate(entries):
        if idx < len(file_ids):
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            manifest["files"][rel] = {"file_id": file_ids[idx], "content_hash": ch}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  manifest 저장: {manifest_path.name}")
    return vs_id


def main():
    parser = argparse.ArgumentParser(description="PDF → OpenAI Vector Store 인덱싱")
    parser.add_argument("--output-env", action="store_true", help=".env에 ID 자동 추가")
    parser.add_argument("--single-store", action="store_true", help="단일 Vector Store (정강+공약+지역별 통합)")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("오류: OPENAI_API_KEY가 .env에 없습니다.")
        sys.exit(1)

    pairs = _collect_pdf_paths()
    policy_pairs = [(c, p, fn) for c, p, fn in pairs if c in ("platform", "pledge")]
    regional_pairs = [(c, p, fn) for c, p, fn in pairs if c == "regional"]

    if not policy_pairs:
        print("오류: data/pdf/ 정강정책 또는 공약 폴더에 PDF가 없습니다.")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print("[1/2] 정강+공약 Vector Store 생성 중...")
    vs_policy = _upload_and_create(client, policy_pairs, "policy-rag-store", MANIFEST_PATH)

    vs_regional = ""
    if regional_pairs and not args.single_store:
        print("[2/2] 지역별 공약 Vector Store 생성 중...")
        vs_regional = _upload_and_create(client, regional_pairs, "regional-pledge-store", MANIFEST_REGIONAL_PATH)
    elif regional_pairs and args.single_store:
        print("[2/2] 단일 통합 Store 사용 (regional 포함)")
        regional_file_ids = []
        regional_entries = []
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for cat, p, _ in regional_pairs:
                content = _create_txt_content(p, cat)
                if content:
                    try:
                        rel = str(p.relative_to(PDF_DIR)).replace("\\", "/")
                    except ValueError:
                        rel = p.name
                    path = Path(tmpdir) / _safe_filename(p.name)
                    path.write_text(content, encoding="utf-8")
                    with open(path, "rb") as f:
                        fid = client.files.create(file=f, purpose="assistants").id
                        regional_file_ids.append(fid)
                    ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                    regional_entries.append((rel, ch, fid))
        if regional_file_ids:
            batch = client.vector_stores.file_batches.create(vector_store_id=vs_policy, file_ids=regional_file_ids)
            for _ in range(60):
                b = client.vector_stores.file_batches.retrieve(vector_store_id=vs_policy, batch_id=batch.id)
                if b.status == "completed":
                    break
                time.sleep(2)
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            for rel, ch, fid in regional_entries:
                manifest["files"][rel] = {"file_id": fid, "content_hash": ch}
            MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 완료 ===")
    print(f"OPENAI_VECTOR_STORE_ID={vs_policy}")
    if vs_regional:
        print(f"OPENAI_REGIONAL_VECTOR_STORE_ID={vs_regional}")

    if args.output_env:
        env_path = ROOT / ".env"
        text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        updates = {"OPENAI_VECTOR_STORE_ID": vs_policy}
        if vs_regional:
            updates["OPENAI_REGIONAL_VECTOR_STORE_ID"] = vs_regional
        for key, val in updates.items():
            if f"{key}=" in text:
                lines = []
                for line in text.splitlines():
                    if line.strip().startswith(f"{key}="):
                        lines.append(f"{key}={val}")
                    else:
                        lines.append(line)
                text = "\n".join(lines) + "\n"
            else:
                text = text.rstrip() + f"\n\n{key}={val}\n"
        env_path.write_text(text, encoding="utf-8")
        print(f"\n.env에 저장됨: {env_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

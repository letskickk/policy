#!/usr/bin/env python3
"""
PDF/TXT ingest → OpenAI Vector Store 업로드/인덱싱.

원칙:
- 서버 런타임에서는 ingest 금지. 이 스크립트로만 수행.
- 동일 파일은 sha256(file bytes)로 중복 방지.
- 변경된 파일만 업로드/인덱싱.
- vector_store_id는 .rag/registry.json 및 .rag/vector_store_id*.txt에 영구 저장.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from openai import OpenAI

from backend.config import PDF_DIR, _nfc
from backend.pdf_loader import _iter_doc_files, extract_text_from_file
from backend.rag_registry import (
    load_registry,
    save_registry,
    write_vector_store_ids,
)


FOLDERS = [
    ("platform", "정강정책", "policy"),
    ("pledge", "공약", "policy"),
    ("regional", "지역별 공약", "regional"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(name: str) -> str:
    base = Path(name).stem[:60]
    safe = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in base)
    return (safe or "doc") + ".txt"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _create_txt_content(doc_path: Path, category: str) -> str | None:
    try:
        text = extract_text_from_file(doc_path)
        if not (text or "").strip() or len(text.strip()) < 10:
            return None
        header = {
            "platform": "[정강정책] 우리당 강령·정책 원칙",
            "pledge": "[공약] 우리당 중앙 공약 (일반공약)",
            "regional": "[지역별공약] 타지역 출마자 공약 (비교·중복 검토용)",
        }.get(category, "")
        try:
            rel = doc_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = doc_path.name
        marker = f"{header}\n출처: {source_path}\n원본파일: {doc_path.name}"

        # 중요: OpenAI가 파일을 청크로 자를 때, 중간 청크에는 헤더/출처 라인이 없을 수 있음.
        # 섹션별(폴더별) 분리를 안정적으로 하기 위해 marker를 본문에도 주기적으로 삽입한다.
        lines = (text or "").strip().splitlines()
        blocks: list[str] = []
        buf: list[str] = []
        buf_chars = 0
        # 너무 촘촘하면 토큰이 과도해지고, 너무 띄우면 청크에서 출처가 빠질 수 있음 → 중간값
        target_chars = 1200
        for ln in lines:
            buf.append(ln)
            buf_chars += len(ln) + 1
            if buf_chars >= target_chars:
                blocks.append(marker + "\n\n" + "\n".join(buf).strip())
                buf = []
                buf_chars = 0
        if buf:
            blocks.append(marker + "\n\n" + "\n".join(buf).strip())

        return "\n\n".join(blocks).strip()
    except Exception:
        return None


def _collect_files() -> list[tuple[str, Path, str, str]]:
    result = []
    for cat, folder_name, store_key in FOLDERS:
        dir_path = PDF_DIR / _nfc(folder_name)
        if not dir_path.exists():
            continue
        for p in _iter_doc_files(dir_path):
            result.append((cat, p, folder_name, store_key))
    return result


def _ensure_vector_store(client: OpenAI, registry: dict, store_key: str, store_name: str) -> str:
    ids = registry.setdefault("vector_store_ids", {})
    if ids.get(store_key):
        return ids[store_key]
    vs = client.vector_stores.create(name=store_name)
    ids[store_key] = vs.id
    return vs.id


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF → OpenAI Vector Store ingest (중복/변경 감지)")
    parser.add_argument("--dry-run", action="store_true", help="업로드 없이 변경 사항만 출력")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("오류: OPENAI_API_KEY가 .env에 없습니다.")
        return 1

    registry = load_registry()
    registry.setdefault("files", {})
    registry.setdefault("path_index", {})

    files = _collect_files()
    if not files:
        print("오류: data/pdf 폴더에 문서가 없습니다.")
        return 1

    client = OpenAI(api_key=api_key)

    # 필요한 store만 준비
    has_policy = any(store_key == "policy" for _, _, _, store_key in files)
    has_regional = any(store_key == "regional" for _, _, _, store_key in files)

    policy_id = registry.get("vector_store_ids", {}).get("policy", "")
    regional_id = registry.get("vector_store_ids", {}).get("regional", "")

    if not args.dry_run:
        if has_policy and not policy_id:
            policy_id = _ensure_vector_store(client, registry, "policy", "policy-rag-store")
        if has_regional and not regional_id:
            regional_id = _ensure_vector_store(client, registry, "regional", "regional-pledge-store")

        # 기록
        write_vector_store_ids(policy_id, regional_id)

    to_upload: dict[str, list[dict]] = {"policy": [], "regional": []}
    now = _utc_now()

    for cat, path, _, store_key in files:
        rel = str(path.relative_to(PDF_DIR)).replace("\\", "/")
        sha = _sha256_file(path)

        # 동일 해시가 이미 업로드된 경우
        entry = registry["files"].get(sha)
        target_id = policy_id if store_key == "policy" else regional_id
        if entry and entry.get("vector_store_id") == target_id and entry.get("openai_file_id"):
            print(f"SKIP (already ingested) {rel}")
            registry["path_index"][rel] = sha
            continue

        # 동일 경로의 이전 해시가 있으면 기존 파일 제거
        prev_sha = registry["path_index"].get(rel)
        if prev_sha and prev_sha != sha:
            prev_entry = registry["files"].get(prev_sha)
            if prev_entry and prev_entry.get("openai_file_id") and prev_entry.get("vector_store_id") == target_id:
                if not args.dry_run:
                    try:
                        client.vector_stores.files.delete(
                            vector_store_id=target_id,
                            file_id=prev_entry["openai_file_id"],
                        )
                        prev_entry["removed_at"] = now
                        print(f"DELETE (old) {rel}")
                    except Exception as e:
                        print(f"WARN delete failed {rel}: {e}")
            registry["path_index"].pop(rel, None)

        content = _create_txt_content(path, cat)
        if not content:
            print(f"SKIP (empty) {rel}")
            continue

        if args.dry_run:
            print(f"INGEST (dry-run) {rel}")
            continue

        tmpdir = ROOT / ".rag" / "tmp_upload"
        tmpdir.mkdir(parents=True, exist_ok=True)
        filename = f"{cat}_{_safe_filename(path.name)}"
        tmp_path = tmpdir / filename
        tmp_path.write_text(content, encoding="utf-8")
        with open(tmp_path, "rb") as f:
            file_id = client.files.create(file=f, purpose="assistants").id
        tmp_path.unlink(missing_ok=True)

        to_upload[store_key].append(
            {
                "sha256": sha,
                "openai_file_id": file_id,
                "vector_store_id": target_id,
                "source_path": rel,
                "category": cat,
                "ingested_at": now,
            }
        )
        registry["path_index"][rel] = sha
        print(f"INGEST {rel}")

    # Vector Store 배치 등록
    for store_key, items in to_upload.items():
        if not items:
            continue
        store_id = policy_id if store_key == "policy" else regional_id
        file_ids = [i["openai_file_id"] for i in items]
        batch = client.vector_stores.file_batches.create(vector_store_id=store_id, file_ids=file_ids)
        for _ in range(60):
            b = client.vector_stores.file_batches.retrieve(vector_store_id=store_id, batch_id=batch.id)
            if b.status == "completed":
                break
            if b.status == "failed":
                raise RuntimeError(f"Vector Store 배치 실패: {b}")
            time.sleep(2)

        for item in items:
            registry["files"][item["sha256"]] = item

    save_registry(registry)
    print("\n완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
OpenAI Vector Store + File Search 기반 RAG.
FAISS 대신 OpenAI가 인덱싱·검색을 담당. AWS 인프라 복잡도 제거.

ENV: USE_OPENAI_VECTOR_STORE=1 시 이 모듈 사용.

증분 업데이트: OPENAI_VECTOR_STORE_ID가 설정된 상태에서 서버 시작 시
새로 추가/수정된 PDF만 업로드, 삭제된 PDF는 Vector Store에서 제거.
manifest에는 content_hash를 저장해 머신 간 배포에서도 정확히 변경 감지.
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict

from openai import OpenAI

from backend.config import CHAT_MODEL, OPENAI_API_KEY, PDF_DIR, ROOT_DIR, _nfc

MANIFEST_PATH = ROOT_DIR / "data" / "vector_store_manifest.json"

logger = logging.getLogger(__name__)

# 업로드된 파일의 폴더 구분을 위한 prefix (파일명에 포함)
_CATEGORY_PREFIX = {
    "platform": "[정강정책]",
    "pledge": "[공약]",
    "regional": "[지역별공약]",
}


def _collect_pdf_paths() -> list[tuple[str, Path]]:
    """(category, path) 리스트. PDF 또는 추출 텍스트로 업로드할 파일."""
    result = []
    folders = [
        ("platform", PDF_DIR / _nfc("정강정책")),
        ("pledge", PDF_DIR / _nfc("공약")),
        ("regional", PDF_DIR / _nfc("지역별 공약")),
    ]
    for cat, dir_path in folders:
        if not dir_path.exists():
            continue
        try:
            for p in sorted(dir_path.rglob("*.pdf")):
                result.append((cat, p))
        except Exception as e:
            logger.warning(f"PDF 스캔 실패 ({dir_path}): {e}")
    return result


def _create_txt_content(pdf_path: Path, category: str) -> str | None:
    """PDF를 읽어 카테고리 prefix가 붙은 텍스트 반환. 실패 시 None."""
    try:
        from backend.pdf_loader import extract_text_from_pdf
        text = extract_text_from_pdf(pdf_path)
        if not (text or "").strip() or len(text.strip()) < 10:
            return None
        prefix = _CATEGORY_PREFIX.get(category, "")
        return f"{prefix} {pdf_path.name}\n\n{text.strip()}"
    except Exception as e:
        logger.warning(f"PDF 추출 실패 {pdf_path}: {e}")
        return None


def ensure_vector_store() -> str:
    """
    Vector Store 생성 및 PDF 업로드.
    Returns: vector_store_id (Responses API file_search에서 사용)
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    # 1. PDF 수집 및 텍스트 추출
    pairs = _collect_pdf_paths()
    if not pairs:
        raise RuntimeError("업로드할 PDF가 없습니다. data/pdf/ 정강정책·공약·지역별 공약 폴더를 확인하세요.")

    logger.info(f"[VECTOR_STORE] PDF {len(pairs)}개 수집 중...")

    # 2. 텍스트 파일로 변환 (OpenAI는 PDF 직접 지원하지만, 카테고리 prefix를 위해 txt 사용)
    files_to_upload: list[tuple[str, str]] = []  # (filename, content)
    for cat, p in pairs:
        content = _create_txt_content(p, cat)
        if content:
            safe_name = p.stem[:80] + ".txt"
            files_to_upload.append((f"{cat}_{safe_name}", content))

    if not files_to_upload:
        raise RuntimeError("PDF에서 추출된 텍스트가 없습니다.")

    logger.info(f"[VECTOR_STORE] 텍스트 파일 {len(files_to_upload)}개 준비 완료")

    # 3. 파일 업로드: Files API로 먼저 업로드
    import tempfile
    file_ids = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for filename, content in files_to_upload:
            path = Path(tmpdir) / filename
            path.write_text(content, encoding="utf-8")
            with open(path, "rb") as f:
                fobj = client.files.create(file=f, purpose="assistants")
                file_ids.append(fobj.id)
    logger.info(f"[VECTOR_STORE] Files API 업로드 완료: {len(file_ids)}개")

    # 4. Vector Store 생성 (file_ids와 함께)
    vs = client.vector_stores.create(name="policy-rag-store", file_ids=file_ids)
    vector_store_id = vs.id
    logger.info(f"[VECTOR_STORE] 생성: {vector_store_id}")

    # 5. Vector Store 준비 대기
    import time
    for _ in range(60):
        vs = client.vector_stores.retrieve(vector_store_id)
        if vs.status == "completed":
            break
        logger.info(f"[VECTOR_STORE] 대기 중... status={vs.status}")
        time.sleep(2)
    else:
        raise RuntimeError("Vector Store 처리 타임아웃. 잠시 후 재시도하세요.")

    logger.info(f"[VECTOR_STORE] 준비 완료: file_count={getattr(vs, 'file_counts', {})}")

    # manifest 저장 (나중에 증분 업데이트용, content_hash로 변경 감지)
    manifest = {"vector_store_id": vector_store_id, "files": {}}
    idx = 0
    for cat, p in pairs:
        content = _create_txt_content(p, cat)
        if content:
            try:
                rel = str(p.relative_to(PDF_DIR))
                ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                manifest["files"][rel] = {"file_id": file_ids[idx], "content_hash": ch}
                idx += 1
            except ValueError:
                pass
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[VECTOR_STORE] manifest 저장: {MANIFEST_PATH}")

    # .env에 OPENAI_VECTOR_STORE_ID 자동 추가
    _append_vector_store_id_to_env(vector_store_id)

    return vector_store_id


def _append_vector_store_id_to_env(vector_store_id: str) -> None:
    """생성된 vector_store_id를 .env에 자동 기록."""
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        env_path.write_text(f"OPENAI_VECTOR_STORE_ID={vector_store_id}\n", encoding="utf-8")
        logger.info(f"[VECTOR_STORE] .env에 OPENAI_VECTOR_STORE_ID 자동 추가: {vector_store_id}")
        return
    text = env_path.read_text(encoding="utf-8")
    if "OPENAI_VECTOR_STORE_ID=" in text:
        # 기존 값이 있으면 갱신 (빈 값일 때만)
        new_lines = []
        for line in text.splitlines():
            if line.strip().startswith("OPENAI_VECTOR_STORE_ID="):
                val = line.split("=", 1)[1].strip()
                if not val:
                    new_lines.append(f"OPENAI_VECTOR_STORE_ID={vector_store_id}")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(text.rstrip() + f"\n\nOPENAI_VECTOR_STORE_ID={vector_store_id}\n", encoding="utf-8")
    logger.info(f"[VECTOR_STORE] .env에 OPENAI_VECTOR_STORE_ID 자동 추가: {vector_store_id}")


def sync_vector_store_incremental(vector_store_id: str) -> None:
    """
    기존 Vector Store에 새/수정 PDF만 추가, 삭제된 PDF는 제거.
    manifest(로컬 경로↔file_id 매핑)를 사용해 변경분만 동기화.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    # manifest 로드 (없으면 증분 불가 → 전체 재생성 필요)
    manifest: dict = {"vector_store_id": vector_store_id, "files": {}}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not manifest.get("files"):
        # manifest 없음 또는 비어있음 = 첫 배포 시. 증분 생략.
        logger.info("[VECTOR_STORE] manifest 없음 → 증분 동기화 생략. (전체 재생성: OPENAI_VECTOR_STORE_ID 지우고 재시작)")
        return

    pairs = _collect_pdf_paths()
    local_keys: dict[str, tuple[Path, str, str]] = {}  # rel -> (path, content_hash, cat)
    for cat, p in pairs:
        try:
            rel = str(p.relative_to(PDF_DIR))
            content = _create_txt_content(p, cat)
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if content else ""
            if ch:
                local_keys[rel] = (p, ch, cat)
        except ValueError:
            pass

    to_add: list[tuple[str, Path, str]] = []
    to_delete: list[str] = []

    for rel, (path, content_hash, cat) in local_keys.items():
        entry = manifest.get("files", {}).get(rel)
        if entry is None or entry.get("content_hash") != content_hash:
            to_add.append((rel, path, cat))

    for rel in list(manifest.get("files", {}).keys()):
        if rel not in local_keys:
            to_delete.append(rel)

    if not to_add and not to_delete:
        logger.info("[VECTOR_STORE] 증분 동기화: 변경 없음")
        return

    logger.info(f"[VECTOR_STORE] 증분 동기화: 추가 {len(to_add)}개, 삭제 {len(to_delete)}개")

    # 삭제
    for rel in to_delete:
        entry = manifest.get("files", {}).get(rel)
        if entry and entry.get("file_id"):
            try:
                client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=entry["file_id"])
                logger.info(f"[VECTOR_STORE] 삭제: {rel}")
            except Exception as e:
                logger.warning(f"[VECTOR_STORE] 삭제 실패 {rel}: {e}")
            del manifest["files"][rel]

    # 추가 (기존 파일 수정 시 먼저 Vector Store에서 제거)
    for rel, path, cat in to_add:
        entry = manifest.get("files", {}).get(rel)
        if entry and entry.get("file_id"):
            try:
                client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=entry["file_id"])
                logger.info(f"[VECTOR_STORE] 교체 (기존 삭제): {rel}")
            except Exception as e:
                logger.warning(f"[VECTOR_STORE] 기존 삭제 실패 {rel}: {e}")
            del manifest["files"][rel]

    import tempfile
    new_file_ids: list[str] = []
    new_entries: dict[str, dict] = {}

    for rel, path, cat in to_add:
        content = _create_txt_content(path, cat)
        if not content:
            continue
        safe_name = path.stem[:80] + ".txt"
        filename = f"{cat}_{safe_name}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = Path(f.name)
        try:
            with open(tmp_path, "rb") as f:
                fobj = client.files.create(file=f, purpose="assistants")
            new_file_ids.append(fobj.id)
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            new_entries[rel] = {"file_id": fobj.id, "content_hash": ch}
        finally:
            tmp_path.unlink(missing_ok=True)

    if new_file_ids:
        batch = client.vector_stores.file_batches.create(vector_store_id=vector_store_id, file_ids=new_file_ids)
        for _ in range(60):
            batch = client.vector_stores.file_batches.retrieve(vector_store_id=vector_store_id, batch_id=batch.id)
            if batch.status == "completed":
                break
            if batch.status == "failed":
                raise RuntimeError(f"Vector Store 배치 실패: {batch}")
            time.sleep(2)
        manifest["files"].update(new_entries)
        logger.info(f"[VECTOR_STORE] 추가 완료: {list(new_entries.keys())}")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


_INSTRUCTIONS = """너는 정책 정합성 채점관이다.
당 출마자 공약을 정강정책·우리당 공약·타지역 공약 문서와 비교해 적합도를 판단한다.

문서 내 [정강정책], [공약], [지역별공약] prefix로 출처를 구분한다.
채점 시 이념·가치·정책 방향의 부합으로 판단하고, 문자열 유사도가 아니다.

출마자 공약이 주어지면, file_search로 관련 문서를 검색한 뒤,
다음 JSON 형식만 반환한다 (다른 설명 없이). JSON만 출력하고 코드블록 마크다운은 사용하지 마라.

{
  "confidence": 0-100,
  "rubric": {
    "platform": [{"item":"가치 정합성","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"정책 방향 일치","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"수단 적합성","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"일관성","score_0_5":0-5,"evidence":[],"note":"..."}],
    "pledges": [{"item":"중복/연계 가능","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"차별성","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"정책 언어 호환","score_0_5":0-5,"evidence":[],"note":"..."}],
    "conflicts": [{"item":"명시적 상충","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"잠재 리스크","score_0_5":0-5,"evidence":[],"note":"..."}]
  },
  "improvements": [{"title":"...","detail":"...","evidence":[]}]
}

score_0_5: 0=상충/근거전무, 1~2=부적합, 3=부분부합, 4=대체로 부합, 5=강한 부합.
evidence는 검색된 문서 인용 시 사용. platform/pledges는 [] 가능.
"""


def run_verify(vector_store_id: str, user_pledge: str) -> Dict:
    """
    Responses API (file_search)로 검증 리포트 JSON 반환. gpt-5.2 등 최신 모델 사용 가능.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    input_text = f"다음 출마자 공약을 검증하고, 지정된 JSON 형식만 반환해라:\n\n{user_pledge}"

    response = client.responses.create(
        model=CHAT_MODEL,
        input=input_text,
        instructions=_INSTRUCTIONS,
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
    )

    if getattr(response, "status", None) != "completed":
        raise RuntimeError(f"Responses API 실패: status={getattr(response, 'status', 'unknown')}")

    # output에서 type=message인 항목의 content[0].text 추출
    text = ""
    for item in response.output:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []):
                if getattr(c, "type", None) == "output_text":
                    text = getattr(c, "text", "")
                    break
            break

    if not text:
        raise RuntimeError("모델이 텍스트를 반환하지 않음")
    # JSON 추출 (마크다운 코드블록 제거)
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    raw = json.loads(text)

    # 기존 report 형식에 맞게 변환
    rubric = raw.get("rubric", {})
    confidence = int(raw.get("confidence", 0))

    def avg_score(items: list) -> float:
        if not items:
            return 0.0
        return sum(i.get("score_0_5", 0) for i in items) / len(items)

    platform_items = rubric.get("platform", [])
    pledges_items = rubric.get("pledges", [])
    conflicts_items = rubric.get("conflicts", [])
    platform_avg = avg_score(platform_items)
    pledges_avg = avg_score(pledges_items)
    conflicts_avg = avg_score(conflicts_items)

    fit_score = round((platform_avg * 0.4 + pledges_avg * 0.4 + (5 - conflicts_avg) * 0.2) * 20, 1)
    if fit_score > 100:
        fit_score = 100.0

    fit_verdict = "강한 부합" if fit_score >= 80 else "부합" if fit_score >= 60 else "부분부합" if fit_score >= 40 else "미부합"

    return {
        "summary": {
            "fit_score": fit_score,
            "fit_verdict": fit_verdict,
            "confidence": confidence,
        },
        "platform": platform_items,
        "pledges": pledges_items,
        "regional_similarity": [],
        "conflicts": conflicts_items,
        "improvements": raw.get("improvements", []),
    }

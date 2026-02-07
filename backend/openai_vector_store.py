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

from backend.config import (
    CHAT_MODEL,
    OPENAI_API_KEY,
    PDF_DIR,
    ROOT_DIR,
    FILE_SEARCH_MAX_RESULTS,
    FILE_SEARCH_MAX_RESULTS_QUICK,
    _nfc,
)

MANIFEST_PATH = ROOT_DIR / "data" / "vector_store_manifest.json"
# 지역별 공약 전용 manifest (타지역 유사성 검토 시 이 store만 검색)
MANIFEST_REGIONAL_PATH = ROOT_DIR / "data" / "vector_store_regional_manifest.json"

logger = logging.getLogger(__name__)

# 업로드된 파일의 폴더 구분. 출처 혼동 방지를 위해 명확한 라벨 사용
_CATEGORY_HEADER = {
    "platform": "[정강정책] 우리당 강령·정책 원칙",
    "pledge": "[공약] 우리당 중앙 공약 (일반공약)",
    "regional": "[지역별공약] 타지역 출마자 공약 (비교·중복 검토용)",
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
    """PDF를 읽어 카테고리 헤더가 붙은 텍스트 반환. 실패 시 None."""
    try:
        from backend.pdf_loader import extract_text_from_pdf
        text = extract_text_from_pdf(pdf_path)
        if not (text or "").strip() or len(text.strip()) < 10:
            return None
        header = _CATEGORY_HEADER.get(category, "")
        # 폴더 경로 포함해 일반공약 vs 지역별공약 출처 명확히 구분
        try:
            rel = pdf_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = pdf_path.name
        return f"{header}\n출처: {source_path}\n\n{text.strip()}"
    except Exception as e:
        logger.warning(f"PDF 추출 실패 {pdf_path}: {e}")
        return None


def ensure_vector_store() -> tuple[str, str]:
    """
    Vector Store 2개 생성: (정강+공약) / (지역별 공약) 분리.
    타지역 유사성 검토 시 지역별 store만 검색해 공약 폴더 혼선 방지.
    Returns: (policy_vector_store_id, regional_vector_store_id)
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    pairs = _collect_pdf_paths()
    policy_pairs = [(c, p) for c, p in pairs if c in ("platform", "pledge")]
    regional_pairs = [(c, p) for c, p in pairs if c == "regional"]

    if not policy_pairs:
        raise RuntimeError("정강정책 또는 공약 폴더에 PDF가 없습니다.")

    def _upload_and_create(pairs_subset: list, store_name: str, manifest_path: Path) -> str:
        files_to_upload: list[tuple[str, str]] = []
        for cat, p in pairs_subset:
            content = _create_txt_content(p, cat)
            if content:
                safe_name = p.stem[:80] + ".txt"
                files_to_upload.append((f"{cat}_{safe_name}", content))
        if not files_to_upload:
            return ""
        import tempfile
        file_ids = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename, content in files_to_upload:
                path = Path(tmpdir) / filename
                path.write_text(content, encoding="utf-8")
                with open(path, "rb") as f:
                    fobj = client.files.create(file=f, purpose="assistants")
                    file_ids.append(fobj.id)
        vs = client.vector_stores.create(name=store_name, file_ids=file_ids)
        vs_id = vs.id
        for _ in range(60):
            vs = client.vector_stores.retrieve(vs_id)
            if vs.status == "completed":
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Vector Store {store_name} 처리 타임아웃")
        manifest = {"vector_store_id": vs_id, "files": {}}
        idx = 0
        for cat, p in pairs_subset:
            content = _create_txt_content(p, cat)
            if content:
                try:
                    rel = str(p.relative_to(PDF_DIR))
                    ch = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                    manifest["files"][rel] = {"file_id": file_ids[idx], "content_hash": ch}
                    idx += 1
                except ValueError:
                    pass
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return vs_id

    logger.info(f"[VECTOR_STORE] 정강+공약 {len(policy_pairs)}개, 지역별 {len(regional_pairs)}개")
    vs_policy = _upload_and_create(policy_pairs, "policy-rag-store", MANIFEST_PATH)
    vs_regional = _upload_and_create(regional_pairs, "regional-pledge-store", MANIFEST_REGIONAL_PATH) if regional_pairs else ""
    logger.info(f"[VECTOR_STORE] policy: {vs_policy}, regional: {vs_regional or '(없음)'}")

    _append_vector_store_ids_to_env(vs_policy, vs_regional)
    return (vs_policy, vs_regional)


def _append_vector_store_ids_to_env(vs_policy: str, vs_regional: str) -> None:
    """생성된 vector_store_id를 .env에 자동 기록."""
    env_path = ROOT_DIR / ".env"
    lines_add = [f"OPENAI_VECTOR_STORE_ID={vs_policy}"]
    if vs_regional:
        lines_add.append(f"OPENAI_REGIONAL_VECTOR_STORE_ID={vs_regional}")
    if not env_path.exists():
        env_path.write_text("\n".join(lines_add) + "\n", encoding="utf-8")
        logger.info(f"[VECTOR_STORE] .env에 자동 추가: {lines_add}")
        return
    text = env_path.read_text(encoding="utf-8")
    new_lines = []
    seen = {"OPENAI_VECTOR_STORE_ID": False, "OPENAI_REGIONAL_VECTOR_STORE_ID": False}
    for line in text.splitlines():
        if line.strip().startswith("OPENAI_VECTOR_STORE_ID="):
            seen["OPENAI_VECTOR_STORE_ID"] = True
            new_lines.append(f"OPENAI_VECTOR_STORE_ID={vs_policy}")
        elif line.strip().startswith("OPENAI_REGIONAL_VECTOR_STORE_ID="):
            seen["OPENAI_REGIONAL_VECTOR_STORE_ID"] = True
            new_lines.append(f"OPENAI_REGIONAL_VECTOR_STORE_ID={vs_regional}")
        else:
            new_lines.append(line)
    if not seen["OPENAI_VECTOR_STORE_ID"]:
        new_lines.extend(lines_add)
    elif vs_regional and not seen["OPENAI_REGIONAL_VECTOR_STORE_ID"]:
        new_lines.append(f"OPENAI_REGIONAL_VECTOR_STORE_ID={vs_regional}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    logger.info(f"[VECTOR_STORE] .env에 자동 추가: {lines_add}")


def sync_vector_store_incremental(vector_store_id: str, manifest_path: Path = MANIFEST_PATH, categories: tuple[str, ...] = ("platform", "pledge")) -> None:
    """
    기존 Vector Store에 새/수정 PDF만 추가, 삭제된 PDF는 제거.
    categories: 이 manifest에 해당하는 폴더만 동기화 (platform,pledge) 또는 (regional,)
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    manifest: dict = {"vector_store_id": vector_store_id, "files": {}}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not manifest.get("files"):
        logger.info(f"[VECTOR_STORE] manifest 없음 ({manifest_path.name}) → 증분 동기화 생략")
        return

    pairs = [(c, p) for c, p in _collect_pdf_paths() if c in categories]
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

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


_INSTRUCTIONS = """너는 정책 정합성 채점관이다.
당 출마자 공약을 정강정책·우리당 공약·타지역 공약 문서와 비교해 적합도를 판단한다.

file_search 도구가 2개 있음 (반드시 구분 사용):
1) 정강정책·공약 검색: platform, pledges 채점 시 반드시 이 도구만 사용. 우리당 강령·중앙 공약.
2) 지역별 공약 검색: 타지역 유사성·중복 검토(conflicts, regional_similarity) 시 반드시 이 도구만 사용. 공약 폴더가 아님.

타지역 출마자 공약과의 유사성·중복을 검토할 때는 반드시 '지역별 공약' 전용 검색 도구를 사용. 공약 폴더(일반공약)를 검색하면 안 됨.

[채점 원칙]
- **단어·문자열 일치가 아니다.** 핵심 가치·이념·정책 방향의 부합으로 판단한다. 표현이 다르더라도 가치가 맞으면 높은 점수, 표현이 비슷해도 가치가 어긋나면 낮은 점수.
- **모호한 방향/구체성 부족**: 방향만 제시하고 구체적 수단·수치·이행 계획이 없으면 improvements에 반드시 짚어라. 예: "지역경제 활성화"만 쓰고 어떻게 할지 없음 → "구체적 방안·수치·이행 계획 보완 필요".

출마자 공약이 주어지면, file_search로 관련 문서를 검색한 뒤,
다음 JSON 형식만 반환한다 (다른 설명 없이). JSON만 출력하고 코드블록 마크다운은 사용하지 마라.

{
  "confidence": 0-100,
  "rubric": {
    "platform": [{"item":"가치 정합성","score_0_5":0-5,"evidence":[],"note":"핵심 이념·가치 부합(문자열 아님)"}, {"item":"정책 방향 일치","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"수단 적합성","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"일관성","score_0_5":0-5,"evidence":[],"note":"..."}],
    "pledges": [{"item":"중복/연계 가능","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"차별성","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"정책 언어 호환","score_0_5":0-5,"evidence":[],"note":"..."}],
    "conflicts": [{"item":"명시적 상충","score_0_5":0-5,"evidence":[],"note":"..."}, {"item":"잠재 리스크","score_0_5":0-5,"evidence":[],"note":"..."}]
  },
  "improvements": [{"title":"...","detail":"...","evidence":[]}]
}

score_0_5: 0=상충/근거전무, 1~2=부적합, 3=부분부합, 4=대체로 부합, 5=강한 부합.
evidence는 검색된 문서 인용 시 사용. platform/pledges는 [] 가능.
improvements: 구체적 방안·수치·이행 계획이 없으면 \"구체성 보완 필요\" 항목을 반드시 포함.
"""


def _check_vector_store_ready(client: OpenAI, vs_id: str) -> None:
    """인덱싱 완료 여부 확인. in_progress면 RuntimeError."""
    vs = client.vector_stores.retrieve(vs_id)
    if getattr(vs, "status", None) == "in_progress":
        raise RuntimeError("Vector Store 인덱싱 중입니다. 잠시 후 다시 시도하세요.")


def run_verify(vector_store_id: str, user_pledge: str, regional_vector_store_id: str = "", max_results: int | None = None) -> Dict:
    """
    Responses API (file_search)로 검증 리포트 JSON 반환.
    max_results: file_search로 가져올 결과 개수 제한 (기본 FILE_SEARCH_MAX_RESULTS).
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    _check_vector_store_ready(client, vector_store_id)
    if regional_vector_store_id:
        _check_vector_store_ready(client, regional_vector_store_id)
    limit = max_results if max_results is not None else FILE_SEARCH_MAX_RESULTS

    input_text = f"다음 출마자 공약을 검증하고, 지정된 JSON 형식만 반환해라:\n\n{user_pledge}"

    def _tool(vs_id: str):
        t = {"type": "file_search", "vector_store_ids": [vs_id], "max_num_results": limit}
        return t

    tools = [_tool(vector_store_id)]
    if regional_vector_store_id:
        tools.append(_tool(regional_vector_store_id))

    response = client.responses.create(
        model=CHAT_MODEL,
        input=input_text,
        instructions=_INSTRUCTIONS,
        tools=tools,
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

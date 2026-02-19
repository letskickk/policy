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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import re

from openai import OpenAI

from backend.config import (
    CHAT_MODEL,
    OPENAI_API_KEY,
    PDF_DIR,
    ROOT_DIR,
    FILE_SEARCH_MAX_RESULTS,
    FILE_SEARCH_MAX_RESULTS_QUICK,
    PROMPTS_DIR,
    DATA_GO_KR_API_KEY,
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
    pledge_names = set()
    for cat, dir_path in folders:
        if not dir_path.exists():
            continue
        try:
            from backend.pdf_loader import _iter_doc_files
            files = list(_iter_doc_files(dir_path))
            if cat == "pledge":
                pledge_names = {p.name for p in files}
            if cat == "regional" and pledge_names and files:
                regional_names = {p.name for p in files}
                overlap = len(regional_names & pledge_names) / max(len(pledge_names), 1)
                if overlap >= 0.7 and len(regional_names & pledge_names) >= 3:
                    logger.warning(
                        "지역별 공약 폴더가 공약 폴더와 거의 동일하여 Vector Store 업로드 건너뜀. "
                        "타지역 공약 전용 파일만 넣어 주세요."
                    )
                    continue
            for p in files:
                result.append((cat, p))
        except Exception as e:
            logger.warning(f"PDF 스캔 실패 ({dir_path}): {e}")
    return result


def _create_txt_content(doc_path: Path, category: str) -> str | None:
    """PDF/TXT를 읽어 카테고리 헤더가 붙은 텍스트 반환. 실패 시 None."""
    try:
        from backend.pdf_loader import extract_text_from_file
        text = extract_text_from_file(doc_path)
        if not (text or "").strip() or len(text.strip()) < 10:
            return None
        header = _CATEGORY_HEADER.get(category, "")
        # 폴더 경로 포함해 일반공약 vs 지역별공약 출처 명확히 구분
        try:
            rel = doc_path.relative_to(PDF_DIR)
            source_path = str(rel).replace("\\", "/")
        except ValueError:
            source_path = doc_path.name
        marker = f"{header}\n출처: {source_path}"

        # 중요: Vector Store는 문서를 청크로 자르며, 중간 청크에는 marker가 포함되지 않을 수 있음.
        # 섹션별(폴더별) 분리를 위해 marker를 본문에도 주기적으로 삽입한다.
        lines = (text or "").strip().splitlines()
        blocks: list[str] = []
        buf: list[str] = []
        buf_chars = 0
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
    except Exception as e:
        logger.warning(f"문서 추출 실패 {doc_path}: {e}")
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
- **제목·표제만 적은 경우 = 2점 이하 고정**: "다자녀 핑크번호판", "지역경제 활성화" 같이 명칭만 적고 구체적 방안이 없으면 2점 이하. 이때 note에는 "90% 일치", "거의 동일" 대신 → "우리당 공약은 [검색된 내용 요약: 누구에게 무엇을 어떻게 주겠다]. 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요." 형식으로 작성.
- **구체성 없으면 높은 점수 금지**: 내용이 짧거나(한 문장·한 줄 수준) 구체적 방안이 없으면 3점 이하. 4~5점은 구체적 수단·수치·이행 계획이 있을 때만.
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
- 제목만·한 줄만 적은 경우: platform/pledges 2점 이하. note는 "우리당 공약은 [검색된 내용]. 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요." 형식으로. "90% 일치", "거의 동일" 사용 금지.
evidence는 검색된 문서 인용 시 사용. platform/pledges는 [] 가능.
note에 유사 공약을 나열할 때는 대표 2~3건만. 모든 공약을 나열하지 말 것.
지역별 공약 store가 비어 있으면 conflicts·regional_similarity에서 우리당 공약 내용을 인용하지 말 것.
improvements: 구체적 방안·수치·이행 계획이 없으면 \"구체성 보완 필요\" 항목을 반드시 포함.
"""


_JUDGE_INSTRUCTIONS = """You are a judge that separates '공약 유사도 탐색(QUERY)' from '정책 내용 검증(VERIFY)'.

[모드 규칙]
- 입력이 키워드/제목 수준(예: 20자 미만 또는 1문장 또는 정책 슬롯 2개 미만)이면 mode="QUERY"로 처리한다.
- QUERY 모드에서는 final_score(적합/검증 점수)를 산출하지 말고, 유사 문서 후보와 "추가로 필요한 정보"만 제시한다.

[검증 규칙]
- mode="VERIFY"는 입력에 정책 슬롯이 3개 이상 있을 때만 허용한다.
- VERIFY에서 점수는 '정책 내용 동일성' 기준이며, 제목/키워드 일치만으로 80점 이상을 주지 않는다.
- evidence: 입력에서 2개, 레퍼런스에서 2개 근거를 반드시 인용한다. 못하면 INSUFFICIENT_INFO로 종료한다.

[상한]
- 슬롯 0~1개면 final_score 금지 또는 55 이하, confidence=LOW.
- 슬롯 2개면 final_score <= 70.
- 슬롯 3개 이상이어야 80+ 가능.

[정책 슬롯 예시] 대상, 목표, 구체적 수단, 수치·목표치, 이행 계획 등.

Output JSON only:
{
  "status": "OK" | "INSUFFICIENT_INFO",
  "mode": "QUERY" | "VERIFY",
  "policy_slot_count": 0-5,
  "duplication_score": 0-100,
  "ideology_fit_score": 0-100 or null,
  "specificity_score": 0-100,
  "final_score": 0-100 or null,
  "confidence": "LOW"|"MED"|"HIGH",
  "missing_fields": ["추가로 필요한 정보 목록"],
  "evidence": {"input_quotes":[], "reference_quotes":[]},
  "similar_candidates": []  // QUERY 모드 시 유사 문서 후보 요약
}
"""


def _load_check_instructions(has_regional: bool, has_winners2022: bool = False) -> str:
    """당 부합 점검용 instructions (file_search 버전)."""
    sys_path = PROMPTS_DIR / "당_부합_점검_시스템.txt"
    user_path = PROMPTS_DIR / "당_부합_점검_유저.txt"
    system = sys_path.read_text(encoding="utf-8").strip() if sys_path.exists() else ""
    user_tpl = user_path.read_text(encoding="utf-8").strip() if user_path.exists() else ""

    if has_winners2022:
        tool_desc = """
file_search 도구가 2개 제공됨:
1) 첫 번째 도구: 정강·공약·지역별 공약 (1~3번 섹션용)
2) 두 번째 도구: 2022 당선인 공약 전용 (4번 섹션용). 반드시 이 도구를 호출하여 4번 섹션을 작성하라.
4번 섹션은 두 번째 도구 검색 결과를 사용. 두 번째 도구를 호출하지 않으면 4번에 "없음"을 적지 말고, 먼저 호출한 뒤 결과에 따라 작성하라.
"""
    else:
        tool_desc = """
file_search 도구: 정강·공약·지역별 공약 검색.
"""
    if not has_regional:
        tool_desc += "\n지역별 공약 store가 없음. '3. 타지역 공약과 유사성'에서는 반드시 '유사 공약: 없음', '유사성 분석: 없음'만 표기."
    if not has_winners2022:
        tool_desc += "\n2022 당선인 공약 store가 없음. '4. 이전 당선인(2022) 공약과의 비교'에서는 반드시 '유사·참고 공약: 없음', '발전 방향: 없음'만 표기."

    winners2022_ctx = "[file_search로 2022 당선인 공약 검색 (해당 store 있으면)]" if has_winners2022 else "(2022 당선인 공약 문서 없음)"

    # user 템플릿: 문서 블록은 file_search로 대체, PLEDGE는 입력에서 전달됨
    user_adapted = (
        user_tpl.replace("{{PLATFORM_CONTEXT}}", "[file_search로 정강·정책 문서 검색하여 사용]")
        .replace("{{PLEDGES_CONTEXT}}", "[file_search로 우리당 공약 검색하여 사용]")
        .replace("{{REGIONAL_PLEDGES_CONTEXT}}", "[file_search로 타지역 공약 검색 (지역별 store 있으면)]" if has_regional else "(타지역 공약 문서 없음)")
        .replace("{{WINNERS2022_PLEDGES_CONTEXT}}", winners2022_ctx)
        .replace("{{PLEDGE}}", "[입력으로 전달되는 출마자 공약]")
        .replace("{{ELECTION_TYPE}}", "[입력 메타정보에서 전달]")
        .replace("{{REGION_LEVEL}}", "[입력 메타정보에서 전달]")
        .replace("{{REGION_PROVINCE}}", "[입력 메타정보에서 전달]")
        .replace("{{REGION_CITY}}", "[입력 메타정보에서 전달]")
        .replace("{{DISTRICT_NAME}}", "[입력 메타정보에서 전달]")
        # 구버전 템플릿 호환
        .replace("{{REGION_NAME}}", "[입력 메타정보에서 전달]")
    )
    return f"{system}\n\n{tool_desc}\n\n{user_adapted}"


def run_check(
    vector_store_id: str,
    user_pledge: str,
    regional_vector_store_id: str = "",
    winners2022_vector_store_id: str = "",
    max_results: int = 12,
    user_meta: dict | None = None,
) -> str:
    """
    A안(전면 재설계):
    - 모델에게 file_search 호출을 맡기지 않고,
    - 서버가 4개 출처(정강정책/공약/지역별/2022당선인)를 각각 검색해 컨텍스트를 구성한 뒤
    - 최종 답변만 생성한다.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    def _coalesce_text(content: object) -> str:
        """Search result content -> text (SDK object/dict 모두 지원)."""
        if not content:
            return ""
        parts: List[str] = []
        if isinstance(content, list):
            items = content
        else:
            items = [content]
        for c in items:
            c_type = getattr(c, "type", None) if not isinstance(c, dict) else c.get("type")
            if c_type != "text":
                continue
            txt = getattr(c, "text", None) if not isinstance(c, dict) else c.get("text")
            if txt:
                parts.append(str(txt))
        return "\n".join(parts).strip()

    # 동일 요청 내 동일 쿼리 재호출 방지 캐시 (스레드 세이프)
    search_cache: Dict[str, List[Tuple[float, str, str]]] = {}
    search_cache_lock = threading.Lock()

    def _is_retryable_err(e: Exception) -> bool:
        s = str(e).lower()
        return "502" in s or "503" in s or "504" in s or "timeout" in s or "timed out" in s or "gateway" in s

    def _search(
        client: OpenAI,
        vs_id: str,
        query: str | list[str],
        k: int,
        rewrite: bool = True,
    ) -> List[Tuple[float, str, str]]:
        """
        Returns list of (score, filename, chunk_text).
        일시적 오류(502/503/504/timeout) 시 최대 2회 재시도, 짧은 백오프. 동일 쿼리 캐시 사용.
        """
        if not vs_id:
            return []
        q_repr = (query if isinstance(query, str) else "|".join(str(x) for x in query))[:500]
        cache_key = hashlib.sha256(f"{vs_id}|{q_repr}|{k}|{rewrite}".encode("utf-8", errors="ignore")).hexdigest()
        with search_cache_lock:
            if cache_key in search_cache:
                return list(search_cache[cache_key])
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                try:
                    page = client.vector_stores.search(
                        vector_store_id=vs_id,
                        query=query,
                        max_num_results=max(1, min(int(k), 50)),
                        rewrite_query=bool(rewrite),
                    )
                except TypeError:
                    page = client.vector_stores.search(
                        vs_id,
                        query=query,
                        max_num_results=max(1, min(int(k), 50)),
                        rewrite_query=bool(rewrite),
                    )
                data = getattr(page, "data", None) if not isinstance(page, dict) else page.get("data")
                if not data:
                    out: List[Tuple[float, str, str]] = []
                else:
                    out = []
                    for item in data:
                        score = getattr(item, "score", None) if not isinstance(item, dict) else item.get("score")
                        filename = getattr(item, "filename", None) if not isinstance(item, dict) else item.get("filename")
                        content = getattr(item, "content", None) if not isinstance(item, dict) else item.get("content")
                        text = _coalesce_text(content)
                        if not text:
                            continue
                        out.append((float(score or 0.0), str(filename or ""), text))
                    out.sort(key=lambda t: t[0], reverse=True)
                with search_cache_lock:
                    search_cache[cache_key] = out
                return out
            except Exception as e:
                last_error = e
                if attempt < 2 and _is_retryable_err(e):
                    time.sleep(0.5 * (1 + attempt))  # 0.5s, 1.0s
                    continue
                raise
        if last_error is not None:
            raise last_error
        return []

    def _dedup(items: Iterable[Tuple[float, str, str]]) -> List[Tuple[float, str, str]]:
        seen: set[str] = set()
        out: List[Tuple[float, str, str]] = []
        for score, filename, text in items:
            key = hashlib.sha256((filename + "\n" + text[:400]).encode("utf-8", errors="ignore")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            out.append((score, filename, text))
        return out

    def _extract_source_path(text: str) -> str:
        """
        청크 안의 '출처: ...' 라인에서 폴더/파일 경로를 최대한 복원.
        (청크가 잘려서 없을 수도 있음)
        """
        t = text or ""
        m = re.search(r"^\s*출처:\s*(.+?)\s*$", t, flags=re.MULTILINE)
        if not m:
            return ""
        return (m.group(1) or "").strip()

    def _source_bucket(source_path: str) -> str:
        """
        폴더 기준 버킷 분리.
        Returns: 'platform'|'pledge'|'regional'|'winners2022'|''
        """
        sp = (source_path or "").replace("\\", "/").strip()
        if not sp:
            return ""
        if sp.startswith("정강정책/"):
            return "platform"
        if sp.startswith("공약/"):
            return "pledge"
        if sp.startswith("지역별 공약/"):
            return "regional"
        if sp.startswith("8회 당선인 공약/"):
            return "winners2022"
        return ""

    def _compact_spaced_hangul(text: str) -> str:
        """
        OCR/PDF 추출에서 '오 세 훈', '서 울 특 별 시'처럼
        한 글자씩 띄어진 패턴을 '오세훈', '서울특별시'로 정규화.
        """
        if not text:
            return text
        return re.sub(
            r"((?:[가-힣]\s+){1,}[가-힣])",
            lambda m: re.sub(r"\s+", "", m.group(1)),
            text,
        )

    def _normalize_region_name(region: str) -> str:
        r = re.sub(r"\s+", "", (region or ""))
        mapping = {
            "서울": "서울특별시",
            "부산": "부산광역시",
            "대구": "대구광역시",
            "인천": "인천광역시",
            "광주": "광주광역시",
            "대전": "대전광역시",
            "울산": "울산광역시",
            "세종": "세종특별자치시",
            "경기": "경기도",
            "강원": "강원도",
            "충북": "충청북도",
            "충남": "충청남도",
            "전북": "전라북도",
            "전남": "전라남도",
            "경북": "경상북도",
            "경남": "경상남도",
            "제주": "제주특별자치도",
        }
        return mapping.get(r, r)

    def _infer_position_from_region(region: str) -> str | None:
        r = _normalize_region_name(region)
        if not r:
            return None
        if r.endswith("특별시"):
            return r[:-3] + "특별시장"
        if r.endswith("광역시"):
            return r[:-3] + "광역시장"
        if r.endswith("도"):
            return r + "지사"
        if r.endswith("특별자치시"):
            return r[:-5] + "특별자치시장"
        if r.endswith(("시", "군", "구")):
            if r.endswith("구"):
                return r + "청장"
            if r.endswith("군"):
                return r + "수"
            return r + "장"
        return None

    def _get_winner_name_from_api(position: str, region: str) -> str | None:
        """
        공공데이터포털 당선인 정보 API로 이름 조회.
        position: "서울특별시장", "경기도지사" 등
        region: "서울특별시", "경기도" 등
        Returns: 당선인 이름 또는 None
        """
        if not DATA_GO_KR_API_KEY:
            return None
        
        try:
            from urllib.parse import urlencode
            from urllib.request import Request, urlopen
            from urllib.error import HTTPError
            
            # 직책에서 sgTypecode 추론
            sg_typecode = None
            if "지사" in position:
                sg_typecode = "2"  # 시도지사
            elif "시장" in position or "구청장" in position or "군수" in position:
                sg_typecode = "3"  # 시장/구청장/군수
            elif "의원" in position:
                if "광역" in position:
                    sg_typecode = "4"  # 광역의원
                else:
                    sg_typecode = "5"  # 기초의원
            
            if not sg_typecode:
                return None
            
            # 지역명 정규화
            sd_name = _normalize_region_name(region)
            if not sd_name:
                return None
            
            # API 호출
            base_url = "http://apis.data.go.kr/9760000/WinnerInfoInqireService2/getWinnerInfoInqire"
            params = {
                "ServiceKey": DATA_GO_KR_API_KEY,
                "sgId": "20220601",  # 제8회 지방선거
                "sgTypecode": sg_typecode,
                "sdName": sd_name,
                "resultType": "json",
                "pageNo": "1",
                "numOfRows": "100"
            }
            
            url = f"{base_url}?{urlencode(params)}"
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.data.go.kr/"
            })
            
            with urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
            
            # 응답 파싱
            body = data.get("response", {}).get("body", {}) or data.get("body", {})
            items = body.get("items") or body.get("item")
            if not items:
                return None
            
            if isinstance(items, dict):
                items = items.get("item")
            if not items:
                return None
            
            if not isinstance(items, list):
                items = [items]
            
            # 직책과 일치하는 당선인 찾기
            for item in items:
                name = (item.get("name") or item.get("NAME") or "").strip()
                sgg_name = (item.get("sggName") or item.get("SGG_NAME") or "").strip()
                
                # 직책과 선거구명이 일치하는지 확인
                if name and len(name) >= 2:
                    # 시도지사/시장의 경우 선거구명이 없거나 지역명과 일치
                    if sg_typecode in ("2", "3"):
                        if not sgg_name or sd_name in sgg_name or sgg_name in sd_name:
                            return name
                    else:
                        # 의원의 경우 선거구명 확인 필요 (일단 이름만 반환)
                        return name
            
            return None
            
        except Exception as e:
            logger.debug(f"[API] 당선인 정보 조회 실패: {e}")
            return None

    def _extract_pledge_title(text: str) -> str | None:
        """
        공약 제목 추출 (공약 1, 공약 2 등 다음 텍스트 또는 따옴표 안 텍스트).
        """
        if not text:
            return None
        
        # 패턴 1: "공약 1", "공약 2" 등 다음 텍스트
        match = re.search(r'공약\s*\d+\s*[:\s]*([^\n]{10,80}?)(?:\n|목표|이행방법|$)', text, re.MULTILINE)
        if match:
            title = match.group(1).strip()
            # 따옴표 제거
            title = re.sub(r'^["\'「」『』]|["\'「」『』]$', '', title).strip()
            if len(title) >= 5:
                return title
        
        # 패턴 2: 따옴표 안 텍스트
        for quote in ['"', "'", '「', '」', '『', '』']:
            match = re.search(rf'{quote}([^{quote}]{{10,80}}?){quote}', text)
            if match:
                title = match.group(1).strip()
                if len(title) >= 5:
                    return title
        
        # 패턴 3: 첫 번째 문장 (50자 이내)
        lines = text.split('\n')
        for line in lines[:5]:
            line = line.strip()
            if 10 <= len(line) <= 50 and not line.startswith('[') and not line.startswith('출처'):
                return line
        
        return None

    def _extract_winners2022_metadata(text: str, filename: str = "") -> dict:
        """
        winners2022 청크에서 메타 정보 추출 (이름/직책/지역/공약제목).
        Returns: {'name': str|None, 'position': str|None, 'region': str|None, 'pledge_title': str|None}
        ingest 시 삽입된 [문서 메타] 헤더(직책 후보군/지역 후보군/이름 후보)를 우선 파싱,
        없으면 본문 전체에서 정규식으로 추출.
        """
        meta = {'name': None, 'position': None, 'region': None, 'pledge_title': None}
        if not text:
            return meta

        # ── 우선순위 1: [문서 메타] 헤더 파싱 ─────────────────────────────
        # ingest 시 헤더 형식:
        #   직책 후보군: 구청장, 남구청장
        #   지역 후보군: 광주, 광주광역시, 남구
        #   이름 후보: 홍길동
        m_pos = re.search(r"직책 후보군\s*:\s*([^\n]+)", text)
        m_reg = re.search(r"지역 후보군\s*:\s*([^\n]+)", text)
        m_name = re.search(r"이름 후보\s*:\s*([^\n]+)", text)
        if m_pos and m_pos.group(1).strip() != "확인 필요":
            candidates = [c.strip() for c in m_pos.group(1).split(",") if c.strip()]
            if candidates:
                meta['position'] = candidates[0]
        if m_reg and m_reg.group(1).strip() != "확인 필요":
            candidates = [c.strip() for c in m_reg.group(1).split(",") if c.strip()]
            if candidates:
                meta['region'] = _normalize_region_name(candidates[0])
        if m_name and m_name.group(1).strip() != "확인 필요":
            NAME_BLACKLIST_FAST = {
                "특별", "광역", "자치", "시도", "지사", "시장", "구청", "군수", "당선인", "교육감",
                "의원", "후보", "성명", "이름", "공약", "정책", "추진", "목표", "이행",
                "위원", "회장", "단체", "협회", "센터", "기관", "담당", "관리",
            }
            for cand in [c.strip() for c in m_name.group(1).split(",") if c.strip()]:
                if 2 <= len(cand) <= 4 and re.match(r"^[가-힣]{2,4}$", cand) and cand not in NAME_BLACKLIST_FAST:
                    meta['name'] = cand
                    break

        # OCR 간격 정규화 텍스트까지 함께 스캔
        compact_text = _compact_spaced_hangul(text)
        scan_text = f"{text}\n{compact_text}"

        # 공약 제목 추출 (먼저)
        meta['pledge_title'] = _extract_pledge_title(text)

        # 헤더로 이미 모두 채웠으면 본문 파싱 생략
        if meta['name'] and meta['position'] and meta['region']:
            return meta

        # 이름 추출 (직책 앞/뒤, 당선인 표기, 시도명+이름 표기 모두 대응)
        # 우선순위: 시도명+이름 > 직책+이름 > 이름+직책 > 당선인 표기
        # 더 정확한 패턴: 시도명/직책 바로 다음에 오는 이름만 매칭
        name_patterns = [
            # 시도명 + 이름 (가장 정확, 바로 다음에 오는 이름만)
            r"(?:서울특별시|서울시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)\s+([가-힣]{2,4})(?:\s|$|[0-9]|공약)",
            # 직책 + 이름 (직책 바로 다음)
            r"(?:서울특별시장|부산광역시장|대구광역시장|인천광역시장|광주광역시장|대전광역시장|울산광역시장|세종특별자치시장|경기도지사|강원도지사|충청북도지사|충청남도지사|전라북도지사|전라남도지사|경상북도지사|경상남도지사|제주특별자치도지사|특별시장|광역시장|시장|도지사|구청장|군수|교육감)\s+([가-힣]{2,4})(?:\s|$|[0-9]|공약)",
            # 이름 + 직책 (이름 바로 다음)
            r"([가-힣]{2,4})\s+(?:서울특별시장|부산광역시장|대구광역시장|인천광역시장|광주광역시장|대전광역시장|울산광역시장|세종특별자치시장|경기도지사|강원도지사|충청북도지사|충청남도지사|전라북도지사|전라남도지사|경상북도지사|경상남도지사|제주특별자치도지사|특별시장|광역시장|시장|도지사|구청장|군수|교육감)",
        ]
        # 당선인 이름 블랙리스트: 직책/일반 명사·접미사 (2~4자 한글만 허용 + 블랙리스트)
        NAME_BLACKLIST = {
            "특별", "광역", "자치", "시도", "지사", "시장", "구청", "군수", "당선인", "교육감",
            "의원", "후보", "성명", "이름", "공약", "정책", "추진", "목표", "이행", "재단",
            "위원", "회장", "단체", "협회", "센터", "기관", "담당", "관리",
        }
        for p in name_patterns:
            name_match = re.search(p, scan_text)
            if name_match:
                name = name_match.group(1)
                if 2 <= len(name) <= 4 and re.match(r"^[가-힣]{2,4}$", name) and name not in NAME_BLACKLIST:
                    meta['name'] = name
                    break

        # 직책 추출 (완전한 형태 우선, 부분 매칭은 신중하게)
        position_patterns = [
            r"(서울특별시장|부산광역시장|대구광역시장|인천광역시장|광주광역시장|대전광역시장|울산광역시장|세종특별자치시장)",
            r"(경기도지사|강원도지사|충청북도지사|충청남도지사|전라북도지사|전라남도지사|경상북도지사|경상남도지사|제주특별자치도지사)",
            r"(국회의원|광역의원|기초의원|시의원|구의원|도의원|교육감)",
            # 구/시/군 단위는 완전한 형태만 매칭 (잘못된 매칭 방지)
            r"([가-힣]{2,}(?:구|시|군))\s*(?:구청장|시장|군수)",
            r"(특별시장|광역시장|시장|도지사|구청장|군수)",
        ]
        for pattern in position_patterns:
            match = re.search(pattern, scan_text)
            if match:
                pos = re.sub(r"\s+", "", match.group(0))
                # 잘못된 추출 검증: "세출" 같은 잘못된 매칭 방지
                invalid_prefixes = ["세출", "출구", "출시", "출군"]
                if not any(pos.startswith(prefix) for prefix in invalid_prefixes):
                    meta['position'] = pos
                    break

        # 지역 추출 (완전한 형태 우선)
        valid_regions = [
            "서울특별시", "서울", "부산광역시", "부산", "대구광역시", "대구",
            "인천광역시", "인천", "광주광역시", "광주", "대전광역시", "대전",
            "울산광역시", "울산", "세종특별자치시", "세종",
            "경기도", "경기", "강원도", "강원", "충청북도", "충북", "충청남도", "충남",
            "전라북도", "전북", "전라남도", "전남", "경상북도", "경북", "경상남도", "경남",
            "제주특별자치도", "제주"
        ]
        # 완전한 형태 우선 매칭
        region_match = None
        for region in valid_regions:
            pattern = re.escape(region)
            match = re.search(pattern, scan_text)
            if match:
                region_match = match
                meta['region'] = _normalize_region_name(region)
                break
        
        # 완전한 형태가 없으면 구/시/군 패턴 시도 (2자 이상만)
        if not region_match:
            region_match = re.search(r"([가-힣]{2,}(?:구|시|군))", scan_text)
            if region_match:
                region = region_match.group(1)
                # 잘못된 추출 검증
                if not any(region.startswith(prefix) for prefix in ["세출", "출구", "출시", "출군"]):
                    meta['region'] = _normalize_region_name(region)

        # 직책에서 지역 역추론
        if not meta['region'] and meta['position']:
            m = re.match(r"([가-힣]+(?:특별시|광역시|도|시|군|구))", meta['position'])
            if m:
                meta['region'] = _normalize_region_name(m.group(1))

        # 지역은 있는데 직책이 비어 있으면 지역으로 직책 추론
        if meta['region'] and not meta['position']:
            inferred = _infer_position_from_region(meta['region'])
            if inferred:
                meta['position'] = inferred

        return meta
    
    def _enhance_winners2022_hits(
        client: OpenAI,
        vs_id: str,
        hits: List[Tuple[float, str, str]],
        max_enhance: int = 18,
    ) -> List[Tuple[float, str, str, Dict]]:
        """
        winners2022 hits에 메타 정보 보강.
        Returns: [(score, filename, raw_text, meta), ...]
        동일 chunk 재파싱 방지용 메타 캐시, 공공 API 이름 보강은 상위 후보(idx<3)만 최대 3회.
        """
        enhanced: List[Tuple[float, str, str, Dict]] = []
        meta_cache: Dict[str, Dict] = {}  # chunk_key -> meta (재파싱 방지)
        api_cache: Dict[str, str] = {}
        api_call_count = 0
        max_api_calls = 3  # 상위 후보에만 제한

        for idx, (score, filename, text) in enumerate(hits[:max_enhance]):
            chunk_key = hashlib.sha256((str(filename) + "\n" + (text or "")[:500]).encode("utf-8", errors="ignore")).hexdigest()
            if chunk_key in meta_cache:
                meta = dict(meta_cache[chunk_key])
            else:
                meta = _extract_winners2022_metadata(text, filename)
                meta_cache[chunk_key] = dict(meta)

            needs_enhancement = not (meta["name"] and meta["position"] and meta["region"])

            if needs_enhancement:
                # 공공 API 이름 보강: 상위 3개 후보에만, 최대 3회
                if idx < 3 and meta.get("position") and meta.get("region") and not meta.get("name"):
                    cache_key = f"{meta['position']}|{meta['region']}"
                    if cache_key in api_cache:
                        meta["name"] = api_cache[cache_key] or None
                    elif api_call_count < max_api_calls:
                        api_name = _get_winner_name_from_api(meta["position"], meta["region"])
                        api_call_count += 1
                        if api_name:
                            meta["name"] = api_name
                            api_cache[cache_key] = api_name
                            logger.debug(f"[API] 이름 조회: {meta['position']} {meta['region']} → {api_name}")
                        else:
                            api_cache[cache_key] = ""

                if not meta["name"] or not meta["position"] or not meta["region"]:
                    source_path = _extract_source_path(text)
                    if source_path or filename:
                        refine_queries = []
                        if meta.get("region") and not meta.get("name"):
                            refine_queries.append(f"{meta['region']} 당선인 이름")
                        elif meta.get("position") and not meta.get("name"):
                            refine_queries.append(f"{meta['position']} 당선인 이름")
                        elif not meta.get("region") and not meta.get("position"):
                            refine_queries.append("제8회 전국동시지방선거 당선인 직책 지역 이름")
                        for rq in refine_queries[:1]:
                            enhance_hits = _search(client, vs_id, rq, k=4, rewrite=False)
                            for _, _, enhance_text in enhance_hits:
                                enhance_meta = _extract_winners2022_metadata(enhance_text, filename)
                                if enhance_meta["name"] and not meta["name"]:
                                    meta["name"] = enhance_meta["name"]
                                if enhance_meta["position"] and not meta["position"]:
                                    meta["position"] = enhance_meta["position"]
                                if enhance_meta["region"] and not meta["region"]:
                                    meta["region"] = enhance_meta["region"]
                                if meta["name"] and meta["position"] and meta["region"]:
                                    break
                            if meta["name"] and meta["position"] and meta["region"]:
                                break

            enhanced.append((score, filename, text, meta))

        logger.debug(f"[WINNERS2022] 메타 보강 완료: {len(enhanced)}개 hit")
        return enhanced

    def _filter_winners_by_user_meta(
        items: List[Tuple[float, str, str, Dict]],
        user_meta: dict,
    ) -> List[Tuple[float, str, str, Dict]]:
        """
        user_meta(region_province, region_city, election_type) 기반으로
        엉뚱한 지역/직책 후보 제거.
        """
        if not user_meta:
            return items
        province = (user_meta.get("region_province") or "").strip()
        city = (user_meta.get("region_city") or "").strip()
        election = (user_meta.get("election_type") or "").strip()
        if not province and not city and not election:
            return items

        def _norm(s: str) -> str:
            return _normalize_region_name(s) if s else ""

        filtered = []
        for score, filename, text, meta in items:
            hit_region = (meta.get("region") or "").strip()
            hit_position = (meta.get("position") or "").strip()
            hit_region_norm = _norm(hit_region)

            if province:
                user_prov_norm = _norm(province)
                if user_prov_norm and hit_region_norm:
                    if hit_region_norm != user_prov_norm and user_prov_norm not in hit_region_norm and hit_region_norm not in user_prov_norm:
                        continue
            if city and hit_region:
                if city not in hit_region and city not in hit_position:
                    continue
            if election and hit_position:
                el = election.lower()
                if "metro_mayor" in el:
                    if "지사" not in hit_position and "시장" not in hit_position:
                        continue
                elif "regional_council" in el:
                    if "의원" not in hit_position:
                        continue
                elif "local_mayor" in el:
                    if "시장" not in hit_position and "구청장" not in hit_position and "군수" not in hit_position:
                        continue
                elif "local_council" in el or "기초의원" in election:
                    if "의원" not in hit_position and "구청장" not in hit_position:
                        continue
            filtered.append((score, filename, text, meta))
        logger.debug(f"[WINNERS2022] user_meta 필터: {len(items)} → {len(filtered)}건")
        return filtered

    def _build_structured_winners_context(
        items: List[Tuple[float, str, str, Dict]],
        max_chars: int = 22_000,
        excerpt_len: int = 800,
    ) -> str:
        """
        구조화된 winners 컨텍스트 생성.
        포맷: 당선인명, 직책, 지역, 공약제목, score, 출처, 근거발췌.
        """
        blocks: List[str] = []
        total = 0
        for score, filename, text, meta in items:
            name = (meta.get("name") or "미상").strip()
            position = (meta.get("position") or "-").strip()
            region = (meta.get("region") or "-").strip()
            title = (meta.get("pledge_title") or "-").strip()
            src = _extract_source_path(text) or filename or "-"
            excerpt = (text or "").strip()[:excerpt_len]
            if len((text or "").strip()) > excerpt_len:
                excerpt += "…"
            name_line = name if name != "미상" else "미상(아래 근거발췌에서 직책과 같은 줄/문단의 2~4자 한글 이름을 찾아 기재)"
            block = (
                f"당선인명: {name_line}\n"
                f"직책: {position}\n"
                f"지역: {region}\n"
                f"공약제목: {title}\n"
                f"score: {score:.3f}\n"
                f"출처: {src}\n"
                f"근거발췌: {excerpt}"
            )
            if total + len(block) > max_chars:
                break
            blocks.append(block)
            total += len(block) + 2
        return "\n\n---\n\n".join(blocks) if blocks else ""

    def _fmt(items: List[Tuple[float, str, str]], max_chars: int) -> str:
        chunks: List[str] = []
        total = 0
        for score, filename, text in items:
            src = _extract_source_path(text)
            src_line = f"\n[출처] {src}" if src else ""
            block = f"--- {filename or 'document'} (score={score:.3f}) ---{src_line}\n{text.strip()}"
            if total + len(block) > max_chars:
                break
            chunks.append(block)
            total += len(block) + 2
        return "\n\n".join(chunks)

    def _has_prefix(text: str, prefix: str) -> bool:
        return (text or "").lstrip().startswith(prefix)

    pledge = (user_pledge or "").strip()
    client = OpenAI(api_key=OPENAI_API_KEY)

    # 인덱싱 완료 여부 — 병렬 수행
    def _ready(vs_id: str) -> None:
        if vs_id:
            _check_vector_store_ready(client, vs_id)

    with ThreadPoolExecutor(max_workers=3) as ex:
        ready_futures = [
            ex.submit(_ready, vector_store_id),
            ex.submit(_ready, regional_vector_store_id),
            ex.submit(_ready, winners2022_vector_store_id),
        ]
        for f in ready_futures:
            f.result()

    # 1) policy 본문 + platform 키워드 + regional(있으면) — 병렬 검색
    policy_hits: List[Tuple[float, str, str]] = []
    platform_hits_kw: List[Tuple[float, str, str]] = []
    regional_hits: List[Tuple[float, str, str]] = []

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_policy = ex.submit(_search, client, vector_store_id, pledge, 32, True)
        f_platform = ex.submit(_search, client, vector_store_id, "개혁신당 강령 정강정책 이념 취지 가치", 18, False)
        f_regional = ex.submit(_search, client, regional_vector_store_id or "", pledge, 16, True)
        policy_hits = f_policy.result()
        platform_hits_kw = f_platform.result()
        regional_hits = f_regional.result() if regional_vector_store_id else []

    policy_all = _dedup([*policy_hits, *platform_hits_kw])
    platform_hits: List[Tuple[float, str, str]] = []
    pledges_hits: List[Tuple[float, str, str]] = []
    unknown_hits: List[Tuple[float, str, str]] = []

    for score, fn, txt in policy_all:
        bucket = _source_bucket(_extract_source_path(txt))
        if bucket == "platform":
            platform_hits.append((score, fn, txt))
        elif bucket == "pledge":
            pledges_hits.append((score, fn, txt))
        else:
            unknown_hits.append((score, fn, txt))

    # fallback: 출처 라인이 청크에 없으면 filename/헤더로만 보조 분류
    for score, fn, txt in unknown_hits:
        f = (fn or "")
        if "정강정책" in f or "강령" in f or _has_prefix(txt, "[정강정책]"):
            platform_hits.append((score, fn, txt))
        else:
            pledges_hits.append((score, fn, txt))

    # 안전: platform에 들어간 청크는 pledge에서 제거
    platform_key = {
        hashlib.sha256((fn + "\n" + txt[:200]).encode("utf-8", errors="ignore")).hexdigest()
        for _, fn, txt in platform_hits
    }
    pledges_hits = [
        h for h in pledges_hits
        if hashlib.sha256((h[1] + "\n" + h[2][:200]).encode("utf-8", errors="ignore")).hexdigest() not in platform_key
    ]

    # 4) winners2022 store (선택) - 다중 질의로 리콜 강화
    winners_hits: List[Tuple[float, str, str]] = []
    if winners2022_vector_store_id:
        def _build_winners2022_queries(pledge_text: str, meta: dict) -> List[str]:
            """복붙한 긴 공약 원문에서도 당선인 검색이 빠지지 않도록 다중 질의 생성. 중복 제거."""
            out: List[str] = []
            if not (pledge_text or "").strip():
                return ["제8회 전국동시지방선거 당선인 공약"]
            p = (pledge_text or "").strip()
            # 정규화 원문 (띄어쓰기 압축 + 한글 간격 정규화)
            normalized = re.sub(r"\s+", " ", _compact_spaced_hangul(p)).strip()
            if len(normalized) >= 15:
                out.append(normalized[:2000])  # 길면 앞 2000자
            # 첫 줄 / 첫 문장
            lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
            if lines:
                first_line = lines[0]
                if 10 <= len(first_line) <= 300:
                    out.append(first_line)
                # 첫 문장 (마침표/물음표/느낌표까지)
                first_sent = re.split(r"[.!?。！？]", first_line, maxsplit=1)[0].strip()
                if first_sent != first_line and 10 <= len(first_sent) <= 200:
                    out.append(first_sent)
            # 본문 중간 스니펫 (긴 텍스트일 때)
            if len(p) > 500:
                start = len(p) // 3
                snippet = p[start : start + 350].strip()
                if len(snippet) >= 30:
                    out.append(snippet)
            # user_meta 결합 질의
            if meta:
                meta_parts = []
                if meta.get("election_type"):
                    meta_parts.append(meta["election_type"])
                if meta.get("region_province"):
                    meta_parts.append(meta["region_province"])
                if meta.get("region_city"):
                    meta_parts.append(meta["region_city"])
                if meta_parts:
                    head = " ".join(meta_parts)
                    out.append(f"{head} {p[:200]}")
            # 고정 앵커
            out.append("제8회 전국동시지방선거 당선인 공약")
            # 중복 제거 (정규화 후 동일한 질의 제거)
            seen: set = set()
            unique: List[str] = []
            for q in out:
                key = re.sub(r"\s+", " ", (q or "").strip())[:500]
                if key not in seen and len(key) >= 8:
                    seen.add(key)
                    unique.append((q or "").strip())
            return unique if unique else ["제8회 전국동시지방선거 당선인 공약"]

        queries = _build_winners2022_queries(pledge, user_meta or {})[:3]  # 상한 3개

        # rewrite True/False 병렬 처리
        all_winners_hits = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for q in queries:
                futures.append(executor.submit(_search, client, winners2022_vector_store_id, q, 20, True))
                futures.append(executor.submit(_search, client, winners2022_vector_store_id, q, 20, False))
            for f in as_completed(futures):
                all_winners_hits.extend(f.result())
        winners_hits_raw = _dedup(all_winners_hits)[:30]

        logger.debug(f"[WINNERS2022] 질의 {len(queries)}개(상한3), hit dedup 후 {len(winners_hits_raw)}개")

        # a. 다중 질의 검색 → b. 메타 보강 → c. user_meta 필터 → d. 구조화 컨텍스트
        if winners_hits_raw:
            winners_enhanced = _enhance_winners2022_hits(
                client, winners2022_vector_store_id, winners_hits_raw, max_enhance=18
            )
            winners_filtered = _filter_winners_by_user_meta(winners_enhanced, user_meta or {})
            winners2022_context = _build_structured_winners_context(
                winners_filtered, max_chars=14_000, excerpt_len=500
            )
        else:
            winners2022_context = ""
    else:
        winners2022_context = ""

    # 컨텍스트 구성 (속도 우선: 아이템 수·길이 합리적 축소, 출력 포맷 유지)
    platform_context = _fmt(platform_hits[: max(5, max_results)], max_chars=8_000) or "(정강·정책 문서 없음)"
    pledges_context = _fmt(pledges_hits[: max(8, max_results * 2)], max_chars=12_000) or "(우리당 공약 문서 없음)"
    regional_context = _fmt(regional_hits[:6], max_chars=6_000) if regional_hits else ""

    # 프롬프트 생성 후 최종 답변만 생성
    from backend.prompts import load_system_prompt, build_user_message

    system = load_system_prompt()
    user = build_user_message(
        platform_context, pledges_context, regional_context, pledge, winners2022_context, user_meta=user_meta
    )

    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = resp.choices[0].message.content or ""
    if not text.strip():
        raise RuntimeError("모델이 텍스트를 반환하지 않음")
    return text.strip()


def _is_query_mode(text: str) -> bool:
    """20자 미만 또는 1문장 → QUERY mode."""
    t = (text or "").strip()
    if len(t) < 20:
        return True
    # 1문장: 마침표/느낌표/물음표가 0~1개
    punct_count = sum(1 for c in t if c in ".!?。！？")
    return punct_count <= 1


def run_verify_judge(
    vector_store_id: str,
    user_pledge: str,
    regional_vector_store_id: str = "",
    max_results: int | None = None,
) -> Dict:
    """
    Strict policy judge: JSON output with evidence, specificity cap, QUERY/VERIFY mode.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    _check_vector_store_ready(client, vector_store_id)
    if regional_vector_store_id:
        _check_vector_store_ready(client, regional_vector_store_id)
    limit = max_results if max_results is not None else FILE_SEARCH_MAX_RESULTS

    input_text = f"Evaluate the following pledge. Return only valid JSON.\n\n{user_pledge}"

    def _tool(vs_id: str):
        return {"type": "file_search", "vector_store_ids": [vs_id], "max_num_results": limit}

    tools = [_tool(vector_store_id)]
    if regional_vector_store_id:
        tools.append(_tool(regional_vector_store_id))

    response = client.responses.create(
        model=CHAT_MODEL,
        input=input_text,
        instructions=_JUDGE_INSTRUCTIONS,
        tools=tools,
    )

    if getattr(response, "status", None) != "completed":
        raise RuntimeError(f"Responses API 실패: status={getattr(response, 'status', 'unknown')}")

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

    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
        text = "\n".join(lines)
    raw = json.loads(text)

    # QUERY mode: server-side override (20자 미만 또는 1문장)
    if _is_query_mode(user_pledge):
        raw["mode"] = "QUERY"
        raw["final_score"] = None
        raw["ideology_fit_score"] = None
        raw["policy_slot_count"] = raw.get("policy_slot_count", 0)
        raw["status"] = raw.get("status", "INSUFFICIENT_INFO")
        raw.setdefault("evidence", {"input_quotes": [], "reference_quotes": []})
        raw.setdefault("missing_fields", ["구체적 수단", "대상", "수치·이행 계획"])
        raw.setdefault("similar_candidates", [])

    # Scoring cap by policy_slot_count (슬롯 0~1 → 55 이하, 2개 → 70 이하, 3+ → 80+ 가능)
    slot_count = raw.get("policy_slot_count")
    final = raw.get("final_score")
    spec = raw.get("specificity_score")
    if final is not None and raw.get("mode") == "VERIFY":
        if slot_count is not None:
            if slot_count <= 1:
                raw["final_score"] = min(final, 55)
                raw["confidence"] = "LOW"
            elif slot_count == 2:
                raw["final_score"] = min(final, 70)
        # 제목/키워드 일치만으로 80점 이상 금지 (specificity < 50이면 80 상한)
        if spec is not None and spec < 50 and final and final > 80:
            raw["final_score"] = min(raw["final_score"], 80)

    return raw


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

    # 제목·한 줄만 적은 경우: rubric 점수 강제 상한 (80자 미만). note는 모델이 우리당 공약 내용을 참조해 작성하도록 프롬프트에 위임.
    _short_input = len(user_pledge.strip()) < 80
    if _short_input:
        for item in platform_items + pledges_items:
            if isinstance(item, dict):
                item["score_0_5"] = min(item.get("score_0_5", 0), 2)
                # note에 "90% 일치" 등 잘못된 표현만 제거, 구체적 지적은 유지
                n = (item.get("note") or "").strip()
                if any(x in n for x in ("90%", "거의 동일", "사실상 동일", "동일하여", "동일로")):
                    item["note"] = "우리당 공약에 구체적 방안이 있으나, 제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 보완 필요."

    platform_avg = avg_score(platform_items)
    pledges_avg = avg_score(pledges_items)
    conflicts_avg = avg_score(conflicts_items)

    fit_score = round((platform_avg * 0.4 + pledges_avg * 0.4 + (5 - conflicts_avg) * 0.2) * 20, 1)
    if fit_score > 100:
        fit_score = 100.0

    if _short_input and fit_score > 40:
        fit_score = min(fit_score, 40.0)

    fit_verdict = "강한 부합" if fit_score >= 80 else "부합" if fit_score >= 60 else "부분부합" if fit_score >= 40 else "미부합"

    improvements = raw.get("improvements", [])
    if _short_input:
        has_concreteness = any("구체" in str(t.get("title", "") or t.get("detail", "")) for t in improvements)
        if not has_concreteness:
            improvements = [{"title": "구체성 보완 필요", "detail": "제시공약은 명칭만 있어 구체적으로 뭘 하겠다는 내용이 없음. 우리당 공약의 구체적 방안을 참고해 보완하세요.", "evidence": []}] + improvements

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
        "improvements": improvements,
    }

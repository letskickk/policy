import argparse
import hashlib
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import database
from backend.policy_ssot import find_policy_document_by_source, upsert_policy_document


DEFAULT_BASE_URL = "http://gw.reformparty.kr"
DEFAULT_MODULE = "ONECHAMBER"
DEFAULT_ROOT_FOLDER = "전자문서함"
DEFAULT_OWNER_FOLDER = "개혁신당"
DEFAULT_TARGET_FOLDER = "최고위원회의"
DEFAULT_RULES_FOLDER = "당헌당규"
DEFAULT_DOCUMENT_URL = "#/UQ/UQA/UQA0000?specialLnb=Y&moduleCode=UQ&menuCode=UQA&pageCode=UQA0500"


@dataclass
class AmaranthDocument:
    source_ref: str
    source_url: str
    title: str
    published_at: Optional[str]
    summary: str
    body: str
    doc_type: str
    metadata: dict


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _click_first_visible(locator, *, timeout: int = 30000) -> None:
    candidate = _first_visible(locator)
    if candidate is None:
        raise RuntimeError("no visible clickable element matched")
    _click_locator(candidate, timeout=timeout)


def _click_locator(locator, *, timeout: int = 30000) -> None:
    last_error: Optional[Exception] = None
    try:
        locator.scroll_into_view_if_needed(timeout=timeout)
    except Exception:
        pass
    for click_action in (
        lambda: locator.click(timeout=timeout),
        lambda: locator.click(timeout=timeout, force=True),
        lambda: locator.evaluate("(el) => el.click()"),
    ):
        try:
            click_action()
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError("failed to click locator") from last_error


def _click_text(page, text: str, *, exact: bool = True, timeout: int = 30000) -> None:
    variants = []
    if exact:
        variants.append(page.get_by_text(text, exact=True))
    variants.append(page.get_by_text(text, exact=False))
    pattern = re.compile(r"\s+".join(map(re.escape, text.split())))
    variants.append(page.get_by_text(pattern))
    last_error: Optional[Exception] = None
    for locator in variants:
        try:
            _click_first_visible(locator, timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"could not click text: {text}") from last_error


def _resolve_storage_state_path(storage_state: str) -> Path:
    raw = (storage_state or "").strip()
    if not raw:
        return ROOT / "data" / "amaranth-storage-state.json"
    if platform.system() != "Windows" and re.match(r"^[A-Za-z]:\\", raw):
        return ROOT / "data" / Path(raw).name
    return Path(raw)


def _dump_debug_state(page, *, storage_state_path: Path, stage: str) -> None:
    debug_dir = storage_state_path.parent
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "-", stage).strip("-") or "state"
    body_path = debug_dir / f"amaranth-debug-{safe_stage}.txt"
    html_path = debug_dir / f"amaranth-debug-{safe_stage}.html"
    meta_path = debug_dir / f"amaranth-debug-{safe_stage}.json"
    body_text = ""
    html_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    try:
        html_text = page.content()
    except Exception:
        pass
    body_path.write_text(body_text[:50000], encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "stage": stage,
                "url": page.url,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _dismiss_company_selection_modal(page, *, company_name: str, timeout: int = 15000) -> None:
    modal_hint = page.get_by_text("회사를 선택해 주세요", exact=False)
    if modal_hint.count() == 0:
        return
    try:
        modal_hint.first.wait_for(state="visible", timeout=5000)
    except Exception:
        return

    modal_root = page.locator(
        "div.commonPopup.userInfoPop, div:has(.companySelectWrap):has-text('회사를 선택해 주세요')"
    ).first
    if modal_root.count():
        close_button = modal_root.locator(".cls, .btnClose, .close").first
        if close_button.count():
            try:
                _click_locator(close_button, timeout=3000)
                modal_hint.first.wait_for(state="hidden", timeout=3000)
                return
            except Exception:
                pass

    preferred_rows = []
    if company_name:
        preferred_rows.extend(
            [
                modal_root.locator("tr", has_text=company_name).first if modal_root.count() else page.locator("tr", has_text=company_name).first,
                modal_root.locator("li", has_text=company_name).first if modal_root.count() else page.locator("li", has_text=company_name).first,
                modal_root.locator("div", has_text=company_name).first if modal_root.count() else page.locator("div", has_text=company_name).first,
            ]
        )
    preferred_rows.extend(
        [
            modal_root.locator("tbody tr").first if modal_root.count() else page.locator("tbody tr").first,
            modal_root.locator("tr").nth(1) if modal_root.count() else page.locator("tr").nth(1),
            modal_root.locator("li[role='option']").first if modal_root.count() else page.locator("li[role='option']").first,
        ]
    )

    for row in preferred_rows:
        try:
            if row.count() and row.is_visible():
                _click_locator(row, timeout=3000)
                break
        except Exception:
            try:
                row.evaluate("(el) => el.click()")
                break
            except Exception:
                continue

    try:
        if modal_root.count():
            confirm_button = modal_root.locator("button", has_text="확인").first
            if confirm_button.count():
                _click_locator(confirm_button, timeout=5000)
            else:
                _click_text(page, "확인", exact=True, timeout=5000)
        else:
            _click_text(page, "확인", exact=True, timeout=5000)
    except Exception:
        confirm_button = modal_root.locator("button", has_text="확인").first if modal_root.count() else page.locator("button", has_text="확인").first
        if confirm_button.count():
            try:
                _click_locator(confirm_button, timeout=3000)
            except Exception:
                confirm_button.evaluate("(el) => el.click()")

    try:
        modal_hint.first.wait_for(state="hidden", timeout=timeout)
    except Exception:
        try:
            page.keyboard.press("Escape")
            modal_hint.first.wait_for(state="hidden", timeout=3000)
        except Exception:
            pass


def _ensure_document_view(page, *, base_url: str, module_name: str, timeout: int = 30000) -> None:
    document_url = f"{base_url.rstrip('/')}/{DEFAULT_DOCUMENT_URL}"
    if "pageCode=UQA0500" in page.url:
        return
    try:
        page.goto(document_url, wait_until="networkidle")
        page.wait_for_timeout(3000)
    except Exception:
        pass
    if "pageCode=UQA0500" in page.url or page.locator("#UQA_UQA0500").count():
        return
    try:
        _click_text(page, module_name, timeout=timeout)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
    except Exception:
        pass
    if "pageCode=UQA0500" in page.url or page.locator("#UQA_UQA0500").count():
        return
    page.goto(document_url, wait_until="networkidle")
    page.wait_for_timeout(3000)


def _expand_named_folder(page, folder_name: str, *, timeout: int = 10000) -> None:
    folder_row = page.locator(".subTit", has=page.locator(".folderName", has_text=folder_name)).first
    if folder_row.count() == 0:
        fallback_targets = [
            page.locator(".sideLnbMenu, .onechamberSide, .ofONECHAMBERMenuLnb").get_by_text(folder_name, exact=True).first,
            page.get_by_text(folder_name, exact=True).first,
            page.get_by_text(folder_name, exact=False).first,
        ]
        last_error: Optional[Exception] = None
        for target in fallback_targets:
            try:
                if target.count() and target.is_visible():
                    _click_locator(target, timeout=timeout)
                    page.wait_for_timeout(1500)
                    return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"folder row not found: {folder_name}") from last_error
    expand_button = folder_row.locator(".arr1, .arr").first
    child_menu = folder_row.locator("xpath=ancestor::div[contains(@class,'folderAll')][1]//ul[1]").first
    before = child_menu.locator("> *").count() if child_menu.count() else 0
    if expand_button.count():
        try:
            _click_locator(expand_button, timeout=timeout)
        except Exception:
            expand_button.evaluate("(el) => el.click()")
    else:
        try:
            _click_locator(folder_row, timeout=timeout)
        except Exception:
            folder_row.evaluate("(el) => el.click()")
    deadline = time.time() + (timeout / 1000)
    while time.time() < deadline:
        try:
            count = child_menu.locator("> *").count() if child_menu.count() else 0
            if count > before:
                return
        except Exception:
            pass
        page.wait_for_timeout(250)


def _click_named_folder(page, folder_name: str, *, timeout: int = 10000) -> None:
    folder_row = page.locator(".subTit", has=page.locator(".folderName", has_text=folder_name)).first
    if folder_row.count() == 0:
        fallback_targets = [
            page.locator(".sideLnbMenu, .onechamberSide, .ofONECHAMBERMenuLnb").get_by_text(folder_name, exact=True).first,
            page.get_by_text(folder_name, exact=True).first,
            page.get_by_text(folder_name, exact=False).first,
        ]
        last_error: Optional[Exception] = None
        for target in fallback_targets:
            try:
                if target.count() and target.is_visible():
                    _click_locator(target, timeout=timeout)
                    page.wait_for_timeout(1500)
                    return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"folder row not found: {folder_name}") from last_error
    try:
        _click_locator(folder_row, timeout=timeout)
    except Exception:
        folder_row.evaluate("(el) => el.click()")


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _normalize_date(text: str) -> Optional[str]:
    korean = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if korean:
        year, month, day = korean.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    matched = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if matched:
        year, month, day = matched.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    compact = re.search(r"(20\d{2})(\d{2})(\d{2})", re.sub(r"\s+", "", text))
    if compact:
        year, month, day = compact.groups()
        return f"{year}-{month}-{day}"
    return None


def _extract_meeting_held_at(detail_text: str) -> Optional[str]:
    for line in _clean_lines(detail_text):
        compact = re.sub(r"\s+", "", line)
        if "일시" not in compact:
            continue
        parsed = _normalize_date(line)
        if parsed:
            return parsed
    return None


def _extract_title(text: str) -> str:
    for line in _clean_lines(text):
        if len(line) >= 2:
            return line[:200]
    return "Untitled Amaranth document"


def _looks_like_metadata_only(text: str) -> bool:
    lines = _clean_lines(text)
    if not lines:
        return True
    joined = "\n".join(lines[:12])
    has_file_ext = any(line.lower() in {".hwp", ".hwpx", ".pdf", ".doc", ".docx"} for line in lines[:6])
    has_size = bool(re.search(r"\b\d+(?:\.\d+)?\s*(KB|MB|GB)\b", joined, re.IGNORECASE))
    has_date = bool(re.search(r"\b20\d{2}[.\-]\d{1,2}[.\-]\d{1,2}\b", joined))
    has_meeting_markers = any(token in joined for token in ("회의록", "개 요", "일  시", "장  소", "참  석", "안건"))
    if has_meeting_markers:
        return False
    if len(lines) <= 8 and has_file_ext and (has_size or has_date):
        return True
    if len(joined) < 120 and has_file_ext:
        return True
    return False


def _viewer_abs_url(base_url: str, viewer_url: str) -> str:
    if viewer_url.startswith("http://") or viewer_url.startswith("https://"):
        return viewer_url
    return f"{base_url.rstrip('/')}/{viewer_url.lstrip('/')}"


def _request_viewer_url(page, *, row) -> Optional[str]:
    title_locator = row.locator(".tooltipname").first
    click_targets = [
        title_locator,
        row.locator(".nameClass").first,
        row,
    ]
    click_actions = [
        lambda locator: locator.dblclick(timeout=5000),
        lambda locator: _click_locator(locator, timeout=5000),
    ]
    for target in click_targets:
        if target.count() == 0:
            continue
        for click_action in click_actions:
            try:
                with page.expect_response(lambda response: response.url.endswith("/ecm/ecm017A03"), timeout=12000) as response_info:
                    click_action(target)
                payload = response_info.value.json()
            except Exception:
                continue
            if not isinstance(payload, dict) or payload.get("resultCode") != 0:
                continue
            viewer_url = payload.get("resultData")
            if isinstance(viewer_url, str) and viewer_url.strip():
                return viewer_url
    return None


def _reconstruct_viewer_text(spans: list[dict]) -> str:
    pages: dict[int, list[dict]] = {}
    for span in spans:
        page_index = int(span.get("page", 0))
        pages.setdefault(page_index, []).append(span)

    chunks: list[str] = []
    for page_index in sorted(pages):
        page_spans = sorted(pages[page_index], key=lambda item: (item["top"], item["left"]))
        line_buckets: list[dict] = []
        for span in page_spans:
            text = span.get("char", "")
            if not text:
                continue
            top = float(span.get("top", 0))
            left = float(span.get("left", 0))
            if not line_buckets or abs(line_buckets[-1]["top"] - top) > 1.5:
                line_buckets.append({"top": top, "items": [(left, text)]})
            else:
                line_buckets[-1]["items"].append((left, text))

        page_lines: list[str] = []
        for bucket in line_buckets:
            chars = [char for _, char in sorted(bucket["items"], key=lambda item: item[0])]
            line = "".join(chars).strip()
            if line:
                page_lines.append(line)
        if page_lines:
            chunks.append("\n".join(page_lines))

    return "\n\n".join(chunks).strip()


def _extract_viewer_text(context, *, base_url: str, viewer_url: str) -> str:
    viewer_page = context.new_page()
    try:
        viewer_page.goto(_viewer_abs_url(base_url, viewer_url), wait_until="load")
        viewer_page.wait_for_timeout(4000)
        text_layer = viewer_page.locator(".textLayer .text").first
        try:
            text_layer.wait_for(timeout=15000)
            spans = viewer_page.evaluate(
                """() => Array.from(document.querySelectorAll('.textLayer .text')).map((node) => {
                    const style = window.getComputedStyle(node);
                    const layer = node.closest('.textLayer');
                    const pageMatch = layer?.id ? layer.id.match(/(\\d+)$/) : null;
                    return {
                        page: pageMatch ? Number(pageMatch[1]) : 0,
                        top: parseFloat(style.top || '0') || 0,
                        left: parseFloat(style.left || '0') || 0,
                        char: node.getAttribute('data-char') || node.textContent || '',
                    };
                })"""
            )
            if isinstance(spans, list):
                reconstructed = _reconstruct_viewer_text(spans)
                if reconstructed.strip():
                    return reconstructed
        except Exception:
            pass

        body_text = ""
        try:
            body_text = viewer_page.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""
        lines = [
            line.strip()
            for line in body_text.splitlines()
            if line.strip()
            and line.strip() not in {"로딩중입니다.", "Loading", "정보보기", "링크복사", "원커넥트공유"}
        ]
        return "\n".join(lines[:4000]).strip()
    finally:
        viewer_page.close()


def _extract_participants(lines: Iterable[str]) -> list[str]:
    participants: list[str] = []
    for line in lines:
        if line.startswith("??"):
            _, _, remainder = line.partition(":")
            for item in re.split(r"[,/?]", remainder):
                name = item.strip()
                if name:
                    participants.append(name)
    return participants[:20]


def _extract_agenda(lines: Iterable[str]) -> list[str]:
    agenda: list[str] = []
    for line in lines:
        if line.startswith(("??", "-", "?")):
            agenda.append(line)
    return agenda[:20]


def _extract_decisions(lines: Iterable[str]) -> list[str]:
    decisions: list[str] = []
    keywords = ("??", "??", "??", "??", "??")
    for line in lines:
        if any(token in line for token in keywords):
            decisions.append(line)
    return decisions[:20]


def _build_metadata(*, target_folder: str, title: str, published_at: Optional[str], detail_text: str, doc_kind: str) -> dict:
    lines = _clean_lines(detail_text)
    title_date = _normalize_date(title)
    held_at = _extract_meeting_held_at(detail_text) if doc_kind == "meeting" else None
    metadata = {
        "collector": "amaranth-playwright",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "original_title": title,
        "folder_name": target_folder,
        "source_file_date": title_date,
        "extraction_quality": "metadata_only" if _looks_like_metadata_only(detail_text) else "full_text",
    }
    if doc_kind == "meeting":
        metadata.update(
            {
                "meeting_type": target_folder,
                "held_at": held_at or published_at,
                "participants": _extract_participants(lines),
                "agenda_items": _extract_agenda(lines),
                "decisions": _extract_decisions(lines),
            }
        )
    else:
        key_articles = [line for line in lines if line.startswith(("제", "Article"))][:20]
        metadata.update(
            {
                "rule_kind": "party_rule",
                "rule_kind_label": target_folder,
                "effective_from": published_at,
                "key_articles": key_articles,
            }
        )
    return metadata


def _to_document(
    *,
    source_ref: str,
    source_url: str,
    row_text: str,
    title_text: Optional[str],
    detail_text: str,
    target_folder: str,
    doc_type: str,
    doc_kind: str,
) -> AmaranthDocument:
    title_source = title_text or row_text
    title = _extract_title(title_source)
    title_date = _normalize_date(title_source)
    held_at = _extract_meeting_held_at(detail_text) if doc_kind == "meeting" else None
    published_at = held_at or title_date or _normalize_date(row_text) or _normalize_date(detail_text)
    lines = _clean_lines(detail_text)
    summary = "\n".join(lines[:5])[:4000] if lines else title
    body = detail_text.strip()[:50000] if detail_text.strip() else title
    metadata = _build_metadata(
        target_folder=target_folder,
        title=title,
        published_at=published_at,
        detail_text=detail_text,
        doc_kind=doc_kind,
    )
    return AmaranthDocument(
        source_ref=source_ref,
        source_url=source_url,
        title=title,
        published_at=published_at,
        summary=summary,
        body=body,
        doc_type=doc_type,
        metadata=metadata,
    )


def _build_source_ref(*, target_folder: str, title: str, row_text: str) -> str:
    digest = hashlib.sha1(row_text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"amaranth:{target_folder}:{title}:{digest}"


def _save_document(document: AmaranthDocument, *, owner_name: str, dry_run: bool) -> dict:
    existing = find_policy_document_by_source(source_ref=document.source_ref)
    if existing:
        existing_body = existing.get("body") or ""
        new_body_is_metadata_only = _looks_like_metadata_only(document.body)
        existing_body_is_metadata_only = _looks_like_metadata_only(existing_body)
        if new_body_is_metadata_only and not existing_body_is_metadata_only:
            document.body = existing_body
            document.summary = existing.get("summary") or document.summary
            if not document.published_at:
                document.published_at = existing.get("published_at")
            document.metadata = {
                **(existing.get("metadata") or {}),
                **document.metadata,
                "extraction_quality": "preserved_existing_full_text",
            }
    payload = {
        "document_id": existing["id"] if existing else None,
        "title": document.title,
        "doc_type": document.doc_type,
        "summary": document.summary,
        "body": document.body,
        "speaker": None,
        "speaker_name": None,
        "owner_name": owner_name,
        "source_url": document.source_url,
        "source_ref": document.source_ref,
        "published_at": document.published_at,
        "status": "active",
        "metadata": document.metadata,
        "actor_id": None,
    }
    if dry_run:
        return {"existing_id": existing["id"] if existing else None, **payload}
    return upsert_policy_document(**payload)


def _login_amaranth(page, *, company_code: str, company_name: str, login_id: str, login_password: str) -> None:
    company = page.locator("#reqCompCd")
    if company.count():
        company_field = company.nth(0)
        if company_field.is_visible() and company_field.is_enabled():
            company_field.fill(company_code)

    login_id_field = page.locator("#reqLoginId")
    if login_id_field.count():
        candidate = login_id_field.nth(0)
        if candidate.is_visible() and candidate.is_enabled():
            candidate.fill(login_id)

    password_field = page.locator("#reqLoginPw")
    if password_field.count() and not password_field.nth(0).is_visible():
        _click_first_visible(page.locator("button"))
        page.wait_for_timeout(1500)

    if password_field.count():
        candidate = password_field.nth(0)
        if candidate.is_visible() and candidate.is_enabled():
            candidate.fill(login_password)
            _click_first_visible(page.locator("button"))
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

    _dismiss_company_selection_modal(page, company_name=company_name)


def sync_amaranth_documents(
    *,
    base_url: str,
    headless: bool,
    limit: int,
    storage_state: str,
    module_name: str,
    root_folder: str,
    owner_folder: str,
    target_folder: str,
    owner_name: str,
    doc_type: str,
    doc_kind: str,
    dry_run: bool,
) -> dict:
    load_dotenv(ROOT / ".env")
    database.init_db()

    company_code = _env_required("AMARANTH_COMPANY_CODE")
    login_id = _env_required("AMARANTH_LOGIN_ID")
    login_password = _env_required("AMARANTH_LOGIN_PASSWORD")
    company_name = os.getenv("AMARANTH_OWNER_NAME", DEFAULT_OWNER_FOLDER).strip()

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    storage_state_path = _resolve_storage_state_path(storage_state)
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    results = {
        "seen": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "sample_rows": [],
        "sample_documents": [],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context_kwargs = {"ignore_https_errors": True}
        if storage_state_path.exists():
            context_kwargs["storage_state"] = str(storage_state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            page.goto(base_url, wait_until="networkidle")

            if page.locator("#reqCompCd").count():
                _login_amaranth(
                    page,
                    company_code=company_code,
                    company_name=company_name,
                    login_id=login_id,
                    login_password=login_password,
                )

            context.storage_state(path=str(storage_state_path))

            page.wait_for_timeout(3000)
            _dismiss_company_selection_modal(page, company_name=company_name)
            _ensure_document_view(page, base_url=base_url, module_name=module_name)

            _click_locator(page.locator("#UQA_UQA0500"), timeout=30000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            try:
                _click_text(page, owner_folder)
                _expand_named_folder(page, owner_folder)
            except Exception:
                _dump_debug_state(page, storage_state_path=storage_state_path, stage="owner-folder-miss")
                raise
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            try:
                _click_named_folder(page, target_folder)
            except Exception:
                _dump_debug_state(page, storage_state_path=storage_state_path, stage="target-folder-miss")
                raise
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)
            if dry_run:
                _dump_debug_state(page, storage_state_path=storage_state_path, stage="target-folder-open")

            row_locator = page.locator("div.customListRow[data-list-item='true']")
            total_rows = row_locator.count()
            if total_rows == 0:
                _dump_debug_state(page, storage_state_path=storage_state_path, stage="no-doc-rows")
            for index in range(total_rows):
                if results["seen"] >= limit:
                    break
                row = row_locator.nth(index)
                title_locator = row.locator(".tooltipname").first
                if title_locator.count() == 0:
                    continue
                row_text = row.inner_text().strip()
                title_text = title_locator.inner_text().strip()
                if title_text and len(results["sample_rows"]) < 10:
                    results["sample_rows"].append(title_text[:300])
                if not row_text or row_text in {root_folder, owner_folder, target_folder}:
                    continue
                if len(title_text) < 4:
                    continue
                results["seen"] += 1

                title = _extract_title(title_text)
                source_ref = _build_source_ref(target_folder=target_folder, title=title, row_text=title_text)
                existing_document = find_policy_document_by_source(source_ref=source_ref)
                detail_text = row_text
                source_url = page.url
                viewer_url = None
                try:
                    viewer_url = _request_viewer_url(page, row=row)
                except Exception:
                    viewer_url = None
                if viewer_url:
                    try:
                        extracted_text = _extract_viewer_text(context, base_url=base_url, viewer_url=viewer_url)
                        if extracted_text.strip():
                            detail_text = extracted_text
                            source_url = _viewer_abs_url(base_url, viewer_url)
                    except Exception:
                        pass

                document = _to_document(
                    source_ref=source_ref,
                    source_url=source_url,
                    row_text=row_text,
                    title_text=title_text,
                    detail_text=detail_text,
                    target_folder=target_folder,
                    doc_type=doc_type,
                    doc_kind=doc_kind,
                )
                if doc_kind == "meeting":
                    document.metadata["source_file_date"] = _normalize_date(title_text)
                if len(results["sample_documents"]) < 10:
                    results["sample_documents"].append(
                        {
                            "title": document.title,
                            "published_at": document.published_at,
                            "source_ref": document.source_ref,
                            "body_preview": document.body[:120],
                        }
                    )
                _save_document(document, owner_name=owner_name, dry_run=dry_run)
                results["updated" if existing_document else "created"] += 1

        except PlaywrightTimeoutError as exc:
            _dump_debug_state(page, storage_state_path=storage_state_path, stage="timeout")
            results["errors"].append(f"timeout: {exc}")
            raise
        except Exception as exc:
            _dump_debug_state(page, storage_state_path=storage_state_path, stage="exception")
            results["errors"].append(f"error: {exc}")
            raise
        finally:
            context.storage_state(path=str(storage_state_path))
            context.close()
            browser.close()

    return results


def sync_amaranth_meetings(*, base_url: str, headless: bool, limit: int, storage_state: str, dry_run: bool) -> dict:
    return sync_amaranth_documents(
        base_url=base_url,
        headless=headless,
        limit=limit,
        storage_state=storage_state,
        module_name=DEFAULT_MODULE,
        root_folder=DEFAULT_ROOT_FOLDER,
        owner_folder=DEFAULT_OWNER_FOLDER,
        target_folder=os.getenv("AMARANTH_MEETINGS_FOLDER", DEFAULT_TARGET_FOLDER),
        owner_name=os.getenv("AMARANTH_OWNER_NAME", DEFAULT_OWNER_FOLDER),
        doc_type="meeting_note",
        doc_kind="meeting",
        dry_run=dry_run,
    )


def sync_amaranth_rules(*, base_url: str, headless: bool, limit: int, storage_state: str, dry_run: bool) -> dict:
    return sync_amaranth_documents(
        base_url=base_url,
        headless=headless,
        limit=limit,
        storage_state=storage_state,
        module_name=DEFAULT_MODULE,
        root_folder=DEFAULT_ROOT_FOLDER,
        owner_folder=DEFAULT_OWNER_FOLDER,
        target_folder=os.getenv("AMARANTH_RULES_FOLDER", DEFAULT_RULES_FOLDER),
        owner_name=os.getenv("AMARANTH_OWNER_NAME", DEFAULT_OWNER_FOLDER),
        doc_type="party_rule",
        doc_kind="rule",
        dry_run=dry_run,
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Sync Amaranth ONECHAMBER documents into policy_documents.")
    parser.add_argument("--base-url", default=os.getenv("AMARANTH_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--headless", action="store_true", default=os.getenv("AMARANTH_HEADLESS", "0") == "1")
    parser.add_argument("--limit", type=int, default=int(os.getenv("AMARANTH_LIMIT", "20")))
    parser.add_argument("--storage-state", default=os.getenv("AMARANTH_STORAGE_STATE", str(ROOT / "data" / "amaranth-storage-state.json")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--kind", choices=("meetings", "rules"), default="meetings")
    args = parser.parse_args()

    if args.kind == "rules":
        results = sync_amaranth_rules(
            base_url=args.base_url,
            headless=args.headless,
            limit=args.limit,
            storage_state=args.storage_state,
            dry_run=args.dry_run,
        )
    else:
        results = sync_amaranth_meetings(
            base_url=args.base_url,
            headless=args.headless,
            limit=args.limit,
            storage_state=args.storage_state,
            dry_run=args.dry_run,
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


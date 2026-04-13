"""
후보자 AI 정책 도구 라우트 — /api/tools/*
승인된 후보자가 직접 정책 콘텐츠(정책포지션, 지역공약, 논평, 메시지)를 생성.
"""

import json
import logging
import time
from typing import Literal, Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ELECTION_LABELS = {
    "metro_mayor": "광역단체장",
    "local_mayor": "기초단체장",
    "regional_council": "광역의원",
    "local_council": "기초의원",
    "party_official": "당직자",
}


def register_tools_routes(app, require_approved, _client_ip):
    """후보자 AI 정책 도구 라우트 등록 — policy_admin_routes.py register_*() 패턴."""

    class ToolsGenerateRequest(BaseModel):
        topic: str = Field(..., min_length=1, max_length=500)
        description: str = Field("", max_length=2000)
        output_format: Literal["정책", "정책포지션", "지역공약", "논평", "메시지"]

    # ── 생성 스트리밍 ──────────────────────────────────────────

    @app.post("/api/tools/generate/stream")
    def tools_generate_stream(body: ToolsGenerateRequest, request: Request):
        from backend.config import OPENAI_MODEL, CHAT_MODEL
        from backend.policy_drafter import generate_policy_draft
        from backend.quota_rate import check_quota, check_rate_limit_ip, check_rate_limit_user
        from backend.usage_logger import log_usage, _estimate_cost, parse_usage_marker

        user = require_approved(request)
        ip = _client_ip(request)

        ok, msg = check_rate_limit_ip(ip)
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_rate_limit_user(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_quota(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)

        topic = body.topic.strip()
        description = body.description.strip()
        combined_topic = f"{topic}\n\n{description}" if description else topic

        region_name = user.get("region_name") or ""
        district_name = user.get("district_name") or ""
        election_pos = user.get("election_position") or ""
        election_label = ELECTION_LABELS.get(election_pos, election_pos)

        # region_province / region_city 분리 (region_name = "서울특별시 강남구" 등)
        parts = region_name.split(None, 1)
        region_province = parts[0] if parts else ""
        region_city = parts[1] if len(parts) > 1 else ""

        t0 = time.perf_counter()

        def generate():
            full_text = ""
            had_error = False

            try:
                gen = generate_policy_draft(
                    stream=True,
                    topic=combined_topic,
                    output_format=body.output_format,
                    region=region_name,
                    election_type=election_label,
                    region_province=region_province,
                    region_city=region_city,
                    district_name=district_name,
                )
                actual_in = 0
                actual_out = 0
                for chunk in gen:
                    if chunk.startswith("[USAGE]"):
                        parts = chunk[7:].split(",")
                        actual_in = int(parts[0]) if len(parts) > 0 else 0
                        actual_out = int(parts[1]) if len(parts) > 1 else 0
                        continue
                    elif chunk.startswith("[FINAL]"):
                        full_text = chunk[len("[FINAL]"):]
                        yield f"data: {json.dumps({'type': 'final', 'text': full_text}, ensure_ascii=False)}\n\n"
                    elif chunk.startswith("[ERROR]"):
                        had_error = True
                        yield f"data: {json.dumps({'type': 'error', 'detail': chunk[7:]}, ensure_ascii=False)}\n\n"
                    else:
                        full_text += chunk
                        yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"

                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

            except Exception as e:
                had_error = True
                logger.exception("[tools/generate/stream] 오류")
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)[:300]}, ensure_ascii=False)}\n\n"

            # 사용량 로깅
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            status = 500 if had_error else 200
            out_chars = len(full_text)
            token_in = actual_in if actual_in else len(combined_topic) // 2
            token_out = actual_out if actual_out else out_chars // 2
            cost = _estimate_cost(token_in, token_out, OPENAI_MODEL) if not had_error else None
            log_usage(
                user_id=user["id"],
                ip=ip,
                endpoint="/api/tools/generate/stream",
                action="draft_generate",
                input_chars=len(combined_topic),
                output_chars=out_chars,
                model=OPENAI_MODEL,
                token_in=token_in if not had_error else 0,
                token_out=token_out if not had_error else 0,
                cost_estimate=cost,
                status_code=status,
                latency_ms=elapsed_ms,
            )

            # history 저장
            if not had_error and full_text:
                try:
                    from backend.history import add_history
                    add_history(
                        user_id=user["id"],
                        kind="draft",
                        input_text=combined_topic,
                        result=full_text,
                        status_code=200,
                        from_cache=False,
                        options={"output_format": body.output_format, "source": "tools/generate"},
                    )
                except Exception:
                    pass

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── 쿼터 현황 ─────────────────────────────────────────────

    @app.get("/api/tools/quota")
    def tools_quota(request: Request):
        from backend.config import QUOTA_DAILY_TOKENS, QUOTA_MONTHLY_TOKENS
        from backend.database import get_connection
        from backend.quota_rate import is_unlimited_quota_user

        user = require_approved(request)

        conn = get_connection()
        try:
            today = time.strftime("%Y-%m-%d")
            month = time.strftime("%Y-%m")
            cur = conn.execute(
                """
                SELECT
                    COALESCE((SELECT SUM(COALESCE(token_in,0)+COALESCE(token_out,0)) FROM usage_logs WHERE user_id = ? AND date(created_at) = ? AND status_code >= 200 AND status_code < 300 AND endpoint != '/api/pledge/verify'), 0) AS daily,
                    COALESCE((SELECT SUM(COALESCE(token_in,0)+COALESCE(token_out,0)) FROM usage_logs WHERE user_id = ? AND strftime('%Y-%m', created_at) = ? AND status_code >= 200 AND status_code < 300 AND endpoint != '/api/pledge/verify'), 0) AS monthly
                """,
                (user["id"], today, user["id"], month),
            )
            row = cur.fetchone()
            daily_used = row["daily"] if row else 0
            monthly_used = row["monthly"] if row else 0
        finally:
            conn.close()

        if is_unlimited_quota_user(user):
            return {
                "daily_used": daily_used,
                "daily_limit": None,
                "monthly_used": monthly_used,
                "monthly_limit": None,
                "daily_remaining": None,
                "monthly_remaining": None,
                "unlimited": True,
            }

        return {
            "daily_used": daily_used,
            "daily_limit": QUOTA_DAILY_TOKENS,
            "monthly_used": monthly_used,
            "monthly_limit": QUOTA_MONTHLY_TOKENS,
            "daily_remaining": max(0, QUOTA_DAILY_TOKENS - daily_used),
            "monthly_remaining": max(0, QUOTA_MONTHLY_TOKENS - monthly_used),
            "unlimited": False,
        }

    # ── 최근 생성 이력 ────────────────────────────────────────

    @app.get("/api/tools/history")
    def tools_history(request: Request, limit: int = 10):
        from backend.database import get_connection

        user = require_approved(request)
        limit = min(max(1, limit), 50)

        conn = get_connection()
        try:
            cur = conn.execute(
                """
                SELECT id, created_at,
                       substr(input_text, 1, 100) AS topic_preview,
                       substr(result_text, 1, 100) AS result_preview,
                       options_json
                FROM analysis_history
                WHERE user_id = ? AND kind = 'draft'
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user["id"], limit),
            )
            rows = []
            for raw in cur.fetchall():
                row = dict(raw)
                # options_json에서 output_format 추출
                if row.get("options_json"):
                    try:
                        opts = json.loads(row["options_json"])
                        row["output_format"] = opts.get("output_format", "")
                    except Exception:
                        row["output_format"] = ""
                else:
                    row["output_format"] = ""
                row.pop("options_json", None)
                rows.append(row)
            return {"items": rows}
        finally:
            conn.close()

    # ── 공약 개발 챗봇 ─────────────────────────────────────────

    class ChatStartRequest(BaseModel):
        topic: str = Field(..., min_length=1, max_length=8000)
        output_format: Literal["정책", "정책포지션", "지역공약", "논평", "메시지"] = "정책"

    class ChatMessageRequest(BaseModel):
        message: str = Field(..., min_length=1, max_length=8000)

    @app.post("/api/pledge-chat/start")
    def pledge_chat_start(body: ChatStartRequest, request: Request):
        from backend.pledge_chat import create_session, first_message_stream
        from backend.quota_rate import check_quota, check_rate_limit_ip, check_rate_limit_user
        from backend.usage_logger import log_usage, _estimate_cost, parse_usage_marker
        from backend.config import OPENAI_MODEL, CHAT_MODEL

        user = require_approved(request)
        ip = _client_ip(request)

        ok, msg = check_rate_limit_ip(ip)
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_rate_limit_user(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_quota(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)

        topic_full = body.topic.strip()
        # DB 세션 제목은 첫 100자만 (긴 어셈블리 데이터 포함 메시지 대응)
        topic_label = topic_full[:100]
        session = create_session(user["id"], topic_label, body.output_format)
        session_id = session["session_id"]

        t0 = time.perf_counter()

        def generate():
            full_text = ""
            had_error = False
            actual_in = 0
            actual_out = 0
            actual_model = CHAT_MODEL
            try:
                # 세션 ID를 먼저 전송
                yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"

                for chunk in first_message_stream(session_id, topic_full):
                    if chunk.startswith("[USAGE]"):
                        usage = parse_usage_marker(chunk)
                        if usage:
                            actual_in = usage["token_in"]
                            actual_out = usage["token_out"]
                            actual_model = usage["model"] or actual_model
                        continue
                    full_text += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            except Exception as e:
                had_error = True
                logger.exception("[pledge-chat/start] error")
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)[:300]}, ensure_ascii=False)}\n\n"

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            out_chars = len(full_text)
            token_in = actual_in if actual_in else len(body.topic) // 4
            token_out = actual_out if actual_out else out_chars // 4
            cost = _estimate_cost(token_in, token_out, actual_model) if not had_error else None
            log_usage(
                user_id=user["id"], ip=ip,
                endpoint="/api/pledge-chat/start", action="chat_start",
                input_chars=len(body.topic), output_chars=out_chars,
                model=actual_model, token_in=token_in if not had_error else 0,
                token_out=token_out if not had_error else 0,
                cost_estimate=cost,
                status_code=500 if had_error else 200, latency_ms=elapsed_ms,
            )

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/pledge-chat/{session_id}/message")
    def pledge_chat_message(session_id: str, body: ChatMessageRequest, request: Request):
        from backend.pledge_chat import chat_stream, get_session
        from backend.quota_rate import check_quota, check_rate_limit_ip, check_rate_limit_user
        from backend.usage_logger import log_usage, _estimate_cost, parse_usage_marker
        from backend.config import OPENAI_MODEL, CHAT_MODEL

        user = require_approved(request)
        ip = _client_ip(request)

        ok, msg = check_rate_limit_ip(ip)
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_rate_limit_user(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_quota(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)

        session = get_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        if session["status"] == "finalized":
            raise HTTPException(status_code=400, detail="이미 완료된 세션입니다.")

        t0 = time.perf_counter()

        def generate():
            full_text = ""
            had_error = False
            actual_in = 0
            actual_out = 0
            actual_model = CHAT_MODEL
            try:
                for chunk in chat_stream(session_id, body.message.strip()):
                    if chunk.startswith("[USAGE]"):
                        usage = parse_usage_marker(chunk)
                        if usage:
                            actual_in = usage["token_in"]
                            actual_out = usage["token_out"]
                            actual_model = usage["model"] or actual_model
                        continue
                    full_text += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            except Exception as e:
                had_error = True
                logger.exception("[pledge-chat/message] error")
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)[:300]}, ensure_ascii=False)}\n\n"

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            out_chars = len(full_text)
            token_in = actual_in if actual_in else len(body.message) // 4
            token_out = actual_out if actual_out else out_chars // 4
            cost = _estimate_cost(token_in, token_out, actual_model) if not had_error else None
            log_usage(
                user_id=user["id"], ip=ip,
                endpoint=f"/api/pledge-chat/{session_id}/message", action="chat_message",
                input_chars=len(body.message), output_chars=out_chars,
                model=actual_model, token_in=token_in if not had_error else 0,
                token_out=token_out if not had_error else 0,
                cost_estimate=cost,
                status_code=500 if had_error else 200, latency_ms=elapsed_ms,
            )

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/pledge-chat/{session_id}/finalize")
    def pledge_chat_finalize(session_id: str, request: Request):
        from backend.pledge_chat import finalize_stream, get_session
        from backend.quota_rate import check_quota, check_rate_limit_ip, check_rate_limit_user
        from backend.usage_logger import log_usage, _estimate_cost, parse_usage_marker
        from backend.config import OPENAI_MODEL, CHAT_MODEL

        user = require_approved(request)
        ip = _client_ip(request)

        ok, msg = check_rate_limit_ip(ip)
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_rate_limit_user(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)
        ok, msg = check_quota(user["id"])
        if not ok:
            raise HTTPException(status_code=429, detail=msg)

        session = get_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

        t0 = time.perf_counter()

        def generate():
            full_text = ""
            had_error = False
            actual_in = 0
            actual_out = 0
            actual_model = CHAT_MODEL
            try:
                for chunk in finalize_stream(session_id):
                    if chunk.startswith("[USAGE]"):
                        usage = parse_usage_marker(chunk)
                        if usage:
                            actual_in = usage["token_in"]
                            actual_out = usage["token_out"]
                            actual_model = usage["model"] or actual_model
                        continue
                    if chunk.startswith("[ERROR]"):
                        had_error = True
                        yield f"data: {json.dumps({'type': 'error', 'detail': chunk[7:]}, ensure_ascii=False)}\n\n"
                    else:
                        full_text += chunk
                        yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'final', 'text': full_text}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            except Exception as e:
                had_error = True
                logger.exception("[pledge-chat/finalize] error")
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)[:300]}, ensure_ascii=False)}\n\n"

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            out_chars = len(full_text)
            token_in = actual_in if actual_in else out_chars // 2
            token_out = actual_out if actual_out else out_chars // 2
            cost = _estimate_cost(token_in, token_out, actual_model) if not had_error else None
            log_usage(
                user_id=user["id"], ip=ip,
                endpoint=f"/api/pledge-chat/{session_id}/finalize", action="chat_finalize",
                input_chars=0, output_chars=out_chars,
                model=actual_model, token_in=token_in if not had_error else 0,
                token_out=token_out if not had_error else 0,
                cost_estimate=cost,
                status_code=500 if had_error else 200, latency_ms=elapsed_ms,
            )

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/pledge-chat/sessions")
    def pledge_chat_sessions(request: Request):
        from backend.pledge_chat import list_sessions
        user = require_approved(request)
        return {"sessions": list_sessions(user["id"])}

    @app.get("/api/pledge-chat/{session_id}")
    def pledge_chat_detail(session_id: str, request: Request):
        from backend.pledge_chat import get_session, get_messages
        user = require_approved(request)
        session = get_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        messages = get_messages(session_id)
        # 시스템 메시지는 프론트에 노출하지 않음
        visible_msgs = [m for m in messages if m["role"] != "system"]
        return {"session": session, "messages": visible_msgs}

    # ── 지역 의회 현황 ───────────────────────────────────────────────────────

    @app.get("/api/assembly/district-overview")
    def assembly_district_overview(request: Request, refresh: bool = False):
        """
        로그인한 사용자의 선거구 기준 지방의회 안건 + 국회의원/발의법안 반환.
        DB에 24시간 캐시. ?refresh=true 로 강제 갱신.
        """
        import json as _json
        from backend.database import get_connection
        from backend.assembly_api import search_local_assembly
        from backend.national_assembly_api import query_national_assembly_overview

        user = require_approved(request)
        user_id = user["id"]

        # ── 캐시 조회 ──
        if not refresh:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT data_json, cached_at FROM assembly_data_cache WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
            finally:
                conn.close()

            if row:
                from datetime import datetime, timezone
                cached_at = datetime.fromisoformat(row["cached_at"].replace("Z", "+00:00")) \
                    if "+" in row["cached_at"] or "Z" in row["cached_at"] \
                    else datetime.fromisoformat(row["cached_at"]).replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
                if age_hours < 48:
                    return _json.loads(row["data_json"])

        # ── 외부 API 호출 ──
        region = (user.get("region_name") or "").strip()
        district = (user.get("district_name") or "").strip()
        election_pos = (user.get("election_position") or "").strip()

        BASIC_POSITIONS = {"local_council", "local_mayor"}

        def _shorten_for_assembly(r: str) -> str:
            """광역시/특별시 접미사 제거해 의회 검색어로 변환. '광주광역시' → '광주'"""
            r = r.strip()
            for _sfx in ("광역시", "특별자치시", "특별자치도", "특별시", "광역도"):
                if r.endswith(_sfx):
                    return r[:-len(_sfx)]
            return r

        if election_pos in BASIC_POSITIONS and district:
            district_base = district.split()[0] if district else district
            assembly_search_region = district_base
        else:
            # metro_mayor, regional_council: 광역시명 접미사 제거해 검색 정확도 향상
            assembly_search_region = _shorten_for_assembly(region) if region else region
            district_base = ""

        local_result = search_local_assembly(region=assembly_search_region or None, keywords=[], years=1, limit=40)
        local_bills = local_result.get("results", [])

        if election_pos in BASIC_POSITIONS and district_base:
            filter_kw = district_base.rstrip("군시구")
            filtered = [
                b for b in local_bills
                if filter_kw in (b.get("title") or "")
                or filter_kw in (b.get("council") or "")
                or filter_kw in (b.get("speaker") or "")
            ]
            local_bills = filtered[:20] if filtered else local_bills[:20]
        else:
            local_bills = local_bills[:20]

        national = query_national_assembly_overview(region=region, district=district)

        result = {
            "region": region,
            "district": district,
            "local_assembly": {
                "available": local_result.get("available", False),
                "bills": local_bills,
            },
            "national_assembly": national,
        }

        # ── 캐시 저장 ──
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO assembly_data_cache (user_id, data_json, cached_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                     data_json = excluded.data_json,
                     cached_at = excluded.cached_at""",
                (user_id, _json.dumps(result, ensure_ascii=False))
            )
            conn.commit()
        finally:
            conn.close()

        return result

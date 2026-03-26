from typing import Optional

from fastapi import Query, Request
from pydantic import BaseModel, Field

from backend.database import get_connection


def register_policy_routes(app, require_admin, ensure_db_ready, serve_html):
    class FeaturedIssueUpsertBody(BaseModel):
        position_id: int = Field(..., ge=1)
        reason: Optional[str] = Field(default=None, max_length=500)
        start_at: Optional[str] = Field(default=None, max_length=10)
        end_at: Optional[str] = Field(default=None, max_length=10)
        manual_weight: int = Field(default=0, ge=-50, le=50)

    class PolicyPositionUpsertBody(BaseModel):
        title: str = Field(..., min_length=1, max_length=200)
        category: str = Field(default="general", min_length=1, max_length=80)
        summary: Optional[str] = Field(default=None, max_length=5000)
        official_summary: Optional[str] = Field(default=None, max_length=2000)
        key_points: Optional[str] = Field(default=None, max_length=4000)
        relevance_note: Optional[str] = Field(default=None, max_length=2000)
        body: Optional[str] = Field(default=None, max_length=50000)
        status: str = Field(default="draft", min_length=1, max_length=40)
        owner_scope: str = Field(default="party", min_length=1, max_length=40)
        effective_from: Optional[str] = Field(default=None, max_length=10)
        effective_to: Optional[str] = Field(default=None, max_length=10)
        version_label: Optional[str] = Field(default=None, max_length=40)

    class PolicyDocumentUpsertBody(BaseModel):
        title: str = Field(..., min_length=1, max_length=200)
        doc_type: str = Field(..., min_length=1, max_length=40)
        summary: Optional[str] = Field(default=None, max_length=5000)
        body: Optional[str] = Field(default=None, max_length=50000)
        speaker: Optional[str] = Field(default=None, max_length=120)
        speaker_name: Optional[str] = Field(default=None, max_length=120)
        owner_name: Optional[str] = Field(default=None, max_length=120)
        source_url: Optional[str] = Field(default=None, max_length=500)
        source_ref: Optional[str] = Field(default=None, max_length=200)
        published_at: Optional[str] = Field(default=None, max_length=10)
        status: str = Field(default="active", min_length=1, max_length=40)
        metadata: dict = Field(default_factory=dict)

    class PolicyLinkBody(BaseModel):
        position_id: int = Field(..., ge=1)
        document_id: int = Field(..., ge=1)
        relation_type: str = Field(default="references", min_length=1, max_length=40)
        notes: Optional[str] = Field(default=None, max_length=2000)

    @app.api_route("/admin/issue-radar", methods=["GET", "HEAD"])
    def admin_issue_radar_page(request: Request):
        """이슈 레이더 대시보드 (개발 단계: 인증 없이 접근 가능)."""
        # _ = require_admin(request)  # 개발 단계 — 인증 생략
        res = serve_html("admin/issue-radar.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="admin/issue-radar.html not found")

    @app.api_route("/admin/policy-ssot", methods=["GET", "HEAD"])
    def admin_policy_ssot_page(request: Request):
        # _ = require_admin(request)  # 개발 단계 — 인증 생략
        res = serve_html("admin/policy-ssot.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="admin/policy-ssot.html not found")

    @app.api_route("/policies", methods=["GET", "HEAD"])
    def public_policy_hub_page():
        res = serve_html("policies.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="policies.html not found")

    @app.api_route("/people", methods=["GET", "HEAD"])
    def public_people_hub_page():
        res = serve_html("people.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="people.html not found")

    @app.api_route("/commentary", methods=["GET", "HEAD"])
    def public_commentary_page():
        res = serve_html("commentary.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="commentary.html not found")

    @app.api_route("/hub", methods=["GET", "HEAD"])
    def public_ssot_hub_page():
        res = serve_html("hub.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="hub.html not found")

    @app.get("/api/admin/policy/summary", tags=["admin", "policy"])
    def api_admin_policy_summary(request: Request):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_ssot import get_policy_ssot_summary
        from backend.policy_featured import get_current_featured_issue, recommend_featured_issues
        from backend.policy_suggestions import list_link_suggestions
        from backend.rallypoint_commentary import list_ingest_runs
        from backend.assembly_bills import list_ingest_runs as list_bill_ingest_runs
        from backend.nesdc_polls import list_ingest_runs as list_poll_ingest_runs

        summary = get_policy_ssot_summary()
        runs = list_ingest_runs(limit=1)
        bill_runs = list_bill_ingest_runs(limit=1)
        poll_runs = list_poll_ingest_runs(limit=1)
        summary["last_commentary_sync"] = runs[0] if runs else None
        summary["last_bill_sync"] = bill_runs[0] if bill_runs else None
        summary["last_poll_sync"] = poll_runs[0] if poll_runs else None
        summary["pending_suggestions"] = len(list_link_suggestions(status="pending", limit=300))
        summary["current_featured_issue"] = get_current_featured_issue()
        summary["featured_candidates"] = recommend_featured_issues(limit=5)
        return summary

    @app.get("/api/admin/policy/operations", tags=["admin", "policy"])
    def api_admin_policy_operations(request: Request):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_ssot import get_policy_operations_overview
        return get_policy_operations_overview()

    @app.get("/api/admin/policy/featured-issues", tags=["admin", "policy"])
    def api_admin_policy_featured_issues(request: Request, limit: int = Query(default=20, ge=1, le=100)):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_featured import get_current_featured_issue, list_featured_issues
        return {"current": get_current_featured_issue(), "items": list_featured_issues(limit=limit)}

    @app.get("/api/admin/policy/featured-issues/recommendations", tags=["admin", "policy"])
    def api_admin_policy_featured_issue_recommendations(request: Request, limit: int = Query(default=5, ge=1, le=20)):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_featured import recommend_featured_issues
        return {"items": recommend_featured_issues(limit=limit)}

    @app.post("/api/admin/policy/featured-issues", tags=["admin", "policy"])
    def api_admin_policy_featured_issues_set(body: FeaturedIssueUpsertBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_featured import set_featured_issue
        return set_featured_issue(
            position_id=body.position_id,
            reason=body.reason,
            start_at=body.start_at,
            end_at=body.end_at,
            manual_weight=body.manual_weight,
            actor_id=user["id"],
        )

    @app.get("/api/admin/policy/positions", tags=["admin", "policy"])
    def api_admin_policy_positions(request: Request, status: Optional[str] = Query(default=None), category: Optional[str] = Query(default=None)):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_ssot import list_policy_positions
        return {"items": list_policy_positions(status=status, category=category)}

    @app.post("/api/admin/policy/positions", tags=["admin", "policy"])
    def api_admin_policy_positions_create(body: PolicyPositionUpsertBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import upsert_policy_position
        from backend.policy_suggestions import rebuild_link_suggestions
        item = upsert_policy_position(
            position_id=None,
            title=body.title,
            category=body.category,
            summary=body.summary,
            official_summary=body.official_summary,
            key_points=body.key_points,
            relevance_note=body.relevance_note,
            body=body.body,
            status=body.status,
            owner_scope=body.owner_scope,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            version_label=body.version_label,
            actor_id=user["id"],
        )
        rebuild_link_suggestions(document_id=None)
        return item

    @app.put("/api/admin/policy/positions/{position_id}", tags=["admin", "policy"])
    def api_admin_policy_positions_update(position_id: int, body: PolicyPositionUpsertBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import upsert_policy_position
        from backend.policy_suggestions import rebuild_link_suggestions
        item = upsert_policy_position(
            position_id=position_id,
            title=body.title,
            category=body.category,
            summary=body.summary,
            official_summary=body.official_summary,
            key_points=body.key_points,
            relevance_note=body.relevance_note,
            body=body.body,
            status=body.status,
            owner_scope=body.owner_scope,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            version_label=body.version_label,
            actor_id=user["id"],
        )
        rebuild_link_suggestions(document_id=None)
        return item

    @app.delete("/api/admin/policy/positions/{position_id}", tags=["admin", "policy"])
    def api_admin_policy_positions_delete(position_id: int, request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import delete_policy_position
        delete_policy_position(position_id)
        return {"ok": True}

    @app.get("/api/admin/policy/documents", tags=["admin", "policy"])
    def api_admin_policy_documents(request: Request, doc_type: Optional[str] = Query(default=None), status: Optional[str] = Query(default=None)):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_ssot import list_policy_documents
        return {"items": list_policy_documents(doc_type=doc_type, status=status)}

    @app.get("/api/admin/policy/import/rallypoint-commentary/runs", tags=["admin", "policy"])
    def api_admin_policy_commentary_runs(request: Request, limit: int = Query(default=10, ge=1, le=100)):
        require_admin(request)
        ensure_db_ready()
        from backend.rallypoint_commentary import list_ingest_runs
        return {"items": list_ingest_runs(limit=limit)}

    @app.post("/api/admin/policy/import/rallypoint-commentary", tags=["admin", "policy"])
    def api_admin_policy_import_commentary(request: Request, limit: int = Query(default=20, ge=1, le=100), include_body: bool = Query(default=True)):
        user = require_admin(request)
        ensure_db_ready()
        from backend.rallypoint_commentary import sync_commentary
        return sync_commentary(actor_id=user["id"], limit=limit, include_body=include_body)

    @app.get("/api/admin/policy/import/assembly-bills/runs", tags=["admin", "policy"])
    def api_admin_policy_assembly_bill_runs(request: Request, limit: int = Query(default=10, ge=1, le=100)):
        require_admin(request)
        ensure_db_ready()
        from backend.assembly_bills import list_ingest_runs
        return {"items": list_ingest_runs(limit=limit)}

    @app.post("/api/admin/policy/import/assembly-bills", tags=["admin", "policy"])
    def api_admin_policy_import_assembly_bills(request: Request, age_from: str = Query(default="22"), age_to: str = Query(default="22")):
        user = require_admin(request)
        ensure_db_ready()
        from backend.assembly_bills import sync_reform_party_bills
        return sync_reform_party_bills(actor_id=user["id"], age_from=age_from, age_to=age_to)

    @app.get("/api/admin/policy/import/nesdc-polls/runs", tags=["admin", "policy"])
    def api_admin_policy_nesdc_poll_runs(request: Request, limit: int = Query(default=10, ge=1, le=100)):
        require_admin(request)
        ensure_db_ready()
        from backend.nesdc_polls import list_ingest_runs
        return {"items": list_ingest_runs(limit=limit)}

    @app.post("/api/admin/policy/import/nesdc-polls", tags=["admin", "policy"])
    def api_admin_policy_import_nesdc_polls(
        request: Request,
        since: str = Query(default="2024-02-01"),
        max_pages: int = Query(default=30, ge=1, le=200),
    ):
        user = require_admin(request)
        ensure_db_ready()
        from backend.nesdc_polls import sync_reform_party_polls
        return sync_reform_party_polls(actor_id=user["id"], since=since, max_pages_per_term=max_pages)

    @app.get("/api/admin/policy/import/pdf-pledges/runs", tags=["admin", "policy"])
    def api_admin_policy_pdf_pledge_runs(request: Request, limit: int = Query(default=10, ge=1, le=100)):
        require_admin(request)
        ensure_db_ready()
        from backend.pdf_pledges_import import list_ingest_runs
        return {"items": list_ingest_runs(limit=limit)}

    @app.post("/api/admin/policy/import/pdf-pledges", tags=["admin", "policy"])
    def api_admin_policy_import_pdf_pledges(request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.pdf_pledges_import import sync_pdf_pledges
        return sync_pdf_pledges(actor_id=user["id"])

    @app.post("/api/admin/policy/commentary/auto-link", tags=["admin", "policy"])
    def api_admin_policy_commentary_auto_link(request: Request, limit: int = Query(default=300, ge=1, le=500), min_score: int = Query(default=4, ge=1, le=10)):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import auto_link_public_commentary
        return auto_link_public_commentary(actor_id=user["id"], limit=limit, min_score=min_score)

    @app.get("/api/admin/policy/suggestions", tags=["admin", "policy"])
    def api_admin_policy_suggestions(request: Request, status: Optional[str] = Query(default="pending"), limit: int = Query(default=100, ge=1, le=300)):
        # require_admin(request)  # 개발 단계 — 인증 생략
        ensure_db_ready()
        from backend.policy_suggestions import list_link_suggestions
        return {"items": list_link_suggestions(status=status, limit=limit)}

    @app.post("/api/admin/policy/suggestions/rebuild", tags=["admin", "policy"])
    def api_admin_policy_suggestions_rebuild(request: Request, document_id: Optional[int] = Query(default=None)):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_suggestions import rebuild_link_suggestions
        return rebuild_link_suggestions(document_id=document_id)

    @app.post("/api/admin/policy/suggestions/{suggestion_id}/accept", tags=["admin", "policy"])
    def api_admin_policy_suggestion_accept(suggestion_id: int, request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_suggestions import update_link_suggestion_status
        return update_link_suggestion_status(suggestion_id, "accepted")

    @app.post("/api/admin/policy/suggestions/{suggestion_id}/reject", tags=["admin", "policy"])
    def api_admin_policy_suggestion_reject(suggestion_id: int, request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_suggestions import update_link_suggestion_status
        return update_link_suggestion_status(suggestion_id, "rejected")

    @app.post("/api/admin/policy/documents", tags=["admin", "policy"])
    def api_admin_policy_documents_create(body: PolicyDocumentUpsertBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import upsert_policy_document
        from backend.policy_suggestions import rebuild_link_suggestions
        item = upsert_policy_document(
            document_id=None,
            title=body.title,
            doc_type=body.doc_type,
            summary=body.summary,
            body=body.body,
            speaker=body.speaker,
            speaker_name=body.speaker_name,
            owner_name=body.owner_name,
            source_url=body.source_url,
            source_ref=body.source_ref,
            published_at=body.published_at,
            status=body.status,
            metadata=body.metadata,
            actor_id=user["id"],
        )
        rebuild_link_suggestions(document_id=item["id"])
        return item

    @app.put("/api/admin/policy/documents/{document_id}", tags=["admin", "policy"])
    def api_admin_policy_documents_update(document_id: int, body: PolicyDocumentUpsertBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import upsert_policy_document
        from backend.policy_suggestions import rebuild_link_suggestions
        item = upsert_policy_document(
            document_id=document_id,
            title=body.title,
            doc_type=body.doc_type,
            summary=body.summary,
            body=body.body,
            speaker=body.speaker,
            speaker_name=body.speaker_name,
            owner_name=body.owner_name,
            source_url=body.source_url,
            source_ref=body.source_ref,
            published_at=body.published_at,
            status=body.status,
            metadata=body.metadata,
            actor_id=user["id"],
        )
        rebuild_link_suggestions(document_id=item["id"])
        return item

    @app.delete("/api/admin/policy/documents/{document_id}", tags=["admin", "policy"])
    def api_admin_policy_documents_delete(document_id: int, request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import delete_policy_document
        delete_policy_document(document_id)
        return {"ok": True}

    @app.get("/api/admin/policy/links", tags=["admin", "policy"])
    def api_admin_policy_links(request: Request, position_id: Optional[int] = Query(default=None), document_id: Optional[int] = Query(default=None)):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import list_policy_links
        return {"items": list_policy_links(position_id=position_id, document_id=document_id)}

    @app.post("/api/admin/policy/links", tags=["admin", "policy"])
    def api_admin_policy_links_create(body: PolicyLinkBody, request: Request):
        user = require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import link_policy_document
        return link_policy_document(
            position_id=body.position_id,
            document_id=body.document_id,
            relation_type=body.relation_type,
            notes=body.notes,
            actor_id=user["id"],
        )

    @app.delete("/api/admin/policy/links/{link_id}", tags=["admin", "policy"])
    def api_admin_policy_links_delete(link_id: int, request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import unlink_policy_document
        unlink_policy_document(link_id)
        return {"ok": True}

    @app.get("/api/policy/positions", tags=["policy"])
    def api_policy_positions(status: Optional[str] = Query(default="approved"), category: Optional[str] = Query(default=None)):
        ensure_db_ready()
        from backend.policy_ssot import list_policy_positions
        return {"items": list_policy_positions(status=status, category=category)}

    @app.get("/api/policy/positions/{slug_or_id}", tags=["policy"])
    def api_policy_position_detail(slug_or_id: str):
        ensure_db_ready()
        from backend.policy_ssot import get_policy_position_detail
        return get_policy_position_detail(slug_or_id)

    @app.get("/api/policy/positions/{slug_or_id}/timeline", tags=["policy"])
    def api_policy_position_timeline(slug_or_id: str):
        ensure_db_ready()
        from backend.policy_ssot import get_policy_position_by_slug, get_policy_position_timeline
        item = get_policy_position_by_slug(slug_or_id)
        return {"items": get_policy_position_timeline(item["id"])}

    @app.get("/api/policy/documents", tags=["policy"])
    def api_policy_documents(doc_type: Optional[str] = Query(default=None), status: Optional[str] = Query(default="active")):
        ensure_db_ready()
        from backend.policy_ssot import list_policy_documents
        return {"items": list_policy_documents(doc_type=doc_type, status=status)}

    @app.get("/api/policy/documents/{document_id}", tags=["policy"])
    def api_policy_document_detail(document_id: int):
        ensure_db_ready()
        from backend.policy_ssot import get_policy_document
        return get_policy_document(document_id)

    @app.get("/api/policy/featured-issues", tags=["policy"])
    def api_policy_featured_issues():
        ensure_db_ready()
        from backend.policy_featured import get_current_featured_issue, recommend_featured_issues
        return {"current": get_current_featured_issue(), "recommendations": recommend_featured_issues(limit=5)}

    @app.get("/api/policy/overview", tags=["policy"])
    def api_policy_overview():
        ensure_db_ready()
        from backend.policy_ssot import get_public_overview
        return get_public_overview()

    @app.get("/api/policy/people", tags=["policy"])
    def api_policy_people():
        ensure_db_ready()
        from backend.policy_ssot import list_public_people
        return {"items": list_public_people()}

    @app.get("/api/policy/people/{person_name}", tags=["policy"])
    def api_policy_person_detail(person_name: str):
        ensure_db_ready()
        from backend.policy_ssot import get_public_person_detail
        return get_public_person_detail(person_name)

    @app.get("/api/policy/commentary", tags=["policy"])
    def api_policy_commentary(q: Optional[str] = Query(default=None), speaker_name: Optional[str] = Query(default=None), limit: int = Query(default=60, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import list_public_commentary
        return {"items": list_public_commentary(q=q, speaker_name=speaker_name, limit=limit)}

    @app.get("/api/policy/messages", tags=["policy"])
    def api_policy_messages(q: Optional[str] = Query(default=None), speaker_name: Optional[str] = Query(default=None), limit: int = Query(default=60, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import list_public_messages
        return {"items": list_public_messages(q=q, speaker_name=speaker_name, limit=limit)}

    @app.get("/api/policy/commentary/overview", tags=["policy"])
    def api_policy_commentary_overview(limit: int = Query(default=120, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import get_public_commentary_overview
        return get_public_commentary_overview(limit=limit)

    @app.get("/api/policy/messages/overview", tags=["policy"])
    def api_policy_messages_overview(limit: int = Query(default=120, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import get_public_messages_overview
        return get_public_messages_overview(limit=limit)

    @app.get("/api/policy/meetings", tags=["policy"])
    def api_policy_meetings(q: Optional[str] = Query(default=None), limit: int = Query(default=60, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import list_public_meetings
        return {"items": list_public_meetings(q=q, limit=limit)}

    @app.get("/api/policy/rules", tags=["policy"])
    def api_policy_rules(q: Optional[str] = Query(default=None), limit: int = Query(default=60, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import list_public_rules
        return {"items": list_public_rules(q=q, limit=limit)}

    @app.get("/api/policy/polls", tags=["policy"])
    def api_policy_polls(q: Optional[str] = Query(default=None), limit: int = Query(default=60, ge=1, le=200)):
        ensure_db_ready()
        from backend.policy_ssot import list_policy_documents
        items = list_policy_documents(doc_type="poll_result", status="active")
        if q:
            needle = q.strip().lower()
            items = [
                item
                for item in items
                if needle in (item.get("title") or "").lower()
                or needle in (item.get("summary") or "").lower()
                or needle in (item.get("body") or "").lower()
            ]
        return {"items": items[:limit]}

    @app.get("/api/policy/hub", tags=["policy"])
    def api_policy_hub():
        ensure_db_ready()
        from backend.policy_ssot import (
            get_public_meetings_overview,
            get_public_messages_overview,
            get_public_overview,
            get_public_rules_overview,
        )
        from backend.policy_featured import get_current_featured_issue, recommend_featured_issues
        return {
            "overview": get_public_overview(),
            "messages": get_public_messages_overview(limit=60),
            "meetings": get_public_meetings_overview(limit=60),
            "rules": get_public_rules_overview(limit=60),
            "featured": {
                "current": get_current_featured_issue(),
                "recommendations": recommend_featured_issues(limit=5),
            },
        }

    # ── 이슈 레이더 API ──

    @app.get("/api/policy/issue-radar", tags=["policy", "issue-radar"])
    def get_issue_radar(
        request: Request,
        refresh: bool = Query(default=False, description="캐시 무시하고 새로 스캔"),
        days: Optional[int] = Query(default=None, description="최근 N일 문서만"),
        doc_type: Optional[str] = Query(default=None, description="문서 유형 필터"),
    ):
        """이슈 레이더 — 정책 사각지대 리포트."""
        ensure_db_ready()
        # _ = require_admin(request)  # 개발 단계 — 인증 생략

        from backend.issue_radar import get_cached_scan, run_issue_scan, save_scan_result

        if not refresh:
            cached = get_cached_scan(max_age_hours=6)
            if cached:
                cached["from_cache"] = True
                return cached

        report = run_issue_scan(days_back=days, doc_type_filter=doc_type)
        save_scan_result(report)
        report["from_cache"] = False
        return report

    @app.post("/api/policy/issue-radar/scan", tags=["policy", "issue-radar"])
    def trigger_issue_radar_scan(
        request: Request,
        days: Optional[int] = Query(default=None, description="최근 N일 문서만"),
    ):
        """이슈 레이더 수동 스캔 트리거."""
        ensure_db_ready()
        # _ = require_admin(request)  # 개발 단계 — 인증 생략

        from backend.issue_radar import run_issue_scan, save_scan_result

        report = run_issue_scan(days_back=days)
        save_scan_result(report)
        return {"status": "ok", "gaps_found": len(report["gaps"]), "scan_time": report["scan_time"]}

    # ── 리서치 어시스턴트 API ──

    @app.get("/api/policy/research", tags=["policy", "research"])
    def get_research_briefing(
        request: Request,
        topic: str = Query(..., min_length=1, max_length=200, description="정책 주제"),
        region: Optional[str] = Query(default=None, max_length=100, description="지역명"),
        years: int = Query(default=2, ge=1, le=5, description="검색 기간 (년)"),
    ):
        """리서치 어시스턴트 — 주제·지역별 관련 자료 수집."""
        ensure_db_ready()
        # _ = require_admin(request)  # 개발 단계 — 인증 생략

        from backend.research_assistant import research_topic

        return research_topic(topic=topic, region=region, years=years)

    # ── 정책 드래프터 API ──

    class DraftRequest(BaseModel):
        topic: str = Field(..., min_length=1, max_length=200)
        output_format: str = Field(default="정책포지션", max_length=20)
        region: Optional[str] = Field(default=None, max_length=100)
        election_type: str = Field(default="", max_length=40)
        region_province: str = Field(default="", max_length=40)
        region_city: str = Field(default="", max_length=40)
        district_name: str = Field(default="", max_length=40)

    @app.post("/api/policy/draft", tags=["policy", "drafter"])
    def create_policy_draft(request: Request, body: DraftRequest):
        """정책 초안 생성."""
        ensure_db_ready()
        # _ = require_admin(request)  # 개발 단계 — 인증 생략

        from backend.policy_drafter import generate_policy_draft

        return generate_policy_draft(
            topic=body.topic,
            output_format=body.output_format,
            region=body.region,
            election_type=body.election_type,
            region_province=body.region_province,
            region_city=body.region_city,
            district_name=body.district_name,
        )

    @app.post("/api/policy/draft/stream", tags=["policy", "drafter"])
    def stream_policy_draft(request: Request, body: DraftRequest):
        """정책 초안 스트리밍 생성."""
        ensure_db_ready()
        # _ = require_admin(request)  # 개발 단계 — 인증 생략

        from fastapi.responses import StreamingResponse
        from backend.policy_drafter import generate_policy_draft

        gen = generate_policy_draft(
            topic=body.topic,
            output_format=body.output_format,
            region=body.region,
            election_type=body.election_type,
            region_province=body.region_province,
            region_city=body.region_city,
            district_name=body.district_name,
            stream=True,
        )
        if isinstance(gen, dict):
            return gen  # error case
        return StreamingResponse(gen, media_type="text/plain; charset=utf-8")

    class SaveDraftBody(BaseModel):
        title: str = Field(..., min_length=1, max_length=200)
        summary: str = Field(default="", max_length=5000)
        key_points: str = Field(default="", max_length=4000)
        body: str = Field(..., min_length=1, max_length=50000)
        category: str = Field(default="general", max_length=80)

    @app.post("/api/policy/draft/save", tags=["policy", "drafter"])
    def save_policy_draft(request: Request, body: SaveDraftBody):
        """초안을 policy_positions(draft)로 저장."""
        ensure_db_ready()
        # 개발 단계 — 인증 생략, user fallback
        try:
            user = require_admin(request)
        except Exception:
            user = {"id": None}

        from backend.policy_drafter import save_draft_as_position

        pos_id = save_draft_as_position(
            title=body.title,
            summary=body.summary,
            key_points=body.key_points,
            body=body.body,
            category=body.category,
            created_by=user.get("id"),
        )
        return {"status": "ok", "position_id": pos_id}

    # ── 정책 연구소 진입점 + 정책 생성 페이지 + 시민 제안 페이지 라우트 ──

    @app.api_route("/policy-lab", methods=["GET", "HEAD"])
    def policy_lab_page():
        """정책 연구소 메인 (독립 진입점)."""
        res = serve_html("policy-lab.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="policy-lab.html not found")

    @app.api_route("/policy-create", methods=["GET", "HEAD"])
    def policy_create_page(request: Request):
        """정책 생성 페이지 (개발 단계: 인증 없이 접근 가능)."""
        # _ = require_admin(request)  # 개발 단계 — 인증 생략
        res = serve_html("policy-create.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="policy-create.html not found")

    @app.api_route("/proposals", methods=["GET", "HEAD"])
    def proposals_page():
        """시민 정책 제안 페이지 (공개)."""
        res = serve_html("proposals.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="proposals.html not found")

    # ── AI 정책 챗봇 API (Phase 3.5) ──

    class ChatRequest(BaseModel):
        question: str = Field(..., min_length=1, max_length=2000)

    @app.post("/api/policy/chat", tags=["policy", "chatbot"])
    def policy_chatbot(request: Request, body: ChatRequest):
        """AI 정책 챗봇 — 당 정책에 대한 시민 질문 답변."""
        ensure_db_ready()

        from backend.policy_chatbot import answer_policy_question

        return answer_policy_question(question=body.question)

    # ── 정책 워크플로 API (Phase 4) ──

    class ReviewCommentBody(BaseModel):
        comment: str = Field(..., min_length=1, max_length=5000)
        comment_type: str = Field(default="review", max_length=20)

    @app.post("/api/policy/positions/{position_id}/submit-review", tags=["policy", "workflow"])
    def submit_for_review(request: Request, position_id: int):
        """초안을 검토 상태로 전환."""
        ensure_db_ready()
        _ = require_admin(request)

        from backend.policy_ssot import update_policy_position_status
        update_policy_position_status(position_id, "review")
        return {"status": "ok", "new_status": "review"}

    @app.post("/api/policy/positions/{position_id}/approve", tags=["policy", "workflow"])
    def approve_position(request: Request, position_id: int):
        """포지션 승인."""
        ensure_db_ready()
        _ = require_admin(request)

        from backend.policy_ssot import update_policy_position_status
        update_policy_position_status(position_id, "approved")
        return {"status": "ok", "new_status": "approved"}

    @app.post("/api/policy/positions/{position_id}/comments", tags=["policy", "workflow"])
    def add_review_comment(request: Request, position_id: int, body: ReviewCommentBody):
        """검토 코멘트 추가."""
        ensure_db_ready()
        user = require_admin(request)

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO policy_review_comments (position_id, author_id, comment, comment_type)
                   VALUES (?, ?, ?, ?)""",
                (position_id, user.get("id"), body.comment, body.comment_type),
            )
            conn.commit()
            return {"status": "ok"}
        finally:
            conn.close()

    @app.get("/api/policy/positions/{position_id}/comments", tags=["policy", "workflow"])
    def get_review_comments(request: Request, position_id: int):
        """검토 코멘트 목록."""
        ensure_db_ready()
        _ = require_admin(request)

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT rc.*, u.name as author_name
                   FROM policy_review_comments rc
                   LEFT JOIN users u ON rc.author_id = u.id
                   WHERE rc.position_id = ?
                   ORDER BY rc.created_at ASC""",
                (position_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/policy/positions/{position_id}/timeline", tags=["policy", "workflow"])
    def get_position_timeline(request: Request, position_id: int):
        """포지션 타임라인 (버전 이력 + 코멘트)."""
        ensure_db_ready()

        conn = get_connection()
        try:
            # 버전 이력
            versions = conn.execute(
                """SELECT id, snapshot_type, changed_fields, created_at
                   FROM policy_position_versions
                   WHERE position_id = ?
                   ORDER BY created_at ASC""",
                (position_id,),
            ).fetchall()

            # 코멘트
            comments = conn.execute(
                """SELECT rc.id, rc.comment, rc.comment_type, rc.created_at,
                          u.name as author_name
                   FROM policy_review_comments rc
                   LEFT JOIN users u ON rc.author_id = u.id
                   WHERE rc.position_id = ?
                   ORDER BY rc.created_at ASC""",
                (position_id,),
            ).fetchall()

            # 근거 문서 링크
            links = conn.execute(
                """SELECT dl.id, dl.relation_type, dl.notes, dl.created_at,
                          pd.title as document_title, pd.doc_type
                   FROM policy_document_links dl
                   JOIN policy_documents pd ON dl.document_id = pd.id
                   WHERE dl.position_id = ?
                   ORDER BY dl.created_at ASC""",
                (position_id,),
            ).fetchall()

            # Merge into timeline
            timeline = []
            for v in versions:
                timeline.append({
                    "type": "version",
                    "timestamp": v["created_at"],
                    "snapshot_type": v["snapshot_type"],
                    "changed_fields": v["changed_fields"],
                })
            for c in comments:
                timeline.append({
                    "type": "comment",
                    "timestamp": c["created_at"],
                    "comment": c["comment"],
                    "comment_type": c["comment_type"],
                    "author": c["author_name"],
                })
            for lk in links:
                timeline.append({
                    "type": "link",
                    "timestamp": lk["created_at"],
                    "relation_type": lk["relation_type"],
                    "document_title": lk["document_title"],
                    "doc_type": lk["doc_type"],
                })

            timeline.sort(key=lambda x: x.get("timestamp") or "")
            return {"position_id": position_id, "timeline": timeline}
        finally:
            conn.close()

    # ── 시민 제안 API (Phase 6) ──

    class CitizenProposalBody(BaseModel):
        title: str = Field(..., min_length=1, max_length=200)
        body: str = Field(..., min_length=10, max_length=5000)
        author_name: str = Field(default="", max_length=50)
        author_email: str = Field(default="", max_length=100)

    @app.post("/api/policy/proposals", tags=["policy", "citizen"])
    def submit_citizen_proposal(request: Request, body: CitizenProposalBody):
        """시민 정책 제안 제출."""
        ensure_db_ready()

        from backend.policy_ssot import _classify_commentary_topic

        topic = _classify_commentary_topic({"title": body.title, "summary": body.body, "body": ""})

        conn = get_connection()
        try:
            cur = conn.execute(
                """INSERT INTO citizen_proposals (title, body, author_name, author_email, topic)
                   VALUES (?, ?, ?, ?, ?)""",
                (body.title, body.body, body.author_name or None, body.author_email or None, topic),
            )
            conn.commit()
            return {"status": "ok", "proposal_id": cur.lastrowid, "classified_topic": topic}
        finally:
            conn.close()

    @app.get("/api/policy/proposals", tags=["policy", "citizen"])
    def list_citizen_proposals(
        request: Request,
        status: Optional[str] = Query(default=None, max_length=20),
    ):
        """시민 제안 목록 (관리자)."""
        ensure_db_ready()
        _ = require_admin(request)

        conn = get_connection()
        try:
            sql = "SELECT * FROM citizen_proposals"
            params = []
            if status:
                sql += " WHERE status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

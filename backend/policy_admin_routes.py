from typing import Optional

from fastapi import Query, Request
from pydantic import BaseModel, Field


def register_policy_routes(app, require_admin, ensure_db_ready, serve_html):
    class PolicyPositionUpsertBody(BaseModel):
        title: str = Field(..., min_length=1, max_length=200)
        category: str = Field(default="general", min_length=1, max_length=80)
        summary: Optional[str] = Field(default=None, max_length=5000)
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

    @app.api_route("/admin/policy-ssot", methods=["GET", "HEAD"])
    def admin_policy_ssot_page(request: Request):
        _ = require_admin(request)
        res = serve_html("admin/policy-ssot.html")
        if res is not None:
            return res
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="admin/policy-ssot.html not found")

    @app.get("/api/admin/policy/summary", tags=["admin", "policy"])
    def api_admin_policy_summary(request: Request):
        require_admin(request)
        ensure_db_ready()
        from backend.policy_ssot import get_policy_ssot_summary
        from backend.policy_suggestions import list_link_suggestions
        from backend.rallypoint_commentary import list_ingest_runs
        from backend.assembly_bills import list_ingest_runs as list_bill_ingest_runs

        summary = get_policy_ssot_summary()
        runs = list_ingest_runs(limit=1)
        bill_runs = list_bill_ingest_runs(limit=1)
        summary["last_commentary_sync"] = runs[0] if runs else None
        summary["last_bill_sync"] = bill_runs[0] if bill_runs else None
        summary["pending_suggestions"] = len(list_link_suggestions(status="pending", limit=300))
        return summary

    @app.get("/api/admin/policy/positions", tags=["admin", "policy"])
    def api_admin_policy_positions(request: Request, status: Optional[str] = Query(default=None), category: Optional[str] = Query(default=None)):
        require_admin(request)
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
        require_admin(request)
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

    @app.get("/api/admin/policy/suggestions", tags=["admin", "policy"])
    def api_admin_policy_suggestions(request: Request, status: Optional[str] = Query(default="pending"), limit: int = Query(default=100, ge=1, le=300)):
        require_admin(request)
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

    @app.get("/api/policy/documents", tags=["policy"])
    def api_policy_documents(doc_type: Optional[str] = Query(default=None), status: Optional[str] = Query(default="active")):
        ensure_db_ready()
        from backend.policy_ssot import list_policy_documents
        return {"items": list_policy_documents(doc_type=doc_type, status=status)}

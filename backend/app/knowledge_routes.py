from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from .audit import audit
from .dependencies import Context, Database
from .models import KnowledgeDocument
from .schemas import KnowledgeDocumentCreateRequest, KnowledgeDocumentDecisionRequest, KnowledgeDocumentOut
from .security import require_role

router = APIRouter(prefix="/api/v1/knowledge/documents", tags=["knowledge-governance"])


@router.get("", response_model=list[KnowledgeDocumentOut])
def list_documents(
    context: Context,
    db: Database,
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[KnowledgeDocumentOut]:
    statement = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == context.tenant_id)
    if status_filter:
        statement = statement.where(KnowledgeDocument.status == status_filter)
    rows = db.scalars(statement.order_by(KnowledgeDocument.created_at.desc()).limit(limit))
    return [KnowledgeDocumentOut.model_validate(row) for row in rows]


@router.post("", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(payload: KnowledgeDocumentCreateRequest, context: Context, db: Database) -> KnowledgeDocumentOut:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认知识版本将进入独立复核")
    latest = db.scalar(
        select(func.max(KnowledgeDocument.version)).where(
            KnowledgeDocument.tenant_id == context.tenant_id,
            KnowledgeDocument.document_key == payload.document_key,
        )
    )
    document = KnowledgeDocument(
        tenant_id=context.tenant_id,
        document_key=payload.document_key,
        title=payload.title,
        category=payload.category,
        source_reference=payload.source_reference,
        content_digest=payload.content_digest,
        summary=payload.summary,
        version=int(latest or 0) + 1,
        proposed_by=context.actor_id,
    )
    db.add(document)
    db.flush()
    audit(
        db,
        context,
        "knowledge.proposed",
        "knowledge_document",
        document.id,
        {
            "document_key": document.document_key,
            "version": document.version,
            "content_digest": document.content_digest,
        },
    )
    db.commit()
    db.refresh(document)
    return KnowledgeDocumentOut.model_validate(document)


@router.post("/{document_id}/decision", response_model=KnowledgeDocumentOut)
def decide_document(
    document_id: str, payload: KnowledgeDocumentDecisionRequest, context: Context, db: Database
) -> KnowledgeDocumentOut:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认已独立核验来源和内容摘要")
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == context.tenant_id, KnowledgeDocument.id == document_id
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="知识版本不存在")
    if document.status != "pending_review" or document.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="知识版本状态或版本已变化")
    if document.proposed_by == context.actor_id:
        raise HTTPException(status_code=409, detail="提案人不能复核自己的知识版本")
    now = datetime.now(UTC).replace(tzinfo=None)
    if payload.decision == "approve":
        previous = db.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == context.tenant_id,
                KnowledgeDocument.document_key == document.document_key,
                KnowledgeDocument.status == "published",
            )
        )
        for item in previous:
            item.status = "retired"
        document.status = "published"
    else:
        document.status = "rejected"
    document.reviewed_by = context.actor_id
    document.review_note = payload.review_note
    document.reviewed_at = now
    audit(
        db,
        context,
        f"knowledge.{document.status}",
        "knowledge_document",
        document.id,
        {
            "document_key": document.document_key,
            "version": document.version,
            "content_digest": document.content_digest,
        },
    )
    db.commit()
    db.refresh(document)
    return KnowledgeDocumentOut.model_validate(document)

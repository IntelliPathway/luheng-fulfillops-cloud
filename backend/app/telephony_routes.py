from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError

from .dependencies import Context, Database
from .schemas import TelephonyEventAcceptanceOut, TelephonyEventOut, TelephonyWebhookPayload
from .telephony import TelephonyError, accept_telephony_webhook, list_telephony_events

router = APIRouter(tags=["telephony"])


@router.post("/api/v1/webhooks/telephony/{tenant_id}/{provider}", response_model=TelephonyEventAcceptanceOut)
async def receive_telephony_event(
    tenant_id: str,
    provider: str,
    request: Request,
    db: Database,
    webhook_timestamp: Annotated[str, Header(alias="X-RepayGuard-Timestamp")],
    webhook_signature: Annotated[str, Header(alias="X-RepayGuard-Signature")],
) -> TelephonyEventAcceptanceOut:
    raw_body = await request.body()
    if len(raw_body) > 16_384:
        raise HTTPException(status_code=413, detail="电话事件载荷超过大小上限")
    try:
        payload = TelephonyWebhookPayload.model_validate_json(raw_body)
        accepted = accept_telephony_webhook(
            db, tenant_id, provider, payload, raw_body, webhook_timestamp, webhook_signature
        )
        return TelephonyEventAcceptanceOut(
            event=TelephonyEventOut.model_validate(accepted.event), duplicate=accepted.duplicate
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="电话事件载荷格式无效") from exc
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


@router.get("/api/v1/telephony/events", response_model=list[TelephonyEventOut])
def get_telephony_events(
    context: Context, db: Database, limit: int = Query(default=100, ge=1, le=500)
) -> list[TelephonyEventOut]:
    return [TelephonyEventOut.model_validate(row) for row in list_telephony_events(db, context.tenant_id, limit)]

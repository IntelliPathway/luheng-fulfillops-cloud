from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .case_validation import build_case_validation
from .connection_readiness import build_connection_readiness
from .dependencies import Context, Database
from .pilot_acceptance import EvidenceCreate, EvidenceDecision, decide_evidence, list_evidence, propose_evidence
from .pilot_preflight import build_pilot_preflight
from .pilot_scorecard import build_pilot_scorecard, build_release_gate
from .recovery_trends import build_recovery_day, build_recovery_trend

router = APIRouter(prefix="/api/v1/pilot", tags=["pilot"])
MAX_LEDGER_DAY = date(9999, 12, 30)


@router.get("/connection-readiness")
def get_connection_readiness(context: Context, db: Database, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return build_connection_readiness(
        db,
        context,
        request.app.state.startup,
        request.headers.get("origin"),
        request.app.state.cors_origins,
        browser_origin=request.query_params.get("browser_origin"),
    )


@router.get("/preflight")
def get_preflight(context: Context, db: Database, request: Request) -> dict:
    return build_pilot_preflight(db, context.tenant_id, request.app.state.startup)


@router.get("/scorecard")
def get_pilot_scorecard(context: Context, db: Database) -> dict:
    return build_pilot_scorecard(db, context.tenant_id)


@router.get("/release-gate")
def get_release_gate(context: Context, db: Database, request: Request) -> dict:
    return build_release_gate(db, context.tenant_id, request.app.state.startup)


@router.get("/evidence")
def get_evidence(context: Context, db: Database, request: Request) -> list[dict]:
    return list_evidence(db, context.tenant_id, request.app.state.startup)


@router.post("/evidence", status_code=201)
def create_evidence(payload: EvidenceCreate, context: Context, db: Database, request: Request) -> dict:
    return propose_evidence(db, context, payload, request.app.state.startup)


@router.post("/evidence/{evidence_id}/decision")
def review_evidence(
    evidence_id: str, payload: EvidenceDecision, context: Context, db: Database, request: Request
) -> dict:
    return decide_evidence(db, context, evidence_id, payload, request.app.state.startup)


@router.get("/case-validation")
def get_case_validation(
    context: Context,
    db: Database,
    request: Request,
    case_id: str = Query(min_length=1, max_length=40),
    expected_net_recovery_cents: int | None = Query(default=None, ge=0, le=9007199254740991),
) -> dict:
    return build_case_validation(db, context.tenant_id, case_id, request.app.state.startup, expected_net_recovery_cents)


@router.get("/recovery-trend")
def get_recovery_trend(
    context: Context, db: Database, response: Response, days: int = Query(default=30, ge=1, le=90)
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return build_recovery_trend(db, context.tenant_id, days)


@router.get("/recovery-day")
def get_recovery_day(
    context: Context,
    db: Database,
    response: Response,
    day: Annotated[date, Query(le=MAX_LEDGER_DAY)],
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    expected_payment_cents: str | None = Query(default=None, pattern=r"^(0|[1-9][0-9]*)$", max_length=16),
    expected_refund_cents: str | None = Query(default=None, pattern=r"^(0|[1-9][0-9]*)$", max_length=16),
    evidence_status: Literal["all", "linked", "incomplete"] = "all",
) -> dict:
    if (expected_payment_cents is None) != (expected_refund_cents is None):
        raise HTTPException(422, "付款与退款凭证金额必须同时填写，单位为非负整数分")
    payment_cents = int(expected_payment_cents) if expected_payment_cents is not None else None
    refund_cents = int(expected_refund_cents) if expected_refund_cents is not None else None
    if any(value is not None and value > 9007199254740991 for value in (payment_cents, refund_cents)):
        raise HTTPException(422, "金额超出安全整数分范围")
    response.headers["Cache-Control"] = "no-store"
    return build_recovery_day(db, context.tenant_id, day, page, page_size, payment_cents, refund_cents, evidence_status)

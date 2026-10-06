from fastapi import APIRouter, Query, Request, Response

from .case_validation import build_case_validation
from .connection_readiness import build_connection_readiness
from .dependencies import Context, Database
from .pilot_acceptance import EvidenceCreate, EvidenceDecision, decide_evidence, list_evidence, propose_evidence
from .pilot_preflight import build_pilot_preflight
from .pilot_scorecard import build_pilot_scorecard, build_release_gate

router = APIRouter(prefix="/api/v1/pilot", tags=["pilot"])


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

from fastapi import APIRouter

from .dependencies import Context, Database
from .pilot_scorecard import build_pilot_scorecard, build_release_gate

router = APIRouter(prefix="/api/v1/pilot", tags=["pilot"])


@router.get("/scorecard")
def get_pilot_scorecard(context: Context, db: Database) -> dict:
    return build_pilot_scorecard(db, context.tenant_id)


@router.get("/release-gate")
def get_release_gate(context: Context, db: Database) -> dict:
    return build_release_gate(db, context.tenant_id)

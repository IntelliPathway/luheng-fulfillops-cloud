from fastapi import APIRouter

from .dependencies import Context, Database
from .pilot_scorecard import build_pilot_scorecard

router = APIRouter(prefix="/api/v1/pilot", tags=["pilot"])


@router.get("/scorecard")
def get_pilot_scorecard(context: Context, db: Database) -> dict:
    return build_pilot_scorecard(db, context.tenant_id)

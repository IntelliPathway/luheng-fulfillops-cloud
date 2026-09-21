from fastapi import APIRouter

from .dependencies import Context, Database
from .provider_operations import daily_close, provider_scorecard
from .security import require_role

router = APIRouter(prefix="/api/v1/provider-operations", tags=["provider-operations"])


@router.get("/scorecard")
def get_provider_scorecard(context: Context, db: Database) -> dict:
    require_role(context, "operator", "admin")
    return provider_scorecard(db, context.tenant_id)


@router.get("/daily-close")
def get_daily_close(context: Context, db: Database) -> dict:
    require_role(context, "operator", "admin")
    return daily_close(db, context.tenant_id)

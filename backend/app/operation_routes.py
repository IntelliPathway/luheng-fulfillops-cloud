from fastapi import APIRouter, Query

from .dependencies import Context, Database
from .security import require_role
from .work_queue import build_work_queue

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/work-queue")
def get_work_queue(context: Context, db: Database, limit: int = Query(default=100, ge=1, le=200)) -> dict:
    require_role(context, "viewer", "operator", "admin")
    return build_work_queue(db, context.tenant_id, limit)

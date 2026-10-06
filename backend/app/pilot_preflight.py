from __future__ import annotations

from sqlalchemy import func, inspect, select, text

from .job_queue import queue_health
from .migrations import _migration_files
from .models import TenantLifecycle, TenantMembership
from .pilot_scorecard import build_release_gate


def build_pilot_preflight(db, tenant_id: str, startup) -> dict:
    gate = build_release_gate(db, tenant_id, startup)
    queue = queue_health(db, tenant_id)
    expected = {path.name for path in _migration_files()}
    applied = set()
    if db.get_bind().dialect.name == "postgresql" and inspect(db.get_bind()).has_table("schema_migrations"):
        applied = set(db.scalars(text("SELECT version FROM schema_migrations")))
    missing = sorted(expected - applied)
    admins = (
        db.scalar(
            select(func.count(TenantMembership.id)).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == "admin",
                TenantMembership.status == "active",
            )
        )
        or 0
    )
    lifecycle = db.get(TenantLifecycle, tenant_id)
    checks = [
        {"id": "migrations", "passed": not missing, "detail": {"missing": missing}},
        {
            "id": "worker-heartbeat",
            "passed": queue["mode"] == "external" and queue["status"] == "healthy" and queue["stale_jobs"] == 0,
            "detail": {"active_workers": queue["active_workers"], "stale_jobs": queue["stale_jobs"]},
        },
        {"id": "independent-admins", "passed": admins >= 2, "detail": {"active_admins": admins}},
        {
            "id": "tenant-active",
            "passed": lifecycle is not None and lifecycle.stage == "active",
            "detail": {"stage": lifecycle.stage if lifecycle else "unprovisioned"},
        },
        {"id": "acceptance", "passed": gate["status"] == "accepted", "detail": {"status": gate["status"]}},
    ]
    return {
        "tenant_id": tenant_id,
        "generated_at": gate["generated_at"],
        "status": "ready" if all(c["passed"] for c in checks) else "blocked",
        "checks": checks,
        "configuration_digest": gate["configuration_digest"],
        "enables_external_execution": False,
    }

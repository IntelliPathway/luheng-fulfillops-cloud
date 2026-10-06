"""Read-only release evidence; a software release never authorizes business execution."""

import hashlib
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from .case_acceptance import acceptance_view
from .connection_readiness import build_connection_readiness
from .models import CaseAcceptance, utcnow
from .pilot_preflight import build_pilot_preflight
from .pilot_scorecard import build_release_gate
from .version import APP_VERSION


def build_integration_acceptance(db, context, startup, origin, allowed_origins, browser_origin=None):
    connection = build_connection_readiness(db, context, startup, origin, allowed_origins, browser_origin)
    preflight = build_pilot_preflight(db, context.tenant_id, startup)
    release = build_release_gate(db, context.tenant_id, startup)
    accepted = invalid = 0
    # Check all accepted records, never a capped browser cache or historical status alone.
    for row in db.scalars(
        select(CaseAcceptance).where(CaseAcceptance.tenant_id == context.tenant_id, CaseAcceptance.status == "accepted")
    ):
        try:
            current = acceptance_view(db, row, startup)
        except HTTPException:
            invalid += 1
            continue
        if current["effective_status"] == "accepted" and current["current_evidence"]["ready_for_acceptance"]:
            accepted += 1
        else:
            invalid += 1
    checks = [
        {"id": "connection", "label": "当前企业会话与站点接入", "passed": connection["status"] == "ready_for_pilot"},
        {"id": "runtime", "label": "迁移、Worker 与租户状态", "passed": preflight["status"] == "ready"},
        {"id": "release", "label": "配置、供应商与恢复独立复核", "passed": release["status"] == "accepted"},
        {"id": "customer", "label": "至少一个当前有效客户案例验收", "passed": accepted > 0},
    ]
    basis = {
        "schema_version": 1,
        "app_version": APP_VERSION,
        "tenant_id": context.tenant_id,
        "generated_at": utcnow().isoformat(),
        "configuration_digest": release["configuration_digest"],
        "checks": checks,
        "connection": connection,
        "runtime": preflight,
        "release_status": release["status"],
        "customer_cases": {"current_accepted": accepted, "invalidated_accepted": invalid},
        "status": "ready_for_controlled_pilot" if all(c["passed"] for c in checks) else "blocked",
        "scope": "current-tenant-production-and-customer-evidence",
        "real_business_verified": False,
        "enables_external_execution": False,
    }
    basis = jsonable_encoder(basis)
    return basis | {"report_digest": hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()}

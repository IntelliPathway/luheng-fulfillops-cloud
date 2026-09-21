from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .agent_gateway import gateway_overview, gateway_profile
from .agent_tools import execute_controlled_tool
from .asset_catalog_routes import router as asset_catalog_router
from .asset_import_routes import router as asset_import_router
from .audit import audit
from .bootstrap import bootstrap_database
from .config import StartupSettings
from .contact_routes import router as contact_router
from .db import build_engine, build_session_factory
from .dependencies import Context, Database
from .domain import (
    SERVICE_TYPES,
    activity_preflight,
    build_self_test_items,
    gate_for,
    invalidate_integration,
    service_versions,
    test_detail,
    utcnow,
    validate_service_settings,
)
from .financial_ledger import (
    FinancialLedgerError,
    accept_payment_webhook,
    configure_payment_webhook,
    decide_payment_reconciliation,
    financial_summary,
    match_payment_receipt,
    payment_match_candidates,
    payment_sandbox_enabled,
    propose_payment_reconciliation,
    record_commission_event,
    sign_payment_webhook,
)
from .governance_routes import router as governance_router
from .harness_runtime import close_harness_runtimes
from .job_queue import clear_job_lease, queue_health, should_execute_inline
from .jobs import TERMINAL_JOB_STATUSES, enqueue_job, execute_job
from .knowledge_routes import router as knowledge_router
from .membership_routes import router as membership_router
from .model_gateway import (
    LIVE_MODE,
    ModelGatewayError,
    live_model_calls_enabled,
    model_gateway_health,
    model_policy_settings,
    policy_snapshot,
    test_model_runtime,
)
from .model_replay import ReplaySuiteError, replay_suite_metadata
from .models import (
    Activity,
    AgentMessage,
    AgentProposal,
    AgentRuntimeCheckpoint,
    AgentSession,
    AsyncJob,
    CaseRecord,
    CommissionLedgerEntry,
    ConnectionTest,
    IntegrationState,
    ModelReplayRun,
    PaymentReceipt,
    PaymentReconciliation,
    PaymentWebhookConfig,
    ProtectionIncident,
    RecoveryLedgerEntry,
    RepaymentPlan,
    SelfTestReport,
    ServiceConfig,
    User,
)
from .observability import Telemetry, observe_request
from .operation_routes import router as operation_router
from .pilot_routes import router as pilot_router
from .policy_routes import router as policy_router
from .protection_workflow import (
    ProtectionWorkflowError,
    decide_protection_resolution,
    open_protection_incident,
    propose_protection_resolution,
    protection_overview,
)
from .provider_operation_routes import router as provider_operation_router
from .repayment_plans import (
    RepaymentPlanError,
    create_plan_proposal,
    decide_plan,
    plan_view,
    repayment_overview,
)
from .runtime_adapters import test_agent_runtime
from .schemas import (
    ActivityOut,
    ActivityPreflightOut,
    ActivityRequest,
    ActivityTransitionRequest,
    AgentGatewayOut,
    AgentMessageRequest,
    AgentRuntimeOut,
    AgentSessionCreate,
    AgentSessionOut,
    AgentToolRequest,
    AsyncTestRequest,
    AuthHealthOut,
    AuthSessionOut,
    CommissionEventAcceptanceOut,
    CommissionEventRequest,
    CommissionLedgerEntryOut,
    ConnectionTestOut,
    DevTokenOut,
    DevTokenRequest,
    EnableRequest,
    FinancialOverviewOut,
    FinancialSummaryOut,
    GateOut,
    HealthOut,
    IntegrationOverviewOut,
    IntegrationStateOut,
    JobOut,
    ModelGatewayHealthOut,
    ModelReplayRequest,
    ModelReplayRunOut,
    PaymentMatchCandidateOut,
    PaymentReceiptAcceptanceOut,
    PaymentReceiptMatchRequest,
    PaymentReceiptOut,
    PaymentReconciliationCreateRequest,
    PaymentReconciliationDecisionRequest,
    PaymentReconciliationOut,
    PaymentSandboxReceiptRequest,
    PaymentWebhookConfigOut,
    PaymentWebhookConfigRequest,
    PaymentWebhookPayload,
    ProposalDecisionRequest,
    ProposalOut,
    ProtectionIncidentCreateRequest,
    ProtectionIncidentOut,
    ProtectionOverviewOut,
    ProtectionResolutionDecisionRequest,
    ProtectionResolutionProposalRequest,
    QueueHealthOut,
    RecoveryLedgerEntryOut,
    RepaymentOverviewOut,
    RepaymentPlanCreateRequest,
    RepaymentPlanDecisionRequest,
    RepaymentPlanOut,
    SecretStoreHealthOut,
    SelfTestItem,
    SelfTestReportOut,
    ServiceConfigOut,
    ServiceConfigUpsert,
)
from .secret_store import (
    SecretStoreError,
    resolve_secret,
    secret_store_status,
    store_secret,
)
from .security import (
    auth_configuration,
    decode_runtime_token,
    issue_dev_token,
    require_at_least,
    require_role,
)
from .security_headers import add_security_headers
from .telephony_routes import router as telephony_router
from .version import APP_VERSION, PRODUCT_ENGLISH_NAME, PRODUCT_NAME


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    try:
        yield
    finally:
        close_harness_runtimes()


def service_out(config: ServiceConfig) -> ServiceConfigOut:
    mask = f"••••{config.credential_last4}" if config.credential_last4 else None
    return ServiceConfigOut(
        service_type=config.service_type,
        provider=config.provider,
        settings=config.settings,
        credential_mask=mask,
        version=config.version,
        saved=config.saved,
        connected=config.connected,
        latency_ms=config.latency_ms,
        connection_tested_at=config.connection_tested_at,
        updated_at=config.updated_at,
    )


def report_out(report: SelfTestReport | None) -> SelfTestReportOut | None:
    if not report:
        return None
    return SelfTestReportOut(
        id=report.id,
        status=report.status,
        service_versions=report.service_versions,
        items=[SelfTestItem(**item) for item in report.items],
        created_at=report.created_at,
        expires_at=report.expires_at,
    )


def overview(db: Session, tenant_id: str) -> IntegrationOverviewOut:
    configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id)))
    state = db.get(IntegrationState, tenant_id)
    report = db.get(SelfTestReport, state.last_self_test_id) if state and state.last_self_test_id else None
    return IntegrationOverviewOut(
        tenant_id=tenant_id,
        services={item.service_type: service_out(item) for item in configs},
        state=IntegrationStateOut(
            enabled=bool(state and state.enabled),
            enabled_at=state.enabled_at if state else None,
            enabled_by=state.enabled_by if state else None,
            enabled_service_versions=state.enabled_service_versions if state else None,
            invalidated_reason=state.invalidated_reason if state else "尚未初始化",
        ),
        gate=GateOut(**gate_for(db, tenant_id)),
        last_report=report_out(report),
    )


def payment_config_out(config: PaymentWebhookConfig) -> PaymentWebhookConfigOut:
    return PaymentWebhookConfigOut(
        provider=config.provider,
        credential_mask=f"••••{config.credential_last4}",
        version=config.version,
        active=config.active,
        max_amount_cents=config.max_amount_cents,
        updated_at=config.updated_at,
    )


def payment_receipt_out(receipt: PaymentReceipt) -> PaymentReceiptOut:
    return PaymentReceiptOut.model_validate(receipt)


def raise_financial_error(exc: FinancialLedgerError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


def raise_protection_error(exc: ProtectionWorkflowError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


def raise_repayment_plan_error(exc: RepaymentPlanError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


def dispatch_inline_job(background_tasks: BackgroundTasks, session_factory, job_id: str) -> None:
    if should_execute_inline():
        background_tasks.add_task(execute_job, session_factory, job_id)


def create_app(database_url: str | None = None, *, seed_demo_data: bool | None = None) -> FastAPI:
    startup = StartupSettings.from_environment(database_url, seed_demo_data=seed_demo_data)
    app = FastAPI(
        title=f"{PRODUCT_NAME} · {PRODUCT_ENGLISH_NAME} API",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=app_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:4177,http://localhost:5173").split(",")
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Tenant-ID", "X-Actor-ID"],
    )
    engine = build_engine(startup.database_url)
    app.state.engine = engine
    app.state.Session = build_session_factory(engine)
    app.state.startup = startup
    app.state.telemetry = Telemetry()
    app.middleware("http")(observe_request)
    app.middleware("http")(add_security_headers)
    app.state.bootstrap = bootstrap_database(engine, app.state.Session, startup)
    app.include_router(asset_catalog_router)
    app.include_router(asset_import_router)
    app.include_router(policy_router)
    app.include_router(telephony_router)
    app.include_router(pilot_router)
    app.include_router(membership_router)
    app.include_router(contact_router)
    app.include_router(provider_operation_router)
    app.include_router(governance_router)
    app.include_router(knowledge_router)
    app.include_router(operation_router)

    @app.get("/api/v1/health", response_model=HealthOut, tags=["system"])
    @app.get("/api/v1/health/ready", response_model=HealthOut, tags=["system"])
    def health(db: Database) -> HealthOut:
        db.execute(select(1))
        return HealthOut(status="ok", service="luheng-fulfillops-api", version=APP_VERSION)

    @app.get("/api/v1/health/live", response_model=HealthOut, tags=["system"])
    def liveness() -> HealthOut:
        return HealthOut(status="ok", service="luheng-fulfillops-api", version=APP_VERSION)

    @app.get("/api/v1/observability/metrics", tags=["system"])
    def observability_metrics(request: Request, context: Context) -> dict:
        require_role(context, "admin")
        return request.app.state.telemetry.snapshot()

    @app.get("/api/v1/observability/slo", tags=["system"])
    def observability_slo(request: Request, context: Context) -> dict:
        require_role(context, "admin")
        return request.app.state.telemetry.slo_snapshot()

    @app.get("/api/v1/observability/prometheus", tags=["system"])
    def observability_prometheus(request: Request, context: Context) -> Response:
        require_role(context, "admin")
        return Response(request.app.state.telemetry.prometheus(), media_type="text/plain; version=0.0.4")

    @app.post("/api/v1/auth/dev-token", response_model=DevTokenOut, tags=["auth"])
    def create_dev_token(payload: DevTokenRequest, db: Database) -> DevTokenOut:
        user = db.get(User, payload.actor_id)
        if not user or user.status != "active":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="开发用户不存在或已停用")
        return DevTokenOut(access_token=issue_dev_token(payload.actor_id))

    @app.get("/api/v1/auth/session", response_model=AuthSessionOut, tags=["auth"])
    def get_auth_session(context: Context) -> AuthSessionOut:
        return AuthSessionOut(
            actor_id=context.actor_id,
            display_name=context.display_name,
            email=context.email,
            tenant_id=context.tenant_id,
            role=context.role,
            auth_mode=context.auth_mode,
        )

    @app.get("/api/v1/security/auth/health", response_model=AuthHealthOut, tags=["security"])
    def get_auth_health(context: Context) -> AuthHealthOut:
        require_role(context, "admin")
        config = auth_configuration()
        return AuthHealthOut(
            mode=config.mode,
            status=config.status,
            issuer_configured=config.issuer_configured,
            audience_configured=config.audience_configured,
            jwks_configured=config.jwks_configured,
            allowed_algorithms=list(config.allowed_algorithms),
            required_claims=list(config.required_claims),
            tenant_claim=config.tenant_claim,
            leeway_seconds=config.leeway_seconds,
            jwks_cache_seconds=config.jwks_cache_seconds,
            dev_header_enabled=config.dev_header_enabled,
            dev_token_enabled=config.dev_token_enabled,
            detail=config.detail,
        )

    @app.get("/api/v1/integrations", response_model=IntegrationOverviewOut, tags=["integrations"])
    def get_integrations(context: Context, db: Database) -> IntegrationOverviewOut:
        return overview(db, context.tenant_id)

    @app.put("/api/v1/integrations/{service_type}", response_model=IntegrationOverviewOut, tags=["integrations"])
    def save_service(
        service_type: str, payload: ServiceConfigUpsert, context: Context, db: Database
    ) -> IntegrationOverviewOut:
        require_role(context, "admin")
        validate_service_settings(service_type, payload.settings)
        if service_type == "model":
            try:
                model_policy_settings(payload.provider, payload.settings)
            except ModelGatewayError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        config = db.scalar(
            select(ServiceConfig).where(
                ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type
            )
        )
        if not config:
            config = ServiceConfig(
                tenant_id=context.tenant_id, service_type=service_type, provider=payload.provider, settings={}
            )
            db.add(config)
            db.flush()
        if payload.credential:
            try:
                config.secret_ref, config.credential_last4 = store_secret(
                    db,
                    context.tenant_id,
                    service_type,
                    payload.credential,
                    previous_reference=config.secret_ref,
                )
            except SecretStoreError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not config.secret_ref:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="首次配置必须提供 credential")
        config.provider = payload.provider
        config.settings = payload.settings
        config.version += 1
        config.saved = True
        config.connected = False
        config.latency_ms = None
        config.connection_tested_at = None
        state = db.get(IntegrationState, context.tenant_id) or IntegrationState(tenant_id=context.tenant_id)
        db.add(state)
        invalidate_integration(state, f"{service_type} 配置已更新到 v{config.version}，需重新连接测试与沙箱自测")
        audit(
            db,
            context,
            "integration.config.updated",
            "service_config",
            service_type,
            {"version": config.version, "provider": config.provider},
        )
        db.commit()
        return overview(db, context.tenant_id)

    @app.post(
        "/api/v1/integrations/{service_type}/connection-test", response_model=ConnectionTestOut, tags=["integrations"]
    )
    def run_connection_test(service_type: str, context: Context, db: Database) -> ConnectionTestOut:
        require_role(context, "admin")
        if service_type not in SERVICE_TYPES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知服务类型")
        config = db.scalar(
            select(ServiceConfig).where(
                ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type
            )
        )
        if not config:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先保存服务配置")
        validate_service_settings(service_type, config.settings)
        if not config.secret_ref:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="服务凭证尚未托管")
        model_config = (
            db.scalar(
                select(ServiceConfig).where(
                    ServiceConfig.tenant_id == context.tenant_id,
                    ServiceConfig.service_type == "model",
                )
            )
            if service_type == "agent"
            else None
        )
        try:
            if service_type == "agent":
                latency, detail = test_agent_runtime(config, model_config, db)
            elif service_type == "model":
                latency, detail = test_model_runtime(db, config)
            else:
                latency, detail = test_detail(service_type)
        except ModelGatewayError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"模型连接测试失败（{exc.code}）"
            ) from exc
        tested_at = utcnow()
        result = ConnectionTest(
            tenant_id=context.tenant_id,
            service_type=service_type,
            config_version=config.version,
            status="passed",
            latency_ms=latency,
            detail=detail,
            created_at=tested_at,
        )
        config.connected = True
        config.latency_ms = latency
        config.connection_tested_at = tested_at
        db.add(result)
        audit(
            db,
            context,
            "integration.connection_test.passed",
            "service_config",
            service_type,
            {"version": config.version, "latency_ms": latency},
        )
        db.commit()
        db.refresh(result)
        return ConnectionTestOut.model_validate(result)

    @app.post(
        "/api/v1/integrations/{service_type}/connection-test/jobs",
        response_model=JobOut,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["integrations", "jobs"],
    )
    def enqueue_connection_test(
        service_type: str,
        payload: AsyncTestRequest,
        background_tasks: BackgroundTasks,
        context: Context,
        db: Database,
    ) -> JobOut:
        require_role(context, "admin")
        if service_type not in SERVICE_TYPES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知服务类型")
        config = db.scalar(
            select(ServiceConfig).where(
                ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type
            )
        )
        if not config:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先保存服务配置")
        job, created = enqueue_job(
            db,
            context,
            "integration.connection_test",
            {"service_type": service_type, "config_version": config.version},
            payload.idempotency_key,
        )
        if created:
            dispatch_inline_job(background_tasks, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.post("/api/v1/integrations/self-test", response_model=SelfTestReportOut, tags=["integrations"])
    def run_self_test(context: Context, db: Database) -> SelfTestReportOut:
        require_role(context, "admin")
        configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id)))
        items = build_self_test_items(configs)
        passed = len(configs) == 4 and all(item["status"] == "passed" for item in items)
        now = utcnow()
        report = SelfTestReport(
            tenant_id=context.tenant_id,
            status="passed" if passed else "blocked",
            service_versions=service_versions(configs),
            items=items,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        db.add(report)
        db.flush()
        state = db.get(IntegrationState, context.tenant_id) or IntegrationState(tenant_id=context.tenant_id)
        db.add(state)
        state.last_self_test_id = report.id
        state.enabled = False
        state.enabled_at = None
        state.enabled_by = None
        state.enabled_service_versions = None
        state.invalidated_reason = None if passed else "沙箱自测存在未通过项"
        audit(db, context, "integration.self_test.completed", "self_test_report", report.id, {"status": report.status})
        db.commit()
        return report_out(report)

    @app.post(
        "/api/v1/integrations/self-test/jobs",
        response_model=JobOut,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["integrations", "jobs"],
    )
    def enqueue_self_test(
        payload: AsyncTestRequest,
        background_tasks: BackgroundTasks,
        context: Context,
        db: Database,
    ) -> JobOut:
        require_role(context, "admin")
        job, created = enqueue_job(
            db,
            context,
            "integration.self_test",
            {
                "service_versions": service_versions(
                    list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id)))
                )
            },
            payload.idempotency_key,
        )
        if created:
            dispatch_inline_job(background_tasks, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.post("/api/v1/integrations/enable", response_model=IntegrationOverviewOut, tags=["integrations"])
    def enable_integrations(payload: EnableRequest, context: Context, db: Database) -> IntegrationOverviewOut:
        require_role(context, "admin")
        if not payload.acknowledged:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认启用")
        gate = gate_for(db, context.tenant_id)
        if not (gate["all_saved"] and gate["all_connected"] and gate["report_current"] and gate["report_fresh"]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="连接测试或沙箱自测门禁未通过")
        configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id)))
        state = db.get(IntegrationState, context.tenant_id)
        state.enabled = True
        state.enabled_at = utcnow()
        state.enabled_by = context.actor_id
        state.enabled_service_versions = service_versions(configs)
        state.invalidated_reason = None
        audit(
            db,
            context,
            "integration.enabled",
            "integration_state",
            context.tenant_id,
            {"service_versions": state.enabled_service_versions},
        )
        db.commit()
        return overview(db, context.tenant_id)

    @app.get("/api/v1/jobs", response_model=list[JobOut], tags=["jobs"])
    def list_jobs(
        context: Context,
        db: Database,
        job_status: str | None = Query(default=None, alias="status"),
        kind: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[JobOut]:
        statement = select(AsyncJob).where(AsyncJob.tenant_id == context.tenant_id)
        if job_status:
            statement = statement.where(AsyncJob.status == job_status)
        if kind:
            statement = statement.where(AsyncJob.kind == kind)
        rows = db.scalars(statement.order_by(AsyncJob.created_at.desc()).limit(limit))
        return [JobOut.model_validate(row) for row in rows]

    @app.get("/api/v1/jobs/queue/health", response_model=QueueHealthOut, tags=["jobs"])
    def get_queue_health(context: Context, db: Database) -> QueueHealthOut:
        return QueueHealthOut(**queue_health(db, context.tenant_id))

    @app.get("/api/v1/security/secrets/health", response_model=SecretStoreHealthOut, tags=["security"])
    def get_secret_store_health(context: Context, db: Database) -> SecretStoreHealthOut:
        require_role(context, "admin")
        return SecretStoreHealthOut(**secret_store_status(db, context.tenant_id).__dict__)

    @app.get(
        "/api/v1/payments/webhook-configs",
        response_model=list[PaymentWebhookConfigOut],
        tags=["payments", "security"],
    )
    def list_payment_webhook_configs(context: Context, db: Database) -> list[PaymentWebhookConfigOut]:
        require_role(context, "admin")
        configs = db.scalars(
            select(PaymentWebhookConfig)
            .where(PaymentWebhookConfig.tenant_id == context.tenant_id)
            .order_by(PaymentWebhookConfig.provider)
        )
        return [payment_config_out(config) for config in configs]

    @app.put(
        "/api/v1/payments/webhook-configs/{provider}",
        response_model=PaymentWebhookConfigOut,
        tags=["payments", "security"],
    )
    def save_payment_webhook_config(
        provider: str,
        payload: PaymentWebhookConfigRequest,
        context: Context,
        db: Database,
    ) -> PaymentWebhookConfigOut:
        require_role(context, "admin")
        try:
            config = configure_payment_webhook(
                db,
                context.tenant_id,
                provider,
                payload.credential,
                payload.max_amount_cents,
            )
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        audit(
            db,
            context,
            "payment.webhook_config.updated",
            "payment_webhook_config",
            config.provider,
            {"version": config.version, "max_amount_cents": config.max_amount_cents},
        )
        db.commit()
        db.refresh(config)
        return payment_config_out(config)

    @app.get("/api/v1/payments/overview", response_model=FinancialOverviewOut, tags=["payments"])
    def get_financial_overview(context: Context, db: Database) -> FinancialOverviewOut:
        summary = financial_summary(db, context.tenant_id)
        recoveries = list(
            db.scalars(
                select(RecoveryLedgerEntry)
                .where(RecoveryLedgerEntry.tenant_id == context.tenant_id)
                .order_by(RecoveryLedgerEntry.booked_at.desc(), RecoveryLedgerEntry.entry_id.desc())
                .limit(500)
            )
        )
        commissions = list(
            db.scalars(
                select(CommissionLedgerEntry)
                .where(CommissionLedgerEntry.tenant_id == context.tenant_id)
                .order_by(CommissionLedgerEntry.occurred_at.desc(), CommissionLedgerEntry.event_id.desc())
                .limit(500)
            )
        )
        pending = list(
            db.scalars(
                select(PaymentReceipt)
                .where(
                    PaymentReceipt.tenant_id == context.tenant_id,
                    PaymentReceipt.status.in_({"unmatched", "review_required", "review_pending"}),
                )
                .order_by(PaymentReceipt.received_at.desc())
                .limit(100)
            )
        )
        reconciliations = list(
            db.scalars(
                select(PaymentReconciliation)
                .where(PaymentReconciliation.tenant_id == context.tenant_id)
                .order_by(PaymentReconciliation.proposed_at.desc())
                .limit(100)
            )
        )
        config = db.scalar(
            select(PaymentWebhookConfig)
            .where(
                PaymentWebhookConfig.tenant_id == context.tenant_id,
                PaymentWebhookConfig.active.is_(True),
            )
            .order_by(PaymentWebhookConfig.updated_at.desc())
        )
        secret_status = secret_store_status(db, context.tenant_id)
        return FinancialOverviewOut(
            summary=FinancialSummaryOut(**summary),
            recovery_ledger=[RecoveryLedgerEntryOut.model_validate(row) for row in recoveries],
            commission_ledger=[CommissionLedgerEntryOut.model_validate(row) for row in commissions],
            pending_receipts=[payment_receipt_out(row) for row in pending],
            reconciliations=[PaymentReconciliationOut.model_validate(row) for row in reconciliations],
            webhook_ready=bool(config and config.secret_ref and secret_status.resolvable),
            webhook_provider=config.provider if config else None,
            sandbox_enabled=payment_sandbox_enabled(),
        )

    @app.post(
        "/api/v1/webhooks/payments/{tenant_id}/{provider}",
        response_model=PaymentReceiptAcceptanceOut,
        tags=["payment-webhooks"],
    )
    async def receive_payment_webhook(
        tenant_id: str,
        provider: str,
        request: Request,
        db: Database,
        webhook_timestamp: Annotated[str, Header(alias="X-FulfillOps-Timestamp")],
        webhook_signature: Annotated[str, Header(alias="X-FulfillOps-Signature")],
    ) -> PaymentReceiptAcceptanceOut:
        raw_body = await request.body()
        if len(raw_body) > 32_768:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="支付回执载荷超过大小上限")
        try:
            payload = PaymentWebhookPayload.model_validate_json(raw_body)
            accepted = accept_payment_webhook(
                db,
                tenant_id,
                provider,
                payload,
                raw_body,
                webhook_timestamp,
                webhook_signature,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="支付回执载荷格式无效"
            ) from exc
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return PaymentReceiptAcceptanceOut(receipt=payment_receipt_out(accepted.receipt), duplicate=accepted.duplicate)

    @app.post(
        "/api/v1/payments/sandbox-receipts",
        response_model=PaymentReceiptAcceptanceOut,
        tags=["payments"],
    )
    def receive_sandbox_payment(
        payload: PaymentSandboxReceiptRequest,
        context: Context,
        db: Database,
    ) -> PaymentReceiptAcceptanceOut:
        require_role(context, "operator", "admin")
        if not payment_sandbox_enabled():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="部署环境未启用支付回执沙箱")
        provider = "sandbox-amc"
        config = db.scalar(
            select(PaymentWebhookConfig).where(
                PaymentWebhookConfig.tenant_id == context.tenant_id,
                PaymentWebhookConfig.provider == provider,
                PaymentWebhookConfig.active.is_(True),
            )
        )
        if not config:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="支付回执沙箱尚未配置签名密钥")
        try:
            secret = resolve_secret(db, config.secret_ref, context.tenant_id, f"payment-{provider}")
        except SecretStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="支付回执沙箱密钥不可用"
            ) from exc
        if not secret:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="支付回执沙箱密钥不可解析")
        now = datetime.now(UTC)
        event = PaymentWebhookPayload(
            event_id=f"SBX-{payload.idempotency_key}",
            event_type="payment",
            amount_cents=payload.amount_cents,
            currency="CNY",
            occurred_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
            case_id=payload.case_id,
        )
        raw_body = event.model_dump_json(exclude_none=True).encode()
        timestamp_value = str(int(now.timestamp()))
        signature = sign_payment_webhook(secret, timestamp_value, raw_body)
        try:
            accepted = accept_payment_webhook(
                db,
                context.tenant_id,
                provider,
                event,
                raw_body,
                timestamp_value,
                signature,
            )
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return PaymentReceiptAcceptanceOut(receipt=payment_receipt_out(accepted.receipt), duplicate=accepted.duplicate)

    @app.post(
        "/api/v1/payments/receipts/{receipt_id}/match",
        response_model=RecoveryLedgerEntryOut,
        tags=["payments"],
    )
    def match_pending_payment_receipt(
        receipt_id: str,
        payload: PaymentReceiptMatchRequest,
        context: Context,
        db: Database,
    ) -> RecoveryLedgerEntryOut:
        require_role(context, "admin")
        if os.getenv("ALLOW_LEGACY_PAYMENT_MATCH", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="单步回执匹配已停用；请使用提案与独立复核流程",
            )
        if not payload.acknowledged:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认回执匹配")
        receipt = db.scalar(
            select(PaymentReceipt)
            .where(
                PaymentReceipt.id == receipt_id,
                PaymentReceipt.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not receipt:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="支付回执不存在")
        try:
            entry = match_payment_receipt(db, receipt, payload.case_id, context.actor_id)
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return RecoveryLedgerEntryOut.model_validate(entry)

    @app.get(
        "/api/v1/payments/receipts/{receipt_id}/candidates",
        response_model=list[PaymentMatchCandidateOut],
        tags=["payments"],
    )
    def get_payment_match_candidates(
        receipt_id: str,
        context: Context,
        db: Database,
    ) -> list[PaymentMatchCandidateOut]:
        receipt = db.scalar(
            select(PaymentReceipt).where(
                PaymentReceipt.id == receipt_id,
                PaymentReceipt.tenant_id == context.tenant_id,
            )
        )
        if not receipt:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="支付回执不存在")
        if receipt.status == "matched":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="支付回执已经匹配入账")
        return [PaymentMatchCandidateOut(**row) for row in payment_match_candidates(db, receipt)]

    @app.get(
        "/api/v1/payments/reconciliations",
        response_model=list[PaymentReconciliationOut],
        tags=["payments"],
    )
    def list_payment_reconciliations(
        context: Context,
        db: Database,
        review_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[PaymentReconciliationOut]:
        statement = select(PaymentReconciliation).where(PaymentReconciliation.tenant_id == context.tenant_id)
        if review_status:
            statement = statement.where(PaymentReconciliation.status == review_status)
        rows = db.scalars(statement.order_by(PaymentReconciliation.proposed_at.desc()).limit(limit))
        return [PaymentReconciliationOut.model_validate(row) for row in rows]

    @app.post(
        "/api/v1/payments/receipts/{receipt_id}/reconciliations",
        response_model=PaymentReconciliationOut,
        tags=["payments"],
    )
    def create_payment_reconciliation(
        receipt_id: str,
        payload: PaymentReconciliationCreateRequest,
        context: Context,
        db: Database,
    ) -> PaymentReconciliationOut:
        require_role(context, "operator", "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须确认仅创建匹配提案，不直接入账"
            )
        receipt = db.scalar(
            select(PaymentReceipt)
            .where(
                PaymentReceipt.id == receipt_id,
                PaymentReceipt.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not receipt:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="支付回执不存在")
        try:
            review = propose_payment_reconciliation(
                db,
                receipt,
                payload.case_id,
                payload.reason,
                context.actor_id,
            )
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return PaymentReconciliationOut.model_validate(review)

    @app.post(
        "/api/v1/payments/reconciliations/{reconciliation_id}/decision",
        response_model=PaymentReconciliationOut,
        tags=["payments"],
    )
    def decide_reconciliation(
        reconciliation_id: str,
        payload: PaymentReconciliationDecisionRequest,
        context: Context,
        db: Database,
    ) -> PaymentReconciliationOut:
        require_role(context, "admin")
        if not payload.acknowledged:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认复核决定")
        review = db.scalar(
            select(PaymentReconciliation)
            .where(
                PaymentReconciliation.id == reconciliation_id,
                PaymentReconciliation.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not review:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对账提案不存在")
        try:
            decided = decide_payment_reconciliation(
                db,
                review,
                payload.decision,
                payload.review_note,
                payload.expected_version,
                context.actor_id,
            )
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return PaymentReconciliationOut.model_validate(decided)

    @app.post(
        "/api/v1/commissions/events",
        response_model=CommissionEventAcceptanceOut,
        tags=["payments", "commissions"],
    )
    def create_commission_event(
        payload: CommissionEventRequest,
        context: Context,
        db: Database,
    ) -> CommissionEventAcceptanceOut:
        require_role(context, "admin")
        if not payload.acknowledged:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认佣金账簿事件")
        try:
            entry, duplicate = record_commission_event(
                db,
                context.tenant_id,
                payload.event_type,
                payload.amount_cents,
                payload.reference,
                payload.idempotency_key,
                context.actor_id,
                payload.occurred_at,
            )
        except FinancialLedgerError as exc:
            raise_financial_error(exc)
        return CommissionEventAcceptanceOut(entry=CommissionLedgerEntryOut.model_validate(entry), duplicate=duplicate)

    @app.get(
        "/api/v1/protections/overview",
        response_model=ProtectionOverviewOut,
        tags=["protections"],
    )
    def get_protection_overview(context: Context, db: Database) -> ProtectionOverviewOut:
        return ProtectionOverviewOut.model_validate(protection_overview(db, context.tenant_id))

    @app.get(
        "/api/v1/protections/incidents",
        response_model=list[ProtectionIncidentOut],
        tags=["protections"],
    )
    def list_protection_incidents(
        context: Context,
        db: Database,
        incident_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[ProtectionIncidentOut]:
        statement = select(ProtectionIncident).where(ProtectionIncident.tenant_id == context.tenant_id)
        if incident_status:
            statement = statement.where(ProtectionIncident.status == incident_status)
        rows = db.scalars(statement.order_by(ProtectionIncident.opened_at.desc()).limit(limit))
        return [ProtectionIncidentOut.model_validate(row) for row in rows]

    @app.post(
        "/api/v1/cases/{case_id}/protections",
        response_model=ProtectionIncidentOut,
        status_code=status.HTTP_201_CREATED,
        tags=["protections"],
    )
    def create_protection_incident(
        case_id: str,
        payload: ProtectionIncidentCreateRequest,
        context: Context,
        db: Database,
    ) -> ProtectionIncidentOut:
        require_role(context, "operator", "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="必须确认保护事件会立即阻断案件及相关活动",
            )
        try:
            incident = open_protection_incident(
                db,
                context.tenant_id,
                case_id.strip().upper(),
                payload.source_event_id,
                payload.category,
                payload.reason,
                payload.owner,
                payload.sla_hours,
                context.actor_id,
            )
        except ProtectionWorkflowError as exc:
            raise_protection_error(exc)
        return ProtectionIncidentOut.model_validate(incident)

    @app.post(
        "/api/v1/protections/incidents/{incident_id}/resolution-proposals",
        response_model=ProtectionIncidentOut,
        tags=["protections"],
    )
    def create_protection_resolution_proposal(
        incident_id: str,
        payload: ProtectionResolutionProposalRequest,
        context: Context,
        db: Database,
    ) -> ProtectionIncidentOut:
        require_role(context, "operator", "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="必须确认本次只创建解除提案，不直接恢复触达",
            )
        incident = db.scalar(
            select(ProtectionIncident)
            .where(
                ProtectionIncident.id == incident_id,
                ProtectionIncident.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not incident:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="保护事件不存在")
        try:
            proposed = propose_protection_resolution(
                db,
                incident,
                payload.resolution_note,
                payload.evidence_refs,
                context.actor_id,
            )
        except ProtectionWorkflowError as exc:
            raise_protection_error(exc)
        return ProtectionIncidentOut.model_validate(proposed)

    @app.post(
        "/api/v1/protections/incidents/{incident_id}/decision",
        response_model=ProtectionIncidentOut,
        tags=["protections"],
    )
    def decide_protection_incident(
        incident_id: str,
        payload: ProtectionResolutionDecisionRequest,
        context: Context,
        db: Database,
    ) -> ProtectionIncidentOut:
        require_role(context, "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="必须明确确认保护复核决定",
            )
        incident = db.scalar(
            select(ProtectionIncident)
            .where(
                ProtectionIncident.id == incident_id,
                ProtectionIncident.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not incident:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="保护事件不存在")
        try:
            decided = decide_protection_resolution(
                db,
                incident,
                payload.decision,
                payload.review_note,
                payload.expected_version,
                context.actor_id,
            )
        except ProtectionWorkflowError as exc:
            raise_protection_error(exc)
        return ProtectionIncidentOut.model_validate(decided)

    @app.get(
        "/api/v1/repayment-plans/overview",
        response_model=RepaymentOverviewOut,
        tags=["repayment-plans"],
    )
    def get_repayment_overview(context: Context, db: Database) -> RepaymentOverviewOut:
        return RepaymentOverviewOut.model_validate(repayment_overview(db, context.tenant_id))

    @app.get(
        "/api/v1/cases/{case_id}/repayment-plans",
        response_model=list[RepaymentPlanOut],
        tags=["repayment-plans"],
    )
    def list_case_repayment_plans(
        case_id: str,
        context: Context,
        db: Database,
    ) -> list[RepaymentPlanOut]:
        rows = db.scalars(
            select(RepaymentPlan)
            .where(
                RepaymentPlan.tenant_id == context.tenant_id,
                RepaymentPlan.case_id == case_id.strip().upper(),
            )
            .order_by(RepaymentPlan.proposed_at.desc())
        )
        return [RepaymentPlanOut.model_validate(plan_view(db, row)) for row in rows]

    @app.post(
        "/api/v1/cases/{case_id}/repayment-plans",
        response_model=RepaymentPlanOut,
        status_code=status.HTTP_201_CREATED,
        tags=["repayment-plans"],
    )
    def propose_repayment_plan(
        case_id: str,
        payload: RepaymentPlanCreateRequest,
        context: Context,
        db: Database,
    ) -> RepaymentPlanOut:
        require_role(context, "operator", "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="必须确认本次只提交签约证据与方案提案，不直接生效",
            )
        try:
            plan = create_plan_proposal(
                db,
                context.tenant_id,
                case_id,
                payload.plan_id,
                payload.total_cents,
                payload.down_payment_cents,
                [item.model_dump() for item in payload.installments],
                payload.agreement_reference,
                payload.agreement_digest,
                payload.signed_at,
                payload.proposal_reason,
                context.actor_id,
            )
        except RepaymentPlanError as exc:
            raise_repayment_plan_error(exc)
        return RepaymentPlanOut.model_validate(plan_view(db, plan))

    @app.post(
        "/api/v1/repayment-plans/{plan_row_id}/decision",
        response_model=RepaymentPlanOut,
        tags=["repayment-plans"],
    )
    def decide_repayment_plan(
        plan_row_id: str,
        payload: RepaymentPlanDecisionRequest,
        context: Context,
        db: Database,
    ) -> RepaymentPlanOut:
        require_role(context, "admin")
        if not payload.acknowledged:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="必须明确确认方案复核决定",
            )
        plan = db.scalar(
            select(RepaymentPlan)
            .where(
                RepaymentPlan.id == plan_row_id,
                RepaymentPlan.tenant_id == context.tenant_id,
            )
            .with_for_update()
        )
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="履约方案不存在")
        try:
            decided = decide_plan(
                db,
                plan,
                payload.decision,
                payload.review_note,
                payload.expected_version,
                context.actor_id,
            )
        except RepaymentPlanError as exc:
            raise_repayment_plan_error(exc)
        return RepaymentPlanOut.model_validate(plan_view(db, decided))

    @app.get("/api/v1/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
    def get_job(job_id: str, context: Context, db: Database) -> JobOut:
        job = db.scalar(select(AsyncJob).where(AsyncJob.id == job_id, AsyncJob.tenant_id == context.tenant_id))
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")
        return JobOut.model_validate(job)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
    def cancel_job(job_id: str, context: Context, db: Database) -> JobOut:
        require_role(context, "operator", "admin")
        job = db.scalar(select(AsyncJob).where(AsyncJob.id == job_id, AsyncJob.tenant_id == context.tenant_id))
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")
        if job.status in TERMINAL_JOB_STATUSES:
            return JobOut.model_validate(job)
        now = utcnow()
        lease_expired = bool(job.status == "running" and job.lease_expires_at and job.lease_expires_at <= now)
        if job.status == "queued" or lease_expired:
            job.status = "cancelled"
            job.completed_at = now
            job.cancel_requested_at = now
            clear_job_lease(job)
            audit(db, context, "job.cancelled", "async_job", job.id, {"kind": job.kind, "immediate": True})
        else:
            job.cancel_requested_at = now
            audit(
                db,
                context,
                "job.cancel_requested",
                "async_job",
                job.id,
                {"kind": job.kind, "lease_owner": job.lease_owner},
            )
        db.commit()
        return JobOut.model_validate(job)

    @app.post("/api/v1/jobs/{job_id}/retry", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
    def retry_job(job_id: str, background_tasks: BackgroundTasks, context: Context, db: Database) -> JobOut:
        require_role(context, "operator", "admin")
        job = db.scalar(select(AsyncJob).where(AsyncJob.id == job_id, AsyncJob.tenant_id == context.tenant_id))
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")
        if job.status != "failed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有失败作业可以重试")
        if job.attempt >= job.max_attempts:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="作业已达到最大重试次数")
        job.status = "queued"
        job.error = None
        job.result = None
        job.available_at = utcnow()
        job.started_at = None
        job.completed_at = None
        job.heartbeat_at = None
        job.cancel_requested_at = None
        clear_job_lease(job)
        audit(db, context, "job.retried", "async_job", job.id, {"kind": job.kind, "next_attempt": job.attempt + 1})
        db.commit()
        dispatch_inline_job(background_tasks, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.get("/api/v1/agents/gateway", response_model=AgentGatewayOut, tags=["agents"])
    def get_agent_gateway(context: Context, db: Database) -> AgentGatewayOut:
        return AgentGatewayOut(**gateway_overview(db, context.tenant_id))

    @app.get("/api/v1/models/gateway/health", response_model=ModelGatewayHealthOut, tags=["models", "security"])
    def get_model_gateway_health(context: Context, db: Database) -> ModelGatewayHealthOut:
        require_role(context, "admin")
        config = db.scalar(
            select(ServiceConfig).where(
                ServiceConfig.tenant_id == context.tenant_id,
                ServiceConfig.service_type == "model",
            )
        )
        return ModelGatewayHealthOut(**model_gateway_health(config))

    @app.post(
        "/api/v1/agents/replays/jobs",
        response_model=JobOut,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["agents", "jobs"],
    )
    def enqueue_model_replay(
        payload: ModelReplayRequest,
        background_tasks: BackgroundTasks,
        context: Context,
        db: Database,
    ) -> JobOut:
        require_role(context, "admin")
        try:
            metadata = replay_suite_metadata(payload.suite_name)
        except ReplaySuiteError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        model_config = None
        model_snapshot: dict = {
            "execution_mode": "deterministic-contract",
            "external_calls_allowed": False,
            "data_policy": "no-provider-input",
        }
        provider = metadata["provider"]
        profile = metadata["profile"]
        if payload.mode == LIVE_MODE:
            if not payload.acknowledged_external_call:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="真实模型回放必须明确确认外部调用"
                )
            if not live_model_calls_enabled():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="部署环境未启用真实模型调用")
            model_config = db.scalar(
                select(ServiceConfig).where(
                    ServiceConfig.tenant_id == context.tenant_id,
                    ServiceConfig.service_type == "model",
                )
            )
            if not model_config or not model_config.connected:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="真实模型回放要求模型配置已通过连接测试"
                )
            try:
                model_snapshot = policy_snapshot(model_config)
            except ModelGatewayError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
            if model_snapshot["execution_mode"] != LIVE_MODE:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型配置尚未选择真实 Provider 模式")
            provider = model_config.provider
            profile = "governed-live-evaluation-v1"
        job, created = enqueue_job(
            db,
            context,
            "agent.model_replay",
            {
                "suite_name": metadata["name"],
                "suite_version": metadata["version"],
                "mode": payload.mode,
                "dataset_digest": metadata["dataset_digest"],
                "model_config_version": model_config.version if model_config else None,
            },
            payload.idempotency_key,
            max_attempts=1 if payload.mode == LIVE_MODE else 2,
            commit=False,
        )
        if created:
            replay = ModelReplayRun(
                tenant_id=context.tenant_id,
                job_id=job.id,
                suite_name=metadata["name"],
                suite_version=metadata["version"],
                mode=payload.mode,
                provider=provider,
                profile=profile,
                model_config_version=model_config.version if model_config else None,
                model_name=str(model_config.settings.get("model")) if model_config else None,
                status="queued",
                dataset_digest=metadata["dataset_digest"],
                policy_snapshot=model_snapshot,
                results=[],
                created_by=context.actor_id,
            )
            db.add(replay)
            db.flush()
            job.payload = {**job.payload, "replay_run_id": replay.id}
            audit(
                db,
                context,
                "agent.replay.queued",
                "model_replay_run",
                replay.id,
                {
                    "suite_name": replay.suite_name,
                    "suite_version": replay.suite_version,
                    "dataset_digest": replay.dataset_digest,
                    "mode": replay.mode,
                    "model_config_version": replay.model_config_version,
                    "external_call_approved": payload.mode == LIVE_MODE,
                },
            )
            db.commit()
            dispatch_inline_job(background_tasks, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.get("/api/v1/agents/replays", response_model=list[ModelReplayRunOut], tags=["agents"])
    def list_model_replays(
        context: Context,
        db: Database,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[ModelReplayRunOut]:
        require_role(context, "admin")
        rows = db.scalars(
            select(ModelReplayRun)
            .where(ModelReplayRun.tenant_id == context.tenant_id)
            .order_by(ModelReplayRun.created_at.desc())
            .limit(limit)
        )
        return [ModelReplayRunOut.model_validate(row) for row in rows]

    @app.get("/api/v1/agents/replays/{replay_run_id}", response_model=ModelReplayRunOut, tags=["agents"])
    def get_model_replay(replay_run_id: str, context: Context, db: Database) -> ModelReplayRunOut:
        require_role(context, "admin")
        replay = db.scalar(
            select(ModelReplayRun).where(
                ModelReplayRun.id == replay_run_id,
                ModelReplayRun.tenant_id == context.tenant_id,
            )
        )
        if not replay:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型回放记录不存在")
        return ModelReplayRunOut.model_validate(replay)

    @app.post("/api/v1/internal/agent-tools/{tool_name}", include_in_schema=False)
    def call_internal_agent_tool(
        tool_name: str,
        payload: AgentToolRequest,
        db: Database,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Runtime 工具令牌")
        grant = decode_runtime_token(authorization.split(" ", 1)[1].strip())
        session = db.scalar(
            select(AgentSession).where(
                AgentSession.id == grant.session_id,
                AgentSession.tenant_id == grant.tenant_id,
            )
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Runtime 会话不存在或租户不匹配")
        if session.scope_type != grant.scope_type or session.scope_id != grant.scope_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Runtime 工具令牌范围已失效")
        try:
            return execute_controlled_tool(
                db,
                grant.tenant_id,
                tool_name,
                payload.arguments,
                agent_session_id=grant.session_id,
                scope_type=grant.scope_type,
                scope_id=grant.scope_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post(
        "/api/v1/agents/sessions", response_model=AgentSessionOut, status_code=status.HTTP_201_CREATED, tags=["agents"]
    )
    def create_agent_session(payload: AgentSessionCreate, context: Context, db: Database) -> AgentSessionOut:
        if payload.scope_type != "global" and not payload.scope_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="活动或案件会话必须指定 scope_id"
            )
        if payload.scope_type == "activity":
            exists = db.scalar(
                select(Activity.id).where(
                    Activity.tenant_id == context.tenant_id, Activity.activity_id == payload.scope_id
                )
            )
            if not exists:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
        if payload.scope_type == "case":
            exists = db.scalar(
                select(CaseRecord.id).where(
                    CaseRecord.tenant_id == context.tenant_id, CaseRecord.case_id == payload.scope_id
                )
            )
            if not exists:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="案件不存在")
        profile = gateway_profile(db, context.tenant_id)
        session = AgentSession(
            tenant_id=context.tenant_id,
            created_by=context.actor_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            runtime_provider=profile.provider,
            runtime_profile=profile.profile,
            title=payload.title,
        )
        db.add(session)
        db.flush()
        audit(
            db,
            context,
            "agent.session.created",
            "agent_session",
            session.id,
            {"scope_type": session.scope_type, "scope_id": session.scope_id},
        )
        db.commit()
        return AgentSessionOut.model_validate(session)

    @app.get("/api/v1/agents/sessions/{session_id}/messages", tags=["agents"])
    def list_agent_messages(session_id: str, context: Context, db: Database) -> list[dict]:
        session = db.scalar(
            select(AgentSession).where(AgentSession.id == session_id, AgentSession.tenant_id == context.tenant_id)
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 会话不存在")
        if session.created_by != context.actor_id and context.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看其他成员的 Agent 会话")
        messages = db.scalars(
            select(AgentMessage).where(AgentMessage.session_id == session.id).order_by(AgentMessage.created_at)
        )
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "structured": row.structured,
                "created_at": row.created_at,
            }
            for row in messages
        ]

    @app.get("/api/v1/agents/sessions/{session_id}/runtime", response_model=AgentRuntimeOut, tags=["agents"])
    def get_agent_runtime(session_id: str, context: Context, db: Database) -> AgentRuntimeOut:
        session = db.scalar(
            select(AgentSession).where(AgentSession.id == session_id, AgentSession.tenant_id == context.tenant_id)
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 会话不存在")
        if session.created_by != context.actor_id and context.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看其他成员的 Runtime 会话")
        checkpoint = db.scalar(
            select(AgentRuntimeCheckpoint).where(
                AgentRuntimeCheckpoint.tenant_id == context.tenant_id,
                AgentRuntimeCheckpoint.session_id == session.id,
            )
        )
        return AgentRuntimeOut(
            session_id=session.id,
            provider=session.runtime_provider,
            profile=session.runtime_profile,
            provider_session_id=checkpoint.provider_session_id if checkpoint else None,
            event_cursor=checkpoint.event_cursor if checkpoint else 0,
            turn_count=checkpoint.turn_count if checkpoint else 0,
            last_run_id=checkpoint.last_run_id if checkpoint else None,
            runtime_metadata=checkpoint.runtime_metadata if checkpoint else {},
            updated_at=checkpoint.updated_at if checkpoint else None,
        )

    @app.post(
        "/api/v1/agents/sessions/{session_id}/messages",
        response_model=JobOut,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["agents", "jobs"],
    )
    def enqueue_agent_message(
        session_id: str,
        payload: AgentMessageRequest,
        background_tasks: BackgroundTasks,
        context: Context,
        db: Database,
    ) -> JobOut:
        session = db.scalar(
            select(AgentSession).where(AgentSession.id == session_id, AgentSession.tenant_id == context.tenant_id)
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 会话不存在")
        if session.created_by != context.actor_id and context.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权向其他成员的 Agent 会话写入消息")
        job, created = enqueue_job(
            db,
            context,
            "agent.turn",
            {"session_id": session.id, "content": payload.content},
            payload.idempotency_key,
            commit=False,
        )
        if created:
            db.add(
                AgentMessage(
                    tenant_id=context.tenant_id,
                    session_id=session.id,
                    role="user",
                    content=payload.content,
                    structured={"job_id": job.id},
                )
            )
            db.commit()
            dispatch_inline_job(background_tasks, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.post("/api/v1/agents/proposals/{proposal_id}/confirm", response_model=ProposalOut, tags=["agents"])
    def confirm_agent_proposal(
        proposal_id: str,
        payload: ProposalDecisionRequest,
        context: Context,
        db: Database,
    ) -> ProposalOut:
        if not payload.acknowledged:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认行动提案")
        proposal = db.scalar(
            select(AgentProposal).where(AgentProposal.id == proposal_id, AgentProposal.tenant_id == context.tenant_id)
        )
        if not proposal:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="行动提案不存在")
        require_at_least(context, proposal.required_role)
        if proposal.status != "pending":
            return ProposalOut.model_validate(proposal)
        if proposal.expires_at <= utcnow():
            proposal.status = "expired"
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="行动提案已过期，请重新生成")
        activity_id = str(proposal.arguments.get("activity_id") or "")
        activity = db.scalar(
            select(Activity).where(Activity.tenant_id == context.tenant_id, Activity.activity_id == activity_id)
        )
        if not activity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标活动不存在")
        if proposal.action_type == "activity.pause":
            if activity.status in {"completed", "blocked"}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="完成或保护阻断的活动不能用普通暂停操作修改"
                )
            activity.status = "paused"
        elif proposal.action_type == "activity.resume":
            if activity.status != "paused":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="只有普通暂停活动可以恢复；保护阻断需走异常处理"
                )
            activity.status = "running"
        else:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该行动类型不支持直接确认执行")
        proposal.status = "confirmed"
        proposal.decided_at = utcnow()
        proposal.decided_by = context.actor_id
        audit(
            db,
            context,
            "agent.proposal.confirmed",
            "agent_proposal",
            proposal.id,
            {
                "action_type": proposal.action_type,
                "activity_id": activity.activity_id,
                "result_status": activity.status,
            },
        )
        db.commit()
        return ProposalOut.model_validate(proposal)

    @app.post("/api/v1/activities/preflight", response_model=ActivityPreflightOut, tags=["activities"])
    def preflight_activity(payload: ActivityRequest, context: Context, db: Database) -> ActivityPreflightOut:
        require_role(context, "operator", "admin")
        return ActivityPreflightOut(**activity_preflight(db, context.tenant_id, payload))

    @app.post(
        "/api/v1/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED, tags=["activities"]
    )
    def create_activity(payload: ActivityRequest, context: Context, db: Database) -> ActivityOut:
        require_role(context, "operator", "admin")
        preflight = activity_preflight(db, context.tenant_id, payload)
        if not preflight["eligible_case_ids"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="没有符合条件的可执行案件")
        if payload.requested_mode == "channel" and not preflight["production_ready"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="授权渠道门禁未通过，不能创建生产触达活动")
        count = db.scalar(select(func.count(Activity.id)).where(Activity.tenant_id == context.tenant_id)) or 0
        configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id)))
        state = db.get(IntegrationState, context.tenant_id)
        snapshot = {
            item.service_type: {"provider": item.provider, "version": item.version, "settings": item.settings}
            for item in configs
        }
        snapshot["self_test_id"] = state.last_self_test_id if preflight["resolved_mode"] == "channel" else None
        activity = Activity(
            tenant_id=context.tenant_id,
            activity_id=f"ACT-{count + 1:03d}",
            name=payload.name,
            package_id=payload.package_id,
            goal=payload.goal,
            status="running",
            mode=preflight["resolved_mode"],
            budget_yuan=payload.budget_yuan,
            case_ids=preflight["eligible_case_ids"],
            policy_version=preflight["policy_version"],
            service_snapshot=snapshot,
            preflight=preflight,
        )
        db.add(activity)
        audit(
            db,
            context,
            "activity.created",
            "activity",
            activity.activity_id,
            {"mode": activity.mode, "case_count": len(activity.case_ids)},
        )
        db.commit()
        db.refresh(activity)
        return ActivityOut.model_validate(activity)

    @app.get("/api/v1/activities", response_model=list[ActivityOut], tags=["activities"])
    def list_activities(context: Context, db: Database) -> list[ActivityOut]:
        rows = db.scalars(
            select(Activity).where(Activity.tenant_id == context.tenant_id).order_by(Activity.created_at.desc())
        )
        return [ActivityOut.model_validate(row) for row in rows]

    @app.post("/api/v1/activities/{activity_id}/transition", response_model=ActivityOut, tags=["activities"])
    def transition_activity(
        activity_id: str, payload: ActivityTransitionRequest, context: Context, db: Database
    ) -> ActivityOut:
        require_role(context, "operator", "admin")
        if not payload.acknowledged:
            raise HTTPException(status_code=422, detail="必须确认活动状态变更及其业务影响")
        activity = db.scalar(
            select(Activity).where(Activity.tenant_id == context.tenant_id, Activity.activity_id == activity_id)
        )
        if not activity:
            raise HTTPException(status_code=404, detail="活动不存在")
        if activity.status in {"blocked", "completed"}:
            raise HTTPException(status_code=409, detail="保护暂停或已完成活动不能直接变更状态")
        if payload.status == "running":
            blocked = db.scalar(
                select(func.count(CaseRecord.id)).where(
                    CaseRecord.tenant_id == context.tenant_id,
                    CaseRecord.case_id.in_(activity.case_ids),
                    CaseRecord.blocked.is_(True),
                )
            )
            if blocked:
                raise HTTPException(status_code=409, detail="活动包含保护案件，不能恢复运行")
        previous = activity.status
        activity.status = payload.status
        audit(
            db,
            context,
            "activity.transitioned",
            "activity",
            activity.activity_id,
            {"from": previous, "to": payload.status, "reason": payload.reason},
        )
        db.commit()
        db.refresh(activity)
        return ActivityOut.model_validate(activity)

    return app


app = create_app()

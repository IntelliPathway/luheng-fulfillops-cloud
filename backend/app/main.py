from __future__ import annotations

import os
from datetime import timedelta
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Base, build_engine, build_session_factory
from .agent_gateway import gateway_overview, gateway_profile
from .domain import (
    SERVICE_TYPES,
    activity_preflight,
    build_self_test_items,
    create_secret_reference,
    gate_for,
    invalidate_integration,
    service_versions,
    test_detail,
    utcnow,
    validate_service_settings,
)
from .jobs import TERMINAL_JOB_STATUSES, enqueue_job, execute_job
from .runtime_adapters import test_agent_runtime
from .models import (
    Activity,
    AgentMessage,
    AgentProposal,
    AgentRuntimeCheckpoint,
    AgentSession,
    AsyncJob,
    AuditEvent,
    CaseRecord,
    ConnectionTest,
    IntegrationState,
    SelfTestReport,
    ServiceConfig,
    User,
)
from .schemas import (
    ActivityOut,
    ActivityPreflightOut,
    ActivityRequest,
    AgentGatewayOut,
    AgentMessageRequest,
    AgentRuntimeOut,
    AgentSessionCreate,
    AgentSessionOut,
    AsyncTestRequest,
    AuthSessionOut,
    ConnectionTestOut,
    DevTokenOut,
    DevTokenRequest,
    EnableRequest,
    GateOut,
    HealthOut,
    IntegrationOverviewOut,
    IntegrationStateOut,
    JobOut,
    ProposalDecisionRequest,
    ProposalOut,
    SelfTestItem,
    SelfTestReportOut,
    ServiceConfigOut,
    ServiceConfigUpsert,
)
from .security import RequestContext, issue_dev_token, request_context, require_at_least, require_role
from .seed import seed_demo_data


Context = Annotated[RequestContext, Depends(request_context)]


def get_db(request: Request):
    with request.app.state.Session() as db:
        yield db


Database = Annotated[Session, Depends(get_db)]


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


def audit(db: Session, context: RequestContext, action: str, resource_type: str, resource_id: str, detail: dict) -> None:
    db.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
        )
    )


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="履衡 AI FulfillOps API", version="0.3.1", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:4177,http://localhost:5173").split(",")],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Tenant-ID", "X-Actor-ID"],
    )
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./luheng-dev.db")
    engine = build_engine(url)
    app.state.engine = engine
    app.state.Session = build_session_factory(engine)
    Base.metadata.create_all(engine)
    with app.state.Session() as db:
        seed_demo_data(db)

    @app.get("/api/v1/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        return HealthOut(status="ok", service="luheng-fulfillops-api", version="0.3.1")

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

    @app.get("/api/v1/integrations", response_model=IntegrationOverviewOut, tags=["integrations"])
    def get_integrations(context: Context, db: Database) -> IntegrationOverviewOut:
        return overview(db, context.tenant_id)

    @app.put("/api/v1/integrations/{service_type}", response_model=IntegrationOverviewOut, tags=["integrations"])
    def save_service(service_type: str, payload: ServiceConfigUpsert, context: Context, db: Database) -> IntegrationOverviewOut:
        require_role(context, "admin")
        validate_service_settings(service_type, payload.settings)
        config = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type)
        )
        if not config:
            config = ServiceConfig(tenant_id=context.tenant_id, service_type=service_type, provider=payload.provider, settings={})
            db.add(config)
            db.flush()
        if payload.credential:
            config.secret_ref, config.credential_last4 = create_secret_reference(context.tenant_id, service_type, payload.credential)
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
        audit(db, context, "integration.config.updated", "service_config", service_type, {"version": config.version, "provider": config.provider})
        db.commit()
        return overview(db, context.tenant_id)

    @app.post("/api/v1/integrations/{service_type}/connection-test", response_model=ConnectionTestOut, tags=["integrations"])
    def run_connection_test(service_type: str, context: Context, db: Database) -> ConnectionTestOut:
        require_role(context, "admin")
        if service_type not in SERVICE_TYPES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知服务类型")
        config = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type)
        )
        if not config:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先保存服务配置")
        validate_service_settings(service_type, config.settings)
        if not config.secret_ref:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="服务凭证尚未托管")
        latency, detail = test_agent_runtime(config) if service_type == "agent" else test_detail(service_type)
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
        audit(db, context, "integration.connection_test.passed", "service_config", service_type, {"version": config.version, "latency_ms": latency})
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
            select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id, ServiceConfig.service_type == service_type)
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
            background_tasks.add_task(execute_job, app.state.Session, job.id)
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
            {"service_versions": service_versions(list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == context.tenant_id))))},
            payload.idempotency_key,
        )
        if created:
            background_tasks.add_task(execute_job, app.state.Session, job.id)
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
        audit(db, context, "integration.enabled", "integration_state", context.tenant_id, {"service_versions": state.enabled_service_versions})
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
        job.status = "cancelled"
        job.completed_at = utcnow()
        audit(db, context, "job.cancelled", "async_job", job.id, {"kind": job.kind})
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
        job.started_at = None
        job.completed_at = None
        audit(db, context, "job.retried", "async_job", job.id, {"kind": job.kind, "next_attempt": job.attempt + 1})
        db.commit()
        background_tasks.add_task(execute_job, app.state.Session, job.id)
        return JobOut.model_validate(job)

    @app.get("/api/v1/agents/gateway", response_model=AgentGatewayOut, tags=["agents"])
    def get_agent_gateway(context: Context, db: Database) -> AgentGatewayOut:
        return AgentGatewayOut(**gateway_overview(db, context.tenant_id))

    @app.post("/api/v1/agents/sessions", response_model=AgentSessionOut, status_code=status.HTTP_201_CREATED, tags=["agents"])
    def create_agent_session(payload: AgentSessionCreate, context: Context, db: Database) -> AgentSessionOut:
        if payload.scope_type != "global" and not payload.scope_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="活动或案件会话必须指定 scope_id")
        if payload.scope_type == "activity":
            exists = db.scalar(
                select(Activity.id).where(Activity.tenant_id == context.tenant_id, Activity.activity_id == payload.scope_id)
            )
            if not exists:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
        if payload.scope_type == "case":
            exists = db.scalar(
                select(CaseRecord.id).where(CaseRecord.tenant_id == context.tenant_id, CaseRecord.case_id == payload.scope_id)
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
        audit(db, context, "agent.session.created", "agent_session", session.id, {"scope_type": session.scope_type, "scope_id": session.scope_id})
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
            {"id": row.id, "role": row.role, "content": row.content, "structured": row.structured, "created_at": row.created_at}
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
            background_tasks.add_task(execute_job, app.state.Session, job.id)
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
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="完成或保护阻断的活动不能用普通暂停操作修改")
            activity.status = "paused"
        elif proposal.action_type == "activity.resume":
            if activity.status != "paused":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有普通暂停活动可以恢复；保护阻断需走异常处理")
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
            {"action_type": proposal.action_type, "activity_id": activity.activity_id, "result_status": activity.status},
        )
        db.commit()
        return ProposalOut.model_validate(proposal)

    @app.post("/api/v1/activities/preflight", response_model=ActivityPreflightOut, tags=["activities"])
    def preflight_activity(payload: ActivityRequest, context: Context, db: Database) -> ActivityPreflightOut:
        require_role(context, "operator", "admin")
        return ActivityPreflightOut(**activity_preflight(db, context.tenant_id, payload))

    @app.post("/api/v1/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED, tags=["activities"])
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
        audit(db, context, "activity.created", "activity", activity.activity_id, {"mode": activity.mode, "case_count": len(activity.case_ids)})
        db.commit()
        db.refresh(activity)
        return ActivityOut.model_validate(activity)

    @app.get("/api/v1/activities", response_model=list[ActivityOut], tags=["activities"])
    def list_activities(context: Context, db: Database) -> list[ActivityOut]:
        rows = db.scalars(select(Activity).where(Activity.tenant_id == context.tenant_id).order_by(Activity.created_at.desc()))
        return [ActivityOut.model_validate(row) for row in rows]

    return app


app = create_app()

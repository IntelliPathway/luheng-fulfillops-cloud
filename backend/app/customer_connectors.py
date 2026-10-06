"""Authenticated, allowlisted incremental customer feed; only encrypted evidence intake."""

from __future__ import annotations

import base64
import csv
import hashlib
import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, update

from .audit import audit
from .customer_materials import FIELDS, MaterialCreate
from .customer_sync import SyncCreate, persist_event
from .dependencies import Context, Database
from .jobs import enqueue_job
from .models import AsyncJob, CustomerConnector, TenantMembership, User, utcnow
from .secret_store import SecretStoreError, resolve_secret, secret_store_backend, store_secret
from .security import RequestContext, require_role

router = APIRouter(prefix="/api/v1/customer-connectors", tags=["customer-connectors"])


class ConnectorConfig(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    endpoint: str = Field(min_length=8, max_length=500)
    mapping: dict[str, str] = Field(max_length=3)
    credential: SecretStr | None = None
    interval_minutes: int = Field(default=60, ge=5, le=1440)
    expected_version: int = Field(default=0, ge=0)
    acknowledged: bool


class ConnectorAction(BaseModel):
    expected_version: int = Field(ge=1)
    acknowledged: bool
    enabled: bool = True


def endpoint_parts(endpoint: str):
    try:
        p = urlsplit(endpoint)
        port = p.port
    except ValueError as exc:
        raise HTTPException(422, "连接地址格式无效") from exc
    hosts = {h.strip().lower() for h in os.getenv("CUSTOMER_CONNECTOR_EGRESS_ALLOWLIST", "").split(",") if h.strip()}
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or port not in {None, 443}
        or p.hostname.lower() not in hosts
        or re.search(r"[\x00-\x20\x7f]", endpoint)
    ):
        raise HTTPException(422, "连接地址必须是部署白名单内的 HTTPS 地址，不含凭证、查询参数或自定义端口")
    return p


def public_addresses(host: str) -> list[str]:
    try:
        addresses = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError
        return addresses
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "连接地址解析失败或指向非公网地址") from exc


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def fetch_page(endpoint: str, token: str, cursor: str) -> dict:
    p = endpoint_parts(endpoint)
    connection = PinnedHTTPS(p.hostname, public_addresses(p.hostname)[0])
    try:
        connection.request(
            "GET",
            (p.path or "/") + "?" + urlencode({"cursor": cursor, "limit": 20}),
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        r = connection.getresponse()
        if r.status != 200:
            raise ValueError
        raw = r.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError
        return json.loads(raw)
    except Exception as exc:
        raise HTTPException(503, "客户数据源响应不可用，请检查认证、协议或网络") from exc
    finally:
        connection.close()


def normalize_page(page, row):
    if not isinstance(page, dict) or not isinstance(page.get("events"), list) or len(page["events"]) > 20:
        raise HTTPException(422, "客户同步响应必须包含最多 20 个事件")
    cursor = page.get("next_cursor")
    if not isinstance(cursor, str) or len(cursor) > 500 or re.search(r"[\x00-\x1f\x7f]", cursor):
        raise HTTPException(422, "客户同步游标无效")
    if cursor == row.cursor and page["events"]:
        raise HTTPException(422, "非空同步页必须推进游标")
    events, ids = [], set()
    for e in page["events"]:
        if not isinstance(e, dict) or not isinstance(e.get("records"), list) or not 1 <= len(e["records"]) <= 50:
            raise HTTPException(422, "每个事件必须包含 1–50 条案件金额汇总")
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(sorted(FIELDS))
        for record in e["records"]:
            if not isinstance(record, dict) or any(row.mapping[f] not in record for f in FIELDS):
                raise HTTPException(422, "客户记录缺少映射字段")
            values = [record[row.mapping[f]] for f in sorted(FIELDS)]
            if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in values):
                raise HTTPException(422, "客户记录字段必须为字符串或整数")
            writer.writerow(values)
        try:
            event = SyncCreate(
                source_system=row.id,
                external_event_id=e.get("event_id"),
                material=MaterialCreate(
                    filename="connector.csv",
                    source_reference=e.get("source_reference"),
                    file_kind="csv",
                    content_base64=base64.b64encode(out.getvalue().encode()).decode(),
                    mapping={f: f for f in FIELDS},
                    acknowledged=True,
                ),
            )
        except ValueError as exc:
            raise HTTPException(422, "客户事件编号或来源格式无效") from exc
        # Validate amounts before accepting any event or advancing a cursor.
        from .customer_materials import parse_rows

        parse_rows(out.getvalue().encode(), event.material.mapping)
        if event.external_event_id in ids:
            raise HTTPException(422, "同步页内事件编号重复")
        ids.add(event.external_event_id)
        events.append(event)
    return events, cursor


def find_connector(db, tenant, ident):
    row = db.scalar(
        select(CustomerConnector)
        .where(CustomerConnector.tenant_id == tenant, CustomerConnector.id == ident)
        .with_for_update()
    )
    if not row:
        raise HTTPException(404, "连接器不存在")
    return row


def view(db, row):
    job = db.get(AsyncJob, row.active_job_id) if row.active_job_id else None
    return {
        f: getattr(row, f)
        for f in (
            "id",
            "tenant_id",
            "name",
            "endpoint",
            "mapping",
            "version",
            "enabled",
            "interval_minutes",
            "tested_at",
            "next_sync_at",
            "created_by",
            "enabled_by",
        )
    } | {
        "credential_configured": bool(row.secret_ref),
        "test_current": bool(row.tested_at and row.tested_at > utcnow() - timedelta(hours=24)),
        "job": {f: getattr(job, f) for f in ("id", "status", "attempt", "max_attempts", "error", "result")}
        if job and job.tenant_id == row.tenant_id
        else None,
        "cursor_digest": hashlib.sha256(row.cursor.encode()).hexdigest(),
        "enables_external_execution": False,
    }


@router.get("")
def list_connectors(context: Context, db: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return [
        view(db, r)
        for r in db.scalars(
            select(CustomerConnector)
            .where(CustomerConnector.tenant_id == context.tenant_id)
            .order_by(CustomerConnector.created_at.desc())
            .limit(50)
        )
    ]


def save_config(payload, context, db, ident=None):
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "请确认客户授权并完成脱敏")
    endpoint_parts(payload.endpoint)
    if (
        set(payload.mapping) != FIELDS
        or len(set(payload.mapping.values())) != 3
        or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", v) for v in payload.mapping.values())
    ):
        raise HTTPException(422, "请映射三个不同的案件、付款分和退款分字段")
    row = (
        find_connector(db, context.tenant_id, ident)
        if ident
        else CustomerConnector(tenant_id=context.tenant_id, created_by=context.actor_id)
    )
    if ident and row.version != payload.expected_version:
        raise HTTPException(409, "连接器已变化，请刷新")
    token = payload.credential.get_secret_value() if payload.credential else None
    if token:
        if len(token) > 4000 or not re.fullmatch(r"[\x21-\x7e]+", token):
            raise HTTPException(422, "凭证格式无效")
        if secret_store_backend() == "reference-only":
            raise HTTPException(503, "请配置可解析的加密凭证存储")
        try:
            row.secret_ref, _ = store_secret(
                db, context.tenant_id, "customer-connector", token, previous_reference=row.secret_ref
            )
        except SecretStoreError as exc:
            raise HTTPException(503, "连接器凭证加密不可用") from exc
    if not row.secret_ref:
        raise HTTPException(422, "首次配置需要客户 API 凭证")
    row.name = payload.name
    row.endpoint = payload.endpoint
    row.mapping = payload.mapping
    row.interval_minutes = payload.interval_minutes
    row.version = (row.version or 0) + 1
    row.enabled = False
    row.tested_at = None
    row.next_sync_at = None
    db.add(row)
    db.flush()
    audit(db, context, "customer_connector.configured", "customer_connector", row.id, {"version": row.version})
    db.commit()
    return view(db, row)


@router.post("", status_code=201)
def create_connector(payload: ConnectorConfig, context: Context, db: Database):
    return save_config(payload, context, db)


@router.put("/{ident}")
def update_connector(ident: str, payload: ConnectorConfig, context: Context, db: Database):
    return save_config(payload, context, db, ident)


def token_for(db, row):
    try:
        token = resolve_secret(db, row.secret_ref, row.tenant_id, "customer-connector")
        if not token:
            raise SecretStoreError("unresolved")
        return token
    except SecretStoreError as exc:
        raise HTTPException(503, "客户 API 凭证不可解析") from exc


@router.post("/{ident}/test")
def test_connector(ident: str, payload: ConnectorAction, context: Context, db: Database):
    require_role(context, "admin")
    row = find_connector(db, context.tenant_id, ident)
    if not payload.acknowledged or payload.expected_version != row.version:
        raise HTTPException(409, "请刷新并确认测试")
    # Clear the old test before a new request: a failed retest cannot retain readiness.
    row.tested_at = None
    row.enabled = False
    db.commit()
    version = row.version
    page = fetch_page(row.endpoint, token_for(db, row), row.cursor)
    events, _ = normalize_page(page, row)
    result = db.execute(
        update(CustomerConnector)
        .where(CustomerConnector.id == row.id, CustomerConnector.version == version)
        .values(tested_at=utcnow())
    )
    if result.rowcount != 1:
        raise HTTPException(409, "测试期间配置已变化")
    audit(
        db,
        context,
        "customer_connector.tested",
        "customer_connector",
        row.id,
        {"version": version, "event_count": len(events)},
    )
    db.commit()
    db.refresh(row)
    return view(db, row)


@router.post("/{ident}/enable")
def enable_connector(ident: str, payload: ConnectorAction, context: Context, db: Database):
    require_role(context, "admin")
    row = find_connector(db, context.tenant_id, ident)
    if not payload.acknowledged or payload.expected_version != row.version:
        raise HTTPException(409, "请刷新并确认启用状态")
    if payload.enabled and not view(db, row)["test_current"]:
        raise HTTPException(409, "请先完成当前配置连接测试")
    if payload.enabled and row.enabled_by != context.actor_id:
        # A new current administrator takes responsibility for the same checkpoint.
        # Old jobs fail their active-job binding rather than using a revoked grant.
        row.active_job_id = None
    row.enabled = payload.enabled
    row.enabled_by = context.actor_id if row.enabled else None
    row.next_sync_at = utcnow() if row.enabled else None
    audit(
        db,
        context,
        "customer_connector.enabled" if row.enabled else "customer_connector.disabled",
        "customer_connector",
        row.id,
        {"version": row.version},
    )
    db.commit()
    return view(db, row)


def submit(db, row, context):
    if not row.enabled or not view(db, row)["test_current"]:
        raise HTTPException(409, "连接器未启用或测试已过期")
    job = db.get(AsyncJob, row.active_job_id) if row.active_job_id else None
    if job and job.status not in {"succeeded", "failed", "cancelled"}:
        return job
    if job and job.status == "failed" and job.attempt < job.max_attempts and job.payload.get("version") == row.version:
        raise HTTPException(409, "请先重试失败的同步作业，避免跳过未完成页")
    job, _ = enqueue_job(
        db,
        context,
        "customer.connector_pull",
        {"connector_id": row.id, "version": row.version},
        "connector:" + uuid4().hex,
        commit=False,
    )
    row.active_job_id = job.id
    row.next_sync_at = utcnow() + timedelta(minutes=row.interval_minutes)
    db.commit()
    return job


@router.post("/{ident}/sync", status_code=202)
def sync_connector(ident: str, context: Context, db: Database):
    require_role(context, "admin")
    row = find_connector(db, context.tenant_id, ident)
    submit(db, row, context)
    return view(db, row)


def actor_context(db, tenant, actor):
    m = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant, TenantMembership.user_id == actor, TenantMembership.status == "active"
        )
    )
    u = db.get(User, actor)
    if not m or m.role != "admin" or not u or u.status != "active":
        raise HTTPException(403, "连接器管理员权限已失效")
    return RequestContext(tenant, actor, m.role, u.display_name, u.email, "worker")


def process_pull(db, job):
    row = find_connector(db, job.tenant_id, job.payload.get("connector_id"))
    version = job.payload.get("version")
    context = actor_context(db, job.tenant_id, job.created_by)
    if row.version != version or not row.enabled or not view(db, row)["test_current"] or row.active_job_id != job.id:
        raise HTTPException(409, "同步配置或授权已变化，请重新测试")
    cursor = row.cursor
    events, next_cursor = normalize_page(fetch_page(row.endpoint, token_for(db, row), cursor), row)
    for e in events:
        db.refresh(row)
        actor_context(db, job.tenant_id, job.created_by)
        if row.version != version or not row.enabled or row.active_job_id != job.id:
            raise HTTPException(409, "同步期间配置已变化")
        persist_event(e, context, db)
    # Intake is durable before checkpoint. Failed/partial pages replay exact event IDs.
    result = db.execute(
        update(CustomerConnector)
        .where(
            CustomerConnector.id == row.id,
            CustomerConnector.version == version,
            CustomerConnector.enabled.is_(True),
            CustomerConnector.cursor == cursor,
            CustomerConnector.active_job_id == job.id,
        )
        .values(cursor=next_cursor, next_sync_at=utcnow() + timedelta(minutes=row.interval_minutes))
    )
    if result.rowcount != 1:
        raise HTTPException(409, "同步游标或配置已变化")
    db.commit()
    return {
        "events_accepted": len(events),
        "checkpoint_digest": hashlib.sha256(next_cursor.encode()).hexdigest(),
        "materials_pending_processing": bool(events),
    }


def schedule_due(session_factory):
    with session_factory() as db:
        identifiers = db.scalars(
            select(CustomerConnector.id)
            .where(CustomerConnector.enabled.is_(True), CustomerConnector.next_sync_at <= utcnow())
            .order_by(CustomerConnector.next_sync_at)
            .limit(10)
        ).all()
        for ident in identifiers:
            try:
                row = db.scalar(
                    select(CustomerConnector).where(CustomerConnector.id == ident).with_for_update(skip_locked=True)
                )
                if row and row.enabled and row.next_sync_at and row.next_sync_at <= utcnow():
                    submit(db, row, actor_context(db, row.tenant_id, row.enabled_by))
            except HTTPException:
                db.rollback()
                # Preserve failed-page barriers but give later healthy connectors a turn.
                row = db.scalar(
                    select(CustomerConnector).where(CustomerConnector.id == ident).with_for_update(skip_locked=True)
                )
                if row and row.enabled:
                    row.next_sync_at = utcnow() + timedelta(minutes=5)
                    db.commit()

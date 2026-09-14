from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Activity,
    AssetPackage,
    BusinessMetricSnapshot,
    CaseRecord,
    IntegrationState,
    ServiceConfig,
    Tenant,
    TenantMembership,
    User,
)


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(Tenant.id).limit(1)):
        demo_users = {
            "Terry": ("terry@example.test", "Terry"),
            "test-user": ("admin@example.test", "测试管理员"),
            "test-operator": ("operator@example.test", "测试运营"),
            "test-viewer": ("viewer@example.test", "测试观察员"),
        }
        for user_id, (email, display_name) in demo_users.items():
            if not db.get(User, user_id):
                db.add(User(id=user_id, email=email, display_name=display_name))
        db.flush()
        for tenant_id in ("TENANT_A", "TENANT_B"):
            for user_id, role in (("Terry", "admin"), ("test-user", "admin"), ("test-operator", "operator"), ("test-viewer", "viewer")):
                exists = db.scalar(
                    select(TenantMembership.id).where(
                        TenantMembership.tenant_id == tenant_id,
                        TenantMembership.user_id == user_id,
                    )
                )
                if not exists:
                    db.add(TenantMembership(tenant_id=tenant_id, user_id=user_id, role=role))
        metric_values = {
            "TENANT_A": {"confirmed_net_recovery": 19920, "commission_eligible_recovery": 18420, "accrued_commission": 2778, "settled_commission": 0, "collected_commission": 0},
            "TENANT_B": {"confirmed_net_recovery": 800, "commission_eligible_recovery": 800, "accrued_commission": 160, "settled_commission": 0, "collected_commission": 0},
        }
        for tenant_id, values in metric_values.items():
            for key, value in values.items():
                exists = db.scalar(
                    select(BusinessMetricSnapshot.id).where(
                        BusinessMetricSnapshot.tenant_id == tenant_id,
                        BusinessMetricSnapshot.metric_key == key,
                    )
                )
                if not exists:
                    db.add(BusinessMetricSnapshot(tenant_id=tenant_id, metric_key=key, value=value, source="已确认回款与佣金账本（演示快照）"))
        upgrade_activities = [
            ("TENANT_A", "ACT-003", "已结清归档", "PKG_A", "履约归档", "completed", ["C001"]),
            ("TENANT_A", "ACT-004", "异议案件保护", "PKG_B", "异议保护", "blocked", ["C010"]),
            ("TENANT_A", "ACT-005", "委托到期管理", "PKG_B", "尾期回款核对", "blocked", ["C014"]),
            ("TENANT_B", "ACT-006", "确认回款归集", "PKG_C", "回款自动核对", "running", ["C021"]),
        ]
        for tenant_id, activity_id, name, package_id, goal, activity_status, case_ids in upgrade_activities:
            exists = db.scalar(select(Activity.id).where(Activity.tenant_id == tenant_id, Activity.activity_id == activity_id))
            if not exists:
                db.add(Activity(tenant_id=tenant_id, activity_id=activity_id, name=name, package_id=package_id, goal=goal, status=activity_status, mode="sandbox", budget_yuan=30, case_ids=case_ids, policy_version=1, service_snapshot={}, preflight={"seed": True}))
        db.commit()
        return

    db.add_all([Tenant(id="TENANT_A", name="租户 A"), Tenant(id="TENANT_B", name="租户 B")])
    db.add_all(
        [
            User(id="Terry", email="terry@example.test", display_name="Terry"),
            User(id="test-user", email="admin@example.test", display_name="测试管理员"),
            User(id="test-operator", email="operator@example.test", display_name="测试运营"),
            User(id="test-viewer", email="viewer@example.test", display_name="测试观察员"),
        ]
    )
    db.flush()
    for tenant_id in ("TENANT_A", "TENANT_B"):
        db.add_all(
            [
                TenantMembership(tenant_id=tenant_id, user_id="Terry", role="admin"),
                TenantMembership(tenant_id=tenant_id, user_id="test-user", role="admin"),
                TenantMembership(tenant_id=tenant_id, user_id="test-operator", role="operator"),
                TenantMembership(tenant_id=tenant_id, user_id="test-viewer", role="viewer"),
            ]
        )
    db.add_all(
        [
            AssetPackage(tenant_id="TENANT_A", package_id="PKG_A", title="长龄个贷一期", policy_status="published", policy_version=1, budget_limit_yuan=30),
            AssetPackage(tenant_id="TENANT_A", package_id="PKG_B", title="长龄个贷二期", policy_status="published", policy_version=1, budget_limit_yuan=30),
            AssetPackage(tenant_id="TENANT_B", package_id="PKG_C", title="消费个贷三期", policy_status="published", policy_version=1, budget_limit_yuan=30),
        ]
    )
    db.add_all(
        [
            CaseRecord(tenant_id="TENANT_A", case_id="C001", package_id="PKG_A", status="已结清", has_signed_plan=True),
            CaseRecord(tenant_id="TENANT_A", case_id="C002", package_id="PKG_A", status="履约中", has_signed_plan=True),
            CaseRecord(tenant_id="TENANT_A", case_id="C003", package_id="PKG_A", status="部分履约", has_signed_plan=True),
            CaseRecord(tenant_id="TENANT_A", case_id="C006", package_id="PKG_A", status="到账待匹配"),
            CaseRecord(tenant_id="TENANT_A", case_id="C008", package_id="PKG_A", status="待联系"),
            CaseRecord(tenant_id="TENANT_A", case_id="C010", package_id="PKG_B", status="异议暂停", blocked=True),
            CaseRecord(tenant_id="TENANT_A", case_id="C014", package_id="PKG_B", status="委托到期", blocked=True, has_signed_plan=True),
            CaseRecord(tenant_id="TENANT_B", case_id="C021", package_id="PKG_C", status="已确认回款"),
            CaseRecord(tenant_id="TENANT_B", case_id="C024", package_id="PKG_C", status="待联系"),
        ]
    )

    configs = [
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="agent",
            provider="Hermes Agent",
            settings={"endpoint": "http://agent-gateway.internal/v1", "profile": "fulfill-agent-v3", "approval": "高影响动作需确认"},
            secret_ref="kms://luheng/TENANT_A/agent/seed",
            credential_last4="DEMO",
            version=3,
            connected=True,
            latency_ms=126,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="model",
            provider="DeepSeek",
            settings={"endpoint": "https://api.deepseek.com/v1", "model": "deepseek-chat", "timeout": "30"},
            secret_ref="kms://luheng/TENANT_A/model/seed",
            credential_last4="DEMO",
            version=2,
            connected=True,
            latency_ms=816,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="voice",
            provider="阿里云智能语音",
            settings={"region": "华东 2（上海）", "asr": "Paraformer 实时版", "tts": "CosyVoice · 龙橙", "sampleRate": "16 kHz"},
            secret_ref="kms://luheng/TENANT_A/voice/seed",
            credential_last4="DEMO",
            version=1,
            connected=False,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="phone",
            provider="LiveKit SIP",
            settings={"sipHost": "sip.example.test:5061", "trunk": "amc-demo-trunk", "callerId": "010****8800", "callback": "https://example.test/telephony/events"},
            secret_ref="kms://luheng/TENANT_A/phone/seed",
            credential_last4="DEMO",
            version=1,
            connected=False,
        ),
    ]
    db.add_all(configs)
    db.add_all(
        [
            IntegrationState(tenant_id="TENANT_A", invalidated_reason="语音与电话服务尚未完成连接测试"),
            IntegrationState(tenant_id="TENANT_B", invalidated_reason="尚未完成服务配置"),
        ]
    )
    db.add_all(
        [
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-001",
                name="履约补款跟进",
                package_id="PKG_A",
                goal="已签协议履约",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C002"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-002",
                name="一次性方案履约",
                package_id="PKG_A",
                goal="已签协议履约",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C003"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-003",
                name="已结清归档",
                package_id="PKG_A",
                goal="履约归档",
                status="completed",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C001"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-004",
                name="异议案件保护",
                package_id="PKG_B",
                goal="异议保护",
                status="blocked",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C010"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-005",
                name="委托到期管理",
                package_id="PKG_B",
                goal="尾期回款核对",
                status="blocked",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C014"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_B",
                activity_id="ACT-006",
                name="确认回款归集",
                package_id="PKG_C",
                goal="回款自动核对",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C021"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
        ]
    )
    metric_values = {
        "TENANT_A": {
            "confirmed_net_recovery": 19920,
            "commission_eligible_recovery": 18420,
            "accrued_commission": 2778,
            "settled_commission": 0,
            "collected_commission": 0,
        },
        "TENANT_B": {
            "confirmed_net_recovery": 800,
            "commission_eligible_recovery": 800,
            "accrued_commission": 160,
            "settled_commission": 0,
            "collected_commission": 0,
        },
    }
    for tenant_id, values in metric_values.items():
        db.add_all(
            BusinessMetricSnapshot(
                tenant_id=tenant_id,
                metric_key=key,
                value=value,
                source="已确认回款与佣金账本（演示快照）",
            )
            for key, value in values.items()
        )
    db.commit()

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .version import PRODUCT_NAME

ServiceType = Literal["agent", "model", "voice", "phone"]
ActivityGoal = Literal["已签协议履约", "首次联络与意愿确认", "回款自动核对"]


class AssetPackageCatalogItemOut(BaseModel):
    package_id: str
    title: str
    policy_status: str
    policy_version: int
    budget_limit_yuan: float
    min_settlement_bps: int
    max_installments: int
    min_down_payment_bps: int
    source_import_batch_id: str | None
    created_at: datetime
    case_count: int
    executable_count: int
    protected_count: int
    data_completeness_score: int
    total_claim_balance_cents: int
    confirmed_net_recovery_cents: int
    accrued_commission_cents: int
    mandate_start: date | None
    mandate_end: date | None
    commission_rate_bps: int | None
    data_source: Literal["server-authoritative"]


class AssetPackagePageOut(BaseModel):
    items: list[AssetPackageCatalogItemOut]
    total: int
    page: int
    page_size: int
    pages: int


class CaseCatalogItemOut(BaseModel):
    case_id: str
    package_id: str
    package_title: str | None
    status: str
    blocked: bool
    has_signed_plan: bool
    data_completeness_score: int
    claim_balance_cents: int | None
    principal_cents: int | None
    interest_cents: int | None
    fee_cents: int | None
    first_overdue_date: date | None
    last_contact_at: datetime | None
    mandate_start: date | None
    mandate_end: date | None
    commission_rule_id: str | None
    commission_rate_bps: int | None
    contact_basis_ref: str | None
    source_import_batch_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    confirmed_net_recovery_cents: int
    accrued_commission_cents: int
    active_protection_id: str | None
    protection_category: str | None
    protection_reason: str | None
    active_plan_id: str | None
    active_plan_status: str | None
    next_allowed: str
    data_source: Literal["server-authoritative"]


class CaseCatalogFacetsOut(BaseModel):
    all: int
    signed: int
    blocked: int
    quality: int


class CaseCatalogPageOut(BaseModel):
    items: list[CaseCatalogItemOut]
    total: int
    page: int
    page_size: int
    pages: int
    facets: CaseCatalogFacetsOut
    next_cursor: str | None = None


class AssetImportPreviewRequest(BaseModel):
    filename: str = Field(min_length=5, max_length=160, pattern=r"^[^/\\]+\.[cC][sS][vV]$")
    csv_text: str = Field(min_length=1, max_length=1_000_000)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")


class AssetImportCommitRequest(BaseModel):
    expected_version: int = Field(ge=1)
    review_note: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class AssetImportIssueOut(BaseModel):
    row_number: int | None
    severity: Literal["error", "warning"]
    code: str
    field: str | None
    message: str


class AssetImportBatchOut(BaseModel):
    id: str
    source_filename: str
    source_digest: str
    schema_version: str
    status: Literal["ready", "blocked", "committed"]
    version: int
    row_count: int
    valid_count: int
    invalid_count: int
    duplicate_count: int
    package_count: int
    total_claim_balance_cents: int
    issues: list[AssetImportIssueOut]
    created_by: str
    created_at: datetime
    committed_by: str | None
    committed_at: datetime | None
    review_note: str | None
    idempotent_replay: bool = False


class PolicyProposalCreateRequest(BaseModel):
    expected_policy_version: int = Field(ge=1)
    budget_limit_yuan: float = Field(gt=0, le=100000)
    min_settlement_bps: int = Field(ge=1000, le=10000)
    max_installments: int = Field(ge=1, le=60)
    min_down_payment_bps: int = Field(ge=0, le=10000)
    proposal_reason: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class PolicyProposalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    review_note: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class PolicyProposalOut(BaseModel):
    id: str
    package_id: str
    status: Literal["pending_review", "approved", "rejected"]
    version: int
    expected_policy_version: int
    proposed_policy: dict[str, Any]
    evaluation: dict[str, Any]
    evidence_digest: str
    proposed_by: str
    proposal_reason: str
    reviewed_by: str | None
    review_note: str | None
    created_at: datetime
    reviewed_at: datetime | None


class MembershipProposalCreateRequest(BaseModel):
    target_user_id: str = Field(min_length=2, max_length=80)
    requested_role: Literal["viewer", "operator", "admin"]
    requested_status: Literal["active", "inactive"] = "active"
    proposal_reason: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class MembershipProposalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    review_note: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class MembershipProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    target_user_id: str
    requested_role: str
    requested_status: str
    expected_role: str | None
    expected_status: str | None
    status: str
    version: int
    proposed_by: str
    proposal_reason: str
    reviewed_by: str | None
    review_note: str | None
    created_at: datetime
    reviewed_at: datetime | None


class TenantMemberOut(BaseModel):
    user_id: str
    display_name: str
    email: str
    role: str
    status: str


class TelephonyWebhookPayload(BaseModel):
    event_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    call_reference: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: Literal["initiated", "ringing", "answered", "completed", "failed"]
    occurred_at: datetime
    case_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9._:-]+$")
    activity_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9._:-]+$")
    failure_code: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")


class ContactAttemptCreateRequest(BaseModel):
    case_id: str = Field(min_length=4, max_length=40, pattern=r"^[A-Za-z0-9._:-]+$")
    activity_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9._:-]+$")
    contact_reference: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    scheduled_at: datetime
    acknowledged: bool


class ContactHandoffRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class ContactAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    activity_id: str | None
    channel: str
    contact_reference: str
    status: str
    scheduled_at: datetime
    requested_by: str
    handoff_reason: str | None
    handoff_requested_by: str | None
    handoff_requested_at: datetime | None
    last_event_at: datetime | None
    created_at: datetime


class TelephonyEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    provider_event_id: str
    call_reference: str
    event_type: str
    status: str
    occurred_at: datetime
    case_id: str | None
    activity_id: str | None
    failure_code: str | None
    payload_digest: str
    duplicate_count: int
    received_at: datetime


class TelephonyEventAcceptanceOut(BaseModel):
    event: TelephonyEventOut
    duplicate: bool


class ServiceConfigUpsert(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    settings: dict[str, Any]
    credential: str | None = Field(default=None, min_length=4, max_length=4096)


class ServiceConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_type: str
    provider: str
    settings: dict[str, Any]
    credential_mask: str | None
    version: int
    saved: bool
    connected: bool
    latency_ms: int | None
    connection_tested_at: datetime | None
    updated_at: datetime


class ConnectionTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    service_type: str
    config_version: int
    status: str
    latency_ms: int | None
    detail: str
    created_at: datetime


class SelfTestItem(BaseModel):
    id: str
    title: str
    status: str
    detail: str


class SelfTestReportOut(BaseModel):
    id: str
    status: str
    service_versions: dict[str, int]
    items: list[SelfTestItem]
    created_at: datetime
    expires_at: datetime


class GateOut(BaseModel):
    all_saved: bool
    all_connected: bool
    report_current: bool
    report_fresh: bool
    enabled_snapshot_current: bool
    ready: bool


class IntegrationStateOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    enabled_by: str | None
    enabled_service_versions: dict[str, int] | None
    invalidated_reason: str | None


class IntegrationOverviewOut(BaseModel):
    tenant_id: str
    services: dict[str, ServiceConfigOut]
    state: IntegrationStateOut
    gate: GateOut
    last_report: SelfTestReportOut | None


class EnableRequest(BaseModel):
    acknowledged: bool


class ActivityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    package_id: str = Field(min_length=1, max_length=40)
    goal: ActivityGoal
    budget_yuan: float = Field(gt=0, le=100000)
    case_ids: list[str] = Field(min_length=1, max_length=1000)
    requested_mode: Literal["auto", "sandbox", "channel"] = "auto"


class ActivityTransitionRequest(BaseModel):
    status: Literal["running", "paused"]
    reason: str = Field(min_length=4, max_length=240)
    acknowledged: bool


class ExcludedCase(BaseModel):
    case_id: str
    reason: str


class ActivityPreflightOut(BaseModel):
    tenant_id: str
    package_id: str
    eligible_case_ids: list[str]
    excluded: list[ExcludedCase]
    policy_status: str
    policy_version: int
    budget_limit_yuan: float
    channel_ready: bool
    production_ready: bool
    resolved_mode: Literal["sandbox", "channel"]
    blockers: list[str]


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activity_id: str
    name: str
    package_id: str
    goal: str
    status: str
    mode: str
    budget_yuan: float
    case_ids: list[str]
    policy_version: int
    service_snapshot: dict[str, Any]
    preflight: dict[str, Any]
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    service: str
    version: str


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, Any]
    created_at: datetime


class AuthSessionOut(BaseModel):
    actor_id: str
    display_name: str
    email: str
    tenant_id: str
    role: str
    auth_mode: str


class AuthHealthOut(BaseModel):
    mode: Literal["oidc", "shared-secret", "development", "unconfigured"]
    status: Literal["ready", "development", "degraded"]
    issuer_configured: bool
    audience_configured: bool
    jwks_configured: bool
    allowed_algorithms: list[str]
    required_claims: list[str]
    tenant_claim: str | None
    leeway_seconds: int
    jwks_cache_seconds: int
    dev_header_enabled: bool
    dev_token_enabled: bool
    detail: str


class DevTokenRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=80)


class DevTokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 3600


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: JobStatus
    queue_name: str
    priority: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    attempt: int
    max_attempts: int
    idempotency_key: str | None
    created_by: str
    created_at: datetime
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    recovery_count: int
    cancel_requested_at: datetime | None


class QueueHealthOut(BaseModel):
    mode: Literal["inline", "external"]
    status: Literal["inline", "healthy", "degraded"]
    active_workers: int
    queued_jobs: int
    running_jobs: int
    stale_jobs: int
    latest_heartbeat_at: datetime | None
    lease_seconds: int
    broker_backend: Literal["database", "postgres-notify"]
    broker_status: Literal["polling", "ready", "degraded"]


class SecretStoreHealthOut(BaseModel):
    backend: Literal["reference-only", "local-envelope", "aws-secrets-manager"]
    status: Literal["reference-only", "ready", "degraded"]
    persistent: bool
    resolvable: bool
    key_version: str | None
    active_secrets: int
    rotation_pending: int
    detail: str


class AsyncTestRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=120)


class ModelReplayRequest(BaseModel):
    suite_name: Literal["fulfillops-safe-core"] = "fulfillops-safe-core"
    mode: Literal["deterministic-contract", "live-provider"] = "deterministic-contract"
    acknowledged_external_call: bool = False
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=120)


class ModelReplayRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    suite_name: str
    suite_version: str
    mode: str
    provider: str
    profile: str
    model_config_version: int | None
    model_name: str | None
    status: str
    dataset_digest: str
    policy_snapshot: dict[str, Any]
    results: list[dict[str, Any]]
    passed_count: int
    failed_count: int
    external_call_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    created_by: str
    created_at: datetime
    completed_at: datetime | None


class ModelGatewayHealthOut(BaseModel):
    status: Literal["unconfigured", "contract", "ready", "degraded"]
    configured: bool
    provider: str | None
    model: str | None
    config_version: int | None
    execution_mode: Literal["contract-only", "live-provider"]
    endpoint_host: str | None
    egress_allowed: bool
    credential_configured: bool
    live_calls_enabled: bool
    connected: bool
    max_output_tokens: int
    max_cost_usd: float
    detail: str


class AgentGatewayOut(BaseModel):
    mode: str
    provider: str
    profile: str
    connected: bool
    transport: str
    session_persistence: str
    supported_runtimes: list[str]
    tools: list[dict[str, Any]]
    forbidden_tools: list[str]
    approval_policy: str
    safety_boundary: str


class AgentRuntimeOut(BaseModel):
    session_id: str
    provider: str
    profile: str
    provider_session_id: str | None
    event_cursor: int
    turn_count: int
    last_run_id: str | None
    runtime_metadata: dict[str, Any]
    updated_at: datetime | None


class AgentToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentSessionCreate(BaseModel):
    scope_type: Literal["global", "activity", "case"] = "global"
    scope_id: str | None = Field(default=None, max_length=80)
    title: str = Field(default=f"{PRODUCT_NAME} 会话", min_length=1, max_length=160)


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope_type: str
    scope_id: str | None
    runtime_provider: str
    runtime_profile: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=120)


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_type: str
    arguments: dict[str, Any]
    status: str
    required_role: str
    expires_at: datetime
    decided_at: datetime | None
    decided_by: str | None


class ProposalDecisionRequest(BaseModel):
    acknowledged: bool


class PaymentWebhookConfigRequest(BaseModel):
    credential: str | None = Field(default=None, min_length=16, max_length=4096)
    max_amount_cents: int = Field(default=100_000_000, ge=1, le=1_000_000_000)


class PaymentWebhookConfigOut(BaseModel):
    provider: str
    credential_mask: str
    version: int
    active: bool
    max_amount_cents: int
    updated_at: datetime


class PaymentWebhookPayload(BaseModel):
    event_id: str = Field(min_length=4, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: Literal["payment", "refund"]
    amount_cents: int = Field(gt=0, le=1_000_000_000)
    currency: Literal["CNY"] = "CNY"
    occurred_at: datetime
    case_id: str | None = Field(default=None, pattern=r"^C[0-9]{3,12}$")
    original_event_id: str | None = Field(default=None, min_length=4, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")

    @model_validator(mode="after")
    def validate_refund_reference(self) -> PaymentWebhookPayload:
        if self.event_type == "refund" and not self.original_event_id:
            raise ValueError("退款回执必须提供 original_event_id")
        if self.event_type == "payment" and self.original_event_id:
            raise ValueError("支付回执不能提供 original_event_id")
        return self


class PaymentReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    provider_event_id: str
    event_type: str
    amount_cents: int
    currency: str
    occurred_at: datetime
    case_id: str | None
    original_provider_event_id: str | None
    payload_digest: str
    signature_digest: str
    signature_version: str
    signature_verified: bool
    status: str
    failure_code: str | None
    recovery_entry_id: str | None
    duplicate_count: int
    received_at: datetime
    updated_at: datetime


class PaymentReceiptAcceptanceOut(BaseModel):
    receipt: PaymentReceiptOut
    duplicate: bool


class PaymentReceiptMatchRequest(BaseModel):
    case_id: str = Field(pattern=r"^C[0-9]{3,12}$")
    acknowledged: bool


class PaymentMatchCandidateOut(BaseModel):
    case_id: str
    package_id: str
    score: int
    signals: list[str]
    blocked: bool
    has_signed_plan: bool


class PaymentReconciliationCreateRequest(BaseModel):
    case_id: str = Field(pattern=r"^C[0-9]{3,12}$")
    reason: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class PaymentReconciliationDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    review_note: str = Field(min_length=4, max_length=500)
    expected_version: int = Field(ge=1)
    acknowledged: bool


class PaymentReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    receipt_id: str
    proposed_case_id: str
    candidate_snapshot: list[dict[str, Any]]
    evidence_digest: str
    reason: str
    status: str
    version: int
    proposed_by: str
    proposed_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    recovery_entry_id: str | None


ProtectionCategory = Literal[
    "debt_dispute",
    "stop_contact",
    "identity_conflict",
    "mandate_expired",
    "data_quality",
    "amount_verification",
    "authorization_gap",
    "contact_data",
    "budget_exhausted",
    "channel_failure",
]
EvidenceReference = Annotated[str, Field(min_length=4, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")]


class ProtectionIncidentCreateRequest(BaseModel):
    source_event_id: str = Field(min_length=4, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    category: ProtectionCategory
    reason: str = Field(min_length=8, max_length=500)
    owner: str = Field(min_length=2, max_length=120)
    sla_hours: int | None = Field(default=None, ge=1, le=720)
    acknowledged: bool


class ProtectionResolutionProposalRequest(BaseModel):
    resolution_note: str = Field(min_length=8, max_length=500)
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=10)
    acknowledged: bool


class ProtectionResolutionDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    review_note: str = Field(min_length=4, max_length=500)
    expected_version: int = Field(ge=1)
    acknowledged: bool


class ProtectionIncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    source_event_id: str
    category: str
    priority: str
    reason: str
    opening_digest: str
    owner: str
    release_policy: str
    status: str
    version: int
    previous_case_status: str
    sla_due_at: datetime | None
    opened_by: str
    opened_at: datetime
    resolution_note: str | None
    evidence_refs: list[str]
    evidence_digest: str | None
    proposed_by: str | None
    proposed_at: datetime | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    resolved_at: datetime | None
    case_released: bool


class ProtectionOverviewOut(BaseModel):
    active_count: int
    p0_count: int
    pending_review_count: int
    overdue_count: int
    permanent_hold_count: int
    incidents: list[ProtectionIncidentOut]


class RepaymentInstallmentRequest(BaseModel):
    installment_no: int = Field(ge=1, le=24)
    due_date: date
    due_cents: int = Field(gt=0, le=1_000_000_000)


class RepaymentPlanCreateRequest(BaseModel):
    plan_id: str = Field(min_length=4, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    total_cents: int = Field(gt=0, le=1_000_000_000)
    down_payment_cents: int = Field(gt=0, le=1_000_000_000)
    installments: list[RepaymentInstallmentRequest] = Field(min_length=1, max_length=24)
    agreement_reference: EvidenceReference
    agreement_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    signed_at: datetime
    proposal_reason: str = Field(min_length=8, max_length=500)
    acknowledged: bool


class RepaymentPlanDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    review_note: str = Field(min_length=4, max_length=500)
    expected_version: int = Field(ge=1)
    acknowledged: bool


class RepaymentInstallmentOut(BaseModel):
    installment_id: str
    installment_no: int
    due_date: date
    due_cents: int
    paid_cents: int
    remaining_cents: int
    status: str
    last_payment_at: datetime | None


class RepaymentPlanOut(BaseModel):
    id: str
    plan_id: str
    case_id: str
    status: str
    version: int
    currency: str
    claim_balance_cents: int
    total_cents: int
    down_payment_cents: int
    installment_count: int
    policy_version: int
    policy_snapshot: dict[str, Any]
    agreement_reference: str
    agreement_digest: str
    signed_at: datetime
    evidence_digest: str
    proposal_reason: str
    proposed_by: str
    proposed_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    activated_at: datetime | None
    completed_at: datetime | None
    paid_cents: int
    remaining_cents: int
    overdue_cents: int
    installments: list[RepaymentInstallmentOut]


class RepaymentOverviewOut(BaseModel):
    active_count: int
    pending_review_count: int
    completed_count: int
    overdue_plan_count: int
    due_soon_count: int
    plans: list[RepaymentPlanOut]


class PaymentSandboxReceiptRequest(BaseModel):
    case_id: str = Field(default="C002", pattern=r"^C[0-9]{3,12}$")
    amount_cents: int = Field(default=101_600, gt=0, le=100_000_000)
    idempotency_key: str = Field(min_length=4, max_length=90, pattern=r"^[A-Za-z0-9._:-]+$")


class RecoveryLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entry_id: str
    receipt_id: str | None
    case_id: str
    package_id: str
    event_type: str
    amount_cents: int
    eligible_amount_cents: int
    commission_rule_id: str
    commission_rule_version: int
    rate_bps: int
    commission_cents: int
    reason: str
    original_entry_id: str | None
    allocation: str
    source: str
    booked_at: datetime
    created_at: datetime


class CommissionLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: str
    amount_cents: int
    source_recovery_entry_id: str | None
    reference: str
    idempotency_key: str
    created_by: str
    occurred_at: datetime
    created_at: datetime


class FinancialSummaryOut(BaseModel):
    confirmed_net_recovery_cents: int
    commission_eligible_recovery_cents: int
    accrued_commission_cents: int
    settled_commission_cents: int
    collected_commission_cents: int
    unsettled_commission_cents: int
    uncollected_settlement_cents: int
    pending_receipt_count: int


class FinancialOverviewOut(BaseModel):
    summary: FinancialSummaryOut
    recovery_ledger: list[RecoveryLedgerEntryOut]
    commission_ledger: list[CommissionLedgerEntryOut]
    pending_receipts: list[PaymentReceiptOut]
    reconciliations: list[PaymentReconciliationOut] = Field(default_factory=list)
    webhook_ready: bool
    webhook_provider: str | None
    sandbox_enabled: bool


class CommissionEventRequest(BaseModel):
    event_type: Literal["settlement", "collection"]
    amount_cents: int = Field(gt=0, le=1_000_000_000)
    reference: str = Field(min_length=4, max_length=160)
    idempotency_key: str = Field(min_length=4, max_length=120)
    occurred_at: datetime
    acknowledged: bool


class CommissionEventAcceptanceOut(BaseModel):
    entry: CommissionLedgerEntryOut
    duplicate: bool

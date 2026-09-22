import {PRODUCT_NAME} from './brand.js';
import {ensureFreshAccessToken} from './oidc-client.js';

const API_BASE = import.meta.env?.VITE_API_BASE_URL || '/api/v1';

export class ApiError extends Error {
  constructor(message, status, details = {}) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

const devHeaderAuthEnabled = () => (
  import.meta.env?.DEV === true || import.meta.env?.VITE_ENABLE_DEV_AUTH === 'true'
);

const headers = async tenant => {
  const token = await ensureFreshAccessToken();
  return {
    'Content-Type': 'application/json',
    'X-Tenant-ID': tenant,
    ...(token ? {Authorization: `Bearer ${token}`} : devHeaderAuthEnabled() ? {'X-Actor-ID': 'Terry'} : {}),
  };
};

async function request(path, tenant, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {...await headers(tenant), ...(options.headers || {})},
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    let body = {};
    try {
      body = await response.json();
      message = body.detail || message;
    } catch {}
    throw new ApiError(message, response.status, body);
  }
  return response.json();
}

export function classifyApiFailure(error) {
  if (error instanceof ApiError && error.details?.mode === 'sites-demo') return 'offline';
  if (error instanceof ApiError && [401, 403].includes(error.status)) return 'auth-required';
  if (error instanceof ApiError) return 'api-error';
  return 'offline';
}

const jobKey = prefix => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

const queryString = values => {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  const query = params.toString();
  return query ? `?${query}` : '';
};

export async function waitForJob(tenant, initial, onUpdate, options = {}) {
  const interval = options.interval || 160;
  const maxPolls = options.maxPolls || 80;
  let job = initial;
  onUpdate?.(job);
  for (let index = 0; index < maxPolls; index += 1) {
    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) break;
    await new Promise(resolve => setTimeout(resolve, interval));
    job = await request(`/jobs/${job.id}`, tenant);
    onUpdate?.(job);
  }
  if (job.status === 'failed') throw new ApiError(job.error || '异步作业执行失败', 500);
  if (job.status === 'cancelled') throw new ApiError('异步作业已取消', 409);
  if (job.status !== 'succeeded') throw new ApiError('异步作业等待超时，可稍后从运行记录恢复', 504);
  return job;
}

const secretField = {agent: 'authToken', model: 'apiKey', voice: 'accessKey', phone: 'secret'};
const settingKeys = {
  agent: ['endpoint', 'profile', 'approval', 'transport', 'safetyPreset', 'sessionPersistence'],
  model: ['endpoint', 'model', 'timeout', 'executionMode', 'maxOutputTokens', 'maxCostUsd'],
  voice: ['region', 'asr', 'tts', 'sampleRate'],
  phone: ['sipHost', 'trunk', 'callerId', 'callback'],
};

export function normalizeIntegrationOverview(payload) {
  const services = Object.fromEntries(
    Object.entries(payload.services || {}).map(([type, config]) => [
      type,
      {
        provider: config.provider,
        ...config.settings,
        [secretField[type]]: config.credential_mask || '',
        credentialConfigured: Boolean(config.credential_mask),
        saved: config.saved,
        tested: config.connected,
        latency: config.latency_ms == null ? '—' : `${config.latency_ms} ms`,
        version: config.version,
        updatedAt: config.updated_at ? new Date(config.updated_at).toLocaleString('zh-CN', {hour12: false}) : '—',
        testedAt: config.connection_tested_at,
      },
    ]),
  );
  const report = payload.last_report;
  return {
    services,
    state: {
      enabled: payload.state.enabled,
      enabledAt: payload.state.enabled_at,
      enabledBy: payload.state.enabled_by,
      enabledServiceVersions: payload.state.enabled_service_versions,
      invalidatedReason: payload.state.invalidated_reason,
      lastRun: report
        ? {
            id: report.id,
            time: new Date(report.created_at).toLocaleString('zh-CN', {hour12: false}),
            expiresAt: report.expires_at,
            passed: report.status === 'passed',
            items: report.items,
            serviceVersions: report.service_versions,
          }
        : null,
    },
    gate: payload.gate,
  };
}

export function servicePayload(type, config) {
  const settings = Object.fromEntries(settingKeys[type].map(key => [key, config[key]]));
  const credentialValue = config[secretField[type]];
  const credential = credentialValue && !String(credentialValue).includes('•') ? credentialValue : undefined;
  return {provider: config.provider, settings, ...(credential ? {credential} : {})};
}

export const integrationApi = {
  get: tenant => request('/integrations', tenant),
  save: (tenant, type, config) => request(`/integrations/${type}`, tenant, {method: 'PUT', body: JSON.stringify(servicePayload(type, config))}),
  test: async (tenant, type, onUpdate) => {
    const queued = await request(`/integrations/${type}/connection-test/jobs`, tenant, {
      method: 'POST', body: JSON.stringify({idempotency_key: jobKey(`connection-${tenant}-${type}`)}),
    });
    return waitForJob(tenant, queued, onUpdate);
  },
  selfTest: async (tenant, onUpdate) => {
    const queued = await request('/integrations/self-test/jobs', tenant, {
      method: 'POST', body: JSON.stringify({idempotency_key: jobKey(`self-test-${tenant}`)}),
    });
    return waitForJob(tenant, queued, onUpdate);
  },
  enable: tenant => request('/integrations/enable', tenant, {method: 'POST', body: JSON.stringify({acknowledged: true})}),
};

export const authApi = {
  session: tenant => request('/auth/session', tenant),
};

export const jobApi = {
  queueHealth: tenant => request('/jobs/queue/health', tenant),
};

export const securityApi = {
  secretHealth: tenant => request('/security/secrets/health', tenant),
  authHealth: tenant => request('/security/auth/health', tenant),
  modelGatewayHealth: tenant => request('/models/gateway/health', tenant),
};

export const operationsApi = {
  pilotScorecard: tenant => request('/pilot/scorecard', tenant),
  releaseGate: tenant => request('/pilot/release-gate', tenant),
  metrics: tenant => request('/observability/metrics', tenant),
  providerScorecard: tenant => request('/provider-operations/scorecard', tenant),
  dailyClose: tenant => request('/provider-operations/daily-close', tenant),
  workQueue: (tenant,limit=100) => request(`/operations/work-queue${queryString({limit})}`,tenant),
  auditEvents: (tenant,{query='',actionPrefix='',limit=100}={}) => {
    const params=new URLSearchParams({limit:String(limit)});
    if(query)params.set('query',query);
    if(actionPrefix)params.set('action_prefix',actionPrefix);
    return request(`/governance/audit-events?${params}`,tenant);
  },
};

export const knowledgeApi = {
  list: (tenant,status) => request(`/knowledge/documents${queryString({status})}`,tenant),
  create: (tenant,payload) => request('/knowledge/documents',tenant,{method:'POST',body:JSON.stringify({...payload,acknowledged:true})}),
  decide: (tenant,id,payload) => request(`/knowledge/documents/${id}/decision`,tenant,{method:'POST',body:JSON.stringify({...payload,acknowledged:true})}),
};

export const membershipApi = {
  members: tenant => request('/governance/members', tenant),
  proposals: tenant => request('/governance/membership-proposals', tenant),
  propose: (tenant, payload) => request('/governance/membership-proposals', tenant, {
    method: 'POST', body: JSON.stringify({...payload, acknowledged: true}),
  }),
  decide: (tenant, proposalId, payload) => request(`/governance/membership-proposals/${proposalId}/decision`, tenant, {
    method: 'POST', body: JSON.stringify({...payload, acknowledged: true}),
  }),
};

export const contactApi = {
  channels: tenant => request('/contact-attempts/channels',tenant),
  list: (tenant, limit = 100) => request(`/contact-attempts${queryString({limit})}`, tenant),
  create: (tenant, payload) => request('/contact-attempts', tenant, {
    method: 'POST', body: JSON.stringify({...payload, acknowledged: true}),
  }),
  handoff: (tenant, attemptId, reason) => request(`/contact-attempts/${attemptId}/handoff`, tenant, {
    method: 'POST', body: JSON.stringify({reason, acknowledged: true}),
  }),
  cancel: (tenant, attemptId, reason) => request(`/contact-attempts/${attemptId}/cancel`, tenant, {
    method: 'POST', body: JSON.stringify({reason, acknowledged: true}),
  }),
  retry: (tenant, attemptId, scheduledAt, reason) => request(`/contact-attempts/${attemptId}/retry`, tenant, {
    method: 'POST', body: JSON.stringify({scheduled_at: scheduledAt, reason, acknowledged: true}),
  }),
};

export const telephonyApi = {
  events: (tenant, limit=100) => request(`/telephony/events${queryString({limit})}`, tenant),
};

export const harnessApi = {
  catalog: tenant => request('/harnesses/catalog',tenant),
  leaderboard: tenant => request('/harnesses/leaderboard',tenant),
};

export const strategyExperimentApi = {
  list: tenant => request('/strategy-experiments', tenant),
  results: (tenant, id) => request(`/strategy-experiments/${id}/results`, tenant),
  create: (tenant, payload) => request('/strategy-experiments', tenant, {
    method: 'POST', body: JSON.stringify({...payload, acknowledged: true}),
  }),
  transition: (tenant, id, action, expectedVersion) => request(`/strategy-experiments/${id}/transition`, tenant, {
    method: 'POST', body: JSON.stringify({action, expected_version: expectedVersion, acknowledged: true}),
  }),
};

export const platformApi = {
  subscription: tenant => request('/platform/subscription', tenant),
  usage: tenant => request('/platform/usage', tenant),
  capabilities: tenant => request('/platform/capabilities', tenant),
  updateSubscription: (tenant, planCode, expectedVersion) => request('/platform/subscription', tenant, {
    method: 'PUT', body: JSON.stringify({plan_code: planCode, expected_version: expectedVersion, acknowledged: true}),
  }),
};

export const agentApi = {
  gateway: tenant => request('/agents/gateway', tenant),
  replays: tenant => request('/agents/replays', tenant),
  replayRun: (tenant, replayId) => request(`/agents/replays/${replayId}`, tenant),
  replay: async (tenant, mode = 'deterministic-contract', onUpdate) => {
    const queued = await request('/agents/replays/jobs', tenant, {
      method: 'POST',
      body: JSON.stringify({
        suite_name: 'fulfillops-safe-core',
        mode,
        acknowledged_external_call: mode === 'live-provider',
        idempotency_key: jobKey(`model-replay-${tenant}-${mode}`),
      }),
    });
    return waitForJob(tenant, queued, onUpdate);
  },
  createSession: (tenant, scope = {}) => request('/agents/sessions', tenant, {
    method: 'POST',
    body: JSON.stringify({
      scope_type: scope.scopeType || 'global',
      scope_id: scope.scopeId || null,
      title: scope.title || `${PRODUCT_NAME} 会话`,
    }),
  }),
  message: async (tenant, sessionId, content, onUpdate) => {
    const queued = await request(`/agents/sessions/${sessionId}/messages`, tenant, {
      method: 'POST',
      body: JSON.stringify({content, idempotency_key: jobKey(`agent-turn-${tenant}`)}),
    });
    return waitForJob(tenant, queued, onUpdate);
  },
  runtime: (tenant, sessionId) => request(`/agents/sessions/${sessionId}/runtime`, tenant),
  confirmProposal: (tenant, proposalId) => request(`/agents/proposals/${proposalId}/confirm`, tenant, {
    method: 'POST', body: JSON.stringify({acknowledged: true}),
  }),
};

const activityPayload = form => ({
  name: form.name,
  package_id: form.package,
  goal: form.goal,
  budget_yuan: Number(form.budget),
  case_ids: form.caseIds,
  requested_mode: form.requestedMode || 'auto',
});

export const activityApi = {
  list: tenant => request('/activities', tenant),
  preflight: (tenant, form) => request('/activities/preflight', tenant, {method: 'POST', body: JSON.stringify(activityPayload(form))}),
  create: (tenant, form) => request('/activities', tenant, {method: 'POST', body: JSON.stringify(activityPayload(form))}),
  transition: (tenant, activityId, status, reason) => request(`/activities/${encodeURIComponent(activityId)}/transition`, tenant, {
    method: 'POST', body: JSON.stringify({status, reason, acknowledged: true}),
  }),
};

export function normalizeCatalogPackage(item, tenant) {
  return {
    ...item,
    tenant_id: tenant,
    package_name: '服务端权威资产目录',
    rate: item.commission_rate_bps == null ? null : item.commission_rate_bps / 10000,
    start_date: item.mandate_start || '—',
    end_date: item.mandate_end || '—',
    serverAuthoritative: item.data_source === 'server-authoritative',
  };
}

export function normalizeCatalogCase(item, tenant) {
  const balanceYuan = item.claim_balance_cents == null ? null : item.claim_balance_cents / 100;
  return {
    ...item,
    tenant_id: tenant,
    transfer_balance_yuan: balanceYuan,
    principal_yuan: item.principal_cents == null ? null : item.principal_cents / 100,
    interest_yuan: item.interest_cents == null ? null : item.interest_cents / 100,
    fees_yuan: item.fee_cents == null ? null : item.fee_cents / 100,
    balance_snapshot_date: item.updated_at?.slice(0, 10) || '—',
    first_overdue_date: item.first_overdue_date,
    ageMonths: null,
    agingBucket: '账龄未接入',
    dataQuality: item.data_completeness_score,
    lastContact: item.last_contact_at || '—',
    nextAllowed: item.next_allowed,
    contactability: item.contact_basis_ref ? '联系依据已登记' : '联系依据待补',
    cash: item.confirmed_net_recovery_cents / 100,
    commission: item.accrued_commission_cents / 100,
    reason: item.protection_reason || '',
    scenario_only: false,
    serverAuthoritative: item.data_source === 'server-authoritative',
  };
}

export const catalogApi = {
  packages: async (tenant, filters = {}) => {
    const payload = await request(`/asset-packages${queryString(filters)}`, tenant);
    return {...payload, items: payload.items.map(item => normalizeCatalogPackage(item, tenant))};
  },
  cases: async (tenant, filters = {}) => {
    const payload = await request(`/cases${queryString(filters)}`, tenant);
    return {...payload, items: payload.items.map(item => normalizeCatalogCase(item, tenant))};
  },
  case: async (tenant, caseId) => normalizeCatalogCase(await request(`/cases/${caseId}`, tenant), tenant),
};

export const assetImportApi = {
  list: tenant => request('/asset-imports', tenant),
  preview: (tenant, filename, csvText, idempotencyKey) => request('/asset-imports/previews', tenant, {
    method: 'POST',
    body: JSON.stringify({filename, csv_text: csvText, idempotency_key: idempotencyKey}),
  }),
  commit: (tenant, batchId, expectedVersion, reviewNote) => request(`/asset-imports/${batchId}/commit`, tenant, {
    method: 'POST',
    body: JSON.stringify({expected_version: expectedVersion, review_note: reviewNote, acknowledged: true}),
  }),
};

export const policyApi = {
  list: (tenant, packageId) => request(`/policy-proposals${queryString({package_id: packageId})}`, tenant),
  propose: (tenant, packageId, policy, reason) => request(`/policy-proposals/packages/${packageId}`, tenant, {
    method: 'POST',
    body: JSON.stringify({
      expected_policy_version: policy.version,
      budget_limit_yuan: Number(policy.budget),
      min_settlement_bps: Math.round(Number(policy.minSettlement) * 100),
      max_installments: Number(policy.maxInstallments),
      min_down_payment_bps: Math.round(Number(policy.minDownPayment) * 100),
      proposal_reason: reason,
      acknowledged: true,
    }),
  }),
  decide: (tenant, proposalId, decision, reviewNote, expectedVersion) => request(`/policy-proposals/${proposalId}/decision`, tenant, {
    method: 'POST',
    body: JSON.stringify({decision, expected_version: expectedVersion, review_note: reviewNote, acknowledged: true}),
  }),
};

export function normalizeFinancialOverview(payload, tenant) {
  return {
    summary: payload.summary,
    ledger: (payload.recovery_ledger || []).map(row => ({
      tenant_id: tenant,
      transaction_id: row.entry_id,
      receipt_id: row.receipt_id,
      case_id: row.case_id,
      package_id: row.package_id,
      booked_date: row.booked_at.slice(0, 10),
      event_type: row.event_type,
      cash_yuan: row.amount_cents / 100,
      eligible: row.eligible_amount_cents !== 0,
      eligible_cash_yuan: row.eligible_amount_cents / 100,
      commission_rule_id: row.commission_rule_id,
      commission_rule_version: row.commission_rule_version,
      rate: row.rate_bps / 10000,
      commission_yuan: row.commission_cents / 100,
      reason: row.reason,
      original_transaction_id: row.original_entry_id || '',
      allocation: row.allocation,
      source: row.source,
      signature: row.receipt_id ? 'HMAC v1 验签通过' : '迁移数据摘要已核验',
      idempotency: row.receipt_id ? 'Provider 事件号唯一' : '迁移批次唯一',
    })),
    commissionLedger: payload.commission_ledger || [],
    pendingReceipts: payload.pending_receipts || [],
    reconciliations: payload.reconciliations || [],
    webhookReady: payload.webhook_ready,
    webhookProvider: payload.webhook_provider,
    sandboxEnabled: payload.sandbox_enabled,
  };
}

export const paymentApi = {
  overview: tenant => request('/payments/overview', tenant),
  sandboxReceipt: (tenant, idempotencyKey, options = {}) => request('/payments/sandbox-receipts', tenant, {
    method: 'POST',
    body: JSON.stringify({
      case_id: options.caseId || 'C002',
      amount_cents: options.amountCents || 101600,
      idempotency_key: idempotencyKey,
    }),
  }),
  matchReceipt: (tenant, receiptId, caseId) => request(`/payments/receipts/${receiptId}/match`, tenant, {
    method: 'POST',
    body: JSON.stringify({case_id: caseId, acknowledged: true}),
  }),
  matchCandidates: (tenant, receiptId) => request(`/payments/receipts/${receiptId}/candidates`, tenant),
  proposeReconciliation: (tenant, receiptId, caseId, reason) => request(`/payments/receipts/${receiptId}/reconciliations`, tenant, {
    method: 'POST',
    body: JSON.stringify({case_id: caseId, reason, acknowledged: true}),
  }),
  decideReconciliation: (tenant, reconciliationId, decision, reviewNote, expectedVersion) => request(`/payments/reconciliations/${reconciliationId}/decision`, tenant, {
    method: 'POST',
    body: JSON.stringify({decision, review_note: reviewNote, expected_version: expectedVersion, acknowledged: true}),
  }),
  commissionEvent: (tenant, payload) => request('/commissions/events', tenant, {
    method: 'POST',
    body: JSON.stringify({...payload, acknowledged: true}),
  }),
};

export const protectionApi = {
  overview: tenant => request('/protections/overview', tenant),
  open: (tenant, caseId, payload) => request(`/cases/${caseId}/protections`, tenant, {
    method: 'POST', body: JSON.stringify({...payload, acknowledged: true}),
  }),
  proposeResolution: (tenant, incidentId, resolutionNote, evidenceRefs) => request(`/protections/incidents/${incidentId}/resolution-proposals`, tenant, {
    method: 'POST',
    body: JSON.stringify({resolution_note: resolutionNote, evidence_refs: evidenceRefs, acknowledged: true}),
  }),
  decideResolution: (tenant, incidentId, decision, reviewNote, expectedVersion) => request(`/protections/incidents/${incidentId}/decision`, tenant, {
    method: 'POST',
    body: JSON.stringify({decision, review_note: reviewNote, expected_version: expectedVersion, acknowledged: true}),
  }),
};

export const repaymentApi = {
  overview: tenant => request('/repayment-plans/overview', tenant),
  casePlans: (tenant, caseId) => request(`/cases/${caseId}/repayment-plans`, tenant),
  propose: (tenant, caseId, payload) => request(`/cases/${caseId}/repayment-plans`, tenant, {
    method: 'POST',
    body: JSON.stringify({...payload, acknowledged: true}),
  }),
  decide: (tenant, planRowId, decision, reviewNote, expectedVersion) => request(`/repayment-plans/${planRowId}/decision`, tenant, {
    method: 'POST',
    body: JSON.stringify({decision, review_note: reviewNote, expected_version: expectedVersion, acknowledged: true}),
  }),
};

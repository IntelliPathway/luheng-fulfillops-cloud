const API_BASE = import.meta.env?.VITE_API_BASE_URL || '/api/v1';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const authToken = () => {
  try { return typeof localStorage === 'undefined' ? '' : localStorage.getItem('luheng.auth.token') || ''; }
  catch { return ''; }
};

const headers = tenant => {
  const token = authToken();
  return {
    'Content-Type': 'application/json',
    'X-Tenant-ID': tenant,
    ...(token ? {Authorization: `Bearer ${token}`} : {'X-Actor-ID': 'Terry'}),
  };
};

async function request(path, tenant, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {...headers(tenant), ...(options.headers || {})},
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {}
    throw new ApiError(message, response.status);
  }
  return response.json();
}

const jobKey = prefix => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

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
      title: scope.title || '履衡 AI 会话',
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
  preflight: (tenant, form) => request('/activities/preflight', tenant, {method: 'POST', body: JSON.stringify(activityPayload(form))}),
  create: (tenant, form) => request('/activities', tenant, {method: 'POST', body: JSON.stringify(activityPayload(form))}),
};

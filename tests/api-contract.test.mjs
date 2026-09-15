import assert from 'node:assert/strict';
import test from 'node:test';
import {agentApi, normalizeFinancialOverview, normalizeIntegrationOverview, paymentApi, protectionApi, securityApi, servicePayload} from '../src/api.js';

test('normalizes backend integration fields for the existing UI model', () => {
  const result = normalizeIntegrationOverview({
    services: {
      model: {
        provider: 'DeepSeek',
        settings: {endpoint: 'https://example.test', model: 'chat', timeout: '30'},
        credential_mask: '••••1234',
        version: 7,
        saved: true,
        connected: true,
        latency_ms: 240,
        connection_tested_at: '2026-09-14T10:00:00',
        updated_at: '2026-09-14T09:59:00',
      },
    },
    state: {enabled: true, enabled_at: '2026-09-14T10:02:00', enabled_by: 'Terry', enabled_service_versions: {model: 7}, invalidated_reason: null},
    gate: {ready: true},
    last_report: {
      id: 'SELF-1', status: 'passed', service_versions: {model: 7}, items: [],
      created_at: '2026-09-14T10:01:00', expires_at: '2026-09-15T10:01:00',
    },
  });
  assert.equal(result.services.model.model, 'chat');
  assert.equal(result.services.model.tested, true);
  assert.equal(result.services.model.latency, '240 ms');
  assert.equal(result.state.lastRun.passed, true);
  assert.deepEqual(result.state.enabledServiceVersions, {model: 7});
});

test('sends credentials separately from provider settings', () => {
  const payload = servicePayload('model', {
    provider: 'DeepSeek', endpoint: 'https://api.deepseek.com', model: 'deepseek-flash', timeout: '30',
    executionMode: 'live-provider', maxOutputTokens: '512', maxCostUsd: '0.05', apiKey: 'plain-secret',
  });
  assert.equal(payload.credential, 'plain-secret');
  assert.equal('apiKey' in payload.settings, false);
  assert.deepEqual(payload.settings, {
    endpoint: 'https://api.deepseek.com', model: 'deepseek-flash', timeout: '30',
    executionMode: 'live-provider', maxOutputTokens: '512', maxCostUsd: '0.05',
  });
});

test('does not resend a masked credential returned by the backend', () => {
  const payload = servicePayload('phone', {
    provider: 'SIP', sipHost: 'sip.test', trunk: 't', callerId: '010****', callback: 'https://callback.test', secret: '••••ABCD',
  });
  assert.equal('credential' in payload, false);
});

test('preserves DeepSeek Harness safety and persistence settings', () => {
  const payload = servicePayload('agent', {
    provider: 'DeepSeek Harness',
    endpoint: 'stdio://deepseek-harness-sdk',
    profile: 'fulfillops-safe',
    approval: '高影响动作需确认',
    transport: 'sandbox-contract',
    safetyPreset: 'fulfillops-safe',
    sessionPersistence: 'database-checkpoint',
    authToken: 'harness-secret',
  });
  assert.deepEqual(payload.settings, {
    endpoint: 'stdio://deepseek-harness-sdk',
    profile: 'fulfillops-safe',
    approval: '高影响动作需确认',
    transport: 'sandbox-contract',
    safetyPreset: 'fulfillops-safe',
    sessionPersistence: 'database-checkpoint',
  });
  assert.equal(payload.credential, 'harness-secret');
});

test('calls assurance health and governed replay endpoints with tenant context', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (String(url).endsWith('/agents/replays/jobs')) {
      return {ok: true, json: async () => ({id: 'JOB-REPLAY', status: 'succeeded', result: {replay_run_id: 'REPLAY-1'}})};
    }
    return {ok: true, json: async () => ({status: 'ready'})};
  };
  try {
    await securityApi.authHealth('TENANT_A');
    await securityApi.modelGatewayHealth('TENANT_A');
    await agentApi.replay('TENANT_A');
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/security/auth/health');
  assert.equal(calls[0].options.headers['X-Tenant-ID'], 'TENANT_A');
  assert.equal(calls[1].url, '/api/v1/models/gateway/health');
  assert.equal(calls[2].url, '/api/v1/agents/replays/jobs');
  const replayBody = JSON.parse(calls[2].options.body);
  assert.equal(replayBody.suite_name, 'fulfillops-safe-core');
  assert.equal(replayBody.mode, 'deterministic-contract');
  assert.equal(replayBody.acknowledged_external_call, false);
  assert.match(replayBody.idempotency_key, /^model-replay-TENANT_A-/);
});

test('normalizes integer-cent financial ledgers for display without losing evidence', () => {
  const result = normalizeFinancialOverview({
    summary: {confirmed_net_recovery_cents: 101600, pending_receipt_count: 0},
    recovery_ledger: [{
      entry_id: 'REC-1', receipt_id: 'PR-1', case_id: 'C002', package_id: 'PKG_A',
      booked_at: '2026-09-12T12:00:00', event_type: 'payment', amount_cents: 101600,
      eligible_amount_cents: 101600, commission_rule_id: 'COM_A_V1', commission_rule_version: 1,
      rate_bps: 1500, commission_cents: 15240, reason: 'IN_MANDATE', original_entry_id: null,
      allocation: 'provider_case_reference', source: 'sandbox-amc',
    }],
    commission_ledger: [], pending_receipts: [], webhook_ready: true,
    webhook_provider: 'sandbox-amc', sandbox_enabled: true,
  }, 'TENANT_A');
  assert.equal(result.ledger[0].cash_yuan, 1016);
  assert.equal(result.ledger[0].commission_yuan, 152.4);
  assert.equal(result.ledger[0].rate, 0.15);
  assert.equal(result.ledger[0].tenant_id, 'TENANT_A');
  assert.equal(result.ledger[0].signature, 'HMAC v1 验签通过');
  assert.equal(result.webhookReady, true);
});

test('submits only a sandbox event key and never serializes a payment secret', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({receipt: {id: 'PR-1'}, duplicate: false})};
  };
  try {
    await paymentApi.overview('TENANT_A');
    await paymentApi.sandboxReceipt('TENANT_A', 'ui-demo-payment-v1');
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/payments/overview');
  assert.equal(calls[1].url, '/api/v1/payments/sandbox-receipts');
  const body = JSON.parse(calls[1].options.body);
  assert.deepEqual(body, {case_id: 'C002', amount_cents: 101600, idempotency_key: 'ui-demo-payment-v1'});
  assert.equal(JSON.stringify(calls).includes('secret'), false);
});

test('uses maker-checker reconciliation endpoints without direct ledger writes', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({id: 'REC-1', version: 1})};
  };
  try {
    await paymentApi.matchCandidates('TENANT_A', 'PRC-1');
    await paymentApi.proposeReconciliation('TENANT_A', 'PRC-1', 'C002', '人工核对付款附言与合同编号一致');
    await paymentApi.decideReconciliation('TENANT_A', 'REC-1', 'approve', '独立复核证据一致', 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/payments/receipts/PRC-1/candidates');
  assert.equal(calls[1].url, '/api/v1/payments/receipts/PRC-1/reconciliations');
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    case_id: 'C002', reason: '人工核对付款附言与合同编号一致', acknowledged: true,
  });
  assert.equal(calls[2].url, '/api/v1/payments/reconciliations/REC-1/decision');
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    decision: 'approve', review_note: '独立复核证据一致', expected_version: 1, acknowledged: true,
  });
  assert.equal(calls.some(call => String(call.url).endsWith('/match')), false);
});

test('uses evidence-bound maker-checker protection endpoints without direct release', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({id: 'PROT-1', version: 2})};
  };
  try {
    await protectionApi.overview('TENANT_A');
    await protectionApi.proposeResolution(
      'TENANT_A',
      'PROT-1',
      '异议事实已经复核，申请重新评估案件',
      ['EVIDENCE-DISPUTE-001'],
    );
    await protectionApi.decideResolution('TENANT_A', 'PROT-1', 'approve', '独立复核证据一致', 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/protections/overview');
  assert.equal(calls[1].url, '/api/v1/protections/incidents/PROT-1/resolution-proposals');
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    resolution_note: '异议事实已经复核，申请重新评估案件',
    evidence_refs: ['EVIDENCE-DISPUTE-001'],
    acknowledged: true,
  });
  assert.equal(calls[2].url, '/api/v1/protections/incidents/PROT-1/decision');
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    decision: 'approve', review_note: '独立复核证据一致', expected_version: 2, acknowledged: true,
  });
  assert.equal(calls.some(call => String(call.url).includes('/release')), false);
});

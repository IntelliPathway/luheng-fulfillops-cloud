import assert from 'node:assert/strict';
import test from 'node:test';
import {ApiError, activityApi, agentApi, assetImportApi, catalogApi, classifyApiFailure, contactApi, knowledgeApi, membershipApi, normalizeCatalogCase, normalizeCatalogPackage, normalizeFinancialOverview, normalizeIntegrationOverview, operationsApi, paymentApi, protectionApi, repaymentApi, securityApi, servicePayload, telephonyApi} from '../src/api.js';

test('governs knowledge versions and loads daily financial close', async () => {
  const calls=[]; const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>[]}};
  try {
    await knowledgeApi.list('TENANT_A','published');
    await knowledgeApi.create('TENANT_A',{document_key:'KNOW-PAY',title:'到账口径',category:'policy',source_reference:'policy://v2',content_digest:'a'.repeat(64),summary:'仅核验入账视为到账'});
    await knowledgeApi.decide('TENANT_A','KNOW-1',{decision:'approve',expected_version:2,review_note:'来源和摘要已核验'});
    await operationsApi.dailyClose('TENANT_A');
  } finally { globalThis.fetch=originalFetch; }
  assert.deepEqual(calls.map(call=>call.url),['/api/v1/knowledge/documents?status=published','/api/v1/knowledge/documents','/api/v1/knowledge/documents/KNOW-1/decision','/api/v1/provider-operations/daily-close']);
  assert.equal(JSON.parse(calls[1].options.body).acknowledged,true);
  assert.equal(JSON.parse(calls[2].options.body).acknowledged,true);
});

test('loads and transitions server-authoritative activities', async () => {
  const calls=[]; const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>[]}};
  try {
    await activityApi.list('TENANT_A');
    await activityApi.transition('TENANT_A','ACT-001','paused','人工检查当前执行证据');
  } finally { globalThis.fetch=originalFetch; }
  assert.deepEqual(calls.map(call=>call.url),['/api/v1/activities','/api/v1/activities/ACT-001/transition']);
  assert.equal(JSON.parse(calls[1].options.body).acknowledged,true);
});

test('creates governed contact tasks and human handoffs without dialing directly', async () => {
  const calls=[]; const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>({id:'CONTACT-1'})}};
  try {
    await contactApi.create('TENANT_A',{case_id:'C004',contact_reference:'CONTACT-REF-C004',scheduled_at:'2026-09-20T06:30:00Z'});
    await contactApi.channels('TENANT_A');
    await contactApi.handoff('TENANT_A','CONTACT-1','manual explanation requested');
    await contactApi.cancel('TENANT_A','CONTACT-2','case state changed, stop the task');
    await contactApi.retry('TENANT_A','CONTACT-3','2026-09-21T06:30:00Z','line failed, retry safely');
    await telephonyApi.events('TENANT_A');
  } finally { globalThis.fetch=originalFetch; }
  assert.equal(calls[0].url,'/api/v1/contact-attempts');
  assert.equal(calls[1].url,'/api/v1/contact-attempts/channels');
  assert.equal(calls[2].url,'/api/v1/contact-attempts/CONTACT-1/handoff');
  assert.equal(calls[3].url,'/api/v1/contact-attempts/CONTACT-2/cancel');
  assert.equal(calls[4].url,'/api/v1/contact-attempts/CONTACT-3/retry');
  assert.equal(calls[5].url,'/api/v1/telephony/events?limit=100');
  assert.equal(JSON.stringify(calls).includes('phone_number'),false);
});

test('uses governed membership proposal endpoints', async () => {
  const calls=[]; const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>({id:'MEMPROP-1'})}};
  try {
    await membershipApi.members('TENANT_A');
    await membershipApi.propose('TENANT_A',{target_user_id:'user-1',requested_role:'viewer',requested_status:'active',proposal_reason:'apply least privilege'});
    await membershipApi.decide('TENANT_A','MEMPROP-1',{decision:'approve',expected_version:1,review_note:'independent review complete'});
  } finally { globalThis.fetch=originalFetch; }
  assert.deepEqual(calls.map(call=>call.url),['/api/v1/governance/members','/api/v1/governance/membership-proposals','/api/v1/governance/membership-proposals/MEMPROP-1/decision']);
  assert.ok(JSON.parse(calls[1].options.body).acknowledged);
});

test('loads pilot evidence from tenant-scoped operational endpoints', async () => {
  const calls=[];
  const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>({status:'ready'})}};
  try {
    await Promise.all([operationsApi.pilotScorecard('TENANT_A'),operationsApi.metrics('TENANT_A'),operationsApi.workQueue('TENANT_A'),operationsApi.releaseGate('TENANT_A')]);
  } finally { globalThis.fetch=originalFetch; }
  assert.deepEqual(calls.map(call=>call.url),['/api/v1/pilot/scorecard','/api/v1/observability/metrics','/api/v1/operations/work-queue?limit=100','/api/v1/pilot/release-gate']);
  assert.ok(calls.every(call=>call.options.headers['X-Tenant-ID']==='TENANT_A'));
});

test('loads model budget and payment exception operations without sensitive payloads', async () => {
  const calls=[]; const originalFetch=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>({model_budget:{},payment_exceptions:{}})}};
  try { await operationsApi.providerScorecard('TENANT_A'); } finally { globalThis.fetch=originalFetch; }
  assert.equal(calls[0].url,'/api/v1/provider-operations/scorecard');
  assert.equal(calls[0].options.headers['X-Tenant-ID'],'TENANT_A');
});

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
  assert.equal('X-Actor-ID' in calls[0].options.headers, false);
  assert.equal(calls[1].url, '/api/v1/models/gateway/health');
  assert.equal(calls[2].url, '/api/v1/agents/replays/jobs');
  const replayBody = JSON.parse(calls[2].options.body);
  assert.equal(replayBody.suite_name, 'fulfillops-safe-core');
  assert.equal(replayBody.mode, 'deterministic-contract');
  assert.equal(replayBody.acknowledged_external_call, false);
  assert.match(replayBody.idempotency_key, /^model-replay-TENANT_A-/);
});

test('distinguishes authentication and API failures from a genuine offline demo', () => {
  assert.equal(classifyApiFailure(new ApiError('需要身份', 401)), 'auth-required');
  assert.equal(classifyApiFailure(new ApiError('禁止访问', 403)), 'auth-required');
  assert.equal(classifyApiFailure(new ApiError('服务异常', 503)), 'api-error');
  assert.equal(classifyApiFailure(new ApiError('Sites 演示', 503, {mode: 'sites-demo'})), 'offline');
  assert.equal(classifyApiFailure(new TypeError('fetch failed')), 'offline');
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

test('submits only plan evidence digests and uses maker-checker activation', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({id: 'RPLAN-1', version: 1})};
  };
  const payload = {
    plan_id: 'PLAN-C008-001', total_cents: 1200000, down_payment_cents: 240000,
    installments: [{installment_no: 1, due_date: '2026-09-20', due_cents: 240000}],
    agreement_reference: 'AGREEMENT-C008-001', agreement_digest: 'a'.repeat(64),
    signed_at: '2026-09-15T08:00:00Z', proposal_reason: '外部签署回执已经核验',
  };
  try {
    await repaymentApi.overview('TENANT_A');
    await repaymentApi.propose('TENANT_A', 'C008', payload);
    await repaymentApi.decide('TENANT_A', 'RPLAN-1', 'approve', '独立复核条款与证据一致', 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/repayment-plans/overview');
  assert.equal(calls[1].url, '/api/v1/cases/C008/repayment-plans');
  assert.deepEqual(JSON.parse(calls[1].options.body), {...payload, acknowledged: true});
  assert.equal(JSON.stringify(calls).includes('agreement_content'), false);
  assert.equal(calls[2].url, '/api/v1/repayment-plans/RPLAN-1/decision');
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    decision: 'approve', review_note: '独立复核条款与证据一致', expected_version: 1, acknowledged: true,
  });
});

test('uses preview and maker-checker commit endpoints for asset imports', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({id: 'IMP-1', version: 1, status: 'ready'})};
  };
  const csv = [
    'package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref',
    'PKG_NEW,测试资产包,C901,1200000,2026-09-01,2027-08-31,COM_NEW_V1,1500,CONSENT-C901',
  ].join('\n');
  try {
    await assetImportApi.list('TENANT_A');
    await assetImportApi.preview('TENANT_A', 'asset_cases.csv', csv, 'asset-import-001');
    await assetImportApi.commit('TENANT_A', 'IMP-1', 1, '独立复核字段、金额和委托期限一致');
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0].url, '/api/v1/asset-imports');
  assert.equal(calls[1].url, '/api/v1/asset-imports/previews');
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    filename: 'asset_cases.csv', csv_text: csv, idempotency_key: 'asset-import-001',
  });
  assert.equal(calls[2].url, '/api/v1/asset-imports/IMP-1/commit');
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    expected_version: 1, review_note: '独立复核字段、金额和委托期限一致', acknowledged: true,
  });
  assert.equal(calls.some(call => String(call.url).includes('/cases/C901')), false);
});

test('queries the server-authoritative asset catalog with encoded filters and pagination', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(url);
    if (String(url).startsWith('/api/v1/asset-packages')) return {ok: true, json: async () => ({items: [{package_id: 'PKG_A', title: '一期', commission_rate_bps: 1500, mandate_start: '2026-08-01', mandate_end: '2026-12-31', data_source: 'server-authoritative'}], total: 1, page: 1, page_size: 5, pages: 1})};
    if (url === '/api/v1/cases/C001') return {ok: true, json: async () => ({case_id: 'C001', package_id: 'PKG_A', status: '履约中', claim_balance_cents: 1440000, data_completeness_score: 85, confirmed_net_recovery_cents: 352000, accrued_commission_cents: 52800, next_allowed: '按已签方案核对下一期', data_source: 'server-authoritative', updated_at: '2026-09-15T10:00:00'})};
    return {ok: true, json: async () => ({items: [{case_id: 'C001', package_id: 'PKG_A', status: '履约中', claim_balance_cents: 1440000, data_completeness_score: 85, confirmed_net_recovery_cents: 352000, accrued_commission_cents: 52800, next_allowed: '按已签方案核对下一期', data_source: 'server-authoritative', updated_at: '2026-09-15T10:00:00'}], total: 1, page: 1, page_size: 8, pages: 1, facets: {all: 1, signed: 1, blocked: 0, quality: 0}})};
  };
  try {
    const packages = await catalogApi.packages('TENANT_A', {query: '长龄 一期', page: 1, page_size: 5});
    const cases = await catalogApi.cases('TENANT_A', {package_id: 'PKG_A', view: 'signed', sort: 'balance_desc', page: 2, page_size: 8});
    const detail = await catalogApi.case('TENANT_A', 'C001');
    assert.equal(packages.items[0].rate, 0.15);
    assert.equal(packages.items[0].serverAuthoritative, true);
    assert.equal(cases.items[0].transfer_balance_yuan, 14400);
    assert.equal(cases.items[0].cash, 3520);
    assert.equal(cases.items[0].scenario_only, false);
    assert.equal(detail.commission, 528);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls[0], '/api/v1/asset-packages?query=%E9%95%BF%E9%BE%84+%E4%B8%80%E6%9C%9F&page=1&page_size=5');
  assert.equal(calls[1], '/api/v1/cases?package_id=PKG_A&view=signed&sort=balance_desc&page=2&page_size=8');
  assert.equal(calls[2], '/api/v1/cases/C001');
});

test('normalizes catalog records without inventing debt-age or contact facts', () => {
  const packageItem = normalizeCatalogPackage({commission_rate_bps: null, mandate_start: null, mandate_end: null, data_source: 'server-authoritative'}, 'TENANT_A');
  const caseItem = normalizeCatalogCase({claim_balance_cents: null, data_completeness_score: 20, confirmed_net_recovery_cents: 0, accrued_commission_cents: 0, next_allowed: '补齐资料', data_source: 'server-authoritative'}, 'TENANT_A');
  assert.equal(packageItem.rate, null);
  assert.equal(packageItem.start_date, '—');
  assert.equal(caseItem.transfer_balance_yuan, null);
  assert.equal(caseItem.ageMonths, null);
  assert.equal(caseItem.agingBucket, '账龄未接入');
  assert.equal(caseItem.contactability, '联系依据待补');
});

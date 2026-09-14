import assert from 'node:assert/strict';
import test from 'node:test';
import {normalizeIntegrationOverview, servicePayload} from '../src/api.js';

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
    provider: 'DeepSeek', endpoint: 'https://example.test', model: 'chat', timeout: '30', apiKey: 'plain-secret',
  });
  assert.equal(payload.credential, 'plain-secret');
  assert.equal('apiKey' in payload.settings, false);
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

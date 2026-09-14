import assert from 'node:assert/strict';
import test from 'node:test';
import {
  integrationGate,
  maskedCredential,
  restoreIntegrationState,
  sanitizeServiceConfig,
  serializeIntegrationState,
  serviceVersionSnapshot,
} from '../src/integration-state.js';

const services = {
  agent: {version: 2, saved: true, tested: true, authToken: 'secret-a'},
  model: {version: 3, saved: true, tested: true, apiKey: 'secret-b'},
  voice: {version: 4, saved: true, tested: true, accessKey: 'secret-c'},
  phone: {version: 5, saved: true, tested: true, secret: 'secret-d'},
};

test('never serializes plaintext service credentials', () => {
  const json = serializeIntegrationState({TENANT_A: services}, {TENANT_A: {enabled: false}});
  assert.equal(json.includes('secret-a'), false);
  assert.equal(json.includes('secret-b'), false);
  assert.equal(json.includes('secret-c'), false);
  assert.equal(json.includes('secret-d'), false);
  assert.equal(JSON.parse(json).configs.TENANT_A.model.apiKey, maskedCredential);
});

test('requires current service versions for self-test and enablement', () => {
  const versions = serviceVersionSnapshot(services);
  const state = {
    enabled: true,
    lastRun: {passed: true, expiresAt: '2099-01-01T00:00:00.000Z', serviceVersions: versions},
    enabledServiceVersions: versions,
  };
  assert.equal(integrationGate(services, state).ready, true);

  const changed = {...services, voice: {...services.voice, version: 5}};
  const gate = integrationGate(changed, state);
  assert.equal(gate.reportCurrent, false);
  assert.equal(gate.ready, false);
});

test('expires old self-test reports', () => {
  const versions = serviceVersionSnapshot(services);
  const state = {
    enabled: true,
    lastRun: {passed: true, expiresAt: '2026-01-01T00:00:00.000Z', serviceVersions: versions},
    enabledServiceVersions: versions,
  };
  assert.equal(integrationGate(services, state, new Date('2026-01-02').getTime()).reportFresh, false);
});

test('restores only known tenants and keeps credentials masked', () => {
  const initialConfigs = {TENANT_A: services};
  const initialStates = {TENANT_A: {enabled: false, lastRun: null}};
  const restored = restoreIntegrationState(initialConfigs, initialStates, {
    schemaVersion: 1,
    configs: {TENANT_A: {model: {...services.model, apiKey: 'persisted-secret'}}, TENANT_X: {model: {}}},
    states: {TENANT_A: {enabled: true}, TENANT_X: {enabled: true}},
  });
  assert.deepEqual(Object.keys(restored.configs), ['TENANT_A']);
  assert.equal(restored.configs.TENANT_A.model.apiKey, maskedCredential);
  assert.equal(restored.states.TENANT_A.enabled, true);
});

test('sanitizes a single configuration before React state receives it', () => {
  const config = sanitizeServiceConfig('phone', {provider: 'SIP', secret: 'plain'});
  assert.equal(config.secret, maskedCredential);
  assert.equal(config.credentialConfigured, true);
});

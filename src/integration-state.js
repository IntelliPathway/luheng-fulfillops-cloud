export const INTEGRATION_STORAGE_KEY = 'luheng-ai:integration-state:v1';

const secretFields = {
  agent: ['authToken'],
  model: ['apiKey'],
  voice: ['accessKey'],
  phone: ['secret'],
};

export const maskedCredential = '••••••••';

export function sanitizeServiceConfig(type, config) {
  const sanitized = {...config};
  for (const field of secretFields[type] || []) {
    const value = String(sanitized[field] || '');
    sanitized[field] = value ? maskedCredential : '';
    sanitized.credentialConfigured = Boolean(value);
  }
  return sanitized;
}

export function serviceVersionSnapshot(services = {}) {
  return Object.fromEntries(
    Object.entries(services).map(([type, config]) => [type, Number(config.version || 1)]),
  );
}

export function versionsMatch(services = {}, snapshot = {}) {
  const current = serviceVersionSnapshot(services);
  const types = Object.keys(current);
  return types.length > 0 && types.every(type => current[type] === Number(snapshot[type] || 0));
}

export function integrationGate(services = {}, state = {}, now = Date.now()) {
  const allSaved = Object.values(services).length === 4 && Object.values(services).every(item => item.saved);
  const allConnected = Object.values(services).length === 4 && Object.values(services).every(item => item.tested);
  const reportCurrent = Boolean(state.lastRun?.passed && versionsMatch(services, state.lastRun.serviceVersions));
  const reportFresh = Boolean(reportCurrent && (!state.lastRun.expiresAt || new Date(state.lastRun.expiresAt).getTime() > now));
  const enabledSnapshotCurrent = Boolean(state.enabled && versionsMatch(services, state.enabledServiceVersions));
  const ready = Boolean(allSaved && allConnected && reportFresh && enabledSnapshotCurrent);

  return {
    allSaved,
    allConnected,
    reportCurrent,
    reportFresh,
    enabledSnapshotCurrent,
    ready,
  };
}

export function restoreIntegrationState(initialConfigs, initialStates, persisted) {
  if (!persisted || persisted.schemaVersion !== 1) {
    return {configs: initialConfigs, states: initialStates};
  }

  const configs = structuredClone(initialConfigs);
  const states = structuredClone(initialStates);
  for (const tenant of Object.keys(configs)) {
    if (persisted.configs?.[tenant]) {
      for (const type of Object.keys(configs[tenant])) {
        if (persisted.configs[tenant][type]) {
          configs[tenant][type] = {
            ...configs[tenant][type],
            ...sanitizeServiceConfig(type, persisted.configs[tenant][type]),
          };
        }
      }
    }
    if (persisted.states?.[tenant]) states[tenant] = {...states[tenant], ...persisted.states[tenant]};
  }
  return {configs, states};
}

export function serializeIntegrationState(configs, states) {
  const sanitized = Object.fromEntries(
    Object.entries(configs).map(([tenant, services]) => [
      tenant,
      Object.fromEntries(
        Object.entries(services).map(([type, config]) => [type, sanitizeServiceConfig(type, config)]),
      ),
    ]),
  );
  return JSON.stringify({schemaVersion: 1, configs: sanitized, states});
}

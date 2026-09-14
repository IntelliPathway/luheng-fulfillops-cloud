import assert from 'node:assert/strict';
import test from 'node:test';
import {determineActivityMode, GOALS, selectActivityCandidates} from '../src/activity-preflight.js';

const cases = [
  {case_id: 'C1', package_id: 'P1', status: '待联系', blocked: false},
  {case_id: 'C2', package_id: 'P1', status: '履约中', blocked: false, plan: {status: 'SIGNED'}},
  {case_id: 'C3', package_id: 'P1', status: '异议暂停', blocked: true},
  {case_id: 'C4', package_id: 'P1', status: '已结清', blocked: false},
  {case_id: 'C5', package_id: 'P1', status: '到账待匹配', blocked: false},
];

test('excludes protected, completed and in-flight cases before goal matching', () => {
  const result = selectActivityCandidates({
    cases,
    activities: [{status: 'running', caseIds: ['C1']}],
    packageId: 'P1',
    goal: GOALS.FIRST_CONTACT,
  });
  assert.deepEqual(result.eligible, []);
  assert.deepEqual(result.exclusions, {'保护暂停': 1, '已完成': 1, '在途任务': 1, '目标不匹配': 2});
});

test('selects only cases that match the requested operating goal', () => {
  const signed = selectActivityCandidates({cases, activities: [], packageId: 'P1', goal: GOALS.SIGNED_PLAN});
  const payments = selectActivityCandidates({cases, activities: [], packageId: 'P1', goal: GOALS.PAYMENT_RECONCILIATION});
  assert.deepEqual(signed.eligible.map(item => item.case_id), ['C2']);
  assert.deepEqual(payments.eligible.map(item => item.case_id), ['C5']);
});

test('allows production channel mode only when every hard gate passes', () => {
  assert.deepEqual(
    determineActivityMode({integrationReady: true, policyStatus: 'published', caseCount: 3}),
    {productionReady: true, mode: 'channel'},
  );
  assert.equal(determineActivityMode({integrationReady: true, policyStatus: 'draft', caseCount: 3}).mode, 'sandbox');
  assert.equal(determineActivityMode({integrationReady: false, policyStatus: 'published', caseCount: 3}).mode, 'sandbox');
  assert.equal(determineActivityMode({integrationReady: true, policyStatus: 'published', caseCount: 0}).mode, 'sandbox');
});

import assert from 'node:assert/strict';
import test from 'node:test';

import {buildOfflineRepaymentOverview, summarizeRepaymentPlans} from '../src/repayment-state.js';

test('builds tenant-scoped installment balances from the immutable recovery ledger', () => {
  const ledger = [
    {tenant_id: 'TENANT_A', case_id: 'C002', cash_yuan: 2520},
    {tenant_id: 'TENANT_A', case_id: 'C002', cash_yuan: 1000},
    {tenant_id: 'TENANT_B', case_id: 'C002', cash_yuan: 9999},
  ];
  const overview = buildOfflineRepaymentOverview('TENANT_A', ledger);
  const plan = overview.plans.find(row => row.plan_id === 'PLAN002');

  assert.equal(overview.plans.length, 6);
  assert.equal(overview.pending_review_count, 1);
  assert.equal(plan.paid_cents, 352000);
  assert.equal(plan.remaining_cents, 908000);
  assert.deepEqual(plan.installments.slice(0, 2).map(row => row.paid_cents), [252000, 100000]);
});

test('summarizes pending, active, completed, overdue and due-soon plan states', () => {
  const plans = [
    {status: 'pending_review', overdue_cents: 0, installments: []},
    {status: 'active', overdue_cents: 300, installments: [{due_date: '2026-09-20', remaining_cents: 500}]},
    {status: 'completed', overdue_cents: 0, installments: [{due_date: '2026-09-15', remaining_cents: 0}]},
  ];
  const overview = summarizeRepaymentPlans(plans);

  assert.deepEqual(
    {
      active: overview.active_count,
      pending: overview.pending_review_count,
      completed: overview.completed_count,
      overdue: overview.overdue_plan_count,
      dueSoon: overview.due_soon_count,
    },
    {active: 1, pending: 1, completed: 1, overdue: 1, dueSoon: 1},
  );
});

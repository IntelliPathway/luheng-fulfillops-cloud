export const GOALS = {
  SIGNED_PLAN: '已签协议履约',
  FIRST_CONTACT: '首次联络与意愿确认',
  PAYMENT_RECONCILIATION: '回款自动核对',
};

const completedStatuses = new Set(['已结清', '本期已足额']);
const firstContactStatuses = new Set(['待联系', '方案待签']);
const paymentStatuses = new Set(['到账待匹配', '支付失败', '支付处理中', '自然回款', '已确认回款']);

export function matchesActivityGoal(caseItem, goal) {
  if (goal === GOALS.SIGNED_PLAN) return ['active', 'completed'].includes(caseItem.governedPlan?.status) || caseItem.plan?.status === 'SIGNED';
  if (goal === GOALS.FIRST_CONTACT) return firstContactStatuses.has(caseItem.status);
  if (goal === GOALS.PAYMENT_RECONCILIATION) return paymentStatuses.has(caseItem.status);
  return false;
}

export function selectActivityCandidates({cases, activities, packageId, goal}) {
  const candidates = cases.filter(item => item.package_id === packageId);
  const activeCaseIds = new Set(
    activities
      .filter(activity => ['running', 'paused'].includes(activity.status))
      .flatMap(activity => activity.caseIds),
  );

  const reasonFor = item => {
    if (item.blocked) return '保护暂停';
    if (completedStatuses.has(item.status)) return '已完成';
    if (activeCaseIds.has(item.case_id)) return '在途任务';
    if (!matchesActivityGoal(item, goal)) return '目标不匹配';
    return null;
  };

  const excluded = candidates.map(item => ({item, reason: reasonFor(item)})).filter(row => row.reason);
  const exclusions = {'保护暂停': 0, '已完成': 0, '在途任务': 0, '目标不匹配': 0};
  for (const row of excluded) exclusions[row.reason] += 1;

  return {
    candidates,
    eligible: candidates.filter(item => !reasonFor(item)),
    excluded,
    exclusions,
  };
}

export function determineActivityMode({integrationReady, policyStatus, caseCount}) {
  const productionReady = Boolean(integrationReady && policyStatus === 'published' && caseCount > 0);
  return {productionReady, mode: productionReady ? 'channel' : 'sandbox'};
}

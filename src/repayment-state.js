import rawPlans from './data/plans.json' with {type:'json'};
import schedules from './data/installment_schedules.json' with {type:'json'};

const demoPendingPlan={
  tenant_id:'TENANT_A',plan_id:'PLAN006-PENDING',case_id:'C006',total_yuan:12480,down_payment_yuan:2496,
  installments:6,status:'PROPOSED',signed_at:'2026-09-14',policy_version:1,agreement_token:'AGREEMENT-PLAN006-PENDING',
};
const demoPendingSchedule=[2496,1996.8,1996.8,1996.8,1996.8,1996.8].map((due_yuan,index)=>({
  tenant_id:'TENANT_A',schedule_id:`PLAN006-PENDING_${index+1}`,plan_id:'PLAN006-PENDING',case_id:'C006',
  installment_no:index+1,due_date:['2026-09-17','2026-10-17','2026-11-17','2026-12-17','2027-01-17','2027-02-17'][index],due_yuan,
}));

const statusFor=(row,asOf='2026-09-15')=>row.paid_cents>=row.due_cents?'paid':row.paid_cents>0?(row.due_date<asOf?'partial_overdue':'partial'):row.due_date<asOf?'overdue':row.due_date===asOf?'due':'scheduled';

export function buildOfflineRepaymentOverview(tenant,ledger){
  const plans=[...rawPlans,demoPendingPlan].filter(plan=>plan.tenant_id===tenant);
  const allSchedules=[...schedules,...demoPendingSchedule];
  const views=plans.map((plan,index)=>{
    const rows=allSchedules.filter(row=>row.tenant_id===tenant&&row.plan_id===plan.plan_id).map(row=>({...row,due_cents:Math.round(row.due_yuan*100),paid_cents:0}));
    let amount=Math.max(0,Math.round(ledger.filter(row=>row.tenant_id===tenant&&row.case_id===plan.case_id).reduce((sum,row)=>sum+row.cash_yuan,0)*100));
    for(const row of rows){const allocated=Math.min(amount,row.due_cents);row.paid_cents=allocated;amount-=allocated;row.remaining_cents=row.due_cents-row.paid_cents;row.status=statusFor(row);row.installment_id=row.schedule_id;row.last_payment_at=row.paid_cents?'2026-09-15T10:00:00':null}
    const paid_cents=rows.reduce((sum,row)=>sum+row.paid_cents,0);
    const overdue_cents=rows.filter(row=>['overdue','partial_overdue'].includes(row.status)).reduce((sum,row)=>sum+row.remaining_cents,0);
    const pending=plan.status==='PROPOSED';
    const completed=!pending&&paid_cents>=Math.round(plan.total_yuan*100);
    return {id:`DEMO-RPLAN-${index+1}`,plan_id:plan.plan_id,case_id:plan.case_id,status:pending?'pending_review':completed?'completed':'active',version:pending?1:2,currency:'CNY',claim_balance_cents:Math.round((plan.total_yuan/.875)*100),total_cents:Math.round(plan.total_yuan*100),down_payment_cents:Math.round(plan.down_payment_yuan*100),installment_count:plan.installments,policy_version:plan.policy_version,policy_snapshot:{min_settlement_bps:7000,max_installments:6,min_down_payment_bps:2000,seed:true},agreement_reference:plan.agreement_token||`AGREEMENT-${plan.plan_id}`,agreement_digest:'d'.repeat(64),signed_at:`${plan.signed_at||'2026-09-14'}T10:00:00`,evidence_digest:'e'.repeat(64),proposal_reason:pending?'已取得脱敏签署回执，提交独立复核':'既有已签方案迁移为服务端权威台账',proposed_by:pending?'test-operator':'system:seed',proposed_at:`${plan.signed_at||'2026-09-14'}T10:00:00`,reviewed_by:pending?null:'system:migration',reviewed_at:pending?null:`${plan.signed_at||'2026-09-14'}T10:00:00`,review_note:null,activated_at:pending?null:`${plan.signed_at||'2026-09-14'}T10:00:00`,completed_at:completed?'2026-09-15T10:00:00':null,paid_cents,remaining_cents:Math.max(0,Math.round(plan.total_yuan*100)-paid_cents),overdue_cents,installments:rows};
  });
  return summarizeRepaymentPlans(views);
}

export function summarizeRepaymentPlans(plans){
  return {active_count:plans.filter(row=>row.status==='active').length,pending_review_count:plans.filter(row=>row.status==='pending_review').length,completed_count:plans.filter(row=>row.status==='completed').length,overdue_plan_count:plans.filter(row=>row.overdue_cents>0).length,due_soon_count:plans.filter(row=>['active','completed'].includes(row.status)).flatMap(row=>row.installments).filter(row=>row.remaining_cents>0&&row.due_date>='2026-09-15'&&row.due_date<='2026-09-22').length,plans};
}

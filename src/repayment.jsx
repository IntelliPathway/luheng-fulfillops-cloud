import React,{useMemo,useState} from 'react';
import {ArrowUpRight,CalendarCheck,ClockCountdown,FileLock,Plus,ShieldCheck,WarningCircle} from '@phosphor-icons/react';
import {useApp} from './context';
import {money} from './model';
import {Badge,Button,Empty,Field,KeyValue,Metrics,Modal,PageHead,Search,Tabs} from './ui';

const statusLabel={pending_review:'待独立复核',active:'履约中',completed:'已完成',rejected:'已驳回'};
const installmentLabel={paid:'已足额',partial:'部分履约',partial_overdue:'部分逾期',overdue:'已逾期',due:'今日到期',scheduled:'未到期'};

export function RepaymentPlans(){
  const a=useApp();
  const [tab,setTab]=useState('all');
  const [q,setQ]=useState('');
  const overview=a.repaymentOverview;
  const plans=useMemo(()=>overview.plans.filter(plan=>(tab==='all'||(tab==='overdue'?plan.overdue_cents>0:plan.status===tab))&&(plan.plan_id+plan.case_id+plan.status).toLowerCase().includes(q.toLowerCase())),[overview,tab,q]);
  return <><PageHead title="履约计划" description="签署证据、复核决定、期次余额和回款分摊由服务端统一裁决。"><Button onClick={()=>a.setCopilotOpen(true)}>用 AI 查询计划</Button><Button icon={Plus} variant="primary" onClick={()=>a.setDialog({type:'plan-create'})}>提交签约提案</Button></PageHead>
    <Metrics className="five-metrics" items={[["执行中",overview.active_count,"已通过独立复核"],["等待复核",overview.pending_review_count,"批准前不改变案件"],["逾期方案",overview.overdue_plan_count,"仍受保护与触达规则约束"],["7 日内到期",overview.due_soon_count,"按服务端日期口径"],["已履约完成",overview.completed_count,"回款账簿已覆盖方案总额"]]}/>
    <section className="repayment-principle"><ShieldCheck size={21}/><span><b>签约事实与资金事实分离</b><small>Agent 只能生成条款草案；外部签署摘要与独立管理员批准使方案生效，只有验签回款才能减少期次余额。</small></span></section>
    <Tabs value={tab} onChange={setTab} items={[{id:'all',label:'全部计划',count:overview.plans.length},{id:'pending_review',label:'待复核',count:overview.pending_review_count},{id:'active',label:'履约中',count:overview.active_count},{id:'overdue',label:'逾期',count:overview.overdue_plan_count},{id:'completed',label:'已完成',count:overview.completed_count}]}/>
    <div className="table-toolbar"><Search value={q} onChange={setQ} placeholder="搜索方案或案件编号…"/><span className="muted small">金额以分存储；退款只冲销原回款的既有分摊</span></div>
    {plans.length?<div className="table-scroll"><table className="data-table repayment-table"><thead><tr><th>方案 / 案件</th><th>条款</th><th>履约进度</th><th>下一期</th><th>状态</th><th>签署与复核证据</th><th/></tr></thead><tbody>{plans.map(plan=>{const next=plan.installments.find(row=>row.remaining_cents>0);const progress=Math.min(100,Math.round(plan.paid_cents/plan.total_cents*100));return <tr key={plan.id}><td><button className="cell-title" onClick={()=>a.openCase(plan.case_id,'协议')}>{plan.plan_id}</button><small>{plan.case_id} · 策略 v{plan.policy_version}</small></td><td className="number">{money(plan.total_cents/100)}<small>{plan.installment_count} 期 · 首付 {money(plan.down_payment_cents/100)}</small></td><td><div className="plan-progress"><span><i style={{width:`${progress}%`}}/></span><b>{progress}%</b></div><small>已付 {money(plan.paid_cents/100)} · 剩余 {money(plan.remaining_cents/100)}</small></td><td>{next?money(next.remaining_cents/100):'—'}<small>{next?`${next.due_date} · ${installmentLabel[next.status]||next.status}`:'全部期次已完成'}</small></td><td><Badge status={plan.status==='completed'?'completed':plan.status==='active'?'running':plan.status==='pending_review'?'paused':'neutral'}>{statusLabel[plan.status]||plan.status}</Badge>{plan.overdue_cents>0&&<small className="negative">逾期余额 {money(plan.overdue_cents/100)}</small>}</td><td><span className="evidence-inline"><FileLock size={15}/>{plan.agreement_reference}</span><small>摘要 {plan.evidence_digest.slice(0,10)}…</small></td><td>{plan.status==='pending_review'?<Button variant="primary" onClick={()=>a.setDialog({type:'plan-review',planId:plan.id})}>独立复核</Button>:<button className="text-link" onClick={()=>a.openCase(plan.case_id,'协议')}>查看期次 <ArrowUpRight size={13}/></button>}</td></tr>})}</tbody></table></div>:<Empty title="暂无匹配的履约计划" description="可调整筛选，或为符合策略且未受保护的案件提交签约提案。"/>}
    <div className="table-footer"><span>{a.backendStatus==='connected'?'服务端权威计划台账':a.hostedDemo?'在线交互沙箱':'离线演示台账'}</span><span>当前工作空间 · {plans.length} 条</span></div>
  </>;
}

export function RepaymentPlanCreateModal({onClose}){
  const a=useApp();
  const eligible=a.visibleCases.filter(row=>!row.blocked&&!a.repaymentOverview.plans.some(plan=>plan.case_id===row.case_id&&['pending_review','active'].includes(plan.status)));
  const initial=eligible.find(row=>row.case_id==='C008')||eligible[0];
  const defaults=caseItem=>{const total=Math.round((caseItem?.transfer_balance_yuan||10000)*.8);return {caseId:caseItem?.case_id||'',total,down:Math.round(total*.2),count:3,firstDue:'2026-09-20',agreementReference:`AGREEMENT-${caseItem?.case_id||'NEW'}-001`,agreementDigest:'',reason:'已取得外部签署回执，条款满足当前资产包授权策略',ack:false}};
  const [form,setForm]=useState(()=>defaults(initial));
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const selected=a.visibleCases.find(row=>row.case_id===form.caseId);
  const policy=selected?a.getPolicy(selected.package_id):{minSettlement:70,maxInstallments:6,minDownPayment:20};
  const update=(key,value)=>{setForm(old=>({...old,[key]:value}));setError('')};
  const selectCase=value=>setForm(defaults(a.visibleCases.find(row=>row.case_id===value)));
  const submit=async()=>{setBusy(true);setError('');try{await a.proposeRepaymentPlan(form);onClose()}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  const valid=selected&&Number(form.total)>0&&Number(form.total)<=selected.transfer_balance_yuan&&Number(form.total)>=selected.transfer_balance_yuan*policy.minSettlement/100&&Number(form.down)>0&&Number(form.down)<=Number(form.total)&&Number(form.down)>=Number(form.total)*policy.minDownPayment/100&&Number(form.count)>=1&&Number(form.count)<=policy.maxInstallments&&form.firstDue&&form.agreementReference.trim().length>=4&&/^[0-9a-fA-F]{64}$/.test(form.agreementDigest.trim())&&form.reason.trim().length>=8&&form.ack;
  return <Modal title="提交签约方案" subtitle="录入外部签署回执；本步骤只创建待复核提案。" onClose={onClose} wide footer={<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!valid||busy} onClick={submit}>{busy?'正在提交…':'提交独立复核'}</Button></>}>
    {eligible.length?<><div className="form-grid"><Field label="案件"><select value={form.caseId} onChange={event=>selectCase(event.target.value)}>{eligible.map(row=><option key={row.case_id} value={row.case_id}>{row.case_id} · {row.package_id} · {money(row.transfer_balance_yuan)}</option>)}</select></Field><Field label="签署证据编号"><input value={form.agreementReference} onChange={event=>update('agreementReference',event.target.value)} maxLength="120"/></Field><Field label="方案总额（元）"><input type="number" min="1" value={form.total} onChange={event=>update('total',event.target.value)}/></Field><Field label="首期金额（元）"><input type="number" min="1" value={form.down} onChange={event=>update('down',event.target.value)}/></Field><Field label="期数"><input type="number" min="1" max={policy.maxInstallments} value={form.count} onChange={event=>update('count',event.target.value)}/></Field><Field label="第一期应还日"><input type="date" value={form.firstDue} onChange={event=>update('firstDue',event.target.value)}/></Field></div>
      <Field label="协议 SHA-256 摘要"><input value={form.agreementDigest} onChange={event=>update('agreementDigest',event.target.value)} placeholder="由外部签约或存证系统返回的 64 位十六进制摘要" maxLength="64"/></Field>
      <KeyValue items={[["已核验债权余额",money(selected.transfer_balance_yuan)],["最低结算比例",`${policy.minSettlement}%`],["最低首付比例",`${policy.minDownPayment}%`],["最大期数",`${policy.maxInstallments} 期`]]}/>
      <Field label="提案依据"><textarea rows="3" value={form.reason} onChange={event=>update('reason',event.target.value)}/></Field>
      <div className="notice"><FileLock size={19}/><span><b>只提交外部证据编号与 SHA-256 摘要</b><small>协议正文不进入浏览器状态、API 载荷或审计日志；管理员批准时服务端重新校验案件、策略、委托期和全部金额。</small></span><Button onClick={()=>update('agreementDigest','d'.repeat(64))}>填入脱敏演示摘要</Button></div>
      <label className="check-agreement"><input type="checkbox" checked={form.ack} onChange={event=>update('ack',event.target.checked)}/><span>确认已取得外部签署证据；当前操作不会直接激活方案、修改案件状态或减少应还余额。</span></label></>:<Empty title="没有可提交方案的案件" description="保护案件、已有执行计划或待复核计划的案件均已排除。"/>}
    {error&&<div className="form-error" role="alert"><WarningCircle size={18}/>{error}</div>}
  </Modal>;
}

export function RepaymentPlanReviewModal({plan,onClose}){
  const a=useApp();
  const [note,setNote]=useState('已独立核验签署摘要、方案金额、分期期次与当前授权策略，结论一致。');
  const [ack,setAck]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const canReview=a.identity.role==='admin'&&plan.proposed_by!==a.identity.actor_id;
  const decide=async decision=>{setBusy(true);setError('');try{await a.decideRepaymentPlan(plan,decision,note);onClose()}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  return <Modal title={`独立复核 · ${plan.plan_id}`} subtitle={`${plan.case_id} · 方案 v${plan.version} · 提案人 ${plan.proposed_by}`} onClose={onClose} wide footer={<><Button onClick={onClose}>关闭</Button><div className="footer-actions"><Button disabled={!canReview||!ack||busy} onClick={()=>decide('reject')}>驳回提案</Button><Button variant="primary" disabled={!canReview||!ack||busy} onClick={()=>decide('approve')}>{busy?'正在复核…':'批准并激活'}</Button></div></>}>
    <div className="plan-review-hero"><span><CalendarCheck size={27}/></span><div><small>方案总额</small><strong>{money(plan.total_cents/100)}</strong><p>{plan.installment_count} 期 · 首期 {money(plan.down_payment_cents/100)} · 签署 {plan.signed_at.slice(0,10)}</p></div><Badge status="paused">待独立复核</Badge></div>
    <KeyValue items={[["债权余额",money(plan.claim_balance_cents/100)],["结算比例",`${(plan.total_cents/plan.claim_balance_cents*100).toFixed(1)}%`],["签署证据",plan.agreement_reference],["协议摘要",`${plan.agreement_digest.slice(0,16)}…`],["策略版本",`v${plan.policy_version}`],["证据快照",`${plan.evidence_digest.slice(0,16)}…`]]}/>
    <h3 className="section-label"><ClockCountdown size={19}/>期次计划</h3><div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>期次</th><th>应还日</th><th>金额</th><th>当前状态</th></tr></thead><tbody>{plan.installments.map(row=><tr key={row.installment_id}><td>第 {row.installment_no} 期</td><td>{row.due_date}</td><td className="number">{money(row.due_cents/100)}</td><td><Badge status="neutral">批准后生效</Badge></td></tr>)}</tbody></table></div>
    <Field label="独立复核结论"><textarea rows="3" value={note} onChange={event=>setNote(event.target.value)}/></Field>
    {!canReview&&<div className="notice warning"><WarningCircle size={19}/><span><b>{plan.proposed_by===a.identity.actor_id?'不能审批自己提交的方案':'当前角色不能审批方案'}</b><small>必须由另一名工作空间管理员重新核对签署证据与条款。</small></span></div>}
    <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本人不是提案人，已独立核验签署证据和服务端策略快照；批准后仅验签回款能够更新期次余额。</span></label>
    {error&&<div className="form-error" role="alert"><WarningCircle size={18}/>{error}</div>}
  </Modal>;
}

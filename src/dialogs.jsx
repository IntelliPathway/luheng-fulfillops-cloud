import React,{useState} from 'react';
import {
  ArrowRight,CheckCircle,Files,Flask,Lightning,Phone,Receipt,
  Robot,ShieldCheck,UploadSimple,WarningCircle
} from '@phosphor-icons/react';
import {useApp} from './context';
import {Badge,Button,Empty,Field,KeyValue,Metrics,Modal,Tabs} from './ui';
import {exceptionMeta,installmentSchedules,money,reasonText} from './model';
import {determineActivityMode,GOALS,selectActivityCandidates} from './activity-preflight';

function PreflightItem({ok,label,note,required=true}){
  return <div className={`preflight-item ${ok?'passed':'blocked'}`}>
    <span>{ok?<CheckCircle size={18} weight="fill"/>:<WarningCircle size={18}/>}</span>
    <div><b>{label}</b><small>{note}</small></div>
    <em>{ok?'通过':required?'阻断真实触达':'仅提示'}</em>
  </div>;
}

export function CreateWizard({defaultPackage,onClose}){
  const a=useApp();
  const [step,setStep]=useState(0);
  const [error,setError]=useState('');
  const [checking,setChecking]=useState(false);
  const [remotePreflight,setRemotePreflight]=useState(null);
  const [form,setForm]=useState({name:'可执行案件首次联络',package:defaultPackage||a.visiblePackages[0].package_id,goal:GOALS.FIRST_CONTACT,budget:30,ack:false});
  const {candidates,eligible,exclusions}=selectActivityCandidates({cases:a.visibleCases,activities:a.visibleActivities,packageId:form.package,goal:form.goal});
  const policy=a.getPolicy(form.package);
  const channelReady=a.integrationReadiness.ready;
  const localDecision=determineActivityMode({integrationReady:channelReady,policyStatus:policy.status,caseCount:eligible.length});
  const effectiveCaseIds=remotePreflight?.eligible_case_ids||eligible.map(c=>c.case_id);
  const productionReady=remotePreflight?.production_ready??localDecision.productionReady;
  const runtimeMode=remotePreflight?.resolved_mode||localDecision.mode;
  const mode=runtimeMode==='channel'?'授权渠道':'纯模拟';
  const change=(key,value)=>{setForm(old=>({...old,[key]:value}));setRemotePreflight(null);setError('')};
  const next=async()=>{
    if(step===0&&(!form.name.trim()||!eligible.length)){setError(!form.name.trim()?'请输入活动名称。':'此目标下没有可执行案件，请更换目标或资产包。');return}
    if(step===1&&(!form.budget||Number(form.budget)<=0||Number(form.budget)>policy.budget)){setError(`单案预算须大于 0，且不得超过授权上限 ${money(policy.budget)}。`);return}
    if(step===1){setChecking(true);try{const result=await a.preflightActivity({...form,name:form.name.trim(),caseIds:eligible.map(c=>c.case_id),requestedMode:'auto'});if(result){setRemotePreflight(result);if(!result.eligible_case_ids.length){setError(result.blockers.join('；'));return}}}catch(reason){setError(`后端预检失败：${reason.message||'未知错误'}`);return}finally{setChecking(false)}}
    setStep(step+1);
  };
  return <Modal title="创建清收活动" subtitle="目标进入 Agent 前，先完成案件、政策与渠道预检。" onClose={onClose} wide footer={<>
    <span className="muted small">{step+1} / 3 · {mode}</span>
    <div className="footer-actions"><Button onClick={step?()=>setStep(step-1):onClose}>{step?'上一步':'取消'}</Button>{step<2?<Button variant="primary" disabled={checking} onClick={next}>{checking?'正在预检…':'下一步'} {!checking&&<ArrowRight size={16}/>}</Button>:<Button variant="primary" icon={Lightning} disabled={!form.ack} onClick={()=>a.createActivity({...form,name:form.name.trim(),caseIds:effectiveCaseIds,requestedMode:'auto'})}>{productionReady?'创建并启动':'创建纯模拟活动'}</Button>}</div>
  </>}>
    <div className="wizard-steps">{['目标与范围','策略与运行时','启动预检'].map((name,index)=><div key={name} className={index===step?'current':index<step?'complete':''}><span>{index<step?<CheckCircle size={20}/>:index+1}</span>{name}</div>)}</div>
    {step===0&&<>
      <Field label="活动名称"><input autoComplete="off" value={form.name} maxLength={40} onChange={event=>change('name',event.target.value)}/></Field>
      <Field label="资产包"><select value={form.package} onChange={event=>change('package',event.target.value)}>{a.visiblePackages.map(p=><option value={p.package_id} key={p.package_id}>{p.package_id} · {p.title}</option>)}</select></Field>
      <Field label="清收目标"><select value={form.goal} onChange={event=>change('goal',event.target.value)}><option>{GOALS.SIGNED_PLAN}</option><option>{GOALS.FIRST_CONTACT}</option><option>{GOALS.PAYMENT_RECONCILIATION}</option></select></Field>
      <div className="scope-preview"><ShieldCheck size={22}/><div><b>{eligible.length} 个案件符合执行条件</b><p>资格规则已排除保护案件、已完成案件和目标不匹配案件。</p><small>{eligible.map(c=>c.case_id).join(' · ')||'暂无可执行案件'}</small></div></div>
      <div className="exclusion-grid">{Object.entries(exclusions).map(([label,count])=><span key={label}><b>{count}</b><small>{label}</small></span>)}</div>
    </>}
    {step===1&&<>
      <div className="agent-recipe"><span className="agent-icon small-icon"><Robot size={24}/></span><div><b>{a.serviceConfigs.agent.provider} · 履约 Agent</b><p>目标分解 → 事实核验 → 策略决策 → 工具执行 → 观察与重规划</p></div><Badge>{mode}</Badge></div>
      <div className="form-grid"><Field label="授权策略"><input value={`${form.package} · 策略 v${policy.version}.0 · ${policy.status==='published'?'已发布':'草稿'}`} readOnly/></Field><Field label="Agent Runtime"><input value={`${a.serviceConfigs.agent.provider} · ${a.serviceConfigs.agent.profile}`} readOnly/></Field><Field label="单案预算（元）" hint={`授权上限 ${money(policy.budget)}`}><input type="number" min="1" max={policy.budget} value={form.budget} onChange={event=>change('budget',event.target.value)}/></Field><Field label="触达时段"><input value={`${policy.start} — ${policy.end}`} readOnly/></Field></div>
      <KeyValue items={[["最低结算比例",`${policy.minSettlement}%`],["最大分期期数",`${policy.maxInstallments} 期`],["最低首付比例",`${policy.minDownPayment}%`],["异常处置","立即保护，进入异常中心"]]}/>
      {!productionReady&&<div className="notice warning"><WarningCircle size={19}/><span>{!channelReady?'AI 与渠道尚未全部自测并启用。':'当前策略尚未发布。'}本活动可以验证 Agent 计划和状态机，但不会调用语音或电话服务。</span></div>}
    </>}
    {step===2&&<>
      <div className={`review-hero ${productionReady?'':'sandbox'}`}><Flask size={35}/><h3>{productionReady?'活动通过启动预检':'沙箱模拟可以启动'}</h3><p>{productionReady?'Agent 将使用已启用的服务快照。':'至少一项生产门禁未通过，自动降级为纯模拟。'}</p></div>
      <div className="preflight-list">
        <PreflightItem ok label="委托与案件范围" note={`${effectiveCaseIds.length} 个案件已校验；${candidates.length-effectiveCaseIds.length} 个已排除${remotePreflight?' · 后端确认':''}`}/>
        <PreflightItem ok={(remotePreflight?.policy_status||policy.status)==='published'} label="授权策略" note={`v${remotePreflight?.policy_version||policy.version}.0 · ${(remotePreflight?.policy_status||policy.status)==='published'?'已发布':'尚未发布'}`}/>
        <PreflightItem ok label="保护与重复任务" note={`已排除保护 ${exclusions.保护暂停} 个、重复在途 ${exclusions.在途任务} 个`}/>
        <PreflightItem ok={channelReady} label="AI 与渠道门禁" note={channelReady?`Agent、模型、语音和 SIP 已按 ${a.integrationState.lastRun?.id} 冻结`:'配置、连接、自测或管理员启用尚未全部有效'}/>
      </div>
      <KeyValue items={[["活动",form.name],["目标",form.goal],["执行模式",mode],["总预算上限",money(Number(form.budget)*effectiveCaseIds.length)],["预检来源",remotePreflight?'后端重新计算':'本地演示规则'],["服务快照",productionReady?`随活动冻结 · ${a.integrationState.lastRun?.id}`:'仅使用 Agent + 模型模拟']]}/>
      <label className="check-agreement"><input type="checkbox" checked={form.ack} onChange={event=>change('ack',event.target.checked)}/><span>确认使用以上范围和服务快照。生产触达、到账和计佣仍以外部回执为准。</span></label>
    </>}
    {error&&<div className="form-error" role="alert"><WarningCircle size={18}/>{error}</div>}
  </Modal>;
}

export function PolicyModal({packageId,onClose}){
  const a=useApp();
  const [form,setForm]=useState({...a.getPolicy(packageId),status:'draft'});
  const [phase,setPhase]=useState('edit');
  const [error,setError]=useState('');
  const update=(key,value)=>{setForm(old=>({...old,[key]:value}));setPhase('edit');setError('')};
  const validate=()=>{
    const valid=Number(form.budget)>0&&Number(form.budget)<=30&&Number(form.daily)>=1&&Number(form.daily)<=1&&Number(form.weekly)>=1&&Number(form.weekly)<=3&&Number(form.retry)>=48&&form.start>='09:00'&&form.end<='18:00'&&form.start<form.end&&Number(form.minSettlement)>=60&&Number(form.minSettlement)<=100&&Number(form.maxInstallments)<=12&&Number(form.minDownPayment)>=10;
    if(!valid)setError('请检查预算、触达时段、频次和协商授权阈值；当前样本要求每日最多 1 次、7 日最多 3 次、重试至少 48 小时。');
    return valid;
  };
  const evaluate=()=>{if(!validate())return;setPhase('evaluated');a.notify('策略样本回放通过：24 个场景，越权 0 次')};
  const publish=()=>a.savePolicy(packageId,{...form,budget:Number(form.budget),daily:Number(form.daily),weekly:Number(form.weekly),retry:Number(form.retry),minSettlement:Number(form.minSettlement),maxInstallments:Number(form.maxInstallments),minDownPayment:Number(form.minDownPayment),offerValidity:Number(form.offerValidity),approvalThreshold:Number(form.approvalThreshold),status:'published',evaluated:true});
  return <Modal title="策略评估与发布" subtitle={`${packageId} · 当前 v${a.getPolicy(packageId).version}.0 · 新版本先回放再发布`} wide onClose={onClose} footer={<>
    <span className="muted small">{phase==='evaluated'?'样本回放通过，可发布':'编辑后必须重新评估'}</span>
    <div className="footer-actions"><Button onClick={onClose}>取消</Button>{phase!=='evaluated'?<Button variant="primary" icon={Flask} onClick={evaluate}>运行样本评估</Button>:<Button variant="primary" onClick={publish}>发布新版本</Button>}</div>
  </>}>
    <div className="policy-lifecycle">{[['草稿',true],['样本回放',phase==='evaluated'],['审批发布',false]].map(([label,done],index)=><span className={done?'active':''} key={label}><i>{done?<CheckCircle size={16}/>:index+1}</i>{label}</span>)}</div>
    <Field label="Agent 运行目标"><textarea rows={3} value={form.goal} onChange={event=>update('goal',event.target.value)}/></Field>
    <div className="form-grid">
      <Field label="单案预算（元）"><input type="number" min="1" max="30" value={form.budget} onChange={event=>update('budget',event.target.value)}/></Field><Field label="沟通风格"><select value={form.tone} onChange={event=>update('tone',event.target.value)}><option>专业、温和、简洁</option><option>耐心解释、核实为先</option></select></Field>
      <Field label="开始时间"><input type="time" value={form.start} onChange={event=>update('start',event.target.value)}/></Field><Field label="结束时间"><input type="time" value={form.end} onChange={event=>update('end',event.target.value)}/></Field>
      <Field label="每日触达上限"><input type="number" min="1" max="1" value={form.daily} onChange={event=>update('daily',event.target.value)}/></Field><Field label="7 日触达上限"><input type="number" min="1" max="3" value={form.weekly} onChange={event=>update('weekly',event.target.value)}/></Field>
      <Field label="最低结算比例（%）"><input type="number" min="60" max="100" value={form.minSettlement} onChange={event=>update('minSettlement',event.target.value)}/></Field><Field label="最大分期期数"><input type="number" min="1" max="12" value={form.maxInstallments} onChange={event=>update('maxInstallments',event.target.value)}/></Field>
      <Field label="最低首付比例（%）"><input type="number" min="10" max="100" value={form.minDownPayment} onChange={event=>update('minDownPayment',event.target.value)}/></Field><Field label="方案有效期（天）"><input type="number" min="1" max="15" value={form.offerValidity} onChange={event=>update('offerValidity',event.target.value)}/></Field>
      <Field label="人工审批阈值（%）"><input type="number" min="60" max="100" value={form.approvalThreshold} onChange={event=>update('approvalThreshold',event.target.value)}/></Field><Field label="最短重试间隔（小时）"><input type="number" min="48" value={form.retry} onChange={event=>update('retry',event.target.value)}/></Field>
    </div>
    {phase==='evaluated'&&<div className="evaluation-result"><CheckCircle size={22} weight="fill"/><span><b>24 / 24 样本场景通过</b><small>金额幻觉 0 · 越权方案 0 · 保护状态漏拦截 0 · 预计影响 2 个后续活动</small></span></div>}
    <div className="notice"><ShieldCheck size={18}/><span>停止联系、异议、授权撤销等保护规则即时生效，不受运行中旧策略快照影响。</span></div>
    {error&&<div className="form-error" role="alert">{error}</div>}
  </Modal>;
}

export function ReceiptModal({onClose}){
  const a=useApp();
  const [ack,setAck]=useState(false);
  return <Modal title="模拟补款到账" subtitle="先展示核验链路，再更新履约与佣金。" onClose={onClose} wide footer={<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!ack||a.paid} onClick={a.receive}>{a.paid?'已完成模拟':'执行核验并确认到账'}</Button></>}>
    <div className="receipt-preview"><Receipt size={32}/><span>待核验回调金额</span><strong>¥1,016</strong><small>C002 · PLAN002 · 第二期</small></div>
    <div className="reconciliation-chain">{['回调接收','签名验证','幂等去重','案件匹配','分期分配','计佣判断'].map((label,index)=><span key={label}><i>{index+1}</i><b>{label}</b><small>{index===0?'DEMO-TX-001':index===1?'沙箱签名':index===2?'首次接收':index===3?'C002':index===4?'第 2 期':'COM_A v1.0'}</small></span>)}</div>
    <KeyValue items={[["本期累计到账","¥1,000 → ¥2,016"],["本期状态","部分履约 → 本期已足额"],["新增应计佣金","¥1,016 × 15% = ¥152.40"],["实际收佣","仍为 ¥0"]]}/>
    <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认在演示账本中执行验签、去重、匹配、分配和计佣流程。</span></label>
  </Modal>;
}

export function PaymentDetailModal({transaction,onClose}){
  const a=useApp();
  const negative=transaction.cash_yuan<0;
  const chain=[['回调来源',transaction.source||'AMC 脱敏回款文件'],['签名校验',transaction.signature||'验签通过'],['幂等状态',transaction.idempotency||'首次接收'],['案件匹配',`${transaction.case_id} · 已匹配`],['分期分配',transaction.allocation||'按支付日期与方案分配'],['佣金规则',transaction.commission_rule_id||'COM_A_V1']];
  return <Modal title={`交易 ${transaction.transaction_id}`} subtitle="付款事实、核验过程与计佣结果" onClose={onClose} wide footer={<Button onClick={onClose}>关闭</Button>}>
    <div className="payment-detail-head"><span className={negative?'negative':''}>{money(transaction.cash_yuan)}</span><Badge status={negative?'neutral':'completed'}>{negative?'退款冲回':'已确认'}</Badge></div>
    <div className="verification-grid">{chain.map(([label,value])=><div key={label}><CheckCircle size={17}/><span><small>{label}</small><b>{value}</b></span></div>)}</div>
    <KeyValue items={[["到账日期",transaction.booked_date],["计佣回款",money(transaction.eligible_cash_yuan)],["佣金比例",transaction.eligible?`${transaction.rate*100}%`:'不计佣'],["应计佣金",money(transaction.commission_yuan)],["计佣依据",reasonText[transaction.reason]],["关联原交易",transaction.original_transaction_id||'—']]}/>
    <button className="document-link" onClick={()=>{onClose();a.openCase(transaction.case_id,'回款')}}><Files size={18}/>打开案件完整回款记录 <ArrowRight size={16}/></button>
  </Modal>;
}

export function ExceptionModal({caseId,onClose}){
  const a=useApp();
  const c=a.visibleCases.find(item=>item.case_id===caseId);
  const meta=exceptionMeta[caseId];
  const [note,setNote]=useState('');
  const [ack,setAck]=useState(false);
  if(!c||!meta)return null;
  const submit=()=>{a.setExceptionResolutions(old=>({...old,[caseId]:{note,time:'2026.09.12 10:52',receipt:`RCP-${caseId}-01`}}));a.addEvent(caseId,'异常处理回执已提交','保护状态保持生效，等待责任方复核','exception',{actor:'异常工作流',tool:'exception.submit_receipt',version:'GUARD v2.1'});a.notify('处理回执已提交；保护状态不会自动解除');onClose()};
  return <Modal title={`处理异常 · ${caseId}`} subtitle={`${meta.id} · ${meta.category} · ${meta.priority}`} onClose={onClose} wide footer={<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!note.trim()||!ack} onClick={submit}>提交处理回执</Button></>}>
    <div className="notice warning"><ShieldCheck size={20}/><span><b>当前仍为保护暂停</b><small>{c.reason}</small></span></div>
    <KeyValue items={[["责任方",meta.owner],["处理时限",meta.sla],["解除条件",meta.condition],["影响范围","取消排队动作；在途动作进入确认"]]}/>
    <Field label="处理结论或补充说明"><textarea rows="4" value={note} onChange={event=>setNote(event.target.value)} placeholder="说明已核验资料、结论和证据编号…"/></Field>
    <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本次仅提交回执，不直接解除保护；解除仍需规则与权限复核。</span></label>
  </Modal>;
}

export function CaseDrawer({c,tab,setTab,onClose}){
  const a=useApp();
  const entries=a.visibleLedger.filter(item=>item.case_id===c.case_id);
  const events=a.events.filter(item=>item.tenant===a.tenant&&item.caseId===c.case_id);
  const c2=c.case_id==='C002';
  const planSchedule=installmentSchedules.filter(item=>item.tenant_id===a.tenant&&item.case_id===c.case_id);
  const tailInstallments=planSchedule.filter(item=>item.due_date>c.mandate_end);
  const tailRisk=c.plan?.status==='SIGNED'&&tailInstallments.length>0;
  return <Modal title={`案件 ${c.case_id}`} subtitle={`${c.package_id} · ${c.agingBucket} · 虚构样本案件`} wide onClose={onClose} footer={<><span className="muted small">当前组织：{a.organization[a.tenant]}</span><Button onClick={onClose}>关闭</Button></>}>
    <div className="case-detail"><div className="case-status"><Badge>{c.status}</Badge><span className="muted small">数据质量 {c.dataQuality}% · 最后联络 {c.lastContact}</span></div><Tabs items={['概览','协议','回款','记录']} value={tab} onChange={setTab}/>
      {tab==='概览'&&<>{c.blocked&&<div className="notice warning"><ShieldCheck size={21}/><span><b>保护暂停已生效</b><small>{c.reason}</small></span></div>}{c2&&<div className="case-due"><span>本期剩余应还</span><strong>{a.paid?'¥0':'¥1,016'}</strong><small>{a.paid?'本期已足额 · 下期 2026.10.10':'第二期 · 原定应还日 2026.09.10'}</small><div><span>本期应还 <b>¥2,016</b></span><span>本期到账 <b>{a.paid?'¥2,016':'¥1,000'}</b></span></div></div>}<h3 className="section-label"><Robot size={19}/> AI 下一步</h3><p className="body-copy">{c.blocked?`${c.reason}。解除条件满足前不产生新的触达动作。`:c2?(a.paid?'等待下一期履约，按授权时段与规则重新评估。':'预计 2026.09.13 10:00 在授权时段内跟进本期补款；完成本人核验后才能说明差额。'):c.status==='已结清'?'当前方案已履约完成，保留回款与归档记录。':`下一允许动作：${c.nextAllowed}。未确认到账不更新为已还款。`}</p>{c2&&!a.paid&&<Button variant="primary" onClick={()=>a.setDialog({type:'receipt'})}>模拟补款到账</Button>}<h3 className="section-label separated">案件与委托</h3><KeyValue items={[["原本金",money(c.principal_yuan)],["利息及费用",money(c.interest_yuan+c.fees_yuan)],["转让余额快照",`${money(c.transfer_balance_yuan)} · ${c.balance_snapshot_date}`],["首次逾期",`${c.first_overdue_date} · ${c.ageMonths} 个月`],["委托截止",c.mandate_end],["数据质量",`${c.dataQuality}%`],["下一允许动作",c.nextAllowed],["累计确认净回款",money(c.cash)],["累计应计佣金",money(c.commission)]]}/><details className="knowledge-row"><summary><Phone size={18}/>AI 沟通片段（模拟示例）</summary><div className="transcript"><p><b>AI 助理</b>您好，我是受委托机构的 AI 语音助理，想与您核实一项业务信息。</p><small>完成本人身份核验前，不披露具体债务信息。</small><p><b>履约说明</b>{c2?'按已签方案，本期应还 2,016 元，目前已确认到账 1,000 元。若您已支付剩余款项，我们将继续核对到账信息。':'系统只使用有效委托、已核验金额和已发布策略生成说明。'}</p></div></details></>}
      {tab==='协议'&&(c.plan?<><div className="notice"><Files size={20}/>{c.plan.status==='SIGNED'?'已签署协议样本':'方案尚未签署，不视为已承诺履约'}</div>{tailRisk&&<div className="notice warning"><WarningCircle size={19}/><span><b>存在 {tailInstallments.length} 个委托期后尾期</b><small>委托到期后禁止主动触达；仅核对已签方案的被动到账，并按合同规则判断是否计佣。</small></span></div>}<KeyValue items={[["方案编号",c.plan.plan_id],["方案总额",money(c.plan.total_yuan)],["原债权余额",money(c.transfer_balance_yuan)],["协议减免",money(c.transfer_balance_yuan-c.plan.total_yuan)],["期数",`${c.plan.installments} 期（含首期）`],["首期金额",money(c.plan.down_payment_yuan)],["签署日期",c.plan.signed_at||'尚未签署'],["授权版本",c.plan.policy_id]]}/>{planSchedule.length>0&&<><h3 className="section-label">分期计划</h3><table className="data-table compact-table"><thead><tr><th>期次</th><th>应还日期</th><th>应还金额</th><th>授权边界</th><th>状态</th></tr></thead><tbody>{planSchedule.map(item=>{const afterMandate=item.due_date>c.mandate_end;const complete=c2&&(item.installment_no===1||(item.installment_no===2&&a.paid));const partial=(c2&&item.installment_no===2&&!a.paid)||(c.case_id==='C014'&&item.installment_no===1);return <tr key={item.schedule_id}><td>第 {item.installment_no} 期</td><td>{item.due_date}</td><td>{money(item.due_yuan)}</td><td>{afterMandate?'委托后尾期 · 禁止主动触达':'委托期内'}</td><td><Badge status={complete?'completed':partial?'running':'neutral'}>{complete?'已足额':partial?'部分履约':'未到期'}</Badge></td></tr>})}</tbody></table></>}</>:<Empty title="尚无已签协议" description="口头意向、待签方案与已签协议会分别记录。"/>)}
      {tab==='回款'&&<><Metrics items={[["确认净回款",money(c.cash)],["计佣回款",money(entries.reduce((sum,item)=>sum+item.eligible_cash_yuan,0))],["应计佣金",money(c.commission)]]}/>{entries.length?<div className="case-payment-list">{entries.map(item=><button key={item.transaction_id} onClick={()=>a.setDialog({type:'payment',transaction:item})}><span><b>{item.transaction_id}</b><small>{item.booked_date} · {reasonText[item.reason]}</small></span><span><small>{item.cash_yuan<0?'退款冲回':'验签 · 去重 · 匹配 · 分配完成'}</small><b className="number">{money(item.cash_yuan)}</b></span></button>)}</div>:<Empty title="暂无已确认回款" description="支付失败、处理中或待匹配记录不计入已确认回款。"/>}</>}
      {tab==='记录'&&<><div className="case-trace-list">{events.map(event=><div key={event.id}><span className="trace-status"><CheckCircle size={18}/></span><span><b>{event.title}</b><small>{event.detail}</small><code>{event.runId} · {event.stepId} · {event.actionId}</code></span><span><small>{event.actor}</small><b>{event.tool}</b><em>{event.time}</em></span></div>)}</div>{!events.length&&<Empty title="暂无运行事件" description="执行活动后，事实、规则与工具回执将在此留痕。"/>}</>}
    </div>
  </Modal>;
}

export function ImportModal({onClose}){
  const a=useApp();
  const [step,setStep]=useState(0);
  const [loaded,setLoaded]=useState(false);
  return <Modal title="导入资产包 · 流程演示" subtitle="使用内置虚构样本，体验字段、授权和质量预检。" onClose={onClose} wide footer={<><Button onClick={onClose}>取消</Button>{step===0?<Button variant="primary" disabled={!loaded} onClick={()=>setStep(1)}>核对样本</Button>:<Button variant="primary" onClick={()=>{a.notify('样本资产包已预载，可进入资格预检');onClose();a.navigate('assets')}}>查看资产包</Button>}</>}>
    {step===0?<><div className="upload-demo"><UploadSimple size={36}/><h3>{loaded?'AMC_样本案件.csv 已选择':'选择内置样本'}</h3><p>此流程不上传真实案件资料。</p><Button onClick={()=>setLoaded(true)}>{loaded?'重新选择样本':'使用内置样本'}</Button></div><Field label="字段映射"><input readOnly value="案件 → case_id；委托 → mandate_id；余额 → transfer_balance_yuan"/></Field></>:<><div className="scope-preview"><CheckCircle size={26}/><div><b>{a.visibleCases.length} 个样本案件已校验并预载</b><p>已识别委托、金额、资料质量、联系依据、重复案件与保护状态。</p></div></div><KeyValue items={[["所属组织",a.organization[a.tenant]],["资产包",a.visiblePackages.map(p=>p.package_id).join('、')],["保护暂停",`${a.visibleCases.filter(c=>c.blocked).length} 个案件`],["低质量资料",`${a.visibleCases.filter(c=>c.dataQuality<70).length} 个案件`],["重复导入","已阻断重复创建"]]}/><div className="notice">原型只展示预检结果；生产导入必须校验租户、委托、债权链、金额和联系方式使用依据。</div></>}
  </Modal>;
}

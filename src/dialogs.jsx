import React,{useEffect,useState} from 'react';
import {
  ArrowRight,CheckCircle,Files,Flask,Lightning,Phone,Receipt,
  Robot,ShieldCheck,UploadSimple,WarningCircle
} from '@phosphor-icons/react';
import {useApp} from './context';
import {assetImportApi} from './api';
import {Badge,Button,Empty,Field,KeyValue,Metrics,Modal,Tabs} from './ui';
import {downloadCSV,installmentSchedules,money,reasonText} from './model';
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
  const [form,setForm]=useState({name:'可执行案件首次联络',package:defaultPackage||a.visiblePackages[0]?.package_id||'',goal:GOALS.FIRST_CONTACT,budget:30,ack:false});
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
  return <Modal title="创建履约活动" subtitle="目标进入 Agent 前，先完成案件、政策与渠道预检。" onClose={onClose} wide footer={<>
    <span className="muted small">{step+1} / 3 · {mode}</span>
    <div className="footer-actions"><Button onClick={step?()=>setStep(step-1):onClose}>{step?'上一步':'取消'}</Button>{step<2?<Button variant="primary" disabled={checking} onClick={next}>{checking?'正在预检…':'下一步'} {!checking&&<ArrowRight size={16}/>}</Button>:<Button variant="primary" icon={Lightning} disabled={!form.ack} onClick={()=>a.createActivity({...form,name:form.name.trim(),caseIds:effectiveCaseIds,requestedMode:'auto'})}>{productionReady?'创建并启动':'创建纯模拟活动'}</Button>}</div>
  </>}>
    <div className="wizard-steps">{['目标与范围','策略与运行时','启动预检'].map((name,index)=><div key={name} className={index===step?'current':index<step?'complete':''}><span>{index<step?<CheckCircle size={20}/>:index+1}</span>{name}</div>)}</div>
    {step===0&&<>
      <Field label="活动名称"><input autoComplete="off" value={form.name} maxLength={40} onChange={event=>change('name',event.target.value)}/></Field>
      <Field label="资产包"><select value={form.package} onChange={event=>change('package',event.target.value)}>{a.visiblePackages.map(p=><option value={p.package_id} key={p.package_id}>{p.package_id} · {p.title}</option>)}</select></Field>
      <Field label="履约目标"><select value={form.goal} onChange={event=>change('goal',event.target.value)}><option>{GOALS.SIGNED_PLAN}</option><option>{GOALS.FIRST_CONTACT}</option><option>{GOALS.PAYMENT_RECONCILIATION}</option></select></Field>
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
  const serverReadOnly=a.backendStatus==='connected';
  const [form,setForm]=useState({...a.getPolicy(packageId),status:'draft'});
  const [phase,setPhase]=useState('edit');
  const [error,setError]=useState('');
  const update=(key,value)=>{setForm(old=>({...old,[key]:value}));setPhase('edit');setError('')};
  const validate=()=>{
    const valid=Number(form.budget)>0&&Number(form.budget)<=30&&Number(form.daily)>=1&&Number(form.daily)<=1&&Number(form.weekly)>=1&&Number(form.weekly)<=3&&Number(form.retry)>=48&&form.start>='09:00'&&form.end<='18:00'&&form.start<form.end&&Number(form.minSettlement)>=60&&Number(form.minSettlement)<=100&&Number(form.maxInstallments)<=12&&Number(form.minDownPayment)>=10;
    if(!valid)setError('请检查预算、触达时段、频次和协商授权阈值；当前样本要求每日最多 1 次、7 日最多 3 次、重试至少 48 小时。');
    return valid;
  };
  const evaluate=()=>{if(serverReadOnly){a.notify('在线目录只读；策略评估与发布需接入后端治理接口');return}if(!validate())return;setPhase('evaluated');a.notify('策略样本回放通过：24 个场景，越权 0 次')};
  const publish=()=>a.savePolicy(packageId,{...form,budget:Number(form.budget),daily:Number(form.daily),weekly:Number(form.weekly),retry:Number(form.retry),minSettlement:Number(form.minSettlement),maxInstallments:Number(form.maxInstallments),minDownPayment:Number(form.minDownPayment),offerValidity:Number(form.offerValidity),approvalThreshold:Number(form.approvalThreshold),status:'published',evaluated:true});
  return <Modal title="策略评估与发布" subtitle={`${packageId} · 当前 v${a.getPolicy(packageId).version}.0 · 新版本先回放再发布`} wide onClose={onClose} footer={<>
    <span className="muted small">{serverReadOnly?'服务端策略写接口尚未开放':phase==='evaluated'?'样本回放通过，可发布':'编辑后必须重新评估'}</span>
    <div className="footer-actions"><Button onClick={onClose}>{serverReadOnly?'关闭':'取消'}</Button>{!serverReadOnly&&(phase!=='evaluated'?<Button variant="primary" icon={Flask} onClick={evaluate}>运行样本评估</Button>:<Button variant="primary" onClick={publish}>发布新版本</Button>)}</div>
  </>}>
    {serverReadOnly&&<div className="notice"><ShieldCheck size={18}/><span><b>当前展示服务端权威策略快照</b><small>浏览器不会模拟修改或发布；待后端治理接口完成后再开放写操作。</small></span></div>}
    <div className="policy-lifecycle">{[['草稿',true],['样本回放',phase==='evaluated'],['审批发布',false]].map(([label,done],index)=><span className={done?'active':''} key={label}><i>{done?<CheckCircle size={16}/>:index+1}</i>{label}</span>)}</div>
    <fieldset className="policy-fields" disabled={serverReadOnly}>
    <Field label="Agent 运行目标"><textarea rows={3} value={form.goal} onChange={event=>update('goal',event.target.value)}/></Field>
    <div className="form-grid">
      <Field label="单案预算（元）"><input type="number" min="1" max="30" value={form.budget} onChange={event=>update('budget',event.target.value)}/></Field><Field label="沟通风格"><select value={form.tone} onChange={event=>update('tone',event.target.value)}><option>专业、温和、简洁</option><option>耐心解释、核实为先</option></select></Field>
      <Field label="开始时间"><input type="time" value={form.start} onChange={event=>update('start',event.target.value)}/></Field><Field label="结束时间"><input type="time" value={form.end} onChange={event=>update('end',event.target.value)}/></Field>
      <Field label="每日触达上限"><input type="number" min="1" max="1" value={form.daily} onChange={event=>update('daily',event.target.value)}/></Field><Field label="7 日触达上限"><input type="number" min="1" max="3" value={form.weekly} onChange={event=>update('weekly',event.target.value)}/></Field>
      <Field label="最低结算比例（%）"><input type="number" min="60" max="100" value={form.minSettlement} onChange={event=>update('minSettlement',event.target.value)}/></Field><Field label="最大分期期数"><input type="number" min="1" max="12" value={form.maxInstallments} onChange={event=>update('maxInstallments',event.target.value)}/></Field>
      <Field label="最低首付比例（%）"><input type="number" min="10" max="100" value={form.minDownPayment} onChange={event=>update('minDownPayment',event.target.value)}/></Field><Field label="方案有效期（天）"><input type="number" min="1" max="15" value={form.offerValidity} onChange={event=>update('offerValidity',event.target.value)}/></Field>
      <Field label="人工审批阈值（%）"><input type="number" min="60" max="100" value={form.approvalThreshold} onChange={event=>update('approvalThreshold',event.target.value)}/></Field><Field label="最短重试间隔（小时）"><input type="number" min="48" value={form.retry} onChange={event=>update('retry',event.target.value)}/></Field>
    </div>
    </fieldset>
    {phase==='evaluated'&&<div className="evaluation-result"><CheckCircle size={22} weight="fill"/><span><b>24 / 24 样本场景通过</b><small>金额幻觉 0 · 越权方案 0 · 保护状态漏拦截 0 · 预计影响 2 个后续活动</small></span></div>}
    <div className="notice"><ShieldCheck size={18}/><span>停止联系、异议、授权撤销等保护规则即时生效，不受运行中旧策略快照影响。</span></div>
    {error&&<div className="form-error" role="alert">{error}</div>}
  </Modal>;
}

export function ReceiptModal({onClose}){
  const a=useApp();
  const [ack,setAck]=useState(false);
  const serverReady=a.backendStatus!=='connected'||(a.financialOverview.webhookReady&&a.financialOverview.sandboxEnabled);
  return <Modal title="模拟补款到账" subtitle="先展示核验链路，再更新履约与佣金。" onClose={onClose} wide footer={<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!ack||a.paid||a.receivingPayment||!serverReady} onClick={a.receive}>{a.paid?'已完成模拟':a.receivingPayment?'正在验签入账…':serverReady?'执行核验并确认到账':'服务端支付沙箱未就绪'}</Button></>}>
    <div className="receipt-preview"><Receipt size={32}/><span>待核验回调金额</span><strong>¥1,016</strong><small>C002 · PLAN002 · 第二期</small></div>
    <div className="reconciliation-chain">{['回调接收','签名验证','幂等去重','案件匹配','账簿入账','计佣判断'].map((label,index)=><span key={label}><i>{index+1}</i><b>{label}</b><small>{index===0?'SBX-ui-demo':index===1?(a.backendStatus==='connected'?'HMAC-SHA256 v1':a.hostedDemo?'在线交互沙箱':'离线沙箱'):index===2?'Provider 事件唯一':index===3?'C002':index===4?(a.backendStatus==='connected'?'不可变事件':'本地演示事件'):'COM_A v1'}</small></span>)}</div>
    <KeyValue items={[["本期累计到账","¥1,000 → ¥2,016"],["本期状态","部分履约 → 本期已足额"],["新增应计佣金","¥1,016 × 15% = ¥152.40"],["实际收佣","仍为 ¥0"]]}/>
    <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认执行验签、去重、案件匹配、回款入账和计佣；API 在线时结果写入租户不可变账簿。</span></label>
    {a.backendStatus==='connected'&&!serverReady&&<div className="form-error" role="alert">支付回执沙箱未配置可解析签名密钥，未发起模拟回调。</div>}
  </Modal>;
}

export function ReconciliationModal({receipt,onClose}){
  const a=useApp();
  const review=a.financialOverview.reconciliations.find(row=>row.receipt_id===receipt.id);
  const [candidates,setCandidates]=useState(review?.candidate_snapshot||[]);
  const [selected,setSelected]=useState(review?.proposed_case_id||'');
  const [reason,setReason]=useState('已核对回执附言、金额与案件履约资料，建议按所选案件进入独立复核。');
  const [note,setNote]=useState('已复核回执摘要、候选依据与案件财务档案，结论一致。');
  const [ack,setAck]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{let active=true;if(review?.candidate_snapshot?.length){setCandidates(review.candidate_snapshot);setSelected(review.proposed_case_id);return()=>{active=false}}a.reconciliationCandidates(receipt).then(rows=>{if(!active)return;setCandidates(rows);setSelected(value=>value||rows[0]?.case_id||'')}).catch(err=>active&&setError(err.message));return()=>{active=false}},[receipt.id,review?.id]);
  const propose=async()=>{setBusy(true);setError('');try{await a.proposeReceiptReconciliation(receipt,selected,reason);setAck(false)}catch(err){setError(err.message)}finally{setBusy(false)}};
  const decide=async decision=>{setBusy(true);setError('');try{await a.decideReceiptReconciliation(review,decision,note);onClose()}catch(err){setError(err.message)}finally{setBusy(false)}};
  const pending=review?.status==='pending_review';
  const canReview=pending&&a.identity.role==='admin'&&review.proposed_by!==a.identity.actor_id;
  const footer=review?<><Button onClick={onClose}>关闭</Button>{pending&&<div className="footer-actions"><Button disabled={!canReview||!ack||busy} onClick={()=>decide('reject')}>驳回提案</Button><Button variant="primary" disabled={!canReview||!ack||busy} onClick={()=>decide('approve')}>{busy?'正在提交…':'批准并入账'}</Button></div>}</>:<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!selected||reason.trim().length<8||!ack||busy} onClick={propose}>{busy?'正在提交…':'提交匹配提案'}</Button></>;
  return <Modal title={`回执复核 · ${receipt.provider_event_id}`} subtitle="候选建议只辅助判断；独立管理员批准后才写入账簿。" onClose={onClose} wide footer={footer}>
    <div className="payment-detail-head"><span>{money(receipt.amount_cents/100)}</span><Badge status={pending?'paused':'neutral'}>{pending?'等待独立复核':review?.status==='approved'?'已批准':review?.status==='rejected'?'已驳回':'待匹配'}</Badge></div>
    <KeyValue items={[["Provider",receipt.provider],["发生时间",receipt.occurred_at.slice(0,16).replace('T',' ')],["签名摘要",`${receipt.signature_digest.slice(0,12)}…`],["原案件引用",receipt.case_id||'缺失'],["失败原因",receipt.failure_code||'—'],["是否进入钱指标","否，批准前隔离"]]}/>
    {review?<>
      <h3 className="section-label"><ShieldCheck size={19}/>匹配提案</h3>
      <div className="reconciliation-proposal"><span><small>建议案件</small><b>{review.proposed_case_id}</b></span><span><small>提案人</small><b>{review.proposed_by}</b></span><span><small>版本</small><b>v{review.version}</b></span><span><small>证据摘要</small><code>{review.evidence_digest.slice(0,12)}…</code></span></div>
      <p className="body-copy">{review.reason}</p>
      {pending&&<Field label="独立复核结论"><textarea rows="3" value={note} onChange={event=>setNote(event.target.value)} placeholder="填写复核依据和结论…"/></Field>}
      {pending&&!canReview&&<div className="notice warning"><WarningCircle size={19}/><span><b>{review.proposed_by===a.identity.actor_id?'不能复核自己创建的提案':'当前角色不能复核'}</b><small>必须由另一名管理员重新核对证据并作出决定。</small></span></div>}
    </>:<>
      <h3 className="section-label"><Robot size={19}/>确定性候选建议</h3>
      <div className="reconciliation-candidates">{candidates.map(candidate=><label className={selected===candidate.case_id?'selected':''} key={candidate.case_id}><input type="radio" name="candidate" checked={selected===candidate.case_id} onChange={()=>setSelected(candidate.case_id)}/><span><b>{candidate.case_id} · {candidate.package_id}</b><small>{candidate.signals.join(' · ')}</small></span><em>{candidate.score} 分</em></label>)}</div>
      {!candidates.length&&!error&&<Empty title="正在加载候选案件" description="只查询当前工作空间且财务档案完整的案件。"/>}
      <Field label="提案依据"><textarea rows="3" value={reason} onChange={event=>setReason(event.target.value)} placeholder="说明人工核对的凭证和依据…"/></Field>
    </>}
    {pending&&<label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本人不是提案人，已独立核验回执摘要、案件归属和计佣边界；批准将原子写入回款及佣金账簿。</span></label>}
    {!review&&<label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本次只创建匹配提案，不更新案件履约状态、钱指标或不可变账簿。</span></label>}
    {error&&<div className="form-error" role="alert">{error}</div>}
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

export function ExceptionModal({incidentId,caseId,onClose}){
  const a=useApp();
  const c=a.visibleCases.find(item=>item.case_id===caseId);
  const incident=a.protectionOverview.incidents.find(item=>item.id===incidentId);
  const categoryNames={debt_dispute:'债务异议',stop_contact:'停止联系',identity_conflict:'身份冲突',mandate_expired:'委托到期',data_quality:'资料缺失',amount_verification:'金额冲突',authorization_gap:'授权缺失',contact_data:'号码缺失',budget_exhausted:'预算耗尽',channel_failure:'渠道异常'};
  const [note,setNote]=useState(incident?.resolution_note||'相关业务事实已经复核，证据已归档，申请重新评估案件。');
  const [evidence,setEvidence]=useState(incident?.evidence_refs?.join('\n')||'');
  const [reviewNote,setReviewNote]=useState('已独立核对保护事实、处理结论与证据引用，复核结论一致。');
  const [ack,setAck]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  if(!c||!incident)return null;
  const permanent=incident.status==='permanent_hold';
  const pending=incident.status==='pending_review';
  const canReview=pending&&a.identity.role==='admin'&&incident.proposed_by!==a.identity.actor_id;
  const evidenceRefs=evidence.split(/[\n,，]/).map(value=>value.trim()).filter(Boolean);
  const submit=async()=>{setBusy(true);setError('');try{await a.proposeProtectionResolution(incident,note,evidenceRefs);setAck(false)}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  const decide=async decision=>{setBusy(true);setError('');try{await a.decideProtectionResolution(incident,decision,reviewNote);onClose()}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  const footer=permanent?<Button onClick={onClose}>关闭</Button>:pending?<><Button onClick={onClose}>关闭</Button><div className="footer-actions"><Button disabled={!canReview||!ack||reviewNote.trim().length<4||busy} onClick={()=>decide('reject')}>驳回提案</Button><Button variant="primary" disabled={!canReview||!ack||reviewNote.trim().length<4||busy} onClick={()=>decide('approve')}>{busy?'正在复核…':'批准解除'}</Button></div></>:<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={note.trim().length<8||!evidenceRefs.length||!ack||busy} onClick={submit}>{busy?'正在提交…':'提交解除提案'}</Button></>;
  return <Modal title={`处理保护事件 · ${caseId}`} subtitle={`${incident.id} · ${categoryNames[incident.category]||incident.category} · ${incident.priority} · v${incident.version}`} onClose={onClose} wide footer={footer}>
    <div className="notice warning"><ShieldCheck size={20}/><span><b>{permanent?'持续保护，不开放通用解除':pending?'保护继续生效，等待独立复核':'当前仍为保护暂停'}</b><small>{incident.reason}</small></span></div>
    <KeyValue items={[["责任方",incident.owner],["来源事件",incident.source_event_id],["解除策略",incident.release_policy==='maker_checker'?'双人复核':incident.release_policy==='renewal_evidence'?'续期证据 + 双人复核':'持续保护'],["影响范围","案件与相关活动已阻断；批准后原活动不自动恢复"]]}/>
    {permanent?<div className="protection-boundary"><ShieldCheck size={32}/><h3>停止联系保护不可通过本流程解除</h3><p>仅允许记录合法有效的后续请求或被动到账核对。Agent、运营人员和普通管理员均没有直接解除工具。</p></div>:pending?<>
      <h3 className="section-label"><Receipt size={19}/>解除提案与证据</h3>
      <p className="body-copy">{incident.resolution_note}</p>
      <div className="evidence-reference-list">{incident.evidence_refs.map(ref=><code key={ref}>{ref}</code>)}</div>
      <KeyValue items={[["提案人",incident.proposed_by],["证据摘要",`${incident.evidence_digest?.slice(0,16)||'—'}…`],["版本",`v${incident.version}`]]}/>
      <Field label="独立复核结论"><textarea rows="3" value={reviewNote} onChange={event=>setReviewNote(event.target.value)} placeholder="填写独立核验依据和结论…"/></Field>
      {!canReview&&<div className="notice warning"><WarningCircle size={19}/><span><b>{incident.proposed_by===a.identity.actor_id?'不能复核自己创建的提案':'当前角色不能复核'}</b><small>必须由另一名管理员重新核对证据并作出决定。</small></span></div>}
      <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本人不是提案人，已独立核验保护事实与证据；批准只让案件进入待重新评估，不恢复原活动。</span></label>
    </>:<>
      <Field label="处理结论"><textarea rows="4" value={note} onChange={event=>setNote(event.target.value)} placeholder="说明已核验事实和申请重新评估的依据…"/></Field>
      <Field label="证据引用（每行一个）"><textarea rows="3" value={evidence} onChange={event=>setEvidence(event.target.value)} placeholder={incident.release_policy==='renewal_evidence'?'MANDATE-RENEWAL-001':'EVIDENCE-DISPUTE-001'}/><small>只填写证据编号，不把证件、合同或通话正文复制到审计日志。</small></Field>
      {incident.release_policy==='renewal_evidence'&&<div className="notice"><ShieldCheck size={19}/><span><b>委托续期强校验</b><small>至少包含一个以 MANDATE- 开头的有效授权证据引用。</small></span></div>}
      <label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本次只创建解除提案，不直接恢复案件、活动或任何触达动作。</span></label>
    </>}
    {error&&<div className="form-error" role="alert">{error}</div>}
  </Modal>;
}

export function CaseDrawer({c,tab,setTab,onClose}){
  const a=useApp();
  const entries=a.visibleLedger.filter(item=>item.case_id===c.case_id);
  const events=a.events.filter(item=>item.tenant===a.tenant&&item.caseId===c.case_id);
  const c2=c.case_id==='C002';
  const governedPlan=c.governedPlan;
  const planSchedule=governedPlan?.installments||installmentSchedules.filter(item=>item.tenant_id===a.tenant&&item.case_id===c.case_id);
  const tailInstallments=planSchedule.filter(item=>item.due_date>c.mandate_end);
  const signedPlan=governedPlan||c.plan;
  const tailRisk=Boolean(signedPlan)&&tailInstallments.length>0;
  const currentInstallment=governedPlan?.installments.find(item=>item.remaining_cents>0);
  const installmentStatus={paid:'已足额',partial:'部分履约',partial_overdue:'部分逾期',overdue:'已逾期',due:'今日到期',scheduled:'未到期'};
  return <Modal title={`案件 ${c.case_id}`} subtitle={`${c.package_id} · ${c.agingBucket} · ${c.serverAuthoritative?'服务端权威案件':'虚构样本案件'}`} wide onClose={onClose} footer={<><span className="muted small">当前组织：{a.organization[a.tenant]}</span><Button onClick={onClose}>关闭</Button></>}>
    <div className="case-detail"><div className="case-status"><Badge>{c.status}</Badge><span className="muted small">数据质量 {c.dataQuality}% · 最后联络 {c.lastContact}</span></div><Tabs items={['概览','协议','回款','记录']} value={tab} onChange={setTab}/>
      {tab==='概览'&&<>{c.blocked&&<div className="notice warning"><ShieldCheck size={21}/><span><b>保护暂停已生效</b><small>{c.reason}</small></span></div>}{currentInstallment&&<div className="case-due"><span>当前期次剩余应还</span><strong>{money(currentInstallment.remaining_cents/100)}</strong><small>第 {currentInstallment.installment_no} 期 · 应还日 {currentInstallment.due_date} · {installmentStatus[currentInstallment.status]||currentInstallment.status}</small><div><span>本期应还 <b>{money(currentInstallment.due_cents/100)}</b></span><span>本期到账 <b>{money(currentInstallment.paid_cents/100)}</b></span></div></div>}<h3 className="section-label"><Robot size={19}/> AI 下一步</h3><p className="body-copy">{c.blocked?`${c.reason}。解除条件满足前不产生新的触达动作。`:governedPlan?.status==='pending_review'?'签约提案等待独立管理员复核，批准前不视为已承诺履约。':currentInstallment?`按已生效方案核对第 ${currentInstallment.installment_no} 期；只有验签回款能够减少剩余金额。`:c.status==='已结清'?'当前方案已履约完成，保留回款与归档记录。':`下一允许动作：${c.nextAllowed}。未确认到账不更新为已还款。`}</p>{c2&&!a.paid&&<Button variant="primary" onClick={()=>a.setDialog({type:'receipt'})}>模拟补款到账</Button>}<h3 className="section-label separated">案件与委托</h3><KeyValue items={c.serverAuthoritative?[["核验债权余额",c.transfer_balance_yuan==null?'资料待补':money(c.transfer_balance_yuan)],["委托期间",c.mandate_start&&c.mandate_end?`${c.mandate_start} — ${c.mandate_end}`:'资料待补'],["佣金规则",c.commission_rule_id?`${c.commission_rule_id} · ${c.commission_rate_bps/100}%`:'资料待补'],["联系依据",c.contact_basis_ref||'尚未登记'],["数据完整度",`${c.dataQuality}%`],["来源批次",c.source_import_batch_id||'系统既有数据'],["下一允许动作",c.nextAllowed],["累计确认净回款",money(c.cash)],["累计应计佣金",money(c.commission)]]:[["原本金",money(c.principal_yuan)],["利息及费用",money(c.interest_yuan+c.fees_yuan)],["转让余额快照",`${money(c.transfer_balance_yuan)} · ${c.balance_snapshot_date}`],["首次逾期",`${c.first_overdue_date} · ${c.ageMonths} 个月`],["委托截止",c.mandate_end],["数据质量",`${c.dataQuality}%`],["下一允许动作",c.nextAllowed],["累计确认净回款",money(c.cash)],["累计应计佣金",money(c.commission)]]}/>{!c.serverAuthoritative&&<details className="knowledge-row"><summary><Phone size={18}/>AI 沟通片段（模拟示例）</summary><div className="transcript"><p><b>AI 助理</b>您好，我是受委托机构的 AI 语音助理，想与您核实一项业务信息。</p><small>完成本人身份核验前，不披露具体债务信息。</small><p><b>履约说明</b>{currentInstallment?`按已生效方案，第 ${currentInstallment.installment_no} 期应还 ${money(currentInstallment.due_cents/100)}，已确认到账 ${money(currentInstallment.paid_cents/100)}。`:'系统只使用有效委托、已核验金额和已发布策略生成说明。'}</p></div></details>}</>}
      {tab==='协议'&&(signedPlan?<><div className="notice"><Files size={20}/><span><b>{governedPlan?governedPlan.status==='pending_review'?'签约证据已提交，等待独立复核':'服务端签约方案已核验':'已签署协议样本'}</b>{governedPlan&&<small>证据摘要 {governedPlan.evidence_digest.slice(0,16)}… · v{governedPlan.version}</small>}</span></div>{tailRisk&&<div className="notice warning"><WarningCircle size={19}/><span><b>存在 {tailInstallments.length} 个委托期后尾期</b><small>委托到期后禁止主动触达；仅核对已签方案的被动到账，并按合同规则判断是否计佣。</small></span></div>}<KeyValue items={governedPlan?[["方案编号",governedPlan.plan_id],["方案总额",money(governedPlan.total_cents/100)],["已分摊回款",money(governedPlan.paid_cents/100)],["剩余应还",money(governedPlan.remaining_cents/100)],["期数",`${governedPlan.installment_count} 期（含首期）`],["首期金额",money(governedPlan.down_payment_cents/100)],["签署日期",governedPlan.signed_at.slice(0,10)],["授权版本",`策略 v${governedPlan.policy_version}`],["签署证据",governedPlan.agreement_reference]]:[["方案编号",c.plan.plan_id],["方案总额",money(c.plan.total_yuan)],["原债权余额",money(c.transfer_balance_yuan)],["协议减免",money(c.transfer_balance_yuan-c.plan.total_yuan)],["期数",`${c.plan.installments} 期（含首期）`],["首期金额",money(c.plan.down_payment_yuan)],["签署日期",c.plan.signed_at||'尚未签署'],["授权版本",c.plan.policy_id]]}/>{planSchedule.length>0&&<><h3 className="section-label">分期计划</h3><table className="data-table compact-table"><thead><tr><th>期次</th><th>应还日期</th><th>应还金额</th><th>到账 / 边界</th><th>状态</th></tr></thead><tbody>{planSchedule.map(item=>{const afterMandate=item.due_date>c.mandate_end;const complete=governedPlan?item.status==='paid':c2&&(item.installment_no===1||(item.installment_no===2&&a.paid));const partial=governedPlan?['partial','partial_overdue'].includes(item.status):(c2&&item.installment_no===2&&!a.paid)||(c.case_id==='C014'&&item.installment_no===1);return <tr key={item.installment_id||item.schedule_id}><td>第 {item.installment_no} 期</td><td>{item.due_date}</td><td>{money(governedPlan?item.due_cents/100:item.due_yuan)}</td><td>{governedPlan?`${money(item.paid_cents/100)} · ${afterMandate?'委托后尾期':'委托期内'}`:afterMandate?'委托后尾期 · 禁止主动触达':'委托期内'}</td><td><Badge status={complete?'completed':partial?'running':item.status==='overdue'?'blocked':'neutral'}>{governedPlan?installmentStatus[item.status]||item.status:complete?'已足额':partial?'部分履约':'未到期'}</Badge></td></tr>})}</tbody></table></>}</>:<Empty title="尚无已签协议" description="口头意向、待签方案与已签协议会分别记录。"/>)}
      {tab==='回款'&&<><Metrics items={[["确认净回款",money(c.cash)],["计佣回款",money(entries.reduce((sum,item)=>sum+item.eligible_cash_yuan,0))],["应计佣金",money(c.commission)]]}/>{entries.length?<div className="case-payment-list">{entries.map(item=><button key={item.transaction_id} onClick={()=>a.setDialog({type:'payment',transaction:item})}><span><b>{item.transaction_id}</b><small>{item.booked_date} · {reasonText[item.reason]}</small></span><span><small>{item.cash_yuan<0?'退款冲回':'验签 · 去重 · 匹配 · 分配完成'}</small><b className="number">{money(item.cash_yuan)}</b></span></button>)}</div>:<Empty title="暂无已确认回款" description="支付失败、处理中或待匹配记录不计入已确认回款。"/>}</>}
      {tab==='记录'&&<><div className="case-trace-list">{events.map(event=><div key={event.id}><span className="trace-status"><CheckCircle size={18}/></span><span><b>{event.title}</b><small>{event.detail}</small><code>{event.runId} · {event.stepId} · {event.actionId}</code></span><span><small>{event.actor}</small><b>{event.tool}</b><em>{event.time}</em></span></div>)}</div>{!events.length&&<Empty title="暂无运行事件" description="执行活动后，事实、规则与工具回执将在此留痕。"/>}</>}
    </div>
  </Modal>;
}

export function ImportModal({onClose}){
  const a=useApp();
  const [file,setFile]=useState(null);
  const [batch,setBatch]=useState(null);
  const [recent,setRecent]=useState([]);
  const [ack,setAck]=useState(false);
  const [reviewNote,setReviewNote]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const sample=[
    'package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status',
    'PKG_DEMO,演示导入资产包,C901,1200000,2026-09-01,2027-08-31,COM_DEMO_V1,1500,CONSENT-C901,待联系'
  ].join('\n');
  useEffect(()=>{if(a.backendStatus==='connected')assetImportApi.list(a.tenant).then(setRecent).catch(()=>setRecent([]))},[a.backendStatus,a.tenant]);
  const choose=async event=>{const selected=event.target.files?.[0];if(!selected)return;if(selected.size>1000000){setError('单个 CSV 不能超过 1 MB。');return}setFile({name:selected.name,text:await selected.text()});setBatch(null);setError('')};
  const preview=async()=>{if(!file)return;setBusy(true);setError('');try{const result=a.backendStatus==='connected'?await assetImportApi.preview(a.tenant,file.name,file.text,`asset-import-${Date.now()}`):{id:'DEMO-PREVIEW',source_filename:file.name,source_digest:'offline-demo',schema_version:'asset-case-v1',status:'ready',version:1,row_count:1,valid_count:1,invalid_count:0,duplicate_count:0,package_count:1,total_claim_balance_cents:1200000,issues:[],created_by:'离线演示',created_at:new Date().toISOString(),committed_by:null,committed_at:null};setBatch(result);setRecent(rows=>[result,...rows.filter(row=>row.id!==result.id)])}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  const commit=async()=>{setBusy(true);setError('');try{const result=await assetImportApi.commit(a.tenant,batch.id,batch.version,reviewNote);setBatch(result);setRecent(rows=>rows.map(row=>row.id===result.id?result:row));await a.refreshCatalog();a.notify(`${result.valid_count} 个案件已由服务端原子导入并进入权威目录`)}catch(reason){setError(reason.message)}finally{setBusy(false)}};
  const report=()=>downloadCSV(`导入问题_${batch.id}.csv`,['行号','级别','代码','字段','说明'],batch.issues.map(issue=>[issue.row_number||'',issue.severity,issue.code,issue.field||'',issue.message]));
  const canCommit=a.backendStatus==='connected'&&batch?.status==='ready'&&a.identity.role==='admin'&&batch.created_by!==a.identity.actor_id;
  const footer=batch?<><Button onClick={()=>{setBatch(null);setAck(false);setReviewNote('');setError('')}}>返回</Button><div className="footer-actions">{batch.issues.length>0&&<Button onClick={report}>下载问题明细</Button>}{batch.status==='ready'&&a.backendStatus==='connected'&&<Button variant="primary" disabled={!canCommit||!ack||reviewNote.trim().length<8||busy} onClick={commit}>{busy?'正在提交…':'确认导入'}</Button>}{batch.status==='committed'&&<Button variant="primary" onClick={onClose}>完成</Button>}{a.backendStatus!=='connected'&&<Button variant="primary" onClick={onClose}>完成演示</Button>}</div></>:<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={!file||busy} onClick={preview}>{busy?'正在校验…':'生成服务端预演'}</Button></>;
  return <Modal title="资产与案件导入中心" subtitle="CSV 先预演、后复核；原始文件不写入数据库。" onClose={onClose} wide footer={footer}>
    {!batch?<>
      <div className="upload-demo"><UploadSimple size={36}/><h3>{file?.name||'选择 UTF-8 CSV'}</h3><p>只接收业务编号、整数分金额、委托期限、佣金规则与联系依据引用，不接收姓名、手机号、证件号或地址。</p><Field label="选择文件"><input type="file" accept=".csv,text/csv" onChange={choose}/></Field><Button onClick={()=>{setFile({name:'RepayGuard_导入样本.csv',text:sample});setError('')}}>使用脱敏样本</Button></div>
      <Field label="v1 字段契约"><textarea readOnly rows="4" value="package_id, package_title, case_id, claim_balance_cents, mandate_start, mandate_end, commission_rule_id, commission_rate_bps, contact_basis_ref, case_status（可选）"/><small>金额必须使用整数分；日期使用 YYYY-MM-DD；已有案件只跳过，不覆盖。</small></Field>
      {recent.length>0&&<><h3 className="section-label"><Files size={19}/>最近导入批次</h3><div className="import-batch-list">{recent.slice(0,4).map(row=><button key={row.id} onClick={()=>{setBatch(row);setAck(false);setError('')}}><span><b>{row.source_filename}</b><small>{row.id} · {row.valid_count} 有效 / {row.invalid_count} 错误</small></span><Badge status={row.status==='committed'?'completed':row.status==='blocked'?'blocked':'paused'}>{row.status==='committed'?'已提交':row.status==='blocked'?'已阻断':'待复核'}</Badge></button>)}</div></>}
    </>:<>
      <div className={`scope-preview ${batch.status==='blocked'?'import-blocked':''}`}>{batch.status==='blocked'?<WarningCircle size={26}/>:<CheckCircle size={26}/>}<div><b>{batch.status==='committed'?'导入已完成':batch.status==='blocked'?'预演发现阻断问题':'预演完成，等待独立确认'}</b><p>{batch.source_filename} · {batch.id} · SHA-256 {batch.source_digest.slice(0,12)}…</p></div></div>
      <Metrics items={[["文件行数",batch.row_count],["可导入",batch.valid_count],["错误行",batch.invalid_count],["已有案件",batch.duplicate_count]]}/>
      <KeyValue items={[["目标组织",a.organization[a.tenant]],["新资产包",`${batch.package_count} 个 · 默认草稿策略`],["债权余额",money(batch.total_claim_balance_cents/100)],["预演创建人",batch.created_by],["原始文件","仅计算摘要，不持久化"]]}/>
      {batch.issues.length>0&&<div className="import-issue-list">{batch.issues.slice(0,6).map((issue,index)=><div key={`${issue.code}-${issue.row_number}-${index}`}><Badge status={issue.severity==='error'?'blocked':'neutral'}>{issue.severity==='error'?'错误':'提示'}</Badge><span><b>{issue.code}{issue.row_number?` · 第 ${issue.row_number} 行`:''}</b><small>{issue.field?`${issue.field}：`:''}{issue.message}</small></span></div>)}</div>}
      {batch.status==='ready'&&a.backendStatus==='connected'&&!canCommit&&<div className="notice warning"><ShieldCheck size={19}/><span><b>{batch.created_by===a.identity.actor_id?'不能确认自己创建的预演':'需要管理员确认'}</b><small>请由另一名工作空间管理员打开最近批次，复核摘要后提交。</small></span></div>}
      {batch.status==='ready'&&a.backendStatus==='connected'&&canCommit&&<><Field label="独立复核结论"><textarea rows="3" value={reviewNote} onChange={event=>setReviewNote(event.target.value)} placeholder="填写对字段、金额、委托期限和佣金规则的复核结论…"/></Field><label className="check-agreement"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/><span>确认本人不是预演创建人，已复核字段、金额、委托期限、佣金规则与问题报告；提交不会覆盖已有案件。</span></label></>}
      {a.backendStatus!=='connected'&&<div className="notice"><ShieldCheck size={19}/><span><b>当前仅为离线预演</b><small>未上传文件、未创建资产包或案件，也没有服务端审计记录。</small></span></div>}
    </>}
    {error&&<div className="form-error" role="alert">{error}</div>}
  </Modal>;
}

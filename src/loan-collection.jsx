import React,{useEffect,useRef,useState} from 'react';
import {loanCollectionApi as api} from './api';
import {loanRequestKey as key,loanPolicyPayload,loanPolicyTime} from './loan-collection-state';
import {useApp} from './context';
import {useRemoteResource} from './remote-resource';
import {Button,Empty,PageHead} from './ui';

const labels={identity_pending:'等待模拟本人核验',debt_explained:'已核验，等待承诺',ptp_recorded:'已记录还款承诺',paused:'异常暂停',ended:'对话结束',paid_claimed:'自述已还款，待核实',closed:'已结束'};
const promiseLabels={pending:'待履约',partial:'部分履约',fulfilled:'承诺已兑现',broken:'承诺已失约'};
const empty={product:'个人无抵押贷款',due_date:'',amount_cents:'',contact_reference:'',source_reference:''};
const emptyPolicy=()=>({start:'09:00',end:'20:00',daily_session_limit:3,snapshot_max_hours:24,promise_max_days:30,authorization_minutes:30,paused:true,authority_reference:'',valid_until:new Date(Date.now()+7*86400000).toISOString()});


export function LoanCollection(){
 const a=useApp(),connected=a.backendStatus==='connected',admin=a.identity.role==='admin',operator=['admin','operator'].includes(a.identity.role);
 const [caseId,setCase]=useState(''),[form,setForm]=useState(empty),[ack,setAck]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[amount,setAmount]=useState(''),[due,setDue]=useState(''),[sipAck,setSipAck]=useState(false);
 const mutation=useRef(0),requestKey=useRef(key());
 const [policyForm,setPolicyForm]=useState(emptyPolicy),[policyAck,setPolicyAck]=useState(false);
 const policy=useRemoteResource(connected,a.tenant,()=>api.policy(a.tenant));
 const overview=useRemoteResource(connected,a.tenant,()=>api.overview(a.tenant));
 const profile=useRemoteResource(connected&&!!caseId,`${a.tenant}:${caseId}`,()=>api.profile(a.tenant,caseId));
 const gate=useRemoteResource(connected&&!!caseId,`${a.tenant}:${caseId}`,()=>api.preflight(a.tenant,caseId));
 useEffect(()=>{mutation.current++;setForm(empty);setBusy(false);setAck(false);setError('');setAmount('');setDue('');setSipAck(false);requestKey.current=key();return()=>{mutation.current++}},[a.tenant,caseId,connected]);
 useEffect(()=>{if(profile.data)setForm({...empty,...profile.data})},[profile.data]);
 useEffect(()=>{setPolicyForm(emptyPolicy());setPolicyAck(false)},[a.tenant,connected]);
 useEffect(()=>{const p=policy.data?.policy;if(p)setPolicyForm({...p,start:loanPolicyTime(p.window_start_minute),end:loanPolicyTime(p.window_end_minute),valid_until:/(Z|[+-]\d{2}:\d{2})$/.test(p.valid_until)?p.valid_until:`${p.valid_until}Z`})},[policy.data]);
 const run=async (fn,after)=>{const generation=mutation.current;setBusy(true);setError('');try{await fn();if(generation===mutation.current){after?.();overview.refresh();gate.refresh();profile.refresh()}}catch(e){if(generation===mutation.current)setError(e.message)}finally{if(generation===mutation.current)setBusy(false)}};
 const event=(row,intent)=>run(()=>api.event(a.tenant,row.id,{event_key:key(),expected_version:row.version,intent,acknowledged:ack,...(intent==='promise'?{confirmed:true,amount_cents:Number(amount),due_date:due}:{})}));
 const scoped=overview.data?.tenant_id===a.tenant?overview.data:null;
 const rows=scoped?.sessions||[],dispatches=scoped?.sip_dispatches||[],jobs=scoped?.execution_jobs||[];
 return <>
  <PageHead title="标准贷款机催" description="先验证标准案件的对话与履约闭环；真实外呼仍需接入电话、媒体和本人核验适配器。"><Button disabled={!connected||busy} onClick={()=>{overview.refresh();gate.refresh();profile.refresh();policy.refresh()}}>刷新证据</Button></PageHead>
  <p className="notice">对话仍是受控沙箱，不调用模型、不改写合同或账务。独立的 Linphone 回声测试会呼叫自己的固定测试分机，不发送客户号码或债务内容；模拟核验不代表真实身份验证。</p>
  {!connected?<Empty title="连接业务 API 后使用" description="这里不生成离线成功记录。请先完成企业接入。" action={<Button onClick={()=>a.navigate('pilot')}>查看接入验收</Button>}/>:<>
   <section className="governance-panel"><h2>租户机催政策</h2>
    <p className="muted">北京时间 · 仅管控沙箱。政策未配置、暂停、到期或不在时段内均阻断；保存变更使旧会话授权失效。</p>
    {policy.busy?<p role="status">读取机催政策…</p>:policy.error?<p role="alert" className="form-error">{policy.error}</p>:<>
     <p>{policy.data?.policy?`当前政策 v${policy.data.policy.version} · ${policy.data.policy.paused?'已暂停':'已启用'}`:'尚未配置政策，请管理员确认后保存。'}</p>
     <div className="loan-form-grid">{[['start','联系开始时间（HH:mm）','text'],['end','联系结束时间（HH:mm，可用 24:00）','text'],['daily_session_limit','同案每日沙箱任务上限（1–3）','number'],['snapshot_max_hours','快照有效小时（1–24）','number'],['promise_max_days','承诺最远天数（1–30）','number'],['authorization_minutes','会话授权分钟（1–30）','number'],['authority_reference','政策授权引用','text'],['valid_until','政策到期时间（含 Z 或时区偏移）','text']].map(([name,label,type])=><label key={name}>{label}<input type={type} value={policyForm[name]} disabled={!admin||busy} onChange={e=>setPolicyForm(f=>({...f,[name]:e.target.value}))}/></label>)}</div>
     <label><input type="checkbox" checked={policyForm.paused} disabled={!admin||busy} onChange={e=>setPolicyForm(f=>({...f,paused:e.target.checked}))}/> 暂停该租户沙箱机催</label>
     <label><input type="checkbox" checked={policyAck} disabled={!admin||busy} onChange={e=>setPolicyAck(e.target.checked)}/> 我确认政策来源；此操作不启用真实外呼。</label>
     <Button disabled={!admin||busy||!policyAck||!policyForm.authority_reference} onClick={()=>run(()=>api.savePolicy(a.tenant,loanPolicyPayload(policyForm,policy.data?.policy?.version||0)),()=>{setPolicyAck(false);requestKey.current=key();policy.refresh()})}>保存机催政策</Button>
    </>}
   </section>
   <section className="governance-panel"><h2>案件准入与资料</h2>
    <label>选择案件 <select aria-label="选择机催案件" value={caseId} onChange={e=>{setBusy(false);setCase(e.target.value)}}><option value="">请选择</option>{a.visibleCases.map(c=><option key={c.case_id} value={c.case_id}>{c.case_id}</option>)}</select></label>
    {(profile.busy||gate.busy)&&<p role="status">读取当前案件证据…</p>}
    {(profile.error||gate.error)&&<p role="alert" className="form-error">{profile.error||gate.error}</p>}
    {caseId&&!profile.busy&&!profile.error&&<><div className="loan-form-grid">{[['product','贷款产品','text'],['due_date','原应还日期','date'],['amount_cents','当前应还金额（分）','number'],['contact_reference','不透明联系引用','text'],['source_reference','账务来源引用','text']].map(([name,label,type])=><label key={name}>{label}<input type={type} value={form[name]} disabled={!admin||busy} onChange={e=>setForm(f=>({...f,[name]:e.target.value}))}/></label>)}</div>
     <p className="muted">只接收引用，不填写手机号。首期准入 DPD 1–30；快照期限由当前政策控制，最多 24 小时。保存表示管理员确认当前快照时间，不构成外部真实性证明。</p>
     <label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/> 我确认资料来源及本次沙箱操作；承诺不等于到账。</label>
     <div className="material-toolbar"><Button disabled={!admin||busy||!ack||!form.due_date||!form.amount_cents||!form.contact_reference||!form.source_reference} onClick={()=>run(()=>api.saveProfile(a.tenant,caseId,{product:form.product,due_date:form.due_date,amount_cents:Number(form.amount_cents),contact_reference:form.contact_reference,source_reference:form.source_reference,snapshot_at:new Date().toISOString(),expected_version:profile.data?.version||0,acknowledged:true}))}>保存资料快照</Button>
      <Button disabled={!admin||busy||!ack||!gate.data?.sandbox_eligible||policy.busy||!!policy.error} onClick={()=>run(()=>api.start(a.tenant,{case_id:caseId,request_key:requestKey.current,mode:'sandbox',acknowledged:true}),()=>{requestKey.current=key()})}>批准并创建沙箱会话</Button></div>
     {gate.data&&<p>{gate.data.sandbox_eligible?'沙箱准入通过':`准入阻断：${gate.data.blockers.join('；')}`} · 真实电话未接通</p>}
    </>}
   </section>
   <section className="governance-panel"><h2>对话与履约记录</h2><p className="muted">最近 100 个会话。所有记录来自服务端；模拟异常只暂停当前会话，不改变真实案件。</p>
    <div className="loan-form-grid"><label>承诺金额（分）<input type="number" value={amount} onChange={e=>setAmount(e.target.value)}/></label><label>承诺日期（北京时间）<input type="date" value={due} onChange={e=>setDue(e.target.value)}/></label></div>
    {!ack&&<p className="muted">选择案件并勾选确认后，才能提交事件或核验履约。</p>}
    {overview.busy&&<p role="status">正在读取会话…</p>}{overview.error&&<p role="alert" className="form-error">{overview.error}</p>}
    <label><input type="checkbox" checked={sipAck} disabled={!admin||busy} onChange={e=>setSipAck(e.target.checked)}/> 我确认 Linphone 1001 是自己的内部测试分机；此操作会发起回声呼叫。</label>
    <p className="muted">{scoped?.internal_sip_echo_configured?'内部测试配置已启用；仍需独立验收响铃与音频。':'内部 SIP 测试未配置或当前后端不支持；门禁检查仍可排队，客户外呼继续阻断。'}</p>
    {rows.map(row=><article className="persisted-agent-run" key={row.id}><h3>{row.case_id} · {labels[row.state]||row.state}</h3><small>{row.id} · 沙箱 · v{row.version} · 政策 v{row.policy_version??'未绑定'} · 授权截止 {row.authorization_expires_at||'缺失'} UTC</small>
     {row.promise?.status&&<p>{promiseLabels[row.promise.status]} · 承诺 {row.promise.amount_cents} 分 / {row.promise.due_date} · 核验净回款 {row.promise.paid_cents} 分（来自现有账本，不代表已结清）</p>}
     {row.events.map((e,i)=><p key={i}>{e.intent} → {e.result.detail||e.result.reason||e.result.action}{e.result.amount_cents!==undefined&&` · 应还 ${e.result.amount_cents} 分`}</p>)}
     <div className="material-toolbar">{row.state==='identity_pending'&&<>
      <Button disabled={!admin||busy||!ack||caseId!==row.case_id} onClick={()=>run(()=>api.dispatchCheck(a.tenant,row.id,row.version))}>排队检查执行门禁</Button>
      <Button disabled={!admin||busy||!ack||!sipAck||caseId!==row.case_id||!scoped?.internal_sip_echo_configured||dispatches.some(item=>item.session_id===row.id)} onClick={()=>run(()=>api.sipEcho(a.tenant,row.id,row.version))}>呼叫我的 Linphone 测试分机</Button>
     </>}{['identity_pending','debt_explained'].includes(row.state)&&<>
      {row.state==='identity_pending'?<Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,'identity_verified')}>模拟本人核验通过</Button>:<><Button disabled={!operator||busy||!ack||caseId!==row.case_id||!amount||!due} onClick={()=>event(row,'promise')}>确认还款承诺</Button><Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,'paid_claimed')}>模拟自述已还款</Button></>}
      {['wrong_person','dispute','complaint','hardship','human_requested','end'].map((intent,i)=><Button key={intent} disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,intent)}>{['错人','债务异议','投诉','困难','请求人工','结束'][i]}</Button>)}
     </>}{row.promise?.status&&<Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>run(()=>api.reconcile(a.tenant,row.id))}>核验承诺履约</Button>}</div>
    </article>)}
    {!overview.busy&&!overview.error&&!rows.length&&<Empty title="尚无机催会话" description="先选择案件、补齐资料并通过准入检查。"/>}
   </section>
   <section className="governance-panel"><h2>执行任务与测试派发</h2><p className="muted">来自当前租户服务端。任务完成只表示处理完成；门禁通过、网关提交、音频验收与 AI 通话是不同结果。</p>
    {jobs.map(job=><article className="persisted-agent-run" key={job.id}><h3>{job.kind==='loan.dispatch_check'?'执行门禁检查':'Linphone 测试任务'} · {({queued:'等待 Worker',running:'运行中',succeeded:'处理完成',failed:'处理失败',cancelled:'已取消'})[job.status]||job.status}</h3><small>{job.id}</small>
     {job.result?.mode==='sandbox_dispatch_check'&&<p>{job.result.gate_passed?'本次门禁检查通过；不授权后续拨号':`已阻断：${(job.result.blockers||[]).join('；')}`}</p>}
     {job.error&&<p role="alert" className="form-error">{job.error}</p>}
    </article>)}
    {dispatches.map(dispatch=><article className="persisted-agent-run" key={dispatch.id}><h3>Linphone 1001 · {({prepared:'已预留',dispatching:'派发意图已保存',unknown:'结果未知',submitted:'网关确认创建',blocked:'已阻断',stop_requested:'已请求停止'})[dispatch.state]||dispatch.state}</h3><small>{dispatch.id} · {dispatch.session_id}</small>
     <p>通道观察：{dispatch.observation?.channel_state||dispatch.observation?.hangup_state||'暂无'} · 音频与 AI 对话尚未验收</p>
     {dispatch.observation?.blockers?.length>0&&<p>阻断原因：{dispatch.observation.blockers.join('；')}</p>}
     <div className="material-toolbar"><Button disabled={!admin||busy} onClick={()=>run(()=>api.sipAction(a.tenant,dispatch.id,'reconcile',key().slice(0,40)))}>查询原呼叫</Button><Button disabled={!admin||busy||dispatch.state==='blocked'} onClick={()=>run(()=>api.sipAction(a.tenant,dispatch.id,'stop',key().slice(0,40)))}>停止测试呼叫</Button></div>
    </article>)}
    {!jobs.length&&!dispatches.length&&!overview.busy&&!overview.error&&<p className="muted">尚无执行记录。门禁任务与固定分机测试分别排队。</p>}
   </section>
  </>}
  {error&&<p role="alert" className="form-error">{error}</p>}
 </>;
}

import React,{useEffect,useRef,useState} from 'react';
import {loanCollectionApi as api} from './api';
import {loanRequestKey as key} from './loan-collection-state';
import {useApp} from './context';
import {useRemoteResource} from './remote-resource';
import {Button,Empty,PageHead} from './ui';

const labels={identity_pending:'等待模拟本人核验',debt_explained:'已核验，等待承诺',ptp_recorded:'已记录还款承诺',paused:'异常暂停',ended:'对话结束',paid_claimed:'自述已还款，待核实',closed:'已结束'};
const promiseLabels={pending:'待履约',partial:'部分履约',fulfilled:'承诺已兑现',broken:'承诺已失约'};
const empty={product:'个人无抵押贷款',due_date:'',amount_cents:'',contact_reference:'',source_reference:''};


export function LoanCollection(){
 const a=useApp(),connected=a.backendStatus==='connected',admin=a.identity.role==='admin',operator=['admin','operator'].includes(a.identity.role);
 const [caseId,setCase]=useState(''),[form,setForm]=useState(empty),[ack,setAck]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[amount,setAmount]=useState(''),[due,setDue]=useState('');
 const mutation=useRef(0),requestKey=useRef(key());
 const overview=useRemoteResource(connected,a.tenant,()=>api.overview(a.tenant));
 const profile=useRemoteResource(connected&&!!caseId,`${a.tenant}:${caseId}`,()=>api.profile(a.tenant,caseId));
 const gate=useRemoteResource(connected&&!!caseId,`${a.tenant}:${caseId}`,()=>api.preflight(a.tenant,caseId));
 useEffect(()=>{mutation.current++;setForm(empty);setBusy(false);setAck(false);setError('');setAmount('');setDue('');requestKey.current=key();return()=>{mutation.current++}},[a.tenant,caseId,connected]);
 useEffect(()=>{if(profile.data)setForm({...empty,...profile.data})},[profile.data]);
 const run=async fn=>{const generation=mutation.current;setBusy(true);setError('');try{await fn();if(generation===mutation.current){overview.refresh();gate.refresh();profile.refresh()}}catch(e){if(generation===mutation.current)setError(e.message)}finally{if(generation===mutation.current)setBusy(false)}};
 const event=(row,intent)=>run(()=>api.event(a.tenant,row.id,{event_key:key(),expected_version:row.version,intent,acknowledged:ack,...(intent==='promise'?{confirmed:true,amount_cents:Number(amount),due_date:due}:{})}));
 const rows=overview.data?.tenant_id===a.tenant?overview.data.sessions:[];
 return <>
  <PageHead title="标准贷款机催" description="先验证标准案件的对话与履约闭环；真实外呼仍需接入电话、媒体和本人核验适配器。"><Button disabled={!connected||busy} onClick={()=>{overview.refresh();gate.refresh();profile.refresh()}}>刷新证据</Button></PageHead>
  <p className="notice">受控沙箱：不拨号、不调用模型、不改写合同或账务。资料是管理员提供的快照，模拟核验不代表真实身份验证。</p>
  {!connected?<Empty title="连接业务 API 后使用" description="这里不生成离线成功记录。请先完成企业接入。" action={<Button onClick={()=>a.navigate('pilot')}>查看接入验收</Button>}/>:<>
   <section className="governance-panel"><h2>案件准入与资料</h2>
    <label>选择案件 <select aria-label="选择机催案件" value={caseId} onChange={e=>{setBusy(false);setCase(e.target.value)}}><option value="">请选择</option>{a.visibleCases.map(c=><option key={c.case_id} value={c.case_id}>{c.case_id}</option>)}</select></label>
    {(profile.busy||gate.busy)&&<p role="status">读取当前案件证据…</p>}
    {(profile.error||gate.error)&&<p role="alert" className="form-error">{profile.error||gate.error}</p>}
    {caseId&&!profile.busy&&!profile.error&&<><div className="loan-form-grid">{[['product','贷款产品','text'],['due_date','原应还日期','date'],['amount_cents','当前应还金额（分）','number'],['contact_reference','不透明联系引用','text'],['source_reference','账务来源引用','text']].map(([name,label,type])=><label key={name}>{label}<input type={type} value={form[name]} disabled={!admin||busy} onChange={e=>setForm(f=>({...f,[name]:e.target.value}))}/></label>)}</div>
     <p className="muted">只接收引用，不填写手机号。首期准入 DPD 1–30；快照有效期 24 小时。保存表示管理员确认当前快照时间，不构成外部真实性证明。</p>
     <label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/> 我确认资料来源及本次沙箱操作；承诺不等于到账。</label>
     <div className="material-toolbar"><Button disabled={!admin||busy||!ack||!form.due_date||!form.amount_cents||!form.contact_reference||!form.source_reference} onClick={()=>run(()=>api.saveProfile(a.tenant,caseId,{product:form.product,due_date:form.due_date,amount_cents:Number(form.amount_cents),contact_reference:form.contact_reference,source_reference:form.source_reference,snapshot_at:new Date().toISOString(),expected_version:profile.data?.version||0,acknowledged:true}))}>保存资料快照</Button>
      <Button disabled={!admin||busy||!ack||!gate.data?.sandbox_eligible} onClick={()=>run(()=>api.start(a.tenant,{case_id:caseId,request_key:requestKey.current,mode:'sandbox',acknowledged:true}))}>批准并创建沙箱会话</Button></div>
     {gate.data&&<p>{gate.data.sandbox_eligible?'沙箱准入通过':`准入阻断：${gate.data.blockers.join('；')}`} · 真实电话未接通</p>}
    </>}
   </section>
   <section className="governance-panel"><h2>对话与履约记录</h2><p className="muted">最近 100 个会话。所有记录来自服务端；模拟异常只暂停当前会话，不改变真实案件。</p>
    <div className="loan-form-grid"><label>承诺金额（分）<input type="number" value={amount} onChange={e=>setAmount(e.target.value)}/></label><label>承诺日期（北京时间）<input type="date" value={due} onChange={e=>setDue(e.target.value)}/></label></div>
    {!ack&&<p className="muted">选择案件并勾选确认后，才能提交事件或核验履约。</p>}
    {overview.busy&&<p role="status">正在读取会话…</p>}{overview.error&&<p role="alert" className="form-error">{overview.error}</p>}
    {rows.map(row=><article className="persisted-agent-run" key={row.id}><h3>{row.case_id} · {labels[row.state]||row.state}</h3><small>{row.id} · 沙箱 · v{row.version}</small>
     {row.promise?.status&&<p>{promiseLabels[row.promise.status]} · 承诺 {row.promise.amount_cents} 分 / {row.promise.due_date} · 核验净回款 {row.promise.paid_cents} 分（来自现有账本，不代表已结清）</p>}
     {row.events.map((e,i)=><p key={i}>{e.intent} → {e.result.detail||e.result.reason||e.result.action}{e.result.amount_cents!==undefined&&` · 应还 ${e.result.amount_cents} 分`}</p>)}
     <div className="material-toolbar">{['identity_pending','debt_explained'].includes(row.state)&&<>
      {row.state==='identity_pending'?<Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,'identity_verified')}>模拟本人核验通过</Button>:<><Button disabled={!operator||busy||!ack||caseId!==row.case_id||!amount||!due} onClick={()=>event(row,'promise')}>确认还款承诺</Button><Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,'paid_claimed')}>模拟自述已还款</Button></>}
      {['wrong_person','dispute','complaint','hardship','human_requested','end'].map((intent,i)=><Button key={intent} disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>event(row,intent)}>{['错人','债务异议','投诉','困难','请求人工','结束'][i]}</Button>)}
     </>}{row.promise?.status&&<Button disabled={!operator||busy||!ack||caseId!==row.case_id} onClick={()=>run(()=>api.reconcile(a.tenant,row.id))}>核验承诺履约</Button>}</div>
    </article>)}
    {!overview.busy&&!overview.error&&!rows.length&&<Empty title="尚无机催会话" description="先选择案件、补齐资料并通过准入检查。"/>}
   </section>
  </>}
  {error&&<p role="alert" className="form-error">{error}</p>}
 </>;
}

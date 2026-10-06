import React,{useState} from 'react';
import {operationsApi} from './api';
import {Button} from './ui';

const labels={manual_confirmation:'待提交',pending_review:'待独立复核',approved:'已批准',rejected:'已驳回',stale:'配置变化，需重验',expired:'已过期'};

export function PilotEvidence({tenant,identity,gate,onRefresh}){
 const [form,setForm]=useState({gate_id:'identity',evidence_reference:'',evidence_digest:'',validity_days:30,acknowledged:false});
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const mutate=async(action)=>{setBusy(true);setError('');try{await action();await onRefresh()}catch(e){setError(e.message)}finally{setBusy(false)}};
 const canSubmit=['admin','operator'].includes(identity?.role);
 return <section className="activity-section pilot-evidence"><div className="section-title"><h2>试点验收记录</h2><span>{gate.status==='accepted'?'联合门禁通过':'待完成联合验收'}</span></div>
 <p>提交外部验收记录编号与 SHA-256 摘要，由另一位管理员独立复核。批准绑定当前配置，有效期最长 90 天；此记录不启用真实调用。</p>
 <div className="pilot-evidence-grid">{gate.external.map(item=><article key={item.id}><h3>{item.label}</h3><p>{labels[item.status]||item.status}</p>{item.evidence&&<><dl className="kv"><div><dt>记录编号</dt><dd>{item.evidence.evidence_reference}</dd></div><div><dt>提交人</dt><dd>{item.evidence.proposed_by}</dd></div><div><dt>复核人</dt><dd>{item.evidence.reviewed_by||'待复核'}</dd></div><div><dt>有效期至</dt><dd>{new Date(item.evidence.expires_at+'Z').toLocaleString('zh-CN')}</dd></div></dl><details><summary>证据摘要</summary><code>{item.evidence.evidence_digest}</code></details>{item.status==='pending_review'&&identity?.role==='admin'&&identity.actor_id!==item.evidence.proposed_by&&<div className="pilot-evidence-actions">{['approve','reject'].map(decision=><Button key={decision} disabled={busy} onClick={()=>mutate(()=>operationsApi.decidePilotEvidence(tenant,item.evidence.id,{decision,expected_version:item.evidence.version,acknowledged:true}))}>{decision==='approve'?'已独立核验，批准':'驳回'}</Button>)}</div>}</>}</article>)}</div>
 {canSubmit&&<form className="pilot-evidence-form" onSubmit={e=>{e.preventDefault();mutate(async()=>{await operationsApi.proposePilotEvidence(tenant,form);setForm(f=>({...f,evidence_reference:'',evidence_digest:'',acknowledged:false}))})}}>
 <label>验收项目<select value={form.gate_id} onChange={e=>setForm({...form,gate_id:e.target.value})}>{gate.external.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
 <label>脱敏记录编号<input required minLength={4} maxLength={160} pattern="[A-Za-z0-9][A-Za-z0-9._/-]{3,159}" value={form.evidence_reference} onChange={e=>setForm({...form,evidence_reference:e.target.value})} placeholder="ACCEPTANCE/2026-001"/></label>
 <label>SHA-256 摘要<input required pattern="[0-9a-f]{64}" maxLength={64} value={form.evidence_digest} onChange={e=>setForm({...form,evidence_digest:e.target.value})}/></label>
 <label>有效天数<input type="number" required min={1} max={90} value={form.validity_days} onChange={e=>setForm({...form,validity_days:Number(e.target.value)})}/></label>
 <label className="pilot-ack"><input type="checkbox" required checked={form.acknowledged} onChange={e=>setForm({...form,acknowledged:e.target.checked})}/>记录已脱敏，未包含凭据或原始个人信息；提交新记录后该项目需重新复核。</label>
 <Button disabled={busy||!form.acknowledged} type="submit">{busy?'处理中':'提交验收证据'}</Button></form>}
 {error&&<p role="alert" className="form-error">{error}</p>}</section>;
}

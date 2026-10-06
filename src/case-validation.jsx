import React,{useEffect,useRef,useState} from 'react';
import {CheckCircle,WarningCircle,DownloadSimple} from '@phosphor-icons/react';
import {operationsApi} from './api';
import {Button,Field,KeyValue} from './ui';

const money=value=>Number.isSafeInteger(value)?new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(value/100):'—';
const comparisonLabels={not_provided:'未填写外部凭证净额',unavailable:'证据链不完整，暂不能比对',matched:'金额一致，仍待外部独立复核',mismatch:'金额不一致，请逐项排查'};

export function CaseValidation({tenant,connected}){
 const [caseId,setCaseId]=useState(''),[expectedNet,setExpectedNet]=useState(''),[result,setReport]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const sequence=useRef(0);
 const report=connected&&result?.tenant_id===tenant?result:null;
 useEffect(()=>{sequence.current++;setCaseId('');setExpectedNet('');setReport(null);setError('');setBusy(false);return()=>{sequence.current++}},[tenant,connected]);
 const invalidate=()=>{sequence.current++;setBusy(false);setReport(null);setError('')};
 const inspect=async e=>{
  e.preventDefault();const current=++sequence.current;setBusy(true);setError('');setReport(null);
  const amount=expectedNet.trim()===''?undefined:Number(expectedNet);
  if(amount!==undefined&&(!/^\d+$/.test(expectedNet)||!Number.isSafeInteger(amount)||amount<0)){setError('外部凭证净额请填写非负整数分。');setBusy(false);return}
  try{const result=await operationsApi.caseValidation(tenant,caseId.trim(),amount);if(current===sequence.current)setReport(result)}catch(err){if(current===sequence.current)setError(err.message)}finally{if(current===sequence.current)setBusy(false)}
 };
 const download=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='case-validation-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 const comparison=report?.external_comparison;
 return <section className="case-validation dashboard-card" aria-label="真实案例证据核对">
  <div className="section-title"><h2>真实案例证据核对</h2>{report&&<Button icon={DownloadSimple} onClick={download}>下载报告</Button>}</div>
  <p className="muted">核对脱敏案件的来源、委托、策略、保护状态和回款证据，再与外部凭证净额比对。此操作不触发外呼或支付。</p>
  <ol className="case-validation-steps"><li>导入脱敏案件，由另一位管理员复核</li><li>审批资产包策略，完成付款与退款流程</li><li>核对全部期间凭证净额，提交独立复核</li></ol>
  {!connected?<div className="notice"><WarningCircle size={20}/><span>当前为演示模式。连接业务 API 后可核对实际导入的案件，演示样本不会生成真实验证结论。</span></div>:<form className="case-validation-form" onSubmit={inspect}>
   <Field label="案件编号" hint="仅填写脱敏编号，无需姓名、手机号或身份证"><input required maxLength={40} value={caseId} onChange={e=>{invalidate();setCaseId(e.target.value)}} placeholder="输入已导入的案件编号"/></Field>
   <Field label="外部凭证净额（分，可选）" hint="同一案件全部期间付款减退款，填写整数分；原始凭证由业务方保管"><input inputMode="numeric" pattern="[0-9]+" maxLength={16} value={expectedNet} onChange={e=>{invalidate();setExpectedNet(e.target.value)}} placeholder="例如 7000 表示 70.00 元"/></Field>
   <Button type="submit" variant="primary" disabled={busy||!caseId.trim()}>{busy?'正在核对':'核对案例证据'}</Button>
  </form>}
  {error&&<p className="form-error" role="alert">{error}</p>}
  {report&&<div aria-live="polite">
   <div className="notice"><WarningCircle size={20}/><span>{report.status==='ready_for_external_review'?'服务端证据链具备外部复核条件':'证据尚不完整'}。来源真实性、实际服务商执行及银行凭证仍需业务方核实。</span></div>
   {comparison&&<div className="notice" role="status"><WarningCircle size={20}/><span>{comparisonLabels[comparison.status]}。手工录入金额不能替代外部凭证或真实业务验收。</span></div>}
   <div className="case-validation-checks">{report.checks.map(check=><article key={check.id}>{check.passed?<CheckCircle size={20}/>:<WarningCircle size={20}/>}<div><b>{check.label}</b><small>{check.detail}</small></div><span>{check.passed?'已具备':'待处理'}</span></article>)}</div>
   <KeyValue items={[["案件",report.case_id],["来源摘要",report.source_digest||'无独立复核导入来源'],["付款账簿总额",money(report.payment_total_cents)],["退款账簿总额",money(report.refund_total_cents)],["净回款（全部期间）",money(report.confirmed_net_recovery_cents)],["外部凭证净额",money(comparison?.expected_net_recovery_cents)],["差异（平台减外部）",money(comparison?.difference_cents)],["账簿记录",report.ledger.length],["报告摘要",report.report_digest],["核对时间",new Date(report.generated_at).toLocaleString('zh-CN',{hour12:false})]]}/>
  </div>}
 </section>;
}

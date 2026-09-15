import React,{useMemo,useState} from 'react';
import {ArrowRight,Clock,ShieldCheck,UserCircle,WarningCircle} from '@phosphor-icons/react';
import {useApp} from './context';
import {Badge,Button,Empty,Metrics,PageHead,Search,Tabs} from './ui';

const activeStatuses=['open','pending_review','permanent_hold'];
const categoryNames={debt_dispute:'债务异议',stop_contact:'停止联系',identity_conflict:'身份冲突',mandate_expired:'委托到期',data_quality:'资料缺失',amount_verification:'金额冲突',authorization_gap:'授权缺失',contact_data:'号码缺失',budget_exhausted:'预算耗尽',channel_failure:'渠道异常'};
const releaseConditions={maker_checker:'证据回执完成后，由独立管理员复核并重新评估。',renewal_evidence:'必须提交 MANDATE- 续期授权证据并通过独立复核。',permanent_hold:'不能通过通用流程解除；仅处理合法有效的后续请求。'};

function slaLabel(incident){
  if(incident.release_policy==='permanent_hold')return '持续保护';
  if(!incident.sla_due_at)return '未设时限';
  const diff=new Date(incident.sla_due_at).getTime()-Date.now();
  if(diff<=0)return '已超时';
  const hours=Math.floor(diff/3600000);
  const minutes=Math.floor(diff%3600000/60000);
  return `剩余 ${hours}小时 ${minutes}分`;
}

export function Exceptions(){
  const a=useApp();
  const [tab,setTab]=useState('全部');
  const [query,setQuery]=useState('');
  const rows=useMemo(()=>a.protectionOverview.incidents.filter(row=>activeStatuses.includes(row.status)).map(incident=>({incident,case:a.visibleCases.find(c=>c.case_id===incident.case_id)||{case_id:incident.case_id}})),[a.protectionOverview,a.visibleCases]);
  const filtered=rows.filter(({incident})=>(tab==='全部'||tab===incident.priority)&&(incident.case_id+(categoryNames[incident.category]||incident.category)+incident.owner+incident.reason).toLowerCase().includes(query.toLowerCase()));
  const p0=rows.filter(row=>row.incident.priority==='P0').length;
  const p1=rows.filter(row=>row.incident.priority==='P1').length;
  const p2=rows.filter(row=>row.incident.priority==='P2').length;
  const resolved=a.protectionOverview.incidents.filter(row=>row.status==='resolved').slice(0,5);
  return <>
    <PageHead title="异常中心" description="保护先于执行；服务端持久化保护事实、证据和独立复核。">
      <Button onClick={()=>a.setCopilotOpen(true)}>询问 AI 如何处理</Button>
    </PageHead>
    <Metrics items={[["保护暂停",a.protectionOverview.active_count,"新的触达已阻断"],["P0 异常",p0,"需优先处理"],["等待复核",a.protectionOverview.pending_review_count,"提案人不能自审"],["超出 SLA",a.protectionOverview.overdue_count,"保护继续生效"]]}/>
    <div className="exception-principle"><ShieldCheck size={21}/><span><b>保护状态立即生效，解除不会恢复原活动</b><small>事件会阻断案件和相关活动；独立复核通过后，案件只进入“待重新评估”。停止联系保护禁止通用解除。</small></span></div>
    <Tabs value={tab} onChange={setTab} items={[{id:'全部',label:'全部',count:rows.length},{id:'P0',label:'P0 紧急',count:p0},{id:'P1',label:'P1 重要',count:p1},{id:'P2',label:'P2 常规',count:p2}]}/>
    <div className="table-toolbar"><Search value={query} onChange={setQuery} placeholder="搜索异常、案件或责任方…"/><span className="muted small">租户隔离 · 证据摘要 · 乐观版本控制</span></div>
    <div className="exception-list">
      {filtered.map(({incident})=><article className={`exception-row priority-${incident.priority.toLowerCase()}`} key={incident.id}>
        <span className="exception-icon"><WarningCircle size={22}/></span>
        <div className="exception-main"><span className="exception-overline">{incident.id} · {incident.priority} · v{incident.version}</span><button onClick={()=>a.openCase(incident.case_id)}>{incident.case_id} · {categoryNames[incident.category]||incident.category}</button><p>{incident.reason}</p><small>{releaseConditions[incident.release_policy]}</small></div>
        <div className="exception-meta"><span><UserCircle size={15}/>{incident.owner}</span><span><Clock size={15}/>{slaLabel(incident)}</span><small>来源 {incident.source_event_id}</small></div>
        <div className="exception-actions"><Badge status="blocked">{incident.status==='pending_review'?'等待独立复核':incident.status==='permanent_hold'?'持续保护':'已保护'}</Badge><Button onClick={()=>a.setDialog({type:'exception',incidentId:incident.id,caseId:incident.case_id})}>{incident.status==='pending_review'?'复核提案':incident.status==='permanent_hold'?'查看边界':'提交回执'}</Button><button className="text-link" onClick={()=>a.openCase(incident.case_id,'记录')}>查看案件证据 <ArrowRight size={14}/></button></div>
      </article>)}
      {!filtered.length&&<Empty title="没有匹配的保护事件" description="当前筛选条件下没有需要处理的保护事件。"/>}
    </div>
    {resolved.length>0&&<section className="review-history protection-history"><div className="section-title"><h2>最近完成复核</h2><span className="muted small">解除后仍需重新预检，不自动恢复原活动</span></div>{resolved.map(row=><div key={row.id}><Badge status="completed">已复核</Badge><span><b>{row.case_id} · {categoryNames[row.category]||row.category}</b><small>{row.reviewed_by} · {row.case_released?'案件等待重新评估':'仍有其他保护事件'}</small></span><code>v{row.version}</code></div>)}</section>}
  </>;
}

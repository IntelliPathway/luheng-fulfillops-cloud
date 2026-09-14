import React,{useMemo,useState} from 'react';
import {ArrowRight,Clock,ShieldCheck,UserCircle,WarningCircle} from '@phosphor-icons/react';
import {useApp} from './context';
import {exceptionMeta} from './model';
import {Badge,Button,Empty,Metrics,PageHead,Search,Tabs} from './ui';

export function Exceptions(){
  const a=useApp();
  const [tab,setTab]=useState('全部');
  const [query,setQuery]=useState('');
  const rows=useMemo(()=>a.visibleCases.filter(c=>c.blocked).map(c=>({case:c,...exceptionMeta[c.case_id]})).filter(row=>row.id),[a.visibleCases]);
  const filtered=rows.filter(row=>(tab==='全部'||tab===row.priority)&&(row.case.case_id+row.category+row.owner+row.case.reason).toLowerCase().includes(query.toLowerCase()));
  const p0=rows.filter(row=>row.priority==='P0').length;
  const p1=rows.filter(row=>row.priority==='P1').length;
  const p2=rows.filter(row=>row.priority==='P2').length;
  return <>
    <PageHead title="异常中心" description="保护先于执行；集中处理阻断 AI 的业务例外。">
      <Button onClick={()=>a.setCopilotOpen(true)}>询问 AI 如何处理</Button>
    </PageHead>
    <Metrics items={[["保护暂停",rows.length,"新的触达已阻断"],["P0 异常",p0,"需优先处理"],["临近 SLA",3,"未来 4 小时"],["越权动作",0,"保护生效后"]]}/>
    <div className="exception-principle"><ShieldCheck size={21}/><span><b>保护状态立即生效</b><small>取消排队动作；在途动作进入确认；仅在解除条件和权限校验通过后重新评估。</small></span></div>
    <Tabs value={tab} onChange={setTab} items={[{id:'全部',label:'全部',count:rows.length},{id:'P0',label:'P0 紧急',count:p0},{id:'P1',label:'P1 重要',count:p1},{id:'P2',label:'P2 常规',count:p2}]}/>
    <div className="table-toolbar"><Search value={query} onChange={setQuery} placeholder="搜索异常、案件或责任方…"/><span className="muted small">按优先级与剩余时限排序</span></div>
    <div className="exception-list">
      {filtered.map(row=><article className={`exception-row priority-${row.priority.toLowerCase()}`} key={row.id}>
        <span className="exception-icon"><WarningCircle size={22}/></span>
        <div className="exception-main"><span className="exception-overline">{row.id} · {row.priority}</span><button onClick={()=>a.openCase(row.case.case_id)}>{row.case.case_id} · {row.category}</button><p>{row.case.reason}</p><small>{row.condition}</small></div>
        <div className="exception-meta"><span><UserCircle size={15}/>{row.owner}</span><span><Clock size={15}/>{row.sla}</span><small>创建于 {row.created}</small></div>
        <div className="exception-actions"><Badge status="blocked">已保护</Badge><Button onClick={()=>a.setDialog({type:'exception',caseId:row.case.case_id})}>处理异常</Button><button className="text-link" onClick={()=>a.openCase(row.case.case_id,'记录')}>查看证据 <ArrowRight size={14}/></button></div>
      </article>)}
      {!filtered.length&&<Empty title="没有匹配的异常" description="当前筛选条件下没有需要处理的保护事件。"/>}
    </div>
  </>;
}

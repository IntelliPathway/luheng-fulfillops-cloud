import React,{useEffect,useRef,useState} from 'react';
import {operationsApi} from './api';
import {Empty,Button,Modal} from './ui';
import {money} from './model';

function RecoveryDay({tenant,day,onClose}){
 const [page,setPage]=useState(1),[retry,setRetry]=useState(0),[state,setState]=useState({report:null,error:'',busy:true});
 const seq=useRef(0);
 useEffect(()=>{
  const current=++seq.current;setState({report:null,error:'',busy:true});
  operationsApi.recoveryDay(tenant,day,page).then(report=>{if(current===seq.current)setState({report,error:'',busy:false})})
   .catch(error=>{if(current===seq.current)setState({report:null,error:error.message,busy:false})});
  return()=>{seq.current++};
 },[tenant,day,page,retry]);
 const report=state.report?.tenant_id===tenant&&state.report?.date===day&&state.report?.page===page?state.report:null;
 return <Modal title="每日回款明细" subtitle={`${day} · UTC · 全工作空间`} wide onClose={onClose}>
  {state.error?<div role="alert"><Empty title="明细暂不可用" description="读取失败，请重试。" action={<Button onClick={()=>setRetry(x=>x+1)}>重试明细</Button>}/></div>:!report?<Empty title="正在读取每日账簿" description="付款为正，退款为负。"/>:<>
   <div className="recovery-day-summary"><b>当日净回款 {money(report.net_recovery_cents/100)}</b><span>{report.total} 条记录 · 应计佣金 {money(report.accrued_commission_cents/100)}</span></div>
   {report.items.length?<div className="recovery-day-table" tabIndex={0} role="region" aria-label="每日账簿记录"><table><thead><tr><th>案件 / 账簿编号</th><th>类型</th><th>回款金额</th><th>应计佣金</th><th>回执证据</th></tr></thead><tbody>{report.items.map(item=><tr key={item.entry_id}><td><b>{item.case_id}</b><small>{item.entry_id}</small></td><td>{item.event_type==='REFUND'?'退款':'付款'}</td><td>{money(item.amount_cents/100)}</td><td>{money(item.commission_cents/100)}</td><td>{item.evidence_status==='linked'?'已关联验签回执':'证据待补全'}<small>{item.receipt_id||'无关联回执'}</small></td></tr>)}</tbody></table></div>:<Empty title="当日暂无账簿记录" description="无记录日期的净回款为 0。"/>}
   {report.total>report.page_size&&<div className="recovery-day-pages"><Button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</Button><span>第 {page} / {Math.ceil(report.total/report.page_size)} 页</span><Button disabled={page*report.page_size>=report.total} onClick={()=>setPage(p=>p+1)}>下一页</Button></div>}
   <p className="muted recovery-day-note">关联验签回执仅表示平台证据可追溯，仍需外部银行或服务商核对，不能替代真实业务验收。</p>
  </>}
 </Modal>;
}

function DayPicker({tenant,report}){
 const [day,setDay]=useState(report.end_date),[open,setOpen]=useState(false);
 return <><div className="recovery-day-picker"><label>核对日期（UTC）<select aria-label="核对日期（UTC）" value={day} onChange={e=>{setDay(e.target.value);setOpen(false)}}>{report.points.map(p=><option key={p.date} value={p.date}>{p.date} · {p.ledger_entry_count} 条</option>)}</select></label><Button onClick={()=>setOpen(true)}>查看当日明细</Button></div>{open&&<RecoveryDay key={`${tenant}:${day}`} tenant={tenant} day={day} onClose={()=>setOpen(false)}/>}</>;
}

export function RecoveryTrend({tenant,range}){
 const days={week:7,month:30,quarter:90}[range];
 const [state,setState]=useState({report:null,error:'',busy:true}),[retry,setRetry]=useState(0);
 const seq=useRef(0);
 useEffect(()=>{
  const current=++seq.current;setState({report:null,error:'',busy:true});
  operationsApi.recoveryTrend(tenant,days).then(report=>{if(current===seq.current)setState({report,error:'',busy:false})})
   .catch(error=>{if(current===seq.current)setState({report:null,error:error.message,busy:false})});
  return()=>{seq.current++};
 },[tenant,days,retry]);
 const report=state.report?.tenant_id===tenant&&state.report?.days===days?state.report:null;
 if(state.busy||(!report&&!state.error))return <Empty title="正在读取账簿趋势" description="按 UTC 日期统计当前工作空间。"/>;
 if(state.error)return <div role="alert"><Empty title="趋势暂不可用" description="读取失败，未使用演示数据补齐。" action={<Button onClick={()=>setRetry(x=>x+1)}>重试趋势</Button>}/></div>;
 const max=Math.max(1,...report.points.map(p=>Math.abs(p.net_recovery_cents)));
 const x0=60,width=520,slot=width/report.points.length,base=105,height=72;
 return <div className="ledger-trend" aria-label="服务端回款趋势">
  <div className="ledger-trend-meta"><b>期间净回款 {money(report.net_recovery_cents/100)}</b><span>{report.ledger_entry_count} 条账簿记录</span></div>
  <svg viewBox="0 0 600 215" role="img" aria-label={`近 ${days} 天服务端净回款趋势，UTC，单位元`}>
   <title>全工作空间净回款，按服务商事件 UTC 日期，退款计负值</title>
   {[1,0,-1].map(v=><g key={v}><line x1={x0} x2={580} y1={base-v*height} y2={base-v*height} stroke="var(--border)"/><text x={x0-6} y={base-v*height+4} textAnchor="end" fill="var(--muted)" fontSize="10">{Math.abs(v*max/100)>=10000?`${(v*max/1000000).toFixed(1)}万`:(v*max/100).toLocaleString('zh-CN',{maximumFractionDigits:2})}</text></g>)}
   {report.points.map((point,i)=>{const h=Math.abs(point.net_recovery_cents)/max*height;return <rect key={point.date} x={x0+i*slot+slot*.15} y={point.net_recovery_cents<0?base:base-h} width={slot*.7} height={h} rx={Math.min(3,slot/4)} fill={point.net_recovery_cents<0?'var(--muted)':'var(--primary)'}><title>{point.date}：{money(point.net_recovery_cents/100)} · {point.ledger_entry_count} 条</title></rect>})}
   <text x={x0} y={200} fill="var(--muted)" fontSize="10">{report.start_date}</text><text x={580} y={200} textAnchor="end" fill="var(--muted)" fontSize="10">{report.end_date}</text>
  </svg>
  <p className="muted">全工作空间 · UTC · 按服务商事件时间，退款计负值；无记录日期为 0。</p>
  {report.ledger_entry_count===0&&<p className="muted">所选期间暂无回款账簿记录。</p>}
  <DayPicker key={`${tenant}:${days}:${report.end_date}`} tenant={tenant} report={report}/>
 </div>;
}

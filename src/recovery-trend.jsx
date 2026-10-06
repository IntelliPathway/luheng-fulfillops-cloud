import React,{useEffect,useRef,useState} from 'react';
import {operationsApi} from './api';
import {Empty,Button} from './ui';
import {money} from './model';

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
 </div>;
}

import React,{useEffect,useState} from 'react';
import {CheckCircle,WarningCircle,ArrowClockwise} from '@phosphor-icons/react';
import {useApp} from './context';
import {operationsApi} from './api';
import {Button,Empty,Metrics,PageHead} from './ui';

const money=cents=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format((cents||0)/100);

export function PilotAcceptance(){
 const a=useApp();
 const [state,setState]=useState({loading:false,error:'',scorecard:null,metrics:null});
 const load=async()=>{if(a.backendStatus!=='connected')return;setState(s=>({...s,loading:true,error:''}));try{const [scorecard,metrics]=await Promise.all([operationsApi.pilotScorecard(a.tenant),operationsApi.metrics(a.tenant)]);setState({loading:false,error:'',scorecard,metrics})}catch(error){setState({loading:false,error:error.message,scorecard:null,metrics:null})}};
 useEffect(()=>{load()},[a.backendStatus,a.tenant]);
 if(a.backendStatus!=='connected')return <><PageHead title="试点验收" description="使用服务端权威数据判定试点是否可进入下一阶段。"/><Empty title="需要连接业务 API" description="Sites 和离线演示不会生成虚构的试点验收结论。"/></>;
 const s=state.scorecard,m=state.metrics;
 return <><PageHead title="试点验收" description="权威目录、不可变账簿、复核队列与运行指标的联合视图。"><Button icon={ArrowClockwise} disabled={state.loading} onClick={load}>{state.loading?'正在刷新':'刷新证据'}</Button></PageHead>{state.error&&<div className="form-error" role="alert">{state.error}</div>}{s&&<><section className={`notice ${s.status==='ready'?'success':''}`}>{s.status==='ready'?<CheckCircle size={20}/>:<WarningCircle size={20}/>}<b>{s.status==='ready'?'受控试点门禁已就绪':'还有事项需处理'}</b><span>证据生成于 {new Date(s.generated_at).toLocaleString('zh-CN',{hour12:false})}</span></section><Metrics items={[["在管案件",s.portfolio.case_count,`${s.portfolio.protected_case_count} 个保护暂停`],["运行活动",s.operations.running_activity_count,`${s.operations.active_protection_count} 个活动保护`],["确认净回款",money(s.money.confirmed_net_recovery_cents),"不可变账簿"],["待复核",Object.values(s.review_queue).reduce((x,y)=>x+y,0),"策略、计划与回执"]]}/><div className="workspace-grid"><section className="activity-section"><h2>验收阻断项</h2>{s.blockers.length?<ul className="guide">{s.blockers.map(item=><li key={item}>{item}</li>)}</ul>:<p className="notice success">无服务端阻断项。</p>}</section><section className="event-feed"><h2>运行健康</h2><dl className="kv"><div><dt>请求总数</dt><dd>{m?.requests_total||0}</dd></div><div><dt>p50</dt><dd>{m?.latency_ms?.p50||0} ms</dd></div><div><dt>p95</dt><dd>{m?.latency_ms?.p95||0} ms</dd></div><div><dt>指标样本</dt><dd>{m?.sample_size||0}</dd></div></dl></section></div></>}</>;
}

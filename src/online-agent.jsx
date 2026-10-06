import React,{useState} from 'react';
import {useApp} from './context';
import {agentApi} from './api';
import {useRemoteResource} from './remote-resource';
import {AgentCommand} from './ai';
import {Badge,Button,Empty,KeyValue,Metrics,PageHead,Tabs} from './ui';
import {money} from './model';

export function OnlineAgent({activity}){
 const a=useApp(),[tab,setTab]=useState('运行概览');
 const runs=useRemoteResource(true,`${a.tenant}:${activity.id}`,()=>agentApi.activityRuns(a.tenant,activity.id));
 const ledger=a.visibleLedger.filter(row=>activity.caseIds.includes(row.case_id));
 const total=key=>ledger.reduce((n,row)=>n+(Number(row[key])||0),0);
 const blocked=a.visibleCases.filter(row=>activity.caseIds.includes(row.case_id)&&row.blocked);
 return <>
  <PageHead title={`${activity.name} Agent`} description="活动状态、范围和冻结快照来自服务端；运行记录只展示已持久化的真实工具结果。"><Button onClick={()=>a.navigate('activities')}>返回履约活动</Button><Button disabled={runs.busy} onClick={runs.refresh}>刷新运行证据</Button></PageHead>
  <div className="agent-meta"><span>{activity.id} · {activity.package}</span><Badge status={activity.status}/><span>{activity.mode==='channel'?'受控渠道':'服务端沙箱活动'}</span></div>
  <Tabs items={['运行概览','工具轨迹','策略与版本','用量']} value={tab} onChange={setTab}/>
  {tab==='运行概览'&&<><section className="governance-panel"><h2>活动目标与等待条件</h2><p>{activity.goal}</p><p>{activity.next}</p><KeyValue items={[["案件范围",activity.caseIds.join('、')||'无案件'],["冻结策略版本",`v${activity.version}`],["活动预算",money(activity.budget)],["当前保护阻断",`${blocked.length} 个已加载案件`]]}/><div className="material-toolbar">{activity.caseIds.map(id=><Button key={id} onClick={()=>a.openCase(id)}>查看案件 {id}</Button>)}</div></section><Metrics items={[["范围内确认净回款",money(total('cash_yuan')),'累计账簿事实，非活动归因'],["范围内应计佣金",money(total('commission_yuan')),'累计账簿事实，非实收'],["可见运行证据",runs.data?.length??'—','最近 20 次，会话权限内']]}/><AgentCommand key={`${a.tenant}:${activity.id}`} activity={activity}/></>}
  {tab==='工具轨迹'&&<section className="governance-panel" aria-label="持久化工具轨迹"><h2>活动会话的持久化运行</h2><p className="muted">管理员可查看本租户活动会话；其他成员只查看自己的会话。这里不会生成模拟步骤、耗时或成本。</p>{runs.error&&<p role="alert" className="form-error">运行证据读取失败：{runs.error}</p>}{runs.busy&&<p role="status">正在读取运行证据…</p>}{(runs.data||[]).map(row=><article className="persisted-agent-run" key={row.id}><h3>{row.id} · {row.status}</h3><small>{row.provider} · {row.profile} · {row.started_at}</small>{row.tool_trace.map((trace,i)=><p key={i}><code>{trace.tool}</code> · {trace.status} · {trace.detail}</p>)}{!row.tool_trace.length&&<p>尚无工具结果。</p>}</article>)}{!runs.busy&&!runs.error&&!runs.data?.length&&<Empty title="尚无可见持久化运行" description="在运行概览向本活动 Agent 提问，完成后刷新运行证据。没有执行过的工具不会显示为完成。"/>}</section>}
  {tab==='策略与版本'&&<section className="governance-panel"><h2>活动冻结快照</h2><KeyValue items={Object.entries(activity.serviceSnapshot||{}).map(([key,value])=>[key,typeof value==='string'?value:JSON.stringify(value)])}/><p className="notice">当前资产包策略不一定是本活动冻结版本；不使用当前策略推断历史执行参数。</p><Button onClick={()=>a.setDialog({type:'policy',package:activity.package})}>查看与提案新策略</Button></section>}
  {tab==='用量'&&<section className="governance-panel"><h2>活动成本归因待核对</h2><p>当前接口提供工作空间计量，尚无此活动完整成本归因；不使用固定模型调用数或预估成本代替。</p><Button onClick={()=>a.navigate('usage')}>查看服务端工作空间用量</Button></section>}
  {runs.error&&tab!=='工具轨迹'&&<p className="form-error" role="alert">运行证据读取失败：{runs.error}</p>}
 </>;
}

import React,{useEffect,useState} from 'react';
import {
  ArrowRight,ArrowUpRight,Brain,ChartBar,CheckCircle,Circle,Clock,
  Database,Files,Flask,GearSix,Pause,Play,Receipt,Robot,ShieldCheck,
  Sparkle,WarningCircle,Wrench
} from '@phosphor-icons/react';
import {useApp} from './context';
import {AgentCommand} from './ai';
import {knowledgeApi,operationsApi} from './api';
import {Badge,Button,Empty,Field,KeyValue,Metrics,PageHead,Search,Tabs} from './ui';
import {downloadCSV,money} from './model';

const sum=(rows,key)=>Math.round(rows.reduce((total,row)=>total+(Number(row[key])||0),0)*100)/100;

const agentTemplates=[
  {id:'AGT-RUNTIME-01',name:'自主履约 Agent',icon:Brain,description:'由 Hermes、DeepSeek Harness 或 LangGraph 承载目标分解、工具编排与跨天重规划。',status:'已连接',tools:8,runs:2},
  {id:'AGT-PAY-01',name:'回款核验 Agent',icon:Database,description:'验签、去重、匹配、分期分配与佣金判断。',status:'沙箱',tools:6,runs:1},
  {id:'AGT-GUARD-01',name:'保护与异常 Agent',icon:ShieldCheck,description:'实时阻断争议、停止联系、授权异常和冲突任务。',status:'运行中',tools:5,runs:6}
];

function AgentsHome(){
  const a=useApp();
  return <>
    <PageHead title="Agents" description="类自主智能体负责持续执行，确定性规则负责最终边界。"><Button variant="primary" onClick={()=>a.setCopilotOpen(true)}>通过对话下达目标</Button></PageHead>
    <div className="agent-template-grid">{agentTemplates.map(({icon:Icon,...agent})=><article key={agent.id}><span className="agent-template-icon"><Icon size={25}/></span><div><span className="eyebrow">{agent.id}</span><h2>{agent.name}</h2><p>{agent.description}</p></div><div className="agent-template-meta"><span><b>{agent.tools}</b><small>授权工具</small></span><span><b>{agent.runs}</b><small>当前运行</small></span><Badge status={agent.status==='运行中'?'running':'neutral'}>{agent.status}</Badge></div></article>)}</div>
    <section className="agent-run-list"><div className="section-title"><h2>当前运行</h2><span className="muted small">目标、等待条件与下一动作</span></div>{a.visibleActivities.map(activity=><button key={activity.id} onClick={()=>a.navigate(`agents/${activity.id}`)}><span className="agent-run-icon"><Robot size={20}/></span><span><b>{activity.name}</b><small>{activity.id} · {activity.package} · {activity.mode==='channel'?'授权渠道':'模拟运行'}</small></span><span><small>AI 下一步</small><b>{activity.next}</b></span><Badge status={activity.status}/><ArrowUpRight size={17}/></button>)}</section>
  </>;
}

export function AgentScreen(){
  const a=useApp();
  const [tab,setTab]=useState('运行概览');
  const [selectedCase,setSelectedCase]=useState('');
  const id=a.route.split('/')[1];
  if(!id)return <AgentsHome/>;
  const activity=a.visibleActivities.find(item=>item.id===id);
  if(!activity)return <Empty title="当前工作空间中没有此活动" description="切换工作空间后，只能查看当前组织的活动。" action={<Button onClick={()=>a.navigate('activities')}>返回履约活动</Button>}/>;
  const c=a.visibleCases.find(item=>item.case_id===(activity.caseIds.includes(selectedCase)?selectedCase:activity.caseIds[0]));
  const latestPolicy=a.getPolicy(activity.package);
  const policy=activity.policy||latestPolicy;
  const pkg=a.visiblePackages.find(item=>item.package_id===activity.package);
  const entries=a.visibleLedger.filter(item=>item.package_id===activity.package);
  const done=activity.status==='completed';
  const stage=done?5:a.runSteps[activity.id]??2;
  const paused=['paused','blocked'].includes(activity.status);
  const isC2=c?.case_id==='C002';
  const isC3=c?.case_id==='C003';
  const amount=isC2?2016:isC3?8000:c?.plan?.total_yuan;
  const installmentPaid=isC2?(a.paid?2016:1000):c?.cash||0;
  const gap=amount===undefined?undefined:Math.max(0,amount-installmentPaid);
  const runId=`RUN-${activity.id}-${c.case_id}`;
  const snapshot=activity.serviceSnapshot||{agent:'Hermes Agent · fulfill-agent-v3 · v3',model:'DeepSeek · deepseek-chat · v2',voice:'阿里云智能语音 · CosyVoice · v1',phone:'LiveKit SIP · 010****8800 · v1'};
  const steps=[
    ['读取事实','case.read_snapshot','案件、委托与保护状态'],
    ['核对到账','payment.reconcile','验签、去重、匹配与分期分配'],
    ['计算差额','payment.calculate_gap','确定性规则计算 ¥1,016'],
    ['计划触达','contact.plan','等待授权时段与渠道门禁'],
    ['等待事件','workflow.wait','仅以经核验回执更新状态']
  ];
  return <>
    <div className="agent-heading"><span className="agent-icon"><Robot size={34}/></span><div><span className="eyebrow">{runId}</span><h1>{activity.name} Agent</h1><p>{a.agentGateway.provider} 自主运行 · 目标、工具、观察结果、会话恢复与重规划均可追溯。</p></div><div className="head-actions"><Button onClick={()=>a.setDialog({type:'policy',package:activity.package})}>编辑策略</Button>{!['blocked','completed'].includes(activity.status)&&<Button icon={paused?Play:Pause} onClick={()=>a.setActivityStatus([activity.id],paused?'running':'paused')}>{paused?'恢复模拟':'暂停模拟'}</Button>}</div></div>
    <div className="agent-meta"><span>{activity.package} · {pkg.title}</span><span>策略 v{activity.version}.0</span><span>{activity.mode==='channel'?'授权渠道':'纯模拟'}</span><Badge status={activity.status}/><select aria-label="切换 Agent 活动" value={activity.id} onChange={event=>{a.navigate(`agents/${event.target.value}`);setTab('运行概览');setSelectedCase('')}}>{a.visibleActivities.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
    <Tabs value={tab} onChange={setTab} items={['运行概览','工具轨迹','策略与版本','用量']}/>
    {tab==='运行概览'&&<div className="agent-layout"><section className="agent-primary">
      <div className="section-title"><h2>计划与执行</h2><span className="muted small"><Clock size={14}/>下一检查 2026.09.13 10:00</span></div>
      <div className="run-surface"><div className="run-title"><button className="cell-title" onClick={()=>a.openCase(c.case_id)}>{c.case_id} · {activity.goal}</button><Badge status={activity.status}/></div>{activity.caseIds.length>1&&<select aria-label="切换运行案件" value={c.case_id} onChange={event=>setSelectedCase(event.target.value)}>{activity.caseIds.map(caseId=><option key={caseId}>{caseId}</option>)}</select>}
        <p className="run-description">{activity.status==='blocked'?c.reason:activity.status==='paused'?'新动作已暂停；排队动作已取消，在途动作等待确认。':done?(isC2?'本期已足额，Agent 已重新规划为等待下一期。':'当前目标已完成，结果与证据已归档。'):stage>=4?'正在等待经核验的支付或通话回执，不使用口头承诺更新账务。':'已读取事实并形成计划，准备在授权范围内执行下一工具。'}</p>
        {activity.status==='blocked'?<div className="blocked-state"><ShieldCheck size={38}/><h3>保护暂停已生效</h3><p>{c.reason}</p><Button onClick={()=>a.navigate('exceptions')}>进入异常中心</Button></div>:<>
          <div className="agent-plan-list">{steps.map(([name,tool,note],index)=>{const state=index<stage?'complete':index===stage?'current':'pending';return <div className={`agent-plan-step ${state} ${paused?'suspended':''}`} key={name}><span>{state==='complete'?<CheckCircle size={20} weight="fill"/>:state==='current'?<Clock size={20}/>:<Circle size={20}/>}</span><div><b><code>STEP-{String(index+1).padStart(2,'0')}</code>{name}</b><small>{note}</small></div><span><Wrench size={14}/>{tool}</span></div>})}</div>
          {amount!==undefined?<Metrics items={[[isC2?'本期应还':'方案应还',money(amount)],['确认到账',money(installmentPaid)],['剩余应还',money(gap)]]}/>:<div className="notice">尚无已签方案，不生成应还金额。</div>}
          <div className="decision-summary"><Sparkle size={18}/><div><b>决策摘要</b><p>{gap>0?`已签协议显示本期应还 ${money(amount)}，账务接口确认到账 ${money(installmentPaid)}，规则引擎计算差额 ${money(gap)}。下一动作需同时满足授权时段与渠道门禁。`:'经核验的付款事实已覆盖本期应还，Agent 将等待下一期而不是继续触达。'}</p></div></div>
          <div className="run-evidence"><Files size={16}/><span>证据：</span><button className="text-link" onClick={()=>a.openCase(c.case_id,'协议')}>协议与委托</button><span>·</span><button className="text-link" onClick={()=>a.openCase(c.case_id,'回款')}>到账核验链</button></div>
          <div className="next-action"><ArrowRight size={19}/><span>{done?'观察条件：下一期到期或收到新回款。':stage>=4?'等待条件：收到验签通过的支付/通话回调；超时后按策略重新规划。':activity.mode==='channel'?'下一动作：在授权窗口内调用 contact.plan；执行前再次检查保护、频次和号码用途。':'下一动作：调用 contact.plan 生成模拟回执，不触达真实号码。'}</span></div>
          <div className="run-actions">{activity.status==='running'&&stage<4&&<Button variant="primary" icon={Play} onClick={()=>a.advance(activity)}>推进沙箱模拟</Button>}{isC2&&!a.paid&&<Button disabled={paused} onClick={()=>a.setDialog({type:'receipt'})}>模拟支付回调</Button>}<button className="text-link" onClick={()=>a.openCase(c.case_id)}>查看完整案件 <ArrowUpRight size={14}/></button></div>
        </>}
      </div>
      <AgentCommand activity={activity}/>
    </section><aside className="agent-rail">
      <h2><ShieldCheck size={21}/>运行边界</h2><KeyValue items={[["Run ID",runId],["执行范围",`${activity.package} · ${activity.caseIds.length} 个案件`],["执行模式",activity.mode==='channel'?'授权渠道':'纯模拟'],["策略",`v${activity.version}.0 · 已发布`],["触达窗口",`${policy.start}—${policy.end}`],["频率限制",`${policy.daily} 次/日 · ${policy.weekly} 次/7日`],["单案预算",money(activity.budget)],["保护覆盖","异议、停止联系、身份/授权冲突"]]}/>
      <div className="rail-section"><h2><Brain size={21}/>服务快照</h2><KeyValue items={[["Agent",snapshot.agent],["模型",snapshot.model],["语音",snapshot.voice],["电话",snapshot.phone]]}/><button className="text-link" onClick={()=>a.navigate('integrations')}>查看接入状态 <ArrowRight size={14}/></button></div>
      <div className="rail-section"><h2><ChartBar size={21}/>经营结果</h2><div className="rail-money"><span>确认净回款<strong>{money(sum(entries,'cash_yuan'))}</strong></span><span>应计佣金<strong>{money(sum(entries,'commission_yuan'))}</strong></span></div><button className="text-link" onClick={()=>{a.setScope(activity.package);a.navigate('payments')}}>查看账务依据 <ArrowUpRight size={14}/></button></div>
    </aside></div>}
    {tab==='工具轨迹'&&<><div className="trace-toolbar"><div><h2>Agent 工具轨迹</h2><p>只展示业务可审计摘要，不展示模型私密思维链。</p></div><code>{runId}</code></div><RunRows packageId={activity.package}/></>}
    {tab==='策略与版本'&&<div className="settings-content"><div className="section-title"><h2>活动冻结快照 · v{activity.version}.0</h2><Button onClick={()=>a.setDialog({type:'policy',package:activity.package})}>创建新策略版本</Button></div><div className="notice">新版本只作用于后续活动；停止联系、异议和授权撤销等保护规则立即覆盖所有运行。</div><KeyValue items={[["运行目标",activity.goal],["最低结算比例",`${policy.minSettlement||70}%`],["最大分期期数",`${policy.maxInstallments||6} 期`],["最低首付比例",`${policy.minDownPayment||20}%`],["触达窗口",`${policy.start}—${policy.end}`],["重试间隔",`至少 ${policy.retry} 小时`]]}/><div className="version-row"><span className="version-label">v{latestPolicy.version}.0</span><span><b>{latestPolicy.status==='published'?'已发布策略':'草稿策略'}</b><small>样本回放 {latestPolicy.evaluated?'已通过':'待执行'} · 2026.09.12</small></span><Badge status="completed">新活动可用</Badge></div></div>}
    {tab==='用量'&&<div className="settings-content"><Metrics items={[["模型调用",12,'次'],["工具调用",8,'次'],["模拟语音",0,'分钟'],["预估成本",money(.38),'不产生真实计费']]}/><div className="notice">用量按 Run、步骤、服务商和模型版本归集，可与回款和佣金计算净贡献。</div><Button onClick={()=>a.navigate('usage')}>查看工作空间用量</Button></div>}
  </>;
}

function RunRows({packageId}){
  const a=useApp();
  const events=a.events.filter(event=>event.tenant===a.tenant&&(!packageId||a.visibleCases.find(c=>c.case_id===event.caseId)?.package_id===packageId));
  return <><div className="table-scroll"><table className="data-table trace-table"><thead><tr><th>时间 / ID</th><th>案件</th><th>执行主体</th><th>工具 / 结果</th><th>版本</th><th>耗时 / 成本</th><th/></tr></thead><tbody>{events.map(event=><tr key={event.id}><td><b>{event.time}</b><small>{event.stepId} · {event.actionId}</small></td><td><button className="text-link" onClick={()=>a.openCase(event.caseId)}>{event.caseId}</button><small>{event.runId}</small></td><td>{event.actor}</td><td><code>{event.tool}</code><small>{event.title} · {event.status}</small></td><td>{event.version}</td><td>{event.latency}<small>{event.cost}</small></td><td><button className="text-link" onClick={()=>a.openCase(event.caseId,'记录')}>证据 <ArrowUpRight size={13}/></button></td></tr>)}</tbody></table></div>{!events.length&&<Empty title="暂无运行记录" description="创建活动并推进模拟后，事实、规则和工具回执会出现在这里。"/>}</>;
}

export function Logs(){
  const a=useApp();
  const [query,setQuery]=useState('');
  const [tab,setTab]=useState('全部');
  const [auditRows,setAuditRows]=useState([]);
  const [loading,setLoading]=useState(false);
  const prefix=tab==='Agent 决策'?'agent.':tab==='账务事件'?'payment.':tab==='异常与阻断'?'protection.':'';
  useEffect(()=>{if(a.backendStatus!=='connected')return;let active=true;setLoading(true);const timer=setTimeout(()=>operationsApi.auditEvents(a.tenant,{query,actionPrefix:prefix,limit:200}).then(rows=>active&&setAuditRows(rows)).catch(error=>a.notify(`审计记录读取失败：${error.message}`)).finally(()=>active&&setLoading(false)),180);return()=>{active=false;clearTimeout(timer)}},[a.backendStatus,a.tenant,query,prefix]);
  const rows=a.events.filter(event=>event.tenant===a.tenant&&(event.caseId+event.title+event.detail+event.tool+event.runId).toLowerCase().includes(query.toLowerCase())&&(tab==='全部'||(tab==='异常与阻断'?event.type==='exception':tab==='账务事件'?event.type==='payment':event.actor==='Hermes Agent')));
  const exportRows=()=>{const source=a.backendStatus==='connected'?auditRows:rows;downloadCSV('履约智控审计记录.csv',['时间','主体','动作','资源类型','资源编号','证据摘要'],source.map(row=>a.backendStatus==='connected'?[row.created_at,row.actor_id,row.action,row.resource_type,row.resource_id,JSON.stringify(row.detail)]:[row.time,row.actor,row.tool,row.type,row.caseId,row.detail]));a.notify(`已导出 ${source.length} 条审计记录`)};
  return <><PageHead title="运行记录" description="服务端审计事件与 Agent 工具证据统一追溯。"><span className="sync-label"><span className="live-dot"/>{a.backendStatus==='connected'?'服务端审计账本':'离线演示记录'}</span><Button onClick={exportRows}>导出审计</Button></PageHead><Tabs value={tab} onChange={setTab} items={['全部','Agent 决策','账务事件','异常与阻断']}/><div className="table-toolbar"><Search value={query} onChange={setQuery} placeholder="搜索动作、资源、主体或编号…"/><span className="muted small">{loading?'正在读取…':'保留事实摘要，不记录私密思维链'}</span></div>{a.backendStatus==='connected'?<AuditRows rows={auditRows}/>:<RunRowsCustom rows={rows} a={a}/>}</>;
}

function AuditRows({rows}){return <><div className="table-scroll"><table className="data-table trace-table"><thead><tr><th>时间 / 事件</th><th>执行主体</th><th>动作</th><th>资源</th><th>证据摘要</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td><b>{new Date(row.created_at).toLocaleString('zh-CN',{hour12:false})}</b><small>{row.id}</small></td><td>{row.actor_id}</td><td><code>{row.action}</code></td><td>{row.resource_type}<small>{row.resource_id}</small></td><td><small className="audit-detail">{Object.entries(row.detail||{}).map(([key,value])=>`${key}: ${String(value)}`).join(' · ')||'—'}</small></td></tr>)}</tbody></table></div>{!rows.length&&<Empty title="暂无匹配审计事件" description="关键写操作将在这里形成租户隔离的服务端证据。"/>}</>}

function RunRowsCustom({rows,a}){
  return <><div className="table-scroll"><table className="data-table trace-table"><thead><tr><th>时间 / ID</th><th>案件</th><th>执行主体</th><th>工具 / 结果</th><th>版本</th><th>耗时 / 成本</th><th/></tr></thead><tbody>{rows.map(event=><tr key={event.id}><td><b>{event.time}</b><small>{event.stepId} · {event.actionId}</small></td><td><button className="text-link" onClick={()=>a.openCase(event.caseId)}>{event.caseId}</button><small>{event.runId}</small></td><td>{event.actor}</td><td><code>{event.tool}</code><small>{event.title} · {event.status}</small></td><td>{event.version}</td><td>{event.latency}<small>{event.cost}</small></td><td><button className="text-link" onClick={()=>a.openCase(event.caseId,'记录')}>证据 <ArrowUpRight size={13}/></button></td></tr>)}</tbody></table></div>{!rows.length&&<Empty/>}</>;
}

export function Strategy(){
  const a=useApp();
  const [tab,setTab]=useState('授权策略');
  const [query,setQuery]=useState('');
  const [documents,setDocuments]=useState([]);
  const [draft,setDraft]=useState(null);
  const [busy,setBusy]=useState(false);
  const fallbackKnowledge=[
    {id:'KNOW-ID-03',document_key:'KNOW-ID',title:'身份核验与披露边界',summary:'核验本人前不披露债务信息；身份冲突立即暂停。',status:'published',version:3,source_reference:'离线政策包'},
    {id:'KNOW-NEG-07',document_key:'KNOW-NEG',title:'长账龄协商说明',summary:'说明金额构成、方案有效期和授权边界，不自行承诺。',status:'published',version:7,source_reference:'离线政策包'},
    {id:'KNOW-PAY-04',document_key:'KNOW-PAY',title:'履约与到账口径',summary:'口头承诺不视为到账，仅使用经核验账务事实。',status:'published',version:4,source_reference:'离线账务口径'},
    {id:'KNOW-GUARD-09',document_key:'KNOW-GUARD',title:'异议与停止联系',summary:'识别请求后立即保护并生成异常工单。',status:'published',version:9,source_reference:'离线保护政策'}
  ];
  useEffect(()=>{if(a.backendStatus!=='connected')return;let active=true;knowledgeApi.list(a.tenant).then(rows=>{if(active)setDocuments(rows)}).catch(error=>a.notify(`知识版本读取失败：${error.message}`));return()=>{active=false}},[a.backendStatus,a.tenant]);
  const knowledge=(a.backendStatus==='connected'?documents:fallbackKnowledge).filter(item=>(item.title+item.document_key+item.summary).includes(query));
  const createDocument=async()=>{setBusy(true);try{const bytes=new TextEncoder().encode(`${draft.source_reference}\n${draft.summary}`);const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');await knowledgeApi.create(a.tenant,{...draft,content_digest:digest});setDocuments(await knowledgeApi.list(a.tenant));setDraft(null);a.notify('知识版本已提交，等待独立复核')}catch(error){a.notify(`提交失败：${error.message}`)}finally{setBusy(false)}};
  const decideDocument=async(document,decision)=>{setBusy(true);try{await knowledgeApi.decide(a.tenant,document.id,{decision,expected_version:document.version,review_note:decision==='approve'?'来源与摘要已独立核验':'来源或摘要需要修订'});setDocuments(await knowledgeApi.list(a.tenant));a.notify(decision==='approve'?'知识版本已发布':'知识版本已驳回')}catch(error){a.notify(`复核失败：${error.message}`)}finally{setBusy(false)}};
  return <><PageHead title="策略与知识" description="授权政策控制能做什么；知识库帮助 Agent 正确解释。"><Button onClick={()=>a.setCopilotOpen(true)}>向知识库提问</Button></PageHead><Tabs value={tab} onChange={setTab} items={['授权策略','知识库','评估记录']}/>
    {tab==='授权策略'&&<><div className="notice"><ShieldCheck size={19}/>策略发布前必须完成样本回放；保护规则可以即时覆盖运行中旧版本。</div>{a.visiblePackages.map(p=>{const policy=a.getPolicy(p.package_id);return <div className="policy-row" key={p.package_id}><span className="asset-icon"><ShieldCheck size={24}/></span><div><h3>{p.title} · 授权策略</h3><p>{p.package_id} · 最低结算 {policy.minSettlement}% · 最多 {policy.maxInstallments} 期 · 首付至少 {policy.minDownPayment}%</p></div><span className="policy-status"><Badge status="completed">已发布</Badge><small>v{policy.version}.0 · 回放通过</small></span><Button onClick={()=>a.setDialog({type:'policy',package:p.package_id})}>新建版本</Button></div>})}</>}
    {tab==='知识库'&&<><div className="knowledge-overview"><div><BookOpenCard icon={Files} value={String(knowledge.length)} label="可见版本"/><BookOpenCard icon={Database} value={String(knowledge.filter(x=>x.status==='published').length)} label="已发布"/><BookOpenCard icon={CheckCircle} value={String(knowledge.filter(x=>x.status==='pending_review').length)} label="待独立复核"/></div><p>Agent 只检索当前租户已发布版本；正文不进入运营接口，只保存来源引用、摘要与 SHA-256 指纹。</p></div><div className="table-toolbar"><Search value={query} onChange={setQuery} placeholder="搜索知识条目…"/><Button disabled={a.backendStatus!=='connected'} onClick={()=>setDraft({document_key:'KNOW-',title:'',category:'policy',source_reference:'',summary:''})}>新建受审版本</Button></div>{draft&&<section className="knowledge-proposal"><div><Field label="知识键"><input value={draft.document_key} onChange={e=>setDraft({...draft,document_key:e.target.value.toUpperCase()})}/></Field><Field label="标题"><input value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})}/></Field><Field label="来源引用"><input value={draft.source_reference} onChange={e=>setDraft({...draft,source_reference:e.target.value})}/></Field><Field label="可检索摘要"><textarea value={draft.summary} onChange={e=>setDraft({...draft,summary:e.target.value})}/></Field></div><footer><small>提交后由另一名管理员核验；发布新版本会自动退役旧版本。</small><Button onClick={()=>setDraft(null)}>取消</Button><Button variant="primary" disabled={busy||draft.document_key.length<3||draft.title.length<3||draft.source_reference.length<3||draft.summary.length<8} onClick={createDocument}>提交复核</Button></footer></section>}<div className="knowledge-table">{knowledge.map(document=><article key={document.id}><span className="knowledge-icon"><Files size={19}/></span><div><span className="eyebrow">{document.document_key} · {document.source_reference}</span><h3>{document.title}</h3><p>{document.summary}</p></div><div><Badge status={document.status==='published'?'completed':document.status==='pending_review'?'paused':'neutral'}>{document.status==='published'?'已发布':document.status==='pending_review'?'待复核':document.status==='retired'?'已退役':'已驳回'}</Badge><small>v{document.version} · {document.content_digest?`${document.content_digest.slice(0,8)}…`:'离线样本'}</small>{document.status==='pending_review'&&a.identity.role==='admin'&&document.proposed_by!==a.identity.actor_id&&<Button disabled={busy} onClick={()=>decideDocument(document,'approve')}>批准</Button>}</div></article>)}</div>{!knowledge.length&&<Empty title="没有知识版本" description="创建第一个带来源引用和内容指纹的受审版本。"/>}</>}
    {tab==='评估记录'&&<div className="evaluation-history"><article><Flask size={22}/><span><b>EVAL-20260912-014 · 策略与话术联合回放</b><small>24 个场景 · 越权 0 · 保护漏拦截 0 · 金额事实错误 0</small></span><Badge status="completed">通过</Badge></article><article><Flask size={22}/><span><b>EVAL-20260910-011 · 知识检索评估</b><small>引用命中 98.6% · 无来源回答 0 · 平均检索 82 ms</small></span><Badge status="completed">通过</Badge></article></div>}
  </>;
}

function BookOpenCard({icon:Icon,value,label}){return <span><Icon size={19}/><b>{value}</b><small>{label}</small></span>}

export function Usage(){
  const a=useApp();
  const [tab,setTab]=useState('服务用量');
  return <><PageHead title="用量与账单" description="按 Agent、模型、语音和电话归集成本，并计算净贡献。"/><div className="plan-surface"><div><span className="plan-tag">Team</span><h2>{a.organization[a.tenant]} 的工作空间计划</h2><p>多资产包 · 自主 Agent · ChatBI · 回款与佣金核对</p></div><Badge>演示计划</Badge></div><Tabs value={tab} onChange={setTab} items={['服务用量','账单记录']}/>{tab==='账单记录'?<Empty title="暂无计费账单" description="当前为演示环境，模拟操作不会产生真实费用。"/>:<><Metrics items={[["Agent 运行",18,'Run'],["模型调用",146,'次'],["语音时长",0,'分钟'],["直接运营成本",money(42.6),'模拟估算']]}/><div className="service-row"><Brain size={22}/><span><b>{a.agentGateway.provider} 与模型服务</b><small>按 Run、Token、工具、检查点和版本归集；当前为沙箱估算。</small></span><Badge status="neutral">¥18.40</Badge></div><div className="service-row"><Receipt size={22}/><span><b>经营贡献</b><small>应计佣金 ¥2,778 − 模拟直接成本 ¥42.60。</small></span><strong>{money(2735.4)}</strong></div></>}</>;
}

export function Settings(){
  const a=useApp();
  const [name,setName]=useState(a.organization[a.tenant]);
  const [saved,setSaved]=useState(false);
  return <><PageHead title="组织设置" description="管理工作空间、权限和演示偏好。"/><div className="settings-content"><h2>工作空间</h2><Field label="工作空间名称"><input value={name} maxLength={24} onChange={event=>{setName(event.target.value);setSaved(false)}}/></Field><Field label="工作空间标识"><input value={a.tenant} disabled/></Field><Button variant="primary" disabled={!name.trim()} onClick={()=>{a.setOrganization(old=>({...old,[a.tenant]:name.trim()}));setSaved(true);a.notify('工作空间名称已更新')}}>{saved?'已保存':'保存修改'}</Button><div className="section-title separated"><h2>AI 权限默认值</h2></div><div className="preference-row"><span><b>高影响动作需要确认</b><small>触达、预算、策略发布、解除保护和账务变更均不能由对话直接执行。</small></span><Badge status="completed">已启用</Badge></div><div className="preference-row"><span><b>租户内知识检索</b><small>ChatBI 与知识库查询不跨工作空间拼接数据。</small></span><Badge status="completed">已启用</Badge></div><div className="section-title separated"><h2>演示通知</h2></div>{[['exception','异常状态提示','集中关注需要暂停的案件。'],['daily','每日经营摘要','偏好仅保存在本次会话。']].map(([key,label,note])=><label className="preference-row" key={key}><span><b>{label}</b><small>{note}</small></span><input role="switch" type="checkbox" checked={a.notificationPreferences[key]} onChange={event=>a.setNotificationPreferences(old=>({...old,[key]:event.target.checked}))}/></label>)}<div className="section-title separated"><h2>演示管理</h2></div><p className="muted">刷新页面会保留 AI 与渠道的脱敏配置和门禁状态；还原演示会重置全部样本。</p><Button onClick={()=>a.setDialog({type:'reset'})}>还原演示数据</Button></div></>;
}

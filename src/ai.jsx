import React,{useMemo,useState} from 'react';
import {
  ArrowRight,BookOpen,Brain,ChartBar,CheckCircle,Database,Files,
  Lightning,MagnifyingGlass,PaperPlaneRight,Robot,ShieldCheck,Sparkle,X
} from '@phosphor-icons/react';
import {useApp} from './context';
import {money} from './model';
import {Button,IconButton} from './ui';

const sum=(rows,key)=>Math.round(rows.reduce((total,row)=>total+(Number(row[key])||0),0)*100)/100;

function answerFor(query,a){
  const text=query.trim();
  const lower=text.toLowerCase();
  const caseMatch=text.match(/C\d{3}/i);
  const targetCase=caseMatch?a.visibleCases.find(c=>c.case_id===caseMatch[0].toUpperCase()):null;

  if(targetCase){
    return {
      title:`${targetCase.case_id} 当前状态`,
      body:targetCase.blocked
        ? `案件处于保护暂停：${targetCase.reason}。在解除条件满足前，Agent 不会安排新的触达。`
        : `案件账龄 ${targetCase.ageMonths} 个月，当前状态为“${targetCase.status}”，确认净回款 ${money(targetCase.cash)}，下一允许动作：${targetCase.nextAllowed}。`,
      facts:[['转让余额',money(targetCase.transfer_balance_yuan)],['数据质量',`${targetCase.dataQuality}%`],['已签方案',targetCase.plan?.status==='SIGNED'?'是':'否']],
      sources:['案件主数据','委托授权','协议与回款账本'],
      action:{label:'打开案件',run:()=>a.openCase(targetCase.case_id)}
    };
  }

  if(/创建|启动|下达|安排/.test(text)){
    const channelReady=a.integrationReadiness.ready;
    return {
      title:'已生成任务草案',
      body:`我已把自然语言意图转换为结构化活动草案。执行前仍需确认资产包、案件范围、策略版本、渠道门禁与预算；当前${channelReady?'四类服务与全链路自测已启用，可按授权范围创建渠道活动':'渠道门禁尚未通过，只能创建纯模拟活动'}。`,
      facts:[['建议目标','已签协议履约'],['建议范围','PKG_A · 2 个可执行案件'],['执行模式',channelReady?'已启用渠道':'纯模拟']],
      sources:['案件资格规则','策略 v1.0','渠道启用状态'],
      proposal:true,
      action:{label:'审阅任务草案',run:()=>a.setDialog({type:'create',package:'PKG_A'})}
    };
  }

  if(/回款|佣金|收入|钱|经营/.test(text)||lower.includes('bi')){
    const cash=sum(a.visibleLedger,'cash_yuan');
    const eligible=sum(a.visibleLedger,'eligible_cash_yuan');
    const commission=sum(a.visibleLedger,'commission_yuan');
    return {
      title:'经营指标查询结果',
      body:`当前工作空间累计确认净回款 ${money(cash)}，其中计佣回款 ${money(eligible)}，应计佣金 ${money(commission)}。实收佣金仍为 ¥0，因此不能把应计金额当作现金收入。`,
      facts:[['确认净回款',money(cash)],['计佣回款',money(eligible)],['应计佣金',money(commission)]],
      sources:['已确认回款账本','佣金规则版本','结算台账'],
      action:{label:'查看回款与佣金',run:()=>a.navigate('payments')}
    };
  }

  if(/异常|暂停|不能联系|风险|保护/.test(text)){
    const blocked=a.visibleCases.filter(c=>c.blocked);
    const p0=blocked.filter(c=>['C010','C012','C013','C018','C020'].includes(c.case_id)).length;
    return {
      title:'保护与异常查询结果',
      body:`当前有 ${blocked.length} 个案件处于保护暂停，其中 ${p0} 个属于高优先级。异议、停止联系、金额冲突、授权缺失和身份冲突均已阻断新的触达动作。`,
      facts:[['保护暂停',`${blocked.length} 个`],['P0 异常',`${p0} 个`],['越权动作','0 次']],
      sources:['保护状态机','异常工单','运行审计'],
      action:{label:'进入异常中心',run:()=>a.navigate('exceptions')}
    };
  }

  if(/策略|优化|分期|协商/.test(text)){
    return {
      title:'策略分析建议',
      body:'PKG_A 的履约型任务已有确认回款，但首次联络型案件缺少基于账龄、数据质量和预期净贡献的分层。建议先对高质量、可联系、委托期充足的案件运行沙箱回放，再提交策略评估与发布。',
      facts:[['建议优先级','履约差额 > 新联络'],['评估方式','历史样本回放'],['发布方式','审批后小流量']],
      sources:['案件画像','历史运行结果','授权策略知识库'],
      action:{label:'查看策略与知识',run:()=>a.navigate('strategy')}
    };
  }

  return {
    title:'可以继续拆解这个问题',
    body:'我可以跨案件、活动、策略、回款、佣金、渠道和运行轨迹回答问题。查询结果会给出统计口径与来源；如果你的问题包含执行意图，我会先生成可审阅的行动草案。',
    facts:[['数据范围',a.organization[a.tenant]],['知识源','4 类已连接'],['写操作','必须确认']],
    sources:['案件仓库','策略知识库','回款账本','Agent 运行记录']
  };
}

function remoteAnswerFor(result,a){
  const payload=result.answer||{};
  const facts=(payload.facts||[]).map(item=>[item.label,item.value]);
  const sources=(payload.sources||[]).map(item=>`${item.label} · ${item.version}`);
  const hint=payload.navigation_hint||'';
  let action=null;
  if(result.proposal){
    action={label:'确认执行提案',run:async()=>{
      try{
        await a.confirmAgentProposal(result.proposal.id);
        const {activity_id:activityId,target_status:targetStatus}=result.proposal.arguments||{};
        if(activityId&&targetStatus)a.setActivityStatus([activityId],targetStatus);
        a.notify('服务端已重新校验并执行行动提案');
      }catch(error){a.notify(`提案执行失败：${error.message||'服务不可用'}`)}
    }};
  }else if(hint.startsWith('case:'))action={label:'打开案件',run:()=>a.openCase(hint.split(':')[1])};
  else if(hint.startsWith('agent:'))action={label:'查看 Agent 运行',run:()=>a.navigate(`agents/${hint.split(':')[1]}`)};
  else if(hint==='payments')action={label:'查看回款与佣金',run:()=>a.navigate('payments')};
  else if(hint==='strategy')action={label:'查看策略与知识',run:()=>a.navigate('strategy')};
  else if(hint==='agents')action={label:'查看 Agent',run:()=>a.navigate('agents')};
  else if(hint==='cases')action={label:'查看案件',run:()=>a.navigate('cases')};
  else if(hint==='create'||hint.startsWith('create:'))action={label:'审阅活动草案',run:()=>a.setDialog({type:'create',package:hint.split(':')[1]||undefined})};
  return {...payload,facts,sources,proposal:Boolean(result.proposal),action,runId:result.run_id,provider:result.provider,runtime:result.runtime};
}

function SourceChips({sources}){
  return <div className="ai-source-chips">{sources.map((source,index)=><span key={`${source}-${index}`}><BookOpen size={12}/>{source}</span>)}</div>;
}

function AssistantAnswer({answer}){
  return <div className="assistant-answer">
    <div className="assistant-answer-title"><Sparkle size={16} weight="fill"/><b>{answer.title}</b>{answer.proposal&&<span>待确认</span>}</div>
    <p>{answer.body}</p>
    {answer.facts&&<div className="assistant-facts">{answer.facts.map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>}
    <SourceChips sources={answer.sources}/>
    {answer.runtime&&<div className="assistant-runtime"><Robot size={13}/><span>{answer.runtime.adapter} · {answer.runtime.resume_mode==='checkpoint-replay'?'检查点重放':answer.runtime.resume_mode==='in-process'?'进程内续接':answer.runtime.resumed?'已恢复会话':'新会话'} · Turn {answer.runtime.turn_count} · Cursor {answer.runtime.event_cursor}{Number.isInteger(answer.runtime.verified_tool_count)?` · 已核验工具 ${answer.runtime.verified_tool_count}`:''}</span></div>}
    {answer.action&&<button className="text-link assistant-action" onClick={answer.action.run}>{answer.action.label}<ArrowRight size={14}/></button>}
  </div>;
}

export function AICopilot(){
  const a=useApp();
  const [query,setQuery]=useState('');
  const [answer,setAnswer]=useState(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const suggestions=['查询 C002 当前状态','今天哪些案件不能联系？','本月回款和佣金是多少？','为 PKG_A 创建履约活动'];
  const submit=async value=>{
    const next=(value??query).trim();
    if(!next)return;
    setQuery('');setAnswer(null);setError('');setLoading(true);
    try{
      if(a.backendStatus==='connected'){
        const result=await a.askAgent(next,{scopeType:'global',title:'履衡 AI 全局查询'});
        setAnswer(remoteAnswerFor(result,a));
      }else setAnswer(answerFor(next,a));
    }catch(reason){setError(reason.message||'Agent 服务暂时不可用，请从运行记录恢复。')}
    finally{setLoading(false)}
  };
  return <>
    <button className="ai-copilot-launcher" onClick={()=>a.setCopilotOpen(true)} aria-label="打开履衡 AI 助手">
      <Sparkle size={18} weight="fill"/><span>问履衡 AI</span><kbd>⌘ J</kbd>
    </button>
    {a.copilotOpen&&<aside className="ai-copilot" role="dialog" aria-modal="false" aria-label="履衡 AI 助手">
      <header>
        <span className="copilot-brand"><span><Brain size={22}/></span><span><b>履衡 AI</b><small>Agent + 知识库 + ChatBI</small></span></span>
        <IconButton icon={X} label="关闭 AI 助手" onClick={()=>a.setCopilotOpen(false)}/>
      </header>
      <div className="copilot-scope"><ShieldCheck size={15}/><span>数据范围：{a.organization[a.tenant]} · {a.backendStatus==='connected'?`${a.agentGateway.provider} 服务端会话`:'离线知识演示'} · 写操作先生成草案</span></div>
      <div className="copilot-body">
        <div className="copilot-welcome">
          <span className="copilot-orb"><Robot size={27}/></span>
          <h2>从问题直接到依据与行动</h2>
          <p>查询案件、策略、任务和经营指标，或用自然语言向 Agent 下达一个目标。</p>
        </div>
        <div className="copilot-capabilities">
          <span><Files size={15}/>案件</span><span><BookOpen size={15}/>策略</span><span><ChartBar size={15}/>指标</span><span><Database size={15}/>账务</span>
        </div>
        {!answer&&!loading&&!error&&<div className="copilot-suggestions">{suggestions.map(item=><button key={item} onClick={()=>submit(item)}>{item}<ArrowRight size={13}/></button>)}</div>}
        {loading&&<div className="copilot-runtime-state"><Robot size={20}/><span><b>Agent 正在查询与核验</b><small>持久作业 {a.activeJob?.id||'准备中'} · 仅使用当前租户工具</small></span></div>}
        {error&&<div className="copilot-runtime-state error"><ShieldCheck size={20}/><span><b>本次运行未完成</b><small>{error}</small></span></div>}
        {answer&&<AssistantAnswer answer={answer}/>}
      </div>
      <footer>
        <div className="copilot-input"><MagnifyingGlass size={17}/><textarea rows="2" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();submit()}}} placeholder="询问数据，或下达一个任务目标…"/><button onClick={()=>submit()} disabled={!query.trim()} aria-label="发送"><PaperPlaneRight size={18} weight="fill"/></button></div>
        <small>{a.backendStatus==='connected'?'会话、工具轨迹与来源已写入服务端；高影响动作需结构化确认。':'当前为离线演示回答，不执行外部动作。'}</small>
      </footer>
    </aside>}
  </>;
}

export function AgentCommand({activity}){
  const a=useApp();
  const [value,setValue]=useState('');
  const [proposal,setProposal]=useState(null);
  const [running,setRunning]=useState(false);
  const suggestions=useMemo(()=>['解释当前等待条件','重新评估下一步','生成本活动经营摘要'],[]);
  const submit=async text=>{
    const command=(text??value).trim();
    if(!command)return;
    setValue('');setRunning(true);
    try{
      if(a.backendStatus==='connected'){
        const result=await a.askAgent(command,{scopeType:'activity',scopeId:activity.id,title:`${activity.name} Agent`});
        setProposal({command,highImpact:Boolean(result.proposal),id:result.proposal?.id,actionType:result.proposal?.action_type,body:result.answer?.body,runId:result.run_id,runtime:result.runtime});
      }else{
        const highImpact=/暂停|恢复|调整|联系|外呼|修改|提高/.test(command);
        setProposal({command,highImpact,body:highImpact?'已校验当前范围；确认后仍由策略引擎决定是否可执行。':'当前步骤正在等待经核验的外部事件，Agent 不会把口头承诺当作到账。'});
      }
    }catch(error){a.notify(`Agent 指令失败：${error.message||'服务不可用'}`)}
    finally{setRunning(false)}
  };
  const confirmProposal=async()=>{
    const command=proposal.command;
    if(proposal.id&&a.backendStatus==='connected'){
      try{await a.confirmAgentProposal(proposal.id)}catch(error){a.notify(`提案确认失败：${error.message||'服务不可用'}`);return}
    }
    if(/暂停/.test(command)){
      a.setActivityStatus([activity.id],'paused');
      a.addEvent(activity.caseIds[0],'对话指令已确认','活动已暂停；排队动作取消，在途动作等待外部确认','decision',{runId:`RUN-${activity.id}-${activity.caseIds[0]}`,stepId:'STEP-CMD',tool:'campaign.pause',status:'已完成'});
      a.notify('行动草案已确认：活动已暂停');
    }else if(/恢复/.test(command)){
      a.setActivityStatus([activity.id],'running');
      a.addEvent(activity.caseIds[0],'对话指令已确认','恢复前已重新校验授权、保护、付款、预算与频次','decision',{runId:`RUN-${activity.id}-${activity.caseIds[0]}`,stepId:'STEP-CMD',tool:'campaign.resume',status:'已完成'});
      a.notify('行动草案已确认：通过门禁后恢复运行');
    }else{
      a.addEvent(activity.caseIds[0],'对话行动草案已确认',command,'decision',{runId:`RUN-${activity.id}-${activity.caseIds[0]}`,stepId:'STEP-CMD',tool:'agent.command.confirm',status:'等待策略执行'});
      a.notify('行动草案已确认，等待策略引擎与工具门禁执行');
    }
    setProposal(null);
  };
  return <section className="agent-command">
    <div className="agent-command-head"><span><Lightning size={18}/><b>对话驱动 Agent</b></span><small>{a.agentGateway.provider} · {a.backendStatus==='connected'?'服务端持久会话':'离线演示'} · 受策略与工具权限约束</small></div>
    <p>用自然语言补充目标或要求解释。涉及触达、预算、策略和状态变更时，先生成行动草案。</p>
    <div className="agent-command-suggestions">{suggestions.map(item=><button key={item} onClick={()=>submit(item)}>{item}</button>)}</div>
    {running&&<div className="command-proposal running"><div><span>持久作业运行中</span><b>{a.activeJob?.id||'正在创建作业'}</b><small>查询、工具轨迹和证据将在完成后写入会话。</small></div><Robot size={21}/></div>}
    {proposal&&!running&&<div className="command-proposal"><div><span>{proposal.highImpact?'行动草案 · 待确认':'查询结果'}{proposal.runId?` · ${proposal.runId}`:''}</span><b>{proposal.command}</b><small>{proposal.body}</small>{proposal.runtime&&<small className="runtime-resume-state">{proposal.runtime.adapter} · {proposal.runtime.resume_mode==='checkpoint-replay'?'检查点重放':proposal.runtime.resume_mode==='in-process'?'进程内续接':proposal.runtime.resumed?'检查点已恢复':'新建检查点'} · Turn {proposal.runtime.turn_count} · Cursor {proposal.runtime.event_cursor}</small>}</div>{proposal.highImpact?<Button onClick={confirmProposal}>确认草案</Button>:<CheckCircle size={21}/>}</div>}
    <div className="agent-command-input"><input value={value} disabled={running} onChange={event=>setValue(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')submit()}} placeholder={`向 ${activity.name} Agent 提问或下达目标…`}/><button onClick={()=>submit()} disabled={running||!value.trim()} aria-label="发送 Agent 指令"><PaperPlaneRight size={17}/></button></div>
  </section>;
}

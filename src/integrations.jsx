import React,{useMemo,useState} from 'react';
import {
 ArrowRight,Check,CheckCircle,Cpu,Clock,CloudCheck,Flask,Info,
 LockKey,PhoneCall,Play,Robot,ShieldCheck,SpinnerGap,WarningCircle,Waveform
} from '@phosphor-icons/react';
import {useApp} from './context';
import {Button,Field,Modal,PageHead} from './ui';
import {serviceVersionSnapshot} from './integration-state';

const serviceMeta={
 agent:{
  title:'Agent Runtime',icon:Robot,action:'配置 Agent Runtime',
  description:'接入 Hermes Agent 等自主运行时，定义工具权限、审批边界与状态回调。',
  summary:c=>`${c.provider} · ${c.profile}`,
 },
 model:{
  title:'模型服务',icon:Cpu,action:'配置模型服务',
  description:'填写模型配置，测试连接，通过后启用案件说明生成。',
  summary:c=>`${c.provider} · ${c.model}`,
 },
 voice:{
  title:'语音服务',icon:Waveform,action:'配置语音服务',
  description:'配置语音识别与回复播报，测试通过后接入案件对话。',
  summary:c=>`${c.asr} · ${c.tts}`,
 },
 phone:{
  title:'电话接入',icon:PhoneCall,action:'准备电话接入',
  description:'选择 SIP 服务商，保存线路资料，核对开通前的准备事项。',
  summary:c=>`${c.provider} · ${c.callerId}`,
 }
};

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function connectionLabel(config){
 if(config.tested)return {label:'连接通过',tone:'ready'};
 if(config.saved)return {label:'等待测试',tone:'waiting'};
 return {label:'未配置',tone:'idle'};
}

function ServiceCard({type,config,onOpen}){
 const meta=serviceMeta[type];
 const Icon=meta.icon;
 const status=connectionLabel(config);
 return <article className={`integration-service-card ${status.tone}`}>
  <div className="integration-card-title"><Icon size={28}/><h2>{meta.title}</h2><span className={`connection-pill ${status.tone}`}>{config.tested?<CheckCircle size={13} weight="fill"/>:<Clock size={13}/>} {status.label}</span></div>
  <p>{meta.description}</p>
  <div className="integration-card-meta"><span>{config.saved?meta.summary(config):'尚未保存服务配置'}</span>{config.saved&&<small>配置 v{config.version||1} · {config.tested?`延迟 ${config.latency}`:`更新 ${config.updatedAt||'—'}`}</small>}</div>
  <button className="integration-card-action" onClick={onOpen}>{config.saved?'查看并配置':meta.action}<ArrowRight size={23}/></button>
 </article>;
}

function ServiceForm({type,initial,onClose}){
 const a=useApp();
 const [form,setForm]=useState(initial);
 const [testState,setTestState]=useState('idle');
 const [error,setError]=useState('');
 const meta=serviceMeta[type];
 const set=(key,value)=>{setForm(old=>({...old,[key]:value}));setTestState('idle');setError('')};
 const setAgentProvider=value=>{
  const preset=value==='DeepSeek Harness'
   ? {provider:value,endpoint:'stdio://deepseek-harness-sdk',profile:'fulfillops-safe',transport:'sandbox-contract',safetyPreset:'fulfillops-safe',sessionPersistence:'database-checkpoint'}
   : {provider:value,endpoint:form.endpoint||'https://agent.example.test/v1',profile:form.profile||'fulfill-agent',transport:'sandbox-contract',safetyPreset:'fulfillops-safe',sessionPersistence:'database-checkpoint'};
  setForm(old=>({...old,...preset}));setTestState('idle');setError('');
 };
 const required=type==='agent'?['provider','endpoint','profile','authToken']:type==='model'?['provider','endpoint','model','apiKey']:type==='voice'?['provider','region','asr','tts','accessKey']:['provider','sipHost','trunk','callerId','callback','secret'];
 const saveDraft=async()=>{try{await a.saveServiceConfig(type,form);a.notify(`${meta.title}配置已保存，需重新测试连接`);onClose()}catch(reason){setError(reason.message||'配置保存失败')}};
 const test=async()=>{
  const missing=required.filter(key=>!String(form[key]||'').trim());
  if(missing.length){setError('请补全必填配置后再测试连接。');return}
  setTestState('running');setError('');
  await wait(720);
  const latency=type==='agent'?'126 ms':type==='model'?'816 ms':type==='voice'?'236 ms':'41 ms';
  try{
   await a.testServiceConnection(type,form);
   setTestState('passed');
   a.notify(`${meta.title}连接测试通过`);
  }catch(reason){setTestState('idle');setError(reason.message||'连接测试失败')}
 };
 return <Modal wide title={`配置${meta.title}`} subtitle="保存配置、持久作业测试与生产启用分别确认。当前适配器使用沙箱契约，不调用外部服务。" onClose={onClose} footer={<><span className="muted small">带 * 为连接测试必填项</span><div className="footer-actions"><Button onClick={saveDraft}>保存草稿</Button><Button variant="primary" disabled={testState==='running'} onClick={test}>{testState==='running'?<><SpinnerGap className="spin" size={16}/>测试中…</>:testState==='passed'?<><CheckCircle size={16}/>重新测试</>:'测试连接'}</Button></div></>}>
  {type==='agent'&&<div className="form-grid integration-form">
   <Field label="Agent Runtime *"><select value={form.provider} onChange={e=>setAgentProvider(e.target.value)}><option>Hermes Agent</option><option>DeepSeek Harness</option><option>LangGraph Runtime</option><option>自研 Agent Gateway</option></select></Field>
   <Field label="Agent Profile *"><input value={form.profile} onChange={e=>set('profile',e.target.value)}/></Field>
   <Field label="Runtime Endpoint *"><input value={form.endpoint} onChange={e=>set('endpoint',e.target.value)}/></Field>
   <Field label="Auth Token *" hint="仅显示脱敏演示值"><input type="password" value={form.authToken} onChange={e=>set('authToken',e.target.value)}/></Field>
   <Field label="行动审批"><select value={form.approval} onChange={e=>set('approval',e.target.value)}><option>高影响动作需确认</option><option>所有写操作需确认</option><option>仅授权外自动阻断</option></select></Field>
   <Field label="运行传输"><select value={form.transport||'sandbox-contract'} onChange={e=>set('transport',e.target.value)}><option value="sandbox-contract">安全适配契约（沙箱）</option><option value="python-sdk">真实 SDK / JSON-RPC（受控 MCP）</option></select></Field>
   <Field label="安全策略"><input value={form.safetyPreset||'fulfillops-safe'} readOnly/></Field>
   <Field label="会话持久化"><select value={form.sessionPersistence||'database-checkpoint'} onChange={e=>set('sessionPersistence',e.target.value)}><option value="database-checkpoint">数据库检查点</option><option value="runtime-jsonl">Runtime JSONL + 数据库游标</option></select></Field>
   <Field label="工具权限"><input value="8 个受控业务工具 · 禁止 shell / 直接改账" readOnly/></Field>
  </div>}
  {type==='agent'&&form.provider==='DeepSeek Harness'&&<div className="harness-preview-note"><WarningCircle size={19}/><span><b>DeepSeek Harness · Developer Preview</b><small>{form.transport==='python-sdk'?'真实模式会启动官方 SDK 进程，应用 fulfillops-safe Patch 禁用默认 Shell，并仅注册 8 个租户级 MCP 工具；需由部署环境显式启用并从 KMS 注入模型凭证。':'当前为零外部调用的契约沙箱；会验证工具边界和数据库检查点，但不会启动官方 SDK。'}</small></span></div>}
  {type==='model'&&<div className="form-grid integration-form">
   <Field label="服务商 *"><select value={form.provider} onChange={e=>set('provider',e.target.value)}><option>DeepSeek</option><option>OpenAI Compatible</option><option>Azure OpenAI</option><option>私有化模型网关</option></select></Field>
   <Field label="模型 / 部署名称 *"><input value={form.model} onChange={e=>set('model',e.target.value)}/></Field>
   <Field label="API Endpoint *"><input value={form.endpoint} onChange={e=>set('endpoint',e.target.value)}/></Field>
   <Field label="API Key *" hint="仅显示脱敏演示值"><input type="password" value={form.apiKey} onChange={e=>set('apiKey',e.target.value)}/></Field>
   <Field label="请求超时"><select value={form.timeout} onChange={e=>set('timeout',e.target.value)}><option value="15">15 秒</option><option value="30">30 秒</option><option value="60">60 秒</option></select></Field>
   <Field label="输出约束"><input value="结构化 JSON · 规则引擎复核" readOnly/></Field>
  </div>}
  {type==='voice'&&<div className="form-grid integration-form">
   <Field label="语音服务商 *"><select value={form.provider} onChange={e=>set('provider',e.target.value)}><option>阿里云智能语音</option><option>火山引擎语音</option><option>腾讯云语音</option><option>私有化语音网关</option></select></Field>
   <Field label="服务区域 *"><input value={form.region} onChange={e=>set('region',e.target.value)}/></Field>
   <Field label="ASR 实时识别 *"><input value={form.asr} onChange={e=>set('asr',e.target.value)}/></Field>
   <Field label="TTS 播报音色 *"><input value={form.tts} onChange={e=>set('tts',e.target.value)}/></Field>
   <Field label="采样率"><select value={form.sampleRate} onChange={e=>set('sampleRate',e.target.value)}><option>8 kHz</option><option>16 kHz</option><option>24 kHz</option></select></Field>
   <Field label="Access Key *" hint="仅显示脱敏演示值"><input type="password" value={form.accessKey} onChange={e=>set('accessKey',e.target.value)}/></Field>
  </div>}
  {type==='phone'&&<div className="form-grid integration-form">
   <Field label="电话 / SIP 服务商 *"><select value={form.provider} onChange={e=>set('provider',e.target.value)}><option>LiveKit SIP</option><option>标准 SIP Trunk</option><option>现有呼叫平台</option></select></Field>
   <Field label="SIP 地址 *"><input value={form.sipHost} onChange={e=>set('sipHost',e.target.value)}/></Field>
   <Field label="Trunk 用户名 *"><input value={form.trunk} onChange={e=>set('trunk',e.target.value)}/></Field>
   <Field label="外显号码 *"><input value={form.callerId} onChange={e=>set('callerId',e.target.value)}/></Field>
   <Field label="事件回调地址 *"><input value={form.callback} onChange={e=>set('callback',e.target.value)}/></Field>
   <Field label="SIP 密钥 *" hint="仅显示脱敏演示值"><input type="password" value={form.secret} onChange={e=>set('secret',e.target.value)}/></Field>
  </div>}
  <div className="config-safety-note"><LockKey size={18}/><span><b>凭证安全边界</b><small>生产环境应写入密钥管理服务，前端仅展示末四位并记录配置版本；本原型不上传任何凭证。</small></span></div>
  {testState==='running'&&<div className="connection-feedback running"><SpinnerGap className="spin" size={18}/><span><b>正在验证配置</b><small>执行鉴权、响应格式与基础延迟检查…</small></span></div>}
  {testState==='passed'&&<div className="connection-feedback passed"><CheckCircle size={18} weight="fill"/><span><b>连接测试通过</b><small>配置已保存。仍需完成全链路沙箱自测和管理员启用确认。</small></span></div>}
  {error&&<div className="form-error"><WarningCircle size={18}/>{error}</div>}
 </Modal>;
}

function TestStep({item,result,index,active}){
 const status=result?.status||(active?'running':'idle');
 const Icon=status==='passed'?CheckCircle:status==='blocked'?WarningCircle:status==='running'?SpinnerGap:Clock;
 return <div className={`self-test-step ${status}`}>
  <span className="test-step-index">{status==='idle'?index+1:<Icon size={18} weight={status==='passed'?'fill':'regular'} className={status==='running'?'spin':''}/>}</span>
  <span><b>{item.title}</b><small>{result?.detail||item.description}</small></span>
  <span className={`test-result ${status}`}>{status==='passed'?'通过':status==='blocked'?'未通过':status==='running'?'检测中':'待检测'}</span>
 </div>;
}

export function Integrations(){
 const a=useApp();
 const [editing,setEditing]=useState(null);
 const [running,setRunning]=useState(false);
 const [activeStep,setActiveStep]=useState(-1);
 const [results,setResults]=useState([]);
 const [confirmEnable,setConfirmEnable]=useState(false);
 const services=a.serviceConfigs;
 const readyCount=Object.values(services).filter(service=>service.tested).length;
 const allReady=a.integrationReadiness.allConnected;
 const checks=useMemo(()=>[
  {id:'agent',title:'Agent 工具契约、会话恢复与策略护栏',description:`验证 ${services.agent.provider}、8 个受控工具和审批回调`,ready:services.agent.tested,blocked:'Agent Runtime 尚未通过连接测试'},
  {id:'model',title:'模型结构化输出',description:'验证鉴权、响应速度与 JSON 输出约束',ready:services.model.tested,blocked:'模型服务尚未通过连接测试'},
  {id:'asr',title:'ASR 与 TTS 回环',description:'播放测试音频并核对识别与播报结果',ready:services.voice.tested,blocked:'语音服务尚未通过连接测试'},
  {id:'sip',title:'SIP 注册与事件回调',description:'验证线路注册、外显号码与状态回调',ready:services.phone.tested,blocked:'电话接入尚未通过连接测试'},
  {id:'e2e',title:'端到端沙箱通话',description:'使用脱敏白名单号码回放完整测试脚本',ready:allReady,blocked:'需先通过全部四类服务连接测试'}
 ],[services,allReady]);
 const runSelfTest=async()=>{
  setRunning(true);setResults([]);setActiveStep(0);
  const next=[];
  for(let index=0;index<checks.length;index+=1){
   setActiveStep(index);
   await wait(520);
   const check=checks[index];
   const result={id:check.id,status:check.ready?'passed':'blocked',detail:check.ready?(check.id==='e2e'?'沙箱呼叫完成，未产生真实外呼':check.id==='agent'?'工具白名单 8/8 · 越权动作已阻断':check.id==='model'?'响应 816 ms · JSON Schema 校验通过':check.id==='asr'?'识别 1.3 s · 播报清晰度通过':'注册成功 · 回调签名通过'):check.blocked};
   next.push(result);setResults([...next]);
  }
  let passed=checks.every(check=>check.ready);
  try{
   if(a.backendStatus==='connected'){
    const remote=await a.runIntegrationSelfTest();
    if(remote){next.splice(0,next.length,...remote.items);setResults([...remote.items]);passed=remote.passed}
   }else{
    const now=new Date();
    const report={id:`SELF-${Date.now().toString().slice(-6)}`,time:now.toLocaleString('zh-CN',{hour12:false}),expiresAt:new Date(now.getTime()+24*60*60*1000).toISOString(),passed,items:next,serviceVersions:serviceVersionSnapshot(services)};
    a.updateIntegrationState({lastRun:report,enabled:false,enabledAt:null,enabledBy:null,enabledServiceVersions:null,invalidatedReason:passed?null:'存在未通过的沙箱自测项'});
   }
   a.notify(passed?'全链路沙箱自测通过，可进入启用确认':'自测完成：请先处理未通过项');
  }catch(reason){passed=false;a.notify(`沙箱自测失败：${reason.message||'服务不可用'}`)}
  setActiveStep(-1);setRunning(false);
 };
 const enable=async()=>{try{await a.enableIntegration();setConfirmEnable(false);a.notify('AI 与渠道已启用到后续新建活动')}catch(reason){a.notify(`启用失败：${reason.message||'门禁未通过'}`)}};
 const lastRun=a.integrationState.lastRun;
 return <div className="integrations-page">
  <PageHead title="AI 与渠道接入" description="配置、持久作业测试、自测与生产启用分别确认。"><span className={`environment-chip ${a.backendStatus==='connected'?'enabled':''}`}><CloudCheck size={14}/>{a.backendStatus==='checking'?'连接后端…':a.backendStatus==='connected'?'API v0.4.0 已连接':'本地演示降级'}</span>{a.activeJob&&<span className={`environment-chip ${a.activeJob.status==='succeeded'?'enabled':''}`}><Clock size={14}/>{a.activeJob.id} · {a.activeJob.status==='succeeded'?'已完成':a.activeJob.status==='running'?'运行中':'排队中'}</span>}<span className={`environment-chip ${a.integrationReadiness.ready?'enabled':''}`}><span className="live-dot"/>{a.integrationReadiness.ready?'新建活动已启用':'演示沙箱'}</span><Button icon={Flask} variant="primary" disabled={running} onClick={runSelfTest}>{running?'自测运行中':allReady?'运行全链路自测':'检查接入条件'}</Button></PageHead>

  <section className="gateway-runtime-strip" aria-label="Agent Gateway 状态">
   <span className="gateway-runtime-icon"><Robot size={21}/></span>
   <span><b>Agent Gateway</b><small>{a.agentGateway.provider} · {a.agentGateway.profile} · {a.agentGateway.tools?.length||0} 个受控工具</small></span>
   <span className={`connection-pill ${a.agentGateway.connected?'ready':'waiting'}`}>{a.agentGateway.connected?<CheckCircle size={13} weight="fill"/>:<Clock size={13}/>} {a.agentGateway.mode==='sandbox-contract'?'沙箱契约已就绪':a.agentGateway.connected?'SDK 连接已验证':'SDK 等待验证'}</span>
   <small>{a.agentGateway.transport} · {a.agentGateway.session_persistence}；对话负责查询、解释和提案，确定性业务服务最终裁决。</small>
  </section>

  <section className="integration-service-grid" aria-label="接入服务">
   {Object.entries(serviceMeta).map(([type])=><ServiceCard key={type} type={type} config={services[type]} onOpen={()=>setEditing(type)}/>) }
  </section>

  <section className="integration-progress" aria-label="接入进度">
   <div><span className="progress-label">接入进度</span><strong>{readyCount}/4 服务已通过连接测试</strong></div>
   <div className="integration-progress-track" aria-label={`${readyCount} / 4`}><span style={{width:`${readyCount/4*100}%`}}/></div>
   <div className="progress-legend"><span><Check size={14}/>配置保存</span><span><CloudCheck size={14}/>连接测试</span><span><Flask size={14}/>全链路自测</span><span><ShieldCheck size={14}/>启用确认</span></div>
  </section>

  <div className="integration-workspace">
   <section className="self-test-card">
    <div className="integration-section-head"><div><span className="eyebrow">SANDBOX SELF-TEST</span><h2>接入自测中心</h2><p>从模型响应到沙箱通话逐项检查，不触达真实号码。</p></div><Button icon={Play} disabled={running} onClick={runSelfTest}>{running?'检测中…':'开始自测'}</Button></div>
    <div className="self-test-list">{checks.map((item,index)=><TestStep key={item.id} item={item} index={index} active={activeStep===index} result={results.find(result=>result.id===item.id)}/>)}</div>
    <div className="sandbox-boundary"><Info size={18}/><span><b>自测安全边界</b><small>仅生成模拟回执并使用脱敏白名单号码；不读取真实案件、不产生计费、不发起真实外呼。</small></span></div>
   </section>

   <aside className="integration-gate-card">
    <div className="integration-section-head"><div><span className="eyebrow">ENABLEMENT GATE</span><h2>启用门禁</h2><p>满足全部条件后，才允许新建活动使用。</p></div><ShieldCheck size={26}/></div>
    <div className="gate-list">
     <div className={Object.values(services).every(x=>x.saved)?'complete':''}><span>{Object.values(services).every(x=>x.saved)?<CheckCircle size={19} weight="fill"/>:<Clock size={19}/>}</span><b>四类运行服务已保存</b></div>
     <div className={allReady?'complete':''}><span>{allReady?<CheckCircle size={19} weight="fill"/>:<Clock size={19}/>}</span><b>连接测试全部通过</b></div>
     <div className={a.integrationReadiness.reportFresh?'complete':''}><span>{a.integrationReadiness.reportFresh?<CheckCircle size={19} weight="fill"/>:<Clock size={19}/>}</span><b>沙箱全链路通过且报告有效</b></div>
     <div className={a.integrationReadiness.ready?'complete':''}><span>{a.integrationReadiness.ready?<CheckCircle size={19} weight="fill"/>:<Clock size={19}/>}</span><b>管理员启用确认</b></div>
    </div>
    <Button variant="primary" disabled={!a.integrationReadiness.reportFresh||a.integrationReadiness.ready} onClick={()=>setConfirmEnable(true)}>{a.integrationReadiness.ready?'已启用到新建活动':'确认启用'}</Button>
    {!a.integrationReadiness.reportFresh&&<button className="text-link gate-help" onClick={runSelfTest}>运行自测以解锁启用 <ArrowRight size={14}/></button>}
    {a.integrationState.invalidatedReason&&<div className="gate-invalid-reason"><WarningCircle size={16}/><span>{a.integrationState.invalidatedReason}</span></div>}
   </aside>
  </div>

  <section className="latest-test-report">
   <div className="integration-section-head"><div><span className="eyebrow">LATEST REPORT</span><h2>最近自测报告</h2></div>{lastRun&&<span className={`report-status ${a.integrationReadiness.reportFresh?'passed':'blocked'}`}>{a.integrationReadiness.reportFresh?<CheckCircle size={15}/>:<WarningCircle size={15}/>} {a.integrationReadiness.reportFresh?'有效且全部通过':lastRun.passed?'报告已失效':'存在未通过项'}</span>}</div>
   {lastRun?<div className="report-row"><span><b>{lastRun.id}</b><small>{lastRun.time} · {a.organization[a.tenant]}</small></span><span><b>{lastRun.items.filter(x=>x.status==='passed').length}/5</b><small>检测通过</small></span><span><b>服务版本 {Object.values(lastRun.serviceVersions||{}).join(' / ')||'旧报告'}</b><small>{a.integrationReadiness.reportFresh?'24 小时内有效 · 无真实外呼':'已失效 · 请重新运行'}</small></span><button className="text-link" onClick={runSelfTest}>重新运行 <ArrowRight size={14}/></button></div>:<div className="empty-report"><Flask size={24}/><span><b>尚无自测报告</b><small>运行接入自测后，这里会保留本工作空间的最近结果。</small></span></div>}
  </section>

  {editing&&<ServiceForm type={editing} initial={services[editing]} onClose={()=>setEditing(null)}/>}
  {confirmEnable&&<Modal title="确认启用 AI 与渠道" subtitle="连接通过不代表自动启用，需要管理员明确确认。" onClose={()=>setConfirmEnable(false)} footer={<><Button onClick={()=>setConfirmEnable(false)}>取消</Button><Button variant="primary" onClick={enable}>确认启用</Button></>}><div className="enable-summary"><ShieldCheck size={38}/><h3>仅应用于后续新建活动</h3><p>启用时冻结 Agent、模型、语音与电话的当前版本；配置变更会自动撤销启用，已运行活动仍保留原服务快照。</p></div><label className="enable-confirm"><input type="checkbox" defaultChecked readOnly/><span>我确认已查看自测报告，并理解当前页面仍为前端演示。</span></label></Modal>}
 </div>;
}

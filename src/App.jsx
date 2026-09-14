import React,{useEffect,useMemo,useRef,useState} from 'react';
import {House,ChartBar,FolderSimple,Files,Database,Robot,ClockCounterClockwise,BookOpen,Receipt,GearSix,Buildings,CaretDown,MagnifyingGlass,Question,Sun,Moon,List,CheckCircle,X,ArrowRight,Command,Bell,WarningCircle,CurrencyCircleDollar,ShieldCheck,PlugsConnected,Siren} from '@phosphor-icons/react';
import {AppContext} from './context';
import {titles,initialActivities,initialEvents,baseLedger,caseModels,packages} from './model';
import {Button,IconButton,Modal,Search,Empty} from './ui';
import {Activities,Overview,Assets,Cases,Payments,AgentScreen,Logs,Strategy,Integrations,Usage,Settings} from './screens';
import {CreateWizard,CaseDrawer,PolicyModal,ReceiptModal,ImportModal,PaymentDetailModal,ExceptionModal} from './dialogs';
import {Exceptions} from './exceptions';
import {AICopilot} from './ai';
import {INTEGRATION_STORAGE_KEY,integrationGate,maskedCredential,restoreIntegrationState,sanitizeServiceConfig,serializeIntegrationState,serviceVersionSnapshot} from './integration-state';
import {determineActivityMode} from './activity-preflight';
import {activityApi,agentApi,authApi,integrationApi,jobApi,normalizeIntegrationOverview,securityApi} from './api';
const nav=[['overview',House],['activities',ChartBar],['assets',FolderSimple],['cases',Files],['payments',Database],['agents',Robot],['exceptions',Siren],['logs',ClockCounterClockwise],['strategy',BookOpen],['integrations',PlugsConnected]];
const makeInitialServiceConfigs=()=>({
 TENANT_A:{
  agent:{provider:'Hermes Agent',endpoint:'http://agent-gateway.internal/v1',profile:'fulfill-agent-v3',authToken:maskedCredential,credentialConfigured:true,approval:'高影响动作需确认',transport:'sandbox-contract',safetyPreset:'fulfillops-safe',sessionPersistence:'database-checkpoint',saved:true,tested:true,latency:'126 ms',version:3,updatedAt:'2026.09.13 10:18'},
  model:{provider:'DeepSeek',endpoint:'https://api.deepseek.com/v1',model:'deepseek-chat',apiKey:maskedCredential,credentialConfigured:true,timeout:'30',saved:true,tested:true,latency:'842 ms',version:2,updatedAt:'2026.09.13 10:22'},
  voice:{provider:'阿里云智能语音',region:'华东 2（上海）',asr:'Paraformer 实时版',tts:'CosyVoice · 龙橙',sampleRate:'16 kHz',accessKey:maskedCredential,credentialConfigured:true,saved:true,tested:false,latency:'—',version:1,updatedAt:'2026.09.13 10:26'},
  phone:{provider:'LiveKit SIP',sipHost:'sip.example.test:5061',trunk:'amc-demo-trunk',callerId:'010****8800',callback:'https://example.test/telephony/events',secret:maskedCredential,credentialConfigured:true,saved:true,tested:false,latency:'—',version:1,updatedAt:'2026.09.13 10:30'}
 },
 TENANT_B:{
  agent:{provider:'LangGraph Runtime',endpoint:'https://agent.example.test/v1',profile:'fulfill-agent',authToken:'',credentialConfigured:false,approval:'高影响动作需确认',transport:'sandbox-contract',safetyPreset:'fulfillops-safe',sessionPersistence:'database-checkpoint',saved:false,tested:false,latency:'—',version:1,updatedAt:'—'},
  model:{provider:'OpenAI Compatible',endpoint:'https://api.example.test/v1',model:'fulfill-model',apiKey:'',credentialConfigured:false,timeout:'30',saved:false,tested:false,latency:'—',version:1,updatedAt:'—'},
  voice:{provider:'火山引擎语音',region:'华北 2（北京）',asr:'流式语音识别',tts:'中文女声',sampleRate:'16 kHz',accessKey:'',credentialConfigured:false,saved:false,tested:false,latency:'—',version:1,updatedAt:'—'},
  phone:{provider:'标准 SIP Trunk',sipHost:'sip.example.test:5061',trunk:'tenant-b-demo',callerId:'021****6600',callback:'https://example.test/telephony/events',secret:'',credentialConfigured:false,saved:false,tested:false,latency:'—',version:1,updatedAt:'—'}
 }
});
const makeInitialIntegrationStates=()=>({
 TENANT_A:{enabled:false,lastRun:null,enabledAt:null,enabledBy:null,enabledServiceVersions:null,invalidatedReason:'语音与电话服务尚未完成连接测试'},
 TENANT_B:{enabled:false,lastRun:null,enabledAt:null,enabledBy:null,enabledServiceVersions:null,invalidatedReason:'尚未完成服务配置'}
});
const loadIntegrationBundle=()=>{
 const configs=makeInitialServiceConfigs();
 const states=makeInitialIntegrationStates();
 try{
  const persisted=JSON.parse(localStorage.getItem(INTEGRATION_STORAGE_KEY)||'null');
  return restoreIntegrationState(configs,states,persisted);
 }catch{return {configs,states}}
};
const formatNow=()=>new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date()).replaceAll('/','.');
const defaultQueueHealth={mode:'inline',status:'inline',active_workers:0,queued_jobs:0,running_jobs:0,stale_jobs:0,latest_heartbeat_at:null,lease_seconds:60,broker_backend:'database',broker_status:'polling'};
const defaultSecretHealth={backend:'reference-only',status:'reference-only',persistent:false,resolvable:false,key_version:null,active_secrets:0,rotation_pending:0,detail:'凭证由部署环境注入'};
export function App(){
 const [route,setRoute]=useState(location.hash.slice(2)||'overview');
 const [tenant,setTenant]=useState('TENANT_A');
 const [theme,setTheme]=useState('light');
 const [activities,setActivities]=useState(initialActivities);
 const [events,setEvents]=useState(initialEvents);
 const [ledger,setLedger]=useState(baseLedger);
 const [paid,setPaid]=useState(false);
 const [dialog,setDialog]=useState(null);
 const [caseId,setCaseId]=useState(null);
 const [caseTab,setCaseTab]=useState('概览');
 const [searchOpen,setSearchOpen]=useState(false);
 const [search,setSearch]=useState('');
 const [workspaceOpen,setWorkspaceOpen]=useState(false);
 const [toast,setToast]=useState('');
 const [mobileOpen,setMobileOpen]=useState(false);
 const [notificationOpen,setNotificationOpen]=useState(false);
 const [notificationsRead,setNotificationsRead]=useState(false);
 const [scope,setScope]=useState('all');
 const [runSteps,setRunSteps]=useState({});
 const [policies,setPolicies]=useState({});
 const [organization,setOrganization]=useState({TENANT_A:'租户 A',TENANT_B:'租户 B'});
 const [notificationPreferences,setNotificationPreferences]=useState({exception:true,daily:false});
 const [integrationSeed]=useState(loadIntegrationBundle);
 const [serviceConfigsByTenant,setServiceConfigsByTenant]=useState(integrationSeed.configs);
 const [integrationStatesByTenant,setIntegrationStatesByTenant]=useState(integrationSeed.states);
 const [backendStatus,setBackendStatus]=useState('checking');
 const [identity,setIdentity]=useState({actor_id:'Terry',display_name:'Terry',role:'admin',auth_mode:'local-demo'});
 const [agentGateway,setAgentGateway]=useState({mode:'local-demo',provider:'Hermes Agent',profile:'fulfill-agent-v3',connected:false,transport:'sandbox-contract',session_persistence:'database-checkpoint',tools:[],forbidden_tools:[]});
 const [queueHealth,setQueueHealth]=useState(defaultQueueHealth);
 const [secretHealth,setSecretHealth]=useState(defaultSecretHealth);
 const [activeJob,setActiveJob]=useState(null);
 const agentSessions=useRef({});
 const [copilotOpen,setCopilotOpen]=useState(false);
 const [exceptionResolutions,setExceptionResolutions]=useState({});
 const cases=useMemo(()=>caseModels(ledger,paid),[ledger,paid]);
 const visibleCases=cases.filter(c=>c.tenant_id===tenant);
 const visiblePackages=packages.filter(p=>p.tenant_id===tenant);
 const visibleLedger=ledger.filter(t=>t.tenant_id===tenant);
 const visibleActivities=activities.filter(x=>x.tenant===tenant);
 const protectedCount=visibleCases.filter(c=>c.blocked).length;
 const page=route.split('/')[0];
 const navigate=s=>{location.hash='/'+s;setRoute(s);setMobileOpen(false);setCaseId(null);setNotificationOpen(false)};
 const notify=s=>setToast(s);
 useEffect(()=>{const fn=()=>{setRoute(location.hash.slice(2)||'overview');setCaseId(null)};window.addEventListener('hashchange',fn);return()=>window.removeEventListener('hashchange',fn)},[]);
 useEffect(()=>{document.documentElement.dataset.theme=theme},[theme]);
 useEffect(()=>{setTheme(page==='agents'&&route.includes('/')?'dark':'light')},[route,page]);
 useEffect(()=>{window.scrollTo({top:0,behavior:'instant'})},[route]);
 useEffect(()=>{if(toast){const t=setTimeout(()=>setToast(''),4000);return()=>clearTimeout(t)}},[toast]);
 useEffect(()=>{try{localStorage.setItem(INTEGRATION_STORAGE_KEY,serializeIntegrationState(serviceConfigsByTenant,integrationStatesByTenant))}catch{}},[serviceConfigsByTenant,integrationStatesByTenant]);
 useEffect(()=>{let active=true;setBackendStatus('checking');setQueueHealth(defaultQueueHealth);setSecretHealth(defaultSecretHealth);Promise.all([integrationApi.get(tenant),authApi.session(tenant),agentApi.gateway(tenant),jobApi.queueHealth(tenant).catch(()=>defaultQueueHealth),securityApi.secretHealth(tenant).catch(()=>defaultSecretHealth)]).then(([payload,session,gateway,queue,secrets])=>{if(!active)return;const normalized=normalizeIntegrationOverview(payload);setServiceConfigsByTenant(all=>({...all,[tenant]:{...all[tenant],...normalized.services}}));setIntegrationStatesByTenant(all=>({...all,[tenant]:normalized.state}));setIdentity(session);setAgentGateway(gateway);setQueueHealth(queue);setSecretHealth(secrets);setBackendStatus('connected')}).catch(()=>{if(active){setBackendStatus('offline');setQueueHealth(defaultQueueHealth);setSecretHealth(defaultSecretHealth);setAgentGateway(current=>({...current,connected:false,mode:'local-demo'}))}});return()=>{active=false}},[tenant]);
 useEffect(()=>{if(backendStatus!=='connected')return;let active=true;const refresh=()=>jobApi.queueHealth(tenant).then(queue=>{if(active)setQueueHealth(queue)}).catch(()=>{});const timer=setInterval(refresh,5000);return()=>{active=false;clearInterval(timer)}},[backendStatus,tenant]);
 useEffect(()=>{const fn=e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setSearchOpen(s=>!s)}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='j'){e.preventDefault();setCopilotOpen(s=>!s)}if(e.key==='Escape'){setWorkspaceOpen(false);setMobileOpen(false);setNotificationOpen(false)}};document.addEventListener('keydown',fn);return()=>document.removeEventListener('keydown',fn)},[]);
 const addEvent=(caseId,title,detail,type='decision',meta={})=>setEvents(old=>[{id:'EV-'+Date.now(),tenant,time:'10:48:32',caseId,title,detail,type,runId:meta.runId||`RUN-${tenant.slice(-1)}-${caseId}`,stepId:meta.stepId||'STEP-04',actionId:meta.actionId||`ACTN-${String(Date.now()).slice(-4)}`,actor:meta.actor||'Hermes Agent',tool:meta.tool||'workflow.transition',status:meta.status||'已完成',version:meta.version||'FLOW v3.2',latency:meta.latency||'84 ms',cost:meta.cost||'¥0.02'},...old]);
 const openCase=(id,tab='概览')=>{if(visibleCases.some(c=>c.case_id===id)){setCaseId(id);setCaseTab(tab)}};
 const setActivityStatus=(ids,status)=>{const allowed=visibleActivities.filter(a=>ids.includes(a.id)&&!['blocked','completed'].includes(a.status));setActivities(old=>old.map(a=>allowed.some(x=>x.id===a.id)?{...a,status}:a));notify(allowed.length?`${allowed.length} 个活动已${status==='paused'?'暂停':'恢复模拟运行'}`:'当前选择包含保护暂停或已完成活动，无可操作项目')};
 const getPolicy=id=>policies[id]||{version:1,status:'published',budget:30,start:'09:00',end:'18:00',daily:1,weekly:3,retry:48,tone:'专业、温和、简洁',goal:'优先跟进已签协议的本期差额，到账后自动更新履约状态。',minSettlement:70,maxInstallments:6,minDownPayment:20,offerValidity:7,approvalThreshold:75,evaluated:true};
 const savePolicy=(pkg,config)=>{setPolicies(s=>({...s,[pkg]:{...config,version:getPolicy(pkg).version+1,status:config.status||'draft'}}));notify(`策略 v${getPolicy(pkg).version+1}.0 已保存为${config.status==='published'?'已发布版本':'草稿版本'}`);setDialog(null)};
 const advance=activity=>{const current=runSteps[activity.id]??2;setRunSteps(s=>({...s,[activity.id]:Math.min(3,current+1)}));addEvent(activity.caseIds[0],'补款跟进模拟完成','已进入等待回款阶段；尚未确认到账');notify('模拟跟进完成，进入等待支付状态')};
 const receive=()=>{if(paid)return;setLedger(old=>[...old,{tenant_id:'TENANT_A',transaction_id:'DEMO-TX-001',case_id:'C002',package_id:'PKG_A',booked_date:'2026-09-12',event_type:'PAYMENT',cash_yuan:1016,eligible:true,eligible_cash_yuan:1016,rate:.15,commission_yuan:152.4,reason:'IN_MANDATE',commission_rule_id:'COM_A_V1',source:'沙箱支付回调',signature:'验签通过',match_status:'已匹配',allocation:'PLAN002 · 第2期',idempotency:'首次接收'}]);setPaid(true);setActivities(old=>old.map(a=>a.tenant==='TENANT_A'&&a.caseIds.length===1&&a.caseIds[0]==='C002'?{...a,status:'completed',next:'本期已足额 · 下期 10.10',updated:'10:48'}:a));addEvent('C002','本期补款已入账','验签、去重、匹配与分期分配完成；确认回款 +¥1,016','payment',{actor:'账务接口',tool:'payment.reconcile',version:'COM_A v1.0',latency:'326 ms',cost:'¥0.00'});setDialog(null);notify('模拟到账已完成验签、匹配、分期分配与佣金更新')};
 const switchTenant=id=>{setTenant(id);setScope('all');setCaseId(null);setDialog(null);setWorkspaceOpen(false);navigate('activities');notify(`已切换至${organization[id]}`)};
 const serviceConfigs=serviceConfigsByTenant[tenant];
 const integrationState=integrationStatesByTenant[tenant];
 const integrationReadiness=integrationGate(serviceConfigs,integrationState);
 const reset=()=>{setActivities(initialActivities);setEvents(initialEvents);setLedger(baseLedger);setPaid(false);setRunSteps({});setPolicies({});setServiceConfigsByTenant(makeInitialServiceConfigs());setIntegrationStatesByTenant(makeInitialIntegrationStates());setExceptionResolutions({});setCopilotOpen(false);setDialog(null);setScope('all');navigate('activities');notify('演示数据已还原')};
 const createActivity=async form=>{const p=getPolicy(form.package);let remote=null;if(backendStatus==='connected'){try{remote=await activityApi.create(tenant,form)}catch(error){notify(`活动创建失败：${error.message}`);return}}const id=remote?.activity_id||'ACT-'+String(activities.length+1).padStart(3,'0');const {productionReady,mode:localMode}=determineActivityMode({integrationReady:integrationReadiness.ready,policyStatus:p.status,caseCount:form.caseIds.length});const mode=remote?.mode||localMode;const versions=serviceVersionSnapshot(serviceConfigs);const rawSnapshot=remote?.service_snapshot;const snapshot=rawSnapshot?{agent:`${rawSnapshot.agent?.provider||'—'} · ${rawSnapshot.agent?.settings?.profile||'—'} · v${rawSnapshot.agent?.version||'—'}`,model:`${rawSnapshot.model?.provider||'—'} · ${rawSnapshot.model?.settings?.model||'—'} · v${rawSnapshot.model?.version||'—'}`,voice:`${rawSnapshot.voice?.provider||'—'} · ${rawSnapshot.voice?.settings?.tts||'—'} · v${rawSnapshot.voice?.version||'—'}`,phone:`${rawSnapshot.phone?.provider||'—'} · ${rawSnapshot.phone?.settings?.callerId||'—'} · v${rawSnapshot.phone?.version||'—'}`,selfTest:rawSnapshot.self_test_id||'未使用',enabledAt:integrationState.enabledAt||'未启用'}:{agent:`${serviceConfigs.agent.provider} · ${serviceConfigs.agent.profile} · v${versions.agent}`,model:`${serviceConfigs.model.provider} · ${serviceConfigs.model.model} · v${versions.model}`,voice:`${serviceConfigs.voice.provider} · ${serviceConfigs.voice.tts} · v${versions.voice}`,phone:`${serviceConfigs.phone.provider} · ${serviceConfigs.phone.callerId} · v${versions.phone}`,selfTest:productionReady?integrationState.lastRun?.id:'未使用',enabledAt:productionReady?integrationState.enabledAt:'未启用'};const caseIds=remote?.case_ids||form.caseIds;const entry={id,tenant,name:remote?.name||form.name,package:remote?.package_id||form.package,caseIds,status:remote?.status||'running',goal:remote?.goal||form.goal,next:mode==='sandbox'?'沙箱计划已生成 · 不触达真实号码':'预检通过 · 等待执行窗口',updated:'刚刚',version:remote?.policy_version||p.version,policy:{...p},budget:Number(remote?.budget_yuan||form.budget),mode,serviceSnapshot:snapshot,preflight:remote?.preflight||{mandate:true,policy:p.status==='published',caseScope:caseIds.length>0,channel:productionReady,channelChecks:{...integrationReadiness}}};setActivities(old=>[entry,...old.filter(item=>item.id!==id)]);setRunSteps(s=>({...s,[id]:1}));addEvent(caseIds[0],'活动已创建',`${entry.name} · ${caseIds.length} 个案件 · ${mode==='sandbox'?'纯模拟':'已启用渠道'} · ${backendStatus==='connected'?'后端预检已确认':'本地演示预检'}`,'decision',{runId:`RUN-${id}-${caseIds[0]}`,stepId:'STEP-01',tool:'campaign.preflight',version:`POL v${entry.version}.0`});setDialog(null);navigate('agents/'+id);notify(`活动已创建，${serviceConfigs.agent.provider} 进入${mode==='sandbox'?'纯模拟':'授权'}运行`)};
 const updateServiceConfig=(service,config)=>{
  const safeConfig=sanitizeServiceConfig(service,config);
  setServiceConfigsByTenant(all=>{const current=all[tenant][service];return {...all,[tenant]:{...all[tenant],[service]:{...current,...safeConfig,version:Number(current.version||0)+1,updatedAt:formatNow()}}}});
  setIntegrationStatesByTenant(all=>({...all,[tenant]:{...all[tenant],enabled:false,lastRun:null,enabledAt:null,enabledBy:null,enabledServiceVersions:null,invalidatedReason:`${service} 配置已变更，需重新完成连接测试与沙箱自测`}}));
 };
 const updateIntegrationState=patch=>setIntegrationStatesByTenant(all=>({...all,[tenant]:{...all[tenant],...patch}}));
 const applyRemoteOverview=payload=>{const normalized=normalizeIntegrationOverview(payload);setServiceConfigsByTenant(all=>({...all,[tenant]:{...all[tenant],...normalized.services}}));setIntegrationStatesByTenant(all=>({...all,[tenant]:normalized.state}));return normalized};
 const refreshAgentGateway=async()=>{const gateway=await agentApi.gateway(tenant);setAgentGateway(gateway);agentSessions.current=Object.fromEntries(Object.entries(agentSessions.current).filter(([key])=>!key.startsWith(`${tenant}:`)));return gateway};
 const saveServiceConfig=async(service,config)=>{if(backendStatus!=='connected'){updateServiceConfig(service,{...config,saved:true,tested:false,latency:'—'});if(service==='agent'){setAgentGateway(current=>({...current,provider:config.provider,profile:config.profile,transport:config.transport||'sandbox-contract',session_persistence:config.sessionPersistence||'database-checkpoint',connected:false}));agentSessions.current=Object.fromEntries(Object.entries(agentSessions.current).filter(([key])=>!key.startsWith(`${tenant}:`)))}return null}const payload=await integrationApi.save(tenant,service,config);const normalized=applyRemoteOverview(payload);if(service==='agent')await refreshAgentGateway();return normalized};
 const testServiceConnection=async(service,config)=>{if(backendStatus!=='connected'){const latency=service==='agent'?'126 ms':service==='model'?'816 ms':service==='voice'?'236 ms':'41 ms';updateServiceConfig(service,{...config,saved:true,tested:true,latency});if(service==='agent'){setAgentGateway(current=>({...current,provider:config.provider,profile:config.profile,transport:config.transport||'sandbox-contract',session_persistence:config.sessionPersistence||'database-checkpoint',connected:true}));agentSessions.current=Object.fromEntries(Object.entries(agentSessions.current).filter(([key])=>!key.startsWith(`${tenant}:`)))}return null}await integrationApi.save(tenant,service,config);await integrationApi.test(tenant,service,setActiveJob);const normalized=applyRemoteOverview(await integrationApi.get(tenant));if(service==='agent')await refreshAgentGateway();return normalized};
 const runIntegrationSelfTest=async()=>{if(backendStatus!=='connected')return null;await integrationApi.selfTest(tenant,setActiveJob);return applyRemoteOverview(await integrationApi.get(tenant)).state.lastRun};
 const enableIntegration=async()=>{if(backendStatus!=='connected'){updateIntegrationState({enabled:true,enabledAt:formatNow(),enabledBy:'Terry',enabledServiceVersions:serviceVersionSnapshot(serviceConfigs),invalidatedReason:null});return null}return applyRemoteOverview(await integrationApi.enable(tenant))};
 const preflightActivity=async form=>backendStatus==='connected'?activityApi.preflight(tenant,form):null;
 const askAgent=async(content,scope={})=>{if(backendStatus!=='connected')return null;const scopeType=scope.scopeType||'global';const scopeId=scope.scopeId||null;const key=`${tenant}:${scopeType}:${scopeId||'root'}`;let sessionId=agentSessions.current[key];if(!sessionId){const session=await agentApi.createSession(tenant,{scopeType,scopeId,title:scope.title||'履衡 AI 会话'});sessionId=session.id;agentSessions.current[key]=sessionId}const job=await agentApi.message(tenant,sessionId,content,setActiveJob);return {...job.result,session_id:sessionId}};
 const confirmAgentProposal=async proposalId=>{if(backendStatus!=='connected')return null;const result=await agentApi.confirmProposal(tenant,proposalId);return result};
 const queueLabel=backendStatus!=='connected'?'离线演示 · 本地运行':queueHealth.mode==='external'?(queueHealth.status==='healthy'?'独立 Worker · 持久运行':'Worker 降级 · 作业保留'):'API 内联 · 持久作业';
 const a={tenant,theme,setTheme,route,page,navigate,activities,visibleActivities,cases,visibleCases,packages,visiblePackages,ledger,visibleLedger,events,paid,scope,setScope,dialog,setDialog,openCase,notify,setActivityStatus,getPolicy,savePolicy,runSteps,advance,receive,createActivity,preflightActivity,organization,setOrganization,notificationPreferences,setNotificationPreferences,serviceConfigs,integrationState,integrationReadiness,backendStatus,identity,agentGateway,queueHealth,secretHealth,activeJob,askAgent,confirmAgentProposal,updateServiceConfig,updateIntegrationState,saveServiceConfig,testServiceConnection,runIntegrationSelfTest,enableIntegration,reset,copilotOpen,setCopilotOpen,exceptionResolutions,setExceptionResolutions,addEvent};
 const activeCase=visibleCases.find(c=>c.case_id===caseId);
 const results=search.trim()?visibleCases.filter(c=>(c.case_id+' '+c.package_id+' '+c.status).toLowerCase().includes(search.toLowerCase())).slice(0,5):[];
 const acts=search.trim()?visibleActivities.filter(x=>(x.name+x.id).toLowerCase().includes(search.toLowerCase())).slice(0,3):[];
 return <AppContext.Provider value={a}><div className={`app-shell ${mobileOpen?'mobile-open':''}`}>
 <aside className="sidebar"><button className="brand" onClick={()=>navigate('overview')}><img src="/assets/brand.png?v=2" alt="履衡 AI"/><span><strong>履衡 AI</strong><small>FulfillOps Cloud</small></span></button>
 <div className="workspace-area"><button className="workspace-switch" aria-expanded={workspaceOpen} onClick={()=>setWorkspaceOpen(!workspaceOpen)}><Buildings size={21}/><span><b>{organization[tenant]}</b><small>资产履约工作空间</small></span><CaretDown size={14}/></button>{workspaceOpen&&<div className="workspace-pop"><span className="overline">切换工作空间</span>{['TENANT_A','TENANT_B'].map(id=><button key={id} onClick={()=>switchTenant(id)}><Buildings size={17}/>{organization[id]}{tenant===id&&<CheckCircle size={17}/>}</button>)}</div>}</div>
 <button className="sidebar-search" onClick={()=>{setSearch('');setNotificationOpen(false);setSearchOpen(true)}}><MagnifyingGlass size={18}/><span>快速搜索</span><kbd>⌘ K</kbd></button>
 <nav>{nav.map(([id,Icon],i)=><React.Fragment key={id}>{i===5&&<div className="nav-label">AI 运营</div>}<button className={page===id?'nav-item active':'nav-item'} aria-current={page===id?'page':undefined} onClick={()=>navigate(id)}><Icon size={20}/><span>{titles[id]}</span>{id==='activities'&&<small>{visibleActivities.filter(x=>x.status==='running').length}</small>}{id==='exceptions'&&protectedCount>0&&<small>{protectedCount}</small>}</button></React.Fragment>)}</nav>
 <div className="sidebar-bottom"><div className="simulation-status"><span className={backendStatus==='connected'&&queueHealth.status==='degraded'?'warning-dot':'live-dot'}/>{queueLabel}</div><button className={`nav-item ${page==='usage'?'active':''}`} onClick={()=>navigate('usage')}><Receipt size={20}/>用量与账单</button><button className={`nav-item ${page==='settings'?'active':''}`} onClick={()=>navigate('settings')}><GearSix size={20}/>组织设置</button><div className="profile"><span className="avatar">{identity.display_name?.slice(0,1)||'T'}</span><span>{identity.display_name}<small>{identity.role==='admin'?'工作空间管理员':identity.role==='operator'?'运营人员':'观察员'} · {identity.auth_mode==='oidc'?'OIDC':'开发身份'}</small></span><span className="plan-tag">Team</span></div></div></aside>
 <div className="main-shell"><header className="topbar"><IconButton icon={List} label="打开导航" className="mobile-menu icon-btn" onClick={()=>setMobileOpen(!mobileOpen)}/><div className="breadcrumb"><span>工作空间</span><span>/</span><b>{titles[page]||'清收活动'}</b>{route.includes('/')&&<><span>/</span><span>运行详情</span></>}</div><div className="top-actions"><button className="global-search mobile-search" onClick={()=>{setSearch('');setNotificationOpen(false);setSearchOpen(true)}}><MagnifyingGlass size={17}/><span>搜索案件、活动或功能…</span><kbd>⌘ K</kbd></button><button className="top-ai-button" onClick={()=>setCopilotOpen(true)}><Robot size={16}/><span>AI 查询</span><kbd>⌘ J</kbd></button><span className={`demo-tag ${backendStatus==='connected'?'server-connected':''}`}>{backendStatus==='connected'?'API v0.6.0':'演示'}</span><div className="notification-wrap"><button className="icon-btn notification-trigger" aria-label="打开通知中心" aria-expanded={notificationOpen} onClick={()=>setNotificationOpen(!notificationOpen)}><Bell size={19}/>{!notificationsRead&&<span className="notification-count">3</span>}</button>{notificationOpen&&<section className="notification-pop" aria-label="通知中心"><header><div><b>通知中心</b><small>AI 运行与账务提醒</small></div><button className="text-link small" onClick={()=>setNotificationsRead(true)}>全部已读</button></header><button onClick={()=>navigate('exceptions')}><span className="notification-icon warning"><ShieldCheck size={18}/></span><span><b>{protectedCount} 个案件已保护暂停</b><small>争议、停止联系或授权异常已阻断触达</small></span></button><button onClick={()=>navigate('payments')}><span className="notification-icon"><CurrencyCircleDollar size={18}/></span><span><b>1 笔回款等待匹配</b><small>核验后才会更新履约与应计佣金</small></span></button><button onClick={()=>navigate('logs')}><span className="notification-icon neutral"><WarningCircle size={18}/></span><span><b>今日运行摘要已生成</b><small>1,248 次动作，越权执行 0 次</small></span></button></section>}</div><IconButton icon={theme==='light'?Moon:Sun} label={theme==='light'?'切换深色主题':'切换浅色主题'} onClick={()=>setTheme(theme==='light'?'dark':'light')}/><IconButton icon={Question} label="交互指南" onClick={()=>setDialog({type:'help'})}/><button className="avatar top-avatar" aria-label="打开个人设置" onClick={()=>navigate('settings')}>{identity.display_name?.slice(0,1)||'T'}</button></div></header>
 <main className="main-content" key={`${tenant}-${page}`}>{page==='overview'?<Overview/>:page==='activities'?<Activities/>:page==='assets'?<Assets/>:page==='cases'?<Cases/>:page==='payments'?<Payments/>:page==='agents'?<AgentScreen/>:page==='exceptions'?<Exceptions/>:page==='logs'?<Logs/>:page==='strategy'?<Strategy/>:page==='integrations'?<Integrations/>:page==='usage'?<Usage/>:page==='settings'?<Settings/>:<Activities/>}</main></div>
 {activeCase&&<CaseDrawer c={activeCase} tab={caseTab} setTab={setCaseTab} onClose={()=>setCaseId(null)}/>}
 {dialog?.type==='create'&&<CreateWizard defaultPackage={dialog.package} onClose={()=>setDialog(null)}/>}
 {dialog?.type==='policy'&&<PolicyModal packageId={dialog.package||visiblePackages[0].package_id} onClose={()=>setDialog(null)}/>}
 {dialog?.type==='receipt'&&tenant==='TENANT_A'&&<ReceiptModal onClose={()=>setDialog(null)}/>}
 {dialog?.type==='payment'&&dialog.transaction&&<PaymentDetailModal transaction={dialog.transaction} onClose={()=>setDialog(null)}/>}
 {dialog?.type==='exception'&&<ExceptionModal caseId={dialog.caseId} onClose={()=>setDialog(null)}/>}
 {dialog?.type==='import'&&<ImportModal onClose={()=>setDialog(null)}/>}
 {dialog?.type==='help'&&<Modal title="体验一条完整的资产履约流程" subtitle="当前 Provider 与业务数据均为沙箱样本，可随时还原。" onClose={()=>setDialog(null)} footer={<Button variant="primary" onClick={()=>{setDialog(null);navigate('activities')}}>开始体验 <ArrowRight size={16}/></Button>}><ol className="guide"><li><b>完成 AI 与渠道接入</b><p>分别配置 Agent、模型、语音和电话，完成连接、自测与启用。</p></li><li><b>创建履约活动</b><p>选择资产包与目标，由后端重新预检并冻结服务快照。</p></li><li><b>查看 Agent 执行</b><p>查看运行阶段、判断依据和下一步，体验暂停与恢复。</p></li><li><b>核对到账与佣金</b><p>模拟 ¥1,016 到账，观察履约、回款和应计佣金联动。</p></li></ol><div className="notice">API 已连接时配置与门禁写入后端；离线时明确降级为浏览器演示。两种模式都不保存明文凭证，也不会发起真实外呼或支付。</div></Modal>}
 {dialog?.type==='reset'&&<Modal title="还原演示数据" subtitle="将清除本次会话内创建的活动、策略修改和模拟到账。" onClose={()=>setDialog(null)} footer={<><Button onClick={()=>setDialog(null)}>取消</Button><Button variant="primary" onClick={reset}>还原演示</Button></>}><p>还原后回到最初的样本状态。此操作仅影响当前原型。</p></Modal>}
 {searchOpen&&<Modal title="搜索工作空间" onClose={()=>setSearchOpen(false)}><Search value={search} onChange={setSearch} placeholder="输入案件编号、活动名称或功能…"/><div className="command-results">{!search&&<p className="muted small">快速跳转</p>}{Object.entries(titles).filter(([,v])=>!search||v.toLowerCase().includes(search.toLowerCase())).slice(0,search?10:5).map(([id,v])=><button key={id} onClick={()=>{navigate(id);setSearchOpen(false)}}><Command size={17}/><span>{v}</span><ArrowRight size={16}/></button>)}{results.map(c=><button key={c.case_id} onClick={()=>{setSearchOpen(false);openCase(c.case_id)}}><Files size={17}/><span>{c.case_id} · {c.status}</span><small>{c.package_id}</small></button>)}{acts.map(x=><button key={x.id} onClick={()=>{navigate('agents/'+x.id);setSearchOpen(false)}}><Robot size={17}/><span>{x.name}</span><ArrowRight size={16}/></button>)}{search&&!results.length&&!acts.length&&!Object.values(titles).some(v=>v.toLowerCase().includes(search.toLowerCase()))&&<Empty/>}</div><p className="muted small">仅搜索当前组织的数据 · 按 Esc 关闭</p></Modal>}
 <AICopilot/>
 {toast&&<div className="toast" role="status"><CheckCircle size={20} weight="fill"/><span>{toast}</span><IconButton icon={X} label="关闭提示" onClick={()=>setToast('')}/></div>}
 </div></AppContext.Provider>
}

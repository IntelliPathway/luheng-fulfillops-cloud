import React,{useEffect,useRef,useState} from 'react';
import {ArrowRight,Clock,Flask,GearSix,Plus,Waveform} from '@phosphor-icons/react';
import {useApp} from './context';
import {voiceCombinationApi} from './api';
import {useRemoteResource} from './remote-resource';
import {Badge,Button,Empty,Field,KeyValue,Modal} from './ui';
import fallbackCatalog from './voice-model-catalog.json';
import './voice-combinations.css';

const profiles={'baseline':'均衡基线','asr-fast':'ASR 轻量对照','llm-4bit':'LLM 4bit 对照','tts-large':'TTS 1.7B 对照'};
const errors={local_model_host_disabled:'本地模型宿主尚未启用，请由管理员完成连接配置。',local_model_host_unavailable:'无法连接本地模型宿主，请检查宿主是否运行。',model_not_prepared_or_invalid:'所选模型尚未准备好，请先在模型宿主准备权重。',model_load_failed:'模型加载失败，请检查本地权重与运行环境。',model_load_timeout:'模型加载超时，请检查本地运行环境。',local_voice_busy:'电话或模型会话正在使用，请结束后重试。',model_host_busy:'另一项模型测试正在运行，请等待完成。',unmanaged_model_service_running:'检测到独立运行的模型服务，请结束后交由模型宿主管理。',invalid_model_host_report:'测试未返回完整有效证据，请重新验证。',local_voice_configuration_mismatch:'实际服务与所选组合不一致，请重新验证。',local_voice_revision_mismatch:'实际权重版本不一致，请重新验证。'};
const explain=code=>errors[code]||'模型链路未通过验证，请检查连接与当前配置。';
function exportSelection(row){const url=URL.createObjectURL(new Blob([JSON.stringify(row.selection,null,2)+'\n'],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`${row.id}-v${row.version}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const label=alias=>({'fun-asr-nano':'FunASR Nano','sensevoice-small':'SenseVoice Small','cosyvoice3':'CosyVoice 3'}[alias]||alias.replace('qwen-','Qwen ').replace('llm-30b-','3 30B-A3B ').replace('asr-','3 ASR ').replace('tts-','3 TTS ').replace(/(\d+(?:\.\d+)?)b\b/g,'$1B'));
const ms=value=>typeof value==='number'?`${value.toLocaleString('zh-CN')} ms`:'—';
const time=value=>value?new Date(`${value}${/[Z+]|\d-\d\d:\d\d$/.test(value)?'':'Z'}`).toLocaleString('zh-CN',{hour12:false}):'—';

function useAction(scope,refresh){
 const live=useRef(scope),inFlight=useRef(null);live.current=scope;
 const [state,setState]=useState({scope,busy:'',error:''});
 useEffect(()=>()=>{live.current=null},[]);
 const visible=state.scope===scope?state:{busy:'',error:''};
 const run=async(name,task)=>{
  if(inFlight.current===scope)return;
  const current=scope;inFlight.current=current;setState({scope:current,busy:name,error:''});
  try{await task(()=>live.current===current);if(live.current===current){setState({scope:current,busy:'',error:''});refresh()}}
  catch(error){if(live.current===current){setState({scope:current,busy:'',error:error.message||'请求未完成'});refresh()}}
  finally{if(inFlight.current===current)inFlight.current=null}
 };
 return {...visible,run};
}

function CombinationForm({row,catalog,onClose,onSave,busy,error,online}){
 const [name,setName]=useState(row?.name||'均衡基线');
 const [selection,setSelection]=useState(row?.selection||catalog.profiles.baseline);
 const [preset,setPreset]=useState(row?'custom':'baseline');
 const setStage=(stage,value)=>{setPreset('custom');setSelection(old=>({...old,[stage]:{...old[stage],model:value,...(stage==='tts'&&value!=='qwen-tts-1.7b'?{style:'default'}:{})}}))};
 return <Modal title={row?'编辑模型组合':'新增模型组合'} subtitle="保存后需完成连接检查、真实链路测试，再启用到活动。" onClose={busy?()=>{}:onClose} footer={<><Button disabled={!!busy} onClick={onClose}>取消</Button><Button variant="primary" disabled={!online||!!busy||!name.trim()} onClick={()=>onSave({id:row?.id,version:row?.version||0,name:name.trim(),selection})}>{busy?'保存中…':'保存组合'}</Button></>}>
  <Field label="组合名称"><input value={name} maxLength={80} onChange={event=>setName(event.target.value)}/></Field>
  <Field label="模型预设" hint="对照预设只替换一个环节，方便比较质量与延迟。"><select value={preset} onChange={event=>{const value=event.target.value;setPreset(value);if(value!=='custom'){setSelection(catalog.profiles[value]);if(!row)setName(profiles[value])}}}>{Object.entries(profiles).map(([id,title])=><option key={id} value={id}>{title}</option>)}<option value="custom">自定义组合</option></select></Field>
  {['asr','llm','tts'].map(stage=><Field key={stage} label={`${stage.toUpperCase()} 模型`}><select value={selection[stage].model} onChange={event=>setStage(stage,event.target.value)}>{Object.entries(catalog.models).filter(([,model])=>model.kind===stage).map(([alias,model])=><option key={alias} value={alias}>{label(alias)}{model.adapter_implemented?'':' · 适配器待实现'}</option>)}</select></Field>)}
  <div className="voice-form-pair"><Field label="播报音色"><select value={selection.tts.voice||'Vivian'} onChange={event=>{setPreset('custom');setSelection(old=>({...old,tts:{...old.tts,voice:event.target.value}}))}}>{catalog.voices.map(voice=><option key={voice}>{voice}</option>)}</select></Field><Field label="播报风格"><select value={selection.tts.style||'default'} onChange={event=>{setPreset('custom');setSelection(old=>({...old,tts:{...old.tts,style:event.target.value}}))}}><option value="default">默认</option><option value="calm" disabled={selection.tts.model!=='qwen-tts-1.7b'}>平静清晰 · 1.7B</option></select></Field></div>
  <p className="notice">修改会使旧连接检查、测试报告和启用状态失效。权重版本在模型宿主准备时固定，页面不接收凭据。</p>
  {!online&&<p className="muted">当前可浏览配置；连接业务 API 后才能保存和测量。</p>}
  {error&&<p role="alert" className="form-error">{error}</p>}
 </Modal>;
}

export function VoiceCombinations(){
 const a=useApp(),online=a.backendStatus==='connected',admin=a.identity.role==='admin';
 const scope=`${a.tenant}:${a.backendStatus}`;
 const resource=useRemoteResource(online,scope,()=>voiceCombinationApi.overview(a.tenant));
 const action=useAction(scope,resource.refresh);
 const [editing,setEditing]=useState(null),[chosen,setChosen]=useState({scope,ids:[]}),[comparison,setComparison]=useState('all');
 useEffect(()=>{setEditing(null);setChosen({scope,ids:[]});setComparison('all')},[scope]);
 const data=resource.data?.tenant_id===a.tenant?resource.data:null;
 const catalog=data?.catalog||fallbackCatalog,rows=data?.combinations||[],reports=data?.reports||[];
 const selected=chosen.scope===scope?chosen.ids.filter(id=>rows.some(row=>row.id===id)):[];
 const canWrite=online&&admin&&!!data&&!action.busy;
 const canCompare=canWrite&&selected.length>0&&selected.every(id=>rows.find(row=>row.id===id)?.adapter_implemented);
 const toggle=id=>setChosen({scope,ids:selected.includes(id)?selected.filter(value=>value!==id):selected.length<4?[...selected,id]:selected});
 const groups=[...new Set(reports.map(report=>report.comparison_id))];
 const shown=reports.filter(report=>comparison==='all'||report.comparison_id===comparison).slice(0,12);
 const save=row=>action.run('save',async current=>{await voiceCombinationApi.save(a.tenant,row);if(current())setEditing(null)});
 return <section className="voice-combinations governance-panel" aria-label="语音模型组合">
  <div className="integration-section-head"><div><span className="eyebrow">ASR · LLM · TTS</span><h2>语音模型组合</h2><p>管理员配置与验证，运营人员在活动中选择已启用组合。</p></div><div className="voice-actions"><Button disabled={resource.busy||!!action.busy||!online} onClick={resource.refresh}>刷新状态</Button><Button icon={Plus} variant="primary" disabled={!!action.busy||online&&!admin} onClick={()=>setEditing({row:null,scope})}>新增组合</Button></div></div>
  {!online&&<div className="notice">{a.backendStatus==='offline'?'当前为页面演示；模型目录可浏览，连接业务 API 后才能保存、测试和启用。':'业务 API 尚未可用；模型配置和测试操作已暂停。'}<Button onClick={()=>a.navigate('pilot')}>查看接入诊断</Button></div>}
  {online&&!admin&&<p className="notice">当前成员可查看测试报告，在活动页使用管理员已启用的组合。配置、对照测试和启用由管理员完成。</p>}
  {(resource.error||action.error)&&<p role="alert" className="form-error">{action.error||resource.error}</p>}
  {resource.busy&&<p role="status">正在读取模型组合与报告…</p>}
  <div className="voice-preset-strip">{Object.entries(profiles).map(([id,title])=><div key={id}><b>{title}</b><small>{['asr','llm','tts'].map(stage=>label(catalog.profiles[id][stage].model)).join(' / ')}</small></div>)}</div>
  {!!rows.length&&<div className="table-scroll"><table className="data-table voice-combination-table"><thead><tr><th>对照</th><th>组合 / 版本</th><th>ASR / LLM / TTS</th><th>连接与启用状态</th><th>操作</th></tr></thead><tbody>{rows.map(row=>{const latest=reports.find(report=>report.combination_id===row.id);const passed=latest?.current&&latest.status==='passed';return <tr key={row.id}><td><input type="checkbox" aria-label={`对照 ${row.name}`} checked={selected.includes(row.id)} disabled={!canWrite||(!selected.includes(row.id)&&selected.length>=4)||!row.adapter_implemented} onChange={()=>toggle(row.id)}/></td><td><b>{row.name}</b><small>v{row.version} · {row.selection.tts.voice||'Vivian'}</small></td><td>{['asr','llm','tts'].map(stage=><small className="voice-model-name" key={stage}>{stage.toUpperCase()} · {label(row.selection[stage].model)}</small>)}</td><td><Badge status={row.enabled?'completed':'neutral'}>{row.enabled?'已启用 · 合成测试':row.active_report_id?'测试待完成':!row.adapter_implemented?'适配器待实现':row.connection.status==='prepared'?'宿主可达 · 权重已准备':row.connection.status==='unavailable'?'连接不可用':'待连接检查'}</Badge>{row.connection.error&&<small className="voice-error">{explain(row.connection.error)}</small>}</td><td><div className="voice-row-actions"><Button disabled={!admin||!!action.busy} onClick={()=>exportSelection(row)}>导出配置</Button><Button icon={GearSix} disabled={!canWrite||!!row.active_report_id} onClick={()=>setEditing({row,scope})}>编辑</Button><Button disabled={!canWrite||!row.adapter_implemented||!!row.active_report_id} onClick={()=>action.run(`connect:${row.id}`,()=>voiceCombinationApi.connect(a.tenant,row))}>检查连接</Button><Button disabled={!canWrite||!passed||row.enabled||!!row.active_report_id} onClick={()=>action.run(`enable:${row.id}`,()=>voiceCombinationApi.enable(a.tenant,row))}>{row.enabled?'已启用':'启用组合'}</Button></div></td></tr>})}</tbody></table></div>}
  {!resource.busy&&!rows.length&&<Empty title="尚未保存模型组合" description="先选择均衡基线，再用单环节预设做对照。连接检查会核对实际准备的模型和权重版本。"/>}
  <div className="voice-compare-bar"><span>已选 {selected.length}/4 套 · 同一固定内部语句 · 每套 3 次</span><Button icon={Flask} variant="primary" disabled={!canCompare} onClick={()=>action.run('compare',()=>voiceCombinationApi.compare(a.tenant,rows.filter(row=>selected.includes(row.id))))}>{action.busy==='compare'?'切换模型并测试中…':'开始对照测试'}</Button></div>
  {!!action.busy&&<p role="status" className="muted">{action.busy==='compare'?'正在加载与预热模型，再依次测量。报告完成前不会显示通过；电话会话占用时会停止切换。':'正在提交操作，请等待结果。'}</p>}
  <div className="integration-section-head voice-report-head"><div><h3>延迟与对照报告</h3><p>加载与预热不计入测量；首音为完整合成测试首段音频，不代表电话端听到声音的耗时。</p></div>{groups.length>0&&<select aria-label="筛选对照报告" value={comparison} onChange={event=>setComparison(event.target.value)}><option value="all">最近报告</option>{groups.map(id=><option key={id} value={id}>{id}</option>)}</select>}</div>
  {shown.length?<div className="table-scroll"><table className="data-table voice-report-table"><thead><tr><th>组合 / 结果</th><th>ASR P50</th><th>LLM P50</th><th>TTS P50</th><th>合成首音 P50</th><th>全链路 P50 / P95</th><th>样本 / 时间</th></tr></thead><tbody>{shown.map(report=>{const stat=report.result.statistics||{};return <tr key={report.id}><td><b>{report.combination_name} · v{report.config_version}</b><small>{report.status==='passed'?'实际模型链路通过':report.status==='running'?'测试正在执行':report.status==='unknown'?'结果未知，请核对宿主':'测试未通过'} · {report.current?'当前版本':'历史或已过期'}</small>{report.result.error&&<small className="voice-error">{explain(report.result.error)}</small>}</td><td>{ms(stat.asr?.p50_ms)}</td><td>{ms(stat.llm?.p50_ms)}</td><td>{ms(stat.reply_tts?.p50_ms)}</td><td>{ms(stat.first_audio_ms?.p50_ms)}</td><td>{ms(stat.elapsed_ms?.p50_ms)} / {ms(stat.elapsed_ms?.p95_ms)}</td><td>{report.result.sample_count??'—'}<small>{time(report.created_at)}</small><details><summary>查看权重与证据</summary><code>{report.id}</code>{Object.entries(report.result.configuration?.revisions||{}).map(([kind,revision])=><small key={kind}>{kind.toUpperCase()} · {revision}</small>)}<small>来源：{report.result.source==='local_model_host'?'本地模型宿主':'尚无有效测量'} · 24 小时内有效</small></details></td></tr>})}</tbody></table></div>:<p className="muted voice-empty-report"><Clock size={18}/> 尚无实际测量报告；耗时保持为空。</p>}
  <div className="sandbox-boundary"><Waveform size={19}/><span><b>模型验证与电话验证分别记录</b><small>仅使用固定内部语句，不读取案件、不拨号。报告通过后可在活动页启用合成测试；真实电话媒体与业务渠道仍需独立验收。</small></span>{admin&&<a className="text-link" href="https://github.com/IntelliPathway/luheng-fulfillops-cloud/blob/main/docs/integrations/linphone-sip-lab.md" target="_blank" rel="noreferrer">模型宿主接入指南 <ArrowRight size={14}/></a>}</div>
  {editing?.scope===scope&&<CombinationForm key={editing.row?.id||'new'} row={editing.row} catalog={catalog} online={online&&admin&&!!data} busy={action.busy==='save'} error={action.error} onClose={()=>setEditing(null)} onSave={save}/>}
 </section>;
}

export function ActivityVoiceCombination({activity}){
 const a=useApp(),online=a.backendStatus==='connected',scope=`${a.tenant}:${activity.id}:${a.backendStatus}`;
 const resource=useRemoteResource(online,scope,async()=>({overview:await voiceCombinationApi.overview(a.tenant),choice:await voiceCombinationApi.activity(a.tenant,activity.id)}));
 const action=useAction(scope,resource.refresh),[selected,setSelected]=useState({scope,id:''}),[result,setResult]=useState({scope,data:null});
 const data=resource.data?.overview.tenant_id===a.tenant?resource.data:null;
 const available=(data?.overview.combinations||[]).filter(row=>row.enabled&&!row.active_report_id);
 const id=selected.scope===scope?selected.id:'',choice=data?.choice;
 const canUse=online&&['admin','operator'].includes(a.identity.role)&&!['blocked','completed'].includes(activity.status)&&!action.busy&&!!data;
 const choose=row=>action.run('bind',async current=>{await voiceCombinationApi.bind(a.tenant,activity.id,row);if(current())setResult({scope,data:null})});
 const test=()=>action.run('test',async current=>{setResult({scope,data:null});const response=await voiceCombinationApi.testActivity(a.tenant,activity.id);if(current())setResult({scope,data:response})});
 const report=result.scope===scope?result.data?.reports?.[0]:null;
 return <section className="governance-panel activity-voice-combination" aria-label="活动语音模型组合"><div className="integration-section-head"><div><h2>语音模型组合</h2><p>使用管理员验证并启用的组合；活动保存模型、权重与测试版本。</p></div><Button onClick={()=>a.navigate('integrations')}>查看模型接入</Button></div>
  {(resource.error||action.error)&&<p className="form-error" role="alert">{action.error||resource.error}</p>}
  {!online&&<p className="notice">连接业务 API 后，才能选择管理员启用的模型组合。</p>}
  {choice?.snapshot?<><KeyValue items={[["当前组合",`${choice.snapshot.name} · v${choice.snapshot.version}`],["快照状态",choice.current?'已启用 · 合成验证有效':'已失效 · 请重新选择'],["模型",['asr','llm','tts'].map(stage=>label(choice.snapshot.configuration.selection[stage].model)).join(' / ')]]}/></>:<p className="muted">活动尚未选择语音组合。</p>}
  <div className="voice-activity-controls"><Field label="活动可用组合"><select disabled={!canUse||!available.length} value={available.some(row=>row.id===id)?id:''} onChange={event=>{setSelected({scope,id:event.target.value});setResult({scope,data:null})}}><option value="">{available.length?'请选择已启用组合':'暂无有效且已启用的组合'}</option>{available.map(row=><option key={row.id} value={row.id}>{row.name} · v{row.version}</option>)}</select></Field><Button variant="primary" disabled={!canUse||!available.some(row=>row.id===id)} onClick={()=>choose(available.find(row=>row.id===id))}>应用到活动</Button><Button icon={Flask} disabled={!canUse||!choice?.current} onClick={test}>{action.busy==='test'?'验证中…':'验证活动语音链路'}</Button></div>
  {resource.busy&&<p role="status">正在读取活动模型快照…</p>}{action.busy&&<p role="status">正在执行，请等待实际结果。</p>}
  {report&&<p role="status" className={report.status==='passed'?'notice':'form-error'}>{report.status==='passed'?`固定内部语句验证通过 · ASR ${ms(report.result.statistics?.asr?.p50_ms)} · LLM ${ms(report.result.statistics?.llm?.p50_ms)} · TTS ${ms(report.result.statistics?.reply_tts?.p50_ms)}`:explain(report.result.error)}</p>}
  <p className="muted small">此入口执行内部合成测试，不使用案件信息、不拨打客户电话。电话渠道门禁与业务授权继续由服务端检查。</p>
 </section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {CheckCircle,WarningCircle,ArrowClockwise,DownloadSimple} from '@phosphor-icons/react';
import {operationsApi} from './api';
import {publicRuntimeConfig} from './runtime-config';
import {Button,KeyValue} from './ui';

export function ConnectionReadiness({tenant,connected}){
 const [state,setState]=useState({busy:false,error:'',report:null});
 const sequence=useRef(0);
 const load=async()=>{
  const current=++sequence.current;setState({busy:true,error:'',report:null});
  try{const report=await operationsApi.connectionReadiness(tenant);if(current===sequence.current)setState({busy:false,error:'',report})}
  catch(error){if(current===sequence.current)setState({busy:false,error:error.message,report:null})}
 };
 useEffect(()=>{sequence.current++;setState({busy:false,error:'',report:null});if(connected)load();return()=>{sequence.current++}},[tenant,connected]);
 const config=publicRuntimeConfig();const configured=config.mode==='connected';
 const report=state.report?.tenant_id===tenant?state.report:null;
 const ready=configured&&report?.status==='ready_for_pilot';
 const download=()=>{
  if(!report)return;
  const evidence={schema_version:1,site_runtime_mode:config.mode||'build-time-development',site_connection_configured:configured,technical_status:ready?'ready_for_pilot':'blocked',backend:report,real_business_verified:false,enables_external_execution:false};
  const url=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='connection-readiness-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 return <section className="case-validation dashboard-card connection-readiness" aria-label="生产连接诊断">
  <div className="section-title"><h2>生产连接诊断</h2><div className="head-actions">{connected&&<Button icon={ArrowClockwise} disabled={state.busy} onClick={load}>{state.busy?'正在检查':'重新检查连接'}</Button>}{report&&<Button icon={DownloadSimple} onClick={download}>下载接入报告</Button>}</div></div>
  <p className="muted">检查当前连接与部署条件，区分技术接入和业务验收。此检查不会配置服务、修改权限或发起业务执行。</p>
  <div className="notice" role="status"><WarningCircle size={20}/><span>{!connected?'当前为演示模式，尚未连接业务后端。':state.busy?'正在核对服务端接入证据。':state.error?'接入检查失败，请修复后重新检查。':ready?'技术接入具备试点条件，真实案例仍需单独验收。':'技术接入尚未就绪，请处理下方检查项。'}</span></div>
  {state.error&&<p className="form-error" role="alert">{state.error}</p>}
  <div className="case-validation-checks"><article>{configured?<CheckCircle size={20}/>:<WarningCircle size={20}/>}<div><b>站点企业接入配置</b><small>{configured?'已配置业务 API 与企业登录；仍需当前会话及浏览器跨域实测。':'尚未配置正式业务 API 和企业登录。由部署管理员完成站点公开接入参数设置。'}</small></div><span>{configured?'已配置':'待配置'}</span></article>{report?.checks.map(check=><article key={check.id}>{check.passed?<CheckCircle size={20}/>:<WarningCircle size={20}/>}<div><b>{check.label}</b><small>{check.passed?'本次服务端检查通过。':check.action}</small></div><span>{check.passed?'通过':'待处理'}</span></article>)}</div>
  {report&&<KeyValue items={[["当前工作空间",report.tenant_id],["后端版本",report.app_version],["业务联合验收",report.business_acceptance_status==='accepted'?'已接受':'尚未完成'],["检查摘要",report.report_digest],["检查时间",new Date(report.generated_at).toLocaleString('zh-CN',{hour12:false})]]}/>}
  {!connected&&<ol className="case-validation-steps"><li>开通生产后端与两个企业管理员</li><li>配置正式站点与企业登录，再检查连接</li><li>连接通过后导入授权脱敏案例验收</li></ol>}
 </section>;
}

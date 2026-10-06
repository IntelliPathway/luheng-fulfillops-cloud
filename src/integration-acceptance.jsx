import React,{useEffect,useRef,useState} from 'react';
import {operationsApi} from './api';
import {APP_VERSION} from './brand';
import {useRemoteResource} from './remote-resource';
import {Button} from './ui';

export function IntegrationAcceptance({tenant,connected}){
 const active=useRef(true),[exporting,setExporting]=useState(false),[error,setError]=useState('');
 useEffect(()=>{active.current=true;return()=>{active.current=false}},[]);
 const read=async()=>{const r=await operationsApi.integrationAcceptance(tenant);if(r?.tenant_id!==tenant||r.app_version!==APP_VERSION||r.schema_version!==1||!Array.isArray(r.checks))throw Error('验收报告范围或版本不匹配');return r};
 const state=useRemoteResource(connected,tenant,read),report=state.data;
 const exportReport=async()=>{if(exporting)return;setExporting(true);setError('');try{const fresh=await read();if(!active.current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(fresh,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`RepayGuard-v${APP_VERSION}-${tenant}-integration.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);state.refresh()}catch(e){if(active.current){setError(e.message);state.refresh()}}finally{if(active.current)setExporting(false)}};
 return <section className="activity-section integration-acceptance" aria-label="v5.0 集成验收"><div className="section-title"><h2>v5.0 集成验收</h2><div className="material-toolbar"><Button disabled={!connected||state.busy||exporting} onClick={()=>{setError('');state.refresh()}}>重新核验集成</Button><Button disabled={!report||state.busy||exporting||!!error} onClick={exportReport}>下载当前集成报告</Button></div></div>
 <p className="notice">软件发布、当前生产接入和客户案例验收分别核验。报告不批准外呼、模型调用或资金操作。</p>
 {!connected&&<><p>业务 API 尚未接入，不能生成生产验收报告。可在下方查看接入条件与客户证据流程。</p><p>本演示支持案件、保护、回款查询与活动草案；真实客户验证等待授权来源。</p></>}
 {state.busy&&<p role="status">正在核验当前集成证据…</p>}{(state.error||error)&&<p role="alert" className="form-error">{state.error||error}</p>}
 {report&&<><p><b>{report.status==='ready_for_controlled_pilot'?'当前条件可进入受控试点':'当前集成验收仍有阻断'}</b> · {new Date(report.generated_at).toLocaleString('zh-CN')}</p><div className="release-gate">{report.checks.map(c=><article key={c.id}><span><b>{c.label}</b><small>{c.passed?'当前证据满足':'需要补齐条件'}</small></span></article>)}</div><p>有效客户案例 {report.customer_cases.current_accepted} 个；已失效历史验收 {report.customer_cases.invalidated_accepted} 个。</p><p className="muted">仅核验当前租户证据；独立人工声明不等于外部真实性证明。</p></>}
 </section>;
}

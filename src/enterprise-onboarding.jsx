import React from 'react';
import {operationsApi} from './api';
import {useRemoteResource} from './remote-resource';
import {Button} from './ui';

const guide=[
 {id:'identity',label:'企业成员与独立审核',action:'settings',detail:'按企业清单开通两个不同的管理员账号，并核对登录 subject。'},
 {id:'connection',label:'正式后台与登录连接',action:'connection',detail:'部署业务后台、数据库、Worker 和身份服务，配置正式 HTTPS 地址。'},
 {id:'imports',label:'案件与委托导入',action:'assets',detail:'下载案件模板，预演校验后由另一位管理员确认导入。'},
 {id:'materials',label:'材料接入',action:'materials',detail:'按来源编号上传授权材料，保留原件和版本历史。'},
 {id:'acceptance',label:'关联、对账与独立验收',action:'acceptance',detail:'核对付款、退款与回执，再由独立管理员复核。退款可以为零。'},
];
export function EnterpriseOnboarding({tenant,connected,onNavigate,onStep}){
 const state=useRemoteResource(connected,tenant,async()=>{const r=await operationsApi.enterpriseOnboarding(tenant);if(r.tenant_id!==tenant||r.schema_version!==1||!Array.isArray(r.steps))throw Error('企业接入报告范围不匹配');return r});
 const steps=state.data?.steps||guide;
 const open=step=>{if(['settings','assets'].includes(step.action))onNavigate(step.action);else if(step.action==='connection')document.querySelector('.connection-readiness')?.scrollIntoView({behavior:'smooth',block:'start'});else {onStep(step.action);document.querySelector('.customer-workflow')?.scrollIntoView({behavior:'smooth',block:'start'})}};
 return <section className="dashboard-card enterprise-onboarding" aria-label="企业接入向导">
 <div className="section-title"><h2>企业接入向导</h2><Button disabled={!connected||state.busy} onClick={state.refresh}>刷新接入进度</Button></div>
 <p className="muted">按同一标准开通首家与后续企业。每个步骤都保留来源与审核记录。</p>
 {!connected&&<p className="notice">当前为演示模式。下方提供接入步骤和模板，正式进度在连接后台后核验。</p>}
 {state.busy&&<p role="status">正在读取当前企业接入进度…</p>}{state.error&&<p role="alert" className="form-error">{state.error}</p>}
 <div className="enterprise-steps">{steps.map((step,index)=><article key={step.id}><div><b>{index+1}. {step.label}</b><p>{step.detail}</p><span className="muted">{state.data?(step.complete?'步骤证据已具备':'待处理'):'连接后核验'}</span></div><Button onClick={()=>open(step)}>处理此步骤</Button></article>)}</div>
 <div className="material-toolbar"><a href="/standards/enterprise-cases.csv" download>下载案件模板</a><a href="/standards/enterprise-materials.csv" download>下载金额材料模板</a><a href="/standards/enterprise-manifest.json" download>下载企业成员清单</a><a href="/standards/enterprise-standard.json" download>下载接入契约</a></div>
 <p className="muted">模板包含明确标注的合成示例。步骤具备证据不代表真实客户验收通过；完整门禁见下方集成验收。</p>
 </section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {operationsApi} from './api';
import {Button,Empty} from './ui';
const labels={queued:'等待处理',running:'处理中',succeeded:'已接入材料',failed:'处理失败',cancelled:'已取消'};
export function CustomerSync({tenant,connected,identity}){
 const [rows,setRows]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);const seq=useRef(0);
 const load=async()=>{const id=++seq.current;setRows([]);setError('');if(!connected)return;setBusy(true);try{const r=await operationsApi.customerSyncEvents(tenant);if(id!==seq.current)return;if(!Array.isArray(r)||r.some(e=>e.tenant_id!==tenant))throw Error('同步范围不匹配');setRows(r)}catch(e){if(id===seq.current)setError(e.message)}finally{if(id===seq.current)setBusy(false)}};
 useEffect(()=>{load();return()=>{seq.current++}},[tenant,connected]);
 const retry=async row=>{setBusy(true);setError('');try{await operationsApi.retryCustomerSync(tenant,row.job_id);await load()}catch(e){setError(e.message);setBusy(false)}};
 return <section className="activity-section customer-sync" aria-label="业务数据同步"><div className="section-title"><h2>业务数据同步</h2><Button disabled={!connected||busy} onClick={load}>刷新同步</Button></div><p className="muted">授权业务系统通过标准接口推送材料；事件去重，失败可重试，不直接写入回款账簿。</p><details><summary>接入约定</summary><p>由企业身份授权运营角色，推送到 /api/v1/customer-sync/webhook。来源系统与事件编号确定唯一性，材料须获授权并脱敏。最多重试 3 次，服务端保留加密原始事件。</p></details>{error&&<p role="alert" className="form-error">{error}</p>}{rows.length?<div className="material-list">{rows.map(r=><article key={r.id}><div><b>{r.source_system} · {r.external_event_id}</b><p>{labels[r.status]||'待确认'} · 第 {r.attempt}/{r.max_attempts} 次处理</p>{r.error&&<p>{r.error}</p>}</div>{r.status==='failed'&&r.attempt<r.max_attempts&&['admin','operator'].includes(identity?.role)&&<Button disabled={busy} onClick={()=>retry(r)}>重试同步</Button>}</article>)}</div>:<Empty title={busy?'正在读取同步状态':connected?'暂无同步事件':'连接业务 API 后查看同步'} description="未接入数据不会生成成功记录。"/>}</section>
}

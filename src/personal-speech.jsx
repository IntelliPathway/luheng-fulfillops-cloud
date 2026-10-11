import React,{useState} from 'react';
import {Waveform} from '@phosphor-icons/react';
import {Button,Field,KeyValue} from './ui';
import {readSpeechPreference,saveSpeechPreference,speechDownload,speechProfiles} from './personal-speech.js';

export function PersonalSpeechSettings({tenant,admin}){
 const [state,setState]=useState(()=>{try{const saved=readSpeechPreference(localStorage,tenant);return {profile:saved?.speech_profile||'baseline',saved:saved?.speech_profile||null,error:'',downloaded:false}}catch{return {profile:'baseline',saved:null,error:'无法读取网页音色偏好，请重新保存；Mac 配置未改变。',downloaded:false}}});
 const voice=speechProfiles[state.profile],dirty=state.saved!==state.profile;
 const save=()=>{if(!admin)return;try{saveSpeechPreference(localStorage,tenant,state.profile);setState({...state,saved:state.profile,error:'',downloaded:false})}catch{setState({...state,error:'浏览器无法保存偏好，未显示保存成功；请检查浏览器存储权限。'})}};
 const download=()=>{if(!admin||dirty)return;let url;try{const file=speechDownload(state.profile);url=URL.createObjectURL(new Blob([file.content],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=file.filename;link.click();setState({...state,error:'',downloaded:true});setTimeout(()=>URL.revokeObjectURL(url),1000)}catch{if(url)URL.revokeObjectURL(url);setState({...state,error:'配置下载未完成，请重试。'})}};
 return <section className="personal-speech-settings" aria-label="千问电话测试音色">
  <div className="integration-section-head"><div><span className="eyebrow">个人电话测试</span><h3><Waveform size={20}/> 千问电话测试音色</h3><p>选择音色并保存偏好，下载后在 Mac 应用一次；后续启动自动沿用。</p></div></div>
  <div className="personal-speech-layout"><Field label="测试播报音色" hint={voice.description}><select disabled={!admin} value={state.profile} onChange={event=>setState({...state,profile:event.target.value,error:'',downloaded:false})}>{Object.entries(speechProfiles).map(([key,item])=><option key={key} value={key}>{item.label}</option>)}</select></Field><KeyValue items={[["语速",`${voice.rate} 倍`],["音调",`${voice.pitch} 倍`],["TTS 模型","Qwen Audio 3.0 TTS Plus"]]}/></div>
  <div className="voice-actions"><Button variant="primary" disabled={!admin||!dirty} onClick={save}>保存测试偏好</Button><Button disabled={!admin||dirty} onClick={download}>下载本机配置</Button><span role="status" className="muted small">{dirty?'待保存':state.downloaded?'配置已生成下载；Mac 应用结果以本机终端为准':'网页偏好已保存；尚未确认 Mac 应用'}</span></div>
  {!admin&&<p className="muted small">音色偏好和配置下载由管理员操作；活动继续使用管理员已批准的模型组合。</p>}
  {state.error&&<p role="alert" className="form-error">{state.error}</p>}
  <details className="personal-speech-apply"><summary>在 Mac 应用并试听</summary><p>挂断当前电话，在原启动终端按 Ctrl+C 退出，再从仓库根目录执行：</p><pre><code>{'git pull\npython3 scripts/sip-lab.py start --speech-config ~/Downloads/sip-lab-speech.json'}</code></pre><p>隐藏输入 Key，手机拨1003试听。启动成功后配置记在该 Mac；下次运行 <code>python3 scripts/sip-lab.py start</code> 即可。浏览器若改了下载文件名，请使用实际文件路径。</p></details>
  <p className="muted small">此处保存网页测试偏好，不启用活动、不连接 Mac、不保存 Key。配置不含凭据，音色自然度以实际试听为准。当前语速和音调随预设选择。</p>
 </section>;
}

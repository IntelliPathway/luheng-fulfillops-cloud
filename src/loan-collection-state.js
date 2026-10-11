// Business idempotency keys, not credentials. getRandomValues also works on HTTP previews.
export function loanRequestKey(cryptoProvider=globalThis.crypto){
 if(cryptoProvider?.randomUUID)return `loan-${cryptoProvider.randomUUID()}`;
 if(cryptoProvider?.getRandomValues){
  const bytes=cryptoProvider.getRandomValues(new Uint8Array(16));
  return `loan-${Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')}`;
 }
 return `loan-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function loanPolicyMinute(value){
 const match=/^(\d{2}):(\d{2})$/.exec(value);
 if(!match)throw new Error('联系时段请填写 HH:mm');
 const hour=Number(match[1]),minute=Number(match[2]);
 if(minute>59||hour>24||(hour===24&&minute!==0))throw new Error('联系时段无效');
 return hour*60+minute;
}

export function loanPolicyTime(value){
 return `${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;
}

export function loanPolicyPayload(form,version){
 const start=loanPolicyMinute(form.start),end=loanPolicyMinute(form.end);
 if(start>=end)throw new Error('开始时间必须早于结束时间；首期不支持跨午夜');
 if(!form.valid_until||!/(Z|[+-]\d{2}:\d{2})$/.test(form.valid_until))throw new Error('到期时间须包含 Z 或时区偏移');
 return {timezone:'Asia/Shanghai',window_start_minute:start,window_end_minute:end,
  daily_session_limit:Number(form.daily_session_limit),snapshot_max_hours:Number(form.snapshot_max_hours),
  promise_max_days:Number(form.promise_max_days),authorization_minutes:Number(form.authorization_minutes),
  paused:form.paused,authority_reference:form.authority_reference,valid_until:form.valid_until,
  expected_version:version,acknowledged:true};
}

export function loanShanghaiDate(){
 return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
}

const exactKeys=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const validDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value;
export function parseSyntheticPhoneResult(text,today=loanShanghaiDate()){
 const fail=()=>{throw new Error('请导入当天已明确确认的100元合成电话结果；该文件只能作为人工审核草案。')};
 if(typeof text!=='string'||text.length>4096||!validDate(today))return fail();
 let data;try{data=JSON.parse(text)}catch{return fail()}
 if(!exactKeys(data,['schema_version','mode','fixture_case','session_id','as_of_date','stage','verification','candidate','phone_audio_verified','business_ready'])||
  data.schema_version!==1||data.mode!=='synthetic_phone_dialogue'||data.fixture_case!=='FIXTURE-LOAN-001'||
  typeof data.session_id!=='string'||! /^[a-f0-9]{32}$/.test(data.session_id)||data.as_of_date!==today||
  data.stage!=='ptp_confirmed'||data.verification!=='synthetic_fixture'||data.phone_audio_verified!==false||data.business_ready!==false)return fail();
 const c=data.candidate;
 if(!exactKeys(c,['amount_cents','due_date'])||!Number.isInteger(c.amount_cents)||c.amount_cents<1||c.amount_cents>10000||
  !validDate(c.due_date)||c.due_date<today||Date.parse(c.due_date)-Date.parse(today)>30*86400000)return fail();
 return {session_id:data.session_id,as_of_date:data.as_of_date,amount_cents:c.amount_cents,due_date:c.due_date};
}

export function canApplySyntheticPhoneDraft(draft,row,context){
 return !!draft&&draft.as_of_date===(context.today||loanShanghaiDate())&&context.connected===true&&context.operator===true&&
  context.acknowledged===true&&context.amount_cents===10000&&row?.case_id===context.caseId&&
  row?.mode==='sandbox'&&row?.state==='debt_explained';
}

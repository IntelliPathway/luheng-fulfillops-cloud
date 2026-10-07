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

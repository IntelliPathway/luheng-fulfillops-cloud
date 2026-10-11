import catalog from './sip-lab-speech-catalog.json' with {type:'json'};

export const speechProfiles=catalog;
export const speechStorageKey=tenant=>`repayguard:personal-speech:${tenant}`;
export function speechSettings(profile){
 if(!Object.hasOwn(catalog,profile))throw new Error('请选择支持的音色。');
 return {schema_version:1,usage_scope:'personal_qwen_phone_lab',speech_profile:profile};
}
export function validateSpeechSettings(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='schema_version,speech_profile,usage_scope'||value.schema_version!==1||value.usage_scope!=='personal_qwen_phone_lab'||typeof value.speech_profile!=='string'||!Object.hasOwn(catalog,value.speech_profile))throw new Error('测试音色偏好无效，请重新选择并保存。');
 return speechSettings(value.speech_profile);
}
export function readSpeechPreference(storage,tenant){
 const raw=storage.getItem(speechStorageKey(tenant));
 return raw===null?null:validateSpeechSettings(JSON.parse(raw));
}
export function saveSpeechPreference(storage,tenant,profile){
 const data=speechSettings(profile);
 storage.setItem(speechStorageKey(tenant),JSON.stringify(data));
 return data;
}
export function speechDownload(profile){
 return {filename:'sip-lab-speech.json',content:JSON.stringify(speechSettings(profile),null,2)+'\n'};
}

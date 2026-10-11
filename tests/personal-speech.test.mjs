import assert from 'node:assert/strict';
import test from 'node:test';
import {readSpeechPreference,saveSpeechPreference,speechDownload,speechProfiles,speechSettings,validateSpeechSettings} from '../src/personal-speech.js';

function storage(){const data=new Map();return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)}}

test('product preferences are tenant scoped and export only reviewed data',()=>{
 const s=storage();
 assert.equal(readSpeechPreference(s,'A'),null);
 for(const profile of Object.keys(speechProfiles)){
  saveSpeechPreference(s,'A',profile);
  assert.equal(readSpeechPreference(s,'A').speech_profile,profile);
  assert.equal(readSpeechPreference(s,'B'),null);
  const file=speechDownload(profile);
  assert.equal(file.filename,'sip-lab-speech.json');
  assert.deepEqual(validateSpeechSettings(JSON.parse(file.content)),speechSettings(profile));
  assert.deepEqual(Object.keys(JSON.parse(file.content)).sort(),['schema_version','speech_profile','usage_scope']);
 }
});

test('unknown voices, credential fields, wrong scope and malformed preferences fail',()=>{
 for(const value of [null,[],{...speechSettings('baseline'),api_key:'synthetic'},
  {...speechSettings('baseline'),usage_scope:'production'},
  {...speechSettings('baseline'),schema_version:true},
  {...speechSettings('baseline'),speech_profile:'unknown'}])assert.throws(()=>validateSpeechSettings(value));
 assert.throws(()=>saveSpeechPreference({setItem:()=>{throw Error('storage denied')}},'A','warm_female'));
 assert.throws(()=>readSpeechPreference({getItem:()=>'{malformed'},'A'));
});

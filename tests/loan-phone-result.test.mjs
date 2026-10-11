import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as state from '../src/loan-collection-state.js';

const fixture=()=>({schema_version:1,mode:'synthetic_phone_dialogue',fixture_case:'FIXTURE-LOAN-001',
 session_id:'a'.repeat(32),as_of_date:'2026-10-12',stage:'ptp_confirmed',verification:'synthetic_fixture',
 candidate:{amount_cents:6050,due_date:'2026-10-14'},phone_audio_verified:false,business_ready:false});
const parse=(data,today='2026-10-12')=>{
 assert.equal(typeof state.parseSyntheticPhoneResult,'function','phone result import is missing');
 return state.parseSyntheticPhoneResult(JSON.stringify(data),today);
};
test('confirmed synthetic result stays a draft in integer cents',()=>{
 assert.deepEqual(parse(fixture()),{session_id:'a'.repeat(32),as_of_date:'2026-10-12',amount_cents:6050,due_date:'2026-10-14'});
});
test('rejects unconfirmed, stale, real-business flags and extra fields',()=>{
 for(const change of [{stage:'paused'},{verification:'unverified'},{candidate:null},{business_ready:true},
  {phone_audio_verified:true},{as_of_date:'2026-10-11'},{api_key:'secret'},{schema_version:true}]){
  assert.throws(()=>parse({...fixture(),...change}));
 }
});
test('rejects money coercion, invalid dates and changed schema',()=>{
 for(const candidate of [{amount_cents:'5000',due_date:'2026-10-14'},
  {amount_cents:true,due_date:'2026-10-14'},{amount_cents:10001,due_date:'2026-10-14'},
  {amount_cents:5000,due_date:'2026-11-15'},{amount_cents:5000,due_date:'2026-02-30'},
  {amount_cents:5000,due_date:'2026-10-11'},{amount_cents:5000,due_date:'2026-10-14',confirmed:true}]){
  assert.throws(()=>parse({...fixture(),candidate}));
 }
});
test('draft may be applied only to the current verified 100-yuan sandbox session',()=>{
 assert.equal(typeof state.canApplySyntheticPhoneDraft,'function');
 const row={case_id:'CASE-1',mode:'sandbox',state:'debt_explained'};
 const context={caseId:'CASE-1',amount_cents:10000,connected:true,operator:true,acknowledged:true,today:'2026-10-12'};
 assert.equal(state.canApplySyntheticPhoneDraft(parse(fixture()),row,context),true);
 for(const change of [{caseId:'CASE-2'},{amount_cents:20000},{connected:false},{operator:false},{acknowledged:false},{today:'2026-10-13'}]){
  assert.equal(state.canApplySyntheticPhoneDraft(parse(fixture()),row,{...context,...change}),false);
 }
 for(const change of [{mode:'real'},{state:'identity_pending'},{state:'ptp_recorded'}]){
  assert.equal(state.canApplySyntheticPhoneDraft(parse(fixture()),{...row,...change},context),false);
 }
});

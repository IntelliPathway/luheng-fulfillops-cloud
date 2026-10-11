import {test,expect} from '@playwright/test';
import {loanShanghaiDate} from '../../src/loan-collection-state.js';

const today=loanShanghaiDate(),due=new Date(Date.parse(today)+2*86400000).toISOString().slice(0,10);
const fixture=()=>({schema_version:1,mode:'synthetic_phone_dialogue',fixture_case:'FIXTURE-LOAN-001',
 session_id:'a'.repeat(32),as_of_date:today,stage:'ptp_confirmed',verification:'synthetic_fixture',
 candidate:{amount_cents:6050,due_date:due},phone_audio_verified:false,business_ready:false});
const file=data=>({name:'synthetic-phone.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});

async function workspace(page){
 const calls=[];
 const row={id:'PHONE-SANDBOX-1',case_id:'C003',mode:'sandbox',state:'debt_explained',version:7,
  policy_version:2,authorization_expires_at:'2099-01-01T00:00:00',events:[]};
 await page.route('**/api/v1/loan-collection/**',async route=>{
  const url=route.request().url();
  if(route.request().method()==='POST'){
   calls.push(route.request().postDataJSON());row.state='ptp_recorded';row.version++;
   row.promise={status:'pending',amount_cents:6050,due_date:due,paid_cents:0};
   return route.fulfill({json:row});
  }
  if(url.endsWith('/overview'))return route.fulfill({json:{tenant_id:'TENANT_A',sessions:[row],sip_dispatches:[],execution_jobs:[]}});
  if(url.endsWith('/policy'))return route.fulfill({json:{policy:null}});
  if(url.endsWith('/profile'))return route.fulfill({json:{version:1,product:'个人无抵押贷款',amount_cents:10000,due_date:today,source_reference:'SYNTHETIC',contact_reference:'SYNTHETIC'}});
  return route.fulfill({json:{sandbox_eligible:false,blockers:['仅用于导入交互测试']}});
 });
 await page.goto('/#/loan-collection');
 await page.getByLabel('选择机催案件').selectOption('C003');
 await expect(page.getByLabel('当前应还金额（分）')).toHaveValue('10000');
 return calls;
}

test('import shows a draft and records exactly one existing sandbox event only after human confirmation',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const calls=await workspace(page);
 await page.getByLabel('导入合成电话结果（JSON）').setInputFiles(file(fixture()));
 await expect(page.getByRole('status')).toContainText('电话草案：60.50 元');
 expect(calls).toEqual([]);
 const confirm=page.getByRole('button',{name:'确认电话草案',exact:true});
 await expect(confirm).toBeDisabled();
 await page.getByRole('checkbox',{name:'我确认资料来源及本次沙箱操作；承诺不等于到账。'}).check();
 await expect(confirm).toBeEnabled();
 await page.screenshot({path:'test-results/loan-phone-draft-desktop.png',fullPage:true});
 await confirm.click();
 await expect(page.getByText('待履约 · 承诺 6050 分 / '+due+' · 核验净回款 0 分（来自现有账本，不代表已结清）')).toBeVisible();
 expect(calls).toEqual([{event_key:'phone-'+ 'a'.repeat(32)+'-PHONE-SANDBOX-1',expected_version:7,intent:'promise',
  acknowledged:true,confirmed:true,amount_cents:6050,due_date:due}]);
 await expect(page.getByText('尚未写入业务记录。',{exact:false})).toHaveCount(0);
 expect(errors).toEqual([]);
});

test('scope changes and invalid replacements clear the draft without writes and remain usable on mobile',async({page})=>{
 const calls=await workspace(page),input=page.getByLabel('导入合成电话结果（JSON）');
 await input.setInputFiles(file(fixture()));
 await expect(page.getByRole('status')).toContainText('电话草案');
 await page.getByLabel('选择机催案件').selectOption('C002');
 await expect(page.getByText('电话草案：',{exact:false})).toHaveCount(0);
 await page.getByLabel('选择机催案件').selectOption('C003');
 await input.setInputFiles(file(fixture()));
 await expect(page.getByRole('status')).toContainText('电话草案');
 await input.setInputFiles(file({...fixture(),stage:'paused',candidate:null}));
 await expect(page.getByRole('alert')).toContainText('请导入当天已明确确认的100元合成电话结果');
 await expect(page.getByText('电话草案：',{exact:false})).toHaveCount(0);
 expect(calls).toEqual([]);
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'test-results/loan-phone-draft-mobile.png',fullPage:true});
});

test('read-only member cannot import a phone draft',async({page})=>{
 await page.route('**/api/v1/auth/session',async route=>{
  const response=await route.fetch();const data=await response.json();
  await route.fulfill({response,json:{...data,role:'viewer'}});
 });
 const calls=await workspace(page);
 await expect(page.getByLabel('导入合成电话结果（JSON）')).toBeDisabled();
 expect(calls).toEqual([]);
});

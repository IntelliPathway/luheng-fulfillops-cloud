import {test,expect} from '@playwright/test';

test('pilot evidence is submitted, independently reviewed, and visible online',async({page,request})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('/');await page.getByRole('button',{name:'试点验收',exact:true}).click();
 await expect(page.getByText('后端试点预检',{exact:true})).toBeVisible();
 await page.getByLabel('脱敏记录编号').fill('BROWSER/ACCEPTANCE-001');
 await page.getByLabel('SHA-256 摘要').fill('b'.repeat(64));
 await page.getByRole('checkbox',{name:'记录已脱敏，未包含凭据或原始个人信息；提交新记录后该项目需重新复核。'}).check();
 await page.getByRole('button',{name:'提交验收证据',exact:true}).click();
 await expect(page.getByText('BROWSER/ACCEPTANCE-001',{exact:true})).toBeVisible();
 const rows=await request.get('http://127.0.0.1:8000/api/v1/pilot/evidence',{headers:{'X-Tenant-ID':'TENANT_A','X-Actor-ID':'test-user'}});
 const row=(await rows.json()).find(r=>r.evidence_reference==='BROWSER/ACCEPTANCE-001');
 expect(row).toBeTruthy();
 const decision=await request.post(`http://127.0.0.1:8000/api/v1/pilot/evidence/${row.id}/decision`,{
  headers:{'X-Tenant-ID':'TENANT_A','X-Actor-ID':'test-user'},data:{decision:'approve',expected_version:row.version,acknowledged:true}});
 expect(decision.status()).toBe(200);
 await page.getByRole('button',{name:'刷新证据',exact:true}).click();
 await expect(page.getByText('已批准',{exact:true})).toBeVisible();
 expect(errors).toEqual([]);
 await page.setViewportSize({width:390,height:844});
 const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,class:e.className,right:e.getBoundingClientRect().right})).slice(0,20));
 if(overflow.length)console.log('Mobile overflow diagnostics',JSON.stringify(overflow));
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});

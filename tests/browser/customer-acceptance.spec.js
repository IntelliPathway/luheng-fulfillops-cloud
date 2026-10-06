import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
const headers={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'Terry'};
const reviewer={...headers,'X-Actor-ID':'test-user'};
const root='http://127.0.0.1:8000/api/v1';

test('sync monitoring shows retry outcomes and clears failed refresh',async({page})=>{
 let retried=false;const row={id:'SYNC-MOCK',tenant_id:'TENANT_A',source_system:'test-system',external_event_id:'TEST-EVENT',job_id:'JOB-MOCK',status:'failed',attempt:1,max_attempts:3,error:'临时处理失败'};
 await page.route('**/api/v1/customer-sync/events',route=>route.fulfill({json:[{...row,status:retried?'queued':'failed',error:retried?null:row.error}]}));
 await page.route('**/api/v1/jobs/JOB-MOCK/retry',route=>{retried=true;return route.fulfill({status:202,json:{status:'queued'}})});
 await page.goto('/#/pilot');await page.getByRole('tab',{name:'同步事件',exact:true}).click();const panel=page.getByRole('region',{name:'业务数据同步'});
 await expect(panel.getByText('临时处理失败',{exact:true})).toBeVisible();await panel.getByRole('button',{name:'重试同步',exact:true}).click();
 await expect(panel.getByText('等待处理 · 第 1/3 次处理',{exact:true})).toBeVisible();await expect(panel.getByRole('button',{name:'重试同步',exact:true})).toHaveCount(0);
 await page.route('**/api/v1/customer-sync/events',route=>route.fulfill({status:503,json:{detail:'同步状态暂不可用'}}));await panel.getByRole('button',{name:'刷新同步'}).click();
 await expect(panel.getByRole('alert')).toHaveText('同步状态暂不可用');await expect(panel.getByText('test-system · TEST-EVENT')).toHaveCount(0);await page.unrouteAll({behavior:'wait'});
});

test('material association and case acceptance use real scoped API records',async({page,request})=>{
 await page.goto('/#/pilot');let panel=page.getByRole('region',{name:'客户材料接入'});await expect(panel.getByRole('button',{name:'刷新材料'})).toBeEnabled();await expect(panel.getByLabel('客户材料文件')).toBeEnabled();
 await panel.getByLabel('客户材料文件').setInputFiles({name:'browser-case.csv',mimeType:'text/csv',buffer:Buffer.from('case_id,payment_cents,refund_cents\nC002,0,0\n')});
 await expect(panel.getByRole('combobox',{name:'案件编号列',exact:true})).toHaveValue('case_id');await panel.getByLabel('来源记录编号').fill('BANK/BROWSER-413');
 await panel.getByRole('checkbox',{name:'确认材料已获授权并完成脱敏；金额为每个案件的历史累计汇总。'}).check();await panel.getByRole('button',{name:'留存材料并核对'}).click();
 await expect(panel.getByRole('heading',{name:'材料核对结果'})).toBeVisible();
 const materials=await (await request.get(`${root}/customer-materials`,{headers})).json();const material=materials.find(m=>m.source_reference==='BANK/BROWSER-413');expect(material).toBeTruthy();
 await page.getByRole('tab',{name:'证据关联',exact:true}).click();panel=page.getByRole('region',{name:'材料证据审核'});await expect(panel.getByRole('button',{name:'刷新关联'})).toBeEnabled();
 await panel.getByRole('combobox',{name:'选择已留存材料',exact:true}).selectOption(material.id);await panel.getByRole('button',{name:'采用案件 C002',exact:true}).click();
 await panel.getByRole('checkbox',{name:'确认仅创建材料关联提案，不修改账簿或验签结论。'}).check();const proposal=page.waitForResponse(r=>r.url().endsWith('/material-associations')&&r.request().method()==='POST');await panel.getByRole('button',{name:'提交关联提案'}).click();expect((await proposal).status()).toBe(201);
 await expect(panel.locator('article').filter({hasText:'BANK/BROWSER-413'}).getByText('C002 · 待独立复核',{exact:true})).toBeVisible();const associations=await (await request.get(`${root}/material-associations`,{headers})).json();const link=associations.find(l=>l.material_id===material.id);
 const decision=await request.post(`${root}/material-associations/${link.id}/decision`,{headers:reviewer,data:{decision:'approve',expected_version:link.version,decision_reference:'REVIEW/BROWSER-413',acknowledged:true}});expect(decision.status()).toBe(200);
 await panel.getByRole('button',{name:'刷新关联'}).click();await expect(panel.locator('article').filter({hasText:'BANK/BROWSER-413'}).getByText('C002 · 关联已复核',{exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'案例验收',exact:true}).click();panel=page.getByRole('region',{name:'客户案例验收'});await expect(panel.getByRole('button',{name:'刷新验收'})).toBeEnabled();
 await panel.getByRole('combobox',{name:'选择已复核案件关联',exact:true}).selectOption(link.id);await expect(panel.getByText('仍有验收条件待处理',{exact:true})).toBeVisible();
 await panel.getByLabel('外部验收记录编号',{exact:true}).fill('CUSTOMER/BROWSER-413');await panel.getByLabel('外部验收记录 SHA-256',{exact:true}).fill('a'.repeat(64));
 await panel.getByRole('checkbox',{name:'确认外部记录已获授权并脱敏，摘要与原件一致，待另一位管理员独立核验。'}).check();await panel.getByRole('button',{name:'提交案例验收申请'}).click();
 await expect(panel.getByText('C002 · 待独立验收',{exact:true})).toBeVisible();const pending=page.waitForEvent('download');await panel.getByRole('button',{name:'下载验收报告',exact:true}).click();const download=await pending;
 const report=JSON.parse(await readFile(await download.path(),'utf8'));expect(report.tenant_id).toBe('TENANT_A');expect(report.real_business_verified).toBe(false);expect(report.externally_attested).toBe(false);expect(report.current_evidence.ready_for_acceptance).toBe(false);
 const cross=await request.get(`${root}/case-acceptances/${report.id}/report`,{headers:{'X-Tenant-ID':'TENANT_B','X-Actor-ID':'test-viewer'}});expect(cross.status()).toBe(404);
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/customer-acceptance-desktop.png'});await page.setViewportSize({width:320,height:1000});
 await expect.poll(()=>page.locator('.sidebar').evaluate(el=>el.getBoundingClientRect().right)).toBeLessThanOrEqual(0);await panel.getByLabel('外部验收记录编号',{exact:true}).scrollIntoViewIfNeeded();
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.screenshot({path:'test-results/customer-acceptance-mobile.png'});
 await page.route('**/api/v1/case-acceptances',route=>route.fulfill({status:503,json:{detail:'验收记录暂不可用'}}));await panel.getByRole('button',{name:'刷新验收'}).click();await expect(panel.getByRole('alert')).toHaveText('验收记录暂不可用');await expect(panel.getByRole('button',{name:'下载验收报告'})).toHaveCount(0);await page.unrouteAll({behavior:'wait'});
});

test('independent administrator can review association and reject blocked acceptance',async({page,request})=>{
 const materialResponse=await request.post(`${root}/customer-materials`,{headers:reviewer,data:{filename:'independent.csv',source_reference:'BANK/REVIEW-413',file_kind:'csv',content_base64:Buffer.from('case_id,payment_cents,refund_cents\nC002,0,0\n').toString('base64'),mapping:{case_id:'case_id',payment_cents:'payment_cents',refund_cents:'refund_cents'},acknowledged:true}});expect(materialResponse.status()).toBe(201);const material=await materialResponse.json();
 const proposed=await request.post(`${root}/material-associations`,{headers:reviewer,data:{material_id:material.id,case_id:'C002',acknowledged:true}});expect(proposed.status()).toBe(201);const link=await proposed.json();
 await page.goto('/#/pilot');await page.getByRole('tab',{name:'证据关联',exact:true}).click();const panel=page.getByRole('region',{name:'材料证据审核'});
 const article=panel.locator('article').filter({hasText:'提案人 test-user'}).filter({hasText:'待独立复核'});await article.getByRole('button',{name:'独立复核关联'}).click();const dialog=page.getByRole('dialog',{name:'独立复核材料关联'});
 await dialog.getByLabel('关联复核记录编号',{exact:true}).fill('REVIEW/INDEPENDENT-413');await dialog.getByRole('checkbox',{name:'已独立核验材料归属；此决定不代表外部业务认证。'}).check();await dialog.getByRole('button',{name:'批准关联'}).click();await expect(dialog).toHaveCount(0);
 const created=await request.post(`${root}/case-acceptances`,{headers:reviewer,data:{association_id:link.id,idempotency_key:'BROWSER-INDEPENDENT-413',external_reference:'CUSTOMER/INDEPENDENT-413',external_digest:'b'.repeat(64),acknowledged:true}});expect(created.status()).toBe(201);
 await page.getByRole('tab',{name:'案例验收',exact:true}).click();const acceptance=page.getByRole('region',{name:'客户案例验收'});const row=acceptance.locator('article').filter({hasText:'CUSTOMER/INDEPENDENT-413'});await row.getByRole('button',{name:'独立复核验收'}).click();const review=page.getByRole('dialog',{name:'独立复核案例验收'});
 await review.getByLabel('案例验收复核记录编号',{exact:true}).fill('DECISION/INDEPENDENT-413');await review.getByRole('checkbox',{name:'已独立核验外部记录与摘要，此记录为人工验收声明，不授权业务执行。'}).check();await expect(review.getByRole('button',{name:'确认验收通过'})).toBeDisabled();await review.getByRole('button',{name:'驳回验收'}).click();await expect(review).toHaveCount(0);await expect(row.getByText('C002 · 验收已驳回',{exact:true})).toBeVisible();
});

import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test.afterEach(async({page})=>{
 await page.unrouteAll({behavior:'wait'});
});

test('connection diagnostics export scoped evidence and clear failed refresh results',async({page})=>{
 await page.goto('/#/pilot');const panel=page.getByRole('region',{name:'生产连接诊断'});
 await expect(panel.getByText('当前企业身份',{exact:true})).toBeVisible();
 await expect(panel.getByText('技术接入尚未就绪，请处理下方检查项。')).toBeVisible();
 const pending=page.waitForEvent('download');await panel.getByRole('button',{name:'下载接入报告'}).click();
 const download=await pending;expect(download.suggestedFilename()).toBe('connection-readiness-report.json');
 const report=JSON.parse(await readFile(await download.path(),'utf8'));
 expect(report.backend.tenant_id).toBe('TENANT_A');expect(report.technical_status).toBe('blocked');
 expect(report.real_business_verified).toBe(false);expect(report.enables_external_execution).toBe(false);
 await page.route('**/api/v1/pilot/connection-readiness*',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"诊断暂不可用"}'}));
 await panel.getByRole('button',{name:'重新检查连接'}).click();
 await expect(panel.getByRole('alert')).toHaveText('诊断暂不可用');
 await expect(panel.getByRole('button',{name:'下载接入报告'})).toHaveCount(0);
 await page.unroute('**/api/v1/pilot/connection-readiness*');await panel.getByRole('button',{name:'重新检查连接'}).click();
 await expect(panel.getByRole('button',{name:'下载接入报告'})).toBeVisible();
 await page.setViewportSize({width:320,height:1000});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'test-results/connection-readiness-mobile.png'});
});

test('backend ready response cannot turn an unconfigured demo Site into production readiness',async({page})=>{
 await page.route('**/api/v1/pilot/connection-readiness*',async route=>{
  const response=await route.fetch();const report=await response.json();
  await route.fulfill({response,json:{...report,status:'ready_for_pilot',checks:report.checks.map(c=>({...c,passed:true}))}});
 });
 await page.goto('/#/pilot');const panel=page.getByRole('region',{name:'生产连接诊断'});
 await expect(panel.getByText('技术接入尚未就绪，请处理下方检查项。')).toBeVisible();
 await expect(panel.getByText('待配置',{exact:true})).toBeVisible();
 await expect(panel.getByRole('button',{name:'下载接入报告'})).toBeVisible();
});

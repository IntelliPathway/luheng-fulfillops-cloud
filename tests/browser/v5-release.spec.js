import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
const routes=['overview','control','pilot','activities','assets','cases','plans','payments','agents','exceptions','logs','strategy','integrations','usage','settings'];

test('v5 current integration export rechecks server evidence and clears on failure',async({page})=>{
 await page.goto('/#/pilot');const panel=page.getByRole('region',{name:'v5.0 集成验收'});await expect(panel).toContainText('当前集成验收仍有阻断');
 const downloadPromise=page.waitForEvent('download');await panel.getByRole('button',{name:'下载当前集成报告'}).click();const file=await downloadPromise;const report=JSON.parse(await readFile(await file.path(),'utf8'));expect(report.app_version).toBe('5.0.0');expect(report.tenant_id).toBe('TENANT_A');expect(report.status).toBe('blocked');expect(report.enables_external_execution).toBe(false);
 await page.route('**/pilot/integration-acceptance?*',r=>r.fulfill({status:503,json:{detail:'INTEGRATION_UNAVAILABLE'}}));await panel.getByRole('button',{name:'重新核验集成'}).click();await expect(panel.getByRole('alert')).toContainText('INTEGRATION_UNAVAILABLE');await expect(panel.getByRole('button',{name:'下载当前集成报告'})).toBeDisabled();await expect(panel.getByText('当前集成验收仍有阻断',{exact:true})).toHaveCount(0);
});

for(const width of [1440,1024,768,390,320])test(`packaged Sites Worker renders all release surfaces at ${width}px`,async({page,request})=>{
 test.setTimeout(90000);const origin='http://127.0.0.1:5180';const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width,height:1000});
 const runtime=await request.get(origin+'/runtime-config.json');expect(await runtime.json()).toEqual({schema_version:1,mode:'demo'});const api=await request.get(origin+'/api/v1/health');expect(api.status()).toBe(503);expect((await api.json()).mode).toBe('sites-demo');
 for(const route of routes){const response=await page.goto(`${origin}/#/${route}`);expect(response.headers()['content-security-policy']).toContain("script-src 'self'");expect(response.headers()['x-content-type-options']).toBe('nosniff');await expect(page.getByRole('heading',{level:1})).toBeVisible();await expect(page.locator('.main-content')).toBeVisible();expect(await page.locator('img').evaluateAll(rows=>rows.every(i=>i.complete&&i.naturalWidth>0))).toBe(true);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true)}
 await page.goto(origin+'/#/control');await page.getByLabel('指挥台指令').fill('查询 C001 当前状态');await page.getByRole('button',{name:'发送指令',exact:true}).click();await expect(page.locator('.assistant-answer')).toContainText('C001');await page.screenshot({animations:'disabled',path:`test-results/v5-packaged-${width}-control.png`});
 await page.goto(origin+'/#/pilot');const panel=page.getByRole('region',{name:'v5.0 集成验收'});await expect(panel).toContainText('业务 API 尚未接入');await expect(panel.getByRole('button',{name:'下载当前集成报告'})).toBeDisabled();await page.screenshot({animations:'disabled',path:`test-results/v5-packaged-${width}-acceptance.png`});
 if(width<=390){await page.getByRole('button',{name:'AI 查询',exact:true}).click();await expect(page.getByLabel('AI 查询指令')).toBeVisible();await expect(page.locator('.ai-copilot-launcher')).toBeHidden()}
 expect(errors).toEqual([]);
});

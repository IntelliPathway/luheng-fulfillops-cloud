import {test,expect} from '@playwright/test';

const cards=page=>[page.locator('.recovery-chart-card .metric-value-row strong'),page.locator('.commission-card .metric-value-row strong')];

test('failed payment overview stays unavailable while the business API remains connected',async({page})=>{
 await page.route('**/api/v1/payments/overview',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'financial overview unavailable'})}));
 await page.goto('/');await expect(page.locator('.server-connected')).toBeVisible();
 for(const card of cards(page))await expect(card).toHaveText('—');
 await expect(page.getByRole('status').filter({hasText:'财务摘要尚未读取'})).toBeVisible();
 await expect(page.locator('.sync-label')).toHaveText('财务摘要暂不可用');
 await expect(page.locator('.recovery-chart-card .metric-value-row strong')).toHaveText('—');
 await expect(page.getByRole('img',{name:'近 30 天服务端净回款趋势，UTC，单位元'})).toBeVisible();
 await expect(page.locator('.commission-card')).not.toContainText('¥0');
 await page.screenshot({path:'test-results/payment-summary-unavailable.png'});
});

test('authoritative zero recovery and commission remain real zeros',async({page})=>{
 await page.route('**/api/v1/payments/overview',async route=>{
  const response=await route.fetch();const payload=await response.json();
  payload.summary={...payload.summary,confirmed_net_recovery_cents:0,accrued_commission_cents:0};
  await route.fulfill({response,json:payload});
 });
 await page.goto('/');await expect(page.locator('.server-connected')).toBeVisible();
 for(const card of cards(page))await expect(card).toHaveText('¥0');
 await expect(page.locator('.sync-label')).toHaveText('服务端账簿 · 全部期间');
 await expect(page.getByRole('status').filter({hasText:'财务摘要尚未读取'})).toHaveCount(0);
});

test('available payment summary displays the exact authoritative amounts',async({page,request})=>{
 const result=await request.get('http://127.0.0.1:8000/api/v1/payments/overview',{headers:{'X-Tenant-ID':'TENANT_A','X-Actor-ID':'Terry'}});
 expect(result.status()).toBe(200);const {summary}=await result.json();
 const money=cents=>'¥'+(cents/100).toLocaleString('zh-CN',{maximumFractionDigits:2});
 await page.goto('/');await expect(page.locator('.server-connected')).toBeVisible();
 const [recovery,commission]=cards(page);
 await expect(recovery).toHaveText(money(summary.confirmed_net_recovery_cents));
 await expect(commission).toHaveText(money(summary.accrued_commission_cents));
});

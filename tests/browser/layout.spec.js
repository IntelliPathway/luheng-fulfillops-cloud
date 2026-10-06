import {test,expect} from '@playwright/test';
const routes=['overview','control','pilot','activities','assets','cases','plans','payments','agents','exceptions','logs','strategy','integrations','usage','settings'];
for(const width of [1440,1024,768,390,320]){
 test(`all working surfaces fit ${width}px and load brand image`,async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width,height:1000});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const route of routes){
   await page.goto(`/#/${route}`);await expect(page.locator('.main-content')).toBeVisible();await expect(page.locator('.server-connected')).toBeVisible();
   await expect(page.getByRole('heading',{level:1})).toBeVisible();
   const overflow=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,items:[...document.querySelectorAll('.main-content *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest('.table-scroll')).map(e=>({class:e.className,right:e.getBoundingClientRect().right})).slice(0,15)}));if(overflow.scroll>overflow.width+1)console.log('Layout overflow',route,width,overflow);expect.soft(overflow.scroll,`${route} at ${width}px`).toBeLessThanOrEqual(overflow.width+1);
   expect(await page.locator('img').evaluateAll(images=>images.every(i=>i.complete&&i.naturalWidth>0))).toBe(true);
   await page.screenshot({path:`test-results/layout-${width}-${route}.png`});
  }
  await page.goto('/#/cases');await expect(page.locator('.server-connected')).toBeVisible();await page.getByRole('button',{name:'查看案件 C002',exact:true}).click();const dialog=page.getByRole('dialog');await expect(dialog).toBeVisible();await expect.poll(async()=>{const box=await dialog.boundingBox();return box.x>=0&&box.x+box.width<=width+1;},{message:"drawer settles inside viewport after opening animation"}).toBe(true);await expect(page.locator('.ai-copilot-launcher')).toBeHidden();await page.screenshot({path:`test-results/dialog-${width}.png`});await page.getByRole('button',{name:'关闭弹窗',exact:true}).press('Escape');await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
 });
}
test('connected dashboard never displays fabricated performance and tabs work by keyboard',async({page})=>{
 await page.goto('/');await expect(page.locator('.server-connected')).toContainText('API');
 await expect(page.getByRole('img',{name:'近 30 天服务端净回款趋势，UTC，单位元'})).toBeVisible();
 await expect(page.getByText('92.4%',{exact:true})).toHaveCount(0);
 await page.goto('/#/cases');const first=page.getByRole('tab').first();await first.focus();await first.press('ArrowRight');await expect(page.getByRole('tab').nth(1)).toHaveAttribute('aria-selected','true');
});

test('independently imported case is inspected in the browser without inventing real outcomes',async({page,request})=>{
 const actor={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'test-user'};
 const csv='package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status\nPKG_BROWSER_VERIFY,浏览器脱敏测试,VERIFY_BROWSER,1200000,2026-01-01,2027-12-31,COM_BROWSER_VERIFY,1500,CONSENT-BROWSER,待联系';
 const preview=await request.post('http://127.0.0.1:8000/api/v1/asset-imports/previews',{headers:actor,data:{filename:'cases.csv',csv_text:csv,idempotency_key:'browser-verify-source'}});expect(preview.status()).toBe(201);const batch=await preview.json();
 const commit=await request.post(`http://127.0.0.1:8000/api/v1/asset-imports/${batch.id}/commit`,{headers:{...actor,'X-Actor-ID':'Terry'},data:{expected_version:1,review_note:'独立核对脱敏测试来源和委托金额',acknowledged:true}});expect(commit.status()).toBe(200);
 await page.goto('/#/pilot');await expect(page.locator('.server-connected')).toBeVisible();await page.getByPlaceholder('输入已导入的案件编号').fill('VERIFY_BROWSER');await page.getByRole('button',{name:'核对案例证据',exact:true}).click();await expect(page.getByText(batch.source_digest,{exact:true})).toBeVisible();await expect(page.getByText('证据尚不完整',{exact:false})).toBeVisible();await expect(page.getByText('每条账簿记录必须关联验签且已匹配入账的回执',{exact:false})).toBeVisible();await page.screenshot({path:'test-results/imported-case-validation.png'});
});

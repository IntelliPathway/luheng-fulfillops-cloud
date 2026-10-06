import {test,expect} from '@playwright/test';

const material={id:'MAT-TEST',tenant_id:'TENANT_A',filename:'customer.csv',source_reference:'BANK/TEST-001',source_digest:'a'.repeat(64),file_kind:'csv',size_bytes:60,case_count:1};
const report={tenant_id:'TENANT_A',material,scope:'all_time_case_totals',status:'compared',results:[{case_id:'C002',status:'mismatch',payment_difference_cents:-2000,refund_difference_cents:-2000}],real_business_verified:false,enables_external_execution:false,externally_attested:false};
test('customer intake maps CSV and preserves evidence boundaries on desktop and mobile',async({page})=>{
 let uploads=0;await page.route('**/api/v1/customer-materials',async route=>{
  if(route.request().method()==='GET')return route.fulfill({json:uploads?[material]:[]});
  const p=route.request().postDataJSON();expect(p.mapping).toEqual({case_id:'客户案件',payment_cents:'累计付款',refund_cents:'累计退款'});expect(p.acknowledged).toBe(true);expect(p.source_reference).toBe('BANK/TEST-001');uploads++;return route.fulfill({status:201,json:material});
 });
 await page.route('**/api/v1/customer-materials/MAT-TEST/report',route=>route.fulfill({json:report}));
 await page.goto('/#/pilot');const panel=page.getByRole('region',{name:'客户材料接入'});
 await expect(panel.getByRole('button',{name:'刷新材料'})).toBeEnabled();await expect(panel.getByLabel('客户材料文件')).toBeEnabled();
 await panel.getByLabel('客户材料文件').setInputFiles({name:'customer.csv',mimeType:'text/csv',buffer:Buffer.from('客户案件,累计付款,累计退款\nC002,12000,5000\n')});
 for(const [label,value] of [['案件编号列','客户案件'],['付款金额列（分）','累计付款'],['退款金额列（分）','累计退款']])await panel.getByRole('combobox',{name:label,exact:true}).selectOption(value);
 await panel.getByLabel('来源记录编号').fill('BANK/TEST-001');
 await panel.getByLabel('确认材料已获授权并完成脱敏；金额为每个案件的历史累计汇总。').check();
 await panel.getByRole('button',{name:'留存材料并核对'}).click();
 await expect(panel.getByRole('cell',{name:'金额有差异'})).toBeVisible();
 await expect(panel.getByRole('cell',{name:'-2000',exact:true})).toHaveCount(2);
 await expect(panel.getByRole('button',{name:'下载材料核对报告'})).toBeVisible();
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/customer-materials-desktop.png'});
 await page.setViewportSize({width:320,height:1000});await panel.scrollIntoViewIfNeeded();
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await panel.getByLabel('来源记录编号').scrollIntoViewIfNeeded();await expect(panel.getByLabel('来源记录编号')).toBeInViewport();
 await page.screenshot({path:'test-results/customer-materials-mobile.png'});
 await page.route('**/api/v1/customer-materials/MAT-TEST/report',route=>route.fulfill({status:503,json:{detail:'材料完整性校验失败'}}));
 await panel.getByRole('button',{name:'重新核对',exact:true}).click();
 await expect(panel.getByRole('alert')).toHaveText('材料完整性校验失败');
 await expect(panel.getByRole('button',{name:'下载材料核对报告'})).toHaveCount(0);
 await page.route('**/api/v1/customer-materials/MAT-TEST/report',route=>route.fulfill({json:{...report,tenant_id:'TENANT_B'}}));
 await panel.getByRole('button',{name:'重新核对',exact:true}).click();
 await expect(panel.getByRole('alert')).toHaveText('材料核对范围不匹配');
 await expect(panel.getByRole('cell',{name:'金额有差异'})).toHaveCount(0);
 await panel.getByLabel('客户材料文件').setInputFiles({name:'too-large.csv',mimeType:'text/csv',buffer:Buffer.alloc(1000001)});
 await expect(panel.getByRole('alert')).toHaveText('请选择不超过 1 MB 的文件。');
 await page.unrouteAll({behavior:'wait'});
});

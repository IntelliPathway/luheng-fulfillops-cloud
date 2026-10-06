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

test('case comparison rejects stale exports and clears failed evidence',async({page})=>{
 await page.goto('/#/pilot');const panel=page.getByRole('region',{name:'真实案例证据核对'});
 await panel.getByPlaceholder('输入已导入的案件编号').fill('C002');
 await panel.getByLabel('外部凭证净额（分，可选）').fill('0');
 await panel.getByRole('button',{name:'核对案例证据',exact:true}).click();
 await expect(panel.getByText('证据链不完整，暂不能比对。手工录入金额不能替代外部凭证或真实业务验收。',{exact:true})).toBeVisible();
 const pending=page.waitForEvent('download');await panel.getByRole('button',{name:'下载报告',exact:true}).click();
 const report=JSON.parse(await readFile(await (await pending).path(),'utf8'));
 expect(report.tenant_id).toBe('TENANT_A');expect(report.external_comparison.expected_net_recovery_cents).toBe(0);
 expect(report.external_comparison.status).toBe('unavailable');expect(report.external_comparison.difference_cents).toBeNull();
 expect(report.real_business_verified).toBe(false);
 await panel.getByLabel('外部凭证净额（分，可选）').fill('7000');
 await expect(panel.getByRole('button',{name:'下载报告',exact:true})).toHaveCount(0);
 await page.route('**/api/v1/pilot/case-validation*',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"凭证核对暂不可用"}'}));
 await panel.getByRole('button',{name:'核对案例证据',exact:true}).click();
 await expect(panel.getByRole('alert')).toHaveText('凭证核对暂不可用');
 await expect(panel.getByRole('button',{name:'下载报告',exact:true})).toHaveCount(0);
 await page.setViewportSize({width:320,height:1000});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'test-results/case-comparison-mobile.png'});
});

test('server ledger trend switches periods and fails without demo fallback',async({page})=>{
 await page.goto('/');
 await expect(page.getByRole('img',{name:'近 30 天服务端净回款趋势，UTC，单位元'})).toBeVisible();
 await page.locator('.recovery-chart-card').getByRole('button',{name:'周',exact:true}).click();
 await expect(page.getByRole('img',{name:'近 7 天服务端净回款趋势，UTC，单位元'})).toBeVisible();
 await page.route('**/api/v1/pilot/recovery-trend*',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"趋势暂不可用"}'}));
 await page.locator('.recovery-chart-card').getByRole('button',{name:'季',exact:true}).click();
 await expect(page.getByRole('alert').getByText('趋势暂不可用',{exact:true})).toBeVisible();
 await expect(page.getByRole('img',{name:/服务端净回款趋势/})).toHaveCount(0);
 await expect(page.getByRole('img',{name:/^近.*确认净回款趋势$/})).toHaveCount(0);
 await page.unrouteAll({behavior:'wait'});await page.getByRole('button',{name:'重试趋势',exact:true}).click();
 await expect(page.getByRole('img',{name:'近 90 天服务端净回款趋势，UTC，单位元'})).toBeVisible();
});

test('daily ledger details paginate, clear errors and remain usable on mobile',async({page})=>{
 await page.route('**/api/v1/pilot/recovery-day*',route=>{
  const params=new URL(route.request().url()).searchParams;const current=Number(params.get('page'));
  const items=current===1?Array.from({length:20},(_,i)=>({entry_id:`TEST-${i}`,case_id:`C${i}`,event_type:'PAYMENT',amount_cents:500,commission_cents:75,receipt_id:`RECEIPT-${i}`,evidence_status:'linked'})):[{entry_id:'TEST-REFUND',case_id:'C901',event_type:'REFUND',amount_cents:-3000,commission_cents:-450,receipt_id:null,evidence_status:'incomplete'}];
  return route.fulfill({json:{tenant_id:'TENANT_A',date:params.get('day'),page:current,page_size:20,total:21,filtered_total:21,evidence_filter:params.get('evidence_status'),net_recovery_cents:7000,accrued_commission_cents:1050,payment_total_cents:10000,refund_total_cents:3000,items}});
 });
 await page.goto('/');await expect(page.getByRole('button',{name:'查看当日明细',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'查看当日明细',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'每日回款明细'});
 await expect(dialog.getByText('当日净回款 ¥70',{exact:true})).toBeVisible();
 await expect(dialog.getByRole('button',{name:'上一页'})).toBeDisabled();
 await dialog.getByRole('button',{name:'下一页'}).click();
 await expect(dialog.getByText('TEST-REFUND',{exact:true})).toBeVisible();
 await expect(dialog.getByRole('cell',{name:/^证据待补全/})).toBeVisible();
 await expect(dialog.getByText('当日净回款 ¥70',{exact:true})).toBeVisible();
 await expect(dialog.getByRole('button',{name:'下一页'})).toBeDisabled();
 await page.setViewportSize({width:320,height:1000});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await expect(dialog.getByText('左右滑动表格，查看金额与回执证据。',{exact:true})).toBeVisible();
 const table=dialog.getByRole('region',{name:'每日账簿记录'});await table.evaluate(el=>{el.scrollLeft=el.scrollWidth});
 await expect(dialog.getByRole('cell',{name:/^证据待补全/})).toBeInViewport();
 await page.screenshot({path:'test-results/recovery-day-mobile.png'});
 await dialog.getByRole('button',{name:'关闭弹窗'}).click();
 await page.route('**/api/v1/pilot/recovery-day*',route=>route.fulfill({status:503,json:{detail:'明细读取失败'}}));
 await page.getByRole('button',{name:'查看当日明细',exact:true}).click();
 await expect(dialog.getByRole('alert')).toBeVisible();
 await expect(dialog.getByText('TEST-REFUND',{exact:true})).toHaveCount(0);
 await page.unrouteAll({behavior:'wait'});await dialog.getByRole('button',{name:'重试明细'}).click();
 await expect(dialog.getByText('当日暂无账簿记录',{exact:true})).toBeVisible();
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 await expect(page.getByRole('button',{name:'查看当日明细',exact:true})).toBeFocused();
});

test('daily comparison detects separate totals and invalidates edited exports',async({page})=>{
 await page.route('**/api/v1/pilot/recovery-day*',route=>{
  const p=new URL(route.request().url()).searchParams;const supplied=p.has('expected_payment_cents');
  const payment=supplied?Number(p.get('expected_payment_cents')):null,refund=supplied?Number(p.get('expected_refund_cents')):null;
  return route.fulfill({json:{tenant_id:'TENANT_A',date:p.get('day'),page:Number(p.get('page')),page_size:20,total:2,filtered_total:2,evidence_filter:p.get('evidence_status'),net_recovery_cents:7000,accrued_commission_cents:1050,payment_total_cents:10000,refund_total_cents:3000,linked_receipt_count:2,real_business_verified:false,enables_external_execution:false,items:[{entry_id:'COMPARE-PAY',case_id:'C901',event_type:'PAYMENT',amount_cents:10000,commission_cents:1500,receipt_id:'COMPARE-RECEIPT',evidence_status:'linked'},{entry_id:'COMPARE-REFUND',case_id:'C901',event_type:'REFUND',amount_cents:-3000,commission_cents:-450,receipt_id:'COMPARE-REFUND-RECEIPT',evidence_status:'linked'}],external_comparison:{status:!supplied?'not_provided':payment===10000&&refund===3000?'matched':'mismatch',expected_payment_cents:payment,expected_refund_cents:refund,payment_difference_cents:supplied?10000-payment:null,refund_difference_cents:supplied?3000-refund:null,evidence_kind:'operator_entered_amounts_only',externally_attested:false}}});
 });
 await page.goto('/');await page.getByRole('button',{name:'查看当日明细',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'每日回款明细'});
 await dialog.getByLabel('外部付款金额（分）').fill('11000');await dialog.getByLabel('外部退款金额（分）').fill('4000');
 await dialog.getByRole('button',{name:'核对当日金额'}).click();
 await expect(dialog.getByText('付款或退款金额存在差异',{exact:true})).toBeVisible();
 const pending=page.waitForEvent('download');await dialog.getByRole('button',{name:'下载核对摘要'}).click();
 const report=JSON.parse(await readFile(await (await pending).path(),'utf8'));
 expect(report.external_comparison.payment_difference_cents).toBe(-1000);expect(report.external_comparison.refund_difference_cents).toBe(-1000);
 expect(report.real_business_verified).toBe(false);expect(report.external_comparison.externally_attested).toBe(false);
 expect(report.includes_ledger_items).toBe(false);expect(report).not.toHaveProperty('items');
 await dialog.getByLabel('外部付款金额（分）').fill('10000');await expect(dialog.getByRole('button',{name:'下载核对摘要'})).toHaveCount(0);
 await dialog.getByLabel('外部付款金额（分）').fill('11000');await expect(dialog.getByRole('button',{name:'下载核对摘要'})).toHaveCount(0);
 await dialog.getByLabel('外部付款金额（分）').fill('1.5');await dialog.getByRole('button',{name:'核对当日金额'}).click();await expect(dialog.getByRole('alert')).toBeVisible();
 await dialog.getByLabel('外部付款金额（分）').fill('10000');await dialog.getByLabel('外部退款金额（分）').fill('3000');await dialog.getByRole('button',{name:'核对当日金额'}).click();
 await expect(dialog.getByText('付款与退款金额均一致',{exact:true})).toBeVisible();
 await page.setViewportSize({width:320,height:1000});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await dialog.getByRole('button',{name:'下载核对摘要'}).scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/daily-comparison-mobile.png'});
 await page.route('**/api/v1/pilot/recovery-day*',route=>route.fulfill({status:503,json:{detail:'核对读取失败'}}));
 await dialog.getByRole('button',{name:'核对当日金额'}).click();await expect(dialog.getByRole('alert')).toBeVisible();
 await expect(dialog.getByRole('button',{name:'下载核对摘要'})).toHaveCount(0);
});

test('evidence filters reset pagination and preserve whole-day unavailable comparison',async({page})=>{
 const requested=[];let holdLinked=false,linkedDone=false,releaseLinked;const linkedLatch=new Promise(resolve=>{releaseLinked=resolve});
 await page.route('**/api/v1/pilot/recovery-day*',async route=>{
  const p=new URL(route.request().url()).searchParams;const filter=p.get('evidence_status'),current=Number(p.get('page'));requested.push({filter,page:current});
  const good=Array.from({length:20},(_,i)=>({entry_id:`GOOD-${i}`,case_id:`C${i}`,event_type:'PAYMENT',amount_cents:500,commission_cents:75,receipt_id:`RECEIPT-${i}`,evidence_status:'linked',evidence_gap:null}));
  const bad={entry_id:'BAD-SIGNATURE',case_id:'C901',event_type:'REFUND',amount_cents:-3000,commission_cents:-450,receipt_id:'FAILED-RECEIPT',evidence_status:'incomplete',evidence_gap:'unverified_signature'};
  if(filter==='linked'&&holdLinked)await linkedLatch;
  const supplied=p.has('expected_payment_cents');
  await route.fulfill({json:{tenant_id:'TENANT_A',date:p.get('day'),page:current,page_size:20,total:21,filtered_total:filter==='all'?21:filter==='linked'?20:1,evidence_filter:filter,net_recovery_cents:7000,payment_total_cents:10000,refund_total_cents:3000,accrued_commission_cents:1050,linked_receipt_count:20,items:filter==='incomplete'||filter==='all'&&current===2?[bad]:good,external_comparison:{status:supplied?'unavailable':'not_provided',expected_payment_cents:supplied?Number(p.get('expected_payment_cents')):null,expected_refund_cents:supplied?Number(p.get('expected_refund_cents')):null,payment_difference_cents:null,refund_difference_cents:null,externally_attested:false}}});
  if(filter==='linked'&&holdLinked)linkedDone=true;
 });
 await page.goto('/');await page.getByRole('button',{name:'查看当日明细',exact:true}).click();const dialog=page.getByRole('dialog',{name:'每日回款明细'});
 await dialog.getByRole('button',{name:'下一页'}).click();await expect(dialog.getByText('BAD-SIGNATURE',{exact:true})).toBeVisible();
 await dialog.getByLabel('回执证据筛选').selectOption('incomplete');
 await expect(dialog.getByText('符合筛选 1 条 / 当日 21 条',{exact:true})).toBeVisible();expect(requested.at(-1)).toEqual({filter:'incomplete',page:1});
 await expect(dialog.getByText('回执验签未通过',{exact:true})).toBeVisible();await expect(dialog.getByText('当日净回款 ¥70',{exact:true})).toBeVisible();
 await expect(dialog.getByRole('button',{name:'下一页'})).toHaveCount(0);
 await dialog.getByLabel('外部付款金额（分）').fill('10000');await dialog.getByLabel('外部退款金额（分）').fill('3000');await dialog.getByRole('button',{name:'核对当日金额'}).click();
 await expect(dialog.getByText('当日回执证据不完整，暂不能比对。',{exact:true})).toBeVisible();
 await page.screenshot({path:'test-results/evidence-gap-desktop.png'});
 await page.setViewportSize({width:320,height:1000});const gapTable=dialog.getByRole('region',{name:'每日账簿记录'});await gapTable.evaluate(el=>{el.scrollLeft=el.scrollWidth});await dialog.getByText('回执验签未通过',{exact:true}).scrollIntoViewIfNeeded();await expect(dialog.getByText('回执验签未通过',{exact:true})).toBeInViewport();await page.screenshot({path:'test-results/evidence-gap-mobile.png'});await page.setViewportSize({width:1440,height:1000});
 await dialog.getByLabel('回执证据筛选').selectOption('linked');await expect(dialog.getByText('符合筛选 20 条 / 当日 21 条',{exact:true})).toBeVisible();
 await expect(dialog.getByText('BAD-SIGNATURE',{exact:true})).toHaveCount(0);await expect(dialog.getByText('当日回执证据不完整，暂不能比对。',{exact:true})).toBeVisible();
 await page.setViewportSize({width:320,height:1000});await dialog.getByLabel('回执证据筛选').scrollIntoViewIfNeeded();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'test-results/evidence-filter-mobile.png'});
 await dialog.getByLabel('回执证据筛选').selectOption('all');await expect(dialog.getByText('符合筛选 21 条 / 当日 21 条',{exact:true})).toBeVisible();
 holdLinked=true;try{await dialog.getByLabel('回执证据筛选').selectOption('linked');await expect.poll(()=>requested.at(-1).filter).toBe('linked');
 await dialog.getByLabel('回执证据筛选').selectOption('all');await expect(dialog.getByText('符合筛选 21 条 / 当日 21 条',{exact:true})).toBeVisible()}finally{releaseLinked()}
 await expect.poll(()=>linkedDone).toBe(true);await expect(dialog.getByText('符合筛选 21 条 / 当日 21 条',{exact:true})).toBeVisible();await expect(dialog.getByLabel('回执证据筛选')).toHaveValue('all');
 await page.route('**/api/v1/pilot/recovery-day*',route=>route.fulfill({status:503,json:{detail:'筛选读取失败'}}));
 await dialog.getByLabel('回执证据筛选').selectOption('incomplete');await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog.getByRole('button',{name:'下载核对摘要'})).toHaveCount(0);
 await expect(dialog.getByRole('region',{name:'每日账簿记录'})).toHaveCount(0);
 await page.route('**/api/v1/pilot/recovery-day*',route=>route.fulfill({json:{tenant_id:'TENANT_A',date:new URL(route.request().url()).searchParams.get('day'),page:1,page_size:20,total:21,filtered_total:21,evidence_filter:'all',items:[]}}));
 await dialog.getByRole('button',{name:'重试明细'}).click();await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog.getByRole('region',{name:'每日账簿记录'})).toHaveCount(0);
});

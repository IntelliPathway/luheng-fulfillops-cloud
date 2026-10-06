import {test,expect} from '@playwright/test';
const headers={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'Terry'};
const root='http://127.0.0.1:8000/api/v1';

test('connector configuration, test, enable and sync states remain scoped',async({page})=>{
 let rows=[];let unavailable=false;
 await page.route('**/api/v1/customer-connectors**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(req.method()==='GET')return route.fulfill(unavailable?{status:503,json:{detail:'连接器读取失败'}}:{json:rows});
  if(path.endsWith('/customer-connectors')){const body=req.postDataJSON();expect(body.credential).toBe('synthetic-browser-token');rows=[{id:'CONN-BROWSER',tenant_id:'TENANT_A',name:body.name,endpoint:body.endpoint,mapping:body.mapping,interval_minutes:body.interval_minutes,version:1,enabled:false,test_current:false,job:null}];}
  else if(path.endsWith('/test'))rows[0]={...rows[0],test_current:true};
  else if(path.endsWith('/enable'))rows[0]={...rows[0],enabled:true};
  else if(path.endsWith('/sync'))rows[0]={...rows[0],job:{id:'JOB-BROWSER',status:'queued',attempt:0,max_attempts:3}};
  return route.fulfill({status:req.method()==='POST'&&path.endsWith('/customer-connectors')?201:200,json:rows[0]});
 });
 await page.goto('/#/pilot');await page.getByRole('tab',{name:'业务连接器',exact:true}).click();const panel=page.getByRole('region',{name:'客户业务连接器'});
 await expect(panel.getByLabel('连接器名称',{exact:true})).toBeEnabled();await panel.getByLabel('连接器名称',{exact:true}).fill('合成客户连接器');await panel.getByLabel('客户 HTTPS 接口',{exact:true}).fill('https://customer.example/events');await panel.getByLabel('API 凭证',{exact:true}).fill('synthetic-browser-token');await panel.getByRole('checkbox',{name:'客户已授权此数据源，拉取内容已脱敏，允许按当前间隔同步。'}).check();await panel.getByRole('button',{name:'保存连接器',exact:true}).click();
 await expect(panel.getByRole('button',{name:'启用同步',exact:true})).toBeDisabled();await expect(panel.getByLabel('API 凭证',{exact:true})).toHaveValue('');await panel.getByRole('button',{name:'连接测试',exact:true}).click();await expect(panel.getByRole('button',{name:'启用同步',exact:true})).toBeEnabled();await panel.getByRole('button',{name:'启用同步',exact:true}).click();await panel.getByRole('button',{name:'立即同步',exact:true}).click();await expect(panel.getByText('同步：等待处理 · 0 个已接入事件',{exact:true})).toBeVisible();
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/customer-connectors-desktop.png'});await page.setViewportSize({width:320,height:1000});await panel.getByLabel('连接器名称',{exact:true}).scrollIntoViewIfNeeded();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.screenshot({path:'test-results/customer-connectors-mobile.png'});
 unavailable=true;await panel.getByRole('button',{name:'刷新连接器'}).click();await expect(panel.getByRole('alert')).toHaveText('连接器读取失败');await expect(panel.getByText('合成客户连接器 · v1',{exact:true})).toHaveCount(0);await page.unrouteAll({behavior:'wait'});
});

test('immutable material versions and evidence task journey use actual API',async({page,request})=>{
 const payload={filename:'version-root.csv',source_reference:'BANK/VERSION-415',file_kind:'csv',content_base64:Buffer.from('case_id,payment_cents,refund_cents\nC002,0,0\n').toString('base64'),mapping:{case_id:'case_id',payment_cents:'payment_cents',refund_cents:'refund_cents'},acknowledged:true};
 const created=await request.post(`${root}/customer-materials`,{headers,data:payload});expect(created.status()).toBe(201);const material=await created.json();
 await page.goto('/#/pilot');const intake=page.getByRole('region',{name:'客户材料接入'});const row=intake.locator('article').filter({hasText:'BANK/VERSION-415'});await row.getByRole('button',{name:'版本历史',exact:true}).click();await intake.getByRole('button',{name:'追加当前材料新版本',exact:true}).click();
 await intake.getByLabel('客户材料文件').setInputFiles({name:'version-two.csv',mimeType:'text/csv',buffer:Buffer.from('case_id,payment_cents,refund_cents\nC002,100,0\n')});await expect(intake.getByRole('combobox',{name:'案件编号列',exact:true})).toHaveValue('case_id');await intake.getByRole('checkbox',{name:'确认材料已获授权并完成脱敏；金额为每个案件的历史累计汇总。'}).check();await intake.getByRole('button',{name:'留存新版本并核对',exact:true}).click();await expect(intake.getByRole('heading',{name:'材料核对结果'})).toBeVisible();
 const h=await (await request.get(`${root}/material-versions/${material.id}`,{headers})).json();expect(h.latest_version).toBe(2);const original=await request.get(`${root}/customer-materials/${material.id}/content`,{headers});expect(await original.text()).toContain('C002,0,0');
 const link=await request.post(`${root}/material-associations`,{headers,data:{material_id:h.latest_id,case_id:'C002',acknowledged:true}});expect(link.status()).toBe(201);
 await page.getByRole('tab',{name:'证据工作台',exact:true}).click();const panel=page.getByRole('region',{name:'案件证据工作台'});await panel.getByLabel('核验案件编号',{exact:true}).fill('C002');await panel.getByRole('button',{name:'读取当前证据',exact:true}).click();await expect(panel.getByRole('heading',{name:'证据与验收条件',exact:true})).toBeVisible();await panel.getByRole('button',{name:'生成核验建议',exact:true}).click();await expect(panel.getByRole('button',{name:'创建核验任务',exact:true})).toBeVisible();await panel.getByRole('button',{name:'创建核验任务',exact:true}).click();
 const taskForm=panel.locator('.evidence-task-action').first();await taskForm.getByLabel('处理记录编号',{exact:true}).fill('REVIEW/BROWSER-415');await taskForm.getByRole('button',{name:'记录处理结果',exact:true}).click();await expect(panel.getByText(/已记录处理/)).toBeVisible();
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'test-results/evidence-workspace-desktop.png'});await page.setViewportSize({width:320,height:1000});await panel.getByLabel('核验案件编号',{exact:true}).scrollIntoViewIfNeeded();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.screenshot({path:'test-results/evidence-workspace-mobile.png'});
 await panel.getByLabel('核验案件编号',{exact:true}).fill('MISSING');await expect(panel.getByRole('heading',{name:'证据与验收条件',exact:true})).toHaveCount(0);await panel.getByRole('button',{name:'读取当前证据',exact:true}).click();await expect(panel.getByRole('alert')).toHaveText('案件不存在');
});

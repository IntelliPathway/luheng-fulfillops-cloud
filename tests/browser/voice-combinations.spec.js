import {test,expect} from '@playwright/test';

const api='http://127.0.0.1:8000/api/v1',headers={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'test-user'};

test('administrator saves presets and sees actual unavailable reports instead of fixed latency',async({page,request})=>{
 const name=`浏览器 ASR 对照 ${Date.now()}`;
 await page.goto('/#/integrations');
 const panel=page.getByRole('region',{name:'语音模型组合'});
 await panel.getByRole('button',{name:'新增组合',exact:true}).click();
 await page.getByLabel('模型预设',{exact:true}).selectOption('tts-large');
 await page.getByLabel('播报风格',{exact:true}).selectOption('calm');
 await page.getByLabel('TTS 模型',{exact:true}).selectOption('qwen-tts-0.6b');
 await expect(page.getByLabel('播报风格',{exact:true})).toHaveValue('default');
 await page.getByLabel('模型预设',{exact:true}).selectOption('asr-fast');
 await page.getByLabel('组合名称',{exact:true}).fill(name);
 await page.getByRole('button',{name:'保存组合',exact:true}).click();
 const row=panel.getByRole('row').filter({hasText:name}).first();
 await expect(row).toBeVisible();
 await row.getByRole('button',{name:'检查连接',exact:true}).click();
 await expect(row.getByText('连接不可用',{exact:true})).toBeVisible();
 await panel.getByRole('checkbox',{name:`对照 ${name}`,exact:true}).check();
 await panel.getByRole('button',{name:'开始对照测试',exact:true}).click();
 const report=panel.locator('.voice-report-table').getByRole('row').filter({hasText:name}).first();
 await expect(report).toContainText('测试未通过');
 await expect(report).not.toContainText(/\d+ ms/);
 await expect(row.getByRole('button',{name:'启用组合',exact:true})).toBeDisabled();
 const response=await request.get(`${api}/voice-combinations`,{headers});
 const saved=(await response.json()).combinations.find(item=>item.name===name);
 expect(saved.selection.asr.model).toBe('qwen-asr-0.6b');expect(saved.enabled).toBe(false);
 await row.getByRole('button',{name:'编辑',exact:true}).click();
 await page.getByLabel('模型预设',{exact:true}).selectOption('baseline');
 await page.getByRole('button',{name:'保存组合',exact:true}).click();
 await expect(row).toContainText('v2');
 await expect(report).toContainText('历史或已过期');
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('operator reads combinations without administrator actions',async({page})=>{
 await page.route('**/api/v1/auth/session',async route=>{
  const response=await route.fetch();const session=await response.json();
  await route.fulfill({response,json:{...session,role:'operator'}});
 });
 await page.goto('/#/integrations');
 const panel=page.getByRole('region',{name:'语音模型组合'});
 await expect(panel.getByText('当前成员可查看测试报告',{exact:false})).toBeVisible();
 await expect(panel.getByRole('button',{name:'新增组合',exact:true})).toBeDisabled();
 await expect(panel.getByRole('button',{name:'开始对照测试',exact:true})).toBeDisabled();
 await page.goto('/#/agents/ACT-001');
 const activity=page.getByRole('region',{name:'活动语音模型组合'});
 await expect(activity.getByText('活动尚未选择语音组合。',{exact:true})).toBeVisible();
 await expect(activity.getByRole('button',{name:'验证活动语音链路',exact:true})).toBeDisabled();
});

test('reachable model API errors block mutations and never become a demo configuration',async({page})=>{
 let mutations=0;
 await page.route('**/api/v1/voice-combinations**',async route=>{
  if(route.request().method()!=='GET')mutations++;
  await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模型目录暂时不可用'})});
 });
 await page.goto('/#/integrations');
 const panel=page.getByRole('region',{name:'语音模型组合'});
 await expect(panel.getByRole('alert')).toContainText('模型目录暂时不可用');
 await expect(panel.getByRole('button',{name:'开始对照测试',exact:true})).toBeDisabled();
 await panel.getByRole('button',{name:'新增组合',exact:true}).click();
 await expect(page.getByRole('button',{name:'保存组合',exact:true})).toBeDisabled();
 expect(mutations).toBe(0);
});

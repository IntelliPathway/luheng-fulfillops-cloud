import {test,expect} from '@playwright/test';
const api='http://127.0.0.1:8000/api/v1';
const actor={'X-Tenant-ID':'TENANT_A','X-Actor-ID':'test-user'};
const routes=['overview','control','pilot','activities','assets','cases','plans','payments','agents','exceptions','logs','strategy','integrations','usage','settings'];
async function demo(page){await page.route('**/api/v1/**',r=>r.fulfill({status:503,json:{mode:'sites-demo',detail:'业务 API 尚未接入'}}))}
async function connected(page,route='control'){await page.goto(`/#/${route}`);await expect(page.locator('.server-connected')).toBeVisible()}

test('all 15 offline surfaces stay useful and explicitly distinguish unavailable writes',async({page})=>{
 test.setTimeout(90000);await demo(page);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const route of routes){await page.goto(`/#/${route}`);await expect(page.locator('.main-content')).toBeVisible();await expect(page.getByRole('heading',{level:1})).toBeVisible()}
 await page.goto('/#/control');await expect(page.getByLabel('指挥台指令')).toBeVisible();await page.getByRole('button',{name:'今天哪些案件不能联系？',exact:true}).click();await expect(page.locator('.assistant-answer')).toContainText('保护与异常查询结果');await expect(page.locator('.assistant-answer')).toContainText('演示规则回答');
 await page.getByRole('button',{name:'进入异常中心',exact:true}).click();await expect(page).toHaveURL(/exceptions/);
 await page.goto('/#/control');await page.getByRole('button',{name:'为 PKG_A 创建履约活动',exact:true}).click();await page.getByRole('button',{name:'审阅演示任务草案',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
 await page.getByRole('tab',{name:/成员治理/}).click();await expect(page.getByRole('heading',{name:'成员审批需要业务 API 与管理员'})).toBeVisible();await page.getByRole('tab',{name:/联系编排/}).click();await expect(page.getByRole('button',{name:'创建演示活动',exact:true})).toBeEnabled();expect(errors).toEqual([]);
});

for(const width of [1440,390,320])test(`offline command actions and evidence fit ${width}px`,async({page})=>{
 await demo(page);await page.setViewportSize({width,height:1000});await page.goto('/#/control');await page.getByRole('button',{name:'累计回款和佣金是多少？',exact:true}).click();await expect(page.locator('.assistant-answer')).toContainText('经营指标查询结果');
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
 await expect(page.locator('img').first()).toHaveJSProperty('complete',true);await page.screenshot({path:`test-results/command-demo-${width}.png`,fullPage:true});
});

test('operator can use command center despite admin-only membership endpoints',async({page})=>{
 let adminRequests=0;await page.route('**/api/v1/**',async r=>{if(/\/governance\/(members|membership-proposals)/.test(r.request().url()))adminRequests++;await r.continue({headers:{...r.request().headers(),'x-actor-id':'test-operator'}})});
 await connected(page);await expect(page.getByText('创建变更提案',{exact:true})).toHaveCount(0);await expect(page.getByRole('alert')).toHaveCount(0);await page.getByRole('tab',{name:/成员治理/}).click();await expect(page.getByText('当前身份可查询运营，成员治理需要管理员',{exact:true})).toBeVisible();expect(adminRequests).toBe(0);
 await page.getByRole('tab',{name:/联系编排/}).click();await expect(page.getByLabel('案件',{exact:true})).toBeVisible();
});

test('one failed feed never removes working contact controls; queue failure is not reported as empty',async({page})=>{
 let fail=true;await page.route('**/api/v1/operations/work-queue?*',r=>fail?r.fulfill({status:503,json:{detail:'QUEUE_UNAVAILABLE'}}):r.continue());
 await connected(page);await expect(page.getByRole('alert')).toContainText('QUEUE_UNAVAILABLE');await expect(page.getByText('决策队列暂不可用',{exact:true})).toBeVisible();await expect(page.getByText('队列已经清空',{exact:true})).toHaveCount(0);
 await page.getByRole('tab',{name:/联系编排/}).click();await expect(page.getByRole('heading',{name:'创建联系计划'})).toBeVisible();await expect(page.getByLabel('脱敏联系引用')).not.toHaveValue('CONTACT-REF-');fail=false;await page.getByRole('button',{name:'刷新状态',exact:true}).click();await expect(page.getByRole('alert')).toHaveCount(0);
});

test('command center uses real server jobs and never substitutes demo answers on failure',async({page})=>{
 await connected(page);await page.getByLabel('指挥台指令').fill('查询 C002 当前状态');await page.getByRole('button',{name:'发送指令',exact:true}).click();await expect(page.locator('.command-assistant .assistant-answer')).toContainText('C002');await expect(page.locator('.command-assistant .assistant-answer')).not.toContainText('演示规则回答');
 await page.route('**/agents/sessions/*/messages',r=>r.fulfill({status:503,json:{detail:'AGENT_UNAVAILABLE'}}));await page.getByLabel('指挥台指令').fill('回款是多少');await page.getByRole('button',{name:'发送指令',exact:true}).click();await expect(page.getByRole('alert')).toContainText('AGENT_UNAVAILABLE');await expect(page.locator('.command-assistant .assistant-answer')).toHaveCount(0);await expect(page.getByLabel('指挥台指令')).toBeEnabled();
});

test('late AI response cannot expose the previous tenant after switching workspace',async({page})=>{
 let release;const gate=new Promise(resolve=>release=resolve);await page.route('**/agents/sessions/*/messages',async r=>{await gate;await r.fulfill({json:{id:'LATE_JOB',status:'succeeded',result:{answer:{title:'TENANT_A_PRIVATE_ANSWER',body:'previous tenant data',facts:[],sources:[]}}}})});
 await connected(page);await page.getByRole('button',{name:'AI 查询',exact:false}).click();await page.getByLabel('AI 查询指令').fill('回款');await page.getByRole('button',{name:'发送',exact:true}).click();await expect(page.getByRole('button',{name:'发送',exact:true})).toBeDisabled();
 await page.locator('.workspace-switch').click();await page.locator('.workspace-pop').getByRole('button').last().click();await expect(page.locator('.server-connected')).toBeVisible();const arrived=page.waitForResponse(r=>r.url().includes('/messages'));release();await arrived;await expect(page.locator('.copilot-scope')).toContainText('租户 B');await expect(page.locator('.assistant-answer')).toHaveCount(0);await expect(page.getByText('TENANT_A_PRIVATE_ANSWER')).toHaveCount(0);
});

test('connected usage errors have no fabricated meters and retry recovers',async({page})=>{
 let fail=true;await page.route('**/api/v1/platform/usage',r=>fail?r.fulfill({status:503,json:{detail:'METER_UNAVAILABLE'}}):r.continue());await connected(page,'usage');await expect(page.getByRole('alert')).toContainText('METER_UNAVAILABLE');await expect(page.locator('.metrics')).not.toContainText('146');await expect(page.locator('.metrics')).not.toContainText('18');await expect(page.locator('.metrics')).toContainText('—');fail=false;await page.getByRole('button',{name:'刷新用量',exact:true}).click();await expect(page.getByRole('alert')).toHaveCount(0);await expect(page.locator('.metrics')).toContainText('活跃席位');
});

test('audit reload failure clears old rows and disables stale export',async({page})=>{
 await connected(page,'logs');await expect(page.getByRole('button',{name:'导出审计',exact:true})).toBeEnabled();await page.route('**/api/v1/governance/audit-events?*',r=>r.fulfill({status:503,json:{detail:'AUDIT_UNAVAILABLE'}}));await page.getByPlaceholder('搜索动作、资源、主体或编号…').fill('broken-scope');await expect(page.getByRole('alert')).toContainText('AUDIT_UNAVAILABLE');await expect(page.getByRole('button',{name:'导出审计',exact:true})).toBeDisabled();await expect(page.locator('.trace-table tbody tr')).toHaveCount(0);
});

test('strategy experiment UI creates, requires independent start, displays results and stops',async({page,request})=>{
 await connected(page,'strategy');await page.getByRole('tab',{name:'策略实验',exact:true}).click();await expect(page.getByRole('button',{name:'创建实验草稿',exact:true})).toBeEnabled();await page.getByRole('button',{name:'创建实验草稿',exact:true}).click();const modal=page.getByRole('dialog');await modal.getByLabel('实验名称').fill('浏览器治理实验');await modal.getByLabel('实验假设').fill('比较候选分组与控制分组的历史确认回款比例');await modal.getByRole('checkbox').check();await modal.getByRole('button',{name:'提交实验草稿',exact:true}).click();await expect(modal).toBeHidden();const row=page.locator('.experiment-grid article').filter({hasText:'浏览器治理实验'});await expect(row.getByRole('button',{name:'独立启动',exact:true})).toBeDisabled();
 const response=await request.get(`${api}/strategy-experiments`,{headers:actor});const created=(await response.json()).find(r=>r.name==='浏览器治理实验');expect(created).toBeTruthy();const start=await request.post(`${api}/strategy-experiments/${created.id}/transition`,{headers:actor,data:{action:'start',expected_version:created.version,acknowledged:true}});expect(start.status()).toBe(200);
 await page.getByRole('button',{name:'刷新实验',exact:true}).click();await row.getByRole('button',{name:'查看分组统计',exact:true}).click();await expect(page.getByRole('heading',{name:/历史分组统计/})).toBeVisible();await row.getByRole('button',{name:'停止实验',exact:true}).click();await page.getByRole('dialog').getByRole('checkbox').check();await page.getByRole('button',{name:'确认实验变更',exact:true}).click();await expect(row).toContainText('已停止');
});

test('lifecycle UI submits and independently reviews a versioned proposal',async({page,request})=>{
 // Submit using a different authenticated admin, then review in the browser as Terry.
 const lifecycle=await (await request.get(`${api}/platform/lifecycle`,{headers:actor})).json();
 const target=lifecycle.stage==='trial'?'active':'grace';
 await connected(page,'settings');await page.getByRole('button',{name:'提交生命周期变更',exact:true}).click();let draft=page.getByRole('dialog');await draft.getByLabel('合同或试点批准引用').fill('BROWSER-CREATE-LIFECYCLE');await draft.getByLabel('生命周期变更依据').fill('合成案例验证完整提案界面，不改变生产客户状态');await draft.getByRole('checkbox').check();await draft.getByRole('button',{name:'提交独立复核',exact:true}).click();await expect(draft).toBeHidden();await expect(page.getByLabel('租户生命周期治理')).toContainText('等待另一名管理员');const ownRows=await (await request.get(`${api}/platform/lifecycle/proposals`,{headers:actor})).json();const own=ownRows.find(r=>r.status==='pending_review');expect(own.proposed_by).toBe('Terry');const rejected=await request.post(`${api}/platform/lifecycle/proposals/${own.id}/decision`,{headers:actor,data:{decision:'reject',expected_version:own.version,review_note:'独立驳回合成测试提案，保持初始生命周期',acknowledged:true}});expect(rejected.status()).toBe(200);
 const created=await request.post(`${api}/platform/lifecycle/proposals`,{headers:actor,data:{target_stage:target,expected_lifecycle_version:lifecycle.version,contract_reference:'BROWSER-LIFECYCLE-REF',proposal_reason:'独立核对试点授权和数据保留策略的合成验证',acknowledged:true}});expect(created.status()).toBe(201);
 await connected(page,'settings');const surface=page.getByLabel('租户生命周期治理');await surface.getByRole('button',{name:'独立复核生命周期',exact:true}).click();const modal=page.getByRole('dialog');await modal.getByLabel('生命周期复核意见').fill('独立核验合同和保留策略，通过合成测试');await modal.getByRole('checkbox').check();await modal.getByRole('button',{name:'批准生命周期变更',exact:true}).click();await expect(modal).toBeHidden();await expect(surface).toContainText('approved');await expect(page.getByLabel('工作空间显示别名（本次会话）')).toBeVisible();
});

test('authoritative protection feed failure blocks startup instead of assuming no restrictions',async({page})=>{
 await page.route('**/api/v1/protections/overview',r=>r.fulfill({status:503,json:{detail:'PROTECTION_UNAVAILABLE'}}));await page.goto('/#/control');await expect(page.getByRole('heading',{name:'业务 API 尚未就绪'})).toBeVisible();await expect(page.locator('.main-content')).toHaveCount(0);await expect(page.getByRole('button',{name:'发送指令',exact:true})).toHaveCount(0);
});

test('online activity shows persisted tools and confirms an Agent proposal exactly once',async({page,request})=>{
 const activities=await (await request.get(`${api}/activities`,{headers:actor})).json();const activity=activities.find(row=>row.status==='running');expect(activity).toBeTruthy();let confirmations=0,transitions=0;page.on('request',r=>{if(r.method()==='POST'&&/agents\/proposals\/.*\/confirm/.test(r.url()))confirmations++;if(r.method()==='POST'&&/activities\/.*\/transition/.test(r.url()))transitions++});
 await connected(page,`agents/${activity.activity_id}`);await expect(page.getByRole('button',{name:'推进沙箱模拟',exact:true})).toHaveCount(0);await expect(page.getByText('下一检查 2026.09.13 10:00')).toHaveCount(0);await page.getByRole('tab',{name:'用量',exact:true}).click();await expect(page.getByText('活动成本归因待核对',{exact:true})).toBeVisible();await expect(page.getByText('¥0.38',{exact:true})).toHaveCount(0);
 await page.getByRole('tab',{name:'运行概览',exact:true}).click();await page.getByPlaceholder(`向 ${activity.name} Agent 提问或下达目标…`).fill('暂停本活动');await page.getByRole('button',{name:'发送 Agent 指令',exact:true}).click();await expect(page.getByRole('button',{name:'确认草案',exact:true})).toBeVisible();await page.getByRole('button',{name:'确认草案',exact:true}).click();await expect(page).toHaveURL(/activities/);expect(confirmations).toBe(1);expect(transitions).toBe(0);
 const latest=await (await request.get(`${api}/activities`,{headers:actor})).json();expect(latest.find(row=>row.activity_id===activity.activity_id).status).toBe('paused');
 await connected(page,`agents/${activity.activity_id}`);await page.getByRole('tab',{name:'工具轨迹',exact:true}).click();await expect(page.locator('.persisted-agent-run')).not.toHaveCount(0);await expect(page.locator('.persisted-agent-run').first()).toContainText('activity.read');await page.screenshot({path:'test-results/persisted-agent-tools-desktop.png'});
 await request.post(`${api}/activities/${activity.activity_id}/transition`,{headers:actor,data:{status:'running',reason:'合成验证完成，恢复原始活动状态',acknowledged:true}});
});

test('new governance forms remain usable at 320px and do not claim executed policy replay',async({page})=>{
 await page.setViewportSize({width:320,height:1000});await connected(page,'strategy');await page.getByRole('tab',{name:'策略实验',exact:true}).click();await page.getByRole('button',{name:'创建实验草稿',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);await page.screenshot({path:'test-results/experiment-form-mobile.png'});await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
 await page.goto('/#/settings');await expect(page.getByLabel('租户生命周期治理')).toBeVisible();await page.getByRole('button',{name:'提交生命周期变更',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);await page.screenshot({path:'test-results/lifecycle-form-mobile.png'});
});

test('online policy accepts server bounds, preserves only supported fields and reports no replay',async({page,request})=>{
 await connected(page,'strategy');await page.locator('.policy-row').first().getByRole('button',{name:'新建版本',exact:true}).click();const modal=page.getByRole('dialog');await expect(modal.getByLabel('开始时间')).toBeDisabled();await expect(modal.getByLabel('Agent 运行目标')).toBeDisabled();await modal.getByLabel('单案预算（元）').fill('42');await modal.getByLabel('最低结算比例（%）').fill('15');await modal.getByLabel('最大分期期数').fill('30');await modal.getByLabel('最低首付比例（%）').fill('0');await modal.getByRole('button',{name:'检查策略参数',exact:true}).click();await expect(modal).toContainText('未执行场景回放');await expect(modal).not.toContainText('25 / 25');await modal.getByRole('button',{name:'提交独立复核',exact:true}).click();await expect(modal).toContainText('服务端参数检查通过 · 未执行回放');await expect(modal.getByRole('button',{name:'批准并发布',exact:true})).toBeDisabled();
 const proposals=await (await request.get(`${api}/policy-proposals?package_id=PKG_A`,{headers:actor})).json();const row=proposals.find(r=>r.status==='pending_review');expect(row.proposed_policy.budget_limit_yuan).toBe(42);expect(row.evaluation.replay_executed).toBe(false);expect(row.evaluation.scenario_count).toBe(0);const rejected=await request.post(`${api}/policy-proposals/${row.id}/decision`,{headers:actor,data:{decision:'reject',expected_version:row.version,review_note:'合成验证完成，驳回测试参数保持原策略',acknowledged:true}});expect(rejected.status()).toBe(200);
});

test('failed financial summary cannot expose demo payment rows or export',async({page})=>{
 await page.route('**/api/v1/payments/overview',r=>r.fulfill({status:503,json:{detail:'FINANCIAL_UNAVAILABLE'}}));await connected(page,'payments');await expect(page.getByText('账务资料暂不可用',{exact:true})).toBeVisible();await expect(page.locator('.payment-table tbody tr')).toHaveCount(0);await expect(page.getByRole('button',{name:'导出明细',exact:true})).toHaveCount(0);await page.unroute('**/api/v1/payments/overview');await page.getByRole('button',{name:'重新读取账务',exact:true}).click();await expect(page.getByText('账务资料暂不可用',{exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'导出明细',exact:true})).toBeEnabled();
});

test('activity totals come from complete server aggregates and refresh independently of cached ledger rows',async({page,request})=>{
 const activity=(await (await request.get(`${api}/activities`,{headers:actor})).json())[0];let cents=9876543;await page.route('**/agents/activities/*/summary',r=>r.fulfill({json:{tenant_id:'TENANT_A',activity_id:activity.activity_id,confirmed_net_recovery_cents:cents,accrued_commission_cents:123456,ledger_entry_count:601,protected_case_count:0,activity_attribution:false}}));await connected(page,`agents/${activity.activity_id}`);await expect(page.locator('.metrics')).toContainText('¥98,765.43');cents=10000000;await page.getByRole('button',{name:'刷新运行证据',exact:true}).click();await expect(page.locator('.metrics')).toContainText('¥100,000');await page.route('**/agents/activities/*/summary',r=>r.fulfill({status:503,json:{detail:'ACTIVITY_SUMMARY_UNAVAILABLE'}}));await page.getByRole('button',{name:'刷新运行证据',exact:true}).click();await expect(page.getByRole('alert')).toContainText('ACTIVITY_SUMMARY_UNAVAILABLE');await expect(page.locator('.metrics')).not.toContainText('¥100,000');
});

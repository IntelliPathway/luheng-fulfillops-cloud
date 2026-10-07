import {test,expect} from '@playwright/test';

test('enterprise guide opens working steps and downloads standards in demo mode',async({page,request})=>{
 await page.goto('http://127.0.0.1:5180/#/pilot');
 const guide=page.getByRole('region',{name:'企业接入向导'});
 await expect(guide).toContainText('当前为演示模式');
 await expect(guide.getByRole('button',{name:'刷新接入进度'})).toBeDisabled();
 for(const name of ['enterprise-cases.csv','enterprise-materials.csv','enterprise-manifest.json','enterprise-standard.json']){
  const response=await request.get('http://127.0.0.1:5180/standards/'+name);expect(response.status()).toBe(200);expect(response.headers()['content-type']).not.toContain('text/html');
 }
 await guide.locator('article').filter({hasText:'材料接入'}).getByRole('button').click();
 await expect(page.getByRole('tab',{name:'接入材料',exact:true})).toHaveAttribute('aria-selected','true');
 await guide.locator('article').filter({hasText:'关联、对账与独立验收'}).getByRole('button').click();
 await expect(page.getByRole('tab',{name:'案例验收',exact:true})).toHaveAttribute('aria-selected','true');
 await page.setViewportSize({width:320,height:900});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('enterprise progress reload clears stale evidence on failure',async({page})=>{
 await page.goto('/#/pilot');const guide=page.getByRole('region',{name:'企业接入向导'});
 await expect(guide).toContainText('当前有效管理员');
 await page.route('**/enterprise/onboarding?*',r=>r.fulfill({status:503,json:{detail:'ONBOARDING_UNAVAILABLE'}}));
 await guide.getByRole('button',{name:'刷新接入进度'}).click();
 await expect(guide.getByRole('alert')).toContainText('ONBOARDING_UNAVAILABLE');
 await expect(guide).not.toContainText('当前有效管理员');
});

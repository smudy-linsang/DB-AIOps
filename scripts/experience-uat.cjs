const { chromium } = require(require.resolve('playwright', { paths: [process.env.UAT_TOOLS_DIR || '/workspace/uat-tools', process.cwd()] }));
const fs = require('fs');
const assert = require('node:assert/strict');
const account = JSON.parse(fs.readFileSync(process.env.UAT_ACCOUNT_FILE || '/workspace/dbaiops-runtime/uat-account.json'));
const env = Object.fromEntries(fs.readFileSync('/workspace/dbaiops-runtime/runtime.env','utf8').trim().split('\n').map(line => { const i=line.indexOf('='); return [line.slice(0,i),line.slice(i+1)]; }));
const report = { browser: 'system Chromium via Playwright, real Django + PostgreSQL', tests: [], pageErrors: [] };
const dir = process.env.UAT_EVIDENCE_DIR || '/workspace/dbaiops-evidence';
fs.mkdirSync(dir, {recursive:true});
(async () => {
 const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true, args:['--no-sandbox'] });
 const context = await browser.newContext({ timezoneId:'Asia/Shanghai', viewport: { width:1440, height:1000 } });
 const page = await context.newPage(); page.setDefaultTimeout(15000);
 page.on('pageerror', err => report.pageErrors.push(err.message));
 async function test(name, fn) { try { await fn(); report.tests.push({name,status:'pass'}); console.log('PASS '+name); } catch(err) { report.tests.push({name,status:'fail',error:String(err.message).replaceAll(account.password,'[redacted]').replaceAll(env.POSTGRES_PASSWORD,'[redacted]')}); throw err; } }
 try {
 await test('登录校验与原页面返回', async () => {
   await page.goto('http://127.0.0.1:8010/databases?from=uat');
   await page.getByRole('button',{ name:/登\s*录/ }).click();
   await page.getByText('请输入用户名',{exact:true}).waitFor();
   await page.getByLabel('用户名',{exact:true}).fill(account.username);
   await page.getByLabel('密码',{exact:true}).fill('deliberately-wrong');
   await page.getByRole('button',{name:/登\s*录/}).click();
   await page.getByText('用户名或密码不正确，请检查后重试').waitFor();
   await page.getByLabel('密码',{exact:true}).fill(account.password);
   await page.getByRole('button',{name:/登\s*录/}).click();
   await page.waitForURL('**/databases?from=uat');
 });
 await test('搜索功能并以回车导航', async () => {
   await page.getByLabel('搜索功能或实例').fill('事故');
   await page.getByText('功能 · 事故中心',{exact:true}).waitFor();
   await page.getByLabel('搜索功能或实例').press('Enter');
   await page.waitForURL('**/incidents');
 });
 await test('搜索无结果解释', async () => {
   await page.getByLabel('搜索功能或实例').fill('not-a-real-feature');
   await page.getByText(/未找到入口/).waitFor();
   await page.getByLabel('搜索功能或实例').fill('');
 });
 await test('指南提供下一步和权限说明', async () => {
   await page.getByRole('button',{name:'使用指南',exact:true}).click();
   await page.getByText('使用指南 · 从纳管到闭环').waitFor();
   await page.getByRole('button',{name:'前往确认监控数据'}).click();
   await page.waitForURL('http://127.0.0.1:8010/');
   await page.getByRole('dialog',{name:'使用指南 · 从纳管到闭环'}).waitFor({state:'hidden'});
   await page.getByText('数据来源与更新时间',{exact:true}).waitFor();
   await page.getByText('正在获取趋势…').waitFor({state:'hidden'});
   await page.screenshot({path:dir+'/dashboard-desktop.png',fullPage:true});
 });
 await test('时间范围请求真实后端并展示来源', async () => {
   const responsePromise=page.waitForResponse(r=>r.url().includes('/dashboard/charts/?hours=6'));
   await page.getByText('6小时',{exact:true}).click();
   const response=await responsePromise; assert.equal(response.status(),200); assert.equal((await response.json()).hours,6);
   await page.getByText(/来源：orm/).waitFor();
 });
 await test('部分接口失败保留旧数据并支持重试（网络故障注入）', async () => {
   await page.route('**/api/v1/dashboard/stats/', route => route.fulfill({ status:503, contentType:'application/json', headers:{'X-Request-ID':'uat-stats-failure'}, body:JSON.stringify({error:'验收注入：统计服务暂不可用'}) }));
   await page.getByRole('button',{name:'刷新数据'}).click();
   await page.getByText('失败 · 保留旧数据',{exact:true}).waitFor();
   await page.getByText(/请求编号：uat-stats-failure/).waitFor();
   await page.screenshot({path:dir+'/partial-failure.png',fullPage:true});
   await page.unroute('**/api/v1/dashboard/stats/');
   await page.getByRole('button',{name:'刷新数据'}).click();
   await page.getByText('部分数据未更新，请勿据此判断系统正常',{exact:true}).waitFor({state:'hidden'});
 });
 await test('未知链接明确404并可返回', async () => {
   await page.goto('http://127.0.0.1:8010/unknown-uat-page');
   await page.getByText('页面不存在',{exact:true}).waitFor();
   await page.getByRole('link',{name:'返回监控概览'}).click();
   await page.getByText('数据来源与更新时间',{exact:true}).waitFor();
 });
 await test('过期会话重新登录回到同一地址', async () => {
   await page.evaluate(()=>localStorage.setItem('auth_token','invalid-uat-token'));
   await page.goto('http://127.0.0.1:8010/?check=expired#context');
   await page.waitForURL('**/login?returnTo=**');
   await page.getByLabel('用户名',{exact:true}).fill(account.username);
   await page.getByLabel('密码',{exact:true}).fill(account.password);
   await page.getByRole('button',{name:/登\s*录/}).click();
   await page.waitForURL('http://127.0.0.1:8010/?check=expired#context');
 });
 await test('纳管输入取消可继续编辑，确认后才丢弃', async () => {
   await page.goto('http://127.0.0.1:8010/databases');
   await page.getByRole('button',{name:/添加数据库$/}).click();
   const dialog=page.getByRole('dialog',{name:'添加数据库',exact:true});
   await dialog.getByLabel('数据库名称',{exact:true}).fill('UAT-draft');
   await dialog.getByRole('button',{name:/取\s*消/}).click();
   await page.getByRole('button',{name:'继续编辑',exact:true}).click();
   assert.equal(await dialog.getByLabel('数据库名称',{exact:true}).inputValue(),'UAT-draft');
   await dialog.getByRole('button',{name:/取\s*消/}).click();
   await page.getByRole('button',{name:'放弃修改',exact:true}).click();
   await dialog.waitFor({state:'hidden'});
 });
 await test('真实PG连接测试、参数修改撤销旧结果、配置保存回读', async () => {
   await page.getByRole('button',{name:/添加数据库$/}).click();
   const d=page.getByRole('dialog',{name:'添加数据库',exact:true});
   await d.getByLabel('数据库名称',{exact:true}).fill('UAT-PostgreSQL-' + Date.now());
   await d.getByLabel('数据库类型',{exact:true}).locator('..').locator('..').click();
   await page.locator('.ant-select-item-option-content').getByText('PostgreSQL',{exact:true}).click();
   await d.getByLabel('主机地址',{exact:true}).fill(env.POSTGRES_HOST);
   await d.getByLabel('端口',{exact:true}).fill(env.POSTGRES_PORT);
   await d.getByLabel('用户名',{exact:true}).fill(env.POSTGRES_USER);
   await d.getByLabel('密码',{exact:true}).fill(env.POSTGRES_PASSWORD);
   await d.getByLabel('服务名/数据库名',{exact:true}).fill(env.POSTGRES_DB);
   await d.getByLabel('采集周期(秒)',{exact:true}).fill('120');
   await d.getByRole('button',{name:/测试连接$/}).click();
   await d.getByText('✅ 连接成功',{exact:true}).waitFor();
   await d.getByLabel('数据库名称',{exact:true}).fill('UAT-PostgreSQL-ready-' + Date.now());
   await d.getByText('✅ 连接成功',{exact:true}).waitFor({state:'hidden'});
   let release;
   let receivedResolve;
   const received = new Promise(resolve => { receivedResolve = resolve; });
   await page.route('**/api/v1/databases/test-connection/', async route => {
     const response = await route.fetch();
     await new Promise(resolve => { release = resolve; receivedResolve(); });
     await route.fulfill({response});
   });
   await d.getByRole('button',{name:/测试连接$/}).click();
   await received;
   await d.getByLabel('数据库名称',{exact:true}).fill('UAT-PostgreSQL-late-' + Date.now());
   const completed = page.waitForResponse(r=>r.url().endsWith('/api/v1/databases/test-connection/'));
   release(); await completed;
   await page.waitForFunction(() => !document.querySelector('.ant-modal .ant-btn-loading'));
   assert.equal(await d.getByText('✅ 连接成功',{exact:true}).count(),0);
   await page.unroute('**/api/v1/databases/test-connection/');
   const saved=page.waitForResponse(r=>r.url().endsWith('/api/v1/databases/')&&r.request().method()==='POST');
   await d.getByRole('button',{name:/添\s*加$/}).click();
   const response=await saved; assert.equal(response.status(),201); const db=await response.json();
   report.createdConfigId=db.id;
   await d.waitFor({state:'hidden'});
   const detail=await page.evaluate(async id => {const r=await fetch(`/api/v1/databases/${id}/`,{headers:{Authorization:`Bearer ${localStorage.getItem('auth_token')}`}});const data=await r.json();return {interval:data.collect_interval_sec,template:data.template_name};},db.id);
   assert.equal(detail.interval,120); assert.equal(detail.template,'pgsql_standard');
 });
 await test('窄窗口内容可读、导航可折叠', async () => {
   await page.goto('http://127.0.0.1:8010/');
   await page.setViewportSize({width:390,height:844});
   await page.getByRole('button',{name:'展开导航',exact:true}).waitFor();
   await page.getByRole('button',{name:'展开导航',exact:true}).click();
   await page.getByRole('button',{name:'折叠导航',exact:true}).click();
   await page.waitForFunction(() => document.querySelector('.ant-layout-sider').getBoundingClientRect().width <= 1.5);
   await page.getByText('数据来源与更新时间',{exact:true}).waitFor();
   await page.screenshot({path:dir+'/dashboard-mobile.png',fullPage:true});
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth > window.innerWidth+1);
   assert.equal(overflow,false);
   await page.getByRole('button',{name:'使用指南',exact:true}).click();
   await page.getByText('使用指南 · 从纳管到闭环').waitFor();
 });
 assert.equal(report.pageErrors.length,0,'browser runtime errors');
 } catch(e) { report.failureUrl=page.url(); report.failurePage=(await page.locator('body').innerText()).slice(0,300);console.log('UAT failure (see sanitized JSON report)'); process.exitCode=1; }
 finally {
   if (report.createdConfigId) {
     report.cleanupStatus = await page.evaluate(async id => {
       const response = await fetch(`/api/v1/databases/${id}/`, {method:'DELETE',headers:{Authorization:`Bearer ${localStorage.getItem('auth_token')}`}});
       return response.status;
     },report.createdConfigId).catch(()=>0);
   }
   fs.writeFileSync(dir+'/uat-results.json',JSON.stringify(report,null,2));await browser.close();}
})();

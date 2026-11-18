/* Browser acceptance against the local Django application and PostgreSQL. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { execFileSync } = require('node:child_process')
const { chromium } = require(require.resolve('playwright', {
  paths: [process.env.UAT_TOOLS_DIR || '/workspace/uat-tools', process.cwd()],
}))

const admin = JSON.parse(fs.readFileSync('/workspace/dbaiops-runtime/uat-account.json'))
const fixture = JSON.parse(fs.readFileSync('/workspace/dbaiops-runtime/reliability-uat.json'))
const dir = process.env.UAT_EVIDENCE_DIR || '/workspace/dbaiops-evidence/reliability'
const origin = 'http://127.0.0.1:8010'
const report = { runtime: 'Chromium, Django, PostgreSQL', checks: [], pageErrors: [] }

const clean = error => String(error).replaceAll(admin.password, '[redacted]')
fs.mkdirSync(dir, { recursive: true })

async function run() {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' })
  page.setDefaultTimeout(16000)
  page.on('pageerror', error => report.pageErrors.push(clean(error.message)))
  const check = async (name, step) => {
    try {
      await step()
      report.checks.push({ name, status: 'pass' })
      console.log('PASS ' + name)
    } catch (error) {
      report.checks.push({ name, status: 'fail', error: clean(error.message) })
      throw error
    }
  }

  try {
    await check('登录后从首页区分采集过期，展开详情核对新鲜度', async () => {
      await page.goto(origin + '/')
      await page.getByLabel('用户名', { exact: true }).fill(admin.username)
      await page.getByLabel('密码', { exact: true }).fill(admin.password)
      await page.getByRole('button', { name: /登\s*录/ }).click()
      const row = page.getByRole('row').filter({ hasText: fixture.prefix + ' 旧样本' })
      await row.getByText('数据过期').waitFor()
      await row.getByRole('button').click()
      await page.getByText('采集新鲜度', { exact: true }).waitFor()
      await page.getByText('最后成功的样本已过期，无法判断实例当前状态。').waitFor()
      await page.getByText('连接数使用率', { exact: true }).first().waitFor()
      await page.screenshot({ path: dir + '/collection-health.png' })
    })

    await check('值班员确认告警后仍看到异常持续和首次回执', async () => {
      await page.getByRole('menuitem', { name: /值班队列/ }).click()
      await page.getByRole('button', { name: /^告\s*警$/ }).click()
      await page.getByLabel('检索队列').fill(fixture.prefix)
      await page.getByLabel('检索队列').press('Enter')
      await page.getByRole('button', { name: '查看告警摘要' }).click()
      const drawer = page.getByRole('dialog', { name: '告警摘要' })
      await drawer.getByRole('button', { name: '确认知晓告警' }).click()
      await page.getByText('已记录确认，系统继续监测异常状态').waitFor()
      await page.getByText(/^已确认 \(1\)$/).click()
      await page.getByRole('button', { name: '查看告警摘要' }).click()
      await drawer.getByText('异常持续', { exact: true }).waitFor()
      await drawer.getByText(new RegExp(admin.username)).waitFor()
      await page.keyboard.press('Escape')
      await drawer.waitFor({ state: 'hidden' })
    })

    await check('恢复后保留确认记录和含零值的依据', async () => {
      execFileSync('.venv/bin/python', ['scripts/seed-reliability-uat.py', 'recover'])
      await page.getByRole('button', { name: '刷新队列' }).click()
      await page.getByText(/^已结束 \(1\)$/).click()
      await page.getByRole('button', { name: '查看告警摘要' }).click()
      const drawer = page.getByRole('dialog', { name: '告警摘要' })
      await drawer.getByText('恢复依据', { exact: true }).waitFor()
      await drawer.getByText('0', { exact: true }).waitFor()
      await drawer.getByText(new RegExp(admin.username)).waitFor()
      await page.screenshot({ path: dir + '/alert-recovery.png' })
      await page.keyboard.press('Escape')
      await drawer.waitFor({ state: 'hidden' })
    })

    await check('事故负责人和下一步在重载后可交接', async () => {
      await page.getByRole('button', { name: /^事\s*故$/ }).click()
      await page.getByRole('link', { name: '查看事故' }).click()
      await page.getByRole('button', { name: '编辑交接' }).click()
      await page.getByLabel('负责人账号').fill(admin.username)
      await page.getByLabel('下一步行动').fill('下一班核对复发时段和连接池配置')
      await page.getByRole('button', { name: '保存交接' }).click()
      await page.getByText('交接已保存', { exact: true }).waitFor()
      await page.reload()
      await page.getByText('下一班核对复发时段和连接池配置', { exact: true }).waitFor()
      await page.getByText('验收变更：调整连接池额度', { exact: false }).waitFor()
    })

    await check('关联同库工单并从过滤后的工单页返回事故', async () => {
      await page.getByLabel('要关联的工单编号').fill(String(fixture.ticket_id))
      await page.getByRole('button', { name: '关联现有工单' }).click()
      await page.getByText('工单已关联', { exact: true }).waitFor()
      await page.getByRole('link', { name: '打开该事故工单' }).click()
      await page.getByRole('row').filter({ hasText: '验收工单：核查连接池' }).waitFor()
      await page.getByRole('link', { name: '返回事故' }).click()
      await page.getByText('已关联工单', { exact: true }).waitFor()
    })

    await check('事故曲线和性能中心共享窗口，缺少的值可识别', async () => {
      await page.getByText('查看曲线采样值', { exact: true }).click()
      await page.locator('#incident-metrics').getByRole('cell', { name: '0', exact: true }).first().waitFor()
      await page.getByRole('link', { name: '打开同时间窗性能中心' }).click()
      await page.getByText(/固定时间窗/).waitFor()
      const params = new URL(page.url()).searchParams
      assert.equal(params.get('incident'), fixture.incident_id)
      assert.ok(params.get('from') && params.get('to'))
      await page.getByRole('link', { name: '返回事故' }).click()
    })

    await check('执行报告明确区分缺证据、验证成功和后续再次偏离', async () => {
      await page.getByText('验证超时，证据不足', { exact: true }).waitFor()
      await page.getByText('已验证恢复', { exact: true }).waitFor()
      await page.getByText(/验证后最近采样再次偏离/).waitFor()
      await page.getByText('执行与恢复验证').scrollIntoViewIfNeeded()
      await page.screenshot({ path: dir + '/verification.png' })
    })

    await check('AI 引用可核查，未匹配引用及待补证据明确标记', async () => {
      await page.getByText('历史索引缺失或该引用未包含在证据包里').waitFor()
      await page.getByText('业务入口请求量', { exact: true }).waitFor()
      await page.getByText(/备选解释 流量短时上升/).waitFor()
      await page.getByRole('link', { name: '核查原始记录' }).click()
      await page.waitForFunction(id => {
        const bounds = document.getElementById(id)?.getBoundingClientRect()
        return bounds && bounds.bottom > 0 && bounds.top < innerHeight
      }, 'event-' + fixture.prefix.slice(-8)).catch(() => {
        throw new Error('Evidence anchor not in view after following the link')
      })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.waitForFunction(() => document.querySelector('.ant-layout-sider')?.getBoundingClientRect().width <= 1.5).catch(() => { throw new Error('Mobile navigation did not collapse') })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
      await page.screenshot({ path: dir + '/incident-mobile.png' })
    })
    await check('OAM 历史轨迹可直接打开跨页的原始观测', async () => {
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.goto(origin + '/oam/trajectories/' + fixture.prefix)
      await page.getByRole('link', { name: '查看第 11 轮观测' }).click()
      await page.locator('#oam-turn-11').getByText('第11轮实测连接指标', { exact: true }).waitFor()
      await page.getByText('全部主张均有观测或 KB 证据支撑').waitFor()
    })
    assert.deepEqual(report.pageErrors, [])
  } finally {
    await browser.close()
    fs.writeFileSync(dir + '/reliability-uat.json', JSON.stringify(report, null, 2) + '\n')
  }
}

run().catch(error => { console.error(clean(error.message)); process.exitCode = 1 })

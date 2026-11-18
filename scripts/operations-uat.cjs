/* Real-browser acceptance of the operations queue, using local synthetic fixtures. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require(require.resolve('playwright', {
  paths: [process.env.UAT_TOOLS_DIR || '/workspace/uat-tools', process.cwd()],
}))

const origin = process.env.UAT_ORIGIN || 'http://127.0.0.1:8010'
const evidenceDir = process.env.UAT_EVIDENCE_DIR || '/workspace/dbaiops-evidence/v2'
const admin = JSON.parse(fs.readFileSync('/workspace/dbaiops-runtime/uat-account.json'))
const fixture = JSON.parse(fs.readFileSync('/workspace/dbaiops-runtime/operations-uat.json'))
const results = { runtime: 'Chromium + Django + PostgreSQL', steps: [], pageErrors: [] }
fs.mkdirSync(evidenceDir, { recursive: true })

function scrub(message) {
  return [admin.password, fixture.password].reduce((text, value) => text.replaceAll(value, '[redacted]'), String(message))
}

async function signIn(page, account, path) {
  await page.goto(origin + path)
  await page.getByLabel('用户名', { exact: true }).fill(account.username)
  await page.getByLabel('密码', { exact: true }).fill(account.password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(origin + path)
}

async function check(name, test) {
  try {
    await test()
    results.steps.push({ name, status: 'pass' })
    console.log('PASS ' + name)
  } catch (error) {
    results.steps.push({ name, status: 'fail', error: scrub(error.message) })
    throw error
  }
}

async function run() {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox'], headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' })
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  page.on('pageerror', error => results.pageErrors.push(scrub(error.message)))

  try {
    await check('从首页打开值班队列，并用键盘检索', async () => {
      await signIn(page, admin, '/')
      await page.getByRole('button', { name: '打开值班队列' }).click()
      await page.getByRole('heading', { name: '值班队列' }).waitFor()
      await page.getByLabel('检索队列').fill(fixture.prefix)
      await page.getByLabel('检索队列').press('Enter')
      await page.waitForURL(url => url.searchParams.get('q') === fixture.prefix)
      await page.getByText(/共 23 条/).waitFor()
      await page.getByText('正在获取队列…').waitFor({ state: 'hidden' })
      const first = await page.getByRole('list', { name: '队列事项' }).getByRole('heading').first().textContent()
      assert.ok(first.includes('核心连接告警') || first.includes('范围外事故'))
      await page.screenshot({ path: evidenceDir + '/queue-desktop.png' })
    })

    await check('分页、刷新与返回保留筛选', async () => {
      await page.locator('.ant-pagination').getByTitle('2', { exact: true }).click()
      await page.getByText(/第 2 页/).waitFor()
      assert.equal(await page.getByRole('link', { name: '查看事故' }).count(), 3)
      await page.reload()
      await page.getByText(/第 2 页/).waitFor()
      assert.equal(await page.getByLabel('检索队列').inputValue(), fixture.prefix)
      await page.getByRole('link', { name: '查看事故' }).first().click()
      await page.getByRole('link', { name: '返回值班队列' }).click()
      await page.getByText(/第 2 页/).waitFor()
      assert.match(page.url(), /page=2/)
      await page.locator('.ant-pagination').getByTitle('1', { exact: true }).click()
      await page.getByText(/第 1 页/).waitFor()
    })

    await check('刷新失败保留同一筛选的旧数据，重试恢复', async () => {
      const endpoint = '**/api/v1/work-queue/?*'
      await page.route(endpoint, route => route.fulfill({
        status: 503, json: { message: '验收注入的读取故障' }, headers: { 'X-Request-ID': 'uat-queue-503' },
      }))
      await page.getByRole('button', { name: '刷新队列' }).click()
      await page.getByText('刷新失败 · 正在显示上次结果').waitFor()
      await page.getByText(/请求编号：uat-queue-503/).waitFor()
      await page.getByText(/共 23 条/).waitFor()
      await page.unroute(endpoint)
      await page.getByRole('button', { name: /重\s*试/ }).click()
      await page.getByText('刷新失败 · 正在显示上次结果').waitFor({ state: 'hidden' })
      await page.getByText(/共 23 条/).waitFor()
    })

    await check('成功空结果有明确说明', async () => {
      await page.getByLabel('检索队列').fill(fixture.prefix + '-NO-SUCH-ITEM')
      await page.getByLabel('检索队列').press('Enter')
      await page.getByText('当前条件没有匹配事项').waitFor()
      await page.getByLabel('检索队列').fill(fixture.receipt_id)
      await page.getByLabel('检索队列').press('Enter')
      await page.getByRole('heading', { name: fixture.prefix + ' 核心连接告警' }).waitFor()
      await page.getByRole('link', { name: '查看事故' }).click()
    })

    await check('事故确认有回执，结案取消无写入，填写原因后可验证时间线', async () => {
      await page.getByRole('button', { name: '确认知晓' }).click()
      await page.getByText('已确认知晓', { exact: true }).waitFor()
      await page.getByText(new RegExp('确认人 ' + admin.username)).waitFor()
      let closePosts = 0
      page.on('request', request => {
        if (request.method() === 'POST' && request.url().endsWith('/close/')) closePosts++
      })
      const close = page.getByRole('button', { name: /^结\s*案$/ })
      await close.click()
      const dialog = page.getByRole('dialog', { name: '确认结案' })
      assert.ok(await dialog.getByRole('button', { name: '确认结案' }).isDisabled())
      await dialog.getByRole('button', { name: '继续处理' }).click()
      assert.equal(closePosts, 0)
      await close.click()
      await dialog.getByLabel('结案原因').fill('验收记录：已复核恢复证据')
      await dialog.getByRole('button', { name: '确认结案' }).click()
      await page.getByText('已关闭', { exact: true }).waitFor()
      await page.getByText(/已复核恢复证据/).waitFor()
      assert.equal(closePosts, 1)
      await page.getByRole('link', { name: '返回值班队列' }).click()
      await page.getByText('当前条件没有匹配事项').waitFor()
      await page.getByText(/^已结束 \(1\)$/).click()
      await page.getByRole('heading', { name: fixture.prefix + ' 核心连接告警' }).waitFor()
    })

    await check('缺失事故显示错误，提供返回路径', async () => {
      await page.goto(origin + '/incidents/DOES-NOT-EXIST-OPS')
      await page.getByText('无法读取事故', { exact: true }).waitFor()
      await page.getByRole('link', { name: '返回事故中心' }).waitFor()
    })

    await check('告警摘要区分已确认和未记录恢复', async () => {
      await page.goto(origin + '/work-queue?kind=alerts&stage=acknowledged&q=' + encodeURIComponent(fixture.prefix))
      await page.getByRole('button', { name: '查看告警摘要' }).click()
      const drawer = page.getByRole('dialog', { name: '告警摘要' })
      await drawer.getByText('已确认表示人员已知晓。恢复时间目前未记录。').waitFor()
      await drawer.getByText('合成记录，连接池使用达到阈值。').waitFor()
      await page.keyboard.press('Escape')
      await drawer.waitFor({ state: 'hidden' })
    })

    await check('健康概要独立保留，心跳请求失败明确可重试', async () => {
      const endpoint = '**/api/v1/system/components'
      await page.route(endpoint, route => route.fulfill({
        status: 503, json: { message: '验收注入的心跳故障' }, headers: { 'X-Request-ID': 'uat-heartbeat-503' },
      }))
      await page.getByRole('button', { name: '查看系统健康' }).click()
      await page.getByText('有组件尚未上报心跳').waitFor()
      await page.getByText('心跳明细暂不可用').waitFor()
      await page.getByText(/请求编号：uat-heartbeat-503/).waitFor()
      await page.unroute(endpoint)
      await page.getByRole('button', { name: /重\s*试/ }).click()
      await page.getByText(/获取于/).waitFor()
      await page.screenshot({ path: evidenceDir + '/system-health.png' })
    })

    await check('巡检显示真实零值，未评分单独标识', async () => {
      await page.getByRole('menuitem', { name: /智能巡检/ }).click()
      const zero = page.getByRole('row').filter({ hasText: fixture.prefix + '-COVERAGE0' })
      const missing = page.getByRole('row').filter({ hasText: fixture.prefix + '-UNKNOWN' })
      await zero.getByText('0s', { exact: true }).waitFor()
      await zero.getByRole('progressbar').hover()
      await page.getByRole('tooltip').getByText(/覆盖率 0%/).waitFor()
      await missing.getByText('未评分', { exact: true }).waitFor()
    })

    await check('只读账号仅见授权实例且无确认或结案按钮', async () => {
      const reader = await browser.newPage({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Shanghai' })
      reader.on('pageerror', error => results.pageErrors.push(scrub(error.message)))
      try {
        await signIn(reader, fixture, '/work-queue?stage=all&q=' + encodeURIComponent(fixture.prefix))
        await reader.getByText(/共 22 条/).waitFor()
        assert.equal(await reader.getByText(/范围外事故/).count(), 0)
        assert.equal(await reader.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
        await reader.waitForFunction(() => document.querySelector('.ant-layout-sider')?.getBoundingClientRect().width <= 1.5)
        await reader.screenshot({ path: evidenceDir + '/queue-mobile.png' })
        // Open an active incident so absence of write actions proves the role restriction.
        const entry = reader.getByRole('listitem').filter({ has: reader.getByRole('heading', { name: /巡查事项/ }) }).first()
        const title = await entry.getByRole('heading').textContent()
        await entry.getByRole('link', { name: '查看事故' }).click()
        await reader.getByRole('heading', { name: title, exact: true }).waitFor()
        await reader.getByText('待处理', { exact: true }).waitFor()
        assert.equal(await reader.getByRole('button', { name: '确认知晓' }).count(), 0)
        assert.equal(await reader.getByRole('button', { name: /^结\s*案$/ }).count(), 0)
      } finally {
        await reader.close()
      }
    })
    assert.deepEqual(results.pageErrors, [])
  } finally {
    await browser.close()
    fs.writeFileSync(evidenceDir + '/operations-uat.json', JSON.stringify(results, null, 2) + '\n')
  }
}

run().catch(error => { console.error(scrub(error.message)); process.exitCode = 1 })

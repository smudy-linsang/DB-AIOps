/* Browser acceptance of DB-029 against Django and the local PostgreSQL fixtures. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require(require.resolve('playwright', {
  paths: [process.env.UAT_TOOLS_DIR || '/workspace/uat-tools', process.cwd()],
}))

const origin = process.env.UAT_ORIGIN || 'http://127.0.0.1:8010'
const evidence = process.env.UAT_EVIDENCE_DIR || '/workspace/dbaiops-evidence/db029'
const account = require('/workspace/dbaiops-runtime/uat-account.json')
const fixture = require('/workspace/dbaiops-runtime/reliability-uat.json')
const targetedWarning = /validateDOMNesting|instance created by useForm|\[antd: (Card|Modal|Tabs)\].*(deprecated|static function)/i
const clean = value => String(value).replaceAll(account.password, '[redacted]')
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const result = { runtime: 'Chromium, Django and PostgreSQL', origin, checks: [], pageErrors: [], targetedWarnings: [] }
fs.mkdirSync(evidence, { recursive: true })

async function run() {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' })
  page.setDefaultTimeout(20000)
  const requests = []
  page.on('request', request => requests.push({ method: request.method(), path: new URL(request.url()).pathname }))
  page.on('pageerror', error => result.pageErrors.push(clean(error.message)))
  page.on('console', message => {
    if (targetedWarning.test(message.text())) result.targetedWarnings.push(clean(message.text()))
  })

  const check = async (id, step) => {
    try {
      result.checks.push({ id, status: 'pass', details: await step() })
      console.log(`PASS ${id}`)
    } catch (error) {
      result.checks.push({ id, status: 'fail', error: clean(error.message) })
      throw error
    }
  }

  async function checkTextFields(scope) {
    const fields = await scope.locator('input:visible, textarea:visible').evaluateAll(elements => elements
      .filter(element => ['text', 'password', 'search', 'textarea'].includes(element.type))
      .map(element => ({ identifier: element.id || element.name, autocomplete: element.autocomplete })))
    assert(fields.length > 0)
    assert(fields.every(field => field.identifier && field.autocomplete), JSON.stringify(fields))
    return fields.length
  }

  try {
    await page.goto(`${origin}/alerts`)
    await page.getByLabel('用户名', { exact: true }).waitFor()
    const loginFields = await checkTextFields(page)
    await page.getByLabel('用户名', { exact: true }).fill(account.username)
    await page.getByLabel('密码', { exact: true }).fill(account.password)
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await page.getByRole('tab', { name: /已恢复/ }).waitFor()

    await check('D-01', async () => {
      await page.getByRole('tab', { name: /已恢复/ }).click()
      await page.getByRole('row').filter({ hasText: fixture.prefix }).getByRole('button', { name: /详\s*情/ }).click()
      const dialog = page.getByRole('dialog', { name: '告警详情' })
      await dialog.getByText('恢复依据', { exact: true }).waitFor()
      for (const value of ['采集器实测', '0', '80']) await dialog.getByText(value, { exact: true }).waitFor()
      await page.screenshot({ path: `${evidence}/alert-detail.png` })
      await dialog.getByRole('button', { name: /^关\s*闭$/ }).last().click()
      await dialog.waitFor({ state: 'hidden' })
      return { entry: '/alerts → 已恢复 → 详情', observedValue: 0, threshold: 80 }
    })

    await check('D-02', async () => {
      const hint = page.getByText('实时推送不可用', { exact: true })
      await hint.waitFor()
      await page.getByText(/Redis 未启用，实时事件流不可用/).waitFor()
      const health = page.getByRole('button', { name: '查看系统健康' })
      assert.equal((await health.textContent()).trim(), '系统降级')
      const streamCount = () => requests.filter(request => request.path === '/api/v1/events/').length
      const initial = streamCount()
      assert(initial > 0)
      await page.screenshot({ path: `${evidence}/sse-degraded.png` })
      await health.click()
      await page.waitForURL('**/system/health')
      await hint.waitFor()
      const observedMs = Number(process.env.UAT_SSE_OBSERVE_MS || 3500)
      await delay(observedMs)
      assert.equal(streamCount(), initial)
      return { initialRequests: initial, retries: streamCount() - initial, observedMs }
    })

    await check('D-03', async () => {
      const detailPath = `**/api/v1/incidents/${fixture.incident_id}/`
      await page.route(detailPath, async route => { await delay(1200); await route.continue() })
      await page.goto(`${origin}/incidents/${fixture.incident_id}`)
      const loading = page.getByRole('status').filter({ hasText: '正在获取事故' })
      await loading.waitFor()
      assert.equal(await loading.evaluate(element => element.tagName), 'DIV')
      await page.getByText('交接与证据', { exact: true }).waitFor()
      await page.unroute(detailPath)
      return { loadingContainer: 'div', incidentLoaded: true }
    })

    await page.goto(`${origin}/databases`)
    const dbRow = page.getByRole('row').filter({ hasText: `${fixture.prefix} 交接库` })
    await dbRow.waitFor()
    let databaseFields = 0
    await check('D-04', async () => {
      await page.getByRole('button', { name: /添加数据库/ }).click()
      const add = page.getByRole('dialog', { name: '添加数据库' })
      databaseFields = await checkTextFields(add)
      await add.getByRole('button', { name: /^添\s*加$/ }).click()
      await add.getByText('请输入数据库名称', { exact: true }).waitFor()
      await add.getByText('请输入主机地址', { exact: true }).waitFor()
      assert(!requests.some(request => request.method === 'POST' && request.path.startsWith('/api/v1/databases/')))
      await add.getByLabel('数据库名称').fill('测试未保存输入')
      await add.getByRole('button', { name: /^取\s*消$/ }).click()
      await page.getByRole('button', { name: '继续编辑' }).click()
      assert.equal(await add.getByLabel('数据库名称').inputValue(), '测试未保存输入')
      await add.getByRole('button', { name: /^取\s*消$/ }).click()
      await page.getByRole('button', { name: '放弃修改' }).click()
      await add.waitFor({ state: 'hidden' })
      await page.getByRole('button', { name: /添加数据库/ }).click()
      assert.equal(await add.getByLabel('数据库名称').inputValue(), '')
      await add.getByRole('button', { name: /^取\s*消$/ }).click()
      await add.waitFor({ state: 'hidden' })
      await dbRow.getByRole('button', { name: /编\s*辑/ }).click()
      const edit = page.getByRole('dialog', { name: /^编辑数据库/ })
      await edit.waitFor()
      assert.equal(await edit.getByLabel('数据库名称').inputValue(), `${fixture.prefix} 交接库`)
      databaseFields += await checkTextFields(edit)
      await edit.getByRole('button', { name: /^取\s*消$/ }).click()
      await edit.waitFor({ state: 'hidden' })
      assert(!requests.some(request => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method) && request.path.startsWith('/api/v1/databases/')))
      return { requiredValidation: true, continuedEditing: true, discardedInputCleared: true, editPrefilled: true, databaseWriteRequests: 0 }
    })

    await check('D-07', async () => {
      const index = await page.getByRole('columnheader', { name: '最后采集' }).evaluate(cell => cell.cellIndex)
      const cell = dbRow.getByRole('cell').nth(index)
      await cell.getByText(/前$|刚刚/).waitFor()
      return { relativeTime: (await cell.textContent()).trim() }
    })

    await page.goto(`${origin}/inspection`)
    await page.getByRole('tab', { name: /巡检项定义/ }).click()
    await page.getByText(/项巡检规则/).waitFor()
    await page.getByRole('tab', { name: /问题模式/ }).click()
    await page.getByText(/系统自动识别的共性问题模式/).waitFor()
    await page.getByRole('button', { name: /触发巡检/ }).click()
    const inspection = page.getByRole('dialog', { name: '触发巡检' })
    await inspection.waitFor()

    await check('D-06', async () => ({
      loginFields,
      databaseDialogFields: databaseFields,
      inspectionDialogFields: await checkTextFields(inspection),
      pageFields: await checkTextFields(page),
    }))
    await check('D-05', async () => {
      assert.deepEqual(result.targetedWarnings, [])
      return { checkedLoginCard: true, checkedDatabaseModals: true, checkedInspectionTabs: true }
    })
    assert.deepEqual(result.pageErrors, [])
  } finally {
    await browser.close()
    fs.writeFileSync(`${evidence}/uat-results.json`, JSON.stringify(result, null, 2) + '\n')
  }
}

run().catch(error => { console.error(clean(error.message)); process.exitCode = 1 })

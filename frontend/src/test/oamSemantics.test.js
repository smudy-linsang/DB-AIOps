/**
 * V3.0 OAM 展示语义的单测。
 *
 * 逐条钉死六条"前端一旦表达错就是在骗用户"的语义
 * （编号与 `utils/oamSemantics.js` 顶部注释一一对应）：
 *
 *   1. evidence_insufficient + escalated = 合法终止，不是错误
 *   2. verdict.passed 是三态，null 不得渲染成"未通过"
 *   3. COVERAGE_MISSING(HTTP 500) 的业务语义，不是"服务器错误"
 *   4. 404 = "记录不存在或无权访问"，不区分越权与不存在
 *   5. 诊断只给建议：需人工审批 + 执行入口指向 v2 playbooks
 *   6. 成功率必须与覆盖度同屏，缺口必须显著
 */
import { describe, it, expect } from 'vitest'
import {
  OAM_EXECUTE_VIA,
  MUTATION_FORBIDDEN,
  verdictState,
  reportState,
  auditState,
  fixApprovalState,
  coverageWarningText,
  coverageState,
  describeError,
  toolOutcomeState,
  stalenessState,
} from '../utils/oamSemantics'

describe('规则 1：证据不足是合法终止，不是错误', () => {
  it('evidence_insufficient + escalated=true 走 warning，不走 error', () => {
    const s = reportState('evidence_insufficient', true)
    expect(s.legalStop).toBe(true)
    expect(s.escalated).toBe(true)
    expect(s.kind).toBe('warning')
    expect(s.kind).not.toBe('error')
    expect(s.color).toBe('orange')
    expect(s.text).toContain('证据不足')
    expect(s.text).toContain('转人工')
  })

  it('大小写与空白不影响判定', () => {
    expect(reportState(' Evidence_Insufficient ', false).legalStop).toBe(true)
  })

  it('detail 明说"不计成功也不计幻觉"，避免读者当成失败', () => {
    expect(reportState('evidence_insufficient', true).detail).toContain('不计幻觉')
  })

  it('仅 escalated 而非证据不足时，是"已升级人工复核"而非错误', () => {
    const s = reportState('ok', true)
    expect(s.escalated).toBe(true)
    expect(s.legalStop).toBe(false)
    expect(s.kind).not.toBe('error')
  })

  it('正常完成走 success', () => {
    expect(reportState('ok', false).kind).toBe('success')
  })

  it('空 status 不臆断成功或失败', () => {
    const s = reportState('', false)
    expect(s.kind).toBe('info')
    expect(s.text).toContain('未标注')
  })

  it('证据审计未通过也是 warning，不是 error', () => {
    const s = auditState({
      passed: false,
      route_to_human: true,
      reason: '根因主张缺少 KPI 证据',
      unsupported_count: 2,
      unsupported: [{ kind: 'root_cause', text: '共享池配置漂移', evidence_refs: [] }],
    })
    expect(s.kind).toBe('warning')
    expect(s.passed).toBe(false)
    expect(s.routeToHuman).toBe(true)
    expect(s.unsupportedCount).toBe(2)
    expect(s.reason).toBe('根因主张缺少 KPI 证据')
  })

  it('证据审计通过走 success；缺审计结果只提示，不报错', () => {
    expect(auditState({ passed: true }).kind).toBe('success')
    expect(auditState(null).kind).toBe('info')
  })
})

describe('规则 2：verdict.passed 是三态', () => {
  it('true = 已判定通过', () => {
    const s = verdictState(true)
    expect(s.state).toBe('passed')
    expect(s.scored).toBe(true)
    expect(s.kind).toBe('success')
  })

  it('false = 已判定未通过（此时红色叉才是对的）', () => {
    const s = verdictState(false)
    expect(s.state).toBe('failed')
    expect(s.scored).toBe(true)
    expect(s.kind).toBe('error')
  })

  it('null = 未判定，不得说成"未通过"', () => {
    const s = verdictState(null)
    expect(s.state).toBe('unscored')
    expect(s.scored).toBe(false)
    expect(s.state).not.toBe('failed')
    expect(s.kind).not.toBe('error')
    expect(s.color).not.toBe('red')
    expect(s.text).toContain('未判定')
    expect(s.text).toContain('不参与判分')
  })

  it('undefined / 缺字段同样按未判定处理', () => {
    expect(verdictState(undefined).state).toBe('unscored')
    expect(verdictState({}).state).toBe('unscored')
    expect(verdictState({ scored: false }).state).toBe('unscored')
  })

  it('非布尔真值不得被当成"通过"（宽松相等是这里最容易踩的坑）', () => {
    expect(verdictState(1).state).toBe('unscored')
    expect(verdictState('true').state).toBe('unscored')
    expect(verdictState(0).state).toBe('unscored')
  })
})

describe('规则 3：COVERAGE_MISSING 是业务语义，不是服务器错误', () => {
  it('HTTP 500 + COVERAGE_MISSING ⇒ 按设计视为未完成评测', () => {
    const s = describeError('COVERAGE_MISSING', 500)
    expect(s.kind).toBe('warning')
    expect(s.kind).not.toBe('error')
    expect(s.text).toContain('未完成评测')
  })

  it('不得出现"服务器错误 / 网络异常"这类把责任推给运维的措辞', () => {
    const s = describeError('COVERAGE_MISSING', 500)
    expect(s.text).not.toContain('服务器')
    expect(s.text).not.toContain('网络')
    expect(s.text).not.toContain('异常')
  })

  it('无状态的兜底才允许说网络异常', () => {
    expect(describeError(undefined, undefined).text).toContain('网络')
  })
})

describe('规则 4：越权与不存在统一是 404', () => {
  it('404 与 NOT_FOUND 都写"记录不存在或无权访问"', () => {
    const byStatus = describeError(undefined, 404)
    const byCode = describeError('NOT_FOUND', 404)
    expect(byStatus.text).toBe('记录不存在或无权访问')
    expect(byCode.text).toBe('记录不存在或无权访问')
    expect(byCode.ambiguous).toBe(true)
  })

  it('不得把 404 说成"无权限"（那会让越权探测变成预言机）', () => {
    expect(describeError('NOT_FOUND', 404).text).not.toBe('无权限')
    expect(describeError('NOT_FOUND', 404).text).toContain('记录不存在')
  })

  it('真正的 403 才讲权限（后端两道闸里 require_permission 的那道）', () => {
    const s = describeError('PERMISSION_DENIED', 403)
    expect(s.kind).toBe('warning')
    expect(s.text).toContain('没有执行该操作的权限')
    expect(s.ambiguous).toBe(false)
  })

  it('其余错误码不落空', () => {
    expect(describeError('DIAGNOSE_FAILED', 500).kind).toBe('error')
    expect(describeError('BAD_REQUEST', 400).kind).toBe('error')
    expect(describeError('DISABLED', 409).kind).toBe('warning')
    expect(describeError(418, 418).text).toContain('418')
  })
})

describe('规则 5：诊断只给建议，绝不执行变更', () => {
  const fix = {
    spec: 'ALTER SYSTEM SET shared_pool_size=2G',
    risk_level: 'unknown',
    requires_approval: true,
    execute_via: OAM_EXECUTE_VIA,
    note: '本接口不执行任何变更；变更须走既有审批链',
  }

  it('推荐修复必须挂"需人工审批"标记', () => {
    const s = fixApprovalState(fix)
    expect(s.present).toBe(true)
    expect(s.requiresApproval).toBe(true)
    expect(s.badge).toBe('需人工审批')
    expect(s.kind).toBe('warning')
  })

  it('执行入口指向既有 v2 playbooks，而不是 OAM 自己', () => {
    expect(fixApprovalState(fix).executeVia).toBe('/api/v2/playbooks/execute-safely/')
  })

  it('风险等级恒为 unknown —— 线上诊断不判风险，执行前必须人工评估', () => {
    expect(fixApprovalState(fix).riskLevel).toBe('unknown')
  })

  it('后端漏传 execute_via 时也必须落到 v2 审批链', () => {
    const s = fixApprovalState({ spec: 'reboot' })
    expect(s.executeVia).toBe(OAM_EXECUTE_VIA)
    expect(s.requiresApproval).toBe(true)
  })

  it('没有建议时不臆造，但同样保持需审批口径', () => {
    const s = fixApprovalState({ spec: '' })
    expect(s.present).toBe(false)
    expect(s.requiresApproval).toBe(true)
    expect(fixApprovalState(null).present).toBe(false)
  })

  it('MUTATION_FORBIDDEN 是策略边界（warning），不是调用失败（error）', () => {
    const s = toolOutcomeState({ tool_name: 'alter_system', tool_error_code: MUTATION_FORBIDDEN })
    expect(s.kind).toBe('warning')
    expect(s.kind).not.toBe('error')
    expect(s.text).toContain('不在策略动作空间内')
  })

  it('其他工具错误码才算调用失败；正常调用走 success', () => {
    expect(toolOutcomeState({ tool_error_code: 'TOOL_TIMEOUT' }).kind).toBe('error')
    expect(toolOutcomeState({ observation: '{...}' }).kind).toBe('success')
    expect(toolOutcomeState({}).kind).toBe('info')
  })
})

describe('规则 6：成功率必须与覆盖度同屏', () => {
  const coverage = {
    root_causes_covered: ['CONFIG_DRIFT'],
    root_causes_missing: ['LOCK_CONTENTION', 'PLAN_REGRESSION'],
    label_space_size: 4,
    covered_ratio: 0.5,
    db_types: ['mysql'],
  }

  it('后端注入 warning 时原样透出', () => {
    const withWarning = {
      ...coverage,
      warning: '该成绩不覆盖以下根因：LOCK_CONTENTION、PLAN_REGRESSION',
    }
    expect(coverageWarningText(withWarning)).toBe(
      '该成绩不覆盖以下根因：LOCK_CONTENTION、PLAN_REGRESSION',
    )
  })

  it('后端没注入 warning 时自己兜底拼一条，缺口绝不静默', () => {
    const text = coverageWarningText(coverage)
    expect(text).toContain('该成绩不覆盖以下根因')
    expect(text).toContain('LOCK_CONTENTION')
    expect(text).toContain('PLAN_REGRESSION')
  })

  it('无缺口时返回 null（表示"没有缺口"，而不是空串糊弄）', () => {
    expect(
      coverageWarningText({ root_causes_missing: [], root_causes_covered: ['A', 'B'] }),
    ).toBeNull()
    expect(coverageWarningText(null)).toBeNull()
    expect(coverageWarningText({})).toBeNull()
  })

  it('覆盖度摘要带出比率，供与成功率同屏渲染', () => {
    const s = coverageState(coverage)
    expect(s.present).toBe(true)
    expect(s.kind).toBe('warning')
    expect(s.ratio).toBe(0.5)
    expect(s.covered).toBe(1)
    expect(s.missing).toBe(2)
    expect(s.labelSpaceSize).toBe(4)
    expect(s.warning).toContain('LOCK_CONTENTION')
  })

  it('覆盖完整时是 success，不该挂个无意义的警告', () => {
    const s = coverageState({
      root_causes_covered: ['A', 'B'],
      root_causes_missing: [],
      label_space_size: 2,
      covered_ratio: 1,
    })
    expect(s.kind).toBe('success')
    expect(s.warning).toBeNull()
  })

  it('覆盖度整体缺失时按"未完成评测"警告（与 COVERAGE_MISSING 同一口径）', () => {
    const s = coverageState(null)
    expect(s.present).toBe(false)
    expect(s.kind).toBe('warning')
    expect(s.text).toContain('未完成评测')
  })

  it('空批次（标签空间为 0）不得宣称"覆盖完整"', () => {
    const s = coverageState({ root_causes_covered: [], root_causes_missing: [], label_space_size: 0 })
    expect(s.kind).toBe('info')
    expect(s.text).toContain('标签空间为空')
  })
})

describe('KB 新鲜度', () => {
  it('超过阈值走 warning，未超走 success', () => {
    expect(stalenessState(40, 30).stale).toBe(true)
    expect(stalenessState(40, 30).kind).toBe('warning')
    expect(stalenessState(10, 30).stale).toBe(false)
    expect(stalenessState(10, 30).kind).toBe('success')
  })

  it('阈值缺失时不臆断陈旧', () => {
    expect(stalenessState(10, null).stale).toBe(false)
    expect(stalenessState(null, 30).text).toBe('未知')
  })
})

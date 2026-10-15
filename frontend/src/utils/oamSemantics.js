/**
 * V3.0 OAM 展示语义（纯函数层）
 *
 * ## 为什么要单独一层
 *
 * 这六条语义一旦直接写在 JSX 里，就只能靠"人眼读代码"来保证不被表达错：
 *
 * 1. `status === 'evidence_insufficient'` 且 `escalated === true` 是**合法终止**，
 *    不是错误 —— 渲染成红色报错等于告诉 DBA"系统挂了"，而它其实是
 *    "如实说不知道并转人工"（设计 §6，后端刻意不给根因）。
 * 2. `verdict.passed` 是**三态**（`true` / `false` / `null`）。线上诊断没有专家
 *    标注真值，后端刻意不判分（`score=False`），此时是 `null`；把它渲染成红色叉
 *    等于把"没判分"说成"判了但没过"。
 * 3. `COVERAGE_MISSING` 是 HTTP 500，但业务含义是"该评测记录缺少覆盖度，
 *    按设计视为未完成评测"，不是"服务器错误 / 网络异常"。
 * 4. 越权与不存在统一是 404 —— 系统有意不区分，文案必须写"记录不存在或无权访问"，
 *    写成"无权限"就把越权探测变成了可区分的预言机。
 * 5. 诊断接口**只给建议、绝不执行变更**；推荐修复必须带"需人工审批"标记，
 *    执行入口指向既有的 `/api/v2/playbooks/execute-safely/`。
 * 6. 评测成功率必须与覆盖度**同屏**呈现（设计 §8）。
 *
 * 所以这里把"状态 → 展示语义"的映射全部收敛成**纯函数**，
 * 返回可断言的 `{ kind, text, color }`，JSX 只负责渲染、不做判断；
 * `src/test/oamSemantics.test.js` 逐条把这六条测死。
 *
 * `kind` 取值固定为 antd 的语义槽：`success` / `warning` / `info` / `error`，
 * `color` 取 antd `Tag` 的颜色名，方便直接透传。
 */

/** 诊断接口给出的执行入口：本页只给建议，变更走既有审批链。 */
export const OAM_EXECUTE_VIA = '/api/v2/playbooks/execute-safely/'

/** 诊断接口的变更类工具错误码（后端 `monitor/oam/policy/action_space.py`）。 */
export const MUTATION_FORBIDDEN = 'MUTATION_FORBIDDEN'

/** 证据不足 —— 合法终止，不是错误。 */
export const STATUS_EVIDENCE_INSUFFICIENT = 'evidence_insufficient'

const OK = (text, extra = {}) => ({ kind: 'success', color: 'green', text, ...extra })
const WARN = (text, extra = {}) => ({ kind: 'warning', color: 'orange', text, ...extra })
const INFO = (text, extra = {}) => ({ kind: 'info', color: 'blue', text, ...extra })
const ERR = (text, extra = {}) => ({ kind: 'error', color: 'red', text, ...extra })

/**
 * 规则 2：`verdict.passed` 的三态。
 *
 * 只有严格的 `true` / `false` 才是"判了分"，其余（`null` / `undefined` / 缺字段）
 * 一律是"未判定" —— 不得渲染成"未通过"。
 */
export function verdictState(passed) {
  if (passed === true) {
    return OK('已判定：通过', { state: 'passed', scored: true })
  }
  if (passed === false) {
    return ERR('已判定：未通过', { state: 'failed', scored: true })
  }
  return INFO('未判定：线上诊断无专家标注真值，不参与判分（仅供人工研判）', {
    state: 'unscored',
    scored: false,
  })
}

/**
 * 规则 1：诊断报告的整体结论。
 *
 * `status === 'evidence_insufficient'` 是合法终止（`legalStop: true`），
 * 调用方据此渲染 warning/info 语义的 Alert，并**不得**把空根因当结论。
 */
export function reportState(status, escalated) {
  const s = String(status || '').trim().toLowerCase()
  const isEscalated = !!escalated

  if (s === STATUS_EVIDENCE_INSUFFICIENT) {
    return WARN('证据不足，已转人工复核', {
      legalStop: true,
      escalated: isEscalated,
      detail: '合法终止：不计成功，也不计幻觉；未通过证据审计时后端不给出根因。',
    })
  }
  if (isEscalated) {
    return INFO('已升级人工复核', {
      legalStop: false,
      escalated: true,
      detail: '本次诊断未形成可自动采纳的结论，已转人工。',
    })
  }
  if (s) {
    return OK(`诊断完成（${status}）`, { legalStop: false, escalated: false })
  }
  return INFO('报告未标注状态', {
    legalStop: false,
    escalated: false,
    detail: '后端仅在证据不足时给出 status，未标注不代表成功或失败，请以根因与证据为准。',
  })
}

/**
 * 证据审计结果。审计未通过 ⇒ 根因主张无证据支撑 ⇒ 转人工（而非"报错"）。
 */
export function auditState(audit) {
  if (!audit || typeof audit !== 'object') {
    return INFO('未获取到证据审计结果', { passed: null, unsupportedCount: 0 })
  }
  const unsupported = Array.isArray(audit.unsupported) ? audit.unsupported : []
  const unsupportedCount = Number.isFinite(audit.unsupported_count)
    ? audit.unsupported_count
    : unsupported.length

  if (audit.passed === true) {
    return OK('证据审计通过：根因与修复主张均有证据支撑', {
      passed: true,
      routeToHuman: !!audit.route_to_human,
      unsupportedCount: 0,
      reason: audit.reason || '',
    })
  }
  return WARN('证据审计未通过：存在无证据支撑的主张，已转人工复核', {
    passed: false,
    routeToHuman: !!audit.route_to_human,
    unsupportedCount,
    reason: audit.reason || '',
  })
}

/**
 * 规则 5：推荐修复只给建议、绝不执行。
 *
 * 后端固定回传 `requires_approval: true` 与 `execute_via`；这里再钉一道，
 * 避免前端哪天把它渲染成一个"一键修复"按钮。
 */
export function fixApprovalState(fix) {
  const spec = fix && typeof fix === 'object' ? fix.spec : ''
  const executeVia = (fix && fix.execute_via) || OAM_EXECUTE_VIA

  if (!spec) {
    return INFO('本次诊断未给出修复建议', {
      present: false,
      requiresApproval: true,
      executeVia,
      riskLevel: '',
    })
  }
  return WARN('本接口只产出建议，不执行任何变更；执行须走既有审批链', {
    present: true,
    requiresApproval: true,
    badge: '需人工审批',
    riskLevel: fix.risk_level || 'unknown',
    executeVia,
    note: (fix && fix.note) || '',
  })
}

/**
 * 规则 6：`root_causes_missing` 非空 ⇒ 成绩单必须显著标注覆盖缺口。
 *
 * 后端在 `root_causes_missing` 非空时会注入 `coverage.warning`；
 * 这里优先用它，缺失时自己兜底拼一条，**永不**返回空串（空串 = "没缺口"，
 * 那就等于把缺口藏起来了）。
 */
export function coverageWarningText(coverage) {
  if (!coverage || typeof coverage !== 'object') return null
  const missing = Array.isArray(coverage.root_causes_missing)
    ? coverage.root_causes_missing
    : []
  if (!missing.length) return null
  if (coverage.warning) return String(coverage.warning)
  return '该成绩不覆盖以下根因：' + missing.join('、')
}

/**
 * 规则 6：覆盖度与成功率同屏所需的覆盖度摘要。
 *
 * 覆盖度缺失时报 `present: false` —— 调用方仍要渲染成绩单，
 * 但必须挂上"按设计视为未完成评测"的警告，而不是把成绩单藏起来。
 */
export function coverageState(coverage) {
  if (!coverage || typeof coverage !== 'object') {
    return WARN('该成绩未包含覆盖度，按设计视为未完成评测', {
      present: false,
      covered: 0,
      missing: 0,
      ratio: null,
      labelSpaceSize: 0,
      warning: null,
    })
  }

  const covered = Array.isArray(coverage.root_causes_covered) ? coverage.root_causes_covered : []
  const missing = Array.isArray(coverage.root_causes_missing) ? coverage.root_causes_missing : []
  const ratio = Number.isFinite(coverage.covered_ratio) ? coverage.covered_ratio : null
  const labelSpaceSize = Number(coverage.label_space_size) || 0
  const warning = coverageWarningText(coverage)

  const base = {
    present: true,
    covered: covered.length,
    missing: missing.length,
    coveredList: covered,
    missingList: missing,
    ratio,
    labelSpaceSize,
    warning,
  }

  if (!labelSpaceSize) {
    return INFO('本次评测没有可评测的用例，标签空间为空（成绩不具参考意义）', base)
  }
  if (warning) {
    return WARN(warning, base)
  }
  return OK('覆盖完整：本次成绩覆盖标签空间内的全部根因', base)
}

/**
 * 规则 3 / 4：把后端错误码映射成业务语义文案。
 *
 * - `COVERAGE_MISSING` 是 HTTP 500，但**不是**"服务器错误"；
 * - 404 一律写"记录不存在或无权访问"，**不**区分越权与不存在；
 * - `NOT_IMPLEMENTED`(501) 是后端刻意不从 HTTP 暴露（走管理命令）。
 *
 * @param {string|number} code  后端 `code`，或 HTTP 状态码（axios 兜底会填状态码）
 * @param {number} status       HTTP 状态码，可缺省
 */
export function describeError(code, status) {
  const c = String(code == null ? '' : code).trim().toUpperCase()
  const s = Number(status) || 0

  if (c === 'COVERAGE_MISSING') return WARN('该评测记录缺少覆盖度数据，按设计视为未完成评测')
  if (c === MUTATION_FORBIDDEN) return WARN('变更类工具不在策略动作空间内，调用一律返回 MUTATION_FORBIDDEN')
  if (c === STATUS_EVIDENCE_INSUFFICIENT) return WARN('证据不足，已转人工复核')

  if (c === 'NOT_FOUND' || s === 404) {
    return INFO('记录不存在或无权访问', { ambiguous: true })
  }
  if (c === 'PERMISSION_DENIED' || s === 403) {
    return WARN('当前账号没有执行该操作的权限', { ambiguous: false })
  }
  if (c === 'NOT_IMPLEMENTED' || s === 501) {
    return INFO('后端刻意未从 HTTP 暴露该操作，请使用对应管理命令', { code: c || 'NOT_IMPLEMENTED' })
  }
  if (c === 'DISABLED' || s === 409) {
    return WARN('OAM 未启用（配置项 OAM_ENABLED）')
  }
  if (s === 400 || ['BAD_REQUEST', 'VALIDATION_FAILED', 'INVALID_LABEL', 'SNAPSHOT_RANGE'].includes(c)) {
    return ERR('请求参数不合法，请检查入参')
  }
  if (c === 'DIAGNOSE_FAILED') {
    return ERR('诊断执行失败，请查看后端日志')
  }
  if (!s) {
    return ERR('网络异常或服务不可用，请稍后重试', { network: true })
  }
  return ERR(`请求失败（${s}）`)
}

/**
 * 单轮工具调用的展示语义。
 *
 * `MUTATION_FORBIDDEN` 是策略边界（warning）而不是调用失败（error），
 * 其余工具错误码才算调用失败。
 */
export function toolOutcomeState(step) {
  const code = step && step.tool_error_code ? String(step.tool_error_code) : ''
  if (code === MUTATION_FORBIDDEN) {
    return WARN(`${MUTATION_FORBIDDEN}：变更类工具不在策略动作空间内`, { code })
  }
  if (code) {
    return ERR(`工具返回错误码 ${code}`, { code })
  }
  if (step && step.observation) {
    return OK('调用成功', { code: '' })
  }
  return INFO('无返回', { code: '' })
}

/**
 * KB 新鲜度：超过阈值即为陈旧（warning，不是不存在）。
 */
export function stalenessState(days, thresholdDays) {
  const d = days == null || days === '' ? NaN : Number(days)
  // 注意：必须先判空再 Number() —— Number(null) 是 0、Number('') 也是 0，
  // 阈值缺失会被当成"阈值 0 天"，于是每篇文档都被误判成陈旧。
  const t = thresholdDays == null || thresholdDays === '' ? NaN : Number(thresholdDays)
  if (!Number.isFinite(d)) return INFO('未知', { stale: false, days: null })
  if (!Number.isFinite(t)) return INFO(`${d} 天`, { stale: false, days: d })
  if (d > t) {
    return WARN(`${d} 天（已超过 ${t} 天新鲜度阈值）`, { stale: true, days: d, threshold: t })
  }
  return OK(`${d} 天`, { stale: false, days: d, threshold: t })
}

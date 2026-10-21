/**
 * SQL 性能展示语义单测（utils/sqlPerf.js 顶部注释的 5 条）。
 */
import { describe, it, expect } from 'vitest'
import {
  basisNotice, breakdownSegments, explainNotice, fmtMs, planRows, severityMeta, statusMeta,
} from '../utils/sqlPerf'

describe('breakdownSegments（语义 1：CPU 为 0 = 未采集）', () => {
  it('CPU 已采集时拆成锁 / CPU / 其他三段', () => {
    const r = breakdownSegments({ available: true, avg_ms: 100,
      breakdown: { lock_ms: 50, cpu_ms: 20, other_ms: 30, cpu_known: true } })
    expect(r.segments.map((s) => s.key)).toEqual(['lock', 'cpu', 'other'])
    expect(r.note).toBeNull()
  })
  it('CPU 未采集时不把全部耗时算作"其他"，并给出开启方法', () => {
    const r = breakdownSegments({ available: true, avg_ms: 88.75,
      breakdown: { lock_ms: 0, cpu_ms: null, other_ms: null, cpu_known: false,
                   cpu_hint: '需开启 events_statements_cpu' } })
    expect(r.segments.map((s) => s.key)).toEqual(['lock', 'rest'])
    expect(r.segments[1].label).toContain('未采集')
    expect(r.segments.find((s) => s.key === 'other')).toBeUndefined()
    expect(r.note).toContain('events_statements_cpu')
  })
  it('PG / Oracle 无法拆分时只给总耗时', () => {
    const r = breakdownSegments({ available: true, avg_ms: 5,
      breakdown: { lock_ms: null, cpu_ms: null, other_ms: null, cpu_known: false },
      note: '无法拆分' })
    expect(r.segments).toHaveLength(1)
  })
  it('无执行记录', () => {
    expect(breakdownSegments({ available: false }).segments).toEqual([])
  })
})

describe('basisNotice（语义 2 / 3）', () => {
  it('分布式未按 set 采集的告警优先且为 error', () => {
    expect(basisNotice({ basis: 'window', basis_warning: '不可信' })).toEqual({ type: 'error', text: '不可信' })
  })
  it('累计值口径必须提示', () => {
    expect(basisNotice({ basis: 'cumulative' }).type).toBe('warning')
  })
  it('正常时间窗数据不提示', () => {
    expect(basisNotice({ basis: 'window' })).toBeNull()
  })
})

describe('explainNotice（语义 4）', () => {
  it('截断时提示耗时为下限', () => {
    expect(explainNotice({ parsed: true, truncated: true, truncated_note: '下限' }).text).toBe('下限')
  })
  it('未截断不提示', () => {
    expect(explainNotice({ parsed: true, truncated: false })).toBeNull()
  })
})

describe('其他', () => {
  it('亚毫秒不显示成 0', () => {
    expect(fmtMs(0.085)).toBe('0.085 ms')
    expect(fmtMs(88.75)).toBe('88.8 ms')
    expect(fmtMs(6500)).toBe('6.50 s')
    expect(fmtMs(null)).toBe('-')
  })
  it('新增的语义提示（语义 5）', () => {
    expect(statusMeta('new').hint).toContain('完全没有出现')
    expect(statusMeta('regressed').label).toBe('退化')
  })
  it('未知严重度回落为提示', () => {
    expect(severityMeta('xx').label).toBe('提示')
  })
  it('算子树转表格并计算占比', () => {
    const rows = planRows({ op: 'Limit', self_ms: 0, total_ms: 100,
      children: [{ op: 'Table scan', self_ms: 84, total_ms: 84, children: [] }] }, 100)
    expect(rows[0].children[0].op).toBe('Table scan')
    expect(rows[0].children[0].share).toBeCloseTo(0.84)
  })
})

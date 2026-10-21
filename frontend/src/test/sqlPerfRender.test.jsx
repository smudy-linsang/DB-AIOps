/**
 * SQL 详情「耗时构成」与「SQL 洞察」的渲染冒烟：用后端真实响应形状（本地 MySQL + PostgreSQL
 * 端到端得到的字段）喂给组件，确认不白屏、关键语义真的渲染出来。
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('echarts-for-react/lib/core', () => ({ default: () => null }))

const DETAIL = {
  digest: 'a'.repeat(32), db_type: 'tdsql', sql_text_sample: 'SELECT id, amount FROM orders WHERE user_id = 11',
  trend: { bucket_sec: 300, series: {} }, ash_breakdown: {}, plans: [], plan_changed_at: null,
  advisor: { index_suggestions: [] },
  advice: [{ code: 'I-01', severity: 'high', category: 'index', title: '为表 orders 建立复合索引',
             detail: '按 等值列 → 排序/分组列 → 范围列', evidence: { columns: ['user_id', 'created_at'] },
             ddl: 'ALTER TABLE `orders` ADD INDEX `idx_user_id_created_at` (`user_id`, `created_at`);',
             rewrite: null, expected: '消除全表扫描与排序' }],
  profile: { available: true, exec: 4, total_ms: 355, avg_ms: 88.75, is_slow: false,
             breakdown: { lock_ms: 0, cpu_ms: null, other_ms: null, cpu_known: false,
                          cpu_hint: '需开启 events_statements_cpu' },
             per_exec: { rows_examined: 400005, rows_sent: 5.8, sort_rows: 5.8, sort_merge_passes: 0 },
             ratios: { scan_efficiency: 69000, full_scan_share: 1, full_join_share: 0, no_index_share: 1,
                       tmp_disk_share: null, error_share: 0, lock_share: 0 },
             findings: [{ code: 'SCAN_INEFFICIENT', severity: 'high', text: '每次执行平均扫描 400005 行' }] },
  set_distribution: { sets: [{ set_name: 'set_3', exec: 3, total_ms: 300, avg_ms: 100, share: 0.85,
                               rows_examined_per_exec: 1 }], set_count: 2, hottest_set: 'set_3',
                      skew: 1.7, is_skewed: false },
  basis_warning: '该分布式实例的 SQL 统计没有按 set 采集',
  explain_analyze: { enabled: true, timeout_ms: 5000, target_set: 'set_3', has_literal_sample: true },
  related_incidents: [],
}

vi.mock('../services/api', () => ({
  perfAPI: {
    sqlDetail: vi.fn(() => Promise.resolve({ data: DETAIL })),
    explainAnalyze: vi.fn(() => Promise.resolve({ data: {
      tree: { op: 'Limit', self_ms: 0, total_ms: 112, children: [
        { op: 'Table scan on orders', self_ms: 94.6, total_ms: 94.6, actual_rows: 400000, est_rows: 395784, loops: 1, children: [] }] },
      summary: { parsed: true, total_ms: 112, truncated: true, truncated_note: '已在超时处截断', hotspots: [] } } })),
    sqlRegression: vi.fn(() => Promise.resolve({ data: {
      counts: { regressed: 1, new: 1, improved: 0, gone: 0, stable: 3 },
      thresholds: { avg_ratio: 1.5, min_delta_ms: 5 },
      items: [{ digest: 'r1', status: 'regressed', sql_text: 'SELECT slowdown', avg_ratio: 5,
                current: { avg_ms: 500, exec: 10 }, baseline: { avg_ms: 100, exec: 10 } }] } })),
    sqlPeriodic: vi.fn(() => Promise.resolve({ data: { items: [] } })),
    sqlPlan: vi.fn(),
  },
  sqlMonitoringAPI: {
    list: vi.fn(() => Promise.resolve({ data: { basis: 'cumulative', slow_queries: [
      { digest: 'd1', query: 'SELECT 1', exec_count: 3, total_time_sec: 0.3, avg_ms: 100, is_slow: false, findings: [] }] } })),
  },
}))

import SqlDetailPanel from '../components/perf/SqlDetailPanel'
import SqlInsightsCard from '../components/perf/SqlInsightsCard'

describe('SqlDetailPanel 耗时构成', () => {
  it('渲染画像、CPU 未采集提示、set 分布、数据不可信告警与结构化建议', async () => {
    render(<MemoryRouter><SqlDetailPanel configId={1} digest={'a'.repeat(32)} /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('该分布式实例的 SQL 统计没有按 set 采集')).toBeInTheDocument())
    expect(screen.getByText('需开启 events_statements_cpu')).toBeInTheDocument()
    expect(screen.getByText('每次执行平均扫描 400005 行')).toBeInTheDocument()
    expect(screen.getByText('按 set 分布')).toBeInTheDocument()
    fireEvent.click(screen.getByText(/执行（在 set_3 上）/))
    await waitFor(() => expect(screen.getByText('Table scan on orders')).toBeInTheDocument())
    expect(screen.getByText('已在超时处截断')).toBeInTheDocument()
  })
})

describe('SqlInsightsCard', () => {
  it('耗时排行显示累计口径提示；环比显示退化项', async () => {
    render(<SqlInsightsCard configId={1} range={{ window: '1h' }} />)
    await waitFor(() => expect(screen.getByText('以下为自计数器清零以来的累计值，不代表所选时间窗')).toBeInTheDocument())
    fireEvent.click(screen.getByText('环比退化'))
    await waitFor(() => expect(screen.getByText('SELECT slowdown')).toBeInTheDocument())
    expect(screen.getByText('5×')).toBeInTheDocument()
  })
})

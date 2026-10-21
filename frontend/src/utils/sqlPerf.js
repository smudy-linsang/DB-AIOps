/**
 * SQL 性能展示语义（TDSQL_SLOWSQL_DESIGN.md §4 ~ §7）。
 *
 * 与 oamSemantics.js 同样的做法：容易被 UI 表达错的语义抽成纯函数，JSX 只渲染不判断。
 * 下面几条一旦表达错就是在误导 DBA：
 *
 *   1. CPU 时间为 0 = "未采集"，不是"没用 CPU"；未采集时不能把全部耗时算作"其他"
 *   2. basis='cumulative' 的数据是自计数器清零以来的累计值，不是所选时间窗
 *   3. basis_warning（TDSQL 分布式未按 set 采集）必须醒目展示，数据不可信
 *   4. EXPLAIN ANALYZE 被超时截断时，算子耗时是下限
 *   5. 环比"新增"的语义是基线窗口里完全没有出现过
 */

export const SEVERITY_META = {
  high: { color: 'red', alert: 'error', label: '高' },
  medium: { color: 'orange', alert: 'warning', label: '中' },
  low: { color: 'blue', alert: 'info', label: '低' },
  info: { color: 'default', alert: 'info', label: '提示' },
};

export function severityMeta(sev) {
  return SEVERITY_META[sev] || SEVERITY_META.info;
}

export const STATUS_META = {
  regressed: { color: 'red', label: '退化' },
  new: { color: 'purple', label: '新增', hint: '基线窗口中完全没有出现过' },
  improved: { color: 'green', label: '改善' },
  gone: { color: 'default', label: '消失' },
  stable: { color: 'default', label: '稳定' },
};

export function statusMeta(status) {
  return STATUS_META[status] || { color: 'default', label: status || '-' };
}

/** 毫秒数格式化：亚毫秒保留 3 位，避免 0.08ms 显示成 0 */
export function fmtMs(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '-';
  if (ms < 1) return `${ms.toFixed(3)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/**
 * 语义 1：耗时构成分段。返回 { segments: [{key,label,value}], note }。
 * CPU 未采集时只给"锁等待"与"非锁部分（未拆分）"两段，并带上开启方法。
 */
export function breakdownSegments(profile) {
  if (!profile || !profile.available) {
    return { segments: [], note: profile?.note || '窗口内无执行记录' };
  }
  const b = profile.breakdown || {};
  const avg = profile.avg_ms || 0;
  const lock = b.lock_ms || 0;
  if (b.lock_ms === null || b.lock_ms === undefined) {
    return { segments: [{ key: 'total', label: '平均耗时（该库类型无法拆分）', value: avg }],
             note: profile.note || null };
  }
  if (b.cpu_known) {
    return {
      segments: [
        { key: 'lock', label: '锁等待', value: lock },
        { key: 'cpu', label: 'CPU', value: b.cpu_ms || 0 },
        { key: 'other', label: 'IO / 等待 / 网络', value: b.other_ms || 0 },
      ],
      note: null,
    };
  }
  return {
    segments: [
      { key: 'lock', label: '锁等待', value: lock },
      { key: 'rest', label: '非锁部分（CPU 未采集，无法再拆）', value: Math.max(avg - lock, 0) },
    ],
    note: b.cpu_hint || 'CPU 时间未采集',
  };
}

/** 语义 2 / 3：数据口径提示。返回 null 或 { type, text } */
export function basisNotice({ basis, warning, basis_warning: basisWarning } = {}) {
  if (basisWarning) return { type: 'error', text: basisWarning };
  if (warning) return { type: basis === 'cumulative' ? 'warning' : 'error', text: warning };
  if (basis === 'cumulative') {
    return { type: 'warning', text: '以下为自计数器清零以来的累计值，不代表所选时间窗' };
  }
  return null;
}

/** 语义 4 */
export function explainNotice(summary) {
  if (!summary) return null;
  if (!summary.parsed) return { type: 'warning', text: '计划未能解析，请查看原始输出' };
  if (summary.truncated) return { type: 'warning', text: summary.truncated_note || '计划被超时截断，耗时为下限' };
  return null;
}

/** 算子树 → antd Table 树形数据（带 key 与占比） */
export function planRows(node, total, path = '0') {
  if (!node) return [];
  const row = {
    key: path,
    op: node.op,
    self_ms: node.self_ms ?? null,
    total_ms: node.total_ms ?? null,
    share: total && node.self_ms != null ? node.self_ms / total : null,
    actual_rows: node.actual_rows,
    est_rows: node.est_rows,
    loops: node.loops,
    never: !!node.never,
  };
  const kids = (node.children || []).flatMap((c, i) => planRows(c, total, `${path}-${i}`));
  if (kids.length) row.children = kids;
  return [row];
}

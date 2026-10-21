import React, { useEffect, useState } from 'react';
import { Alert, Card, Segmented, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { perfAPI, sqlMonitoringAPI } from '../../services/api';
import { basisNotice, fmtMs, statusMeta } from '../../utils/sqlPerf';

const { Text } = Typography;

const WINDOW_TO_TIME = { '30m': '30m', '1h': '1h', '6h': '6h', '24h': '24h', '7d': '7d' };

function SqlCell({ text, digest, onOpenSql }) {
  return (
    <a onClick={() => digest && onOpenSql?.(digest)}>
      <Text code style={{ fontSize: 11 }}>{text || digest || '-'}</Text>
    </a>
  );
}

/**
 * SQL 洞察（TDSQL_SLOWSQL_DESIGN.md §4、§7）：
 * 耗时排行（sql_stat 时间窗）/ 环比退化（自动基线）/ 周期 SQL（逐次运行对比）。
 * 点击任一行打开下方 SQL 详情。
 */
export default function SqlInsightsCard({ configId, range, refreshKey, onOpenSql }) {
  const [mode, setMode] = useState('top');
  const [baseline, setBaseline] = useState('day');
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [top, setTop] = useState(null);
  const [reg, setReg] = useState(null);
  const [per, setPer] = useState(null);
  const windowKey = range?.window || '1h';

  useEffect(() => {
    if (!configId) return;
    let alive = true;
    setLoading(true);
    const req = mode === 'top'
      ? sqlMonitoringAPI.list(configId, { time: WINDOW_TO_TIME[windowKey] || '1h', limit: 50 })
          .then((r) => alive && setTop(r.data || r))
      : mode === 'regression'
        ? perfAPI.sqlRegression(configId, { ...(range || {}), baseline })
            .then((r) => alive && setReg(r.data))
        : perfAPI.sqlPeriodic(configId, { days }).then((r) => alive && setPer(r.data));
    req.catch((e) => alive && message.error(`加载失败: ${e.response?.data?.message || e.message}`))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [configId, mode, baseline, days, windowKey, refreshKey]);

  const topNotice = top && basisNotice(top);
  const regNotice = reg && (basisNotice({ basis_warning: reg.basis_warning })
    || (reg.baseline_note ? { type: 'warning', text: reg.baseline_note } : null));

  return (
    <Card size="small" style={{ marginTop: 8 }}
      title={<Segmented size="small" value={mode} onChange={setMode} options={[
        { label: '耗时排行', value: 'top' },
        { label: '环比退化', value: 'regression' },
        { label: '周期 SQL', value: 'periodic' },
      ]} />}
      extra={mode === 'regression' ? (
        <Select size="small" value={baseline} onChange={setBaseline} style={{ width: 150 }} options={[
          { label: '对比昨日同时段', value: 'day' },
          { label: '对比上周同时段', value: 'week' },
          { label: '对比上一窗口', value: 'prev' },
        ]} />
      ) : mode === 'periodic' ? (
        <Select size="small" value={days} onChange={setDays} style={{ width: 110 }}
                options={[7, 14, 30].map((d) => ({ label: `近 ${d} 天`, value: d }))} />
      ) : null}>
      {mode === 'top' && (
        <>
          {topNotice && <Alert type={topNotice.type} showIcon message={topNotice.text} style={{ marginBottom: 6 }} />}
          <Table size="small" rowKey={(r) => r.digest || r.query} loading={loading}
            dataSource={top?.slow_queries || []} pagination={{ pageSize: 10, size: 'small' }}
            columns={[
              { title: 'SQL', dataIndex: 'query', ellipsis: true,
                render: (v, r) => <SqlCell text={v} digest={r.digest} onOpenSql={onOpenSql} /> },
              { title: '执行次数', dataIndex: 'exec_count', width: 90 },
              { title: '总耗时', dataIndex: 'total_time_sec', width: 100, render: (v) => fmtMs((v || 0) * 1000) },
              { title: '平均耗时', dataIndex: 'avg_ms', width: 110,
                render: (v, r) => <Space size={4}>{fmtMs(v)}{r.is_slow && <Tag color="red">慢</Tag>}</Space> },
              { title: '结论', dataIndex: 'findings', width: 200,
                render: (fs) => (fs || []).slice(0, 2).map((f) => (
                  <Tooltip key={f.code} title={f.text}><Tag>{f.code}</Tag></Tooltip>)) },
              { title: '热点 set', dataIndex: 'hottest_set', width: 120,
                render: (v, r) => (v ? <Space size={2}>{v}{r.set_skew >= 2 && <Tag color="orange">{r.set_skew}×</Tag>}</Space> : '-') },
            ]} />
        </>
      )}
      {mode === 'regression' && (
        <>
          {regNotice && <Alert type={regNotice.type} showIcon message={regNotice.text} style={{ marginBottom: 6 }} />}
          {reg && (
            <Space style={{ marginBottom: 6 }} wrap>
              {['regressed', 'new', 'improved', 'gone'].map((k) => (
                <Tag key={k} color={statusMeta(k).color}>{statusMeta(k).label} {reg.counts?.[k] ?? 0}</Tag>))}
              <Text type="secondary" style={{ fontSize: 12 }}>
                判定：平均耗时 ≥ 基线 × {reg.thresholds?.avg_ratio} 且差值 ≥ {reg.thresholds?.min_delta_ms}ms，或总耗时翻倍
              </Text>
            </Space>
          )}
          <Table size="small" rowKey="digest" loading={loading} dataSource={reg?.items || []}
            pagination={{ pageSize: 10, size: 'small' }}
            columns={[
              { title: '状态', dataIndex: 'status', width: 80,
                render: (v) => <Tooltip title={statusMeta(v).hint}><Tag color={statusMeta(v).color}>{statusMeta(v).label}</Tag></Tooltip> },
              { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
                render: (v, r) => <SqlCell text={v} digest={r.digest} onOpenSql={onOpenSql} /> },
              { title: '平均耗时（基线 → 当前）', width: 200,
                render: (_, r) => `${fmtMs(r.baseline?.avg_ms)} → ${fmtMs(r.current?.avg_ms)}` },
              { title: '倍数', dataIndex: 'avg_ratio', width: 70, render: (v) => (v ? `${v}×` : '-') },
              { title: '执行次数（基线 → 当前）', width: 170,
                render: (_, r) => `${r.baseline?.exec ?? 0} → ${r.current?.exec ?? 0}` },
              { title: '计划变更', dataIndex: 'plan_changed_at', width: 110,
                render: (v) => (v ? <Tag color="red">{String(v).slice(5, 16)}</Tag> : '-') },
            ]} />
        </>
      )}
      {mode === 'periodic' && (
        <Table size="small" rowKey="digest" loading={loading} dataSource={per?.items || []}
          pagination={{ pageSize: 10, size: 'small' }}
          locale={{ emptyText: '未识别到日周期 SQL（活跃小时 ≤ 6 个且承担 ≥ 80% 执行、≥ 70% 的天里出现）' }}
          columns={[
            { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
              render: (v, r) => <SqlCell text={v} digest={r.digest} onOpenSql={onOpenSql} /> },
            { title: '运行时段', dataIndex: 'hours', width: 110, render: (hs) => (hs || []).map((h) => `${h}点`).join(' ') },
            { title: '出现天数', width: 90, render: (_, r) => `${r.days_seen}/${r.days}` },
            { title: '最近一次平均耗时', width: 140, render: (_, r) => fmtMs(r.latest?.avg_ms) },
            { title: '历次中位数', dataIndex: 'median_avg_ms', width: 110, render: fmtMs },
            { title: '对比中位数', dataIndex: 'latest_vs_median', width: 100,
              render: (v) => (v == null ? '-' : <Tag color={v >= 1.5 ? 'red' : v <= 0.67 ? 'green' : 'default'}>{v}×</Tag>) },
          ]} />
      )}
    </Card>
  );
}

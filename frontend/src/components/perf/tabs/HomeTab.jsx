import React, { useCallback, useEffect, useState } from 'react';
import { Card, Col, Row, Spin, Table, Typography, message } from 'antd';
import { perfAPI } from '../../../services/api';
import AasChart from '../AasChart';
import BreakdownBar from '../BreakdownBar';
import WaitClassTag from '../WaitClassTag';
import { waitClassLabel } from '../waitClassMeta';
import { withAlive, fmtPerfError } from '../useSafeAsync';

const { Text } = Typography;

/** Tab1 性能主页 (phase7/30 §2): 卡片行 + AAS 堆叠 + 联动双 Top */
export default function HomeTab({ configId, range, onOpenSql, onOpenSession, refreshKey }) {
  const [aas, setAas] = useState(null);
  const [subRange, setSubRange] = useState(null); // dataZoom 选窗
  const [topSql, setTopSql] = useState([]);
  const [topSess, setTopSess] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => { setSubRange(null); }, [range, configId]);

  // BUG-133: 用 withAlive 保证只有本次 effect 存活时才写 state，
  // 避免自动刷新/切窗时慢的旧响应覆盖新数据
  useEffect(() => {
    if (!configId) return undefined;
    setLoading(true);
    return withAlive((alive) => {
      perfAPI.aas(configId, { ...range, by: 'wait_class' })
        .then((r) => { if (alive()) setAas(r.data); })
        .catch((e) => { if (alive()) message.error(fmtPerfError('AAS 加载', e)); })
        .finally(() => { if (alive()) setLoading(false); });
    });
  }, [configId, range, refreshKey]);

  useEffect(() => {
    if (!configId) return undefined;
    const win = subRange || range;
    return withAlive((alive) => {
      perfAPI.topActivity(configId, { ...win, dim: 'sql' })
        .then((r) => { if (alive()) setTopSql(r.data?.rows || []); })
        .catch(() => { if (alive()) setTopSql([]); });
      perfAPI.topActivity(configId, { ...win, dim: 'session' })
        .then((r) => { if (alive()) setTopSess(r.data?.rows || []); })
        .catch(() => { if (alive()) setTopSess([]); });
    });
  }, [configId, range, subRange, refreshKey]);

  const onRangeChange = useCallback((from, to) => setSubRange({ from, to }), []);

  const totals = aas?.totals || {};
  const cards = [
    { label: 'DB Time', value: `${(totals.db_time_sec || 0).toFixed(0)}s` },
    { label: '平均 AAS', value: totals.avg_aas ?? '-' },
    { label: '峰值 AAS', value: totals.max_aas ?? '-' },
    { label: 'CPU 核数', value: aas?.cpu_cores ?? '未知' },
    { label: 'Top 等待类', value: topClass(aas) },
  ];

  return (
    <Spin spinning={loading}>
      <Row gutter={8} style={{ marginBottom: 8 }}>
        {cards.map((c) => (
          <Col flex={1} key={c.label}>
            <Card size="small" styles={{ body: { padding: '8px 12px', textAlign: 'center' } }}>
              <Text type="secondary" style={{ fontSize: 12 }}>{c.label}</Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{c.value}</div>
            </Card>
          </Col>
        ))}
      </Row>
      <Card size="small" title="平均活动会话 (AAS) — 按等待类"
            extra={subRange && <a onClick={() => setSubRange(null)}>清除选窗</a>}>
        <AasChart series={aas?.series || []} cpuCores={aas?.cpu_cores}
                  onRangeChange={onRangeChange} />
      </Card>
      <Row gutter={8} style={{ marginTop: 8 }}>
        <Col span={12}>
          <Card size="small" title={`Top SQL${subRange ? ' (选窗)' : ''}`}>
            <Table size="small" rowKey="key" pagination={false} dataSource={topSql}
              onRow={(r) => ({ onClick: () => r.key !== '(null)' && onOpenSql?.(r.key),
                               style: { cursor: 'pointer' } })}
              columns={[
                { title: '活动占比', width: 170,
                  render: (_, r) => <BreakdownBar breakdown={r.breakdown} pct={r.pct} /> },
                { title: '%', dataIndex: 'pct', width: 56, render: (v) => `${v}%` },
                { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
                  render: (v, r) => <Text code style={{ fontSize: 11 }}>{v || r.key}</Text> },
                { title: '会话', dataIndex: 'sessions', width: 56 },
              ]} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title={`Top 会话${subRange ? ' (选窗)' : ''}`}>
            <Table size="small" rowKey="key" pagination={false} dataSource={topSess}
              onRow={(r) => ({ onClick: () => onOpenSession?.(r), style: { cursor: 'pointer' } })}
              columns={[
                { title: '活动占比', width: 170,
                  render: (_, r) => <BreakdownBar breakdown={r.breakdown} pct={r.pct} /> },
                { title: '%', dataIndex: 'pct', width: 56, render: (v) => `${v}%` },
                { title: '会话', dataIndex: 'key', width: 90, render: (v) => <Text code>{v}</Text> },
                { title: '用户', dataIndex: 'user_name', width: 90 },
                { title: '最后 SQL', dataIndex: 'sql_text', ellipsis: true,
                  render: (v) => <Text style={{ fontSize: 11 }}>{v || '-'}</Text> },
              ]} />
          </Card>
        </Col>
      </Row>
    </Spin>
  );
}

function topClass(aas) {
  if (!aas?.series?.length) return '-';
  let best = null, bestSum = -1;
  for (const s of aas.series) {
    const sum = (s.points || []).reduce((a, [, v]) => a + (v || 0), 0);
    if (sum > bestSum) { bestSum = sum; best = s.key; }
  }
  return waitClassLabel(best);
}

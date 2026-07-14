import React, { useCallback, useEffect, useState } from 'react';
import { Card, Radio, Space, Table, Tag, Typography, message } from 'antd';
import { perfAPI } from '../../../services/api';
import AasChart from '../AasChart';
import BreakdownBar from '../BreakdownBar';

const { Text } = Typography;

const DIMS = [
  { value: 'sql', label: 'SQL' }, { value: 'session', label: '会话' },
  { value: 'user', label: '用户' }, { value: 'module', label: '模块' },
  { value: 'wait_event', label: '等待事件' }, { value: 'object', label: '对象' },
];

/** Tab2 顶级活动 (phase7/30 §3): brush 框选 + 维度切换 Top 表 */
export default function TopActivityTab({ configId, range, onOpenSql, refreshKey }) {
  const [aas, setAas] = useState(null);
  const [dim, setDim] = useState('sql');
  const [sel, setSel] = useState(null);
  const [rows, setRows] = useState([]);

  useEffect(() => { setSel(null); }, [range, configId]);

  useEffect(() => {
    if (!configId) return;
    perfAPI.aas(configId, { ...range, by: 'wait_class' })
      .then((r) => setAas(r.data))
      .catch((e) => message.error(`AAS 加载失败: ${e.message}`));
  }, [configId, range, refreshKey]);

  useEffect(() => {
    if (!configId) return;
    perfAPI.topActivity(configId, { ...(sel || range), dim, limit: 15 })
      .then((r) => setRows(r.data.rows || [])).catch(() => setRows([]));
  }, [configId, range, sel, dim, refreshKey]);

  const onBrush = useCallback((from, to) => setSel({ from, to }), []);

  return (
    <div>
      <Card size="small" title="AAS (工具栏框选任意时间窗)"
        extra={sel && (
          <Space>
            <Tag color="blue">已选 {sel.from.slice(11, 19)} – {sel.to.slice(11, 19)}</Tag>
            <a onClick={() => setSel(null)}>清除</a>
          </Space>
        )}>
        <AasChart series={aas?.series || []} cpuCores={aas?.cpu_cores}
                  height={360} brush onRangeChange={onBrush} />
      </Card>
      <Card size="small" style={{ marginTop: 8 }}
        title={<Radio.Group size="small" options={DIMS} optionType="button"
                            value={dim} onChange={(e) => setDim(e.target.value)} />}>
        <Table size="small" rowKey="key" pagination={false} dataSource={rows}
          onRow={(r) => ({
            onClick: () => dim === 'sql' && r.key !== '(null)' && onOpenSql?.(r.key),
            style: { cursor: dim === 'sql' ? 'pointer' : 'default' },
          })}
          columns={[
            { title: '活动占比', width: 220,
              render: (_, r) => <BreakdownBar breakdown={r.breakdown} pct={r.pct} /> },
            { title: '%', dataIndex: 'pct', width: 64, render: (v) => `${v}%` },
            { title: '活动秒', dataIndex: 'active_sec', width: 80 },
            {
              title: DIMS.find((d) => d.value === dim)?.label,
              render: (_, r) => (
                <Text code style={{ fontSize: 11 }}>
                  {dim === 'sql' ? (r.sql_text || r.key) : r.key}
                </Text>
              ), ellipsis: true,
            },
            ...(dim === 'session'
              ? [{ title: '用户', dataIndex: 'user_name', width: 100 }]
              : []),
            ...(dim === 'sql'
              ? [{ title: '会话数', dataIndex: 'sessions', width: 70 },
                 { title: '计划数', dataIndex: 'plan_count', width: 70 }]
              : []),
          ]} />
      </Card>
    </div>
  );
}

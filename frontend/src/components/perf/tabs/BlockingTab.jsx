import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, DatePicker, Radio, Space, Table, Tag, Typography, message,
} from 'antd';
import { ReloadOutlined, StopOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import * as echarts from 'echarts/core';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { perfAPI } from '../../../services/api';
import WaitClassTag from '../WaitClassTag';
import KillModal from '../KillModal';

const { Text } = Typography;

/** Tab5 阻塞分析 (phase7/30 §6): 实时/回放阻塞树 + 锁明细 + 审批 kill + 热力条 */
export default function BlockingTab({ configId, onOpenSql, refreshKey }) {
  const [mode, setMode] = useState('live');
  const [at, setAt] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [heat, setHeat] = useState([]);
  const [killTarget, setKillTarget] = useState(null);

  const load = () => {
    if (!configId) return;
    setLoading(true);
    perfAPI.blockingTree(configId, mode === 'live' ? 'now' : at?.toISOString())
      .then((r) => setData(r.data))
      .catch((e) => message.error(`阻塞树加载失败: ${e.message}`))
      .finally(() => setLoading(false));
  };
  useEffect(load, [configId, mode, at, refreshKey]);

  useEffect(() => {
    if (!configId) return;
    perfAPI.ashFacets(configId, { window: '24h', dims: 'wait_class',
                                  filters: 'wait_class:concurrency' })
      .then((r) => setHeat(r.data.timeline || [])).catch(() => setHeat([]));
  }, [configId, refreshKey]);

  const heatOption = useMemo(() => ({
    grid: { left: 44, right: 12, top: 8, bottom: 20 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: '阻塞AAS', axisLabel: { fontSize: 10 } },
    series: [{ type: 'bar', color: '#8B1A1A', barWidth: 2,
               data: heat.map((p) => [p.t, p.aas]) }],
  }), [heat]);

  const onHeatClick = (params) => {
    if (params?.value?.[0]) {
      setMode('replay');
      setAt(dayjs(params.value[0]));
    }
  };

  const columns = [
    { title: '会话', dataIndex: 'session_id', width: 110,
      render: (v, r) => <Text code strong={r.role === 'root_blocker'}>{v}</Text> },
    { title: '角色', width: 90,
      render: (_, r) => (r.role === 'root_blocker'
        ? <Tag color="red">阻塞根源</Tag>
        : r.placeholder ? <Tag>持锁(空闲)</Tag> : <Tag color="orange">被阻塞</Tag>) },
    { title: '用户', dataIndex: 'user_name', width: 90, ellipsis: true },
    { title: '等待秒', dataIndex: 'wait_secs', width: 76 },
    { title: '锁类型/模式', width: 150,
      render: (_, r) => (r.lock_type
        ? <Text style={{ fontSize: 11 }}>{r.lock_type}/{r.lock_mode}</Text> : '-') },
    { title: '争用对象', dataIndex: 'lock_object', width: 150, ellipsis: true },
    { title: '当前 SQL', dataIndex: 'sql_text', ellipsis: true,
      render: (v, r) => (
        <a onClick={() => r.sql_digest && onOpenSql?.(r.sql_digest)}>
          <Text code style={{ fontSize: 11 }}>{v || '-'}</Text>
        </a>) },
    { title: '影响面', dataIndex: 'subtree_waiters', width: 76,
      render: (v) => (v > 0 ? <Tag color="volcano">{v}</Tag> : 0) },
    { title: '操作', width: 96,
      render: (_, r) => (r.killable !== false && mode === 'live'
        ? <Button size="small" danger icon={<StopOutlined />}
                  onClick={() => setKillTarget(r)}>终止</Button> : null) },
  ];

  return (
    <div>
      <Card size="small" styles={{ body: { padding: 8 } }}>
        <Space>
          <Radio.Group size="small" value={mode} onChange={(e) => setMode(e.target.value)}
            options={[{ value: 'live', label: '实时' }, { value: 'replay', label: '历史回放' }]}
            optionType="button" />
          {mode === 'replay' && (
            <DatePicker showTime size="small" value={at}
                        onChange={setAt} placeholder="选择回放时刻" />
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          {data?.at && <Text type="secondary">快照时刻: {String(data.at).slice(0, 19)}</Text>}
        </Space>
      </Card>
      <Card size="small" style={{ marginTop: 8 }} title="阻塞树 (根源标红, 按影响面排序)">
        {(data?.tree || []).length ? (
          <Table size="small" rowKey="session_id" loading={loading} columns={columns}
            dataSource={data.tree} pagination={false}
            expandable={{ childrenColumnName: 'children', defaultExpandAllRows: true }}
            rowClassName={(r) => (r.role === 'root_blocker' ? 'perf-root-blocker' : '')} />
        ) : (
          <Alert type="success" showIcon message="当前无阻塞链" />
        )}
      </Card>
      <Card size="small" style={{ marginTop: 8 }}
            title="近 24h 阻塞热力 (点击柱子=回放该时刻)" styles={{ body: { padding: 4 } }}>
        <ReactEChartsCore echarts={echarts} option={heatOption} notMerge
          style={{ height: 110 }} onEvents={{ click: onHeatClick }} />
      </Card>
      <KillModal configId={configId} target={killTarget}
                 onClose={() => setKillTarget(null)} onDone={load} />
      <style>{'.perf-root-blocker td { background: #fff1f0 !important; }'}</style>
    </div>
  );
}

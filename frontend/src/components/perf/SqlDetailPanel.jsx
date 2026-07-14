import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Empty, List, Row, Space, Spin,
  Table, Tabs, Tag, Typography, message,
} from 'antd';
import { ThunderboltOutlined, DiffOutlined } from '@ant-design/icons';
import * as echarts from 'echarts/core';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { Link } from 'react-router-dom';
import { perfAPI } from '../../services/api';
import WaitClassTag from './WaitClassTag';
import { waitClassColor, waitClassLabel } from './waitClassMeta';

const { Text, Paragraph } = Typography;

function TrendChart({ title, points, color = '#1868C8' }) {
  const option = useMemo(() => ({
    title: { text: title, textStyle: { fontSize: 12 }, left: 4, top: 2 },
    grid: { left: 44, right: 12, top: 28, bottom: 22 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: [{ type: 'line', symbol: 'none', data: points || [], areaStyle: { opacity: 0.15 }, color }],
  }), [title, points, color]);
  return <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height: 140 }} />;
}

function PlanDiff({ aText = '', bText = '' }) {
  // 轻量行级 diff: 相同行灰、差异行高亮 (契约 phase7/30 §5)
  const a = aText.split('\n');
  const b = bText.split('\n');
  const setA = new Set(a);
  const setB = new Set(b);
  const render = (lines, other, addColor) => (
    <pre style={{ fontSize: 12, lineHeight: 1.5, overflow: 'auto', maxHeight: 360,
                  background: '#fafafa', padding: 8, flex: 1, margin: 0 }}>
      {lines.map((l, i) => (
        <div key={i} style={{ background: other.has(l) ? 'transparent' : addColor }}>{l || ' '}</div>
      ))}
    </pre>
  );
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {render(a, setB, '#ffec3d55')}
      {render(b, setA, '#87e8de55')}
    </div>
  );
}

/** SQL 详情面板 (Tab4 下半屏 + 各处抽屉复用, 契约 phase7/30 §5) */
export default function SqlDetailPanel({ configId, digest }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [planTexts, setPlanTexts] = useState({});
  const [selectedHash, setSelectedHash] = useState(null);
  const [explaining, setExplaining] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await perfAPI.sqlDetail(configId, digest);
      setData(res.data);
    } catch (e) {
      message.error(`SQL 详情加载失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { if (configId && digest) load(); }, [configId, digest]);

  const loadPlanText = async (planHash) => {
    setSelectedHash(planHash);
    if (planTexts[planHash]) return;
    try {
      const res = await perfAPI.sqlPlan(configId, digest, planHash);
      setPlanTexts((p) => ({ ...p, [planHash]: res.data.plan_text || '(空计划)' }));
    } catch (e) {
      message.error(`计划加载失败: ${e.message}`);
    }
  };

  const doExplain = async () => {
    setExplaining(true);
    try {
      await perfAPI.explain(configId, digest);
      message.success('计划采集成功');
      load();
    } catch (e) {
      message.error(`EXPLAIN 失败: ${e.message}`);
    } finally {
      setExplaining(false);
    }
  };

  if (loading || !data) return <Spin style={{ display: 'block', margin: '40px auto' }} />;
  const t = data.trend?.series || {};
  const plans = data.plans || [];
  const currentPlan = plans.find((p) => p.is_current);
  const prevPlan = plans.find((p) => !p.is_current);

  const breakdownOption = {
    tooltip: { trigger: 'item', valueFormatter: (v) => `${(v * 100).toFixed(1)}%` },
    series: [{
      type: 'pie', radius: ['45%', '75%'],
      label: { formatter: (p) => `${p.name} ${(p.value * 100).toFixed(0)}%`, fontSize: 10 },
      data: Object.entries(data.ash_breakdown || {}).map(([k, v]) => ({
        name: waitClassLabel(k), value: v, itemStyle: { color: waitClassColor(k) },
      })),
    }],
  };

  return (
    <div>
      <Descriptions size="small" column={3} style={{ marginBottom: 8 }}>
        <Descriptions.Item label="指纹"><Text code copyable>{data.digest}</Text></Descriptions.Item>
        <Descriptions.Item label="库类型"><Tag>{data.db_type}</Tag></Descriptions.Item>
        <Descriptions.Item label="计划突变">
          {data.plan_changed_at ? <Tag color="red">{data.plan_changed_at}</Tag> : <Tag>无</Tag>}
        </Descriptions.Item>
      </Descriptions>
      <Paragraph copyable={{ text: data.sql_text_sample }} style={{
        background: '#f6f6f6', padding: 8, fontFamily: 'monospace', fontSize: 12,
        maxHeight: 90, overflow: 'auto' }}>
        {data.sql_text_sample || '(无原文样例)'}
      </Paragraph>
      <Row gutter={8}>
        <Col span={5}><TrendChart title="执行次数" points={t.exec_delta} color="#04B04B" /></Col>
        <Col span={5}><TrendChart title="平均耗时(ms)" points={t.avg_latency_ms} color="#D9312B" /></Col>
        <Col span={5}><TrendChart title="返回行数" points={t.rows_delta} color="#1868C8" /></Col>
        <Col span={5}><TrendChart title="逻辑读" points={t.reads_delta} color="#9A6B2F" /></Col>
        <Col span={4}>
          <ReactEChartsCore echarts={echarts} option={breakdownOption} notMerge style={{ height: 140 }} />
        </Col>
      </Row>
      <Tabs size="small" items={[
        {
          key: 'plans', label: '执行计划',
          children: plans.length ? (
            <Row gutter={8}>
              <Col span={7}>
                <List size="small" dataSource={plans} renderItem={(p) => (
                  <List.Item onClick={() => loadPlanText(p.plan_hash)} style={{ cursor: 'pointer' }}>
                    <Space>
                      {p.is_current && <Tag color="gold">当前</Tag>}
                      <Text code>{p.plan_hash.slice(0, 10)}</Text>
                      <Text type="secondary">{p.captured_at?.slice(0, 19)}</Text>
                      {p.cost_total != null && <Text type="secondary">cost={p.cost_total}</Text>}
                    </Space>
                  </List.Item>
                )} />
              </Col>
              <Col span={17}>
                <pre style={{ fontSize: 12, background: '#fafafa', padding: 8,
                              maxHeight: 360, overflow: 'auto' }}>
                  {(selectedHash && planTexts[selectedHash]) || '(点击左侧计划查看)'}
                </pre>
              </Col>
            </Row>
          ) : <Empty description="暂无计划, 可手动 EXPLAIN" />,
        },
        {
          key: 'diff', label: <span><DiffOutlined /> 计划对比</span>,
          children: (currentPlan && prevPlan) ? (
            <PlanCompare configId={configId} digest={digest}
                         aHash={prevPlan.plan_hash} bHash={currentPlan.plan_hash} />
          ) : <Empty description="不足两个计划" />,
        },
        {
          key: 'advice', label: '优化建议',
          children: (
            <div>
              <Button icon={<ThunderboltOutlined />} loading={explaining}
                      onClick={doExplain} style={{ marginBottom: 8 }}>手动 EXPLAIN</Button>
              {(data.advisor?.index_suggestions || []).length
                ? <List size="small" dataSource={data.advisor.index_suggestions}
                    renderItem={(s) => (
                      <List.Item>
                        <Alert type="info" showIcon style={{ width: '100%' }}
                          message={typeof s === 'string' ? s
                            : `建议索引: ${s.table_name || s.table || '?'} (${(s.columns || []).map((c) => c.name || c).join(', ')})`}
                          description={s.reason || s.rationale || ''} />
                      </List.Item>)} />
                : <Empty description="暂无建议" />}
            </div>
          ),
        },
        {
          key: 'incidents', label: '关联事故',
          children: (data.related_incidents || []).length ? (
            <Table size="small" rowKey="incident_id" pagination={false}
              dataSource={data.related_incidents}
              columns={[
                { title: '事故', dataIndex: 'incident_id',
                  render: (v) => <Link to={`/incidents/${v}`}>{v}</Link> },
                { title: '标题', dataIndex: 'title' },
                { title: '优先级', dataIndex: 'priority', width: 80,
                  render: (v) => <Tag color={v === 'P1' ? 'red' : v === 'P2' ? 'orange' : 'blue'}>{v}</Tag> },
                { title: '状态', dataIndex: 'status', width: 100 },
              ]} />
          ) : <Empty description="近 7 天无关联事故" />,
        },
      ]} />
    </div>
  );
}

function PlanCompare({ configId, digest, aHash, bHash }) {
  const [texts, setTexts] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const [a, b] = await Promise.all([
          perfAPI.sqlPlan(configId, digest, aHash),
          perfAPI.sqlPlan(configId, digest, bHash),
        ]);
        setTexts([a.data.plan_text || '', b.data.plan_text || '']);
      } catch { setTexts(['', '']); }
    })();
  }, [configId, digest, aHash, bHash]);
  if (!texts) return <Spin />;
  return (
    <div>
      <Space style={{ marginBottom: 4 }}>
        <Tag>旧 {aHash.slice(0, 10)}</Tag>
        <Tag color="gold">新(当前) {bHash.slice(0, 10)}</Tag>
      </Space>
      <PlanDiff aText={texts[0]} bText={texts[1]} />
    </div>
  );
}

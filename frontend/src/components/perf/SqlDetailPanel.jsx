import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Empty, List, Progress, Row, Space, Spin,
  Table, Tabs, Tag, Typography, message,
} from 'antd';
import { ThunderboltOutlined, DiffOutlined, DashboardOutlined } from '@ant-design/icons';
import * as echarts from 'echarts/core';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { Link } from 'react-router-dom';
import { perfAPI } from '../../services/api';
import WaitClassTag from './WaitClassTag';
import { waitClassColor, waitClassLabel } from './waitClassMeta';
import {
  basisNotice, breakdownSegments, explainNotice, fmtMs, planRows, severityMeta,
} from '../../utils/sqlPerf';

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

  const notice = basisNotice({ basis_warning: data.basis_warning });
  return (
    <div>
      {notice && <Alert type={notice.type} showIcon message={notice.text} style={{ marginBottom: 8 }} />}
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
          key: 'profile', label: <span><DashboardOutlined /> 耗时构成</span>,
          children: <ProfilePane configId={configId} digest={digest} data={data} />,
        },
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
              {(data.advice || []).length
                ? <List size="small" dataSource={data.advice}
                    renderItem={(a) => (
                      <List.Item>
                        <Alert type={severityMeta(a.severity).alert} showIcon style={{ width: '100%' }}
                          message={<Space>
                            <Tag color={severityMeta(a.severity).color}>{a.code}</Tag>
                            <Text strong>{a.title}</Text>
                          </Space>}
                          description={<div>
                            <div>{a.detail}</div>
                            {a.ddl && <Paragraph copyable={{ text: a.ddl }} code style={{ margin: '6px 0 0' }}>{a.ddl}</Paragraph>}
                            {a.rewrite && !a.ddl && <div style={{ marginTop: 4 }}><Text type="secondary">改写：</Text><Text code>{a.rewrite}</Text></div>}
                            {a.expected && <div style={{ marginTop: 4 }}><Text type="secondary">预期效果：{a.expected}</Text></div>}
                          </div>} />
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

const SEG_COLORS = { lock: '#D9312B', cpu: '#1868C8', other: '#9A6B2F', rest: '#8c8c8c', total: '#1868C8' };

/** 耗时构成：语句层（每次执行平均花在哪）+ set 层（TDSQL）+ 算子层（EXPLAIN ANALYZE） */
function ProfilePane({ configId, digest, data }) {
  const [ea, setEa] = useState(null);
  const [running, setRunning] = useState(false);
  const p = data.profile || {};
  const { segments, note } = breakdownSegments(p);
  const dist = data.set_distribution;
  const eaCfg = data.explain_analyze || {};

  const runEa = async () => {
    setRunning(true);
    try {
      const r = await perfAPI.explainAnalyze(configId, digest, eaCfg.target_set ? { set_name: eaCfg.target_set } : {});
      setEa(r.data);
    } catch (e) {
      message.error(`EXPLAIN ANALYZE 失败: ${e.response?.data?.message || e.message}`);
    } finally {
      setRunning(false);
    }
  };

  const barOption = {
    tooltip: { trigger: 'item', valueFormatter: (v) => fmtMs(v) },
    grid: { left: 8, right: 8, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: 'value', show: false },
    yAxis: { type: 'category', data: ['每次执行'], show: false },
    series: segments.map((sg) => ({
      name: sg.label, type: 'bar', stack: 't', data: [sg.value], barWidth: 22,
      itemStyle: { color: SEG_COLORS[sg.key] }, label: { show: sg.value > 0, formatter: () => sg.label, fontSize: 10 },
    })),
  };
  const pe = p.per_exec || {};
  const r = p.ratios || {};
  const pct = (v) => (v == null ? '-' : `${(v * 100).toFixed(0)}%`);
  const notice = explainNotice(ea?.summary);

  return (
    <div>
      {!p.available ? <Empty description={p.note || '窗口内无执行记录'} /> : (
        <>
          <Space style={{ marginBottom: 4 }}>
            <Text>平均每次 <Text strong>{fmtMs(p.avg_ms)}</Text>，共执行 {p.exec} 次</Text>
            {p.is_slow && <Tag color="red">慢 SQL</Tag>}
          </Space>
          <ReactEChartsCore echarts={echarts} option={barOption} notMerge style={{ height: 40 }} />
          {note && <Alert type="info" showIcon message={note} style={{ margin: '4px 0' }} />}
          <Descriptions size="small" column={4} bordered style={{ marginTop: 6 }}>
            <Descriptions.Item label="每次扫描行">{pe.rows_examined ?? pe.logical_reads ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="每次返回行">{pe.rows_sent ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="扫描 / 返回">{r.scan_efficiency ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="锁等待占比">{pct(r.lock_share)}</Descriptions.Item>
            <Descriptions.Item label="全表扫描">{pct(r.full_scan_share)}</Descriptions.Item>
            <Descriptions.Item label="未用索引">{pct(r.no_index_share)}</Descriptions.Item>
            <Descriptions.Item label="临时表落盘">{pct(r.tmp_disk_share)}</Descriptions.Item>
            <Descriptions.Item label="归并排序轮次">{pe.sort_merge_passes ?? '-'}</Descriptions.Item>
          </Descriptions>
          {(p.findings || []).length > 0 && (
            <List size="small" style={{ marginTop: 6 }} dataSource={p.findings}
              renderItem={(f) => (
                <List.Item><Space><Tag color={severityMeta(f.severity).color}>{f.code}</Tag>{f.text}</Space></List.Item>
              )} />
          )}
        </>
      )}
      {dist && (
        <Card size="small" style={{ marginTop: 8 }} title={<Space>按 set 分布
          {dist.is_skewed && <Tag color="orange">倾斜 {dist.skew}×，热点 {dist.hottest_set}</Tag>}</Space>}>
          <Table size="small" rowKey="set_name" pagination={false} dataSource={dist.sets}
            columns={[
              { title: 'set', dataIndex: 'set_name' },
              { title: '执行次数', dataIndex: 'exec', width: 100 },
              { title: '总耗时', dataIndex: 'total_ms', width: 110, render: fmtMs },
              { title: '平均耗时', dataIndex: 'avg_ms', width: 110, render: fmtMs },
              { title: '每次扫描行', dataIndex: 'rows_examined_per_exec', width: 110 },
              { title: '耗时份额', dataIndex: 'share', width: 100, render: pct },
            ]} />
        </Card>
      )}
      <Card size="small" style={{ marginTop: 8 }} title="算子层：EXPLAIN ANALYZE"
        extra={eaCfg.enabled
          ? <Button size="small" icon={<ThunderboltOutlined />} loading={running}
                    disabled={!eaCfg.has_literal_sample} onClick={runEa}>
              执行{eaCfg.target_set ? `（在 ${eaCfg.target_set} 上）` : ''}
            </Button>
          : null}>
        {!eaCfg.enabled ? (
          <Alert type="info" showIcon
            message="EXPLAIN ANALYZE 未开启"
            description="它会在目标库真实执行该查询（只读事务、带超时、仅 SELECT）。如需使用，请由管理员开启配置项 PERF_EXPLAIN_ANALYZE_ENABLED，执行人需具备 tickets.execute 权限。" />
        ) : !eaCfg.has_literal_sample ? (
          <Alert type="warning" showIcon message="暂无带字面量的 SQL 样例，无法执行（只有参数化的 digest 文本）" />
        ) : !ea ? (
          <Text type="secondary">点击右上角执行；超时上限 {eaCfg.timeout_ms} ms</Text>
        ) : (
          <div>
            {notice && <Alert type={notice.type} showIcon message={notice.text} style={{ marginBottom: 6 }} />}
            <Table size="small" pagination={false} defaultExpandAllRows
              dataSource={planRows(ea.tree, ea.summary?.total_ms)}
              columns={[
                { title: '算子', dataIndex: 'op', ellipsis: true },
                { title: '自身耗时', dataIndex: 'self_ms', width: 110, render: fmtMs },
                { title: '占比', dataIndex: 'share', width: 140,
                  render: (v) => (v == null ? '-' : <Progress percent={Math.round(v * 100)} size="small" />) },
                { title: '实际行', dataIndex: 'actual_rows', width: 100 },
                { title: '估算行', dataIndex: 'est_rows', width: 100 },
                { title: '循环', dataIndex: 'loops', width: 70 },
              ]} />
          </div>
        )}
      </Card>
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

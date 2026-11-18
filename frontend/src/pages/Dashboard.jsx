import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Col, Empty, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ReloadOutlined, ArrowRightOutlined } from '@ant-design/icons';
import ReactEChartsCore from 'echarts-for-react/esm/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { healthAPI, databaseAPI, alertAPI, dashboardAPI } from '../services/api';
import { hasPermission, Perm } from '../utils/permission';
import { errorText } from '../utils/experience';

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);
const { Title, Text } = Typography;
const labels = { health: '平台探活', databases: '实例清单', alerts: '活跃告警', trend: '性能趋势', stats: '统计摘要' };
const statusLabels = { UP: ['success', '正常'], DOWN: ['error', '故障'], DEGRADED: ['warning', '降级'], UNKNOWN: ['default', '未采集'] };
const clock = (value) => value ? new Date(value).toLocaleTimeString('zh-CN', { hour12: false }) : '尚未获取';

export default function Dashboard() {
  const navigate = useNavigate();
  const [sources, setSources] = useState({});
  const [loading, setLoading] = useState(false);
  const [hours, setHours] = useState(24);
  const [metric, setMetric] = useState('qps');
  const [interval, setRefreshInterval] = useState(0);
  const generation = useRef(0);
  const busy = useRef(false);
  const fetchData = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    const id = ++generation.current;
    const results = await Promise.allSettled([
      healthAPI.check(), databaseAPI.list(), alertAPI.list({ limit: 50, status: 'active' }),
      dashboardAPI.getCharts(hours), dashboardAPI.getStats(),
    ]);
    if (id !== generation.current) return;
    setSources(previous => {
      const next = { ...previous };
      Object.keys(labels).forEach((key, index) => {
        const result = results[index];
        next[key] = result.status === 'fulfilled'
          ? { data: result.value, at: Date.now(), error: null }
          : { ...previous[key], error: errorText(result.reason) };
      });
      return next;
    });
    busy.current = false;
    setLoading(false);
  }, [hours]);
  useEffect(() => {
    busy.current = false;
    setSources(old => ({ ...old, trend: undefined }));
    fetchData();
    return () => { generation.current++; busy.current = false; };
  }, [fetchData]);
  useEffect(() => {
    if (!interval) return;
    const timer = setInterval(() => { if (!document.hidden) fetchData(); }, interval * 1000);
    return () => clearInterval(timer);
  }, [interval, fetchData]);

  const stats = sources.stats?.data;
  const databases = sources.databases?.data?.databases || [];
  const alerts = sources.alerts?.data?.alerts || [];
  const trend = sources.trend?.data?.trend || [];
  const failed = Object.values(sources).some(source => source?.error);
  const goDatabase = id => navigate(`/databases/${id}`);
  const unknown = source => !source?.data ? '—' : null;
  return (
    <div className="experience-dashboard">
      <div className="experience-heading">
        <div><Text type="secondary">DB-AIOps · 运维工作台</Text><Title level={2}>监控概览</Title>
          <Text>先确认数据，再判断风险，最后采取行动。</Text></div>
        <Space wrap>
          <Select aria-label="自动刷新" value={interval} onChange={setRefreshInterval} options={[
            { value: 0, label: '手动刷新' }, { value: 30, label: '每30秒刷新' }, { value: 60, label: '每分钟刷新' },
          ]} />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchData}>刷新数据</Button>
        </Space>
      </div>
      {failed && <Alert showIcon type="warning" message="部分数据未更新，请勿据此判断系统正常"
        description="成功来源继续展示；失败来源保留上次结果，其更新时间见下方。可点击“刷新数据”重试。" style={{ marginBottom: 16 }} />}
      <Card title="数据来源与更新时间" size="small" style={{ marginBottom: 16 }}>
        <div className="source-grid" aria-live="polite">
          {Object.entries(labels).map(([key, label]) => <div key={key}>
            <Text strong>{label}</Text><br />
            <Tag color={sources[key]?.error ? 'warning' : sources[key]?.at ? 'success' : 'default'}>
              {sources[key]?.error ? (sources[key]?.at ? '失败 · 保留旧数据' : '获取失败') : sources[key]?.at ? '已更新' : '获取中'}
            </Tag><Text type="secondary">{clock(sources[key]?.at)}</Text>
            {sources[key]?.error && <div role="status" className="source-error">{sources[key].error}</div>}
          </div>)}
        </div>
        <Text type="secondary">以上为接口获取时间，实例最后采集时间见清单；统计仅含可见且启用的实例，健康分仅统计今日已评分实例。</Text>
      </Card>
      {sources.databases?.at && !sources.databases.error && databases.length === 0 && <Card style={{ marginBottom: 16 }}>
        <Empty description="还没有可见的纳管数据库。添加实例后，启动采集即可查看监控数据。">
          {hasPermission(Perm.DATABASES_VIEW) && <Button type="primary" onClick={() => navigate('/databases')}>前往数据库管理</Button>}
        </Empty>
      </Card>}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} xl={6}><Card><Statistic title="启用监控的实例" value={stats?.total ?? '—'} />
          <Text type="secondary">{stats ? `${stats.online} 正常 / ${stats.offline} 故障 / ${stats.degraded} 降级 / ${stats.unknown} 未知` : '等待统计数据'}</Text></Card></Col>
        <Col xs={24} sm={12} xl={6}><Card><Statistic title="今日平均健康分" value={stats?.avg_health ?? '未评分'} />
          <Text type="secondary">{stats ? `${stats.scored_count} 个实例已有今日评分` : '等待统计数据'}</Text></Card></Col>
        <Col xs={24} sm={12} xl={6}><Card><Statistic title="活跃告警 · 启用实例" value={stats?.active_alerts ?? '—'} />
          {hasPermission(Perm.ALERTS_VIEW) && <Button type="link" onClick={() => navigate('/alerts')}>查看告警 <ArrowRightOutlined /></Button>}</Card></Col>
        <Col xs={24} sm={12} xl={6}><Card title="建议下一步" size="small"><Space direction="vertical">
          {hasPermission(Perm.DATABASES_VIEW) && <Button onClick={() => navigate('/databases')}>检查纳管与采集状态</Button>}
          {hasPermission(Perm.ALERTS_VIEW) && <Button onClick={() => navigate('/incidents')}>跟进事故与处理进度</Button>}
        </Space></Card></Col>
      </Row>
      <Card title="性能趋势" style={{ marginBottom: 16 }} extra={<Segmented aria-label="趋势时间范围" value={hours} onChange={setHours}
        options={[{ value: 1, label: '1小时' }, { value: 6, label: '6小时' }, { value: 24, label: '24小时' }, { value: 168, label: '7天' }]} />}>
        <Space wrap style={{ marginBottom: 16 }}><Segmented value={metric} onChange={setMetric} options={[
          { value: 'qps', label: 'QPS' }, { value: 'tps', label: 'TPS' }, { value: 'conn', label: '连接数' },
        ]} /><Text type="secondary">小时内采样均值 · 来源：{sources.trend?.data?.source || '未获取'} · 最近 {hours} 小时</Text></Space>
        {trend.some(point => point[metric] != null) ? <ReactEChartsCore echarts={echarts} style={{ height: 280 }} notMerge option={{
          tooltip: { trigger: 'axis' }, grid: { left: 55, right: 20, top: 20, bottom: 40 },
          xAxis: { type: 'category', data: trend.map(point => point.time) }, yAxis: { type: 'value' },
          series: [{ type: 'line', name: metric.toUpperCase(), connectNulls: false, data: trend.map(point => point[metric] ?? null), itemStyle: { color: '#1677ff' } }],
        }} /> : <Empty description={sources.trend?.error ? '趋势获取失败，请重试' : loading ? '正在获取趋势…' : '所选时段暂无此指标采样，暂无数据不代表数值为零'} />}
      </Card>
      <Card title="可见数据库 · 包含停用实例" style={{ marginBottom: 16 }}>
        <Table rowKey="id" dataSource={databases} loading={loading && !sources.databases?.at} scroll={{ x: 700 }} pagination={{ pageSize: 10, showSizeChanger: false }} columns={[
          { title: '数据库', dataIndex: 'name', render: (name, db) => <Button type="link" onClick={() => goDatabase(db.id)}>{name}</Button> },
          { title: '类型', dataIndex: 'db_type' },
          { title: '监控状态', render: (_, db) => { const [color, label] = statusLabels[db.status] || statusLabels.UNKNOWN;
            return db.is_active ? <Tag color={color}>{label}</Tag> : <Tag>已停用监控</Tag>; } },
          { title: '最后采集时间', dataIndex: 'last_collect_time', render: time => time ? new Date(time).toLocaleString('zh-CN') : '尚未采集' },
        ]} locale={{ emptyText: unknown(sources.databases) || '暂无可见数据库' }} />
      </Card>
      <Card title="近期活跃告警 · 最多50条" extra={hasPermission(Perm.ALERTS_VIEW) && <Button onClick={() => navigate('/alerts')}>查看全部告警</Button>}>
        <Table rowKey="id" dataSource={alerts} pagination={{ pageSize: 5, showSizeChanger: false }} scroll={{ x: 550 }} columns={[
          { title: '告警', dataIndex: 'title' }, { title: '实例', dataIndex: 'db_name' },
          { title: '级别', dataIndex: 'severity', render: level => <Tag color={level === 'critical' ? 'error' : 'warning'}>{({ critical: '严重', error: '错误', warning: '警告', info: '信息' })[level] || level}</Tag> },
          { title: '发生时间', dataIndex: 'create_time', render: time => time ? new Date(time).toLocaleString('zh-CN') : '未知' },
        ]} locale={{ emptyText: unknown(sources.alerts) || '暂无活跃告警' }} />
      </Card>
    </div>
  );
}

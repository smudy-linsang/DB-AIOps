import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Row, Col, Statistic, Typography, Space, Spin, Table, Tag,
  Select, Button, Tooltip, Badge, Progress, Empty, Divider, Segmented,
} from 'antd';
import {
  DatabaseOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  RiseOutlined,
  FallOutlined,
  ReloadOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  ArrowRightOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, PieChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, TitleComponent, MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { healthAPI, databaseAPI, alertAPI, dashboardAPI } from '../services/api';
import useAppStore from '../stores/useAppStore';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

// 注册 ECharts 组件
echarts.use([
  LineChart, BarChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, TitleComponent, MarkLineComponent,
  CanvasRenderer,
]);

const { Title, Text } = Typography;

// ==========================================
// 常量配置
// ==========================================

const DB_TYPE_CONFIG = {
  oracle: { label: 'Oracle', color: '#f5222d', icon: '🔴', bgColor: '#fff1f0' },
  mysql: { label: 'MySQL', color: '#1890ff', icon: '🔵', bgColor: '#e6f7ff' },
  pgsql: { label: 'PostgreSQL', color: '#336791', icon: '🐘', bgColor: '#f0f5ff' },
  dm: { label: '达梦 DM8', color: '#ee2222', icon: '🟤', bgColor: '#fff2f0' },
  gbase: { label: 'GBase 8a', color: '#00a854', icon: '🟢', bgColor: '#f6ffed' },
  tdsql: { label: 'TDSQL', color: '#108ee9', icon: '🟦', bgColor: '#e6f7ff' },
};

const SEVERITY_CONFIG = {
  critical: { color: '#ff4d4f', bgColor: '#fff2f0', label: '严重', icon: <CloseCircleOutlined /> },
  error: { color: '#fa541c', bgColor: '#fff7e6', label: '错误', icon: <ExclamationCircleOutlined /> },
  warning: { color: '#faad14', bgColor: '#fffbe6', label: '警告', icon: <WarningOutlined /> },
  info: { color: '#1890ff', bgColor: '#e6f7ff', label: '信息', icon: <InfoCircleOutlined /> },
};

const HEALTH_GRADE_CONFIG = {
  A: { color: '#52c41a', range: [85, 100] },
  B: { color: '#73d13d', range: [70, 85] },
  C: { color: '#faad14', range: [55, 70] },
  D: { color: '#ff7a45', range: [40, 55] },
  E: { color: '#ff4d4f', range: [0, 40] },
  F: { color: '#d9d9d9', range: [-1, 0] },
};

// ==========================================
// 工具函数
// ==========================================

function getHealthGrade(score) {
  if (score == null || isNaN(score)) return { grade: 'F', color: '#d9d9d9' };
  for (const [grade, cfg] of Object.entries(HEALTH_GRADE_CONFIG)) {
    if (score >= cfg.range[0] && score <= cfg.range[1]) return { grade, color: cfg.color };
  }
  return { grade: 'F', color: '#d9d9d9' };
}

function getStatusTag(status) {
  const map = {
    UP: { color: 'green', text: 'UP' },
    DOWN: { color: 'red', text: 'DOWN' },
    UNKNOWN: { color: 'default', text: 'UNKNOWN' },
    DEGRADED: { color: 'orange', text: 'DEGRADED' },
  };
  const info = map[status] || { color: 'default', text: status || 'UNKNOWN' };
  return <Tag color={info.color}>{info.text}</Tag>;
}

// ==========================================
// 子组件: Health Score Ring
// ==========================================
function HealthScoreRing({ score, size = 100, title = '', subtitle = '' }) {
  const { grade, color } = getHealthGrade(score);
  const displayScore = score != null && !isNaN(score) ? score : 0;

  return (
    <div style={{ textAlign: 'center' }}>
      <Progress
        type="circle"
        percent={displayScore}
        size={size}
        strokeColor={color}
        format={(pct) => (
          <span>
            <div style={{ fontSize: 28, fontWeight: 700, color, lineHeight: 1 }}>{pct}</div>
            <div style={{ fontSize: 14, fontWeight: 600, color, marginTop: -2 }}>{grade}</div>
          </span>
        )}
      />
      {title && <div style={{ marginTop: 8, fontWeight: 600, fontSize: 14 }}>{title}</div>}
      {subtitle && <Text type="secondary" style={{ fontSize: 12 }}>{subtitle}</Text>}
    </div>
  );
}

// ==========================================
// 子组件: ECharts 趋势图
// ==========================================
function TrendChart({ data = [], metric = 'qps', height = 280, loading = false }) {
  const option = useMemo(() => {
    const metrics = { qps: 'QPS', tps: 'TPS', conn: '连接数', cpu: 'CPU %' };
    const colors = { qps: '#1890ff', tps: '#52c41a', conn: '#faad14', cpu: '#722ed1' };

    return {
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(0,0,0,0.75)',
        borderColor: 'transparent',
        textStyle: { color: '#fff', fontSize: 12 },
      },
      legend: { show: false },
      grid: { left: 50, right: 20, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.time),
        axisLine: { lineStyle: { color: '#e8e8e8' } },
        axisLabel: { color: '#999', fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
        axisLabel: { color: '#999', fontSize: 11 },
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', start: 0, end: 100, height: 20, bottom: 0 },
      ],
      series: [
        {
          name: metrics[metric] || metric,
          type: 'line',
          data: data.map((d) => d[metric]),
          smooth: true,
          symbol: 'none',
          lineStyle: { color: colors[metric] || '#1890ff', width: 2 },
          areaStyle: {
            opacity: 0.15,
          },
        },
      ],
    };
  }, [data, metric]);

  return (
    <Spin spinning={loading}>
      {data.length > 0 ? (
        <ReactEChartsCore echarts={echarts} option={option} style={{ height }} notMerge lazyUpdate />
      ) : (
        <Empty description="暂无数据" style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }} />
      )}
    </Spin>
  );
}

// ==========================================
// 子组件: Top Alerts 列表
// ==========================================
function TopAlertsSection({ alerts = [], loading = false, onViewAll }) {
  if (loading) return <Spin style={{ display: 'block', textAlign: 'center', padding: 40 }} />;
  if (!alerts || alerts.length === 0) {
    return (
      <Empty
        description="暂无活跃告警"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        style={{ padding: 24 }}
      />
    );
  }

  return (
    <div>
      {alerts.slice(0, 5).map((alert, idx) => {
        const sev = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.warning;
        return (
          <div
            key={alert.id || idx}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 12px',
              marginBottom: 6,
              borderRadius: 6,
              background: sev.bgColor,
              borderLeft: `3px solid ${sev.color}`,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            <span style={{ fontSize: 18, flexShrink: 0 }}>{sev.icon}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500, fontSize: 13, color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {alert.database_name && <Tag color="blue" style={{ marginRight: 4 }}>{alert.database_name}</Tag>}
                {alert.title || alert.message || '告警'}
              </div>
              <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                {alert.created_at ? dayjs(alert.created_at).fromNow() : '-'}
                {alert.duration && <span> · 持续 {alert.duration}</span>}
              </div>
            </div>
            <Tag color={sev.color} style={{ flexShrink: 0 }}>{sev.label}</Tag>
          </div>
        );
      })}
      {alerts.length > 5 && (
        <Button type="link" size="small" onClick={onViewAll} style={{ float: 'right', marginTop: 4 }}>
          查看全部 {alerts.length} 条告警 <ArrowRightOutlined />
        </Button>
      )}
    </div>
  );
}

// ==========================================
// 子组件: Database Fleet 摘要表
// ==========================================
function DatabaseFleetTable({ databases = [], statuses = {}, loading = false, onRowClick }) {
  const columns = [
    {
      title: '数据库名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      ellipsis: true,
      render: (text, record) => (
        <Space>
          <span style={{ fontSize: 14 }}>{DB_TYPE_CONFIG[record.db_type?.toLowerCase()]?.icon || '🗄️'}</span>
          <a style={{ fontWeight: 500 }}>{text}</a>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => {
        const cfg = DB_TYPE_CONFIG[type?.toLowerCase()];
        return cfg ? <Tag color={cfg.color}>{cfg.label}</Tag> : <Tag>{type}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status) => getStatusTag(status),
    },
    {
      title: '健康评分',
      key: 'health',
      width: 110,
      render: (_, record) => {
        const status = statuses[record.id];
        const score = status?.health_score;
        const { grade, color } = getHealthGrade(score);
        return (
          <Space>
            <Progress
              type="circle"
              size={24}
              percent={score ?? 0}
              strokeColor={color}
              format={() => ''}
              strokeWidth={8}
            />
            <span style={{ fontWeight: 600, color, fontSize: 16 }}>{grade}</span>
            <Text type="secondary" style={{ fontSize: 12 }}>{score ?? '-'}</Text>
          </Space>
        );
      },
    },
    {
      title: '活跃告警',
      key: 'alerts',
      width: 80,
      render: (_, record) => {
        const status = statuses[record.id];
        const count = status?.alert_count ?? 0;
        return count > 0 ? <Badge count={count} overflowCount={99} /> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: 'QPS',
      key: 'qps',
      width: 70,
      render: (_, record) => {
        const status = statuses[record.id];
        const qps = status?.metrics?.qps || status?.metrics?.queries_per_second;
        return qps != null ? (Number(qps)).toFixed(0) : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '最后采集',
      dataIndex: 'last_collect_time',
      key: 'last_collect',
      width: 130,
      render: (time) => (time ? dayjs(time).fromNow() : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Button type="link" size="small" onClick={(e) => { e.stopPropagation(); onRowClick?.(record); }}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <Table
      dataSource={databases}
      columns={columns}
      rowKey="id"
      loading={loading}
      size="small"
      pagination={false}
      scroll={{ x: 820 }}
      onRow={(record) => ({
        onClick: () => onRowClick?.(record),
        style: { cursor: 'pointer' },
      })}
      locale={{ emptyText: <Empty description="暂无纳管数据库" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
    />
  );
}

// ==========================================
// 主页组件: Dashboard
// ==========================================
export default function Dashboard() {
  const navigate = useNavigate();
  const { setSelectedDb, setDatabases, setAlerts, setPlatformHealth, setDbStatuses, setTrendData } = useAppStore();

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [healthData, setHealthData] = useState(null);
  const [dbList, setDbList] = useState([]);
  const [dbStatusesLocal, setDbStatusesLocal] = useState({});
  const [alertsLocal, setAlertsLocal] = useState([]);
  const [trendDataLocal, setTrendDataLocal] = useState([]);
  const [performanceMetric, setPerformanceMetric] = useState('qps');
  const [timeRange, setTimeRange] = useState('24h');
  const [autoRefresh, setAutoRefresh] = useState(0);

  // 数据获取
  const fetchData = useCallback(async () => {
    try {
      const [health, dbListRes, alertsRes, trendRes] = await Promise.all([
        healthAPI.check().catch(() => null),
        databaseAPI.list().catch(() => ({ databases: [] })),
        alertAPI.list({ limit: 50, status: 'active' }).catch(() => ({ alerts: [] })),
        dashboardAPI.getCharts().catch(() => null),
      ]);

      if (health) { setHealthData(health); setPlatformHealth(health); }

      const databases = (dbListRes?.databases || []).map((db) => ({
        ...db,
        key: db.id,
      }));
      setDbList(databases);
      setDatabases(databases);

      const alertList = alertsRes?.alerts || [];
      setAlertsLocal(alertList);
      setAlerts(alertList);

      // 趋势数据：仅使用 API 返回的真实数据，无数据时显示空图表
      if (trendRes?.trend) {
        setTrendDataLocal(trendRes.trend);
        setTrendData('dashboard', trendRes.trend);
      } else {
        setTrendDataLocal([]);
        setTrendData('dashboard', []);
      }
    } catch (error) {
      console.error('获取仪表盘数据失败:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // 获取所有数据库的详细状态
  const fetchAllDbStatuses = useCallback(async () => {
    if (dbList.length === 0) return;
    try {
      const results = await Promise.allSettled(
        dbList.map((db) => databaseAPI.getStatus(db.id))
      );
      const map = {};
      results.forEach((r, idx) => {
        if (r.status === 'fulfilled' && r.value) {
          map[dbList[idx].id] = r.value;
        }
      });
      setDbStatusesLocal(map);
      setDbStatuses(map);
    } catch (error) {
      console.error('批量获取数据库状态失败:', error);
    }
  }, [dbList]);

  // 首次加载
  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (dbList.length > 0 && Object.keys(dbStatusesLocal).length === 0) {
      fetchAllDbStatuses();
    }
  }, [dbList, dbStatusesLocal, fetchAllDbStatuses]);

  // 自动刷新
  useEffect(() => {
    if (autoRefresh <= 0) return;
    const interval = setInterval(() => {
      fetchData();
      if (dbList.length > 0) fetchAllDbStatuses();
    }, autoRefresh * 1000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchData, fetchAllDbStatuses, dbList.length]);

  // 刷新按钮
  const handleRefresh = () => {
    setRefreshing(true);
    fetchData().then(() => fetchAllDbStatuses());
  };

  // 点击数据库行
  const handleDbRowClick = (record) => {
    setSelectedDb(record.id, record.name, record.db_type);
    navigate(`/databases/${record.id}`);
  };

  // 查看全部告警
  const handleViewAllAlerts = () => navigate('/alerts');

  // 统计计算
  const stats = useMemo(() => {
    const total = dbList.length;
    const online = dbList.filter((db) => db.status === 'UP').length;
    const offline = dbList.filter((db) => db.status === 'DOWN').length;
    const degraded = dbList.filter((db) => db.status === 'DEGRADED').length;

    // 平均健康评分
    const scores = Object.values(dbStatusesLocal)
      .map((s) => s?.health_score)
      .filter((s) => s != null && !isNaN(s));
    const avgHealth = scores.length > 0
      ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
      : null;

    // 活跃告警数
    const activeAlerts = alertsLocal.length;

    return { total, online, offline, degraded, avgHealth, activeAlerts };
  }, [dbList, dbStatusesLocal, alertsLocal]);

  // 按类型分布
  const dbTypeDistribution = useMemo(() => {
    const dist = {};
    dbList.forEach((db) => {
      const t = (db.db_type || 'unknown').toLowerCase();
      dist[t] = (dist[t] || 0) + 1;
    });
    return Object.entries(dist).map(([type, count]) => ({
      name: DB_TYPE_CONFIG[type]?.label || type,
      value: count,
      itemStyle: { color: DB_TYPE_CONFIG[type]?.color || '#999' },
    }));
  }, [dbList]);

  // ==========================================
  // 渲染
  // ==========================================
  if (loading && !healthData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <Spin size="large" tip="加载监控大屏数据..." />
      </div>
    );
  }

  return (
    <div style={{ padding: '0 0 16px 0' }}>
      <style>{`
        .emcc-dashboard .ant-card {
          border-radius: 8px;
          box-shadow: 0 1px 4px rgba(0,0,0,0.06);
          border: 1px solid #e8e8e8;
        }
        .emcc-dashboard .ant-card-head {
          border-bottom: 1px solid #f0f0f0;
          min-height: 42px;
          padding: 0 16px;
        }
        .emcc-dashboard .ant-card-head-title {
          font-size: 14px;
          font-weight: 600;
          padding: 10px 0;
        }
        .emcc-dashboard .ant-card-body {
          padding: 16px;
        }
        .db-fleet-table .ant-table-thead > tr > th {
          background: #fafafa;
          font-weight: 600;
          font-size: 12px;
          padding: 8px 12px;
        }
        .db-fleet-table .ant-table-tbody > tr > td {
          padding: 8px 12px;
          font-size: 13px;
        }
        .db-fleet-table .ant-table-tbody > tr:hover > td {
          background: #e6f7ff;
        }
      `}</style>

      <div className="emcc-dashboard">
        {/* ========================================== */}
        {/* 顶部操作栏 */}
        {/* ========================================== */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '12px 16px', background: '#fff', borderBottom: '1px solid #e8e8e8',
          marginBottom: 16,
        }}>
          <Space size="middle">
            <Title level={5} style={{ margin: 0 }}>
              <DatabaseOutlined style={{ marginRight: 8, color: '#1890ff' }} />
              监控概览
            </Title>
            <Divider type="vertical" />
            <Segmented
              size="small"
              value={timeRange}
              onChange={setTimeRange}
              options={[
                { value: '1h', label: '1小时' },
                { value: '6h', label: '6小时' },
                { value: '24h', label: '24小时' },
                { value: '7d', label: '7天' },
              ]}
            />
            <Divider type="vertical" />
            <Text type="secondary" style={{ fontSize: 12 }}>
              最后更新: {dayjs().format('HH:mm:ss')}
            </Text>
          </Space>
          <Space>
            <Segmented
              size="small"
              value={autoRefresh}
              onChange={setAutoRefresh}
              options={[
                { value: 0, label: '关闭' },
                { value: 30, label: '30秒' },
                { value: 60, label: '1分钟' },
                { value: 300, label: '5分钟' },
              ]}
            />
            <Button
              icon={<ReloadOutlined spin={refreshing} />}
              size="small"
              onClick={handleRefresh}
              loading={refreshing}
            >
              刷新
            </Button>
          </Space>
        </div>

        {/* ========================================== */}
        {/* 第一行: 总体健康状况卡片 */}
        {/* ========================================== */}
        <Row gutter={[16, 16]} style={{ marginBottom: 16, padding: '0 16px' }}>
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title={<span><CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />纳管数据库</span>}
                value={stats.total}
                suffix={
                  <Space size={4} style={{ fontSize: 13 }}>
                    <Text type="success">{stats.online}在线</Text>
                    {stats.degraded > 0 && <Text type="warning">{stats.degraded}降级</Text>}
                    {stats.offline > 0 && <Text type="danger">{stats.offline}离线</Text>}
                  </Space>
                }
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Card>
              <HealthScoreRing score={stats.avgHealth} size={64} title="平均健康评分" />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title={<span><WarningOutlined style={{ color: '#faad14', marginRight: 6 }} />活跃告警</span>}
                value={stats.activeAlerts}
                valueStyle={{ color: stats.activeAlerts > 10 ? '#ff4d4f' : stats.activeAlerts > 0 ? '#faad14' : '#52c41a' }}
                suffix={stats.activeAlerts > 0 && (
                  <Button type="link" size="small" onClick={handleViewAllAlerts}>查看</Button>
                )}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Card>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>
                  <ThunderboltOutlined style={{ marginRight: 4 }} />
                  数据库类型分布
                </div>
                {dbTypeDistribution.length > 0 ? (
                  <ReactEChartsCore
                    echarts={echarts}
                    option={{
                      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
                      series: [{
                        type: 'pie',
                        radius: ['55%', '80%'],
                        center: ['50%', '50%'],
                        itemStyle: { borderRadius: 2, borderColor: '#fff', borderWidth: 2 },
                        label: { show: false },
                        emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
                        data: dbTypeDistribution,
                      }],
                    }}
                    style={{ height: 80 }}
                    notMerge
                    lazyUpdate
                  />
                ) : (
                  <Text type="secondary">暂无数据</Text>
                )}
              </div>
            </Card>
          </Col>
        </Row>

        {/* ========================================== */}
        {/* 第二行: Performance Summary + Top Alerts */}
        {/* ========================================== */}
        <Row gutter={[16, 16]} style={{ padding: '0 16px', marginBottom: 16 }}>
          <Col xs={24} lg={16}>
            <Card
              title={
                <Space>
                  <RiseOutlined style={{ color: '#1890ff' }} />
                  <span>Performance Summary</span>
                  <Segmented
                    size="small"
                    value={performanceMetric}
                    onChange={setPerformanceMetric}
                    options={[
                      { value: 'qps', label: 'QPS' },
                      { value: 'tps', label: 'TPS' },
                      { value: 'conn', label: '连接数' },
                      { value: 'cpu', label: 'CPU' },
                    ]}
                  />
                </Space>
              }
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  过去 {timeRange === '1h' ? '1小时' : timeRange === '6h' ? '6小时' : timeRange === '7d' ? '7天' : '24小时'}
                </Text>
              }
            >
              <TrendChart data={trendDataLocal} metric={performanceMetric} height={300} loading={loading} />
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card
              title={
                <Space>
                  <WarningOutlined style={{ color: '#faad14' }} />
                  <span>Top Alerts</span>
                  {alertsLocal.length > 0 && <Badge count={alertsLocal.length} overflowCount={99} />}
                </Space>
              }
              extra={
                <Button type="link" size="small" onClick={handleViewAllAlerts}>
                  查看全部 <ArrowRightOutlined />
                </Button>
              }
            >
              <TopAlertsSection alerts={alertsLocal} loading={loading} onViewAll={handleViewAllAlerts} />
            </Card>
          </Col>
        </Row>

        {/* ========================================== */}
        {/* 第三行: Database Fleet Summary */}
        {/* ========================================== */}
        <div style={{ padding: '0 16px' }}>
          <Card
            title={
              <Space>
                <ApiOutlined style={{ color: '#1890ff' }} />
                <span>Database Fleet Summary</span>
                <Tag>{dbList.length} 个数据库</Tag>
              </Space>
            }
            extra={
              <Button
                type="primary"
                size="small"
                onClick={() => navigate('/databases')}
              >
                管理数据库
              </Button>
            }
            className="db-fleet-table"
          >
            <DatabaseFleetTable
              databases={dbList}
              statuses={dbStatusesLocal}
              loading={loading}
              onRowClick={handleDbRowClick}
            />
          </Card>
        </div>
      </div>
    </div>
  );
}

/**
 * DatabasePerformanceHub - 仿 Oracle EMCC Performance Hub
 *
 * Phase 6: 数据库详情页增强
 * 功能：
 *  - 性能摘要卡片（状态、健康度、活跃会话、QPS、TPS、等待事件数）
 *  - 等待事件分类柱状图（按 wait_event_type / wait_event 分组）
 *  - 活动会话实时列表（SID/用户/程序/状态/等待事件）
 *  - SQL 慢查询监控入口（跳转 SQLMonitoring 页面）
 *  - 时间范围选择 + 自动刷新
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space, Typography,
  Spin, Empty, Button, Divider, Alert, Progress, Tooltip, Descriptions,
} from 'antd';
import {
  ReloadOutlined, ClockCircleOutlined, ThunderboltOutlined,
  DatabaseOutlined, AlertOutlined, SearchOutlined,
  WarningOutlined, BugOutlined, BarChartOutlined,
  PieChartOutlined, UserSwitchOutlined, ArrowLeftOutlined,
  InfoCircleOutlined, SettingOutlined,
} from '@ant-design/icons';
import { useParams, Link, useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import * as echarts from 'echarts/core';
import { BarChart, PieChart as PieChartType } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';

import { databaseAPI } from '../services/api';
import TimeRangeSelector, { TIME_RANGE_PRESETS } from '../components/TimeRangeSelector';
import useAppStore from '../stores/useAppStore';

// 注册 ECharts 组件
echarts.use([BarChart, PieChartType, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

const { Title, Text, Paragraph } = Typography;

// ─── 常量配置 ─────────────────────────────────────────────
const DB_TYPE_LABELS = {
  oracle: 'Oracle', mysql: 'MySQL', pgsql: 'PostgreSQL',
  dm: '达梦 DM8', gbase: 'GBase 8a', tdsql: 'TDSQL',
};

const STATUS_CONFIG = {
  UP: { color: 'green', text: '在线', icon: '🟢' },
  DOWN: { color: 'red', text: '离线', icon: '🔴' },
  UNKNOWN: { color: 'default', text: '未知', icon: '⚫' },
};

const SEVERITY_COLORS = ['#f5222d', '#fa8c16', '#faad14', '#1890ff', '#52c41a', '#722ed1', '#eb2f96', '#13c2c2'];

// ─── 辅助函数 ─────────────────────────────────────────────
function formatNumber(n) {
  if (n == null) return '-';
  if (typeof n !== 'number') return n;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toFixed(2);
}

function formatMs(sec) {
  if (sec == null || sec === 0) return '0 ms';
  if (sec < 1) return (sec * 1000).toFixed(0) + ' ms';
  if (sec < 60) return sec.toFixed(2) + ' s';
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return m + 'm ' + s + 's';
}

// ─── 等待事件柱状图组件 ─────────────────────────────────────
function WaitEventsBarChart({ data = [], loading = false }) {
  const option = useMemo(() => {
    if (!data || data.length === 0) return {};

    const names = data.map((d) => d.wait_event || d.event || d.name || 'N/A').slice(0, 15);
    const counts = data.map((d) => d.count || 0).slice(0, 15);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(0,0,0,0.75)',
        borderColor: 'transparent',
        textStyle: { color: '#fff', fontSize: 12 },
      },
      grid: { left: 120, right: 30, top: 10, bottom: 30 },
      xAxis: {
        type: 'value',
        name: '会话数',
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: 'category',
        data: names.reverse(),
        axisLabel: {
          fontSize: 11,
          width: 110,
          overflow: 'truncate',
        },
        inverse: true,
      },
      series: [{
        type: 'bar',
        data: counts.reverse().map((v, i) => ({
          value: v,
          itemStyle: { color: SEVERITY_COLORS[i % SEVERITY_COLORS.length] },
        })),
        barMaxWidth: 30,
        label: {
          show: true,
          position: 'right',
          fontSize: 11,
          formatter: '{c}',
        },
      }],
    };
  }, [data]);

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>;
  if (!data || data.length === 0) return <Empty description="暂无等待事件数据" />;

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={{ height: 400 }}
      notMerge
      lazyUpdate
    />
  );
}

// ─── 等待事件饼图组件 ─────────────────────────────────────
function WaitEventsPieChart({ data = [], loading = false }) {
  const option = useMemo(() => {
    if (!data || data.length === 0) return {};
    const topData = data.slice(0, 10).map((d, i) => ({
      name: d.wait_event || d.event || d.name || 'N/A',
      value: d.count || 0,
    }));

    return {
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} ({d}%)',
        backgroundColor: 'rgba(0,0,0,0.75)',
        borderColor: 'transparent',
        textStyle: { color: '#fff', fontSize: 12 },
      },
      legend: {
        orient: 'vertical',
        right: 10,
        top: 'center',
        textStyle: { fontSize: 11 },
        formatter: (name) => name.length > 20 ? name.substring(0, 20) + '…' : name,
      },
      series: [{
        type: 'pie',
        radius: ['45%', '75%'],
        center: ['35%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 2, borderColor: '#fff', borderWidth: 1 },
        label: { show: false },
        emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
        data: topData.map((d, i) => ({
          ...d,
          itemStyle: { color: SEVERITY_COLORS[i % SEVERITY_COLORS.length] },
        })),
      }],
    };
  }, [data]);

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>;
  if (!data || data.length === 0) return <Empty description="暂无等待事件数据" />;

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={{ height: 350 }}
      notMerge
      lazyUpdate
    />
  );
}

// ─── 主组件 ─────────────────────────────────────────────────
const DatabasePerformanceHub = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { setSelectedDb } = useAppStore();

  const [loading, setLoading] = useState(true);
  const [statusData, setStatusData] = useState(null);
  const [configInfo, setConfigInfo] = useState(null);
  const [metricsHistory, setMetricsHistory] = useState([]);

  // 时间范围
  const [timeRangeKey, setTimeRangeKey] = useState('1h');
  const [autoRefresh, setAutoRefresh] = useState(0);

  // 加载全部数据
  const fetchAllData = useCallback(async (timeKey) => {
    if (!id) return;
    setLoading(true);
    try {
      const [statusRes, confRes, metricsRes] = await Promise.all([
        databaseAPI.getStatus(id),
        databaseAPI.list(),
        databaseAPI.getMetrics(id, { time: timeKey || '1h' }),
      ]);

      setStatusData(statusRes || {});
      const dbs = confRes?.databases || confRes?.data || [];
      const db = dbs.find((d) => d.id === parseInt(id)) || {};
      setConfigInfo(db);
      setMetricsHistory(metricsRes?.metrics || []);

      if (db.name) {
        setSelectedDb(db.id, db.name, db.db_type);
      }
    } catch (e) {
      console.error('加载 Performance Hub 数据失败:', e);
    } finally {
      setLoading(false);
    }
  }, [id, setSelectedDb]);

  useEffect(() => {
    fetchAllData(timeRangeKey);
  }, [id]);

  // 自动刷新
  useEffect(() => {
    if (autoRefresh > 0) {
      const timer = setInterval(() => fetchAllData(timeRangeKey), autoRefresh * 1000);
      return () => clearInterval(timer);
    }
  }, [autoRefresh, timeRangeKey, fetchAllData]);

  const handleTimeRangeChange = useCallback((key) => {
    setTimeRangeKey(key);
    fetchAllData(key);
  }, [fetchAllData]);

  // 从 status 中提取数据
  const metrics = statusData?.metrics || {};
  const dbType = configInfo?.db_type || 'unknown';
  const status = statusData?.status;

  // 等待事件数据（兼容不同 checker 的字段名）
  const waitEvents = useMemo(() => {
    return metrics?.wait_events_by_type || metrics?.wait_events || [];
  }, [metrics]);

  // 活动会话详情（Oracle 的 session_details，PostgreSQL 的 active_sessions_detail）
  const activeSessionDetails = useMemo(() => {
    return metrics?.active_session_details || metrics?.session_details || [];
  }, [metrics]);

  // 连接信息
  const activeConnections = metrics?.active_connections || metrics?.active_sessions || 0;
  const totalConnections = metrics?.total_connections || metrics?.total_sessions || 0;
  const maxConnections = metrics?.max_connections || metrics?.max_sessions || 0;

  // 锁等待
  const lockWaitCount = metrics?.lock_wait_count || 0;

  // QPS/TPS
  const qps = metrics?.qps || 0;
  const tps = metrics?.tps || 0;

  // 表空间使用率（取最大）
  const tablespaceMaxPct = useMemo(() => {
    const ts = metrics?.tablespaces || [];
    if (ts.length === 0) return null;
    return Math.max(...ts.map((t) => t.used_pct || 0));
  }, [metrics]);

  // ─── 会话列表表格列 ─────────────────────────────────────
  const sessionColumns = useMemo(() => {
    const base = [
      {
        title: '会话ID', dataIndex: 'sid', key: 'sid', width: 80,
        render: (v) => <Text code>{v}</Text>,
      },
      {
        title: '用户名', dataIndex: 'username', key: 'username', width: 100,
        ellipsis: true,
      },
      {
        title: '程序', dataIndex: 'program', key: 'program', width: 120,
        ellipsis: true,
      },
      {
        title: '状态', dataIndex: 'status', key: 'status', width: 80,
        render: (v) => {
          const color = v === 'ACTIVE' ? 'green' : v === 'INACTIVE' ? 'default' : 'orange';
          return <Tag color={color}>{v}</Tag>;
        },
      },
      {
        title: '等待事件', dataIndex: 'wait_event', key: 'wait_event', width: 140,
        ellipsis: true,
        render: (v) => v ? <Tag color="orange">{v}</Tag> : <Text type="secondary">-</Text>,
      },
      {
        title: 'SQL ID', dataIndex: 'sql_id', key: 'sql_id', width: 100,
        render: (v) => v ? <Text code style={{ fontSize: 11 }}>{v}</Text> : '-',
      },
      {
        title: '耗时', dataIndex: 'seconds_in_wait', key: 'seconds_in_wait', width: 80,
        align: 'right',
        render: (v) => {
          if (v == null) return '-';
          return <Text style={{ color: v > 10 ? '#f5222d' : '#faad14' }}>{formatMs(v)}</Text>;
        },
      },
    ];

    // PostgreSQL 特殊列
    if (dbType === 'pgsql') {
      return [
        { title: 'PID', dataIndex: 'pid', key: 'pid', width: 70, render: (v) => <Text code>{v}</Text> },
        { title: '用户名', dataIndex: 'usename', key: 'usename', width: 100, ellipsis: true },
        { title: '应用', dataIndex: 'application_name', key: 'app', width: 100, ellipsis: true },
        {
          title: '状态', dataIndex: 'state', key: 'state', width: 80,
          render: (v) => {
            const color = v === 'active' ? 'green' : v === 'idle' ? 'default' : 'orange';
            return <Tag color={color}>{v}</Tag>;
          },
        },
        {
          title: '等待事件', dataIndex: 'wait_event', key: 'wait_event', width: 140, ellipsis: true,
          render: (v) => v ? <Tag color="orange">{v}</Tag> : <Text type="secondary">-</Text>,
        },
        { title: '查询预览', dataIndex: 'query_preview', key: 'query', ellipsis: true, width: 200 },
      ];
    }

    return base;
  }, [dbType]);

  // 等待事件表格列
  const waitEventColumns = [
    { title: '等待事件', dataIndex: 'wait_event', key: 'event', ellipsis: true },
    { title: '会话数', dataIndex: 'count', key: 'count', width: 80, align: 'right', render: (v) => <Text strong>{v}</Text> },
    {
      title: '占比', dataIndex: 'count', key: 'ratio', width: 120,
      render: (v) => {
        const total = waitEvents.reduce((s, d) => s + (d.count || 0), 0);
        const pct = total > 0 ? ((v / total) * 100).toFixed(1) : 0;
        return <Progress percent={parseFloat(pct)} size="small" />;
      },
    },
  ];

  // 转到 SQL Monitoring
  const goToSqlMonitoring = () => {
    setSelectedDb(parseInt(id), configInfo?.name || '', dbType);
    navigate(`/sql-monitoring?db=${id}`);
  };

  // ─── 渲染 ─────────────────────────────────────────────────
  return (
    <div>
      {/* 标题栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space wrap>
          <Link to={`/databases/${id}`}>
            <Button icon={<ArrowLeftOutlined />} size="small">返回详情</Button>
          </Link>
          <ThunderboltOutlined style={{ fontSize: 22, color: '#fa8c16' }} />
          <Title level={4} style={{ margin: 0 }}>
            Performance Hub
          </Title>
          {configInfo?.name && (
            <Tag color="blue">
              <DatabaseOutlined /> {configInfo.name}
            </Tag>
          )}
          {dbType && <Tag>{DB_TYPE_LABELS[dbType] || dbType}</Tag>}
          {status && (
            <Tag color={STATUS_CONFIG[status]?.color || 'default'}>
              {STATUS_CONFIG[status]?.icon} {STATUS_CONFIG[status]?.text}
            </Tag>
          )}
        </Space>

        <Space size={8}>
          <TimeRangeSelector
            value={timeRangeKey}
            onChange={handleTimeRangeChange}
            autoRefresh={autoRefresh}
            onAutoRefreshChange={setAutoRefresh}
            loading={loading}
            onRefresh={() => fetchAllData(timeRangeKey)}
            customPresets={['1h', '6h', '24h']}
            showAutoRefresh={true}
            size="small"
          />
        </Space>
      </div>

      <Spin spinning={loading} tip="加载中...">
        {/* ──── 性能摘要卡片 ──── */}
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="活跃会话"
                value={activeConnections}
                suffix={maxConnections > 0 ? `/ ${maxConnections}` : ''}
                valueStyle={{ color: activeConnections > 0 ? '#1890ff' : '#999', fontSize: 24 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="总连接数"
                value={totalConnections}
                valueStyle={{ fontSize: 24 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="QPS"
                value={formatNumber(qps)}
                valueStyle={{ fontSize: 24 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="TPS"
                value={formatNumber(tps)}
                valueStyle={{ fontSize: 24 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="锁等待"
                value={lockWaitCount}
                suffix="个"
                valueStyle={{ color: lockWaitCount > 0 ? '#f5222d' : '#52c41a', fontSize: 24 }}
                prefix={lockWaitCount > 0 ? <WarningOutlined /> : null}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="等待事件类型"
                value={waitEvents.length}
                suffix="类"
                valueStyle={{ color: waitEvents.length > 5 ? '#fa8c16' : '#52c41a', fontSize: 24 }}
              />
            </Card>
          </Col>
        </Row>

        {/* ──── 等待事件分析（双图） ──── */}
        <Card
          size="small"
          style={{ marginBottom: 16 }}
          title={
            <Space>
              <BarChartOutlined style={{ color: '#fa8c16' }} />
              <Text strong>等待事件分析</Text>
              <Tag>{waitEvents.length} 类型</Tag>
            </Space>
          }
          extra={
            <Tooltip title="等待事件是数据库性能瓶颈的关键指标，按会话等待数量降序排列">
              <InfoCircleOutlined style={{ color: '#999' }} />
            </Tooltip>
          }
        >
          {waitEvents.length === 0 ? (
            <Empty description="当前无等待事件数据" />
          ) : (
            <Row gutter={16}>
              <Col xs={24} md={14}>
                <WaitEventsBarChart data={waitEvents} loading={loading} />
              </Col>
              <Col xs={24} md={10}>
                <WaitEventsPieChart data={waitEvents} loading={loading} />
              </Col>
            </Row>
          )}

          {waitEvents.length > 0 && (
            <>
              <Divider style={{ margin: '12px 0' }} />
              <Table
                columns={waitEventColumns}
                dataSource={waitEvents.map((d, i) => ({ ...d, key: i }))}
                size="small"
                pagination={{ pageSize: 10 }}
              />
            </>
          )}
        </Card>

        {/* ──── Top Activity 分段 ──── */}
        <Card
          size="small"
          style={{ marginBottom: 16 }}
          title={
            <Space>
              <UserSwitchOutlined style={{ color: '#1890ff' }} />
              <Text strong>Top Activity</Text>
            </Space>
          }
          extra={
            <Space size={8}>
              <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchAllData(timeRangeKey)} loading={loading}>
                刷新
              </Button>
            </Space>
          }
        >
          {/* 活动会话列表 */}
          <div style={{ marginBottom: 12 }}>
            <Space>
              <Text strong style={{ fontSize: 13 }}>活动会话</Text>
              <Tag color="blue">{activeSessionDetails.length} 条</Tag>
            </Space>
          </div>

          {activeSessionDetails.length === 0 ? (
            <Alert
              message="暂无活动会话详情"
              description="当前数据库类型可能不支持采集会话详情，或当前无活动会话"
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
            />
          ) : (
            <Table
              columns={sessionColumns}
              dataSource={activeSessionDetails.map((d, i) => ({ ...d, key: i }))}
              size="small"
              scroll={{ x: 800 }}
              pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条会话` }}
            />
          )}

          <Divider style={{ margin: '12px 0' }} />

          {/* 快速操作入口 */}
          <Row gutter={[12, 12]}>
            <Col xs={24} sm={8}>
              <Card
                size="small"
                hoverable
                style={{ cursor: 'pointer' }}
                onClick={goToSqlMonitoring}
              >
                <Space direction="vertical" size={4} style={{ width: '100%', textAlign: 'center' }}>
                  <SearchOutlined style={{ fontSize: 24, color: '#1890ff' }} />
                  <Text strong>SQL 慢查询监控</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    查看慢查询列表、模式分析与优化建议
                  </Text>
                </Space>
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card
                size="small"
                hoverable
                style={{ cursor: 'pointer' }}
                onClick={() => navigate(`/databases/${id}`)}
              >
                <Space direction="vertical" size={4} style={{ width: '100%', textAlign: 'center' }}>
                  <BarChartOutlined style={{ fontSize: 24, color: '#52c41a' }} />
                  <Text strong>性能趋势详情</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    查看完整指标卡片、ECharts 趋势图与异常分析
                  </Text>
                </Space>
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card
                size="small"
                hoverable
                style={{ cursor: 'pointer' }}
                onClick={() => navigate('/alert-config')}
              >
                <Space direction="vertical" size={4} style={{ width: '100%', textAlign: 'center' }}>
                  <AlertOutlined style={{ fontSize: 24, color: '#fa8c16' }} />
                  <Text strong>告警配置</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    管理该数据库的告警模板与阈值覆盖
                  </Text>
                </Space>
              </Card>
            </Col>
          </Row>
        </Card>

        {/* ──── 实时指标快照（调试/参考） ──── */}
        <Card
          size="small"
          title={
            <Space>
              <SettingOutlined />
              <Text strong>实时指标快照</Text>
            </Space>
          }
        >
          <Row gutter={[12, 12]}>
            {Object.entries(metrics)
              .filter(([k, v]) => typeof v === 'number' || typeof v === 'string')
              .slice(0, 20)
              .map(([key, value]) => (
                <Col xs={12} sm={8} md={6} key={key}>
                  <div style={{
                    padding: '6px 10px',
                    background: '#fafafa',
                    borderRadius: 4,
                    border: '1px solid #f0f0f0',
                  }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>{key}</Text>
                    <br />
                    <Text strong style={{ fontSize: 14 }}>
                      {typeof value === 'number' ? formatNumber(value) : String(value).substring(0, 30)}
                    </Text>
                  </div>
                </Col>
              ))}
          </Row>
          {Object.keys(metrics).length === 0 && (
            <Empty description="暂无指标数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </Card>
      </Spin>
    </div>
  );
};

export default DatabasePerformanceHub;

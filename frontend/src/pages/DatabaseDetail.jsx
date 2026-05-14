import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Card, Row, Col, Statistic, Typography, Space, Tag,
  Table, Tabs, Button, Descriptions, Spin, Alert, Modal, Empty, Progress, Tooltip
} from 'antd';
import {
  ArrowLeftOutlined, ReloadOutlined, DatabaseOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined,
  ThunderboltOutlined, DashboardOutlined, LineChartOutlined,
  ZoomInOutlined, ZoomOutOutlined, InfoCircleOutlined,
  ExpandOutlined
} from '@ant-design/icons';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, BarChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, TitleComponent, MarkLineComponent,
  MarkPointComponent
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { databaseAPI, alertAPI } from '../services/api';
import {
  DB_TYPE_LABELS, DB_METRIC_CATEGORIES,
  getMetricCategories, formatMetricValue, getMetricThresholdColor,
  getMetricRawValue
} from '../config/dbMetricsConfig';
import TimeRangeSelector, { TIME_RANGE_PRESETS } from '../components/TimeRangeSelector';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

// 注册 ECharts 组件
echarts.use([
  LineChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, TitleComponent, MarkLineComponent,
  MarkPointComponent, CanvasRenderer,
]);

// 图表配色方案（EMCC 风格）
const CHART_COLORS = {
  qps: '#1890ff',
  tps: '#52c41a',
  active_connections: '#faad14',
  buffer_hit_ratio: '#722ed1',
  cpu_usage: '#eb2f96',
  memory_usage: '#13c2c2',
  disk_usage: '#fa8c16',
  connection_usage: '#8c8c8c',
  sessions: '#52c41a',
  connections: '#f5222d',
  default: '#1890ff',
};

// 指标友好名称
const METRIC_LABELS = {
  qps: 'QPS', tps: 'TPS',
};

// 统计计算函数
function computeStats(data) {
  if (!data || data.length === 0) return null;
  const values = data.map(d => d.value).filter(v => typeof v === 'number');
  if (values.length === 0) return null;
  const avg = values.reduce((a, b) => a + b, 0) / values.length;
  const sorted = [...values].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  const variance = values.reduce((sum, v) => sum + (v - avg) ** 2, 0) / values.length;
  const std = Math.sqrt(variance);
  return { avg, min, max, p95, std, count: values.length };
}

// 异常检测
function detectAnomalies(data, stats) {
  if (!data || data.length === 0 || !stats) return [];
  return data
    .filter(d => typeof d.value === 'number' && Math.abs(d.value - stats.avg) > 2 * stats.std)
    .map(d => ({
      time: d.time,
      value: d.value,
      deviation: stats.std > 0 ? ((d.value - stats.avg) / stats.std).toFixed(2) : '0.00',
    }));
}

// ECharts 趋势图组件
function EChartsTrend({ data = [], metricKey, height = 300, loading = false,
  showDataZoom = true, showMarkLine = false, stats = null, multiMetrics = null,
  yAxisLabel = '', color }) {

  const option = useMemo(() => {
    if (!data || data.length === 0) return {};

    const colorHex = color || CHART_COLORS[metricKey] || CHART_COLORS.default;

    if (multiMetrics) {
      const series = multiMetrics.map((m) => ({
        name: m.label || m.key,
        type: 'line',
        data: data.map(d => d[m.key]),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: m.color || CHART_COLORS[m.key] || CHART_COLORS.default, width: 2 },
        yAxisIndex: m.yAxisIndex || 0,
      }));

      const yAxisArr = [{ type: 'value', name: yAxisLabel || multiMetrics[0]?.label || '', axisLabel: { fontSize: 11 } }];
      if (multiMetrics.some(m => m.yAxisIndex === 1)) {
        yAxisArr.push({
          type: 'value', name: multiMetrics.find(m => m.yAxisIndex === 1)?.label || '',
          axisLabel: { fontSize: 11 },
        });
      }

      return {
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(0,0,0,0.75)',
          borderColor: 'transparent',
          textStyle: { color: '#fff', fontSize: 12 },
        },
        legend: { bottom: 0, textStyle: { fontSize: 11 } },
        grid: { left: 60, right: yAxisArr.length > 1 ? 60 : 20, top: 20, bottom: showDataZoom ? 45 : 25 },
        xAxis: {
          type: 'category',
          data: data.map(d => d.time),
          axisLabel: { fontSize: 11, rotate: data.length > 30 ? 45 : 0 },
        },
        yAxis: yAxisArr,
        dataZoom: showDataZoom ? [
          { type: 'inside', start: 0, end: 100 },
          { type: 'slider', start: 0, end: 100, height: 20, bottom: 0 },
        ] : [],
        series,
      };
    }

    // 单指标模式
    const markLine = [];
    if (showMarkLine && stats) {
      if (stats.avg != null) markLine.push({ type: 'average', name: '均值', lineStyle: { color: '#52c41a', type: 'dashed' } });
      if (stats.max != null) markLine.push({ type: 'max', name: '最大值', lineStyle: { color: '#ff4d4f', type: 'dashed' } });
      if (stats.min != null) markLine.push({ type: 'min', name: '最小值', lineStyle: { color: '#1890ff', type: 'dashed' } });
    }

    return {
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(0,0,0,0.75)',
        borderColor: 'transparent',
        textStyle: { color: '#fff', fontSize: 12 },
        axisPointer: { type: 'cross', crossStyle: { color: '#999' } },
      },
      legend: { show: false },
      grid: { left: 60, right: 20, top: 20, bottom: showDataZoom ? 45 : 25 },
      xAxis: {
        type: 'category',
        data: data.map(d => d.time),
        axisLabel: { fontSize: 11, rotate: data.length > 30 ? 45 : 0 },
        axisLine: { lineStyle: { color: '#e8e8e8' } },
      },
      yAxis: {
        type: 'value',
        name: yAxisLabel || '',
        axisLabel: { fontSize: 11 },
        splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
      },
      dataZoom: showDataZoom ? [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', start: 0, end: 100, height: 20, bottom: 0 },
      ] : [],
      series: [
        {
          name: yAxisLabel || metricKey,
          type: 'line',
          data: data.map(d => d.value),
          smooth: true,
          symbol: data.length < 60 ? 'circle' : 'none',
          symbolSize: 3,
          lineStyle: { color: colorHex, width: 2 },
          areaStyle: { opacity: 0.15 },
          markLine: showMarkLine && stats ? {
            silent: true,
            symbol: 'none',
            data: markLine,
          } : undefined,
        },
      ],
    };
  }, [data, metricKey, showDataZoom, showMarkLine, stats, multiMetrics, yAxisLabel, color]);

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

const TIME_RANGE_CUSTOM_PRESETS = ['15m', '1h', '4h', '24h', '7d'];

const DatabaseDetail = () => {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [configInfo, setConfigInfo] = useState(null);
  const [statusData, setStatusData] = useState(null);
  const [metricsHistory, setMetricsHistory] = useState([]);
  const [alerts, setAlerts] = useState([]);

  const [timeRangeKey, setTimeRangeKey] = useState('1h');
  const [timeRange, setTimeRange] = useState(() => {
    const preset = TIME_RANGE_PRESETS['1h'];
    return { start: preset.getRange()[0], end: preset.getRange()[1] };
  });
  const [autoRefresh, setAutoRefresh] = useState(0);
  const [comparisonMode, setComparisonMode] = useState(false);

  const [metricModalVisible, setMetricModalVisible] = useState(false);
  const [selectedMetric, setSelectedMetric] = useState(null);
  const [tablespaceModalVisible, setTablespaceModalVisible] = useState(false);
  const [selectedTablespace, setSelectedTablespace] = useState(null);
  const [waitEventModalVisible, setWaitEventModalVisible] = useState(false);
  const [selectedWaitEvent, setSelectedWaitEvent] = useState(null);

  const buildChartData = useCallback((metricKey) => {
    if (!metricsHistory || metricsHistory.length === 0) return [];
    return metricsHistory
      .filter(item => item.metric === metricKey)
      .map(item => ({
        time: dayjs(item.timestamp).format(timeRangeKey === '7d' || timeRangeKey === '30d'
          ? 'MM-DD HH:mm' : 'HH:mm'),
        value: typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0,
        timestamp: item.timestamp,
      }))
      .reverse();
  }, [metricsHistory, timeRangeKey]);

  const buildMultiMetricChartData = useCallback(() => {
    if (!metricsHistory || metricsHistory.length === 0) return [];
    const timeMap = {};
    metricsHistory.forEach(item => {
      const ts = item.timestamp;
      if (!timeMap[ts]) timeMap[ts] = { time: dayjs(ts).format(timeRangeKey === '7d' || timeRangeKey === '30d' ? 'MM-DD HH:mm' : 'HH:mm') };
      const val = typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0;
      timeMap[ts][item.metric] = val;
    });
    return Object.values(timeMap);
  }, [metricsHistory, timeRangeKey]);

  const handleMetricClick = (metricKey, metricName, metricValue) => {
    const chartData = buildChartData(metricKey);
    setSelectedMetric({
      key: metricKey,
      name: metricName,
      value: metricValue,
      chartData,
      stats: computeStats(chartData),
    });
    setMetricModalVisible(true);
  };

  const handleTablespaceClick = async (tablespace) => {
    setSelectedTablespace({
      name: tablespace.name,
      total_mb: tablespace.total_mb,
      used_mb: tablespace.used_mb,
      used_pct: tablespace.used_pct,
      chartData: [],
      loading: true,
      stats: null,
    });
    setTablespaceModalVisible(true);

    try {
      const response = await databaseAPI.getMetrics(id, {
        time: timeRangeKey,
        metric: 'tablespace_' + tablespace.name + '_used_pct',
      });
      const data = (response?.metrics || [])
        .map(item => ({
          time: dayjs(item.timestamp).format('MM-DD HH:mm'),
          value: parseFloat(item.value) || 0,
        }))
        .reverse();
      setSelectedTablespace(prev => ({
        ...prev,
        chartData: data,
        loading: false,
        stats: computeStats(data),
      }));
    } catch (error) {
      console.error('获取表空间历史数据失败:', error);
      setSelectedTablespace(prev => ({ ...prev, loading: false }));
    }
  };

  const handleWaitEventClick = async (waitEvent) => {
    setSelectedWaitEvent({
      event: waitEvent.event,
      total_waits: waitEvent.total_waits,
      time_waited: waitEvent.time_waited,
      average_wait: waitEvent.average_wait,
      chartData: [],
      loading: true,
      stats: null,
    });
    setWaitEventModalVisible(true);

    try {
      const response = await databaseAPI.getMetrics(id, {
        time: timeRangeKey,
        metric: 'wait_event_' + (waitEvent.event || ''),
      });
      const data = (response?.metrics || [])
        .map(item => ({
          time: dayjs(item.timestamp).format('MM-DD HH:mm'),
          value: parseFloat(item.value) || 0,
        }))
        .reverse();
      setSelectedWaitEvent(prev => ({
        ...prev,
        chartData: data,
        loading: false,
        stats: computeStats(data),
      }));
    } catch (error) {
      console.error('获取等待事件历史数据失败:', error);
      setSelectedWaitEvent(prev => ({ ...prev, loading: false }));
    }
  };

  // 使用 ref 跟踪是否正在加载，防止重复请求
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (currentTimeRange) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      const configResponse = await databaseAPI.list();
      const dbConfig = configResponse?.databases?.find(db => db.id === parseInt(id));

      const statusResponse = await databaseAPI.getStatus(id);

      const metricsResponse = await databaseAPI.getMetrics(id, {
        time: currentTimeRange || '24h',
      });

      const alertsResponse = await alertAPI.getByDatabase(id);

      setConfigInfo(dbConfig || {});
      setStatusData(statusResponse || {});
      setMetricsHistory(metricsResponse?.metrics || []);
      setAlerts(alertsResponse?.alerts || []);
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [id]);

  const handleTimeRangeChange = useCallback((key, range) => {
    setTimeRangeKey(key);
    if (range && range[0] && range[1]) {
      setTimeRange({ start: range[0], end: range[1] });
      fetchData(key);
    }
  }, [fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData(timeRangeKey);
  }, [fetchData, timeRangeKey]);

  useEffect(() => {
    if (autoRefresh > 0) {
      const interval = setInterval(() => fetchData(timeRangeKey), autoRefresh * 1000);
      return () => clearInterval(interval);
    }
  }, [autoRefresh, timeRangeKey, fetchData]);

  useEffect(() => {
    if (id) {
      fetchData(timeRangeKey);
    }
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const metrics = statusData?.metrics || {};
  const dbType = configInfo?.db_type || 'unknown';
  const metricCategories = useMemo(() => getMetricCategories(dbType), [dbType]);

  const getStatusTag = useCallback((status) => {
    const statusMap = {
      UP: { color: 'green', text: '在线', icon: <CheckCircleOutlined /> },
      DOWN: { color: 'red', text: '离线', icon: <CloseCircleOutlined /> },
      UNKNOWN: { color: 'default', text: '未知', icon: <ClockCircleOutlined /> },
    };
    const cfg = statusMap[status] || { color: 'default', text: status, icon: null };
    return <Tag color={cfg.color} icon={cfg.icon}>{cfg.text}</Tag>;
  }, []);

  const formatNumber = useCallback((num) => {
    if (num === null || num === undefined) return '-';
    if (typeof num !== 'number') return num;
    if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(2) + 'K';
    return num.toFixed(2);
  }, []);

  // 渲染指标卡片分类
  const renderMetricCardsCategory = useCallback((category) => {
    if (category.showWhen && !category.showWhen(metrics)) return null;

    return (
      <Card title={category.title} key={category.key} size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 16]}>
          {category.metrics.map(metricDef => {
            let value = metrics[metricDef.key];
            if ((value === undefined || value === null) && metricDef.fallbackKey) {
              value = metrics[metricDef.fallbackKey];
            }
            const thresholdColor = getMetricThresholdColor(value, metricDef.key);
            const displayValue = formatMetricValue(value, metricDef.format);

            return (
              <Col span={6} key={metricDef.key}>
                <Card
                  size="small"
                  hoverable={metricDef.clickable !== false}
                  onClick={() => metricDef.clickable !== false && handleMetricClick(metricDef.key, metricDef.label, value)}
                >
                  <Statistic
                    title={metricDef.label}
                    value={displayValue}
                    valueStyle={{
                      color: thresholdColor || (metricDef.highlight ? '#1890ff' : undefined),
                      fontSize: 20,
                      cursor: metricDef.clickable !== false ? 'pointer' : 'default',
                    }}
                  />
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>
    );
  }, [metrics, handleMetricClick]);

  // 渲染表格型指标分类
  const renderTableCategory = useCallback((category) => {
    if (category.showWhen && !category.showWhen(metrics)) return null;

    const dataSource = (metrics[category.key] || []).map((item, idx) => ({
      ...item,
      _key: item.name || item.event || item.id || item.pid || idx,
    }));

    if (dataSource.length === 0 && !category.showWhen) return null;

    return (
      <Card title={category.title} key={category.key} size="small" style={{ marginBottom: 16 }}>
        <Table
          dataSource={dataSource}
          size="small"
          pagination={category.pagination ? { pageSize: 10 } : false}
          scroll={category.columns.length > 6 ? { x: 'max-content' } : undefined}
          onRow={(record) => {
            if (!category.rowClick) return {};
            return {
              onClick: () => {
                if (category.rowClick === 'tablespace') handleTablespaceClick(record);
                else if (category.rowClick === 'waitEvent') handleWaitEventClick(record);
              },
              style: { cursor: 'pointer' },
            };
          }}
          columns={category.columns.map(col => ({
            title: col.title,
            dataIndex: col.key,
            key: col.key,
            ellipsis: ['event', 'sql_text', 'query', 'info', 'wait_event', 'sql_id'].includes(col.key),
            render: (val) => {
              if (col.format === 'percent' && typeof val === 'number') {
                const colorMap = { 90: '#ff4d4f', 80: '#faad14', 70: '#1890ff' };
                const color = Object.entries(colorMap).find(([t]) => val >= parseInt(t))?.[1] || '#52c41a';
                return <Tag color={color}>{val.toFixed(1)}%</Tag>;
              }
              if (col.format === 'size_mb' && typeof val === 'number') return `${val.toFixed(1)} MB`;
              if (col.format === 'number' && typeof val === 'number') return val.toLocaleString();
              return val != null ? String(val) : '-';
            },
          }))}
        />
      </Card>
    );
  }, [metrics, handleTablespaceClick, handleWaitEventClick]);

  const renderCategory = useCallback((category) => {
    if (category.type === 'table') return renderTableCategory(category);
    return renderMetricCardsCategory(category);
  }, [renderMetricCardsCategory, renderTableCategory]);

  // 所有 hooks 必须在条件返回之前调用（React Hooks 规则）
  const keyMetricPairs = [
    { key: 'active_connections', label: '活跃会话', color: CHART_COLORS.active_connections, yAxisIndex: 1 },
    { key: 'qps', label: 'QPS', color: CHART_COLORS.qps, yAxisIndex: 0 },
    { key: 'tps', label: 'TPS', color: CHART_COLORS.tps, yAxisIndex: 0 },
    { key: 'buffer_hit_ratio', label: '缓冲命中率', color: CHART_COLORS.buffer_hit_ratio, yAxisIndex: 0 },
  ];

  const multiMetricData = useMemo(() => buildMultiMetricChartData(), [buildMultiMetricChartData]);

  const multiMetricChartData = useMemo(() => ([
    { key: 'active_connections', label: '活跃会话', color: CHART_COLORS.active_connections, yAxisIndex: 1 },
    { key: 'qps', label: 'QPS', color: CHART_COLORS.qps, yAxisIndex: 0 },
    { key: 'tps', label: 'TPS', color: CHART_COLORS.tps, yAxisIndex: 0 },
    { key: 'buffer_hit_ratio', label: '缓冲命中率%', color: CHART_COLORS.buffer_hit_ratio, yAxisIndex: 0 },
  ]), []);

  if (loading && !statusData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  if (!statusData) {
    return <Alert message="未找到数据库信息" type="error" />;
  }

  return (
    <div className="database-detail" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <Space wrap>
          <Link to="/databases">
            <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
          </Link>
          <Title level={4} style={{ margin: 0 }}>
            <DatabaseOutlined /> {configInfo?.name || '数据库详情'}
          </Title>
          {getStatusTag(statusData?.status)}
          <Tag color="blue">{DB_TYPE_LABELS[dbType] || dbType}</Tag>
          {statusData?.collected_at && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              最后采集: {dayjs(statusData.collected_at).format('MM-DD HH:mm:ss')}
            </Text>
          )}
        </Space>
      </div>

      <Card title="基本信息" size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={4} size="small">
          <Descriptions.Item label="主机地址">{configInfo?.host || '-'}</Descriptions.Item>
          <Descriptions.Item label="端口">{configInfo?.port || '-'}</Descriptions.Item>
          <Descriptions.Item label="数据库类型">{DB_TYPE_LABELS[dbType] || dbType}</Descriptions.Item>
          <Descriptions.Item label="服务名/库名">{configInfo?.service_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="最后检查">{statusData?.collected_at ? dayjs(statusData.collected_at).format('YYYY-MM-DD HH:mm:ss') : '-'}</Descriptions.Item>
          <Descriptions.Item label="监控状态">{getStatusTag(statusData?.status)}</Descriptions.Item>
        </Descriptions>
      </Card>

      {metricCategories.map(renderCategory)}

      <Card
        title={
          <Space>
            <LineChartOutlined />
            <span>性能趋势</span>
            <Tooltip title="支持框选缩放、底部滑块平移、十字光标查看详情；对比模式可叠加多指标">
              <InfoCircleOutlined style={{ color: '#999' }} />
            </Tooltip>
          </Space>
        }
        size="small"
        style={{ marginBottom: 16 }}
        extra={
          <Space size={8} wrap>
            <Button
              size="small"
              type={comparisonMode ? 'primary' : 'default'}
              icon={<ExpandOutlined />}
              onClick={() => setComparisonMode(!comparisonMode)}
            >
              {comparisonMode ? '单指标视图' : '对比模式'}
            </Button>
            <TimeRangeSelector
              value={timeRangeKey}
              onChange={handleTimeRangeChange}
              autoRefresh={autoRefresh}
              onAutoRefreshChange={setAutoRefresh}
              loading={loading}
              onRefresh={handleRefresh}
              customPresets={TIME_RANGE_CUSTOM_PRESETS}
              showAutoRefresh={true}
              size="small"
            />
          </Space>
        }
      >
        {comparisonMode ? (
          <Card title="多指标对比" size="small" style={{ marginBottom: 12 }}>
            <EChartsTrend
              data={multiMetricData}
              height={400}
              loading={loading}
              showDataZoom={true}
              showMarkLine={false}
              multiMetrics={multiMetricChartData}
              yAxisLabel="数值"
            />
          </Card>
        ) : (
          <>
            <Row gutter={16}>
              {keyMetricPairs.slice(0, 2).map(m => (
                <Col span={12} key={m.key}>
                  <Card title={m.label} size="small" style={{ marginBottom: 12 }}>
                    <EChartsTrend
                      data={buildChartData(m.key)}
                      metricKey={m.key}
                      height={250}
                      loading={loading}
                      showDataZoom={true}
                      showMarkLine={true}
                      stats={computeStats(buildChartData(m.key))}
                      yAxisLabel={m.label}
                      color={m.color}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
            <Row gutter={16}>
              {keyMetricPairs.slice(2, 4).map(m => (
                <Col span={12} key={m.key}>
                  <Card title={m.label} size="small">
                    <EChartsTrend
                      data={buildChartData(m.key)}
                      metricKey={m.key}
                      height={250}
                      loading={loading}
                      showDataZoom={true}
                      showMarkLine={true}
                      stats={computeStats(buildChartData(m.key))}
                      yAxisLabel={m.label}
                      color={m.color}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
          </>
        )}

        <Card size="small" style={{ marginTop: 12, background: '#fafafa' }}>
          <Row gutter={[12, 8]}>
            {keyMetricPairs.map(m => {
              const st = computeStats(buildChartData(m.key));
              return (
                <Col span={6} key={m.key}>
                  <div style={{ textAlign: 'center' }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>{m.label}</Text>
                    <div>
                      <Text style={{ fontSize: 12, color: '#52c41a' }}>μ{st?.avg?.toFixed(1)}</Text>
                      <Text style={{ fontSize: 12, marginLeft: 6, color: '#999' }}>σ{st?.std?.toFixed(1)}</Text>
                    </div>
                    <div>
                      <Text style={{ fontSize: 10, color: '#ff4d4f' }}>max{st?.max?.toFixed(1)}</Text>
                      <Text style={{ fontSize: 10, marginLeft: 6, color: '#1890ff' }}>min{st?.min?.toFixed(1)}</Text>
                    </div>
                  </div>
                </Col>
              );
            })}
          </Row>
        </Card>
      </Card>

      <Card title="告警记录" size="small" style={{ marginBottom: 16 }}>
        <Table
          dataSource={(alerts || []).map(a => ({ ...a, key: a.id }))}
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '级别', dataIndex: 'severity', key: 'severity',
              render: (severity) => {
                const colorMap = { critical: 'red', warning: 'orange', info: 'blue' };
                return <Tag color={colorMap[severity] || 'default'}>{severity?.toUpperCase()}</Tag>;
              },
            },
            { title: '告警类型', dataIndex: 'alert_type', key: 'alert_type' },
            { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
            { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
            {
              title: '状态', dataIndex: 'status', key: 'status',
              render: v => <Tag color={v === 'active' ? 'red' : 'green'}>{v}</Tag>,
            },
            {
              title: '时间', dataIndex: 'created_at', key: 'created_at',
              render: (time) => time ? dayjs(time).format('YYYY-MM-DD HH:mm:ss') : '-',
            },
          ]}
        />
      </Card>

      {/* 指标下钻详情弹窗 */}
      <Modal
        title={
          <Space>
            <LineChartOutlined />
            <span>{selectedMetric?.name || '指标详情'}</span>
          </Space>
        }
        open={metricModalVisible}
        onCancel={() => setMetricModalVisible(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        {selectedMetric && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={[12, 8]}>
                <Col span={6}>
                  <Statistic title="指标名称" value={selectedMetric.name} valueStyle={{ fontSize: 16 }} />
                </Col>
                <Col span={6}>
                  <Statistic title="当前值" value={selectedMetric.value !== undefined ? selectedMetric.value : '-'} valueStyle={{ fontSize: 16, color: '#1890ff' }} />
                </Col>
                <Col span={6}>
                  <Statistic title="数据点数" value={selectedMetric.chartData?.length || 0} valueStyle={{ fontSize: 16 }} />
                </Col>
                <Col span={6}>
                  <Statistic title="时间范围" value={timeRangeKey} valueStyle={{ fontSize: 16 }} />
                </Col>
              </Row>
              {selectedMetric.stats && (
                <Row gutter={[12, 8]} style={{ marginTop: 12 }}>
                  <Col span={4}>
                    <Statistic title="均值" value={selectedMetric.stats.avg?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#52c41a' }} />
                  </Col>
                  <Col span={5}>
                    <Statistic title="最大值" value={selectedMetric.stats.max?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#ff4d4f' }} />
                  </Col>
                  <Col span={5}>
                    <Statistic title="最小值" value={selectedMetric.stats.min?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#1890ff' }} />
                  </Col>
                  <Col span={5}>
                    <Statistic title="标准差" value={selectedMetric.stats.std?.toFixed(2)} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  <Col span={5}>
                    <Statistic title="P95" value={selectedMetric.stats.p95?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#faad14' }} />
                  </Col>
                </Row>
              )}
            </Card>

            {selectedMetric.stats && selectedMetric.chartData && selectedMetric.chartData.length > 0 && (
              (() => {
                const anomalies = detectAnomalies(selectedMetric.chartData, selectedMetric.stats);
                return anomalies.length > 0 ? (
                  <Card
                    size="small"
                    style={{ marginBottom: 16, border: '1px solid #ff4d4f', background: '#fff2f0' }}
                    title={<span style={{ color: '#ff4d4f' }}>⚠️ 异常检测 — 发现 {anomalies.length} 个超出 2σ 的数据点</span>}
                  >
                    <Table
                      dataSource={anomalies.map((a, i) => ({ ...a, key: i }))}
                      size="small"
                      pagination={false}
                      scroll={{ y: 200 }}
                      columns={[
                        { title: '时间', dataIndex: 'time', key: 'time', width: 160 },
                        {
                          title: '值', dataIndex: 'value', key: 'value',
                          render: v => <Text strong style={{ color: '#ff4d4f' }}>{v?.toFixed(2)}</Text>,
                        },
                        {
                          title: 'σ偏差', dataIndex: 'deviation', key: 'deviation',
                          render: v => {
                            const n = Number(v);
                            const color = Math.abs(n) > 3 ? 'red' : 'orange';
                            return <Tag color={color}>{n > 0 ? '+' : ''}{n.toFixed(1)}σ</Tag>;
                          },
                        },
                        {
                          title: '偏离方向', dataIndex: 'deviation', key: 'direction',
                          render: v => Number(v) > 0
                            ? <Tag color="error">⬆ 高于均值</Tag>
                            : <Tag color="processing">⬇ 低于均值</Tag>,
                        },
                        {
                          title: '建议', key: 'suggestion',
                          render: (_, record) => {
                            const n = Number(record.deviation);
                            if (n > 3) return <Text type="danger" style={{ fontSize: 11 }}>严重异常，建议立即排查</Text>;
                            if (n > 2) return <Text type="warning" style={{ fontSize: 11 }}>关注该时段是否有对应告警</Text>;
                            return <Text type="secondary" style={{ fontSize: 11 }}>正常波动范围边缘</Text>;
                          },
                        },
                      ]}
                    />
                  </Card>
                ) : (
                  <Card size="small" style={{ marginBottom: 16, border: '1px solid #52c41a', background: '#f6ffed' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 16 }} />
                      <span style={{ color: '#52c41a' }}>所有数据点均在 2σ 正常范围内，未检测到显著异常</span>
                    </div>
                  </Card>
                );
              })()
            )}

            <Card title="历史趋势" size="small">
              <EChartsTrend
                data={selectedMetric.chartData || []}
                metricKey={selectedMetric.key}
                height={380}
                loading={false}
                showDataZoom={true}
                showMarkLine={true}
                stats={selectedMetric.stats}
                yAxisLabel={selectedMetric.name}
              />
            </Card>

            {selectedMetric.chartData && selectedMetric.chartData.length > 0 && (
              <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                <Table
                  dataSource={selectedMetric.chartData.slice().reverse().map((item, idx) => ({
                    key: idx,
                    time: item.time,
                    value: item.value,
                    deviation: selectedMetric.stats ? ((item.value - selectedMetric.stats.avg) / (selectedMetric.stats.std || 1)).toFixed(2) : null,
                  }))}
                  size="small"
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '时间', dataIndex: 'time', key: 'time' },
                    {
                      title: '值', dataIndex: 'value', key: 'value',
                      render: v => typeof v === 'number' ? v.toFixed(2) : v,
                    },
                    {
                      title: 'σ偏差', dataIndex: 'deviation', key: 'deviation',
                      render: v => {
                        if (v == null) return '-';
                        const n = Number(v);
                        const color = Math.abs(n) > 2 ? '#ff4d4f' : Math.abs(n) > 1 ? '#faad14' : '#52c41a';
                        return <Tag color={color}>{n}σ</Tag>;
                      },
                    },
                  ]}
                />
              </Card>
            )}
          </div>
        )}
      </Modal>

      {/* 表空间下钻详情弹窗 */}
      <Modal
        title={
          <Space>
            <DatabaseOutlined />
            <span>表空间详情: {selectedTablespace?.name || ''}</span>
          </Space>
        }
        open={tablespaceModalVisible}
        onCancel={() => setTablespaceModalVisible(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        {selectedTablespace && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={24}>
                <Col span={6}><Statistic title="表空间名" value={selectedTablespace.name || '-'} /></Col>
                <Col span={6}><Statistic title="总大小(MB)" value={selectedTablespace.total_mb?.toFixed(2) || '-'} /></Col>
                <Col span={6}><Statistic title="已使用(MB)" value={selectedTablespace.used_mb?.toFixed(2) || '-'} /></Col>
                <Col span={6}>
                  <Statistic
                    title="使用率" value={selectedTablespace.used_pct?.toFixed(2) || '0'} suffix="%"
                    valueStyle={{ color: (selectedTablespace.used_pct || 0) > 90 ? '#ff4d4f' : (selectedTablespace.used_pct || 0) > 80 ? '#faad14' : '#52c41a' }}
                  />
                </Col>
              </Row>
              {selectedTablespace.stats && (
                <Row gutter={[12, 8]} style={{ marginTop: 12 }}>
                  <Col span={6}><Statistic title="均值" value={selectedTablespace.stats.avg?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#52c41a' }} suffix="%" /></Col>
                  <Col span={6}><Statistic title="最大值" value={selectedTablespace.stats.max?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#ff4d4f' }} suffix="%" /></Col>
                  <Col span={6}><Statistic title="最小值" value={selectedTablespace.stats.min?.toFixed(2)} valueStyle={{ fontSize: 14, color: '#1890ff' }} suffix="%" /></Col>
                  <Col span={6}><Statistic title="标准差" value={selectedTablespace.stats.std?.toFixed(2)} valueStyle={{ fontSize: 14 }} /></Col>
                </Row>
              )}
            </Card>

            <Card title="使用率历史趋势" size="small">
              {selectedTablespace.loading ? (
                <div style={{ textAlign: 'center', padding: 50 }}><Spin tip="加载历史数据中..." /></div>
              ) : (
                <EChartsTrend
                  data={selectedTablespace.chartData || []}
                  metricKey="tablespace"
                  height={380}
                  loading={false}
                  showDataZoom={true}
                  showMarkLine={true}
                  stats={selectedTablespace.stats}
                  yAxisLabel="使用率(%)"
                  color="#faad14"
                />
              )}
            </Card>

            {selectedTablespace.chartData && selectedTablespace.chartData.length > 0 && !selectedTablespace.loading && (
              <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                <Table
                  dataSource={selectedTablespace.chartData.slice().reverse().map((item, idx) => ({
                    key: idx, time: item.time, value: item.value,
                  }))}
                  size="small" pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '时间', dataIndex: 'time', key: 'time' },
                    { title: '使用率(%)', dataIndex: 'value', key: 'value', render: v => typeof v === 'number' ? v.toFixed(2) : v },
                  ]}
                />
              </Card>
            )}
          </div>
        )}
      </Modal>

      {/* 等待事件下钻详情弹窗 */}
      <Modal
        title={
          <Space>
            <ThunderboltOutlined />
            <span>等待事件详情: {selectedWaitEvent?.event || ''}</span>
          </Space>
        }
        open={waitEventModalVisible}
        onCancel={() => setWaitEventModalVisible(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        {selectedWaitEvent && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={24}>
                <Col span={6}><Statistic title="事件名" value={selectedWaitEvent.event || '-'} /></Col>
                <Col span={6}><Statistic title="总等待次数" value={formatNumber(selectedWaitEvent.total_waits)} /></Col>
                <Col span={6}><Statistic title="总等待时间(ms)" value={formatNumber(selectedWaitEvent.time_waited)} /></Col>
                <Col span={6}><Statistic title="平均等待(ms)" value={selectedWaitEvent.average_wait?.toFixed(2) || '-'} /></Col>
              </Row>
              {selectedWaitEvent.stats && (
                <Row gutter={[12, 8]} style={{ marginTop: 12 }}>
                  <Col span={6}><Statistic title="均值" value={formatNumber(selectedWaitEvent.stats.avg)} valueStyle={{ fontSize: 14, color: '#52c41a' }} /></Col>
                  <Col span={6}><Statistic title="最大值" value={formatNumber(selectedWaitEvent.stats.max)} valueStyle={{ fontSize: 14, color: '#ff4d4f' }} /></Col>
                  <Col span={6}><Statistic title="最小值" value={formatNumber(selectedWaitEvent.stats.min)} valueStyle={{ fontSize: 14, color: '#1890ff' }} /></Col>
                  <Col span={6}><Statistic title="标准差" value={formatNumber(selectedWaitEvent.stats.std)} valueStyle={{ fontSize: 14 }} /></Col>
                </Row>
              )}
            </Card>

            <Card title="等待次数历史趋势" size="small">
              {selectedWaitEvent.loading ? (
                <div style={{ textAlign: 'center', padding: 50 }}><Spin tip="加载历史数据中..." /></div>
              ) : (
                <EChartsTrend
                  data={selectedWaitEvent.chartData || []}
                  metricKey="wait_event"
                  height={380}
                  loading={false}
                  showDataZoom={true}
                  showMarkLine={true}
                  stats={selectedWaitEvent.stats}
                  yAxisLabel="等待次数"
                  color="#eb2f96"
                />
              )}
            </Card>

            {selectedWaitEvent.chartData && selectedWaitEvent.chartData.length > 0 && !selectedWaitEvent.loading && (
              <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                <Table
                  dataSource={selectedWaitEvent.chartData.slice().reverse().map((item, idx) => ({
                    key: idx, time: item.time, value: item.value,
                  }))}
                  size="small" pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '时间', dataIndex: 'time', key: 'time' },
                    { title: '等待次数', dataIndex: 'value', key: 'value', render: v => formatNumber(v) },
                  ]}
                />
              </Card>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default DatabaseDetail;

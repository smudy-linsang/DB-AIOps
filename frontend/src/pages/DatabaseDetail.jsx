import { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { 
  Card, Row, Col, Statistic, Typography, Space, Tag, 
  Table, Tabs, Button, Descriptions, Spin, Alert, Modal, Empty, Progress
} from 'antd';
import { 
  ArrowLeftOutlined, ReloadOutlined, DatabaseOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined,
  ThunderboltOutlined, DashboardOutlined, LineChartOutlined
} from '@ant-design/icons';
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, 
  CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { databaseAPI, alertAPI } from '../services/api';
import { 
  DB_TYPE_LABELS, DB_METRIC_CATEGORIES, 
  getMetricCategories, formatMetricValue, getMetricThresholdColor,
  getMetricRawValue
} from '../config/dbMetricsConfig';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

const DatabaseDetail = () => {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [configInfo, setConfigInfo] = useState(null);  // 数据库配置信息
  const [statusData, setStatusData] = useState(null);  // 最新状态和指标
  const [metricsHistory, setMetricsHistory] = useState([]);  // 历史指标数据
  const [alerts, setAlerts] = useState([]);
  const [timeRange, setTimeRange] = useState('24h');
  
  // 指标下钻弹窗状态
  const [metricModalVisible, setMetricModalVisible] = useState(false);
  const [selectedMetric, setSelectedMetric] = useState(null);  // { key, name, value }
  
  // 表空间下钻弹窗状态
  const [tablespaceModalVisible, setTablespaceModalVisible] = useState(false);
  const [selectedTablespace, setSelectedTablespace] = useState(null);  // { name, total_mb, used_mb, used_pct }
  
  // 等待事件下钻弹窗状态
  const [waitEventModalVisible, setWaitEventModalVisible] = useState(false);
  const [selectedWaitEvent, setSelectedWaitEvent] = useState(null);  // { event, total_waits, time_waited }
  
  // 指标点击下钻
  const handleMetricClick = (metricKey, metricName, metricValue) => {
    setSelectedMetric({
      key: metricKey,
      name: metricName,
      value: metricValue,
      chartData: formatChartData(metricKey)
    });
    setMetricModalVisible(true);
  };
  
  // 表空间点击下钻 - 发起专门请求获取历史趋势
  const handleTablespaceClick = async (tablespace) => {
    // 先显示loading状态
    setSelectedTablespace({
      name: tablespace.name,
      total_mb: tablespace.total_mb,
      used_mb: tablespace.used_mb,
      used_pct: tablespace.used_pct,
      chartData: [],
      loading: true
    });
    setTablespaceModalVisible(true);
    
    try {
      // 发起专门的API请求获取该表空间的历史数据
      const metricName = `tablespace_${tablespace.name}_used_pct`;
      const response = await databaseAPI.getMetrics(id, { 
        metric: metricName,
        time: timeRange 
      });
      
      const chartData = (response.metrics || [])
        .map(item => ({
          time: dayjs(item.timestamp).format('HH:mm'),
          value: typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0
        }))
        .reverse();
      
      setSelectedTablespace(prev => ({
        ...prev,
        chartData,
        loading: false
      }));
    } catch (error) {
      console.error('获取表空间历史数据失败:', error);
      setSelectedTablespace(prev => ({
        ...prev,
        chartData: [],
        loading: false
      }));
    }
  };
  
  // 等待事件点击下钻 - 发起专门请求获取历史趋势
  const handleWaitEventClick = async (waitEvent) => {
    // 先显示loading状态
    setSelectedWaitEvent({
      event: waitEvent.event,
      total_waits: waitEvent.total_waits,
      time_waited: waitEvent.time_waited,
      average_wait: waitEvent.average_wait,
      chartData: [],
      loading: true
    });
    setWaitEventModalVisible(true);
    
    try {
      // 发起专门的API请求获取该等待事件的历史数据
      const metricName = `wait_event_${waitEvent.event}`;
      const response = await databaseAPI.getMetrics(id, { 
        metric: metricName,
        time: timeRange 
      });
      
      const chartData = (response.metrics || [])
        .map(item => ({
          time: dayjs(item.timestamp).format('HH:mm'),
          value: typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0
        }))
        .reverse();
      
      setSelectedWaitEvent(prev => ({
        ...prev,
        chartData,
        loading: false
      }));
    } catch (error) {
      console.error('获取等待事件历史数据失败:', error);
      setSelectedWaitEvent(prev => ({
        ...prev,
        chartData: [],
        loading: false
      }));
    }
  };

  const fetchData = async (currentTimeRange) => {
    setLoading(true);
    try {
      // 获取数据库配置信息
      const configResponse = await databaseAPI.list();
      const dbConfig = configResponse?.databases?.find(db => db.id === parseInt(id));
      
      // 获取数据库状态（包含最新指标）
      const statusResponse = await databaseAPI.getStatus(id);
      
      // 获取历史指标
      const metricsResponse = await databaseAPI.getMetrics(id, { time: currentTimeRange || '24h' });
      
      // 获取告警
      const alertsResponse = await alertAPI.getByDatabase(id);
      
      setConfigInfo(dbConfig || {});
      setStatusData(statusResponse || {});
      setMetricsHistory(metricsResponse?.metrics || []);
      setAlerts(alertsResponse?.alerts || []);
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) {
      fetchData(timeRange);
      const interval = setInterval(() => fetchData(timeRange), 30000);
      return () => clearInterval(interval);
    }
  }, [id, timeRange]);

  // 获取当前指标数据
  const metrics = statusData?.metrics || {};
  const dbType = configInfo?.db_type || 'unknown';

  // 获取数据库类型对应的指标分类配置
  const metricCategories = useMemo(() => getMetricCategories(dbType), [dbType]);

  const getStatusTag = (status) => {
    const statusMap = {
      UP: { color: 'green', text: '在线', icon: <CheckCircleOutlined /> },
      DOWN: { color: 'red', text: '离线', icon: <CloseCircleOutlined /> },
      UNKNOWN: { color: 'default', text: '未知', icon: <ClockCircleOutlined /> }
    };
    const config = statusMap[status] || { color: 'default', text: status, icon: null };
    return (
      <Tag color={config.color} icon={config.icon}>
        {config.text}
      </Tag>
    );
  };

  // 格式化数字
  const formatNumber = (num) => {
    if (num === null || num === undefined) return '-';
    if (typeof num !== 'number') return num;
    if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(2) + 'K';
    return num.toFixed(2);
  };

  // 格式化图表数据
  const formatChartData = (metricKey) => {
    if (!metricsHistory || metricsHistory.length === 0) return [];
    return metricsHistory
      .filter(item => item.metric === metricKey)
      .map(item => ({
        time: dayjs(item.timestamp).format('HH:mm'),
        value: typeof item.value === 'number' ? item.value : parseFloat(item.value) || 0
      }))
      .reverse();
  };

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

  const chartColors = {
    cpu: '#1890ff',
    memory: '#722ed1',
    disk: '#faad14',
    sessions: '#52c41a',
    connections: '#f5222d',
    qps: '#13c2c2',
    tps: '#eb2f96'
  };

  /**
   * ============================================================
   * 动态渲染：根据 dbMetricsConfig 配置渲染指标卡片分类
   * ============================================================
   */
  const renderMetricCardsCategory = (category) => {
    if (category.showWhen && !category.showWhen(metrics)) return null;
    
    return (
      <Card title={category.title} key={category.key} size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 16]}>
          {category.metrics.map(metricDef => {
            // 兼容 fallbackKey: 如果主key无值，尝试备选key
            let value = metrics[metricDef.key];
            if ((value === undefined || value === null) && metricDef.fallbackKey) {
              value = metrics[metricDef.fallbackKey];
            }
            const thresholdColor = getMetricThresholdColor(value, metricDef.key);
            const displayValue = formatMetricValue(value, metricDef.format);
            
            return (
              <Col span={metricDef.highlight ? 6 : 6} key={metricDef.key}>
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
                      cursor: metricDef.clickable !== false ? 'pointer' : 'default'
                    }}
                  />
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>
    );
  };

  /**
   * 渲染表格型指标分类
   */
  const renderTableCategory = (category) => {
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
              style: { cursor: 'pointer' }
            };
          }}
          columns={category.columns.map(col => ({
            title: col.title,
            dataIndex: col.key,
            key: col.key,
            ellipsis: col.key === 'event' || col.key === 'sql_text' || col.key === 'query' || col.key === 'info' || col.key === 'wait_event' || col.key === 'sql_id',
            render: (val) => {
              if (val === null || val === undefined) return '-';
              // 使用率列显示颜色标签
              if (col.key === 'used_pct') {
                const v = Number(val);
                const color = v > 90 ? '#ff4d4f' : v > 80 ? '#faad14' : '#52c41a';
                return <Tag color={color}>{v.toFixed(2)}%</Tag>;
              }
              // 状态列
              if (col.key === 'status' || col.key === 'state' || col.key === 'node_state') {
                const colorMap = {
                  'ACTIVE': 'green', 'OPEN': 'green', 'UP': 'green', '正常': 'green',
                  'HEALTHY': 'green', 'ONLINE': 'green', 'running': 'green',
                  'INACTIVE': 'default', 'DOWN': 'red', 'OFFLINE': 'red',
                  'STANDBY': 'blue', 'UNKNOWN': 'default',
                };
                return <Tag color={colorMap[val] || 'default'}>{val}</Tag>;
              }
              // 格式化
              if (col.format) return formatMetricValue(val, col.format);
              // 大数字格式化
              if (typeof val === 'number' && (col.key === 'total_waits' || col.key === 'time_waited' || col.key === 'buffer_gets' || col.key === 'exec_count' || col.key === 'rows_examined' || col.key === 'rows' || col.key === 'calls')) {
                return formatNumber(val);
              }
              return String(val);
            }
          }))}
        />
      </Card>
    );
  };

  /**
   * 渲染单个分类区块
   */
  const renderCategory = (category) => {
    if (category.type === 'table') {
      return renderTableCategory(category);
    }
    return renderMetricCardsCategory(category);
  };

  return (
    <div className="database-detail" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Link to="/databases">
          <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
        </Link>
        <Space style={{ marginLeft: 16 }}>
          <Title level={4} style={{ margin: 0 }}>
            <DatabaseOutlined /> {configInfo?.name || '数据库详情'}
          </Title>
          {getStatusTag(statusData?.status)}
          <Tag color="blue">{DB_TYPE_LABELS[dbType] || dbType}</Tag>
        </Space>
        <Button 
          icon={<ReloadOutlined />} 
          onClick={() => fetchData(timeRange)}
          loading={loading}
          style={{ float: 'right' }}
        >
          刷新
        </Button>
      </div>

      {/* 基本信息 */}
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

      {/* ============================================================
          动态渲染所有数据库类型的指标分类（v3.0 多数据库支持）
          替代原有的 Oracle 硬编码区块
          ============================================================ */}
      {metricCategories.map(renderCategory)}

      {/* 通用性能趋势图 */}
      <Card title="性能趋势" size="small" style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 16 }}>
          <Button onClick={() => setTimeRange('1h')}>1小时</Button>
          <Button onClick={() => setTimeRange('6h')}>6小时</Button>
          <Button onClick={() => setTimeRange('24h')}>24小时</Button>
          <Button onClick={() => setTimeRange('7d')}>7天</Button>
        </Space>

        <Row gutter={16}>
          <Col span={12}>
            <Card title="活跃会话趋势" size="small">
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={formatChartData('active_connections')}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="time" />
                  <YAxis />
                  <Tooltip />
                  <Line type="monotone" dataKey="value" stroke={chartColors.sessions} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          </Col>
          <Col span={12}>
            <Card title="QPS趋势" size="small">
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={formatChartData('qps')}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="time" />
                  <YAxis />
                  <Tooltip />
                  <Line type="monotone" dataKey="value" stroke={chartColors.qps} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          </Col>
        </Row>
      </Card>

      {/* 告警记录 */}
      <Card title="告警记录" size="small" style={{ marginBottom: 16 }}>
        <Table
          dataSource={(alerts || []).map(a => ({ ...a, key: a.id }))}
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '级别',
              dataIndex: 'severity',
              key: 'severity',
              render: (severity) => {
                const colorMap = { critical: 'red', warning: 'orange', info: 'blue' };
                return <Tag color={colorMap[severity] || 'default'}>{severity?.toUpperCase()}</Tag>;
              }
            },
            { title: '告警类型', dataIndex: 'alert_type', key: 'alert_type' },
            { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
            { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
            { title: '状态', dataIndex: 'status', key: 'status',
              render: v => <Tag color={v === 'active' ? 'red' : 'green'}>{v}</Tag>
            },
            { title: '时间', dataIndex: 'created_at', key: 'created_at',
              render: (time) => time ? dayjs(time).format('YYYY-MM-DD HH:mm:ss') : '-'
            }
          ]}
        />
      </Card>

      {/* 原始指标JSON（调试用） */}
      <Card title="原始指标数据" size="small">
        <pre style={{ maxHeight: 400, overflow: 'auto', fontSize: 12 }}>
          {JSON.stringify(metrics, null, 2)}
        </pre>
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
        width={900}
        destroyOnClose
      >
        {selectedMetric && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={24}>
                <Col span={8}>
                  <Statistic 
                    title="指标名称" 
                    value={selectedMetric.name} 
                  />
                </Col>
                <Col span={8}>
                  <Statistic 
                    title="当前值" 
                    value={selectedMetric.value !== undefined ? selectedMetric.value : '-'} 
                  />
                </Col>
                <Col span={8}>
                  <Statistic 
                    title="数据点数" 
                    value={selectedMetric.chartData?.length || 0} 
                  />
                </Col>
              </Row>
            </Card>
            
            <Card title="历史趋势" size="small">
              {selectedMetric.chartData && selectedMetric.chartData.length > 0 ? (
                <ResponsiveContainer width="100%" height={350}>
                  <LineChart data={selectedMetric.chartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="time" />
                    <YAxis />
                    <Tooltip 
                      formatter={(value) => [typeof value === 'number' ? value.toFixed(2) : value, '值']}
                      labelFormatter={(label) => `时间: ${label}`}
                    />
                    <Legend />
                    <Line 
                      type="monotone" 
                      dataKey="value" 
                      name={selectedMetric.name}
                      stroke="#1890ff" 
                      strokeWidth={2}
                      dot={{ r: 3 }}
                      activeDot={{ r: 5 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无历史数据" />
              )}
            </Card>
            
            {selectedMetric.chartData && selectedMetric.chartData.length > 0 && (
              <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                <Table
                  dataSource={selectedMetric.chartData.slice().reverse().map((item, idx) => ({
                    key: idx,
                    time: item.time,
                    value: item.value
                  }))}
                  size="small"
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '时间', dataIndex: 'time', key: 'time' },
                    { 
                      title: '值', 
                      dataIndex: 'value', 
                      key: 'value',
                      render: v => typeof v === 'number' ? v.toFixed(2) : v
                    }
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
        width={900}
        destroyOnClose
      >
        {selectedTablespace && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={24}>
                <Col span={6}>
                  <Statistic title="表空间名" value={selectedTablespace.name || '-'} />
                </Col>
                <Col span={6}>
                  <Statistic title="总大小(MB)" value={selectedTablespace.total_mb?.toFixed(2) || '-'} />
                </Col>
                <Col span={6}>
                  <Statistic title="已使用(MB)" value={selectedTablespace.used_mb?.toFixed(2) || '-'} />
                </Col>
                <Col span={6}>
                  <Statistic 
                    title="使用率" 
                    value={selectedTablespace.used_pct?.toFixed(2) || '0'} 
                    suffix="%"
                    valueStyle={{ 
                      color: (selectedTablespace.used_pct || 0) > 90 ? '#ff4d4f' : (selectedTablespace.used_pct || 0) > 80 ? '#faad14' : '#52c41a'
                    }}
                  />
                </Col>
              </Row>
            </Card>
            
            <Card title="使用率历史趋势" size="small">
              {selectedTablespace.loading ? (
                <div style={{ textAlign: 'center', padding: 50 }}>
                  <Spin tip="加载历史数据中..." />
                </div>
              ) : selectedTablespace.chartData && selectedTablespace.chartData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={350}>
                    <LineChart data={selectedTablespace.chartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis domain={[0, 100]} />
                      <Tooltip 
                        formatter={(value) => [typeof value === 'number' ? value.toFixed(2) : value, '使用率(%)']}
                        labelFormatter={(label) => `时间: ${label}`}
                      />
                      <Legend />
                      <Line 
                        type="monotone" 
                        dataKey="value" 
                        name="使用率(%)"
                        stroke="#faad14" 
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                  <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                    <Table
                      dataSource={selectedTablespace.chartData.slice().reverse().map((item, idx) => ({
                        key: idx,
                        time: item.time,
                        value: item.value
                      }))}
                      size="small"
                      pagination={{ pageSize: 10 }}
                      columns={[
                        { title: '时间', dataIndex: 'time', key: 'time' },
                        { 
                          title: '使用率(%)', 
                          dataIndex: 'value', 
                          key: 'value',
                          render: v => typeof v === 'number' ? v.toFixed(2) : v
                        }
                      ]}
                    />
                  </Card>
                </>
              ) : (
                <Empty description="暂无历史数据（可能该表空间数据未被采集）" />
              )}
            </Card>
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
        width={900}
        destroyOnClose
      >
        {selectedWaitEvent && (
          <div>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={24}>
                <Col span={6}>
                  <Statistic title="事件名" value={selectedWaitEvent.event || '-'} />
                </Col>
                <Col span={6}>
                  <Statistic title="总等待次数" value={formatNumber(selectedWaitEvent.total_waits)} />
                </Col>
                <Col span={6}>
                  <Statistic title="总等待时间(ms)" value={formatNumber(selectedWaitEvent.time_waited)} />
                </Col>
                <Col span={6}>
                  <Statistic title="平均等待时间(ms)" value={selectedWaitEvent.average_wait?.toFixed(2) || '-'} />
                </Col>
              </Row>
            </Card>
            
            <Card title="等待次数历史趋势" size="small">
              {selectedWaitEvent.loading ? (
                <div style={{ textAlign: 'center', padding: 50 }}>
                  <Spin tip="加载历史数据中..." />
                </div>
              ) : selectedWaitEvent.chartData && selectedWaitEvent.chartData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={350}>
                    <LineChart data={selectedWaitEvent.chartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis />
                      <Tooltip 
                        formatter={(value) => [typeof value === 'number' ? value.toFixed(2) : value, '等待次数']}
                        labelFormatter={(label) => `时间: ${label}`}
                      />
                      <Legend />
                      <Line 
                        type="monotone" 
                        dataKey="value" 
                        name="等待次数"
                        stroke="#eb2f96" 
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                  <Card title="历史数据明细" size="small" style={{ marginTop: 16 }}>
                    <Table
                      dataSource={selectedWaitEvent.chartData.slice().reverse().map((item, idx) => ({
                        key: idx,
                        time: item.time,
                        value: item.value
                      }))}
                      size="small"
                      pagination={{ pageSize: 10 }}
                      columns={[
                        { title: '时间', dataIndex: 'time', key: 'time' },
                        { 
                          title: '等待次数', 
                          dataIndex: 'value', 
                          key: 'value',
                          render: v => formatNumber(v)
                        }
                      ]}
                    />
                  </Card>
                </>
              ) : (
                <Empty description="暂无历史数据（可能该等待事件数据未被采集）" />
              )}
            </Card>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default DatabaseDetail;

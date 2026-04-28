import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { 
  Card, Row, Col, Statistic, Typography, Space, Tag, 
  Table, Tabs, Button, Descriptions, Spin, Alert, Modal, Empty
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
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

// 数据库类型映射
const DB_TYPE_NAME = {
  'oracle': 'Oracle',
  'mysql': 'MySQL',
  'pgsql': 'PostgreSQL',
  'dm': '达梦数据库',
  'gbase': 'GBase',
  'tdsql': 'TDSQL'
};

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

  // Oracle 特定指标提取
  const isOracle = dbType === 'oracle';
  const isPostgres = dbType === 'pgsql';
  const isMySQL = dbType === 'mysql';

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
          <Tag>{DB_TYPE_NAME[dbType] || dbType}</Tag>
        </Space>
        <Button 
          icon={<ReloadOutlined />} 
          onClick={fetchData}
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
          <Descriptions.Item label="数据库类型">{DB_TYPE_NAME[dbType] || dbType}</Descriptions.Item>
          <Descriptions.Item label="服务名/库名">{configInfo?.service_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="最后检查">{statusData?.collected_at ? dayjs(statusData.collected_at).format('YYYY-MM-DD HH:mm:ss') : '-'}</Descriptions.Item>
          <Descriptions.Item label="监控状态">{getStatusTag(statusData?.status)}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Oracle 数据库核心指标 */}
      {isOracle && (
        <>
          {/* Oracle 基础信息 */}
          <Card title="Oracle 实例信息" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={4}><Statistic title="实例名" value={metrics.instance_name || '-'} /></Col>
              <Col span={4}><Statistic title="主机名" value={metrics.host_name || '-'} /></Col>
              <Col span={4}><Statistic title="数据库版本" value={(metrics.version || '').substring(0, 30) || '-'} /></Col>
              <Col span={4}><Statistic title="打开模式" value={metrics.open_mode || '-'} /></Col>
              <Col span={4}><Statistic title="数据库角色" value={metrics.database_role || '-'} /></Col>
              <Col span={4}><Statistic title="运行时间" value={metrics.uptime_seconds ? `${Math.floor(metrics.uptime_seconds / 86400)}天` : '-'} /></Col>
            </Row>
          </Card>

          {/* Oracle 会话与连接 */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('active_connections', '活跃会话', metrics.active_connections || metrics.active_sessions || 0)}>
                <Statistic 
                  title="活跃会话" 
                  value={metrics.active_connections || metrics.active_sessions || 0}
                  prefix={<ThunderboltOutlined />}
                  valueStyle={{ color: (metrics.active_connections || 0) > 80 ? '#ff4d4f' : '#52c41a', cursor: 'pointer' }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('total_connections', '总会话数', metrics.total_connections || metrics.total_sessions || 0)}>
                <Statistic 
                  title="总会话数" 
                  value={metrics.total_connections || metrics.total_sessions || 0}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('conn_usage_pct', '连接使用率', metrics.conn_usage_pct || 0)}>
                <Statistic 
                  title="连接使用率" 
                  value={metrics.conn_usage_pct || 0}
                  suffix="%"
                  valueStyle={{ 
                    color: (metrics.conn_usage_pct || 0) > 80 ? '#ff4d4f' : (metrics.conn_usage_pct || 0) > 60 ? '#faad14' : '#52c41a',
                    cursor: 'pointer'
                  }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('max_connections', '最大连接数', metrics.max_connections || metrics.max_conn || 0)}>
                <Statistic title="最大连接数" value={metrics.max_connections || metrics.max_conn || 0} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('qps', 'QPS', metrics.qps || 0)}>
                <Statistic title="QPS" value={metrics.qps || 0} suffix="/s" />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small" hoverable onClick={() => handleMetricClick('tps', 'TPS', metrics.tps || 0)}>
                <Statistic title="TPS" value={metrics.tps || 0} suffix="/s" />
              </Card>
            </Col>
          </Row>

          {/* Oracle 性能指标 */}
          <Card title="Oracle 性能指标" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('logical_reads', '逻辑读', metrics.logical_reads)}><Statistic title="逻辑读" value={formatNumber(metrics.logical_reads)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('physical_reads', '物理读', metrics.physical_reads)}><Statistic title="物理读" value={formatNumber(metrics.physical_reads)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('physical_writes', '物理写', metrics.physical_writes)}><Statistic title="物理写" value={formatNumber(metrics.physical_writes)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('buffer_hit_ratio', '缓冲命中率', metrics.buffer_hit_ratio)}><Statistic title="缓冲命中率" value={metrics.buffer_hit_ratio || 0} suffix="%" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('library_cache_hit_ratio', '库缓存命中率', metrics.library_cache_hit_ratio)}><Statistic title="库缓存命中率" value={metrics.library_cache_hit_ratio || 0} suffix="%" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('redo_generation_bytes', 'Redo生成量', metrics.redo_generation_bytes)}><Statistic title="Redo生成量" value={formatNumber(metrics.redo_generation_bytes)} /></Card></Col>
            </Row>
            <Row gutter={16} style={{ marginTop: 16 }}>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('exec_count', '执行次数', metrics.exec_count)}><Statistic title="执行次数" value={formatNumber(metrics.exec_count)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('commits', '事务提交', metrics.commits)}><Statistic title="事务提交" value={formatNumber(metrics.commits)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('rollbacks', '事务回滚', metrics.rollbacks)}><Statistic title="事务回滚" value={formatNumber(metrics.rollbacks)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('parse_count_total', '总解析数', metrics.parse_count_total)}><Statistic title="总解析数" value={formatNumber(metrics.parse_count_total)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('parse_count_hard', '硬解析数', metrics.parse_count_hard)}><Statistic title="硬解析数" value={formatNumber(metrics.parse_count_hard)} /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('db_time_seconds', 'DB Time', metrics.db_time_seconds)}><Statistic title="DB Time" value={metrics.db_time_seconds ? metrics.db_time_seconds.toFixed(2) + 's' : '-'} /></Card></Col>
            </Row>
          </Card>

          {/* Oracle 内存池 (SGA/PGA) */}
          <Card title="Oracle 内存池 (SGA/PGA)" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('buffer_cache_mb', 'Buffer Cache', metrics.buffer_cache_mb)}><Statistic title="Buffer Cache" value={metrics.buffer_cache_mb || 0} suffix="MB" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('shared_pool_mb', 'Shared Pool', metrics.shared_pool_mb)}><Statistic title="Shared Pool" value={metrics.shared_pool_mb || 0} suffix="MB" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('java_pool_mb', 'Java Pool', metrics.java_pool_mb)}><Statistic title="Java Pool" value={metrics.java_pool_mb || 0} suffix="MB" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('large_pool_mb', 'Large Pool', metrics.large_pool_mb)}><Statistic title="Large Pool" value={metrics.large_pool_mb || 0} suffix="MB" /></Card></Col>
              <Col span={4}><Card size="small" hoverable onClick={() => handleMetricClick('pga_used_mb', 'PGA Used', metrics.pga_used_mb)}><Statistic title="PGA Used" value={metrics.pga_used_mb || 0} suffix="MB" /></Card></Col>
            </Row>
          </Card>

          {/* Oracle 表空间 */}
          <Card title="Oracle 表空间使用" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.tablespaces || []).map((tbs, idx) => ({ ...tbs, key: idx }))}
              size="small"
              pagination={false}
              onRow={(record) => ({
                onClick: () => handleTablespaceClick(record),
                style: { cursor: 'pointer' }
              })}
              columns={[
                { title: '表空间名', dataIndex: 'name', key: 'name' },
                { title: '总大小(MB)', dataIndex: 'total_mb', key: 'total_mb', render: v => v?.toFixed(2) || '-' },
                { title: '已使用(MB)', dataIndex: 'used_mb', key: 'used_mb', render: v => v?.toFixed(2) || '-' },
                { 
                  title: '使用率', 
                  dataIndex: 'used_pct', 
                  key: 'used_pct',
                  render: v => {
                    const color = v > 90 ? '#ff4d4f' : v > 80 ? '#faad14' : '#52c41a';
                    return <Tag color={color}>{v?.toFixed(2) || '-'}%</Tag>;
                  }
                }
              ]}
            />
          </Card>

          {/* Oracle 临时表空间 */}
          <Card title="Oracle 临时表空间" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.temp_tablespaces || []).map((tbs, idx) => ({ ...tbs, key: idx }))}
              size="small"
              pagination={false}
              columns={[
                { title: '表空间名', dataIndex: 'name', key: 'name' },
                { title: '大小(MB)', dataIndex: 'size_mb', key: 'size_mb', render: v => v?.toFixed(2) || '-' }
              ]}
            />
          </Card>

          {/* Oracle UNDO表空间 */}
          <Card title="Oracle UNDO表空间" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.undo_tablespaces || []).map((tbs, idx) => ({ ...tbs, key: idx }))}
              size="small"
              pagination={false}
              columns={[
                { title: '表空间名', dataIndex: 'name', key: 'name' },
                { title: '状态', dataIndex: 'status', key: 'status' },
                { title: '大小(MB)', dataIndex: 'size_mb', key: 'size_mb', render: v => v?.toFixed(2) || '-' }
              ]}
            />
          </Card>

          {/* Oracle 锁等待 */}
          <Card title="Oracle 锁等待" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <Statistic title="锁等待数量" value={metrics.lock_wait_count || 0} />
              </Col>
            </Row>
            <Table
              dataSource={(metrics.locks || []).map((lock, idx) => ({ ...lock, key: idx }))}
              size="small"
              pagination={false}
              columns={[
                { title: '阻塞者', dataIndex: 'blocker_id', key: 'blocker_id' },
                { title: '阻塞者用户', dataIndex: 'blocker_user', key: 'blocker_user' },
                { title: '等待者', dataIndex: 'waiter_id', key: 'waiter_id' },
                { title: '等待者用户', dataIndex: 'waiter_user', key: 'waiter_user' },
                { title: '等待时间(秒)', dataIndex: 'seconds', key: 'seconds' },
                { title: '等待事件', dataIndex: 'wait_event', key: 'wait_event', ellipsis: true }
              ]}
            />
          </Card>

          {/* Oracle Top等待事件 */}
          <Card title="Oracle Top等待事件" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.top_wait_events || []).map((evt, idx) => ({ ...evt, key: idx }))}
              size="small"
              pagination={false}
              onRow={(record) => ({
                onClick: () => handleWaitEventClick(record),
                style: { cursor: 'pointer' }
              })}
              columns={[
                { title: '事件名', dataIndex: 'event', key: 'event', ellipsis: true },
                { title: '总等待次数', dataIndex: 'total_waits', key: 'total_waits', render: v => formatNumber(v) },
                { title: '总等待时间(ms)', dataIndex: 'time_waited', key: 'time_waited', render: v => formatNumber(v) },
                { title: '平均等待时间(ms)', dataIndex: 'average_wait', key: 'average_wait', render: v => v?.toFixed(2) || '-' }
              ]}
            />
          </Card>

          {/* Oracle 会话列表 */}
          <Card title="Oracle 会话列表" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.session_list || []).map((sess, idx) => ({ ...sess, key: idx }))}
              size="small"
              pagination={{ pageSize: 10 }}
              scroll={{ x: 'max-content' }}
              columns={[
                { title: 'SID/Serial', dataIndex: 'sid_serial', key: 'sid_serial', width: 120 },
                { title: '用户名', dataIndex: 'username', key: 'username', width: 100 },
                { title: '状态', dataIndex: 'status', key: 'status', width: 80,
                  render: v => <Tag color={v === 'ACTIVE' ? 'green' : v === 'INACTIVE' ? 'default' : 'orange'}>{v}</Tag>
                },
                { title: '程序', dataIndex: 'program', key: 'program', width: 150, ellipsis: true },
                { title: '机器', dataIndex: 'machine', key: 'machine', width: 120, ellipsis: true },
                { title: '等待事件', dataIndex: 'wait_event', key: 'wait_event', width: 150, ellipsis: true },
                { title: '等待秒数', dataIndex: 'seconds_in_wait', key: 'seconds_in_wait', width: 100 },
                { title: 'SQL ID', dataIndex: 'sql_id', key: 'sql_id', width: 150, ellipsis: true }
              ]}
            />
          </Card>

          {/* Oracle Top SQL */}
          <Card title="Oracle Top SQL (Buffer Gets)" size="small" style={{ marginBottom: 16 }}>
            <Table
              dataSource={(metrics.top_sql_by_buffer_gets || []).map((sql, idx) => ({ ...sql, key: idx }))}
              size="small"
              pagination={false}
              columns={[
                { title: 'SQL ID', dataIndex: 'sql_id', key: 'sql_id', width: 150, ellipsis: true },
                { title: 'SQL文本', dataIndex: 'sql_text', key: 'sql_text', ellipsis: true },
                { title: 'Buffer Gets', dataIndex: 'buffer_gets', key: 'buffer_gets', render: v => formatNumber(v) },
                { title: 'Disk Reads', dataIndex: 'disk_reads', key: 'disk_reads', render: v => formatNumber(v) },
                { title: '执行次数', dataIndex: 'executions', key: 'executions', render: v => formatNumber(v) },
                { title: 'Gets/执行', dataIndex: 'buffer_gets_per_exec', key: 'buffer_gets_per_exec', render: v => formatNumber(v) }
              ]}
            />
          </Card>

          {/* Oracle RAC信息 */}
          {metrics.rac_instances && metrics.rac_instances.length > 0 && (
            <Card title="Oracle RAC 集群信息" size="small" style={{ marginBottom: 16 }}>
              <Row gutter={16} style={{ marginBottom: 16 }}>
                <Col span={8}><Statistic title="RAC实例数" value={metrics.rac_instance_count || 0} /></Col>
                <Col span={8}><Statistic title="DataGuard角色" value={metrics.dg_database_role || '-'} /></Col>
                <Col span={8}><Statistic title="保护模式" value={metrics.dg_protection_mode || '-'} /></Col>
              </Row>
              <Table
                dataSource={(metrics.rac_instances || []).map((inst, idx) => ({ ...inst, key: idx }))}
                size="small"
                pagination={false}
                columns={[
                  { title: '实例ID', dataIndex: 'inst_id', key: 'inst_id' },
                  { title: '实例名', dataIndex: 'instance_name', key: 'instance_name' },
                  { title: '主机名', dataIndex: 'host_name', key: 'host_name' },
                  { title: '状态', dataIndex: 'status', key: 'status',
                    render: v => <Tag color={v === 'OPEN' ? 'green' : 'orange'}>{v}</Tag>
                  }
                ]}
              />
            </Card>
          )}

          {/* Oracle 数据文件统计 */}
          <Card title="Oracle 数据文件统计" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={8}><Card size="small" hoverable onClick={() => handleMetricClick('datafile_count', '数据文件数量', metrics.datafile_count)}><Statistic title="数据文件数量" value={metrics.datafile_count || 0} /></Card></Col>
              <Col span={8}><Card size="small" hoverable onClick={() => handleMetricClick('datafile_size_total_gb', '数据文件总大小', metrics.datafile_size_total_gb)}><Statistic title="数据文件总大小" value={metrics.datafile_size_total_gb || 0} suffix="GB" /></Card></Col>
            </Row>
          </Card>

          {/* Oracle 事务统计 */}
          <Card title="Oracle 事务统计" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('active_transactions', '活跃事务数', metrics.active_transactions)}><Statistic title="活跃事务数" value={metrics.active_transactions || 0} /></Card></Col>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('row_lock_contention', '行锁争用', metrics.row_lock_contention)}><Statistic title="行锁争用" value={metrics.row_lock_contention || 0} /></Card></Col>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('committed_transactions', '已提交事务', metrics.committed_transactions)}><Statistic title="已提交事务" value={formatNumber(metrics.committed_transactions)} /></Card></Col>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('rolled_back_transactions', '已回滚事务', metrics.rolled_back_transactions)}><Statistic title="已回滚事务" value={formatNumber(metrics.rolled_back_transactions)} /></Card></Col>
            </Row>
          </Card>

          {/* Oracle 对象统计 */}
          <Card title="Oracle 对象统计" size="small" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('table_count', '表数量', metrics.table_count)}><Statistic title="表数量" value={metrics.table_count || 0} /></Card></Col>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('index_count', '索引数量', metrics.index_count)}><Statistic title="索引数量" value={metrics.index_count || 0} /></Card></Col>
              <Col span={6}><Card size="small" hoverable onClick={() => handleMetricClick('partition_count', '分区数量', metrics.partition_count)}><Statistic title="分区数量" value={metrics.partition_count || 0} /></Card></Col>
              <Col span={6}><Card size="small" hoverable><Statistic title="统计信息过期对象" value={(metrics.stale_statistics || []).length} /></Card></Col>
            </Row>
          </Card>

          {/* Oracle Top表大小 */}
          {metrics.table_size_top20 && metrics.table_size_top20.length > 0 && (
            <Card title="Oracle Top 20 表大小" size="small" style={{ marginBottom: 16 }}>
              <Table
                dataSource={metrics.table_size_top20.map((t, idx) => ({ ...t, key: idx }))}
                size="small"
                pagination={false}
                columns={[
                  { title: '所有者', dataIndex: 'owner', key: 'owner' },
                  { title: '表名', dataIndex: 'table_name', key: 'table_name', ellipsis: true },
                  { title: '大小(MB)', dataIndex: 'size_mb', key: 'size_mb', render: v => v?.toFixed(2) || '-' }
                ]}
              />
            </Card>
          )}

          {/* Oracle Top索引大小 */}
          {metrics.index_size_top20 && metrics.index_size_top20.length > 0 && (
            <Card title="Oracle Top 20 索引大小" size="small" style={{ marginBottom: 16 }}>
              <Table
                dataSource={metrics.index_size_top20.map((t, idx) => ({ ...t, key: idx }))}
                size="small"
                pagination={false}
                columns={[
                  { title: '所有者', dataIndex: 'owner', key: 'owner' },
                  { title: '索引名', dataIndex: 'index_name', key: 'index_name', ellipsis: true },
                  { title: '大小(MB)', dataIndex: 'size_mb', key: 'size_mb', render: v => v?.toFixed(2) || '-' }
                ]}
              />
            </Card>
          )}
        </>
      )}

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

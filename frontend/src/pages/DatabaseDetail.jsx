import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { 
  Card, Row, Col, Statistic, Typography, Space, Tag, 
  Table, Tabs, Button, Descriptions, Spin, Alert
} from 'antd';
import { 
  ArrowLeftOutlined, ReloadOutlined, DatabaseOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined,
  ThunderboltOutlined, DashboardOutlined
} from '@ant-design/icons';
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, 
  CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { getDatabaseDetail, getDatabaseMetrics, getDatabaseAlerts } from '../services/api';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

const DatabaseDetail = () => {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState(null);
  const [metrics, setMetrics] = useState({});
  const [alerts, setAlerts] = useState([]);
  const [timeRange, setTimeRange] = useState('24h');

  const fetchData = async () => {
    setLoading(true);
    try {
      const [detailData, metricsData, alertsData] = await Promise.all([
        getDatabaseDetail(id),
        getDatabaseMetrics(id, timeRange),
        getDatabaseAlerts(id)
      ]);
      setDetail(detailData);
      setMetrics(metricsData);
      setAlerts(alertsData);
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) {
      fetchData();
      const interval = setInterval(fetchData, 30000); // 30秒刷新
      return () => clearInterval(interval);
    }
  }, [id, timeRange]);

  const getStatusTag = (status) => {
    const statusMap = {
      active: { color: 'green', text: '正常', icon: <CheckCircleOutlined /> },
      error: { color: 'red', text: '异常', icon: <CloseCircleOutlined /> },
      warning: { color: 'orange', text: '警告', icon: <ClockCircleOutlined /> }
    };
    const config = statusMap[status] || { color: 'default', text: status };
    return (
      <Tag color={config.color} icon={config.icon}>
        {config.text}
      </Tag>
    );
  };

  const formatChartData = (data) => {
    if (!data || data.length === 0) return [];
    return data.map(item => ({
      time: dayjs(item.timestamp).format('HH:mm'),
      value: item.value,
      ...item
    }));
  };

  if (loading && !detail) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  if (!detail) {
    return <Alert message="未找到数据库信息" type="error" />;
  }

  const chartColors = {
    cpu: '#1890ff',
    memory: '#722ed1',
    disk: '#faad14',
    sessions: '#52c41a',
    connections: '#f5222d'
  };

  return (
    <div className="database-detail" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Link to="/databases">
          <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
        </Link>
        <Space style={{ marginLeft: 16 }}>
          <Title level={4} style={{ margin: 0 }}>
            <DatabaseOutlined /> {detail.name}
          </Title>
          {getStatusTag(detail.status)}
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
          <Descriptions.Item label="主机地址">{detail.host}</Descriptions.Item>
          <Descriptions.Item label="端口">{detail.port}</Descriptions.Item>
          <Descriptions.Item label="数据库类型">{detail.db_type}</Descriptions.Item>
          <Descriptions.Item label="服务名/库名">{detail.service_name || detail.database_name}</Descriptions.Item>
          <Descriptions.Item label="最后检查">{detail.last_check ? dayjs(detail.last_check).format('YYYY-MM-DD HH:mm:ss') : '-'}</Descriptions.Item>
          <Descriptions.Item label="监控间隔">{detail.check_interval || 60}秒</Descriptions.Item>
          <Descriptions.Item label="启用状态">{detail.is_active ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{detail.created_at ? dayjs(detail.created_at).format('YYYY-MM-DD') : '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 核心指标卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="CPU使用率" 
              value={detail.cpu_usage || 0}
              suffix="%"
              valueStyle={{ 
                color: (detail.cpu_usage || 0) > 80 ? '#ff4d4f' : (detail.cpu_usage || 0) > 60 ? '#faad14' : '#52c41a'
              }}
              prefix={<DashboardOutlined />}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="内存使用率" 
              value={detail.memory_usage || 0}
              suffix="%"
              valueStyle={{ 
                color: (detail.memory_usage || 0) > 85 ? '#ff4d4f' : (detail.memory_usage || 0) > 70 ? '#faad14' : '#52c41a'
              }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="磁盘使用率" 
              value={detail.disk_usage || 0}
              suffix="%"
              valueStyle={{ 
                color: (detail.disk_usage || 0) > 90 ? '#ff4d4f' : (detail.disk_usage || 0) > 75 ? '#faad14' : '#52c41a'
              }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="活跃会话" 
              value={detail.active_sessions || 0}
              prefix={<ThunderboltOutlined />}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="连接数" 
              value={detail.connections || 0}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic 
              title="QPS" 
              value={detail.qps || 0}
              suffix="/s"
            />
          </Card>
        </Col>
      </Row>

      {/* 标签页 */}
      <Card>
        <Tabs defaultActiveKey="metrics">
          <TabPane tab="性能趋势" key="metrics">
            <Space style={{ marginBottom: 16 }}>
              <Button onClick={() => setTimeRange('1h')}>1小时</Button>
              <Button onClick={() => setTimeRange('6h')}>6小时</Button>
              <Button onClick={() => setTimeRange('24h')}>24小时</Button>
              <Button onClick={() => setTimeRange('7d')}>7天</Button>
            </Space>

            <Row gutter={16}>
              <Col span={12}>
                <Card title="CPU使用率趋势" size="small">
                  <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={formatChartData(metrics.cpu)}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis domain={[0, 100]} />
                      <Tooltip />
                      <Area 
                        type="monotone" 
                        dataKey="value" 
                        stroke={chartColors.cpu} 
                        fill={chartColors.cpu} 
                        fillOpacity={0.3}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="内存使用率趋势" size="small">
                  <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={formatChartData(metrics.memory)}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis domain={[0, 100]} />
                      <Tooltip />
                      <Area 
                        type="monotone" 
                        dataKey="value" 
                        stroke={chartColors.memory} 
                        fill={chartColors.memory} 
                        fillOpacity={0.3}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="磁盘使用率趋势" size="small">
                  <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={formatChartData(metrics.disk)}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis domain={[0, 100]} />
                      <Tooltip />
                      <Area 
                        type="monotone" 
                        dataKey="value" 
                        stroke={chartColors.disk} 
                        fill={chartColors.disk} 
                        fillOpacity={0.3}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="活跃会话趋势" size="small">
                  <ResponsiveContainer width="100%" height={250}>
                    <LineChart data={formatChartData(metrics.sessions)}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="time" />
                      <YAxis />
                      <Tooltip />
                      <Line 
                        type="monotone" 
                        dataKey="value" 
                        stroke={chartColors.sessions} 
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
            </Row>
          </TabPane>

          <TabPane tab="告警记录" key="alerts">
            <Table
              dataSource={alerts}
              rowKey="id"
              size="small"
              pagination={{ pageSize: 10 }}
              columns={[
                {
                  title: '级别',
                  dataIndex: 'severity',
                  key: 'severity',
                  render: (severity) => {
                    const colorMap = {
                      critical: 'red',
                      warning: 'orange',
                      info: 'blue'
                    };
                    return <Tag color={colorMap[severity] || 'default'}>{severity?.toUpperCase()}</Tag>;
                  }
                },
                {
                  title: '指标',
                  dataIndex: 'metric_name',
                  key: 'metric_name'
                },
                {
                  title: '告警消息',
                  dataIndex: 'message',
                  key: 'message',
                  ellipsis: true
                },
                {
                  title: '当前值',
                  dataIndex: 'current_value',
                  key: 'current_value',
                  render: (val) => val?.toFixed(2)
                },
                {
                  title: '阈值',
                  dataIndex: 'threshold',
                  key: 'threshold'
                },
                {
                  title: '时间',
                  dataIndex: 'created_at',
                  key: 'created_at',
                  render: (time) => time ? dayjs(time).format('YYYY-MM-DD HH:mm:ss') : '-'
                }
              ]}
            />
          </TabPane>

          <TabPane tab="容量预测" key="capacity">
            <Card size="small">
              <Descriptions column={2}>
                <Descriptions.Item label="容量预测引擎">
                  {detail.capacity_prediction?.engine || '未配置'}
                </Descriptions.Item>
                <Descriptions.Item label="预测天数">
                  {detail.capacity_prediction?.predicted_days || '-'} 天
                </Descriptions.Item>
                <Descriptions.Item label="当前使用量">
                  {detail.capacity_prediction?.current_usage || '-'} GB
                </Descriptions.Item>
                <Descriptions.Item label="容量上限">
                  {detail.capacity_prediction?.max_capacity || '-'} GB
                </Descriptions.Item>
                <Descriptions.Item label="日均增长">
                  {detail.capacity_prediction?.daily_growth || '-'} GB/天
                </Descriptions.Item>
                <Descriptions.Item label="预计满容时间">
                  {detail.capacity_prediction?.estimated_full_date || '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </TabPane>

          <TabPane tab="会话信息" key="sessions">
            <Table
              dataSource={detail.sessions || []}
              rowKey="sid"
              size="small"
              pagination={{ pageSize: 10 }}
              columns={[
                { title: 'SID', dataIndex: 'sid', key: 'sid' },
                { title: 'Serial#', dataIndex: 'serial', key: 'serial' },
                { title: '用户名', dataIndex: 'username', key: 'username' },
                { title: '状态', dataIndex: 'status', key: 'status' },
                { title: 'SQL操作', dataIndex: 'sql_operation', key: 'sql_operation', ellipsis: true },
                { title: '等待事件', dataIndex: 'wait_event', key: 'wait_event' },
                { title: '连接时间', dataIndex: 'logon_time', key: 'logon_time' }
              ]}
            />
          </TabPane>
        </Tabs>
      </Card>
    </div>
  );
};

export default DatabaseDetail;

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { 
  Table, Tag, Space, Button, Select, DatePicker, Card, 
  Typography, Badge, Statistic, Row, Col, Modal, Descriptions
} from 'antd';
import { 
  ReloadOutlined, BellOutlined, CheckCircleOutlined, 
  WarningOutlined, ExclamationCircleOutlined, CloseCircleOutlined,
  EyeOutlined, DeleteOutlined, SyncOutlined
} from '@ant-design/icons';
import { getAlerts, acknowledgeAlert, getAlertStatistics } from '../services/api';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const AlertList = () => {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState({
    total: 0,
    critical: 0,
    warning: 0,
    resolved: 0
  });
  const [filters, setFilters] = useState({
    severity: 'all',
    status: 'all',
    dbType: 'all',
    dateRange: null
  });
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [detailVisible, setDetailVisible] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [alertsData, statsData] = await Promise.all([
        getAlerts(filters),
        getAlertStatistics()
      ]);
      setAlerts(alertsData);
      setStats(statsData);
    } catch (error) {
      console.error('获取告警数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 60000); // 1分钟刷新
    return () => clearInterval(interval);
  }, [filters]);

  const getSeverityIcon = (severity) => {
    const iconMap = {
      critical: <CloseCircleOutlined />,
      warning: <WarningOutlined />,
      info: <ExclamationCircleOutlined />
    };
    return iconMap[severity] || iconMap.info;
  };

  const getSeverityTag = (severity) => {
    const configMap = {
      critical: { color: 'red', text: '严重' },
      warning: { color: 'orange', text: '警告' },
      info: { color: 'blue', text: '提示' }
    };
    const config = configMap[severity] || { color: 'default', text: severity };
    return (
      <Tag color={config.color} icon={getSeverityIcon(severity)}>
        {config.text}
      </Tag>
    );
  };

  const getStatusTag = (status) => {
    const configMap = {
      active: { color: 'processing', text: '活跃' },
      acknowledged: { color: 'warning', text: '已确认' },
      resolved: { color: 'success', text: '已解决' },
      silenced: { color: 'default', text: '已屏蔽' }
    };
    const config = configMap[status] || { color: 'default', text: status };
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const handleAcknowledge = async (alertId) => {
    try {
      await acknowledgeAlert(alertId);
      fetchData();
    } catch (error) {
      console.error('确认告警失败:', error);
    }
  };

  const handleViewDetail = (alert) => {
    setSelectedAlert(alert);
    setDetailVisible(true);
  };

  const columns = [
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 100,
      render: (severity) => getSeverityTag(severity)
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status) => getStatusTag(status)
    },
    {
      title: '数据库',
      dataIndex: 'database_name',
      key: 'database_name',
      width: 150,
      render: (name, record) => (
        <Link to={`/database/${record.database_id}`}>
          {name}
        </Link>
      )
    },
    {
      title: '告警消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (msg) => <Text>{msg}</Text>
    },
    {
      title: '指标',
      dataIndex: 'metric_name',
      key: 'metric_name',
      width: 120
    },
    {
      title: '当前值',
      dataIndex: 'current_value',
      key: 'current_value',
      width: 100,
      render: (val) => val?.toFixed(2) || '-'
    },
    {
      title: '阈值',
      dataIndex: 'threshold',
      key: 'threshold',
      width: 100
    },
    {
      title: '发生时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (time) => time ? dayjs(time).format('MM-DD HH:mm:ss') : '-'
    },
    {
      title: '持续时间',
      dataIndex: 'created_at',
      key: 'duration',
      width: 100,
      render: (time) => time ? dayjs().to(dayjs(time)) : '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record) => (
        <Space size="small">
          <Button 
            type="link" 
            size="small" 
            icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record)}
          >
            详情
          </Button>
          {record.status === 'active' && (
            <Button 
              type="link" 
              size="small" 
              icon={<CheckCircleOutlined />}
              onClick={() => handleAcknowledge(record.id)}
            >
              确认
            </Button>
          )}
        </Space>
      )
    }
  ];

  return (
    <div className="alert-list" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ marginBottom: 16 }}>
          <BellOutlined /> 告警中心
        </Title>
        
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="告警总数" 
                value={stats.total}
                prefix={<BellOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="严重告警" 
                value={stats.critical}
                valueStyle={{ color: '#ff4d4f' }}
                prefix={<CloseCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="警告" 
                value={stats.warning}
                valueStyle={{ color: '#faad14' }}
                prefix={<WarningOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="已解决" 
                value={stats.resolved}
                valueStyle={{ color: '#52c41a' }}
                prefix={<CheckCircleOutlined />}
              />
            </Card>
          </Col>
        </Row>

        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            value={filters.severity}
            onChange={(value) => setFilters({...filters, severity: value})}
            style={{ width: 100 }}
          >
            <Option value="all">全部级别</Option>
            <Option value="critical">严重</Option>
            <Option value="warning">警告</Option>
            <Option value="info">提示</Option>
          </Select>
          <Select
            value={filters.status}
            onChange={(value) => setFilters({...filters, status: value})}
            style={{ width: 100 }}
          >
            <Option value="all">全部状态</Option>
            <Option value="active">活跃</Option>
            <Option value="acknowledged">已确认</Option>
            <Option value="resolved">已解决</Option>
          </Select>
          <RangePicker 
            onChange={(dates) => setFilters({...filters, dateRange: dates})}
            style={{ width: 240 }}
          />
          <Button 
            icon={<ReloadOutlined />} 
            onClick={fetchData}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={alerts}
        rowKey="id"
        loading={loading}
        pagination={{
          defaultPageSize: 15,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`
        }}
      />

      <Modal
        title="告警详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailVisible(false)}>
            关闭
          </Button>,
          selectedAlert?.status === 'active' && (
            <Button 
              key="ack" 
              type="primary"
              onClick={() => {
                handleAcknowledge(selectedAlert.id);
                setDetailVisible(false);
              }}
            >
              确认告警
            </Button>
          )
        ].filter(Boolean)}
        width={700}
      >
        {selectedAlert && (
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="告警ID">{selectedAlert.id}</Descriptions.Item>
            <Descriptions.Item label="级别">{getSeverityTag(selectedAlert.severity)}</Descriptions.Item>
            <Descriptions.Item label="数据库">
              <Link to={`/database/${selectedAlert.database_id}`}>
                {selectedAlert.database_name}
              </Link>
            </Descriptions.Item>
            <Descriptions.Item label="状态">{getStatusTag(selectedAlert.status)}</Descriptions.Item>
            <Descriptions.Item label="指标名称">{selectedAlert.metric_name}</Descriptions.Item>
            <Descriptions.Item label="当前值">{selectedAlert.current_value?.toFixed(2)}</Descriptions.Item>
            <Descriptions.Item label="阈值">{selectedAlert.threshold}</Descriptions.Item>
            <Descriptions.Item label="比较方式">{selectedAlert.operator || '>'}</Descriptions.Item>
            <Descriptions.Item label="发生时间" span={2}>
              {selectedAlert.created_at ? dayjs(selectedAlert.created_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="告警消息" span={2}>
              {selectedAlert.message}
            </Descriptions.Item>
            {selectedAlert.acknowledged_at && (
              <Descriptions.Item label="确认时间" span={2}>
                {dayjs(selectedAlert.acknowledged_at).format('YYYY-MM-DD HH:mm:ss')}
              </Descriptions.Item>
            )}
            {selectedAlert.resolved_at && (
              <Descriptions.Item label="解决时间" span={2}>
                {dayjs(selectedAlert.resolved_at).format('YYYY-MM-DD HH:mm:ss')}
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </div>
  );
};

export default AlertList;

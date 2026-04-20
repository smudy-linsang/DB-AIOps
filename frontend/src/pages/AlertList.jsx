import { useState, useEffect } from 'react'
import { 
  Table, Tag, Space, Button, Select, Card, 
  Typography, Statistic, Row, Col, Modal, Descriptions, message
} from 'antd'
import { 
  ReloadOutlined, BellOutlined, CheckCircleOutlined, 
  WarningOutlined, ExclamationCircleOutlined, CloseCircleOutlined,
  EyeOutlined
} from '@ant-design/icons'
import { alertAPI } from '../services/api'
import dayjs from 'dayjs'

const { Title, Text } = Typography
const { Option } = Select

const AlertList = () => {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState({
    severity: 'all',
    status: 'all'
  })
  const [selectedAlert, setSelectedAlert] = useState(null)
  const [detailVisible, setDetailVisible] = useState(false)
  const [stats, setStats] = useState({
    total: 0,
    critical: 0,
    warning: 0,
    active: 0
  })

  const fetchAlerts = async () => {
    setLoading(true)
    try {
      const response = await alertAPI.list({ limit: 100 })
      const alertList = response.data?.alerts || []
      setAlerts(alertList)
      
      // 统计
      setStats({
        total: alertList.length,
        critical: alertList.filter(a => a.severity === 'critical').length,
        warning: alertList.filter(a => a.severity === 'warning').length,
        active: alertList.filter(a => a.status === 'active').length
      })
    } catch (error) {
      console.error('获取告警数据失败:', error)
      message.error('获取告警数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAlerts()
    // 1分钟刷新
    const interval = setInterval(fetchAlerts, 60000)
    return () => clearInterval(interval)
  }, [filters])

  const getSeverityIcon = (severity) => {
    const iconMap = {
      critical: <CloseCircleOutlined />,
      warning: <WarningOutlined />,
      info: <ExclamationCircleOutlined />
    }
    return iconMap[severity] || iconMap.info
  }

  const getSeverityTag = (severity) => {
    const configMap = {
      critical: { color: 'red', text: '严重' },
      warning: { color: 'orange', text: '警告' },
      info: { color: 'blue', text: '提示' }
    }
    const config = configMap[severity] || { color: 'default', text: severity }
    return (
      <Tag color={config.color} icon={getSeverityIcon(severity)}>
        {config.text}
      </Tag>
    )
  }

  const getStatusTag = (status) => {
    const configMap = {
      active: { color: 'processing', text: '活跃' },
      acknowledged: { color: 'warning', text: '已确认' },
      resolved: { color: 'success', text: '已解决' },
      silenced: { color: 'default', text: '已屏蔽' }
    }
    const config = configMap[status] || { color: 'default', text: status }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  const handleAcknowledge = async (alertId) => {
    try {
      await alertAPI.acknowledge(alertId)
      message.success('告警已确认')
      fetchAlerts()
    } catch (error) {
      console.error('确认告警失败:', error)
      message.error('确认告警失败')
    }
  }

  const handleViewDetail = (alert) => {
    setSelectedAlert(alert)
    setDetailVisible(true)
  }

  const filteredAlerts = alerts.filter(alert => {
    if (filters.severity !== 'all' && alert.severity !== filters.severity) {
      return false
    }
    if (filters.status !== 'all' && alert.status !== filters.status) {
      return false
    }
    return true
  })

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
      title: '告警标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true
    },
    {
      title: '指标',
      dataIndex: 'metric_key',
      key: 'metric_key',
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
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (time) => time ? dayjs(time).format('MM-DD HH:mm:ss') : '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
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
  ]

  return (
    <div className="alert-list">
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
                title="活跃告警" 
                value={stats.active}
                valueStyle={{ color: '#1890ff' }}
                prefix={<ExclamationCircleOutlined />}
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
          <Button 
            icon={<ReloadOutlined />} 
            onClick={fetchAlerts}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={filteredAlerts}
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
                handleAcknowledge(selectedAlert.id)
                setDetailVisible(false)
              }}
            >
              确认告警
            </Button>
          )
        ].filter(Boolean)}
        width={600}
      >
        {selectedAlert && (
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="告警ID">{selectedAlert.id}</Descriptions.Item>
            <Descriptions.Item label="级别">{getSeverityTag(selectedAlert.severity)}</Descriptions.Item>
            <Descriptions.Item label="状态">{getStatusTag(selectedAlert.status)}</Descriptions.Item>
            <Descriptions.Item label="指标">{selectedAlert.metric_key}</Descriptions.Item>
            <Descriptions.Item label="当前值">{selectedAlert.current_value?.toFixed(2)}</Descriptions.Item>
            <Descriptions.Item label="基线值">{selectedAlert.baseline_value?.toFixed(2) || '-'}</Descriptions.Item>
            <Descriptions.Item label="发生时间" span={2}>
              {selectedAlert.created_at ? dayjs(selectedAlert.created_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="告警标题" span={2}>
              {selectedAlert.title}
            </Descriptions.Item>
            <Descriptions.Item label="描述" span={2}>
              {selectedAlert.description}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  )
}

export default AlertList

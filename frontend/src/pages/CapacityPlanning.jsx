import { useState, useEffect } from 'react'
import { Card, Row, Col, Table, Tag, Typography, Spin, Alert, Select, Progress, Tooltip, Space, Statistic } from 'antd'
import { WarningOutlined, CheckCircleOutlined, ClockCircleOutlined, RiseOutlined } from '@ant-design/icons'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, Legend } from 'recharts'
import { databaseAPI } from '../services/api'

const { Title, Text } = Typography

const CapacityPlanning = () => {
  const [loading, setLoading] = useState(true)
  const [databases, setDatabases] = useState([])
  const [predictions, setPredictions] = useState({})
  const [selectedDb, setSelectedDb] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)
      const dbRes = await databaseAPI.list()
      const dbs = dbRes.data || dbRes || []
      setDatabases(Array.isArray(dbs) ? dbs : [])

      // Load predictions for each database
      const predMap = {}
      for (const db of (Array.isArray(dbs) ? dbs : [])) {
        try {
          const predRes = await databaseAPI.getPrediction(db.id)
          predMap[db.id] = predRes.data || predRes || {}
        } catch (e) {
          predMap[db.id] = { error: 'No prediction data' }
        }
      }
      setPredictions(predMap)
    } catch (e) {
      setError(e.message || 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }

  const getUrgencyColor = (days) => {
    if (days === null || days === undefined) return '#52c41a'
    if (days <= 7) return '#ff4d4f'
    if (days <= 14) return '#faad14'
    if (days <= 30) return '#fa8c16'
    return '#52c41a'
  }

  const getUrgencyTag = (days) => {
    if (days === null || days === undefined) return <Tag color="green">安全</Tag>
    if (days <= 7) return <Tag color="red">紧急 ({days}天)</Tag>
    if (days <= 14) return <Tag color="orange">警告 ({days}天)</Tag>
    if (days <= 30) return <Tag color="gold">关注 ({days}天)</Tag>
    return <Tag color="green">正常 ({days}天)</Tag>
  }

  const columns = [
    {
      title: '数据库',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <Space>
          <Tag color="blue">{record.db_type?.toUpperCase()}</Tag>
          <Text strong>{text}</Text>
        </Space>
      ),
    },
    {
      title: '地址',
      key: 'host',
      render: (_, record) => `${record.host}:${record.port}`,
    },
    {
      title: '表空间预测',
      key: 'tablespace',
      render: (_, record) => {
        const pred = predictions[record.id]
        if (!pred || pred.error) return <Text type="secondary">无数据</Text>
        const metrics = pred.metrics || {}
        const tbs = metrics.tablespace || metrics.connection || {}
        if (tbs.error) return <Text type="secondary">数据不足</Text>
        return (
          <Space direction="vertical" size={0}>
            <Text>当前: {tbs.current_value?.toFixed(1)}%</Text>
            <Text>预测: {tbs.predicted_value?.toFixed(1)}%</Text>
          </Space>
        )
      },
    },
    {
      title: '到达阈值',
      key: 'threshold',
      render: (_, record) => {
        const pred = predictions[record.id]
        if (!pred || pred.error) return '-'
        const metrics = pred.metrics || {}
        const tbs = metrics.tablespace || metrics.connection || {}
        return getUrgencyTag(tbs.days_to_threshold)
      },
    },
    {
      title: '模型',
      key: 'model',
      render: (_, record) => {
        const pred = predictions[record.id]
        if (!pred || pred.error) return '-'
        const metrics = pred.metrics || {}
        const tbs = metrics.tablespace || metrics.connection || {}
        return <Tag>{tbs.model_used || 'N/A'}</Tag>
      },
    },
    {
      title: '置信度',
      key: 'confidence',
      render: (_, record) => {
        const pred = predictions[record.id]
        if (!pred || pred.error) return '-'
        const metrics = pred.metrics || {}
        const tbs = metrics.tablespace || metrics.connection || {}
        const conf = (tbs.confidence || 0) * 100
        return <Progress percent={conf} size="small" status={conf > 70 ? 'success' : 'normal'} />
      },
    },
  ]

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />

  return (
    <div style={{ padding: 24 }}>
      <Title level={2}>容量规划视图</Title>
      {error && <Alert message={error} type="error" style={{ marginBottom: 16 }} />}

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="监控数据库总数"
              value={databases.length}
              prefix={<RiseOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="紧急扩容"
              value={databases.filter(db => {
                const pred = predictions[db.id]
                const days = pred?.metrics?.tablespace?.days_to_threshold
                return days !== null && days !== undefined && days <= 7
              }).length}
              valueStyle={{ color: '#ff4d4f' }}
              prefix={<WarningOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="30天内需关注"
              value={databases.filter(db => {
                const pred = predictions[db.id]
                const days = pred?.metrics?.tablespace?.days_to_threshold
                return days !== null && days !== undefined && days <= 30 && days > 7
              }).length}
              valueStyle={{ color: '#faad14' }}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="安全"
              value={databases.filter(db => {
                const pred = predictions[db.id]
                const days = pred?.metrics?.tablespace?.days_to_threshold
                return days === null || days === undefined || days > 30
              }).length}
              valueStyle={{ color: '#52c41a' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card title="容量预测总览">
        <Table
          columns={columns}
          dataSource={databases}
          rowKey="id"
          pagination={false}
          size="middle"
        />
      </Card>
    </div>
  )
}

export default CapacityPlanning

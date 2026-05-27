import { useState, useEffect, useCallback } from 'react'
import { Card, Row, Col, Table, Tag, Typography, Spin, Alert, Select, Progress, Tooltip, Space, Statistic, Button, message, Tabs } from 'antd'
import { WarningOutlined, CheckCircleOutlined, ClockCircleOutlined, RiseOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, Legend } from 'recharts'
import { capacityAPI, databaseAPI } from '../services/api'
import { PermissionGuard } from '../components/AuthGuard'
import { Perm } from '../utils/permission'

const { Title, Text } = Typography

const CapacityPlanning = () => {
  const [loading, setLoading] = useState(true)
  const [databases, setDatabases] = useState([])
  const [overview, setOverview] = useState(null)
  const [selectedDb, setSelectedDb] = useState(null)
  const [predictions, setPredictions] = useState([])
  const [predictLoading, setPredictLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const res = await capacityAPI.overview()
      const data = res?.databases || res?.data || []
      setDatabases(Array.isArray(data) ? data : [])
      setOverview(res?.summary || null)
    } catch (e) {
      setError(e.message || 'Failed to load capacity data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const handlePredictNow = async (dbId) => {
    try {
      setPredictLoading(true)
      await capacityAPI.predictNow(dbId)
      message.success('容量预测已完成')
      loadData()
    } catch (e) {
      message.error('预测失败: ' + (e.response?.data?.error || e.message))
    } finally {
      setPredictLoading(false)
    }
  }

  const getUrgencyTag = (days) => {
    if (days === null || days === undefined) return <Tag color="green">安全</Tag>
    if (days <= 7) return <Tag color="red">紧急 ({days}天)</Tag>
    if (days <= 14) return <Tag color="orange">警告 ({days}天)</Tag>
    if (days <= 30) return <Tag color="gold">关注 ({days}天)</Tag>
    return <Tag color="green">正常 ({days}天)</Tag>
  }

  const getDaysToWarn = (pred) => {
    if (!pred?.predicted_warn_date) return null
    return Math.ceil((new Date(pred.predicted_warn_date) - new Date()) / (1000 * 60 * 60 * 24))
  }

  // 展开行的预测详情
  const expandedRowRender = (record) => {
    const preds = record.predictions || []
    if (preds.length === 0) return <Text type="secondary">暂无预测数据，点击"立即预测"获取</Text>

    const predColumns = [
      { title: '指标', dataIndex: 'metric_key', key: 'metric_key', render: v => <Tag>{v}</Tag> },
      { title: '资源名', dataIndex: 'resource_name', key: 'resource_name' },
      { title: '当前值', dataIndex: 'current_value', key: 'current_value', render: v => v != null ? v.toFixed(2) : '-' },
      { title: '月增长率', dataIndex: 'monthly_growth_rate', key: 'monthly_growth_rate', render: v => v != null ? `${(v * 100).toFixed(1)}%` : '-' },
      { title: '预计告警日', dataIndex: 'predicted_warn_date', key: 'predicted_warn_date', render: v => v ? new Date(v).toLocaleDateString('zh-CN') : '-' },
      { title: '模型', dataIndex: 'model_used', key: 'model_used', render: v => <Tag color="blue">{v || 'N/A'}</Tag> },
      { title: '置信度', dataIndex: 'confidence', key: 'confidence', render: v => v != null ? <Progress percent={Math.round(v * 100)} size="small" /> : '-' },
    ]

    return <Table columns={predColumns} dataSource={preds} rowKey="metric_key" pagination={false} size="small" />
  }

  const columns = [
    {
      title: '数据库', dataIndex: 'name', key: 'name',
      render: (text, record) => (
        <Space>
          <Tag color="blue">{record.db_type?.toUpperCase()}</Tag>
          <Text strong>{text}</Text>
        </Space>
      ),
    },
    {
      title: '地址', key: 'host',
      render: (_, record) => `${record.host}:${record.port}`,
    },
    {
      title: '预测指标数', key: 'pred_count',
      render: (_, record) => <Tag color="cyan">{record.predictions?.length || 0} 项</Tag>,
    },
    {
      title: '最紧急项', key: 'urgency',
      render: (_, record) => {
        const preds = record.predictions || []
        if (preds.length === 0) return <Text type="secondary">无数据</Text>
        const minDays = Math.min(...preds.map(p => getDaysToWarn(p)).filter(d => d !== null))
        return getUrgencyTag(minDays)
      },
    },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_, record) => (
        <PermissionGuard code={Perm.CAPACITY_PREDICT}>
        <Button
          size="small"
          type="primary"
          icon={<ThunderboltOutlined />}
          loading={predictLoading}
          onClick={() => handlePredictNow(record.id)}
        >
          立即预测
        </Button>
        </PermissionGuard>
      ),
    },
  ]

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />

  return (
    <div style={{ padding: 24 }}>
      <Title level={2}>容量规划视图</Title>
      {error && <Alert message={error} type="error" style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="监控数据库总数"
              value={overview?.total || databases.length}
              prefix={<RiseOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="紧急扩容"
              value={overview?.emergency || 0}
              valueStyle={{ color: '#ff4d4f' }}
              prefix={<WarningOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="30天内需关注"
              value={overview?.warning || 0}
              valueStyle={{ color: '#faad14' }}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="安全"
              value={overview?.safe || 0}
              valueStyle={{ color: '#52c41a' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="容量预测总览"
        extra={<Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>}
      >
        <Table
          columns={columns}
          dataSource={databases}
          rowKey="id"
          pagination={false}
          size="middle"
          expandable={{
            expandedRowRender,
            rowExpandable: record => (record.predictions?.length || 0) > 0,
          }}
        />
      </Card>
    </div>
  )
}

export default CapacityPlanning

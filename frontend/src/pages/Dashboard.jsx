import { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Typography, Space, Spin, Alert } from 'antd'
import {
  DatabaseOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  RiseOutlined,
  FallOutlined,
  ReloadOutlined,
  Button
} from '@ant-design/icons'
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from 'recharts'
import { healthAPI, databaseAPI, alertAPI } from '../services/api'
import dayjs from 'dayjs'

const { Title, Text } = Typography

const Dashboard = () => {
  const [loading, setLoading] = useState(true)
  const [healthData, setHealthData] = useState(null)
  const [dbStats, setDbStats] = useState({
    total: 0,
    active: 0,
    warning: 0,
    error: 0
  })
  const [alertStats, setAlertStats] = useState({
    critical: 0,
    warning: 0,
    total: 0
  })
  const [trendData, setTrendData] = useState([])

  const fetchDashboardData = async () => {
    setLoading(true)
    try {
      // 并行获取数据
      const [health, dbList, alerts] = await Promise.all([
        healthAPI.check().catch(() => null),
        databaseAPI.list().catch(() => ({ data: [] })),
        alertAPI.list({ limit: 100 }).catch(() => ({ data: [] }))
      ])

      // 处理健康检查数据
      if (health) {
        setHealthData(health)
      }

      // 处理数据库统计
      const databases = dbList.data?.databases || []
      setDbStats({
        total: databases.length,
        active: databases.filter(db => db.is_active).length,
        warning: 0, // 需要基于实际状态计算
        error: 0
      })

      // 处理告警统计
      const alertList = alerts.data?.alerts || []
      setAlertStats({
        critical: alertList.filter(a => a.severity === 'critical').length,
        warning: alertList.filter(a => a.severity === 'warning').length,
        total: alertList.length
      })

      // 生成趋势数据（模拟）
      const now = dayjs()
      const mockTrend = Array.from({ length: 24 }, (_, i) => ({
        time: now.subtract(23 - i, 'hour').format('HH:mm'),
        cpu: 30 + Math.random() * 40,
        memory: 40 + Math.random() * 30,
        alerts: Math.floor(Math.random() * 5)
      }))
      setTrendData(mockTrend)

    } catch (error) {
      console.error('获取仪表盘数据失败:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDashboardData()
    // 定时刷新
    const interval = setInterval(fetchDashboardData, 60000)
    return () => clearInterval(interval)
  }, [])

  if (loading && !healthData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
        <Spin size="large" tip="加载仪表盘数据..." />
      </div>
    )
  }

  return (
    <div className="dashboard">
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={4} style={{ margin: 0 }}>数据库监控仪表盘</Title>
        <Button icon={<ReloadOutlined />} onClick={fetchDashboardData} loading={loading}>
          刷新
        </Button>
      </div>

      {/* 健康状态提示 */}
      {healthData && (
        <Alert
          message={`系统状态: ${healthData.status === 'healthy' ? '正常' : '异常'}`}
          description={`活跃数据库: ${healthData.metrics?.active_databases || 0}, 活跃告警: ${healthData.metrics?.active_alerts || 0}`}
          type={healthData.status === 'healthy' ? 'success' : 'error'}
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="数据库总数"
              value={dbStats.total}
              prefix={<DatabaseOutlined />}
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="正常运行"
              value={dbStats.active}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="严重告警"
              value={alertStats.critical}
              prefix={<CloseCircleOutlined />}
              valueStyle={{ color: '#ff4d4f' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="警告"
              value={alertStats.warning}
              prefix={<WarningOutlined />}
              valueStyle={{ color: '#faad14' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 趋势图表 */}
      <Row gutter={16}>
        <Col span={12}>
          <Card title="CPU/内存使用率趋势" size="small">
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" />
                <YAxis domain={[0, 100]} />
                <Tooltip />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="cpu"
                  name="CPU %"
                  stroke="#1890ff"
                  fill="#1890ff"
                  fillOpacity={0.3}
                />
                <Area
                  type="monotone"
                  dataKey="memory"
                  name="内存 %"
                  stroke="#722ed1"
                  fill="#722ed1"
                  fillOpacity={0.3}
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="告警趋势" size="small">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="alerts"
                  name="告警数"
                  stroke="#ff4d4f"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* 性能指标 */}
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="实时性能指标" size="small">
            <Row gutter={16}>
              <Col span={4}>
                <Statistic
                  title="平均CPU"
                  value={trendData.length > 0 ? (trendData.reduce((a, b) => a + b.cpu, 0) / trendData.length).toFixed(1) : 0}
                  suffix="%"
                  precision={1}
                  valueStyle={{ color: '#1890ff' }}
                  prefix={<DatabaseOutlined />}
                />
              </Col>
              <Col span={4}>
                <Statistic
                  title="平均内存"
                  value={trendData.length > 0 ? (trendData.reduce((a, b) => a + b.memory, 0) / trendData.length).toFixed(1) : 0}
                  suffix="%"
                  precision={1}
                  valueStyle={{ color: '#722ed1' }}
                />
              </Col>
              <Col span={4}>
                <Statistic
                  title="总数据库"
                  value={dbStats.total}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Col>
              <Col span={4}>
                <Statistic
                  title="活跃告警"
                  value={alertStats.total}
                  valueStyle={{ color: alertStats.critical > 0 ? '#ff4d4f' : '#52c41a' }}
                />
              </Col>
              <Col span={4}>
                <Statistic
                  title="系统状态"
                  value={healthData?.status === 'healthy' ? '正常' : '异常'}
                  valueStyle={{ color: healthData?.status === 'healthy' ? '#52c41a' : '#ff4d4f' }}
                />
              </Col>
              <Col span={4}>
                <Statistic
                  title="最后更新"
                  value={dayjs().format('HH:mm:ss')}
                  valueStyle={{ fontSize: 16 }}
                />
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default Dashboard

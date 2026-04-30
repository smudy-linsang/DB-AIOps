import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Table, Tag, Space, Button, Input, Select, Card,
  Typography, Tooltip, Statistic, Row, Col, message,
  Badge, Progress, Switch, Dropdown, Alert
} from 'antd'
import {
  SearchOutlined, ReloadOutlined, PlusOutlined,
  DatabaseOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, WarningOutlined, QuestionCircleOutlined,
  ExclamationCircleOutlined, SortAscendingOutlined,
  ArrowUpOutlined, ArrowDownOutlined, SyncOutlined,
  InfoCircleOutlined, FireOutlined
} from '@ant-design/icons'
import { databaseAPI, alertAPI } from '../services/api'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const { Title, Text } = Typography
const { Option } = Select

// 数据库类型映射
const DB_TYPE_MAP = {
  'oracle': 'Oracle',
  'mysql': 'MySQL',
  'pgsql': 'PostgreSQL',
  'dm': '达梦数据库',
  'gbase': 'Gbase 8a',
  'tdsql': 'TDSQL',
  'mongo': 'MongoDB',
  'redis': 'Redis'
}

// 数据库类型颜色
const DB_TYPE_COLORS = {
  'oracle': '#F44336',
  'mysql': '#007EE5',
  'pgsql': '#336791',
  'dm': '#D34A37',
  'gbase': '#00A859',
  'tdsql': '#FF9800',
  'mongo': '#4DB6AC',
  'redis': '#FF6370'
}

// 状态配置
const STATUS_CONFIG = {
  UP: { color: '#52c41a', text: '正常', icon: <CheckCircleOutlined />, tag: 'success' },
  DOWN: { color: '#ff4d4f', text: '故障', icon: <CloseCircleOutlined />, tag: 'error' },
  UNKNOWN: { color: '#999', text: '未知', icon: <QuestionCircleOutlined />, tag: 'default' }
}

// 健康分颜色
const getHealthColor = (score) => {
  if (score === null || score === undefined) return '#999'
  if (score >= 80) return '#52c41a'
  if (score >= 60) return '#faad14'
  return '#ff4d4f'
}

// 健康分等级
const getHealthLevel = (score) => {
  if (score === null || score === undefined) return '无数据'
  if (score >= 80) return '健康'
  if (score >= 60) return '亚健康'
  return '问题'
}

// 缓存配置
const CACHE_CONFIG = {
  status: { ttl: 30000, key: 'db_status_' },   // 30秒
  health: { ttl: 300000, key: 'db_health_' },  // 5分钟
  alerts: { ttl: 60000, key: 'db_alerts_' }    // 1分钟
}

// 简单缓存实现
const cache = new Map()

const getCachedData = (key, ttl) => {
  const cached = cache.get(key)
  if (cached && Date.now() - cached.timestamp < ttl) {
    return cached.data
  }
  return null
}

const setCachedData = (key, data) => {
  cache.set(key, { data, timestamp: Date.now() })
}

const DatabaseList = () => {
  const navigate = useNavigate()
  const [databases, setDatabases] = useState([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [dbDetails, setDbDetails] = useState({}) // 存储每个数据库的详细信息
  const [lastRefresh, setLastRefresh] = useState(null)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [autoRefreshInterval, setAutoRefreshInterval] = useState(30) // 秒
  const autoRefreshTimerRef = useRef(null)
  const abortControllerRef = useRef(null)

  const [filters, setFilters] = useState({
    search: '',
    dbType: 'all',
    status: 'all',
    healthLevel: 'all'
  })

  const [sortConfig, setSortConfig] = useState({
    field: 'priority',
    order: 'desc'
  })

  // 获取数据库列表
  const fetchDatabases = useCallback(async () => {
    setLoading(true)
    try {
      const response = await databaseAPI.list()
      setDatabases(response?.databases || [])
    } catch (error) {
      console.error('获取数据失败:', error)
      message.error('获取数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  // 获取单个数据库的状态
  const fetchDbStatus = useCallback(async (dbId) => {
    const cacheKey = `${CACHE_CONFIG.status.key}${dbId}`
    const cached = getCachedData(cacheKey, CACHE_CONFIG.status.ttl)
    if (cached) return cached

    try {
      const data = await databaseAPI.getStatus(dbId)
      setCachedData(cacheKey, data)
      return data
    } catch (err) {
      return null
    }
  }, [])

  // 获取单个数据库的健康评分
  const fetchDbHealth = useCallback(async (dbId) => {
    const cacheKey = `${CACHE_CONFIG.health.key}${dbId}`
    const cached = getCachedData(cacheKey, CACHE_CONFIG.health.ttl)
    if (cached) return cached

    try {
      const data = await databaseAPI.getHealth(dbId)
      setCachedData(cacheKey, data)
      return data
    } catch (err) {
      return null
    }
  }, [])

  // 获取单个数据库的告警
  const fetchDbAlerts = useCallback(async (dbId) => {
    const cacheKey = `${CACHE_CONFIG.alerts.key}${dbId}`
    const cached = getCachedData(cacheKey, CACHE_CONFIG.alerts.ttl)
    if (cached) return cached

    try {
      const data = await alertAPI.getByDatabase(dbId)
      setCachedData(cacheKey, data)
      return data
    } catch (err) {
      return { alerts: [] }
    }
  }, [])

  // 批量获取所有数据库的详细信息
  const fetchAllDetails = useCallback(async () => {
    if (databases.length === 0) return

    setRefreshing(true)
    const detailsMap = {}

    // 并行获取所有数据库的状态、健康分、告警
    const promises = databases.map(async (db) => {
      try {
        const [status, health, alerts] = await Promise.all([
          fetchDbStatus(db.id),
          fetchDbHealth(db.id),
          fetchDbAlerts(db.id)
        ])

        // 提取关键指标
        const metrics = status?.metrics || {}
        const healthScores = health?.scores || []
        const latestHealth = healthScores.length > 0 ? healthScores[0] : null
        const alertList = alerts?.alerts || []
        const activeAlerts = alertList.filter(a => a.status === 'active')

        detailsMap[db.id] = {
          status: status?.status || 'UNKNOWN',
          collectedAt: status?.collected_at,
          healthScore: latestHealth?.total_score,
          healthLevel: getHealthLevel(latestHealth?.total_score),
          alertCount: activeAlerts.length,
          criticalAlerts: activeAlerts.filter(a => a.severity === 'critical').length,
          metrics: {
            cpu: metrics.cpu_usage || metrics.cpu || null,
            connections: metrics.threads_connected || metrics.session_count || metrics.num_backends || metrics.active_sessions || null,
            maxConnections: metrics.max_connections || metrics.max_sessions || null,
            tablespacePercent: metrics.tablespace_percent || metrics.tablespace_used_percent || null,
            qps: metrics.qps || metrics.queries_per_second || metrics.select_count || null,
            tps: metrics.tps || metrics.transactions || metrics.xact_commit || null
          }
        }
      } catch (err) {
        console.error(`获取数据库 ${db.id} 详情失败:`, err)
        detailsMap[db.id] = {
          status: 'UNKNOWN',
          healthScore: null,
          alertCount: 0,
          criticalAlerts: 0,
          metrics: {}
        }
      }
    })

    await Promise.all(promises)
    setDbDetails(detailsMap)
    setLastRefresh(dayjs())
    setRefreshing(false)
  }, [databases, fetchDbStatus, fetchDbHealth, fetchDbAlerts])

  // 初始加载
  useEffect(() => {
    fetchDatabases()
  }, [fetchDatabases])

  // 数据库列表加载后获取详情
  useEffect(() => {
    if (databases.length > 0) {
      fetchAllDetails()
    }
  }, [databases, fetchAllDetails])

  // 自动刷新
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshTimerRef.current = setInterval(() => {
        fetchAllDetails()
      }, autoRefreshInterval * 1000)
    }
    return () => {
      if (autoRefreshTimerRef.current) {
        clearInterval(autoRefreshTimerRef.current)
      }
    }
  }, [autoRefresh, autoRefreshInterval, fetchAllDetails])

  // 手动刷新
  const handleRefresh = useCallback(() => {
    // 清除缓存
    cache.clear()
    fetchDatabases()
  }, [fetchDatabases])

  // 计算优先级分数（用于排序）
  const getPriorityScore = useCallback((db) => {
    const detail = dbDetails[db.id] || {}
    const healthScore = detail.healthScore ?? 100
    const alertCount = detail.alertCount || 0
    const isDown = detail.status === 'DOWN'

    return (100 - healthScore) * 10 + alertCount * 5 + (isDown ? 1000 : 0)
  }, [dbDetails])

  // 过滤和排序
  const filteredAndSortedDatabases = databases
    .filter(db => {
      if (filters.search && !db.name?.toLowerCase().includes(filters.search.toLowerCase())) {
        return false
      }
      if (filters.dbType !== 'all' && db.db_type !== filters.dbType) {
        return false
      }
      if (filters.status !== 'all') {
        const detail = dbDetails[db.id] || {}
        if (filters.status === 'up' && detail.status !== 'UP') return false
        if (filters.status === 'down' && detail.status !== 'DOWN') return false
        if (filters.status === 'unknown' && detail.status !== 'UNKNOWN') return false
      }
      if (filters.healthLevel !== 'all') {
        const detail = dbDetails[db.id] || {}
        if (filters.healthLevel === 'healthy' && (detail.healthScore === null || detail.healthScore < 80)) return false
        if (filters.healthLevel === 'warning' && (detail.healthScore === null || detail.healthScore < 60 || detail.healthScore >= 80)) return false
        if (filters.healthLevel === 'critical' && (detail.healthScore === null || detail.healthScore >= 60)) return false
        if (filters.healthLevel === 'no_data' && detail.healthScore !== null) return false
      }
      return true
    })
    .sort((a, b) => {
      const detailA = dbDetails[a.id] || {}
      const detailB = dbDetails[b.id] || {}

      let valueA, valueB

      switch (sortConfig.field) {
        case 'priority':
          valueA = getPriorityScore(a)
          valueB = getPriorityScore(b)
          break
        case 'health':
          valueA = detailA.healthScore ?? -1
          valueB = detailB.healthScore ?? -1
          break
        case 'alerts':
          valueA = detailA.alertCount || 0
          valueB = detailB.alertCount || 0
          break
        case 'status':
          const statusOrder = { 'DOWN': 0, 'UNKNOWN': 1, 'UP': 2 }
          valueA = statusOrder[detailA.status] ?? 1
          valueB = statusOrder[detailB.status] ?? 1
          break
        case 'name':
          valueA = a.name || ''
          valueB = b.name || ''
          return sortConfig.order === 'asc'
            ? valueA.localeCompare(valueB)
            : valueB.localeCompare(valueA)
        case 'updated':
          valueA = new Date(a.updated_at || 0).getTime()
          valueB = new Date(b.updated_at || 0).getTime()
          break
        default:
          valueA = getPriorityScore(a)
          valueB = getPriorityScore(b)
      }

      return sortConfig.order === 'asc' ? valueA - valueB : valueB - valueA
    })

  // 统计数据
  const stats = {
    total: databases.length,
    up: databases.filter(db => (dbDetails[db.id] || {}).status === 'UP').length,
    down: databases.filter(db => (dbDetails[db.id] || {}).status === 'DOWN').length,
    unknown: databases.filter(db => (dbDetails[db.id] || {}).status === 'UNKNOWN').length,
    healthy: databases.filter(db => {
      const score = (dbDetails[db.id] || {}).healthScore
      return score !== null && score !== undefined && score >= 80
    }).length,
    warning: databases.filter(db => {
      const score = (dbDetails[db.id] || {}).healthScore
      return score !== null && score !== undefined && score >= 60 && score < 80
    }).length,
    critical: databases.filter(db => {
      const score = (dbDetails[db.id] || {}).healthScore
      return score !== null && score !== undefined && score < 60
    }).length,
    withAlerts: databases.filter(db => (dbDetails[db.id] || {}).alertCount > 0).length
  }

  // 获取数据库类型标签
  const getDbTypeTag = (type) => {
    const color = DB_TYPE_COLORS[type?.toLowerCase()] || '#999'
    const text = DB_TYPE_MAP[type?.toLowerCase()] || type
    return (
      <Tag color={color} style={{ fontWeight: 500 }}>
        {text}
      </Tag>
    )
  }

  // 获取状态标签
  const getStatusTag = (dbId) => {
    const detail = dbDetails[dbId] || {}
    const config = STATUS_CONFIG[detail.status] || STATUS_CONFIG.UNKNOWN

    return (
      <Tag
        icon={config.icon}
        color={config.tag}
        style={{ fontWeight: 500 }}
      >
        {config.text}
      </Tag>
    )
  }

  // 获取健康分显示
  const getHealthBadge = (dbId) => {
    const detail = dbDetails[dbId] || {}
    const score = detail.healthScore

    if (score === null || score === undefined) {
      return <Tag color="default">无数据</Tag>
    }

    const color = getHealthColor(score)
    const level = getHealthLevel(score)

    return (
      <Tooltip title={`${level} - ${score}分`}>
        <Tag
          color={color}
          style={{
            fontWeight: 600,
            fontSize: 14,
            padding: '2px 8px',
            minWidth: 60,
            textAlign: 'center'
          }}
        >
          {score}分
        </Tag>
      </Tooltip>
    )
  }

  // 获取告警徽章
  const getAlertBadge = (dbId) => {
    const detail = dbDetails[dbId] || {}
    const count = detail.alertCount || 0
    const critical = detail.criticalAlerts || 0

    if (count === 0) {
      return (
        <Badge
          count={0}
          showZero
          style={{ backgroundColor: '#52c41a' }}
        />
      )
    }

    return (
      <Tooltip title={critical > 0 ? `${critical}个严重告警` : `${count}个告警`}>
        <Badge
          count={count}
          style={{
            backgroundColor: critical > 0 ? '#ff4d4f' : '#faad14',
            fontWeight: 600
          }}
          overflowCount={99}
        />
      </Tooltip>
    )
  }

  // 格式化指标值
  const formatMetric = (value, type) => {
    if (value === null || value === undefined) return '-'

    switch (type) {
      case 'percent':
        return `${Number(value).toFixed(1)}%`
      case 'connections':
        return Math.round(Number(value)).toLocaleString()
      case 'qps':
        return Number(value).toFixed(1)
      default:
        return String(value)
    }
  }

  // 获取指标颜色
  const getMetricColor = (value, type) => {
    if (value === null || value === undefined) return '#999'

    switch (type) {
      case 'cpu':
        if (value > 80) return '#ff4d4f'
        if (value > 60) return '#faad14'
        return '#52c41a'
      case 'tablespace':
        if (value > 90) return '#ff4d4f'
        if (value > 80) return '#faad14'
        return '#52c41a'
      default:
        return '#333'
    }
  }

  // 表格列定义
  const columns = [
    {
      title: '数据库名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      fixed: 'left',
      render: (text, record) => (
        <Space direction="vertical" size={2}>
          <Link to={`/databases/${record.id}`} style={{ fontWeight: 600, fontSize: 14 }}>
            {text}
          </Link>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.host}:{record.port}
          </Text>
        </Space>
      )
    },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => getDbTypeTag(type)
    },
    {
      title: '状态',
      key: 'status',
      width: 80,
      render: (_, record) => getStatusTag(record.id)
    },
    {
      title: '健康分',
      key: 'health',
      width: 90,
      sorter: true,
      render: (_, record) => getHealthBadge(record.id)
    },
    {
      title: '告警',
      key: 'alerts',
      width: 70,
      render: (_, record) => getAlertBadge(record.id)
    },
    {
      title: 'CPU',
      key: 'cpu',
      width: 100,
      render: (_, record) => {
        const detail = dbDetails[record.id] || {}
        const cpu = detail.metrics?.cpu
        const color = getMetricColor(cpu, 'cpu')

        if (cpu === null || cpu === undefined) return <Text type="secondary">-</Text>

        return (
          <Space size={4}>
            <Progress
              percent={Number(cpu)}
              size="small"
              strokeColor={color}
              showInfo={false}
              style={{ width: 50 }}
            />
            <Text style={{ color, fontWeight: 500, fontSize: 12 }}>
              {formatMetric(cpu, 'percent')}
            </Text>
          </Space>
        )
      }
    },
    {
      title: '连接数',
      key: 'connections',
      width: 100,
      render: (_, record) => {
        const detail = dbDetails[record.id] || {}
        const conn = detail.metrics?.connections
        const maxConn = detail.metrics?.maxConnections

        if (conn === null || conn === undefined) return <Text type="secondary">-</Text>

        return (
          <Tooltip title={maxConn ? `最大: ${maxConn}` : ''}>
            <Text style={{ fontWeight: 500 }}>
              {formatMetric(conn, 'connections')}
              {maxConn && <Text type="secondary" style={{ fontSize: 11 }}>/{maxConn}</Text>}
            </Text>
          </Tooltip>
        )
      }
    },
    {
      title: '表空间',
      key: 'tablespace',
      width: 100,
      render: (_, record) => {
        const detail = dbDetails[record.id] || {}
        const percent = detail.metrics?.tablespacePercent
        const color = getMetricColor(percent, 'tablespace')

        if (percent === null || percent === undefined) return <Text type="secondary">-</Text>

        return (
          <Space size={4}>
            <Progress
              percent={Number(percent)}
              size="small"
              strokeColor={color}
              showInfo={false}
              style={{ width: 50 }}
            />
            <Text style={{ color, fontWeight: 500, fontSize: 12 }}>
              {formatMetric(percent, 'percent')}
            </Text>
          </Space>
        )
      }
    },
    {
      title: '环境',
      dataIndex: 'environment',
      key: 'environment',
      width: 80,
      render: (env) => env ? <Tag>{env}</Tag> : '-'
    },
    {
      title: '最后采集',
      key: 'collected_at',
      width: 120,
      render: (_, record) => {
        const detail = dbDetails[record.id] || {}
        const collectedAt = detail.collectedAt

        if (!collectedAt) return <Text type="secondary">-</Text>

        const time = dayjs(collectedAt)
        const isStale = dayjs().diff(time, 'minute') > 10

        return (
          <Tooltip title={time.format('YYYY-MM-DD HH:mm:ss')}>
            <Text type={isStale ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
              {time.fromNow()}
            </Text>
          </Tooltip>
        )
      }
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      fixed: 'right',
      render: (_, record) => (
        <Space size="small">
          <Link to={`/databases/${record.id}`}>
            <Button type="link" size="small" icon={<InfoCircleOutlined />}>
              详情
            </Button>
          </Link>
        </Space>
      )
    }
  ]

  // 排序菜单
  const sortMenuItems = [
    { key: 'priority', label: '智能排序（问题优先）', icon: <FireOutlined /> },
    { key: 'health', label: '按健康分', icon: <ArrowUpOutlined /> },
    { key: 'alerts', label: '按告警数', icon: <WarningOutlined /> },
    { key: 'status', label: '按状态', icon: <ExclamationCircleOutlined /> },
    { key: 'name', label: '按名称', icon: <SortAscendingOutlined /> },
    { key: 'updated', label: '按更新时间', icon: <ClockCircleOutlined /> }
  ]

  const handleSortChange = (key) => {
    setSortConfig(prev => ({
      field: key,
      order: prev.field === key && prev.order === 'desc' ? 'asc' : 'desc'
    }))
  }

  return (
    <div className="database-list" style={{ padding: 0 }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: 24 }}>
        <Row justify="space-between" align="middle">
          <Col>
            <Title level={4} style={{ marginBottom: 0 }}>
              <DatabaseOutlined /> 数据库列表
            </Title>
          </Col>
          <Col>
            <Space>
              {lastRefresh && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  更新于 {lastRefresh.format('HH:mm:ss')}
                </Text>
              )}
              <Switch
                checkedChildren="自动刷新"
                unCheckedChildren="手动"
                checked={autoRefresh}
                onChange={setAutoRefresh}
                size="small"
              />
              <Button
                icon={<SyncOutlined spin={refreshing} />}
                onClick={handleRefresh}
                loading={loading}
              >
                刷新
              </Button>
            </Space>
          </Col>
        </Row>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable>
            <Statistic
              title="总数据库"
              value={stats.total}
              prefix={<DatabaseOutlined />}
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable>
            <Statistic
              title="正常运行"
              value={stats.up}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable>
            <Statistic
              title="故障"
              value={stats.down}
              prefix={<CloseCircleOutlined />}
              valueStyle={{ color: '#ff4d4f' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable>
            <Statistic
              title="未知"
              value={stats.unknown}
              prefix={<QuestionCircleOutlined />}
              valueStyle={{ color: '#999' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable style={{ borderLeft: '3px solid #52c41a' }}>
            <Statistic
              title="健康"
              value={stats.healthy}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable style={{ borderLeft: '3px solid #faad14' }}>
            <Statistic
              title="亚健康"
              value={stats.warning}
              prefix={<WarningOutlined />}
              valueStyle={{ color: '#faad14' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable style={{ borderLeft: '3px solid #ff4d4f' }}>
            <Statistic
              title="问题库"
              value={stats.critical}
              prefix={<ExclamationCircleOutlined />}
              valueStyle={{ color: '#ff4d4f' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={3}>
          <Card size="small" hoverable style={{ borderLeft: '3px solid #722ed1' }}>
            <Statistic
              title="告警中"
              value={stats.withAlerts}
              prefix={<FireOutlined />}
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 过滤和排序工具栏 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 12]} align="middle">
          <Col flex="auto">
            <Space wrap>
              <Input
                placeholder="搜索数据库名称"
                prefix={<SearchOutlined />}
                style={{ width: 200 }}
                onChange={(e) => setFilters(prev => ({ ...prev, search: e.target.value }))}
                allowClear
              />
              <Select
                value={filters.dbType}
                onChange={(value) => setFilters(prev => ({ ...prev, dbType: value }))}
                style={{ width: 120 }}
              >
                <Option value="all">全部类型</Option>
                {Object.entries(DB_TYPE_MAP).map(([key, name]) => (
                  <Option key={key} value={key}>{name}</Option>
                ))}
              </Select>
              <Select
                value={filters.status}
                onChange={(value) => setFilters(prev => ({ ...prev, status: value }))}
                style={{ width: 100 }}
              >
                <Option value="all">全部状态</Option>
                <Option value="up">正常</Option>
                <Option value="down">故障</Option>
                <Option value="unknown">未知</Option>
              </Select>
              <Select
                value={filters.healthLevel}
                onChange={(value) => setFilters(prev => ({ ...prev, healthLevel: value }))}
                style={{ width: 120 }}
              >
                <Option value="all">全部健康</Option>
                <Option value="healthy">健康(≥80)</Option>
                <Option value="warning">亚健康(60-80)</Option>
                <Option value="critical">{'问题(<60)'}</Option>
                <Option value="no_data">无数据</Option>
              </Select>
            </Space>
          </Col>
          <Col>
            <Space>
              <Dropdown
                menu={{
                  items: sortMenuItems.map(item => ({
                    ...item,
                    onClick: () => handleSortChange(item.key)
                  })),
                  selectedKeys: [sortConfig.field]
                }}
              >
                <Button icon={<SortAscendingOutlined />}>
                  排序: {sortMenuItems.find(i => i.key === sortConfig.field)?.label || '智能排序'}
                  {sortConfig.order === 'asc' ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
                </Button>
              </Dropdown>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => message.info('添加数据库功能开发中')}>
                添加数据库
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* 问题库提示 */}
      {stats.down > 0 && (
        <Alert
          message={`发现 ${stats.down} 个故障数据库，请立即处理！`}
          type="error"
          showIcon
          closable
          style={{ marginBottom: 16 }}
          action={
            <Button size="small" danger onClick={() => setFilters(prev => ({ ...prev, status: 'down' }))}>
              查看故障库
            </Button>
          }
        />
      )}

      {/* 数据表格 */}
      <Table
        columns={columns}
        dataSource={filteredAndSortedDatabases}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1200 }}
        pagination={{
          defaultPageSize: 20,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 个数据库`,
          pageSizeOptions: ['10', '20', '50', '100']
        }}
        rowClassName={(record) => {
          const detail = dbDetails[record.id] || {}
          if (detail.status === 'DOWN') return 'row-error'
          if (detail.alertCount > 0) return 'row-warning'
          return ''
        }}
        onRow={(record) => ({
          onClick: () => navigate(`/databases/${record.id}`),
          style: { cursor: 'pointer' }
        })}
      />

      {/* 自定义样式 */}
      <style>{`
        .row-error {
          background-color: #fff2f0 !important;
        }
        .row-error:hover td {
          background-color: #ffebe8 !important;
        }
        .row-warning {
          background-color: #fffbe6 !important;
        }
        .row-warning:hover td {
          background-color: #fff7cc !important;
        }
        .ant-table-row {
          transition: background-color 0.2s;
        }
      `}</style>
    </div>
  )
}

export default DatabaseList

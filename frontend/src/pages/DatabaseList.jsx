import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Table, Tag, Space, Button, Input, Select, Card,
  Typography, Tooltip, Statistic, Row, Col, message,
  Badge, Progress, Switch, Dropdown, Alert, Modal, Form,
  InputNumber, Popconfirm, Skeleton
} from 'antd'
import {
  SearchOutlined, ReloadOutlined, PlusOutlined,
  DatabaseOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, WarningOutlined, QuestionCircleOutlined,
  ExclamationCircleOutlined, SortAscendingOutlined,
  ArrowUpOutlined, ArrowDownOutlined, SyncOutlined,
  InfoCircleOutlined, FireOutlined, EditOutlined
} from '@ant-design/icons'
import { databaseAPI, alertAPI, collectTemplateAPI } from '../services/api'
import { PermissionGuard } from '../components/AuthGuard'
import { Perm } from '../utils/permission'
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

// 数据库默认端口
const DEFAULT_PORTS = {
  'oracle': 1521,
  'mysql': 3306,
  'pgsql': 5432,
  'dm': 5236,
  'gbase': 5258,
  'tdsql': 15002,
  'mongo': 27017,
  'redis': 6379
}

// 数据库类型专属推荐配置模板
export const DB_CONFIG_TEMPLATES = {
  'oracle': [
    { label: 'Oracle 标准生产模板 (60s 周期 + 表空间/锁等待/ADG)', value: 'oracle_standard', port: 1521, interval: 60, service_name: 'orcl', desc: '采集 v$session, v$lock, dba_tablespaces, 缓冲池命中率及 ADG 延迟' },
    { label: 'Oracle 核心高频模板 (15s 周期 + 深度 ASH 采样)', value: 'oracle_high_freq', port: 1521, interval: 15, service_name: 'orcl', desc: '15秒高频探查锁阻塞链与活跃会话，适合核心交易账务库' },
    { label: 'Oracle 轻量归档模板 (300s 周期 + 基础容量)', value: 'oracle_light', port: 1521, interval: 300, service_name: 'orcl', desc: '5分钟低频巡检，适合只读报表库或测试环境' },
  ],
  'mysql': [
    { label: 'MySQL 生产标准模板 (60s 周期 + InnoDB/主从复制)', value: 'mysql_standard', port: 3306, interval: 60, desc: '采集 QPS/TPS、活跃线程、InnoDB Buffer Pool 及 Replication Lag' },
    { label: 'MySQL 高频高并发模板 (10s 周期 + 锁等待热点)', value: 'mysql_high_freq', port: 3306, interval: 10, desc: '10秒超高频探测死锁与连接数暴增，适合大促秒杀场景' },
  ],
  'pgsql': [
    { label: 'PostgreSQL 生产标准模板 (60s 周期 + 膨胀率/流复制)', value: 'pgsql_standard', port: 5432, interval: 60, desc: '采集 pg_stat_activity, pg_stat_database, 表膨胀与 WAL 归档延迟' },
  ],
  'dm': [
    { label: '达梦 DM8 国产库生产模板 (60s 周期 + DSC 集群/表空间)', value: 'dm_standard', port: 5236, interval: 60, desc: '采集 V$SESSIONS, V$DATAFILE, 表空间水位及 DSC 共享集群状态' },
  ],
  'tdsql': [
    { label: 'TDSQL 分布式/集中式标准模板 (60s 周期 + Set/ZK 状态)', value: 'tdsql_standard', port: 15002, interval: 60, desc: '采集分布式多节点心跳、主备延迟及分片健康度' },
  ],
  'gbase': [
    { label: 'GBase 8a 大数据集群模板 (120s 周期 + 数据节点存储)', value: 'gbase_standard', port: 5258, interval: 120, desc: '采集管理节点调度状态与各数据节点磁盘均衡度' },
  ],
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
  const [initialLoading, setInitialLoading] = useState(true)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [dbDetails, setDbDetails] = useState({}) // 存储每个数据库的详细信息
  const [lastRefresh, setLastRefresh] = useState(null)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [autoRefreshInterval, setAutoRefreshInterval] = useState(30) // 秒
  const autoRefreshTimerRef = useRef(null)
  const abortControllerRef = useRef(null)
  const lastFetchTimeRef = useRef(0)  // 防抖：记录上次 fetchAllDetails 时间

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

  // 批量选择状态
  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [batchDeleteLoading, setBatchDeleteLoading] = useState(false)

  // 添加数据库弹窗状态
  const [addModalVisible, setAddModalVisible] = useState(false)
  const [addModalLoading, setAddModalLoading] = useState(false)
  const [testLoading, setTestLoading] = useState(false)
  const [testResult, setTestResult] = useState(null) // { success, message, version }
  const [form] = Form.useForm()

  // 编辑数据库弹窗状态
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [editModalLoading, setEditModalLoading] = useState(false)
  const [editTestLoading, setEditTestLoading] = useState(false)
  const [editTestResult, setEditTestResult] = useState(null)
  const [editingDb, setEditingDb] = useState(null) // 当前正在编辑的数据库
  const [editForm] = Form.useForm()

  const [collectTemplates, setCollectTemplates] = useState([])

  // 获取数据库列表与采集模板列表
  const fetchDatabases = useCallback(async () => {
    setLoading(true)
    try {
      const [dbRes, tplRes] = await Promise.all([
        databaseAPI.list(),
        collectTemplateAPI.list().catch(() => ({ templates: [] }))
      ])
      setDatabases(dbRes?.databases || [])
      setCollectTemplates(tplRes?.templates || [])
    } catch (error) {
      console.error('获取数据失败:', error)
      message.error('获取数据失败')
    } finally {
      setLoading(false)
      setInitialLoading(false)
    }
  }, [])

  // 打开添加数据库弹窗
  const openAddModal = () => {
    form.resetFields()
    setTestResult(null)
    setAddModalVisible(true)
  }

  // 提交添加数据库表单
  const handleAddDatabase = async (values) => {
    setAddModalLoading(true)
    try {
      await databaseAPI.create(values)
      message.success('数据库配置添加成功')
      setAddModalVisible(false)
      form.resetFields()
      setTestResult(null)
      fetchDatabases()
    } catch (error) {
      console.error('添加数据库失败:', error)
      message.error(error.response?.data?.error || '添加数据库失败')
    } finally {
      setAddModalLoading(false)
    }
  }

  // 测试数据库连接
  const handleTestConnection = async () => {
    try {
      const values = await form.validateFields()
      setTestLoading(true)
      setTestResult(null)
      const response = await databaseAPI.testConnection(values)
      setTestResult(response)
    } catch (error) {
      if (error.errorFields) {
        // 表单验证未通过，不处理
        return
      }
      setTestResult({
        success: false,
        message: error.response?.data?.message || error.response?.data?.error || '连接测试请求失败'
      })
    } finally {
      setTestLoading(false)
    }
  }

  // 辅助方法：获取当前库型的所有可用模板 (API优先, 静态预置兜底)
  const getAvailableTemplates = useCallback((dbType) => {
    const apiMatched = collectTemplates.filter(t => t.db_type === dbType)
    if (apiMatched.length > 0) {
      return apiMatched.map(t => ({
        label: `${t.name} (${t.collect_interval_sec}s)`,
        value: t.code,
        port: t.default_port,
        interval: t.collect_interval_sec,
        service_name: t.default_service_name,
        desc: t.description
      }))
    }
    return (DB_CONFIG_TEMPLATES[dbType] || []).map(t => ({
      label: t.label,
      value: t.value,
      port: t.port,
      interval: t.interval,
      service_name: t.service_name,
      desc: t.desc
    }))
  }, [collectTemplates])

  // 数据库类型变更时自动设置默认端口与模板
  const handleDbTypeChange = (dbType) => {
    const templates = getAvailableTemplates(dbType)
    const defaultTpl = templates[0]
    form.setFieldsValue({
      port: defaultTpl?.port || DEFAULT_PORTS[dbType] || 3306,
      template_name: defaultTpl ? defaultTpl.value : '',
      collect_interval_sec: defaultTpl ? defaultTpl.interval : 60,
      service_name: (dbType === 'oracle' && defaultTpl?.service_name) ? defaultTpl.service_name : form.getFieldValue('service_name')
    })
  }

  // 选择配置模板时自动联动端口、采集周期与服务名
  const handleTemplateChange = (templateVal) => {
    const currentDbType = form.getFieldValue('db_type')
    const templates = getAvailableTemplates(currentDbType)
    const selected = templates.find(t => t.value === templateVal)
    if (selected) {
      form.setFieldsValue({
        port: selected.port || form.getFieldValue('port'),
        collect_interval_sec: selected.interval || 60,
        service_name: selected.service_name || form.getFieldValue('service_name')
      })
    }
  }

  const handleEditTemplateChange = (templateVal) => {
    const currentDbType = editForm.getFieldValue('db_type')
    const templates = getAvailableTemplates(currentDbType)
    const selected = templates.find(t => t.value === templateVal)
    if (selected) {
      editForm.setFieldsValue({
        port: selected.port || editForm.getFieldValue('port'),
        collect_interval_sec: selected.interval || 60,
        service_name: selected.service_name || editForm.getFieldValue('service_name')
      })
    }
  }

  // 打开编辑数据库弹窗
  const openEditModal = async (db) => {
    setEditingDb(db)
    setEditTestResult(null)
    // 获取最新配置详情
    try {
      const detail = await databaseAPI.getDetail(db.id)
      editForm.setFieldsValue({
        name: detail.name,
        db_type: detail.db_type,
        host: detail.host,
        port: detail.port,
        username: detail.username,
        password: '',
        service_name: detail.service_name || '',
        collect_interval_sec: detail.collect_interval_sec || 60,
        template_name: detail.template_name || ''
      })
      setEditModalVisible(true)
    } catch (error) {
      message.error('获取数据库详情失败')
    }
  }

  // 提交编辑数据库表单
  const handleEditDatabase = async (values) => {
    if (!editingDb) return
    setEditModalLoading(true)
    try {
      // 如果密码为空，不提交密码字段
      const submitData = { ...values }
      if (!submitData.password) {
        delete submitData.password
      }
      await databaseAPI.update(editingDb.id, submitData)
      message.success('数据库配置更新成功')
      setEditModalVisible(false)
      setEditingDb(null)
      editForm.resetFields()
      setEditTestResult(null)
      fetchDatabases()
    } catch (error) {
      console.error('更新数据库失败:', error)
      message.error(error.response?.data?.error || '更新数据库失败')
    } finally {
      setEditModalLoading(false)
    }
  }

  // 测试编辑后的数据库连接
  const handleEditTestConnection = async () => {
    try {
      const values = await editForm.validateFields()
      setEditTestLoading(true)
      setEditTestResult(null)
      const response = await databaseAPI.testConnection(values)
      setEditTestResult(response)
    } catch (error) {
      if (error.errorFields) {
        return
      }
      setEditTestResult({
        success: false,
        message: error.response?.data?.message || error.response?.data?.error || '连接测试请求失败'
      })
    } finally {
      setEditTestLoading(false)
    }
  }

  // 编辑弹窗中数据库类型变更
  const handleEditDbTypeChange = (dbType) => {
    editForm.setFieldsValue({ port: DEFAULT_PORTS[dbType] || 3306 })
  }

  // 删除数据库（级联删除所有关联监控数据）
  const handleDeleteDatabase = async (db) => {
    try {
      const response = await databaseAPI.delete(db.id)
      message.success(response?.message || `数据库「${db.name}」及关联监控数据已删除`)
      fetchDatabases()
    } catch (error) {
      console.error('删除数据库失败:', error)
      message.error(error.response?.data?.error || '删除数据库失败')
    }
  }

  // 批量删除选中的数据库
  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    
    const selectedDbs = databases.filter(db => selectedRowKeys.includes(db.id))
    const dbNames = selectedDbs.map(db => db.name).join('、')
    
    Modal.confirm({
      title: '确认批量删除',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>确定要删除以下 {selectedRowKeys.length} 个数据库吗？</p>
          <ul style={{ maxHeight: 200, overflow: 'auto', paddingLeft: 20, margin: '10px 0' }}>
            {selectedDbs.map(db => (
              <li key={db.id} style={{ color: '#ff4d4f', marginBottom: 4 }}>
                {db.name}（{db.host}:{db.port}）
              </li>
            ))}
          </ul>
          <p style={{ color: '#ff4d4f', fontWeight: 500 }}>
            所有关联的监控日志、告警记录、健康评分、基线模型、容量预测数据将一并删除！
          </p>
        </div>
      ),
      okText: '确认删除',
      cancelText: '取消',
      okButtonProps: { danger: true, loading: batchDeleteLoading },
      onOk: async () => {
        setBatchDeleteLoading(true)
        let successCount = 0
        let failCount = 0
        const failedDbs = []
        
        for (const db of selectedDbs) {
          try {
            await databaseAPI.delete(db.id)
            successCount++
          } catch (error) {
            failCount++
            failedDbs.push(db.name)
          }
        }
        
        setBatchDeleteLoading(false)
        setSelectedRowKeys([])
        fetchDatabases()
        
        if (failCount === 0) {
          message.success(`已成功删除 ${successCount} 个数据库及其关联监控数据`)
        } else {
          message.warning(`成功删除 ${successCount} 个，失败 ${failCount} 个（${failedDbs.join('、')}）`)
        }
      }
    })
  }

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

  // 批量获取所有数据库的详细信息 (支持 AbortController 取消 + 防抖)
  const fetchAllDetails = useCallback(async (force = false) => {
    if (databases.length === 0) return

    // 防抖：5 秒内不重复请求（手动刷新除外）
    const now = Date.now()
    if (!force && now - lastFetchTimeRef.current < 5000) {
      return
    }
    lastFetchTimeRef.current = now

    // 取消上一次请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    const controller = new AbortController()
    abortControllerRef.current = controller

    setRefreshing(true)
    const detailsMap = {}

    // 并行获取所有数据库的状态、健康分、告警
    const promises = databases.map(async (db) => {
      if (controller.signal.aborted) return
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
        if (err?.name === 'AbortError' || controller.signal.aborted) return
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

    try {
      await Promise.all(promises)
    } catch (e) {
      if (e?.name !== 'AbortError') console.error('批量获取详情异常:', e)
    }

    if (!controller.signal.aborted) {
      setDbDetails(detailsMap)
      setLastRefresh(dayjs())
    }
    setRefreshing(false)
  }, [databases, fetchDbStatus, fetchDbHealth, fetchDbAlerts])

  // 初始加载
  useEffect(() => {
    fetchDatabases()
  }, [fetchDatabases])

  // 数据库列表加载后获取详情 (首次加载 force=true)
  useEffect(() => {
    if (databases.length > 0) {
      fetchAllDetails(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databases])

  // 自动刷新 (force=false, 受防抖限制)
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshTimerRef.current = setInterval(() => {
        fetchAllDetails(false)
      }, autoRefreshInterval * 1000)
    }
    return () => {
      if (autoRefreshTimerRef.current) {
        clearInterval(autoRefreshTimerRef.current)
      }
    }
  }, [autoRefresh, autoRefreshInterval])

  // 手动刷新 (force=true 绕过防抖)
  const handleRefresh = useCallback(() => {
    // 清除缓存
    cache.clear()
    lastFetchTimeRef.current = 0
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
      <Tooltip title={`${level} - ${score}分 | 点击查看详情`}>
        <Tag
          color={color}
          style={{
            fontWeight: 600,
            fontSize: 14,
            padding: '2px 8px',
            minWidth: 60,
            textAlign: 'center',
            cursor: 'pointer'
          }}
          onClick={(e) => {
            e.stopPropagation()
            navigate(`/databases/${dbId}`)
          }}
        >
          {score}分
        </Tag>
      </Tooltip>
    )
  }

  // 获取告警徽章 (可点击跳转告警列表)
  const getAlertBadge = (dbId) => {
    const detail = dbDetails[dbId] || {}
    const count = detail.alertCount || 0
    const critical = detail.criticalAlerts || 0

    const handleAlertClick = (e) => {
      e.stopPropagation()
      navigate(`/alerts?dbId=${dbId}`)
    }

    if (count === 0) {
      return (
        <span onClick={handleAlertClick} style={{ cursor: 'pointer' }}>
          <Badge
            count={0}
            showZero
            style={{ backgroundColor: '#52c41a' }}
          />
        </span>
      )
    }

    return (
      <Tooltip title={critical > 0 ? `${critical}个严重告警 | 点击查看` : `${count}个告警 | 点击查看`}>
        <span onClick={handleAlertClick} style={{ cursor: 'pointer' }}>
          <Badge
            count={count}
            style={{
              backgroundColor: critical > 0 ? '#ff4d4f' : '#faad14',
              fontWeight: 600
            }}
            overflowCount={99}
          />
        </span>
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
      title: '采集周期',
      key: 'collect_interval',
      width: 95,
      render: (_, record) => {
        const sec = record.collect_interval_sec || 60
        const isHighFreq = sec <= 15
        return (
          <Tooltip title={`每 ${sec} 秒采集一次指标快照`}>
            <Tag color={isHighFreq ? 'purple' : 'blue'} style={{ borderRadius: 4 }}>
              ⏱️ {sec}s
            </Tag>
          </Tooltip>
        )
      }
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
      width: 220,
      fixed: 'right',
      render: (_, record) => (
        <Space size="small">
          <Link to={`/databases/${record.id}`}>
            <Button type="link" size="small" icon={<InfoCircleOutlined />}>
              详情
            </Button>
          </Link>
          <PermissionGuard code={Perm.DATABASES_UPDATE}>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={(e) => { e.stopPropagation(); openEditModal(record) }}
            >
              编辑
            </Button>
          </PermissionGuard>
          <PermissionGuard code={Perm.DATABASES_DELETE}>
            <Popconfirm
              title="确认删除"
              description={
                <span>
                  确定要删除数据库「{record.name}」吗？<br />
                  <Text type="danger" style={{ fontSize: 12 }}>
                    所有关联的监控日志、告警记录、健康评分、基线模型、容量预测数据将一并删除！
                  </Text>
                </span>
              }
              onConfirm={(e) => { e?.stopPropagation(); handleDeleteDatabase(record) }}
              onCancel={(e) => e?.stopPropagation()}
              okText="确认删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              placement="left"
            >
              <Button
                type="link"
                size="small"
                danger
                icon={<CloseCircleOutlined />}
                onClick={(e) => e.stopPropagation()}
              >
                删除
              </Button>
            </Popconfirm>
          </PermissionGuard>
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
      {initialLoading ? (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          {[...Array(8)].map((_, i) => (
            <Col xs={12} sm={6} md={3} key={i}>
              <Card size="small">
                <Skeleton paragraph={{ rows: 1 }} active />
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
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
      )}

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
              {selectedRowKeys.length > 0 && (
                <PermissionGuard code={Perm.DATABASES_DELETE}>
                <Button
                  type="primary"
                  danger
                  icon={<CloseCircleOutlined />}
                  onClick={handleBatchDelete}
                  loading={batchDeleteLoading}
                >
                  批量删除（{selectedRowKeys.length}）
                </Button>
                </PermissionGuard>
              )}
              <PermissionGuard code={Perm.DATABASES_CREATE}><Button type="primary" icon={<PlusOutlined />} onClick={openAddModal}>
                添加数据库
              </Button></PermissionGuard>
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
        scroll={{ x: 1400 }}
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
          columnWidth: 50
        }}
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
          onClick: (e) => {
            // 如果点击的是复选框、操作按钮或链接，不跳转详情
            const target = e.target
            if (target.type === 'checkbox' || target.closest('.ant-table-selection') ||
                target.closest('.ant-btn') || target.tagName === 'A') {
              return
            }
            navigate(`/databases/${record.id}`)
          },
          style: { cursor: 'pointer' }
        })}
      />

      {/* 添加数据库弹窗 */}
      <Modal
        title="添加数据库"
        open={addModalVisible}
        onCancel={() => setAddModalVisible(false)}
        footer={null}
        width={500}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleAddDatabase}
          initialValues={{
            db_type: 'oracle',
            port: 1521,
            collect_interval_sec: 60,
            template_name: 'oracle_standard'
          }}
        >
          <Form.Item
            name="name"
            label="数据库名称"
            rules={[{ required: true, message: '请输入数据库名称' }]}
          >
            <Input placeholder="例如: 核心交易库_主节点" />
          </Form.Item>

          <Form.Item
            name="db_type"
            label="数据库类型"
            rules={[{ required: true, message: '请选择数据库类型' }]}
          >
            <Select placeholder="请选择数据库类型" onChange={handleDbTypeChange}>
              {Object.entries(DB_TYPE_MAP).map(([key, name]) => (
                <Select.Option key={key} value={key}>
                  {name}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="host"
                label="主机地址"
                rules={[{ required: true, message: '请输入主机地址' }]}
              >
                <Input placeholder="例如: 192.168.1.100 或 localhost" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="port"
                label="端口"
                rules={[{ required: true, message: '请输入端口' }]}
              >
                <InputNumber style={{ width: '100%' }} min={1} max={65535} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入数据库用户名" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入数据库密码" />
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prevValues, currentValues) => prevValues.db_type !== currentValues.db_type}
          >
            {({ getFieldValue }) => {
              const currentDbType = getFieldValue('db_type') || 'oracle'
              const templates = getAvailableTemplates(currentDbType)
              return (
                <Form.Item
                  name="template_name"
                  label="配置模板 (推荐)"
                  extra="根据数据库类型自动匹配专属生产指标采集与默认参数"
                >
                  <Select
                    placeholder="请选择配置模板"
                    allowClear
                    onChange={handleTemplateChange}
                  >
                    {templates.map(t => (
                      <Select.Option key={t.value} value={t.value}>
                        {t.label}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>
              )
            }}
          </Form.Item>

          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="service_name"
                label="服务名/数据库名"
                extra="Oracle: 服务名(SID), MySQL/PG: 数据库名"
              >
                <Input placeholder="Oracle必填，其他可留空" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="collect_interval_sec"
                label="采集周期(秒)"
                rules={[{ required: true, message: '请输入采集周期' }]}
                extra="支持 10s-3600s"
              >
                <InputNumber style={{ width: '100%' }} min={5} max={3600} placeholder="60" />
              </Form.Item>
            </Col>
          </Row>

          {/* 测试连接结果显示 */}
          {testResult && (
            <Form.Item style={{ marginBottom: 16 }}>
              <Alert
                type={testResult.success ? 'success' : 'error'}
                showIcon
                message={
                  <Space direction="vertical" size={2}>
                    <Text strong>{testResult.success ? '✅ 连接成功' : '❌ 连接失败'}</Text>
                    <Text>{testResult.message}</Text>
                    {testResult.version && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        版本: {testResult.version}
                      </Text>
                    )}
                  </Space>
                }
              />
            </Form.Item>
          )}

          <Form.Item style={{ marginBottom: 0 }}>
            <Row gutter={8} justify="end">
              <Col>
                <Button
                  icon={<SyncOutlined spin={testLoading} />}
                  onClick={handleTestConnection}
                  loading={testLoading}
                  disabled={addModalLoading}
                >
                  测试连接
                </Button>
              </Col>
              <Col>
                <Space>
                  <Button onClick={() => { setAddModalVisible(false); setTestResult(null) }}>取消</Button>
                  <Button type="primary" htmlType="submit" loading={addModalLoading}>
                    添加
                  </Button>
                </Space>
              </Col>
            </Row>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑数据库弹窗 */}
      <Modal
        title={`编辑数据库 - ${editingDb?.name || ''}`}
        open={editModalVisible}
        onCancel={() => { setEditModalVisible(false); setEditTestResult(null); setEditingDb(null) }}
        footer={null}
        width={500}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={handleEditDatabase}
          initialValues={{
            port: 3306
          }}
        >
          <Form.Item
            name="name"
            label="数据库名称"
            rules={[{ required: true, message: '请输入数据库名称' }]}
          >
            <Input placeholder="例如: 核心交易库_主节点" />
          </Form.Item>

          <Form.Item
            name="db_type"
            label="数据库类型"
            rules={[{ required: true, message: '请选择数据库类型' }]}
          >
            <Select placeholder="请选择数据库类型" onChange={handleEditDbTypeChange}>
              {Object.entries(DB_TYPE_MAP).map(([key, name]) => (
                <Select.Option key={key} value={key}>
                  {name}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="host"
                label="主机地址"
                rules={[{ required: true, message: '请输入主机地址' }]}
              >
                <Input placeholder="例如: 192.168.1.100 或 localhost" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="port"
                label="端口"
                rules={[{ required: true, message: '请输入端口' }]}
              >
                <InputNumber style={{ width: '100%' }} min={1} max={65535} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入数据库用户名" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            extra="留空则不修改密码"
          >
            <Input.Password placeholder="留空则不修改密码" />
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prevValues, currentValues) => prevValues.db_type !== currentValues.db_type}
          >
            {({ getFieldValue }) => {
              const currentDbType = getFieldValue('db_type') || 'oracle'
              const templates = getAvailableTemplates(currentDbType)
              return (
                <Form.Item
                  name="template_name"
                  label="配置模板 (推荐)"
                  extra="根据数据库类型自动匹配专属生产指标采集与默认参数"
                >
                  <Select
                    placeholder="请选择配置模板"
                    allowClear
                    onChange={handleEditTemplateChange}
                  >
                    {templates.map(t => (
                      <Select.Option key={t.value} value={t.value}>
                        {t.label}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>
              )
            }}
          </Form.Item>

          <Row gutter={16}>
            <Col span={16}>
              <Form.Item
                name="service_name"
                label="服务名/数据库名"
                extra="Oracle: 服务名(SID), MySQL/PG: 数据库名"
              >
                <Input placeholder="Oracle必填，其他可留空" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="collect_interval_sec"
                label="采集周期(秒)"
                rules={[{ required: true, message: '请输入采集周期' }]}
                extra="支持 10s-3600s"
              >
                <InputNumber style={{ width: '100%' }} min={5} max={3600} placeholder="60" />
              </Form.Item>
            </Col>
          </Row>

          {/* 测试连接结果显示 */}
          {editTestResult && (
            <Form.Item style={{ marginBottom: 16 }}>
              <Alert
                type={editTestResult.success ? 'success' : 'error'}
                showIcon
                message={
                  <Space direction="vertical" size={2}>
                    <Text strong>{editTestResult.success ? '✅ 连接成功' : '❌ 连接失败'}</Text>
                    <Text>{editTestResult.message}</Text>
                    {editTestResult.version && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        版本: {editTestResult.version}
                      </Text>
                    )}
                  </Space>
                }
              />
            </Form.Item>
          )}

          <Form.Item style={{ marginBottom: 0 }}>
            <Row gutter={8} justify="end">
              <Col>
                <Button
                  icon={<SyncOutlined spin={editTestLoading} />}
                  onClick={handleEditTestConnection}
                  loading={editTestLoading}
                  disabled={editModalLoading}
                >
                  测试连接
                </Button>
              </Col>
              <Col>
                <Space>
                  <Button onClick={() => { setEditModalVisible(false); setEditTestResult(null); setEditingDb(null) }}>取消</Button>
                  <Button type="primary" htmlType="submit" loading={editModalLoading}>
                    保存修改
                  </Button>
                </Space>
              </Col>
            </Row>
          </Form.Item>
        </Form>
      </Modal>

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

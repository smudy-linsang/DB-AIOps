import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Card, Row, Col, Statistic, Typography, Space, Spin, Alert, Table, Tag, Select, Empty, Button, Collapse, Badge, Tooltip } from 'antd'
import {
  DatabaseOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  RiseOutlined,
  FallOutlined,
  ReloadOutlined,
  SyncOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  BarChartOutlined,
  DesktopOutlined,
  SettingOutlined,
  ClockCircleOutlined
} from '@ant-design/icons'
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, Legend,
  PieChart, Pie, Cell, BarChart, Bar
} from 'recharts'
import { healthAPI, databaseAPI, alertAPI } from '../services/api'
import AlertPanel from '../components/AlertPanel'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const { Title, Text, Paragraph } = Typography
const { Panel } = Collapse

// 数据库类型映射 (必须与后端 monitor/models.py 中的 DB_TYPES 一致)
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

// 状态颜色映射
const STATUS_COLORS = {
  'UP': 'green',
  'DOWN': 'red',
  'UNKNOWN': 'default'
}

// 告警级别颜色
const ALERT_SEVERITY_COLORS = {
  'critical': '#ff4d4f',
  'warning': '#faad14',
  'info': '#1890ff'
}

// 告警级别图标
const ALERT_SEVERITY_ICONS = {
  'critical': <CloseCircleOutlined />,
  'warning': <WarningOutlined />,
  'info': <InfoCircleOutlined />
}

// MySQL 关键指标分类
const MYSQL_METRIC_CATEGORIES = {
  '连接类': ['threads_connected', 'max_used_connections', 'aborted_connects', 'connection_errors'],
  'QPS/TPS': ['questions', 'queries', 'transactions', 'qps', 'tps'],
  '缓冲池': ['innodb_buffer_pool_size', 'innodb_buffer_pool_pages_total', 'innodb_buffer_pool_pages_free', 'innodb_buffer_pool_pages_dirty', 'innodb_buffer_pool_reads', 'innodb_buffer_pool_read_requests'],
  '表缓存': ['open_files', 'opened_files', 'table_open_cache_hits', 'table_open_cache_misses'],
  '查询缓存': ['qcache_hits', 'qcache_inserts', 'qcache_not_cached', 'qcache_queries_in_cache'],
  '临时对象': ['created_tmp_files', 'created_tmp_tables', 'created_tmp_disk_tables'],
  '锁等待': ['table_locks_waited', 'table_locks_immediate', 'innodb_row_locks_waits', 'innodb_row_lock_time'],
  'InnoDB': ['innodb_data_reads', 'innodb_data_writes', 'innodb_log_writes', 'innodb_buffer_pool_hit_ratio'],
  '线程': ['threads_running', 'threads_waits', 'thread_cache_hit_ratio'],
  '复制': ['slave_io_running', 'slave_sql_running', 'seconds_behind_master', 'relay_log_space']
}

// PostgreSQL 关键指标分类
const POSTGRESQL_METRIC_CATEGORIES = {
  '连接类': ['num_backends', 'max_connections', 'total_connections', 'active_connections', 'idle_connections', 'blocked_connections'],
  '事务/语句': ['xact_commit', 'xact_rollback', 'blks_read', 'blks_hit', 'tup_returned', 'tup_fetched', 'tup_inserted', 'tup_updated', 'tup_deleted'],
  '缓冲池': ['shared_buffers', 'effective_cache_size', 'buff_cache_hit_ratio', 'heap_blks_read', 'heap_blks_hit'],
  'WAL': ['wal_files', 'wal_size', 'wal_write', 'wal_sync'],
  '复制': ['replication_lag', 'replication_slots', 'wal_receiver_status'],
  '会话': ['stat_session_time', 'stat_session_cpu', 'stat_session_io'],
  '表空间': ['tablespace_size', 'tablespace_used', 'tablespace_percent'],
  '事务ID': ['oldest_xmin', 'xid_age', 'transaction_id_wraparound_warning'],
  '慢查询': ['slow_queries', 'log_min_duration']
}

// Oracle 关键指标分类
const ORACLE_METRIC_CATEGORIES = {
  '连接类': ['session_count', 'active_sessions', 'inactive_sessions', 'system_sessions', 'background_sessions'],
  '性能类': ['db_cpu_time', 'db_time', 'buffer_gets', 'disk_reads', 'executions', 'parse_count_total', 'parse_count_hard'],
  'SGA/PGA': ['sga_size', 'sga_free', 'pga_allocated', 'pga_used', 'pga_max_size'],
  '缓冲池': ['buffer_cache_size', 'buffer_busy_waits', 'db_block_gets', 'db_block_changes', 'consistent_gets'],
  '日志/归档': ['redo_writes', 'redo_size', 'archiver_status', 'archive_gap'],
  '锁等待': ['enqueue_waits', 'latch_misses', 'lock_requests', 'row_lock_waits'],
  '表空间': ['tablespace_used', 'tablespace_size', 'tablespace_percent', 'datafile_count'],
  '会话内存': ['session_memory_used', 'session_pga_memory', 'process_memory'],
  'RAC': ['cluster_interconnect', 'gc_buffer_busy', 'gc_cr_blocks_received', 'gc_current_blocks_received'],
  'ADG': ['transport_lag', 'apply_lag', 'apply_rate', 'standby_file_resync']
}

// DM8 关键指标分类
const DM8_METRIC_CATEGORIES = {
  '连接类': ['session_count', 'active_sessions', 'max_sessions', 'session_memory'],
  '事务类': ['trx_commit', 'trx_rollback', 'trx_active', 'trx_cur'],
  '缓冲池': ['buffer_pool_size', 'buffer_pool_hit_ratio', 'buf_page_read', 'buf_page_written'],
  'SQL统计': ['select_count', 'insert_count', 'update_count', 'delete_count', 'sql_execute'],
  '会话内存': ['mem_total', 'mem_used', 'sql_pool_size', 'dict_cache_size'],
  '线程': ['thread_count', 'worker_thread_count', 'scheduler_count'],
  '锁等待': ['lock_waits', 'lock_timeout', 'deadlock_count'],
  '归档/WAL': ['archive_size', 'archivelog_count', 'wal_write_count', 'wal_sync_count']
}

// 获取数据库类型对应的指标分类
const getMetricCategories = (dbType) => {
  const type = dbType?.toLowerCase()
  switch (type) {
    case 'mysql': return MYSQL_METRIC_CATEGORIES
    case 'pgsql': return POSTGRESQL_METRIC_CATEGORIES
    case 'oracle': return ORACLE_METRIC_CATEGORIES
    case 'dm': return DM8_METRIC_CATEGORIES
    default: return {}
  }
}

// 格式化数值
const formatValue = (value, metric) => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') {
    // 如果是百分比
    if (metric.includes('ratio') || metric.includes('percent') || metric.includes('pct')) {
      return `${value.toFixed(2)}%`
    }
    // 如果是大数值
    if (value > 1000000) {
      return `${(value / 1000000).toFixed(2)}M`
    }
    if (value > 1000) {
      return `${(value / 1000).toFixed(2)}K`
    }
    // 如果是字节
    if (metric.includes('size') || metric.includes('memory') || metric.includes('bytes')) {
      if (value > 1073741824) return `${(value / 1073741824).toFixed(2)}GB`
      if (value > 1048576) return `${(value / 1048576).toFixed(2)}MB`
      if (value > 1024) return `${(value / 1024).toFixed(2)}KB`
    }
    return value.toFixed(2)
  }
  return String(value)
}

// 过滤有用的指标
const filterUsefulMetrics = (metrics, dbType) => {
  const categories = getMetricCategories(dbType)
  const allMetricNames = Object.values(categories).flat()
  const result = {}
  
  // 只保留已知有用的指标
  for (const key of allMetricNames) {
    if (metrics[key] !== undefined && metrics[key] !== null) {
      result[key] = metrics[key]
    }
  }
  
  // 如果没有匹配到，尝试添加所有数值型指标
  if (Object.keys(result).length === 0) {
    for (const [key, value] of Object.entries(metrics)) {
      if (typeof value === 'number' && !isNaN(value) && isFinite(value)) {
        result[key] = value
      }
    }
  }
  
  return result
}

const Dashboard = () => {
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [healthData, setHealthData] = useState(null)
  const [dbList, setDbList] = useState([])
  const [dbStatuses, setDbStatuses] = useState({}) // 存储每个数据库的详细状态
  const [alerts, setAlerts] = useState([])
  const [dbTypeFilter, setDbTypeFilter] = useState(null)
  const [realtimeMetrics, setRealtimeMetrics] = useState({
    totalConnections: 0,
    totalQPS: 0,
    totalTPS: 0,
    activeAlerts: 0
  })
  const [trendData, setTrendData] = useState([])
  const [expandedDb, setExpandedDb] = useState([]) // 展开的数据库面板

  const fetchDashboardData = async () => {
    setRefreshing(true)
    try {
      // 并行获取数据
      const [health, dbListRes, alertsRes] = await Promise.all([
        healthAPI.check().catch(() => null),
        databaseAPI.list().catch(() => ({ databases: [] })),
        alertAPI.list({ limit: 100 }).catch(() => ({ alerts: [] }))
      ])

      // 处理健康检查数据
      if (health) {
        setHealthData(health)
      }

      // 处理数据库列表
      const databases = dbListRes?.databases || []
      setDbList(databases.map(db => ({
        ...db,
        key: db.id,
        // 使用后端返回的原始 status，不要用 is_active 覆盖
        last_collect: db.last_collect_time || db.last_seen
      })))

      // 处理告警列表
      const alertList = alertsRes?.alerts || []
      setAlerts(alertList.slice(0, 5))

      // 生成趋势数据（基于当前时间模拟）
      const now = dayjs()
      const newTrendData = Array.from({ length: 24 }, (_, i) => ({
        time: now.subtract(23 - i, 'hour').format('HH:mm'),
        cpu: 30 + Math.random() * 40,
        memory: 40 + Math.random() * 30,
        connections: Math.floor(100 + Math.random() * 200),
        alerts: Math.floor(Math.random() * 5)
      }))
      setTrendData(newTrendData)

    } catch (error) {
      console.error('获取仪表盘数据失败:', error)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  // 获取所有数据库的详细状态
  const fetchAllDbStatuses = async () => {
    try {
      const statusPromises = dbList.map(async (db) => {
        try {
          const data = await databaseAPI.getStatus(db.id)
          return { id: db.id, data: data }
        } catch (err) {
          console.error(`获取数据库 ${db.id} 状态失败:`, err)
          return { id: db.id, data: null }
        }
      })
      
      const results = await Promise.all(statusPromises)
      const statusesMap = {}
      let totalConnections = 0
      let totalQPS = 0
      let totalTPS = 0
      
      results.forEach(({ id, data }) => {
        if (data && data.metrics) {
          statusesMap[id] = data
          // 累加连接数
          const connMetric = data.metrics.threads_connected || 
                            data.metrics.session_count || 
                            data.metrics.num_backends ||
                            data.metrics.active_sessions ||
                            0
          totalConnections += Number(connMetric) || 0
          
          // 累加 QPS
          const qpsMetric = data.metrics.qps || 
                            data.metrics.queries_per_second ||
                            data.metrics.select_count ||
                            0
          totalQPS += Number(qpsMetric) || 0
          
          // 累加 TPS
          const tpsMetric = data.metrics.tps || 
                            data.metrics.transactions ||
                            data.metrics.xact_commit ||
                            data.metrics.trx_commit ||
                            0
          totalTPS += Number(tpsMetric) || 0
        }
      })
      
      setDbStatuses(statusesMap)
      
      // 更新汇总指标
      if (totalConnections > 0 || totalQPS > 0 || totalTPS > 0) {
        setRealtimeMetrics(prev => ({
          ...prev,
          totalConnections,
          totalQPS,
          totalTPS
        }))
      }
    } catch (error) {
      console.error('批量获取数据库状态失败:', error)
    }
  }

  useEffect(() => {
    fetchDashboardData()
  }, [])

  // 当数据库列表加载完成后，获取每个数据库的详细状态
  useEffect(() => {
    if (dbList.length > 0 && Object.keys(dbStatuses).length === 0) {
      fetchAllDbStatuses()
    }
  }, [dbList])

  // 定时刷新 - 每30秒
  useEffect(() => {
    const interval = setInterval(() => {
      fetchDashboardData()
      if (dbList.length > 0) {
        fetchAllDbStatuses()
      }
    }, 30000)
    return () => clearInterval(interval)
  }, [dbList.length])

  // 按数据库类型统计
  const getDbStatsByType = () => {
    const stats = {}
    Object.keys(DB_TYPE_MAP).forEach(type => {
      stats[type] = { total: 0, online: 0, offline: 0 }
    })
    
    dbList.forEach(db => {
      const type = db.db_type?.toLowerCase() || 'unknown'
      if (stats[type]) {
        stats[type].total++
        if (db.status === 'UP') {
          stats[type].online++
        } else {
          stats[type].offline++
        }
      }
    })
    
    return stats
  }

  const dbStatsByType = getDbStatsByType()

  // 数据库类型分布数据（用于饼图）
  const getDbTypeDistribution = () => {
    return Object.entries(DB_TYPE_MAP)
      .map(([type, name]) => ({
        name,
        value: dbStatsByType[type]?.total || 0,
        type
      }))
      .filter(item => item.value > 0)
  }

  // 获取数据库类型统计信息
  const getDbTypeSummary = () => {
    const summary = {}
    dbList.forEach(db => {
      const type = db.db_type?.toLowerCase()
      if (!summary[type]) {
        summary[type] = { count: 0, metricsCount: 0 }
      }
      summary[type].count++
      if (dbStatuses[db.id]?.metrics) {
        const metrics = dbStatuses[db.id].metrics
        summary[type].metricsCount += Object.keys(metrics).length
      }
    })
    return summary
  }

  const dbTypeSummary = getDbTypeSummary()

  // 表格列定义
  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
      width: 150
    },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => DB_TYPE_MAP[type?.toLowerCase()] || type
    },
    {
      title: '主机:端口',
      key: 'host_port',
      width: 180,
      render: (_, record) => `${record.host || record.ip_address || '-'}:${record.port || '-'}`
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status) => (
        <Tag color={STATUS_COLORS[status] || 'default'}>
          {status || 'UNKNOWN'}
        </Tag>
      )
    },
    {
      title: '最后采集',
      dataIndex: 'last_collect',
      key: 'last_collect',
      width: 150,
      render: (time) => time ? dayjs(time).fromNow() : '-'
    },
    {
      title: '连接数',
      dataIndex: 'connections',
      key: 'connections',
      width: 100,
      render: (val) => val ?? '-'
    },
    {
      title: 'QPS',
      dataIndex: 'qps',
      key: 'qps',
      width: 100,
      render: (val) => val?.toFixed(2) ?? '-'
    },
    {
      title: '指标数',
      key: 'metrics_count',
      width: 80,
      render: (_, record) => {
        const metrics = dbStatuses[record.id]?.metrics || {}
        const count = Object.keys(metrics).length
        return <Badge count={count} showZero color="#1890ff" />
      }
    }
  ]

  // 渲染数据库详细指标
  const renderDbMetrics = (db) => {
    const status = dbStatuses[db.id]
    if (!status || !status.metrics) {
      return <Empty description="暂无指标数据" />
    }
    
    const metrics = status.metrics
    const categories = getMetricCategories(db.db_type)
    const filteredMetrics = filterUsefulMetrics(metrics, db.db_type)
    
    return (
      <div className="db-metrics-detail">
        <Row gutter={[16, 16]}>
          {/* 显示每个分类的指标 */}
          {Object.entries(categories).map(([categoryName, metricNames]) => {
            const categoryMetrics = metricNames
              .filter(name => filteredMetrics[name] !== undefined)
              .map(name => ({ name, value: filteredMetrics[name] }))
            
            if (categoryMetrics.length === 0) return null
            
            return (
              <Col xs={24} sm={12} md={8} lg={6} key={categoryName}>
                <Card 
                  size="small" 
                  title={
                    <span>
                      <BarChartOutlined style={{ marginRight: 8 }} />
                      {categoryName}
                    </span>
                  }
                  className="metric-category-card"
                >
                  {categoryMetrics.map(({ name, value }) => (
                    <div key={name} className="metric-item">
                      <span className="metric-name" title={name}>
                        {name.replace(/_/g, ' ')}
                      </span>
                      <span className="metric-value">
                        {formatValue(value, name)}
                      </span>
                    </div>
                  ))}
                </Card>
              </Col>
            )
          })}
          
          {/* 显示其他未分类但有值的指标 */}
          {(() => {
            const categorizedNames = Object.values(categories).flat()
            const otherMetrics = Object.entries(filteredMetrics)
              .filter(([name]) => !categorizedNames.includes(name))
              .slice(0, 20) // 限制显示数量
            
            if (otherMetrics.length === 0) return null
            
            return (
              <Col xs={24} sm={12} md={8} lg={6}>
                <Card 
                  size="small" 
                  title={
                    <span>
                      <InfoCircleOutlined style={{ marginRight: 8 }} />
                      其他指标
                    </span>
                  }
                  className="metric-category-card"
                >
                  {otherMetrics.map(([name, value]) => (
                    <div key={name} className="metric-item">
                      <span className="metric-name" title={name}>
                        {name.replace(/_/g, ' ')}
                      </span>
                      <span className="metric-value">
                        {formatValue(value, name)}
                      </span>
                    </div>
                  ))}
                </Card>
              </Col>
            )
          })()}
        </Row>
        
        {/* 原始数据概览 */}
        <Card 
          size="small" 
          title={
            <span>
              <DesktopOutlined style={{ marginRight: 8 }} />
              原始指标数据 (共 {Object.keys(metrics).length} 项)
            </span>
          }
          style={{ marginTop: 16 }}
        >
          <div className="raw-metrics-scroll">
            {Object.entries(metrics).map(([key, value]) => (
              <Tag key={key} className="metric-tag">
                <span className="metric-tag-key">{key}:</span>
                <span className="metric-tag-value">{formatValue(value, key)}</span>
              </Tag>
            ))}
          </div>
        </Card>
      </div>
    )
  }

  // 筛选后的数据库列表
  const filteredDbList = dbTypeFilter 
    ? dbList.filter(db => db.db_type?.toLowerCase() === dbTypeFilter)
    : dbList

  if (loading && !healthData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
        <Spin size="large" tip="加载监控大屏数据..." />
      </div>
    )
  }

  return (
    <div className="dashboard-container">
      <style>{`
        .dashboard-container {
          padding: 24px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          min-height: 100vh;
        }
        .dashboard-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 24px;
        }
        .dashboard-title {
          color: #fff;
          font-size: 28px;
          font-weight: 600;
          margin: 0;
          text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .dashboard-subtitle {
          color: rgba(255,255,255,0.85);
          font-size: 14px;
          margin-top: 4px;
        }
        .refresh-time {
          color: rgba(255,255,255,0.7);
          font-size: 12px;
          margin-top: 8px;
        }
        .stat-card {
          background: rgba(255,255,255,0.95);
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.1);
          transition: transform 0.3s, box-shadow 0.3s;
          height: 100%;
        }
        .stat-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 6px 24px rgba(0,0,0,0.15);
        }
        .stat-card-title {
          font-size: 14px;
          color: #666;
          margin-bottom: 8px;
        }
        .stat-card-value {
          font-size: 32px;
          font-weight: 700;
        }
        .stat-trend {
          font-size: 12px;
          margin-left: 8px;
        }
        .db-type-card {
          background: rgba(255,255,255,0.95);
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.1);
          padding: 16px;
          height: 100%;
        }
        .db-type-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }
        .db-type-name {
          font-size: 16px;
          font-weight: 600;
          color: #333;
        }
        .db-type-count {
          font-size: 24px;
          font-weight: 700;
          color: #1890ff;
        }
        .db-type-stats {
          display: flex;
          gap: 16px;
          font-size: 13px;
        }
        .db-type-stat-online {
          color: #52c41a;
        }
        .db-type-stat-offline {
          color: #ff4d4f;
        }
        .db-type-metrics-info {
          font-size: 12px;
          color: #999;
          margin-top: 8px;
        }
        .alert-item {
          background: rgba(255,255,255,0.95);
          border-radius: 8px;
          padding: 12px 16px;
          margin-bottom: 8px;
          display: flex;
          align-items: center;
          gap: 12px;
          transition: background 0.2s;
        }
        .alert-item:hover {
          background: #fff;
        }
        .alert-icon-wrap {
          width: 36px;
          height: 36px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .alert-content {
          flex: 1;
          min-width: 0;
        }
        .alert-title {
          font-weight: 500;
          color: #333;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .alert-meta {
          font-size: 12px;
          color: #999;
          margin-top: 2px;
        }
        .chart-card {
          background: rgba(255,255,255,0.95);
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        .table-card {
          background: rgba(255,255,255,0.95);
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        .filter-select {
          width: 160px;
        }
        .db-detail-collapse {
          background: rgba(255,255,255,0.95);
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.1);
          margin-bottom: 16px;
          overflow: hidden;
        }
        .db-detail-header {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 8px 0;
        }
        .db-detail-title {
          font-size: 16px;
          font-weight: 600;
        }
        .db-detail-meta {
          font-size: 12px;
          color: #999;
        }
        .metric-category-card {
          border-radius: 8px;
          height: 100%;
        }
        .metric-category-card .ant-card-head {
          min-height: 40px;
          padding: 0 12px;
        }
        .metric-category-card .ant-card-head-title {
          font-size: 13px;
          font-weight: 600;
        }
        .metric-item {
          display: flex;
          justify-content: space-between;
          padding: 4px 0;
          border-bottom: 1px solid #f0f0f0;
        }
        .metric-item:last-child {
          border-bottom: none;
        }
        .metric-name {
          font-size: 12px;
          color: #666;
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .metric-value {
          font-size: 12px;
          font-weight: 600;
          color: #1890ff;
          margin-left: 8px;
        }
        .raw-metrics-scroll {
          max-height: 200px;
          overflow-y: auto;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .metric-tag {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 4px 8px;
          background: #f5f5f5;
          border-radius: 4px;
          font-size: 11px;
        }
        .metric-tag-key {
          color: #666;
        }
        .metric-tag-value {
          color: #1890ff;
          font-weight: 600;
        }
        .metrics-summary-row {
          display: flex;
          gap: 24px;
          flex-wrap: wrap;
        }
        .metrics-summary-item {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .metrics-summary-label {
          color: #999;
          font-size: 12px;
        }
        .metrics-summary-value {
          color: #1890ff;
          font-size: 14px;
          font-weight: 600;
        }
      `}</style>

      {/* 头部 */}
      <div className="dashboard-header">
        <div>
          <h1 className="dashboard-title">数据库监控大屏</h1>
          <p className="dashboard-subtitle">实时监控数据库运行状态与性能指标</p>
          <p className="refresh-time">最后更新: {dayjs().format('YYYY-MM-DD HH:mm:ss')}</p>
        </div>
        <Button 
          type="primary" 
          icon={<SyncOutlined spin={refreshing} />} 
          onClick={() => {
            fetchDashboardData()
            fetchAllDbStatuses()
          }}
          loading={refreshing}
          size="large"
        >
          刷新数据
        </Button>
      </div>

      {/* 系统状态提示 */}
      {healthData && (
        <Alert
          message={`系统状态: ${healthData.status === 'healthy' ? '正常' : '异常'}`}
          description={`活跃数据库: ${healthData.metrics?.active_databases || 0}, 活跃告警: ${healthData.metrics?.active_alerts || 0}`}
          type={healthData.status === 'healthy' ? 'success' : 'error'}
          showIcon
          style={{ marginBottom: 24, borderRadius: 8 }}
        />
      )}

      {/* 实时指标面板 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" size="small">
            <Statistic
              title={<span className="stat-card-title">总连接数</span>}
              value={realtimeMetrics.totalConnections}
              prefix={<DatabaseOutlined style={{ color: '#1890ff' }} />}
              valueStyle={{ color: '#1890ff' }}
              suffix={<span className="stat-trend"><RiseOutlined style={{ color: '#52c41a' }} /></span>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" size="small">
            <Statistic
              title={<span className="stat-card-title">总 QPS</span>}
              value={realtimeMetrics.totalQPS.toFixed(1)}
              prefix={<RiseOutlined style={{ color: '#722ed1' }} />}
              valueStyle={{ color: '#722ed1' }}
              suffix={<span className="stat-trend"><RiseOutlined style={{ color: '#52c41a' }} /></span>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" size="small">
            <Statistic
              title={<span className="stat-card-title">总 TPS</span>}
              value={realtimeMetrics.totalTPS.toFixed(1)}
              prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />}
              valueStyle={{ color: '#52c41a' }}
              suffix={<span className="stat-trend"><FallOutlined style={{ color: '#faad14' }} /></span>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" size="small">
            <Statistic
              title={<span className="stat-card-title">活跃告警</span>}
              value={realtimeMetrics.activeAlerts}
              prefix={<ExclamationCircleOutlined style={{ color: realtimeMetrics.activeAlerts > 0 ? '#ff4d4f' : '#52c41a' }} />}
              valueStyle={{ color: realtimeMetrics.activeAlerts > 0 ? '#ff4d4f' : '#52c41a' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 数据库类型卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {Object.entries(DB_TYPE_MAP).map(([type, name]) => {
          const stats = dbStatsByType[type] || { total: 0, online: 0, offline: 0 }
          const summary = dbTypeSummary[type] || { count: 0, metricsCount: 0 }
          return (
            <Col xs={12} sm={8} lg={4} key={type}>
              <div className="db-type-card">
                <div className="db-type-header">
                  <span className="db-type-name">{name}</span>
                </div>
                <div className="db-type-count">{stats.total}</div>
                <div className="db-type-stats">
                  <span className="db-type-stat-online">
                    <CheckCircleOutlined /> {stats.online} 在线
                  </span>
                  <span className="db-type-stat-offline">
                    <CloseCircleOutlined /> {stats.offline} 离线
                  </span>
                </div>
                {stats.total > 0 && (
                  <div className="db-type-metrics-info">
                    <InfoCircleOutlined /> 已采集 {summary.metricsCount} 个指标
                  </div>
                )}
              </div>
            </Col>
          )
        })}
      </Row>

      {/* 图表区域 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* 告警概览 */}
        <Col xs={24} lg={8}>
          <Card 
            title="告警概览" 
            className="chart-card"
            extra={<Link to="/alerts"><Button type="link" size="small">查看全部</Button></Link>}
          >
            <AlertPanel 
              limit={5}
              showActions={true}
              onRefresh={fetchDashboardData}
            />
          </Card>
        </Col>

        {/* 数据库类型分布 */}
        <Col xs={24} lg={8}>
          <Card title="数据库类型分布" className="chart-card">
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={getDbTypeDistribution()}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={2}
                  dataKey="value"
                  label={({ name, value }) => `${name}: ${value}`}
                >
                  {getDbTypeDistribution().map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={DB_TYPE_COLORS[entry.type] || '#999'} />
                  ))}
                </Pie>
                <RechartsTooltip 
                  formatter={(value, name) => [`${value} 个`, name]}
                />
              </PieChart>
            </ResponsiveContainer>
          </Card>
        </Col>

        {/* 趋势图表 */}
        <Col xs={24} lg={8}>
          <Card title="连接数趋势" className="chart-card">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <RechartsTooltip />
                <Line
                  type="monotone"
                  dataKey="connections"
                  name="连接数"
                  stroke="#1890ff"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* 数据库详情展开面板 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col span={24}>
          <Card 
            title={
              <Space>
                <BarChartOutlined />
                <span>数据库详细指标</span>
                <Badge count={dbList.length} style={{ marginLeft: 8 }} />
              </Space>
            }
            className="chart-card"
            extra={
              <Text type="secondary">
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                展开查看每个数据库的详细采集指标
              </Text>
            }
          >
            <Collapse 
              accordion={false}
              activeKey={expandedDb}
              onChange={setExpandedDb}
            >
              {dbList.map((db) => {
                const status = dbStatuses[db.id]
                const metricsCount = status?.metrics ? Object.keys(status.metrics).length : 0
                return (
                  <Panel
                    key={db.id}
                    header={
                      <div className="db-detail-header">
                        <Tag color={DB_TYPE_COLORS[db.db_type?.toLowerCase()] || '#999'}>
                          {DB_TYPE_MAP[db.db_type?.toLowerCase()] || db.db_type}
                        </Tag>
                        <span className="db-detail-title">{db.name}</span>
                        <Tag color={db.is_active ? 'green' : 'red'}>
                          {db.is_active ? 'UP' : 'DOWN'}
                        </Tag>
                        <span className="db-detail-meta">
                          {db.host || db.ip_address}:{db.port} | 
                          <ClockCircleOutlined style={{ margin: '0 4px' }} />
                          {db.last_collect_time ? dayjs(db.last_collect_time).fromNow() : '暂无数据'}
                        </span>
                        <Badge count={metricsCount} showZero color="#1890ff" style={{ marginLeft: 8 }} />
                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                          个指标
                        </Text>
                      </div>
                    }
                  >
                    <div className="metrics-summary-row" style={{ marginBottom: 16 }}>
                      <div className="metrics-summary-item">
                        <SettingOutlined />
                        <span className="metrics-summary-label">采集状态:</span>
                        <span className="metrics-summary-value">{status?.status || 'N/A'}</span>
                      </div>
                      <div className="metrics-summary-item">
                        <DesktopOutlined />
                        <span className="metrics-summary-label">指标总数:</span>
                        <span className="metrics-summary-value">{metricsCount}</span>
                      </div>
                      <div className="metrics-summary-item">
                        <ClockCircleOutlined />
                        <span className="metrics-summary-label">采集时间:</span>
                        <span className="metrics-summary-value">
                          {status?.collected_at ? dayjs(status.collected_at).format('YYYY-MM-DD HH:mm:ss') : 'N/A'}
                        </span>
                      </div>
                    </div>
                    {renderDbMetrics(db)}
                  </Panel>
                )
              })}
            </Collapse>
          </Card>
        </Col>
      </Row>

      {/* 数据库状态表格 */}
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card 
            title="数据库状态列表" 
            className="table-card"
            extra={
              <Space>
                <Select
                  className="filter-select"
                  placeholder="筛选类型"
                  allowClear
                  value={dbTypeFilter}
                  onChange={setDbTypeFilter}
                  options={Object.entries(DB_TYPE_MAP).map(([type, name]) => ({
                    value: type,
                    label: name
                  }))}
                />
                <Text type="secondary">共 {filteredDbList.length} 个数据库</Text>
              </Space>
            }
          >
            <Table
              columns={columns}
              dataSource={filteredDbList}
              pagination={{ 
                pageSize: 10, 
                showSizeChanger: true,
                showQuickJumper: true,
                showTotal: (total) => `共 ${total} 条`
              }}
              size="small"
              scroll={{ x: 900 }}
              locale={{
                emptyText: <Empty description="暂无数据库数据" />
              }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default Dashboard

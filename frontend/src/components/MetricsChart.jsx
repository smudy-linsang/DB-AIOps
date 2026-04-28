import React, { useState, useEffect, useMemo } from 'react'
import { Select, Segmented, Spin, message } from 'antd'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import dayjs from 'dayjs'
import { databaseAPI } from '../services/api'

// 可选指标列表
const METRIC_OPTIONS = [
  { label: 'QPS', value: 'qps' },
  { label: 'TPS', value: 'tps' },
  { label: '活跃连接数', value: 'active_connections' },
  { label: '缓冲命中率', value: 'buffer_hit_ratio' },
  { label: 'CPU使用率', value: 'cpu_usage' },
  { label: '内存使用率', value: 'memory_usage' },
  { label: '磁盘使用率', value: 'disk_usage' },
  { label: '连接数使用率', value: 'connection_usage' },
  { label: '查询响应时间', value: 'query_response_time' },
  { label: '事务响应时间', value: 'transaction_response_time' },
]

// 时间范围选项
const TIME_RANGE_OPTIONS = [
  { label: '1小时', value: '1h', hours: 1 },
  { label: '6小时', value: '6h', hours: 6 },
  { label: '24小时', value: '24h', hours: 24 },
  { label: '7天', value: '7d', hours: 24 * 7 },
  { label: '30天', value: '30d', hours: 24 * 30 },
]

// 指标颜色映射
const METRIC_COLORS = {
  qps: '#1890ff',
  tps: '#52c41a',
  active_connections: '#faad14',
  buffer_hit_ratio: '#722ed1',
  cpu_usage: '#eb2f96',
  memory_usage: '#13c2c2',
  disk_usage: '#fa8c16',
  connection_usage: '#8c8c8c',
  query_response_time: '#3367d6',
  transaction_response_time: '#7c4dff',
}

// 默认选中的指标
const DEFAULT_METRICS = ['qps', 'tps']

/**
 * 格式化数值
 * @param {number} value - 数值
 * @param {string} metric - 指标名称
 * @returns {string} 格式化后的字符串
 */
const formatValue = (value, metric) => {
  if (value === null || value === undefined) return 'N/A'
  
  // 百分比类指标
  if (['buffer_hit_ratio', 'cpu_usage', 'memory_usage', 'disk_usage', 'connection_usage'].includes(metric)) {
    return `${value.toFixed(2)}%`
  }
  
  // 时间类指标（毫秒）
  if (['query_response_time', 'transaction_response_time'].includes(metric)) {
    return `${value.toFixed(2)} ms`
  }
  
  // 普通数值，使用千分位
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/**
 * 获取指标显示名称
 * @param {string} metric - 指标值
 * @returns {string} 显示名称
 */
const getMetricLabel = (metric) => {
  const option = METRIC_OPTIONS.find((opt) => opt.value === metric)
  return option ? option.label : metric
}

/**
 * 自定义Tooltip组件
 */
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null

  const time = label ? dayjs(label).format('YYYY-MM-DD HH:mm:ss') : ''

  return (
    <div
      style={{
        backgroundColor: '#fff',
        border: '1px solid #d9d9d9',
        borderRadius: 4,
        padding: '12px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
      }}
    >
      <p style={{ margin: '0 0 8px 0', fontWeight: 600, color: '#333' }}>
        {time}
      </p>
      {payload.map((entry, index) => (
        <p
          key={index}
          style={{
            margin: '4px 0',
            color: entry.color,
          }}
        >
          <span>{getMetricLabel(entry.dataKey)}: </span>
          <span style={{ fontWeight: 500 }}>
            {formatValue(entry.value, entry.dataKey)}
          </span>
        </p>
      ))}
    </div>
  )
}

/**
 * 趋势图表组件
 * @param {Object} props
 * @param {number} props.databaseId - 数据库ID
 * @param {number} [props.height=300] - 图表高度
 */
const MetricsChart = ({ databaseId, height = 300 }) => {
  const [timeRange, setTimeRange] = useState('24h')
  const [selectedMetrics, setSelectedMetrics] = useState(DEFAULT_METRICS)
  const [loading, setLoading] = useState(false)
  const [chartData, setChartData] = useState([])

  // 计算时间范围
  const timeRangeConfig = useMemo(() => {
    const config = TIME_RANGE_OPTIONS.find((opt) => opt.value === timeRange)
    return config || TIME_RANGE_OPTIONS[2] // 默认24小时
  }, [timeRange])

  // 获取数据
  const fetchData = async () => {
    if (!databaseId || !selectedMetrics.length) {
      setChartData([])
      return
    }

    setLoading(true)
    try {
      const end = dayjs()
      const start = end.subtract(timeRangeConfig.hours, 'hour')

      // 并行请求所有选中的指标
      const requests = selectedMetrics.map((metric) =>
        databaseAPI.getMetrics(databaseId, {
          metric,
          start: start.toISOString(),
          end: end.toISOString(),
        })
      )

      const results = await Promise.all(requests)

      // 处理返回数据，转换为图表格式
      const dataMap = new Map()

      results.forEach((result) => {
        if (result && result.metrics) {
          result.metrics.forEach((item) => {
            const timestamp = item.timestamp
            if (!dataMap.has(timestamp)) {
              dataMap.set(timestamp, { timestamp })
            }
            const dataPoint = dataMap.get(timestamp)
            dataPoint[item.metric] = item.value
          })
        }
      })

      // 转换为数组并排序
      const dataArray = Array.from(dataMap.values()).sort((a, b) =>
        dayjs(a.timestamp).diff(dayjs(b.timestamp))
      )

      setChartData(dataArray)
    } catch (error) {
      console.error('获取指标数据失败:', error)
      message.error('获取指标数据失败')
      setChartData([])
    } finally {
      setLoading(false)
    }
  }

  // 监听参数变化，重新获取数据
  useEffect(() => {
    fetchData()
  }, [databaseId, timeRange, selectedMetrics])

  // 处理时间范围变化
  const handleTimeRangeChange = (value) => {
    setTimeRange(value)
  }

  // 处理指标选择变化
  const handleMetricsChange = (value) => {
    setSelectedMetrics(value)
  }

  // 渲染图例
  const renderLegend = () => {
    return (
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: '8px 16px',
          padding: '8px 0',
        }}
      >
        {selectedMetrics.map((metric) => (
          <span
            key={metric}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 12,
            }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 12,
                height: 3,
                backgroundColor: METRIC_COLORS[metric] || '#999',
                borderRadius: 2,
              }}
            />
            {getMetricLabel(metric)}
          </span>
        ))}
      </div>
    )
  }

  return (
    <div className="metrics-chart-container">
      {/* 控制面板 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        {/* 时间范围选择 */}
        <Segmented
          value={timeRange}
          onChange={handleTimeRangeChange}
          options={TIME_RANGE_OPTIONS.map((opt) => ({
            label: opt.label,
            value: opt.value,
          }))}
        />

        {/* 指标选择 */}
        <Select
          mode="multiple"
          value={selectedMetrics}
          onChange={handleMetricsChange}
          options={METRIC_OPTIONS}
          style={{ minWidth: 280, maxWidth: 400 }}
          placeholder="选择指标"
          maxTagCount="responsive"
          allowClear
        />
      </div>

      {/* 加载状态 */}
      {loading && (
        <div
          style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: 10,
          }}
        >
          <Spin size="large" />
        </div>
      )}

      {/* 图表 */}
      <div style={{ position: 'relative', minHeight: height }}>
        <ResponsiveContainer width="100%" height={height}>
          <LineChart
            data={chartData}
            margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="timestamp"
              tickFormatter={(value) => dayjs(value).format('MM-DD HH:mm')}
              tick={{ fontSize: 11 }}
              stroke="#999"
            />
            <YAxis
              tick={{ fontSize: 11 }}
              stroke="#999"
              tickFormatter={(value) => {
                if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`
                if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
                return value
              }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend content={renderLegend} />
            {selectedMetrics.map((metric) => (
              <Line
                key={metric}
                type="monotone"
                dataKey={metric}
                stroke={METRIC_COLORS[metric] || '#999'}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>

        {/* 空状态 */}
        {!loading && chartData.length === 0 && (
          <div
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              textAlign: 'center',
              color: '#999',
            }}
          >
            <p>暂无数据</p>
            <p style={{ fontSize: 12 }}>请选择要显示的指标</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default MetricsChart

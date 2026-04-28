import { useState, useEffect, useCallback } from 'react'
import { 
  List, Tag, Button, Space, Modal, Descriptions, 
  message, Empty, Typography, Card, Spin
} from 'antd'
import { 
  CheckCircleOutlined, WarningOutlined, 
  ExclamationCircleOutlined, CloseCircleOutlined,
  EyeOutlined, ReloadOutlined, InfoCircleOutlined
} from '@ant-design/icons'
import { alertAPI, databaseAPI } from '../services/api'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const { Text, Paragraph } = Typography

// 告警级别配置
const SEVERITY_CONFIG = {
  critical: { color: '#ff4d4f', text: '严重', icon: <CloseCircleOutlined /> },
  warning: { color: '#fa8c16', text: '警告', icon: <WarningOutlined /> },
  info: { color: '#1890ff', text: '提示', icon: <InfoCircleOutlined /> }
}

// 告警状态边框颜色
const STATUS_BORDER_COLORS = {
  active: '#ff4d4f',
  acknowledged: '#fa8c16',
  resolved: '#52c41a'
}

// 告警状态文本配置
const STATUS_CONFIG = {
  active: { color: 'processing', text: '活跃' },
  acknowledged: { color: 'warning', text: '已确认' },
  resolved: { color: 'success', text: '已解决' }
}

/**
 * AlertPanel - 告警展示组件
 * 
 * @param {Object} props
 * @param {number|null} props.databaseId - 可选，筛选特定数据库
 * @param {number} props.limit - 显示数量限制，默认10
 * @param {boolean} props.showActions - 是否显示操作按钮，默认true
 * @param {Function} props.onRefresh - 刷新回调
 */
const AlertPanel = ({ 
  databaseId = null, 
  limit = 10, 
  showActions = true,
  onRefresh = null 
}) => {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [selectedAlert, setSelectedAlert] = useState(null)
  const [detailVisible, setDetailVisible] = useState(false)
  const [acknowledging, setAcknowledging] = useState(false)
  const [dbNames, setDbNames] = useState({}) // 存储数据库名称映射

  // 获取告警列表
  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    try {
      const params = { limit }
      if (databaseId) {
        params.database_id = databaseId
      }
      
      const response = await alertAPI.list(params)
      const alertList = response?.alerts || []
      setAlerts(alertList)
      setTotal(response?.total || alertList.length)

      // 获取关联数据库名称
      const uniqueDbIds = [...new Set(alertList
        .filter(a => a.config_id)
        .map(a => a.config_id))]
      
      if (uniqueDbIds.length > 0) {
        fetchDatabaseNames(uniqueDbIds)
      }
    } catch (error) {
      console.error('获取告警列表失败:', error)
      message.error('获取告警列表失败')
    } finally {
      setLoading(false)
    }
  }, [databaseId, limit])

  // 获取数据库名称映射
  const fetchDatabaseNames = async (dbIds) => {
    try {
      const response = await databaseAPI.list()
      const databases = response?.databases || []
      const nameMap = {}
      databases.forEach(db => {
        nameMap[db.id] = db.name || db.host || `DB-${db.id}`
      })
      setDbNames(prev => ({ ...prev, ...nameMap }))
    } catch (error) {
      console.error('获取数据库名称失败:', error)
    }
  }

  useEffect(() => {
    fetchAlerts()
  }, [fetchAlerts])

  // 刷新处理
  const handleRefresh = async () => {
    await fetchAlerts()
    onRefresh?.()
  }

  // 确认告警
  const handleAcknowledge = async (alertId) => {
    setAcknowledging(true)
    try {
      await alertAPI.acknowledge(alertId)
      message.success('告警已确认')
      await fetchAlerts()
      setDetailVisible(false)
      onRefresh?.()
    } catch (error) {
      console.error('确认告警失败:', error)
      message.error('确认告警失败')
    } finally {
      setAcknowledging(false)
    }
  }

  // 查看详情
  const handleViewDetail = (alert) => {
    setSelectedAlert(alert)
    setDetailVisible(true)
  }

  // 渲染告警级别标签
  const renderSeverityTag = (severity) => {
    const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.info
    return (
      <Tag color={config.color} icon={config.icon}>
        {config.text}
      </Tag>
    )
  }

  // 渲染告警状态标签
  const renderStatusTag = (status) => {
    const config = STATUS_CONFIG[status] || { color: 'default', text: status }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  // 获取级别图标
  const getSeverityIcon = (severity) => {
    return SEVERITY_CONFIG[severity]?.icon || <InfoCircleOutlined />
  }

  // List item renderer
  const renderAlertItem = (alert) => {
    const severityConfig = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info
    const borderColor = STATUS_BORDER_COLORS[alert.status] || STATUS_BORDER_COLORS.active
    
    return (
      <div
        style={{
          background: '#fff',
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 8,
          borderLeft: `3px solid ${severityConfig.color}`,
          borderTop: `1px solid ${borderColor}30`,
          borderRight: `1px solid ${borderColor}30`,
          borderBottom: `1px solid ${borderColor}30`,
          transition: 'all 0.2s'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          {/* 级别图标 */}
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: `${severityConfig.color}15`,
              color: severityConfig.color,
              flexShrink: 0
            }}
          >
            {getSeverityIcon(alert.severity)}
          </div>

          {/* 内容区域 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              {renderSeverityTag(alert.severity)}
              {renderStatusTag(alert.status)}
            </div>
            
            <div
              style={{
                fontWeight: 500,
                color: '#333',
                marginBottom: 4,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
              }}
            >
              {alert.title}
            </div>

            <Paragraph
              type="secondary"
              style={{
                fontSize: 12,
                marginBottom: 4,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
              }}
              ellipsis
            >
              {alert.description}
            </Paragraph>

            <div style={{ display: 'flex', gap: 16, fontSize: 12, color: '#999' }}>
              {alert.config_id && dbNames[alert.config_id] && (
                <span>
                  <Text type="secondary">数据库:</Text> {dbNames[alert.config_id]}
                </span>
              )}
              <span>
                <Text type="secondary">时间:</Text> {dayjs(alert.created_at).fromNow()}
              </span>
              {alert.current_value !== undefined && alert.current_value !== null && (
                <span>
                  <Text type="secondary">当前值:</Text> {alert.current_value.toFixed(1)}%
                </span>
              )}
            </div>
          </div>

          {/* 操作按钮 */}
          {showActions && (
            <Space size="small">
              <Button
                type="text"
                size="small"
                icon={<EyeOutlined />}
                onClick={() => handleViewDetail(alert)}
              />
              {alert.status === 'active' && (
                <Button
                  type="text"
                  size="small"
                  icon={<CheckCircleOutlined />}
                  onClick={() => handleAcknowledge(alert.id)}
                  loading={acknowledging}
                  style={{ color: '#52c41a' }}
                />
              )}
            </Space>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="alert-panel">
      {/* 头部 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Text strong style={{ fontSize: 16 }}>告警列表</Text>
          {total > 0 && (
            <Tag color="red" style={{ marginLeft: 8 }}>{total}</Tag>
          )}
        </div>
        <Button
          type="text"
          size="small"
          icon={<ReloadOutlined />}
          onClick={handleRefresh}
          loading={loading}
        >
          刷新
        </Button>
      </div>

      {/* 告警列表 */}
      {loading && alerts.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : alerts.length === 0 ? (
        <Empty description="暂无告警" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div style={{ maxHeight: limit <= 5 ? 300 : 500, overflowY: 'auto' }}>
          {alerts.map((alert) => (
            <div key={alert.id}>
              {renderAlertItem(alert)}
            </div>
          ))}
        </div>
      )}

      {/* 详情弹窗 */}
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
              icon={<CheckCircleOutlined />}
              onClick={() => handleAcknowledge(selectedAlert.id)}
              loading={acknowledging}
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
            <Descriptions.Item label="级别">
              {renderSeverityTag(selectedAlert.severity)}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              {renderStatusTag(selectedAlert.status)}
            </Descriptions.Item>
            <Descriptions.Item label="指标">
              {selectedAlert.metric_key || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="告警类型">
              {selectedAlert.alert_type || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="当前值">
              {selectedAlert.current_value !== undefined && selectedAlert.current_value !== null
                ? `${selectedAlert.current_value.toFixed(2)}%`
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="基线值">
              {selectedAlert.baseline_value !== undefined && selectedAlert.baseline_value !== null
                ? `${selectedAlert.baseline_value.toFixed(2)}%`
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="数据库">
              {selectedAlert.config_id && dbNames[selectedAlert.config_id]
                ? dbNames[selectedAlert.config_id]
                : (selectedAlert.config_id || '-')}
            </Descriptions.Item>
            <Descriptions.Item label="发生时间" span={2}>
              {selectedAlert.created_at
                ? dayjs(selectedAlert.created_at).format('YYYY-MM-DD HH:mm:ss')
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="确认时间" span={2}>
              {selectedAlert.acknowledged_at
                ? dayjs(selectedAlert.acknowledged_at).format('YYYY-MM-DD HH:mm:ss')
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="解决时间" span={2}>
              {selectedAlert.resolved_at
                ? dayjs(selectedAlert.resolved_at).format('YYYY-MM-DD HH:mm:ss')
                : '-'}
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

export default AlertPanel

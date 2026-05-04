import { useState, useEffect, useCallback } from 'react'
import {
  Table, Tag, Space, Button, Select, Card,
  Typography, Statistic, Row, Col, Modal, Descriptions,
  message, Tabs, Popconfirm, Badge, Tooltip
} from 'antd'
import {
  ReloadOutlined, BellOutlined, CheckCircleOutlined,
  WarningOutlined, ExclamationCircleOutlined, CloseCircleOutlined,
  EyeOutlined, DeleteOutlined, CheckOutlined
} from '@ant-design/icons'
import { alertAPI } from '../services/api'
import dayjs from 'dayjs'

const { Title, Text } = Typography
const { Option } = Select

const SEV_CONFIG = {
  critical: { color: 'red',    text: '严重', icon: <CloseCircleOutlined /> },
  error:    { color: 'volcano', text: '高危', icon: <CloseCircleOutlined /> },
  warning:  { color: 'orange', text: '警告', icon: <WarningOutlined /> },
  info:     { color: 'blue',   text: '提示', icon: <ExclamationCircleOutlined /> },
}

const STATUS_CONFIG = {
  active:       { color: 'processing', text: '活跃',  badge: 'error' },
  acknowledged: { color: 'warning',    text: '已确认', badge: 'warning' },
  resolved:     { color: 'success',    text: '已恢复', badge: 'success' },
}

function SeverityTag({ severity }) {
  const c = SEV_CONFIG[severity] || { color: 'default', text: severity, icon: null }
  return <Tag color={c.color} icon={c.icon}>{c.text}</Tag>
}

function StatusBadge({ status }) {
  const c = STATUS_CONFIG[status] || { badge: 'default', text: status }
  return <Badge status={c.badge} text={c.text} />
}

// ─────────────────────────────────────────────
// 告警详情弹窗
// ─────────────────────────────────────────────
function AlertDetailModal({ alert, onClose, onAck, onDelete }) {
  if (!alert) return null
  return (
    <Modal
      title="告警详情"
      open={!!alert}
      onCancel={onClose}
      width={640}
      footer={[
        <Button key="close" onClick={onClose}>关闭</Button>,
        alert.status === 'active' && (
          <Button key="ack" type="primary" icon={<CheckOutlined />}
            onClick={() => { onAck(alert.id); onClose() }}>
            确认告警
          </Button>
        ),
        <Popconfirm key="del"
          title="删除后该指标可重新触发告警，确认删除？"
          onConfirm={() => { onDelete(alert.id); onClose() }}
          okText="确认删除" okButtonProps={{ danger: true }}
        >
          <Button danger icon={<DeleteOutlined />}>删除告警</Button>
        </Popconfirm>,
      ].filter(Boolean)}
    >
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="告警ID">{alert.id}</Descriptions.Item>
        <Descriptions.Item label="级别"><SeverityTag severity={alert.severity} /></Descriptions.Item>
        <Descriptions.Item label="状态"><StatusBadge status={alert.status} /></Descriptions.Item>
        <Descriptions.Item label="类型">{alert.alert_type}</Descriptions.Item>
        <Descriptions.Item label="指标键" span={2}>{alert.metric_key || '—'}</Descriptions.Item>
        <Descriptions.Item label="数据库">{alert.db_name || alert.config_id}</Descriptions.Item>
        <Descriptions.Item label="首次告警">
          {alert.create_time ? dayjs(alert.create_time).format('YYYY-MM-DD HH:mm:ss') : '—'}
        </Descriptions.Item>
        {alert.resolved_at && (
          <Descriptions.Item label="恢复时间" span={2}>
            {dayjs(alert.resolved_at).format('YYYY-MM-DD HH:mm:ss')}
          </Descriptions.Item>
        )}
        <Descriptions.Item label="告警标题" span={2}>{alert.title}</Descriptions.Item>
        <Descriptions.Item label="详细描述" span={2}>
          <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>{alert.description}</pre>
        </Descriptions.Item>
      </Descriptions>
    </Modal>
  )
}

// ─────────────────────────────────────────────
// 通用表格组件
// ─────────────────────────────────────────────
function AlertTable({ alerts, loading, showAck, onAck, onDelete, onView }) {
  const columns = [
    {
      title: '级别', dataIndex: 'severity', width: 80,
      render: v => <SeverityTag severity={v} />,
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <StatusBadge status={v} />,
    },
    {
      title: '数据库', dataIndex: 'db_name', width: 140, ellipsis: true,
      render: (v, r) => v || r.config_id,
    },
    {
      title: '告警类型', dataIndex: 'alert_type', width: 100,
    },
    {
      title: '指标键', dataIndex: 'metric_key', width: 140, ellipsis: true,
      render: v => v || <Text type="secondary">—</Text>,
    },
    {
      title: '告警标题', dataIndex: 'title', ellipsis: true,
    },
    {
      title: '首次告警', dataIndex: 'create_time', width: 140,
      render: v => v ? dayjs(v).format('MM-DD HH:mm:ss') : '—',
      sorter: (a, b) => new Date(a.create_time) - new Date(b.create_time),
      defaultSortOrder: 'descend',
    },
    {
      title: '操作', width: showAck ? 160 : 110, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" icon={<EyeOutlined />} onClick={() => onView(r)}>详情</Button>
          {showAck && r.status === 'active' && (
            <Tooltip title="确认后移入已确认列表，继续抑制重复推送">
              <Button size="small" icon={<CheckOutlined />}
                onClick={() => onAck(r.id)}>确认</Button>
            </Tooltip>
          )}
          <Popconfirm
            title="删除后该指标可重新触发告警"
            onConfirm={() => onDelete(r.id)}
            okText="确认" okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Table
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={alerts}
      columns={columns}
      scroll={{ x: 900 }}
      pagination={{ defaultPageSize: 20, showSizeChanger: true, showTotal: t => `共 ${t} 条` }}
      rowClassName={r => r.status === 'active' ? 'alert-row-active' : ''}
    />
  )
}

// ─────────────────────────────────────────────
// 主页面
// ─────────────────────────────────────────────
export default function AlertList() {
  const [allAlerts, setAllAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [severityFilter, setSeverityFilter] = useState('all')
  const [detailAlert, setDetailAlert] = useState(null)

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    try {
      const res = await alertAPI.list({ limit: 500 })
      setAllAlerts(res?.alerts || [])
    } catch {
      message.error('获取告警数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAlerts()
    const t = setInterval(fetchAlerts, 60000)
    return () => clearInterval(t)
  }, [fetchAlerts])

  const handleAck = async (id) => {
    try {
      await alertAPI.acknowledge(id)
      message.success('告警已确认，移入已确认列表')
      fetchAlerts()
    } catch {
      message.error('确认失败')
    }
  }

  const handleDelete = async (id) => {
    try {
      await alertAPI.delete(id)
      message.success('告警已删除，该指标可重新触发告警')
      fetchAlerts()
    } catch {
      message.error('删除失败')
    }
  }

  // 按严重程度过滤
  const filtered = severityFilter === 'all'
    ? allAlerts
    : allAlerts.filter(a => a.severity === severityFilter)

  const active       = filtered.filter(a => a.status === 'active')
  const acknowledged = filtered.filter(a => a.status === 'acknowledged')
  const resolved     = filtered.filter(a => a.status === 'resolved')

  const stats = {
    active:    allAlerts.filter(a => a.status === 'active').length,
    critical:  allAlerts.filter(a => a.status === 'active' && a.severity === 'critical').length,
    warning:   allAlerts.filter(a => a.status === 'active' && a.severity === 'warning').length,
    acked:     allAlerts.filter(a => a.status === 'acknowledged').length,
  }

  const tableProps = { loading, onAck: handleAck, onDelete: handleDelete, onView: setDetailAlert }

  return (
    <div>
      <style>{`
        .alert-row-active td { background: #fff1f0 !important; }
      `}</style>

      <Title level={4} style={{ marginBottom: 16 }}>
        <BellOutlined /> 告警中心
      </Title>

      {/* 统计卡片 */}
      <Row gutter={12} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="活跃告警" value={stats.active}
              valueStyle={{ color: stats.active > 0 ? '#ff4d4f' : '#52c41a' }}
              prefix={<BellOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="严重" value={stats.critical}
              valueStyle={{ color: '#ff4d4f' }} prefix={<CloseCircleOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="警告" value={stats.warning}
              valueStyle={{ color: '#faad14' }} prefix={<WarningOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="待处理（已确认）" value={stats.acked}
              valueStyle={{ color: '#d46b08' }} prefix={<CheckCircleOutlined />} />
          </Card>
        </Col>
      </Row>

      {/* 过滤栏 */}
      <Space style={{ marginBottom: 16 }} wrap>
        <Select value={severityFilter} onChange={setSeverityFilter} style={{ width: 120 }}>
          <Option value="all">全部级别</Option>
          <Option value="critical">严重</Option>
          <Option value="error">高危</Option>
          <Option value="warning">警告</Option>
          <Option value="info">提示</Option>
        </Select>
        <Button icon={<ReloadOutlined />} onClick={fetchAlerts} loading={loading}>刷新</Button>
        <Text type="secondary" style={{ fontSize: 12 }}>每分钟自动刷新</Text>
      </Space>

      {/* Tab 分组 */}
      <Tabs
        defaultActiveKey="active"
        items={[
          {
            key: 'active',
            label: (
              <span>
                活跃告警&nbsp;
                {stats.active > 0 && <Badge count={stats.active} size="small" />}
              </span>
            ),
            children: (
              <>
                {active.length === 0
                  ? <div style={{ textAlign: 'center', padding: '40px 0', color: '#52c41a' }}>
                      <CheckCircleOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                      <div>当前无活跃告警</div>
                    </div>
                  : <AlertTable alerts={active} showAck {...tableProps} />
                }
              </>
            ),
          },
          {
            key: 'acknowledged',
            label: (
              <span>
                已确认&nbsp;
                {stats.acked > 0 && <Badge count={stats.acked} size="small" color="orange" />}
              </span>
            ),
            children: (
              <div>
                <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
                  已确认的告警不会重复推送通知，也不会在活跃列表中显示。删除后该指标可重新触发告警。
                </Text>
                <AlertTable alerts={acknowledged} showAck={false} {...tableProps} />
              </div>
            ),
          },
          {
            key: 'resolved',
            label: '已恢复',
            children: <AlertTable alerts={resolved} showAck={false} {...tableProps} />,
          },
          {
            key: 'all',
            label: `全部（${filtered.length}）`,
            children: <AlertTable alerts={filtered} showAck {...tableProps} />,
          },
        ]}
      />

      <AlertDetailModal
        alert={detailAlert}
        onClose={() => setDetailAlert(null)}
        onAck={handleAck}
        onDelete={handleDelete}
      />
    </div>
  )
}

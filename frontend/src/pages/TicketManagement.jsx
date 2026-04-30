import { useState, useEffect } from 'react'
import { Card, Table, Tag, Typography, Spin, Alert, Button, Space, Modal, Input, message, Tabs, Descriptions, Timeline } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, ThunderboltOutlined, EyeOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { auditLogAPI } from '../services/api'

const { Title, Text } = Typography
const { TextArea } = Input

const TicketManagement = () => {
  const [loading, setLoading] = useState(true)
  const [tickets, setTickets] = useState([])
  const [error, setError] = useState(null)
  const [detailModal, setDetailModal] = useState({ visible: false, ticket: null })
  const [rejectModal, setRejectModal] = useState({ visible: false, ticketId: null })
  const [rejectReason, setRejectReason] = useState('')
  const [activeTab, setActiveTab] = useState('pending')

  useEffect(() => {
    loadTickets()
  }, [])

  const loadTickets = async () => {
    try {
      setLoading(true)
      const res = await auditLogAPI.list()
      setTickets(res.data || res || [])
    } catch (e) {
      setError(e.message || 'Failed to load tickets')
    } finally {
      setLoading(false)
    }
  }

  const handleApprove = async (id) => {
    try {
      await auditLogAPI.approve(id)
      message.success('工单已批准')
      loadTickets()
    } catch (e) {
      message.error('审批失败: ' + (e.response?.data?.error || e.message))
    }
  }

  const handleReject = async () => {
    try {
      await auditLogAPI.reject(rejectModal.ticketId, rejectReason)
      message.success('工单已拒绝')
      setRejectModal({ visible: false, ticketId: null })
      setRejectReason('')
      loadTickets()
    } catch (e) {
      message.error('拒绝失败: ' + (e.response?.data?.error || e.message))
    }
  }

  const handleExecute = async (id) => {
    Modal.confirm({
      title: '确认执行',
      content: '确定要执行此工单的操作吗？此操作不可撤销。',
      onOk: async () => {
        try {
          await auditLogAPI.execute(id)
          message.success('工单已执行')
          loadTickets()
        } catch (e) {
          message.error('执行失败: ' + (e.response?.data?.error || e.message))
        }
      }
    })
  }

  const handleDryRun = async (id) => {
    try {
      const res = await auditLogAPI.dryRun(id)
      Modal.info({
        title: '试运行结果',
        content: <pre style={{ whiteSpace: 'pre-wrap' }}>{res.data?.message || res.message || JSON.stringify(res)}</pre>,
        width: 600,
      })
    } catch (e) {
      message.error('试运行失败: ' + (e.response?.data?.error || e.message))
    }
  }

  const getStatusTag = (status) => {
    const map = {
      'pending': { color: 'orange', text: '待审批' },
      'approved': { color: 'blue', text: '已批准' },
      'executing': { color: 'processing', text: '执行中' },
      'success': { color: 'green', text: '执行成功' },
      'failed': { color: 'red', text: '执行失败' },
      'rejected': { color: 'default', text: '已拒绝' },
      'cancelled': { color: 'default', text: '已取消' },
    }
    const cfg = map[status] || { color: 'default', text: status }
    return <Tag color={cfg.color}>{cfg.text}</Tag>
  }

  const getRiskTag = (level) => {
    const map = {
      'low': { color: 'green', text: '低风险' },
      'medium': { color: 'orange', text: '中风险' },
      'high': { color: 'red', text: '高风险' },
      'critical': { color: 'magenta', text: '极高风险' },
    }
    const cfg = map[level] || { color: 'default', text: level }
    return <Tag color={cfg.color}>{cfg.text}</Tag>
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
    },
    {
      title: '数据库',
      key: 'config',
      render: (_, record) => record.config_name || record.config?.name || '-',
    },
    {
      title: '操作类型',
      dataIndex: 'action_type',
      key: 'action_type',
      render: (text) => <Tag>{text}</Tag>,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      width: 250,
    },
    {
      title: '风险等级',
      dataIndex: 'risk_level',
      key: 'risk_level',
      render: getRiskTag,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: getStatusTag,
    },
    {
      title: '创建时间',
      dataIndex: 'create_time',
      key: 'create_time',
      render: (text) => text ? new Date(text).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 280,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => setDetailModal({ visible: true, ticket: record })}>
            详情
          </Button>
          {record.status === 'pending' && (
            <>
              <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => handleApprove(record.id)}>
                批准
              </Button>
              <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => setRejectModal({ visible: true, ticketId: record.id })}>
                拒绝
              </Button>
            </>
          )}
          {record.status === 'approved' && (
            <>
              <Button size="small" icon={<ClockCircleOutlined />} onClick={() => handleDryRun(record.id)}>
                试运行
              </Button>
              <Button size="small" type="primary" danger icon={<ThunderboltOutlined />} onClick={() => handleExecute(record.id)}>
                执行
              </Button>
            </>
          )}
        </Space>
      ),
    },
  ]

  const filteredTickets = tickets.filter(t => {
    if (activeTab === 'all') return true
    if (activeTab === 'pending') return t.status === 'pending'
    if (activeTab === 'approved') return t.status === 'approved'
    if (activeTab === 'executed') return ['success', 'failed'].includes(t.status)
    if (activeTab === 'rejected') return t.status === 'rejected'
    return true
  })

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />

  return (
    <div style={{ padding: 24 }}>
      <Title level={2}>运维工单管理</Title>
      {error && <Alert message={error} type="error" style={{ marginBottom: 16 }} />}

      <Card>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          { key: 'pending', label: `待审批 (${tickets.filter(t => t.status === 'pending').length})` },
          { key: 'approved', label: `已批准 (${tickets.filter(t => t.status === 'approved').length})` },
          { key: 'executed', label: `已执行 (${tickets.filter(t => ['success', 'failed'].includes(t.status)).length})` },
          { key: 'rejected', label: `已拒绝 (${tickets.filter(t => t.status === 'rejected').length})` },
          { key: 'all', label: '全部' },
        ]} />

        <Table
          columns={columns}
          dataSource={filteredTickets}
          rowKey="id"
          pagination={{ pageSize: 20 }}
          size="middle"
        />
      </Card>

      {/* Detail Modal */}
      <Modal
        title={`工单详情 #${detailModal.ticket?.id}`}
        open={detailModal.visible}
        onCancel={() => setDetailModal({ visible: false, ticket: null })}
        footer={null}
        width={700}
      >
        {detailModal.ticket && (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="数据库">{detailModal.ticket.config_name || detailModal.ticket.config?.name}</Descriptions.Item>
            <Descriptions.Item label="操作类型">{detailModal.ticket.action_type}</Descriptions.Item>
            <Descriptions.Item label="风险等级">{getRiskTag(detailModal.ticket.risk_level)}</Descriptions.Item>
            <Descriptions.Item label="状态">{getStatusTag(detailModal.ticket.status)}</Descriptions.Item>
            <Descriptions.Item label="描述">{detailModal.ticket.description}</Descriptions.Item>
            <Descriptions.Item label="SQL命令">
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, maxHeight: 200, overflow: 'auto' }}>
                {detailModal.ticket.sql_command}
              </pre>
            </Descriptions.Item>
            {detailModal.ticket.rollback_command && (
              <Descriptions.Item label="回滚命令">
                <pre style={{ background: '#fff1f0', padding: 8, borderRadius: 4 }}>
                  {detailModal.ticket.rollback_command}
                </pre>
              </Descriptions.Item>
            )}
            <Descriptions.Item label="审批人">{detailModal.ticket.approver || '-'}</Descriptions.Item>
            <Descriptions.Item label="执行人">{detailModal.ticket.executor || '-'}</Descriptions.Item>
            <Descriptions.Item label="执行结果">{detailModal.ticket.execution_result || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{detailModal.ticket.create_time ? new Date(detailModal.ticket.create_time).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      {/* Reject Modal */}
      <Modal
        title="拒绝工单"
        open={rejectModal.visible}
        onOk={handleReject}
        onCancel={() => { setRejectModal({ visible: false, ticketId: null }); setRejectReason('') }}
        okText="确认拒绝"
        okButtonProps={{ danger: true }}
      >
        <p>请输入拒绝原因：</p>
        <TextArea
          rows={4}
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          placeholder="拒绝原因（必填）"
        />
      </Modal>
    </div>
  )
}

export default TicketManagement

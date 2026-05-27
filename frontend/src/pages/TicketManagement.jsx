import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Typography, Spin, Alert, Button, Space, Modal, Input, message, Tabs, Descriptions, Form, Select, Row, Col, Statistic } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, ThunderboltOutlined, EyeOutlined, ClockCircleOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { auditLogAPI, ticketAPI, databaseAPI } from '../services/api'
import { PermissionGuard } from '../components/AuthGuard'
import { Perm } from '../utils/permission'

const { Title, Text } = Typography
const { TextArea } = Input

const TicketManagement = () => {
  const [loading, setLoading] = useState(true)
  const [tickets, setTickets] = useState([])
  const [error, setError] = useState(null)
  const [detailModal, setDetailModal] = useState({ visible: false, ticket: null })
  const [rejectModal, setRejectModal] = useState({ visible: false, ticketId: null })
  const [rejectReason, setRejectReason] = useState('')
  const [createModal, setCreateModal] = useState(false)
  const [createLoading, setCreateLoading] = useState(false)
  const [databases, setDatabases] = useState([])
  const [activeTab, setActiveTab] = useState('pending')
  const [form] = Form.useForm()

  const loadTickets = useCallback(async () => {
    try {
      setLoading(true)
      const res = await auditLogAPI.list()
      setTickets(res?.auditlogs || res?.data || [])
    } catch (e) {
      setError(e.message || 'Failed to load tickets')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list()
      const dbs = res?.databases || res?.data || []
      setDatabases(Array.isArray(dbs) ? dbs : [])
    } catch (_) {}
  }, [])

  useEffect(() => { loadTickets(); loadDatabases(); }, [loadTickets, loadDatabases])

  const handleApprove = async (id) => {
    Modal.confirm({
      title: '确认审批',
      content: '确定要批准此工单吗？批准后操作人可执行该工单。',
      onOk: async () => {
        try {
          await auditLogAPI.approve(id)
          message.success('工单已批准')
          loadTickets()
        } catch (e) {
          message.error('审批失败: ' + (e.response?.data?.error || e.message))
        }
      }
    })
  }

  const handleReject = async () => {
    if (!rejectReason.trim()) {
      message.warning('请输入拒绝原因')
      return
    }
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
      content: '确定要执行此工单的操作吗？此操作将在目标数据库上执行SQL，不可撤销。',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await auditLogAPI.execute(id)
          if (res?.status === 'success') {
            message.success('工单执行成功')
          } else {
            message.warning('工单执行完成，但可能存在问题')
          }
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
        content: (
          <div>
            <p><Tag color={res?.status === 'valid' ? 'green' : 'red'}>{res?.status === 'valid' ? '语法验证通过' : '语法验证失败'}</Tag></p>
            <p>{res?.message || ''}</p>
            {res?.parsed_commands && (
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, maxHeight: 300, overflow: 'auto', fontSize: 12 }}>
                {res.parsed_commands.map((c, i) => `${c.status === 'valid' ? 'OK' : 'ERR'}: ${c.sql}${c.error ? '\n  Error: ' + c.error : ''}`).join('\n\n')}
              </pre>
            )}
          </div>
        ),
        width: 700,
      })
    } catch (e) {
      message.error('试运行失败: ' + (e.response?.data?.error || e.message))
    }
  }

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      setCreateLoading(true)
      await ticketAPI.create(values)
      message.success('工单已创建')
      setCreateModal(false)
      form.resetFields()
      loadTickets()
    } catch (e) {
      if (e.errorFields) return
      message.error('创建失败: ' + (e.response?.data?.error || e.message))
    } finally {
      setCreateLoading(false)
    }
  }

  const getStatusTag = (status) => {
    const map = {
      'pending': { color: 'orange', text: '待审批' },
      'pending_approval_2': { color: 'gold', text: '待二级审批' },
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
      title: 'ID', dataIndex: 'id', key: 'id', width: 60,
    },
    {
      title: '数据库', key: 'config',
      render: (_, record) => record.config_name || record.config?.name || `DB#${record.config_id}`,
    },
    {
      title: '操作类型', dataIndex: 'action_type', key: 'action_type', width: 100,
      render: (text) => <Tag>{text}</Tag>,
    },
    {
      title: '描述', dataIndex: 'description', key: 'description', ellipsis: true, width: 220,
    },
    {
      title: '风险等级', dataIndex: 'risk_level', key: 'risk_level', width: 90,
      render: getRiskTag,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: getStatusTag,
    },
    {
      title: '创建时间', dataIndex: 'create_time', key: 'create_time', width: 160,
      render: (text) => text ? new Date(text).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'actions', width: 260,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => setDetailModal({ visible: true, ticket: record })}>
            详情
          </Button>
          {['pending', 'pending_approval_2'].includes(record.status) && (
            <PermissionGuard code={Perm.TICKETS_APPROVE}>
              <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => handleApprove(record.id)}>
                批准
              </Button>
              <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => setRejectModal({ visible: true, ticketId: record.id })}>
                拒绝
              </Button>
            </PermissionGuard>
          )}
          {record.status === 'approved' && (
            <PermissionGuard code={Perm.TICKETS_EXECUTE}>
              <Button size="small" icon={<ClockCircleOutlined />} onClick={() => handleDryRun(record.id)}>
                试运行
              </Button>
              <Button size="small" type="primary" danger icon={<ThunderboltOutlined />} onClick={() => handleExecute(record.id)}>
                执行
              </Button>
            </PermissionGuard>
          )}
        </Space>
      ),
    },
  ]

  const filteredTickets = tickets.filter(t => {
    if (activeTab === 'all') return true
    if (activeTab === 'pending') return ['pending', 'pending_approval_2'].includes(t.status)
    if (activeTab === 'approved') return t.status === 'approved'
    if (activeTab === 'executed') return ['success', 'failed', 'executing'].includes(t.status)
    if (activeTab === 'rejected') return t.status === 'rejected'
    return true
  })

  const pendingCount = tickets.filter(t => ['pending', 'pending_approval_2'].includes(t.status)).length
  const approvedCount = tickets.filter(t => t.status === 'approved').length
  const executedCount = tickets.filter(t => ['success', 'failed'].includes(t.status)).length

  if (loading && tickets.length === 0) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />

  return (
    <div style={{ padding: 24 }}>
      <Title level={2}>运维工单管理</Title>
      {error && <Alert message={error} type="error" style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card><Statistic title="待审批" value={pendingCount} valueStyle={{ color: '#fa8c16' }} prefix={<ClockCircleOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="已批准待执行" value={approvedCount} valueStyle={{ color: '#1890ff' }} prefix={<CheckCircleOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="已执行" value={executedCount} valueStyle={{ color: '#52c41a' }} prefix={<ThunderboltOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="工单总数" value={tickets.length} /></Card>
        </Col>
      </Row>

      <Card>
        <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between' }}>
          <Tabs activeKey={activeTab} onChange={setActiveTab} style={{ marginBottom: 0 }} items={[
            { key: 'pending', label: `待审批 (${pendingCount})` },
            { key: 'approved', label: `已批准 (${approvedCount})` },
            { key: 'executed', label: `已执行 (${executedCount})` },
            { key: 'rejected', label: `已拒绝` },
            { key: 'all', label: '全部' },
          ]} />
          <Space>
            <Button icon={<ReloadOutlined />} onClick={loadTickets}>刷新</Button>
            <PermissionGuard code={Perm.TICKETS_CREATE}><Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModal(true)}>创建工单</Button></PermissionGuard>
          </Space>
        </div>

        <Table
          columns={columns}
          dataSource={filteredTickets}
          rowKey="id"
          pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }}
          size="middle"
        />
      </Card>

      {/* Detail Modal */}
      <Modal
        title={`工单详情 #${detailModal.ticket?.id}`}
        open={detailModal.visible}
        onCancel={() => setDetailModal({ visible: false, ticket: null })}
        footer={null}
        width={750}
      >
        {detailModal.ticket && (
          <Descriptions bordered column={2} size="small">
            <Descriptions.Item label="数据库" span={1}>{detailModal.ticket.config_name || detailModal.ticket.config?.name || `DB#${detailModal.ticket.config_id}`}</Descriptions.Item>
            <Descriptions.Item label="操作类型" span={1}><Tag>{detailModal.ticket.action_type}</Tag></Descriptions.Item>
            <Descriptions.Item label="风险等级" span={1}>{getRiskTag(detailModal.ticket.risk_level)}</Descriptions.Item>
            <Descriptions.Item label="状态" span={1}>{getStatusTag(detailModal.ticket.status)}</Descriptions.Item>
            <Descriptions.Item label="描述" span={2}>{detailModal.ticket.description}</Descriptions.Item>
            <Descriptions.Item label="SQL命令" span={2}>
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, maxHeight: 200, overflow: 'auto', fontSize: 12 }}>
                {detailModal.ticket.sql_command || '-'}
              </pre>
            </Descriptions.Item>
            {detailModal.ticket.rollback_command && (
              <Descriptions.Item label="回滚命令" span={2}>
                <pre style={{ background: '#fff1f0', padding: 8, borderRadius: 4, fontSize: 12 }}>
                  {detailModal.ticket.rollback_command}
                </pre>
              </Descriptions.Item>
            )}
            <Descriptions.Item label="审批人" span={1}>{detailModal.ticket.approver || '-'}</Descriptions.Item>
            <Descriptions.Item label="审批时间" span={1}>{detailModal.ticket.approve_time ? new Date(detailModal.ticket.approve_time).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
            <Descriptions.Item label="执行人" span={1}>{detailModal.ticket.executor || '-'}</Descriptions.Item>
            <Descriptions.Item label="执行时间" span={1}>{detailModal.ticket.execute_time ? new Date(detailModal.ticket.execute_time).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
            {detailModal.ticket.execution_result && (
              <Descriptions.Item label="执行结果" span={2}>
                <pre style={{ background: '#f6ffed', padding: 8, borderRadius: 4, maxHeight: 150, overflow: 'auto', fontSize: 12 }}>
                  {detailModal.ticket.execution_result}
                </pre>
              </Descriptions.Item>
            )}
            <Descriptions.Item label="创建时间" span={2}>{detailModal.ticket.create_time ? new Date(detailModal.ticket.create_time).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
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

      {/* Create Ticket Modal */}
      <Modal
        title="创建运维工单"
        open={createModal}
        onOk={handleCreate}
        onCancel={() => { setCreateModal(false); form.resetFields() }}
        confirmLoading={createLoading}
        okText="提交工单"
        width={650}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="config_id" label="目标数据库" rules={[{ required: true, message: '请选择数据库' }]}>
            <Select placeholder="选择数据库" showSearch optionFilterProp="children">
              {databases.map(db => (
                <Select.Option key={db.id} value={db.id}>
                  {db.name} ({db.db_type?.toUpperCase()}) - {db.host}:{db.port}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="action_type" label="操作类型" rules={[{ required: true, message: '请选择操作类型' }]}>
            <Select placeholder="选择操作类型">
              <Select.Option value="manual_sql">手动执行SQL</Select.Option>
              <Select.Option value="kill_session">终止会话</Select.Option>
              <Select.Option value="resize_datafile">调整数据文件</Select.Option>
              <Select.Option value="config_change">配置变更</Select.Option>
              <Select.Option value="other">其他</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="description" label="操作描述" rules={[{ required: true, message: '请输入操作描述' }]}>
            <TextArea rows={2} placeholder="描述本次操作的目的和预期影响" />
          </Form.Item>
          <Form.Item name="sql_command" label="SQL命令" rules={[{ required: true, message: '请输入SQL命令' }]}>
            <TextArea rows={4} placeholder="输入要执行的SQL命令" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item name="risk_level" label="风险等级" rules={[{ required: true, message: '请选择风险等级' }]}>
            <Select placeholder="选择风险等级">
              <Select.Option value="low">低风险 - 自动批准</Select.Option>
              <Select.Option value="medium">中风险 - 需主管审批</Select.Option>
              <Select.Option value="high">高风险 - 需多级审批</Select.Option>
              <Select.Option value="critical">极高风险 - 需最高级别审批</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="rollback_command" label="回滚命令（可选）">
            <TextArea rows={2} placeholder="输入回滚SQL命令" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default TicketManagement

import React, { useState, useEffect, useCallback } from 'react'
import {
  Tabs, Table, Select, Tag, Button, Modal, Form, Input,
  InputNumber, Switch, Space, Typography, Popconfirm,
  message, Badge, Divider, Row, Col, Card, Tooltip, Checkbox
} from 'antd'
import {
  EditOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined,
  SettingOutlined, InfoCircleOutlined, DatabaseOutlined,
  CopyOutlined, ThunderboltOutlined, AppstoreOutlined,
  UnorderedListOutlined, SwapOutlined
} from '@ant-design/icons'
import { alertRuleAPI, alertTemplateAPI, databaseAPI } from '../services/api'
import { PermissionGuard } from '../components/AuthGuard'
import { Perm } from '../utils/permission'
import { Spin } from 'antd'

const { Title, Text } = Typography
const { Option } = Select

const DB_TYPE_LABELS = {
  oracle: 'Oracle',
  mysql: 'MySQL',
  pgsql: 'PostgreSQL',
  dm: '达梦数据库',
  gbase: 'Gbase 8a',
  tdsql: 'TDSQL',
  mongo: 'MongoDB',
  redis: 'Redis',
}
const DB_TYPES = Object.keys(DB_TYPE_LABELS)

const RULE_TYPE_LABELS = { threshold: '固定阈值', baseline_amplitude: '基线振幅' }
const DIRECTION_LABELS = { up: '上升触发', down: '下降触发', both: '双向触发' }

const SEV_COLORS = {
  warn: 'gold',
  error: 'orange',
  critical: 'red',
}

// 渲染三级阈值展示块
function ThresholdBadges({ row, isOverride }) {
  const src = isOverride ? row.override : row.template
  if (!src) return <Text type="secondary">—</Text>
  const cfg = row.effective || src

  if (cfg.rule_type === 'threshold') {
    return (
      <Space size={4} wrap>
        {cfg.warn_threshold != null && (
          <Tag color={SEV_COLORS.warn}>一级 {cfg.direction === 'down' ? '<' : '>'}{cfg.warn_threshold}{src.unit || ''}</Tag>
        )}
        {cfg.error_threshold != null && (
          <Tag color={SEV_COLORS.error}>二级 {cfg.direction === 'down' ? '<' : '>'}{cfg.error_threshold}{src.unit || ''}</Tag>
        )}
        {cfg.critical_threshold != null && (
          <Tag color={SEV_COLORS.critical}>三级 {cfg.direction === 'down' ? '<' : '>'}{cfg.critical_threshold}{src.unit || ''}</Tag>
        )}
      </Space>
    )
  }
  return (
    <Space size={4} wrap>
      {cfg.warn_amplitude_pct != null && (
        <Tag color={SEV_COLORS.warn}>一级 偏离≥{cfg.warn_amplitude_pct}%</Tag>
      )}
      {cfg.error_amplitude_pct != null && (
        <Tag color={SEV_COLORS.error}>二级 偏离≥{cfg.error_amplitude_pct}%</Tag>
      )}
      {cfg.critical_amplitude_pct != null && (
        <Tag color={SEV_COLORS.critical}>三级 偏离≥{cfg.critical_amplitude_pct}%</Tag>
      )}
    </Space>
  )
}

// ─────────────────────────────────────────────
// 模板组 编辑弹窗
// ─────────────────────────────────────────────
function TemplateGroupModal({ open, initial, dbType: parentDbType, onOk, onCancel }) {
  const [form] = Form.useForm()

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initial || { db_type: parentDbType || 'oracle', is_default: false, description: '' })
    }
  }, [open, initial, form, parentDbType])

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      onOk(values)
    } catch (_) {}
  }

  return (
    <Modal
      title={initial?.id ? '编辑模板组' : '新建模板组'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      width={500}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="模板组名称" rules={[{ required: true, message: '请输入模板组名称' }]}>
          <Input placeholder="如：生产库-严格、测试库-宽松" />
        </Form.Item>
        {!initial?.id && (
          <Form.Item name="db_type" label="数据库类型" rules={[{ required: true }]}>
            <Select>
              {DB_TYPES.map(t => <Option key={t} value={t}>{DB_TYPE_LABELS[t]}</Option>)}
            </Select>
          </Form.Item>
        )}
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} placeholder="模板组用途说明" />
        </Form.Item>
        <Form.Item name="is_default" label="设为默认模板" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Text type="secondary">
          <InfoCircleOutlined /> 默认模板会在数据库未显式分配模板组时自动使用。同类型仅允许一个默认模板。
        </Text>
      </Form>
    </Modal>
  )
}

// ─────────────────────────────────────────────
// 克隆模板组弹窗
// ─────────────────────────────────────────────
function CloneModal({ open, source, onOk, onCancel }) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open && source) {
      form.setFieldsValue({ name: `${source.name}（副本）`, description: source.description || '' })
    }
  }, [open, source, form])

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      await onOk(values)
    } catch (_) {
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={`克隆模板组：${source?.name || ''}`}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      width={450}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="新模板组名称" rules={[{ required: true, message: '请输入新名称' }]}>
          <Input placeholder="克隆后的模板组名称" />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ─────────────────────────────────────────────
// 规则编辑弹窗
// ─────────────────────────────────────────────
function RuleModal({ open, initial, dbType, onOk, onCancel }) {
  const [form] = Form.useForm()
  const [ruleType, setRuleType] = useState('threshold')
  const [availableMetrics, setAvailableMetrics] = useState([])
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [existingKeys, setExistingKeys] = useState(new Set())

  const loadMetrics = useCallback(async (dt) => {
    if (!dt) return
    setMetricsLoading(true)
    try {
      const res = await alertRuleAPI.listAvailableMetrics(dt)
      setAvailableMetrics(res.metrics || [])
    } catch (_) {
      setAvailableMetrics([])
    } finally {
      setMetricsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initial || { rule_type: 'threshold', direction: 'up', is_enabled: true })
      setRuleType(initial?.rule_type || 'threshold')
      loadMetrics(dbType)
    }
  }, [open, initial, form, dbType, loadMetrics])

  const handleMetricSelect = (val) => {
    const m = availableMetrics.find(x => x.metric_key === val)
    if (m && m.display_name) {
      form.setFieldValue('display_name', m.display_name)
    }
  }

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      onOk(values)
    } catch (_) {}
  }

  return (
    <Modal
      title={initial?.id ? '编辑告警规则' : '新增告警规则'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      width={600}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        {!initial?.id && (
          <>
            <Form.Item
              name="metric_key"
              label="指标键"
              rules={[{ required: true, message: '请选择或输入指标键' }]}
            >
              <Select
                showSearch
                allowClear
                mode="combobox"
                placeholder={metricsLoading ? '加载中…' : '选择或输入指标键'}
                notFoundContent={metricsLoading ? <Spin size="small" /> : '暂无采集数据，可手动输入'}
                filterOption={(input, opt) =>
                  (opt?.value || '').toLowerCase().includes(input.toLowerCase()) ||
                  (opt?.label || '').toLowerCase().includes(input.toLowerCase())
                }
                onSelect={handleMetricSelect}
                options={availableMetrics.map(m => ({
                  value: m.metric_key,
                  label: m.display_name
                    ? `${m.metric_key}（${m.display_name}）${m.has_template ? ' ✓已有规则' : ''}`
                    : `${m.metric_key}${m.has_template ? ' ✓已有规则' : ''}`,
                  disabled: m.has_template,
                }))}
              />
            </Form.Item>
            {availableMetrics.length > 0 && (
              <div style={{ marginTop: -12, marginBottom: 12, fontSize: 12, color: '#888' }}>
                共 {availableMetrics.filter(m => !m.has_template).length} 个未配置指标可选
              </div>
            )}
          </>
        )}
        <Form.Item name="display_name" label="指标显示名" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
              <Select onChange={v => setRuleType(v)}>
                <Option value="threshold">固定阈值</Option>
                <Option value="baseline_amplitude">基线振幅</Option>
              </Select>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="direction" label="触发方向" rules={[{ required: true }]}>
              <Select>
                <Option value="up">上升触发（值越大越危险）</Option>
                <Option value="down">下降触发（值越小越危险）</Option>
                <Option value="both">双向触发</Option>
              </Select>
            </Form.Item>
          </Col>
        </Row>

        {ruleType === 'threshold' ? (
          <>
            <Divider orientation="left" plain>三级固定阈值</Divider>
            <Row gutter={12}>
              <Col span={8}>
                <Form.Item name="warn_threshold" label={<Tag color={SEV_COLORS.warn}>一级(warning)</Tag>}>
                  <InputNumber style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="error_threshold" label={<Tag color={SEV_COLORS.error}>二级(error)</Tag>}>
                  <InputNumber style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="critical_threshold" label={<Tag color={SEV_COLORS.critical}>三级(critical)</Tag>}>
                  <InputNumber style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
          </>
        ) : (
          <>
            <Divider orientation="left" plain>三级基线振幅阈值（相对基线均值的偏离百分比）</Divider>
            <Row gutter={12}>
              <Col span={8}>
                <Form.Item name="warn_amplitude_pct" label={<Tag color={SEV_COLORS.warn}>一级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="error_amplitude_pct" label={<Tag color={SEV_COLORS.error}>二级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="critical_amplitude_pct" label={<Tag color={SEV_COLORS.critical}>三级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
            </Row>
          </>
        )}

        <Row gutter={12}>
          <Col span={8}>
            <Form.Item name="unit" label="单位（可选）">
              <Input placeholder="如 % / count / ms" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="is_enabled" label="是否启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="description" label="描述（可选）">
          <Input.TextArea rows={2} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ─────────────────────────────────────────────
// 覆盖配置编辑弹窗
// ─────────────────────────────────────────────
function OverrideModal({ open, initial, onOk, onCancel }) {
  const [form] = Form.useForm()
  const [ruleType, setRuleType] = useState(null)

  useEffect(() => {
    if (open) {
      const tpl = initial?.template
      const ov = initial?.override
      form.setFieldsValue({
        rule_type: ov?.rule_type || tpl?.rule_type || 'threshold',
        direction: ov?.direction || tpl?.direction || 'up',
        warn_threshold: ov?.warn_threshold ?? tpl?.warn_threshold,
        error_threshold: ov?.error_threshold ?? tpl?.error_threshold,
        critical_threshold: ov?.critical_threshold ?? tpl?.critical_threshold,
        warn_amplitude_pct: ov?.warn_amplitude_pct ?? tpl?.warn_amplitude_pct,
        error_amplitude_pct: ov?.error_amplitude_pct ?? tpl?.error_amplitude_pct,
        critical_amplitude_pct: ov?.critical_amplitude_pct ?? tpl?.critical_amplitude_pct,
        is_enabled: ov?.is_enabled ?? true,
        note: ov?.note || '',
      })
      setRuleType(ov?.rule_type || tpl?.rule_type || 'threshold')
    }
  }, [open, initial, form])

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      onOk({ metric_key: initial.metric_key, ...values })
    } catch (_) {}
  }

  return (
    <Modal
      title={`配置覆盖：${initial?.display_name || initial?.metric_key}`}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      width={600}
      destroyOnClose
    >
      {initial?.template && (
        <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
          <Text type="secondary">
            模板默认值（{RULE_TYPE_LABELS[initial.template.rule_type]}
            / {DIRECTION_LABELS[initial.template.direction]}）
          </Text>
          <div style={{ marginTop: 4 }}>
            <ThresholdBadges row={initial} isOverride={false} />
          </div>
        </Card>
      )}
      <Form form={form} layout="vertical">
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="rule_type" label="规则类型">
              <Select onChange={v => setRuleType(v)}>
                <Option value="threshold">固定阈值</Option>
                <Option value="baseline_amplitude">基线振幅</Option>
              </Select>
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="direction" label="触发方向">
              <Select>
                <Option value="up">上升触发</Option>
                <Option value="down">下降触发</Option>
                <Option value="both">双向触发</Option>
              </Select>
            </Form.Item>
          </Col>
        </Row>

        {ruleType === 'threshold' ? (
          <>
            <Divider orientation="left" plain>三级固定阈值（覆盖模板）</Divider>
            <Row gutter={12}>
              <Col span={8}>
                <Form.Item name="warn_threshold" label={<Tag color={SEV_COLORS.warn}>一级</Tag>}>
                  <InputNumber style={{ width: '100%' }} placeholder="留空则沿用模板" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="error_threshold" label={<Tag color={SEV_COLORS.error}>二级</Tag>}>
                  <InputNumber style={{ width: '100%' }} placeholder="留空则沿用模板" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="critical_threshold" label={<Tag color={SEV_COLORS.critical}>三级</Tag>}>
                  <InputNumber style={{ width: '100%' }} placeholder="留空则沿用模板" />
                </Form.Item>
              </Col>
            </Row>
          </>
        ) : (
          <>
            <Divider orientation="left" plain>三级振幅阈值（覆盖模板，相对基线均值偏离%）</Divider>
            <Row gutter={12}>
              <Col span={8}>
                <Form.Item name="warn_amplitude_pct" label={<Tag color={SEV_COLORS.warn}>一级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="error_amplitude_pct" label={<Tag color={SEV_COLORS.error}>二级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="critical_amplitude_pct" label={<Tag color={SEV_COLORS.critical}>三级 偏离%</Tag>}>
                  <InputNumber min={0} max={999} style={{ width: '100%' }} addonAfter="%" />
                </Form.Item>
              </Col>
            </Row>
          </>
        )}

        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="is_enabled" label="是否启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="note" label="备注（可选）">
          <Input.TextArea rows={2} placeholder="说明为何覆盖模板默认值" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ─────────────────────────────────────────────
// Tab 1：模板组管理
// ─────────────────────────────────────────────
function TemplateGroupTab() {
  const [dbType, setDbType] = useState('oracle')
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [cloneOpen, setCloneOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [cloning, setCloning] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await alertTemplateAPI.list({ db_type: dbType })
      setGroups(res.templates || [])
    } catch (e) {
      message.error('加载模板组失败')
    } finally {
      setLoading(false)
    }
  }, [dbType])

  useEffect(() => { load() }, [load])

  const handleSave = async (values) => {
    try {
      if (editing?.id) {
        await alertTemplateAPI.update(editing.id, values)
        message.success('模板组已更新')
      } else {
        await alertTemplateAPI.create(values)
        message.success('模板组已创建')
      }
      setModalOpen(false)
      load()
    } catch (e) {
      message.error(e?.response?.data?.error || '保存失败')
    }
  }

  const handleDelete = async (id) => {
    try {
      await alertTemplateAPI.delete(id)
      message.success('模板组已删除（含所有规则）')
      load()
    } catch (e) {
      message.error('删除失败')
    }
  }

  const handleClone = async (values) => {
    try {
      await alertTemplateAPI.clone(cloning.id, {
        action: 'clone',
        name: values.name,
        description: values.description,
      })
      message.success('模板组已克隆')
      setCloneOpen(false)
      load()
    } catch (e) {
      message.error(e?.response?.data?.error || '克隆失败')
    }
  }

  const columns = [
    {
      title: '模板组名称',
      dataIndex: 'name',
      width: 200,
      render: (text, r) => (
        <Space>
          <AppstoreOutlined />
          <Text strong>{text}</Text>
          {r.is_default && <Tag color="blue">默认</Tag>}
        </Space>
      ),
    },
    {
      title: '数据库类型',
      dataIndex: 'db_type',
      width: 120,
      render: v => <Tag>{DB_TYPE_LABELS[v] || v}</Tag>,
    },
    {
      title: '规则数',
      dataIndex: 'rule_count',
      width: 80,
      align: 'center',
      render: v => <Badge count={v} showZero style={{ backgroundColor: '#1890ff' }} />,
    },
    {
      title: '已分配数据库',
      dataIndex: 'assigned_db_count',
      width: 110,
      align: 'center',
      render: v => <Badge count={v} showZero style={{ backgroundColor: '#52c41a' }} />,
    },
    {
      title: '描述',
      dataIndex: 'description',
      ellipsis: true,
      render: v => <Text type="secondary">{v || '—'}</Text>,
    },
    {
      title: '更新时间',
      dataIndex: 'update_time',
      width: 170,
      render: v => v ? new Date(v).toLocaleString() : '—',
    },
    {
      title: '操作',
      width: 220,
      render: (_, r) => (
        <Space>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Button
              size="small" icon={<EditOutlined />}
              onClick={() => { setEditing(r); setModalOpen(true) }}
            >编辑</Button>
          </PermissionGuard>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Tooltip title="克隆模板组及所有规则">
              <Button
                size="small" icon={<CopyOutlined />}
                onClick={() => { setCloning(r); setCloneOpen(true) }}
              >克隆</Button>
            </Tooltip>
          </PermissionGuard>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Popconfirm
              title="删除模板组将同时删除组内所有规则，确定删除？"
              onConfirm={() => handleDelete(r.id)}
            >
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </PermissionGuard>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select value={dbType} onChange={v => setDbType(v)} style={{ width: 160 }}>
          {DB_TYPES.map(t => <Option key={t} value={t}>{DB_TYPE_LABELS[t]}</Option>)}
        </Select>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}><Button
          type="primary" icon={<PlusOutlined />}
          onClick={() => { setEditing(null); setModalOpen(true) }}
        >
          新建模板组
        </Button></PermissionGuard>
        <Text type="secondary">
          <InfoCircleOutlined /> 模板组是告警规则的容器，在「规则配置」Tab 中添加具体指标规则
        </Text>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={groups}
        columns={columns}
        pagination={false}
        bordered
      />

      <TemplateGroupModal
        open={modalOpen}
        initial={editing}
        dbType={dbType}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
      />

      <CloneModal
        open={cloneOpen}
        source={cloning}
        onOk={handleClone}
        onCancel={() => setCloneOpen(false)}
      />
    </>
  )
}

// ─────────────────────────────────────────────
// Tab 2：规则配置
// ─────────────────────────────────────────────
function RuleConfigTab() {
  const [dbType, setDbType] = useState('oracle')
  const [groups, setGroups] = useState([])
  const [selectedGroupId, setSelectedGroupId] = useState(null)
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [selectedRowKeys, setSelectedRowKeys] = useState([])

  // 加载模板组列表
  const loadGroups = useCallback(async () => {
    try {
      const res = await alertTemplateAPI.list({ db_type: dbType })
      setGroups(res.templates || [])
    } catch (_) {}
  }, [dbType])

  useEffect(() => { loadGroups() }, [loadGroups])

  // 加载选定模板组的规则
  const loadRules = useCallback(async () => {
    if (!selectedGroupId) { setRules([]); return }
    setLoading(true)
    try {
      const res = await alertTemplateAPI.listRules(selectedGroupId)
      setRules(res.rules || [])
    } catch (e) {
      message.error('加载规则失败')
    } finally {
      setLoading(false)
    }
  }, [selectedGroupId])

  useEffect(() => { loadRules() }, [loadRules])

  // 切换数据库类型时重置选择
  useEffect(() => {
    setSelectedGroupId(null)
    setRules([])
    setSelectedRowKeys([])
  }, [dbType])

  const handleSave = async (values) => {
    try {
      if (editing?.id) {
        await alertTemplateAPI.updateRule(selectedGroupId, editing.id, values)
        message.success('规则已更新')
      } else {
        await alertTemplateAPI.addRule(selectedGroupId, values)
        message.success('规则已添加')
      }
      setModalOpen(false)
      loadRules()
      // 刷新模板组列表以更新 rule_count
      loadGroups()
    } catch (e) {
      message.error(e?.response?.data?.error || '保存失败')
    }
  }

  const handleDelete = async (ruleId) => {
    try {
      await alertTemplateAPI.deleteRule(selectedGroupId, ruleId)
      message.success('规则已删除')
      loadRules()
      loadGroups()
    } catch (e) {
      message.error('删除失败')
    }
  }

  const handleBatchToggle = async (enabled) => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择规则')
      return
    }
    try {
      await alertTemplateAPI.batchToggleRules(selectedGroupId, selectedRowKeys, enabled)
      message.success(`已${enabled ? '启用' : '停用'} ${selectedRowKeys.length} 条规则`)
      setSelectedRowKeys([])
      loadRules()
    } catch (e) {
      message.error('批量操作失败')
    }
  }

  const selectedGroup = groups.find(g => g.id === selectedGroupId)

  const columns = [
    {
      title: '指标',
      dataIndex: 'display_name',
      width: 160,
      render: (text, r) => (
        <Space direction="vertical" size={0}>
          <Text strong>{text}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.metric_key}</Text>
        </Space>
      ),
    },
    {
      title: '规则类型',
      dataIndex: 'rule_type',
      width: 100,
      render: v => <Tag>{RULE_TYPE_LABELS[v] || v}</Tag>,
    },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 90,
      render: v => <Tag color="blue">{DIRECTION_LABELS[v] || v}</Tag>,
    },
    {
      title: '三级阈值/振幅',
      render: (_, r) => <ThresholdBadges row={{ template: r, effective: r }} isOverride={false} />,
    },
    {
      title: '单位',
      dataIndex: 'unit',
      width: 60,
      render: v => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'is_enabled',
      width: 70,
      render: v => <Badge status={v ? 'success' : 'default'} text={v ? '启用' : '停用'} />,
    },
    {
      title: '描述',
      dataIndex: 'description',
      width: 120,
      ellipsis: true,
      render: v => v || '—',
    },
    {
      title: '操作',
      width: 120,
      render: (_, r) => (
        <Space>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Button
              size="small" icon={<EditOutlined />}
              onClick={() => { setEditing(r); setModalOpen(true) }}
            >编辑</Button>
          </PermissionGuard>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Popconfirm title="确认删除此规则？" onConfirm={() => handleDelete(r.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </PermissionGuard>
        </Space>
      ),
    },
  ]

  const rowSelection = {
    selectedRowKeys,
    onChange: setSelectedRowKeys,
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select value={dbType} onChange={v => setDbType(v)} style={{ width: 160 }}>
          {DB_TYPES.map(t => <Option key={t} value={t}>{DB_TYPE_LABELS[t]}</Option>)}
        </Select>
        <Select
          showSearch
          placeholder="选择模板组"
          value={selectedGroupId}
          onChange={v => { setSelectedGroupId(v); setSelectedRowKeys([]) }}
          style={{ width: 280 }}
          optionFilterProp="label"
          notFoundContent="该类型暂无模板组"
          options={groups.map(g => ({
            value: g.id,
            label: `${g.name}${g.is_default ? ' (默认)' : ''} — ${g.rule_count} 条规则`,
          }))}
        />
        <Button icon={<ReloadOutlined />} onClick={loadRules} disabled={!selectedGroupId}>刷新</Button>
        {selectedGroupId && (
          <>
            <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}><Button
              type="primary" icon={<PlusOutlined />}
              onClick={() => { setEditing(null); setModalOpen(true) }}
            >
              新增规则
            </Button></PermissionGuard>
            <Divider type="vertical" />
            <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => handleBatchToggle(true)}
              disabled={selectedRowKeys.length === 0}
            >
              批量启用
            </Button>
            </PermissionGuard>
            <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Button
              size="small"
              onClick={() => handleBatchToggle(false)}
              disabled={selectedRowKeys.length === 0}
            >
              批量停用
            </Button>
            </PermissionGuard>
            {selectedRowKeys.length > 0 && (
              <Text type="secondary">已选 {selectedRowKeys.length} 条</Text>
            )}
          </>
        )}
      </Space>

      {selectedGroup && (
        <Card size="small" style={{ marginBottom: 12, background: '#f6f8fa' }}>
          <Space>
            <AppstoreOutlined />
            <Text strong>{selectedGroup.name}</Text>
            <Tag color="blue">{DB_TYPE_LABELS[selectedGroup.db_type] || selectedGroup.db_type}</Tag>
            {selectedGroup.is_default && <Tag color="green">默认模板</Tag>}
            <Text type="secondary">{selectedGroup.description || ''}</Text>
          </Space>
        </Card>
      )}

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rules}
        columns={columns}
        pagination={false}
        bordered
        rowSelection={selectedGroupId ? rowSelection : undefined}
        locale={{ emptyText: selectedGroupId ? '该模板组暂无规则，请点击「新增规则」添加' : '请先选择一个模板组' }}
      />

      <RuleModal
        open={modalOpen}
        initial={editing}
        dbType={selectedGroup?.db_type || dbType}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
      />
    </>
  )
}

// ─────────────────────────────────────────────
// Tab 3：数据库分配与覆盖配置
// ─────────────────────────────────────────────
function AssignmentTab() {
  const [databases, setDatabases] = useState([])
  const [selectedDb, setSelectedDb] = useState(null)
  const [templateGroups, setTemplateGroups] = useState([])
  const [assignedTemplate, setAssignedTemplate] = useState(null)
  const [rows, setRows] = useState([])
  const [dbName, setDbName] = useState('')
  const [loading, setLoading] = useState(false)
  const [assignLoading, setAssignLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRow, setEditingRow] = useState(null)

  // 加载数据库列表
  useEffect(() => {
    databaseAPI.list().then(res => {
      const dbs = res.databases || []
      setDatabases(dbs)
      if (dbs.length > 0 && !selectedDb) setSelectedDb(dbs[0].id)
    }).catch(() => {})
  }, [])

  // 当选中的数据库变化时，加载对应类型的模板组
  useEffect(() => {
    const db = databases.find(d => d.id === selectedDb)
    if (db) {
      alertTemplateAPI.list({ db_type: db.db_type }).then(res => {
        setTemplateGroups(res.templates || [])
      }).catch(() => setTemplateGroups([]))
    }
  }, [selectedDb, databases])

  const loadOverrides = useCallback(async () => {
    if (!selectedDb) return
    setLoading(true)
    try {
      const res = await alertRuleAPI.listOverrides(selectedDb)
      setRows(res.rows || [])
      setDbName(res.db_name || '')
      setAssignedTemplate(res.assigned_template || null)
    } catch (e) {
      message.error('加载覆盖配置失败')
    } finally {
      setLoading(false)
    }
  }, [selectedDb])

  useEffect(() => { loadOverrides() }, [loadOverrides])

  const handleAssignTemplate = async (templateId) => {
    setAssignLoading(true)
    try {
      if (templateId === '__none__' || !templateId) {
        await alertTemplateAPI.assignTemplate(selectedDb, null)
        message.success('已取消模板分配（将使用默认模板）')
      } else {
        await alertTemplateAPI.assignTemplate(selectedDb, templateId)
        message.success('模板已分配')
      }
      loadOverrides()
    } catch (e) {
      message.error(e?.response?.data?.error || '分配失败')
    } finally {
      setAssignLoading(false)
    }
  }

  const handleSave = async (values) => {
    try {
      await alertRuleAPI.saveOverride(selectedDb, values)
      message.success('覆盖配置已保存')
      setModalOpen(false)
      loadOverrides()
    } catch (e) {
      message.error(e?.response?.data?.error || '保存失败')
    }
  }

  const handleReset = async (metricKey) => {
    try {
      await alertRuleAPI.deleteOverride(selectedDb, metricKey)
      message.success('已恢复模板默认值')
      loadOverrides()
    } catch (e) {
      message.error('重置失败')
    }
  }

  const selectedDbInfo = databases.find(d => d.id === selectedDb)
  const defaultTgForType = templateGroups.find(g => g.is_default)

  const columns = [
    {
      title: '指标',
      dataIndex: 'display_name',
      width: 160,
      render: (text, r) => (
        <Space direction="vertical" size={0}>
          <Text strong>{text}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.metric_key}</Text>
        </Space>
      ),
    },
    {
      title: '模板默认值',
      render: (_, r) => r.template
        ? <ThresholdBadges row={r} isOverride={false} />
        : <Text type="secondary">无模板</Text>,
    },
    {
      title: '当前库覆盖值',
      render: (_, r) => r.override
        ? <ThresholdBadges row={r} isOverride={true} />
        : <Text type="secondary" style={{ fontSize: 12 }}>未覆盖，沿用模板</Text>,
    },
    {
      title: '生效规则',
      render: (_, r) => {
        const eff = r.effective
        if (!eff) return <Text type="secondary">—</Text>
        return (
          <Space size={4}>
            <Tag color={eff.source === 'override' ? 'purple' : 'default'}>
              {eff.source === 'override' ? '已覆盖' : '模板'}
            </Tag>
            <Tag>{RULE_TYPE_LABELS[eff.rule_type] || eff.rule_type}</Tag>
            <Tag color="blue">{DIRECTION_LABELS[eff.direction] || eff.direction}</Tag>
          </Space>
        )
      },
    },
    {
      title: '操作',
      width: 150,
      render: (_, r) => (
        <Space>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            <Button
              size="small" icon={<EditOutlined />}
              onClick={() => { setEditingRow(r); setModalOpen(true) }}
            >覆盖</Button>
          </PermissionGuard>
          <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
            {r.override && (
              <Popconfirm title="删除覆盖配置后将恢复模板默认值" onConfirm={() => handleReset(r.metric_key)}>
                <Button size="small" icon={<DeleteOutlined />}>重置</Button>
              </Popconfirm>
            )}
          </PermissionGuard>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          showSearch
          placeholder="选择数据库"
          value={selectedDb}
          onChange={setSelectedDb}
          style={{ width: 260 }}
          optionFilterProp="label"
          options={databases.map(d => ({
            value: d.id,
            label: `${d.name} (${DB_TYPE_LABELS[d.db_type] || d.db_type})`,
          }))}
        />
        {selectedDbInfo && (
          <>
            <Divider type="vertical" />
            <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
              <Text strong>分配模板：</Text>
              <Select
                value={assignedTemplate?.id || (defaultTgForType?.id || '__none__')}
                onChange={handleAssignTemplate}
                loading={assignLoading}
                style={{ width: 280 }}
                placeholder="选择模板组"
                options={[
                  ...(defaultTgForType
                    ? [{ value: defaultTgForType.id, label: `${defaultTgForType.name} (默认)` }]
                    : []),
                  ...templateGroups
                    .filter(g => !g.is_default)
                    .map(g => ({ value: g.id, label: g.name })),
                ]}
              />
            </PermissionGuard>
            <PermissionGuard code={Perm.ALERT_CONFIG_MANAGE}>
              <Tooltip title="选择「无」则使用同类型的默认模板">
                <Button size="small" onClick={() => handleAssignTemplate(null)}>恢复默认</Button>
              </Tooltip>
            </PermissionGuard>
          </>
        )}
        <Button icon={<ReloadOutlined />} onClick={loadOverrides}>刷新</Button>
        {dbName && <Text type="secondary"><DatabaseOutlined /> {dbName}</Text>}
      </Space>

      {assignedTemplate && (
        <Card size="small" style={{ marginBottom: 12, background: '#e6f7ff' }}>
          <Space>
            <SwapOutlined />
            <Text>
              当前使用模板组：<Text strong>{assignedTemplate.name}</Text>
              <Tag style={{ marginLeft: 8 }}>{DB_TYPE_LABELS[assignedTemplate.db_type] || assignedTemplate.db_type}</Tag>
              {assignedTemplate.is_default && <Tag color="blue">默认</Tag>}
            </Text>
            <Text type="secondary">（{assignedTemplate.rule_count || 0} 条规则）</Text>
          </Space>
        </Card>
      )}

      <Table
        rowKey="metric_key"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={false}
        bordered
        rowClassName={r => r.override ? 'row-overridden' : ''}
      />

      <OverrideModal
        open={modalOpen}
        initial={editingRow}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
      />
    </>
  )
}

// ─────────────────────────────────────────────
// 主页面
// ─────────────────────────────────────────────
export default function AlertConfig() {
  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>
        <SettingOutlined style={{ marginRight: 8 }} />
        告警阈值配置
      </Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 20 }}>
        多层告警配置体系：创建「模板组」→ 在模板组中配置「规则」→ 为数据库「分配」模板组并可按需「覆盖」个别指标
      </Text>

      <style>{`
        .row-overridden td { background: #fffbe6 !important; }
      `}</style>

      <Tabs
        defaultActiveKey="groups"
        items={[
          {
            key: 'groups',
            label: <span><AppstoreOutlined /> 模板组管理</span>,
            children: <TemplateGroupTab />,
          },
          {
            key: 'rules',
            label: <span><UnorderedListOutlined /> 规则配置</span>,
            children: <RuleConfigTab />,
          },
          {
            key: 'assignment',
            label: <span><SwapOutlined /> 数据库分配与覆盖</span>,
            children: <AssignmentTab />,
          },
        ]}
      />
    </div>
  )
}

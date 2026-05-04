import React, { useState, useEffect, useCallback } from 'react'
import {
  Tabs, Table, Select, Tag, Button, Modal, Form, Input,
  InputNumber, Switch, Space, Typography, Tooltip, Popconfirm,
  message, Badge, Divider, Row, Col, Card
} from 'antd'
import {
  EditOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined,
  SettingOutlined, InfoCircleOutlined, DatabaseOutlined
} from '@ant-design/icons'
import { alertRuleAPI, databaseAPI } from '../services/api'
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
// 模板编辑弹窗
// ─────────────────────────────────────────────
function TemplateModal({ open, initial, dbType: parentDbType, onOk, onCancel }) {
  const [form] = Form.useForm()
  const [ruleType, setRuleType] = useState('threshold')
  const [availableMetrics, setAvailableMetrics] = useState([])
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [selectedDbType, setSelectedDbType] = useState(parentDbType || 'oracle')

  // 加载该类型可用指标
  const loadMetrics = useCallback(async (dbType) => {
    if (!dbType) return
    setMetricsLoading(true)
    try {
      const res = await alertRuleAPI.listAvailableMetrics(dbType)
      setAvailableMetrics(res.metrics || [])
    } catch (_) {
      setAvailableMetrics([])
    } finally {
      setMetricsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      const dt = parentDbType || 'oracle'
      setSelectedDbType(dt)
      form.setFieldsValue(initial || { rule_type: 'threshold', direction: 'up', is_enabled: true })
      setRuleType(initial?.rule_type || 'threshold')
      if (!initial?.id) loadMetrics(dt)
    }
  }, [open, initial, form, parentDbType, loadMetrics])

  const handleDbTypeChange = (val) => {
    setSelectedDbType(val)
    form.setFieldValue('metric_key', undefined)
    form.setFieldValue('display_name', '')
    loadMetrics(val)
  }

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
      title={initial?.id ? '编辑告警模板' : '新增告警模板'}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      width={600}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        {!initial?.id && (
          <>
            <Form.Item name="db_type" label="数据库类型" rules={[{ required: true }]}
              initialValue={parentDbType}>
              <Select onChange={handleDbTypeChange}>
                {DB_TYPES.map(t => <Option key={t} value={t}>{DB_TYPE_LABELS[t]}</Option>)}
              </Select>
            </Form.Item>
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
                    ? `${m.metric_key}（${m.display_name}）${m.has_template ? ' ✓已有模板' : ''}`
                    : `${m.metric_key}${m.has_template ? ' ✓已有模板' : ''}`,
                  disabled: m.has_template,
                }))}
              />
            </Form.Item>
            {availableMetrics.length > 0 && (
              <div style={{ marginTop: -12, marginBottom: 12, fontSize: 12, color: '#888' }}>
                共 {availableMetrics.filter(m => !m.has_template).length} 个未配置指标可选，
                ✓ 标记的已有模板
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
// Tab 1：类型模板管理
// ─────────────────────────────────────────────
function TemplateTab() {
  const [dbType, setDbType] = useState('oracle')
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await alertRuleAPI.listTemplates(dbType)
      setTemplates(res.templates || [])
    } catch (e) {
      message.error('加载失败')
    } finally {
      setLoading(false)
    }
  }, [dbType])

  useEffect(() => { load() }, [load])

  const handleSave = async (values) => {
    try {
      if (editing?.id) {
        await alertRuleAPI.updateTemplate(editing.id, values)
        message.success('已更新')
      } else {
        await alertRuleAPI.createTemplate({ db_type: dbType, ...values })
        message.success('已创建')
      }
      setModalOpen(false)
      load()
    } catch (e) {
      message.error(e?.response?.data?.error || '保存失败')
    }
  }

  const handleDelete = async (id) => {
    try {
      await alertRuleAPI.deleteTemplate(id)
      message.success('已删除')
      load()
    } catch (e) {
      message.error('删除失败')
    }
  }

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
      title: '操作',
      width: 120,
      render: (_, r) => (
        <Space>
          <Button
            size="small" icon={<EditOutlined />}
            onClick={() => { setEditing(r); setModalOpen(true) }}
          >编辑</Button>
          <Popconfirm title="确认删除此模板？" onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
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
        <Button
          type="primary" icon={<PlusOutlined />}
          onClick={() => { setEditing(null); setModalOpen(true) }}
        >
          新增指标模板
        </Button>
        <Text type="secondary">
          <InfoCircleOutlined /> 模板为同类型所有数据库的默认告警规则，可在「数据库覆盖」中针对单库调整
        </Text>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={templates}
        columns={columns}
        pagination={false}
        bordered
      />

      <TemplateModal
        open={modalOpen}
        initial={editing}
        dbType={dbType}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
      />
    </>
  )
}

// ─────────────────────────────────────────────
// Tab 2：数据库覆盖配置
// ─────────────────────────────────────────────
function OverrideTab() {
  const [databases, setDatabases] = useState([])
  const [selectedDb, setSelectedDb] = useState(null)
  const [rows, setRows] = useState([])
  const [dbName, setDbName] = useState('')
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRow, setEditingRow] = useState(null)

  useEffect(() => {
    databaseAPI.list().then(res => {
      const dbs = res.databases || []
      setDatabases(dbs)
      if (dbs.length > 0 && !selectedDb) setSelectedDb(dbs[0].id)
    }).catch(() => {})
  }, [])

  const loadOverrides = useCallback(async () => {
    if (!selectedDb) return
    setLoading(true)
    try {
      const res = await alertRuleAPI.listOverrides(selectedDb)
      setRows(res.rows || [])
      setDbName(res.db_name || '')
    } catch (e) {
      message.error('加载覆盖配置失败')
    } finally {
      setLoading(false)
    }
  }, [selectedDb])

  useEffect(() => { loadOverrides() }, [loadOverrides])

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
          <Button
            size="small" icon={<EditOutlined />}
            onClick={() => { setEditingRow(r); setModalOpen(true) }}
          >覆盖</Button>
          {r.override && (
            <Popconfirm title="删除覆盖配置后将恢复模板默认值" onConfirm={() => handleReset(r.metric_key)}>
              <Button size="small" icon={<DeleteOutlined />}>重置</Button>
            </Popconfirm>
          )}
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
        <Button icon={<ReloadOutlined />} onClick={loadOverrides}>刷新</Button>
        {dbName && <Text type="secondary"><DatabaseOutlined /> {dbName}</Text>}
      </Space>

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
        在「类型模板」中为每种数据库设置统一的多级告警规则；在「数据库覆盖」中针对单个数据库进行个性化调整，覆盖配置优先级高于模板。
      </Text>

      <style>{`
        .row-overridden td { background: #fffbe6 !important; }
      `}</style>

      <Tabs
        defaultActiveKey="templates"
        items={[
          {
            key: 'templates',
            label: '类型模板',
            children: <TemplateTab />,
          },
          {
            key: 'overrides',
            label: '数据库覆盖',
            children: <OverrideTab />,
          },
        ]}
      />
    </div>
  )
}

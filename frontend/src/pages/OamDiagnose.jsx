import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Card, Form, Input, Select, Button, Space, Tag, Alert, Descriptions,
  Table, Spin, message, Typography, Collapse
} from 'antd'
import {
  RobotOutlined, ReloadOutlined, ThunderboltOutlined, LinkOutlined
} from '@ant-design/icons'
import { oamAPI, databaseAPI } from '../services/api'
import {
  reportState, verdictState, auditState, fixApprovalState, describeError
} from '../utils/oamSemantics'

const { TextArea } = Input
const { Text, Paragraph } = Typography
const { Panel } = Collapse

// 取值与后端 OamCase.CATEGORY / OamCase.DIFFICULTY 一一对应，
// 写错会被后端以 400 BAD_REQUEST 挡回（枚举闸在入库前）
const CATEGORY_OPTIONS = [
  { value: 'inspection', label: '日常巡检' },
  { value: 'tuning', label: '查询性能调优' },
  { value: 'overload', label: '系统过载诊断' },
  { value: 'consultation', label: '数据库咨询' },
]
const DIFFICULTY_OPTIONS = [
  { value: 'easy', label: '易' },
  { value: 'hard', label: '难' },
]

/**
 * OAM 智能诊断（V3.0 设计 §6）
 *
 * 两条硬约束在这一页的体现：
 *   1. **只给建议，绝不执行变更** —— 推荐修复挂"需人工审批"标记，
 *      执行入口指向既有 `/api/v2/playbooks/execute-safely/`，本页不提供任何执行按钮；
 *   2. `status === 'evidence_insufficient'` 是**合法终止**，用 warning 呈现，
 *      且**不把空根因当结论**。
 *
 * 所有状态 → 展示语义的映射都在 `utils/oamSemantics.js` 里（可单测），
 * 本文件只做渲染，不做判断。
 */
const OamDiagnose = () => {
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [databases, setDatabases] = useState([])
  const [dbLoading, setDbLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [report, setReport] = useState(null)
  const [trajId, setTrajId] = useState('')
  const [err, setErr] = useState(null)
  const [toolCatalog, setToolCatalog] = useState(null)
  const [toolsErr, setToolsErr] = useState(null)

  const loadDatabases = useCallback(async () => {
    setDbLoading(true)
    try {
      const res = await databaseAPI.list()
      setDatabases(res?.databases || [])
    } catch (e) {
      message.error('加载实例列表失败: ' + e.message)
    } finally {
      setDbLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDatabases()
  }, [loadDatabases])

  // 工具目录只读展示（后端 GET /api/v3/oam/tools/，权限码 metrics.view）
  const loadTools = useCallback(async (dbType) => {
    if (!dbType) return
    setToolsErr(null)
    try {
      const res = await oamAPI.tools({ db_type: dbType })
      setToolCatalog(res?.data || null)
    } catch (e) {
      setToolCatalog(null)
      setToolsErr(describeError(e?.code, e?.status))
    }
  }, [])

  const handleSubmit = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch (_) {
      return
    }
    setSubmitting(true)
    setErr(null)
    setReport(null)
    setTrajId('')
    try {
      const res = await oamAPI.diagnose({
        config_id: values.config_id,
        symptom: values.symptom,
        category: values.category,
        difficulty: values.difficulty,
      })
      // 响应拦截器已 return response.data —— 拿到的就是信封本体，
      // 要再取一层 data 才是业务体
      const body = res?.data || {}
      setReport(body.report || {})
      setTrajId(body.traj_id || '')
      const st = reportState(body.report?.status, body.report?.escalated)
      if (st.kind === 'success') message.success('诊断完成')
      else if (st.kind === 'warning') message.warning(st.text)
      else message.info(st.text)
    } catch (e) {
      setErr(describeError(e?.code, e?.status))
    } finally {
      setSubmitting(false)
    }
  }

  // 报告语义：全部交给纯函数算，本文件只渲染
  const state = reportState(report?.status, report?.escalated)
  const verdict = verdictState(report?.verdict?.passed)
  const audit = auditState(report?.audit)
  const fix = fixApprovalState(report?.recommended_fix)
  const unsupported = Array.isArray(report?.unsupported_claims) ? report.unsupported_claims : []

  const claimColumns = [
    { title: '类型', dataIndex: 'kind', key: 'kind', width: 140, render: (k) => <Tag>{k || '-'}</Tag> },
    { title: '无证据支撑的主张', dataIndex: 'text', key: 'text' },
    {
      title: '证据引用', dataIndex: 'evidence_refs', key: 'refs', width: 200,
      render: (refs) => ((refs || []).length
        ? <Space size={4} wrap>{(refs || []).map((r, i) => <Tag key={i} color="blue">{r}</Tag>)}</Space>
        : <Text type="secondary">无</Text>),
    },
  ]

  const toolColumns = [
    { title: '工具', dataIndex: 'name', key: 'name', width: 180 },
    { title: '类别', dataIndex: 'kind', key: 'kind', width: 120, render: (k) => <Tag>{k}</Tag> },
    { title: '语义增强描述', dataIndex: 'description', key: 'description' },
    {
      title: '可回放', dataIndex: 'replayable', key: 'replayable', width: 90,
      render: (v) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}><RobotOutlined /> OAM 智能诊断</h2>
        <Button icon={<ReloadOutlined />} onClick={loadDatabases}>刷新实例</Button>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="本页只产出诊断建议，不执行任何变更"
        description="推荐修复一律带「需人工审批」标记；确需执行请走既有审批链 /api/v2/playbooks/execute-safely/。变更类工具不在策略动作空间内，调用一律返回 MUTATION_FORBIDDEN。"
      />

      <Card size="small" title="发起诊断" style={{ marginBottom: 16 }}>
        <Spin spinning={dbLoading}>
          <Form form={form} layout="vertical" initialValues={{ category: 'inspection', difficulty: 'easy' }}>
            <Form.Item
              label="目标实例"
              name="config_id"
              rules={[{ required: true, message: '请选择实例' }]}
              extra="只能选择你数据范围内的实例；越权与不存在后端统一返回 404"
            >
              <Select
                showSearch
                placeholder="选择数据库实例"
                optionFilterProp="label"
                onChange={(v) => {
                  const cfg = databases.find((d) => d.id === v)
                  if (cfg) loadTools(cfg.db_type)
                }}
                options={databases.map((d) => ({
                  value: d.id,
                  label: `${d.name}（${d.db_type} · ${d.host}:${d.port}）`,
                }))}
              />
            </Form.Item>
            <Form.Item
              label="症状描述"
              name="symptom"
              rules={[{ required: true, message: '请描述症状' }]}
            >
              <TextArea rows={3} placeholder="例如：每天 10 点后活跃会话突增，Top SQL 集中在同一张订单表的全表扫描" />
            </Form.Item>
            <Space size={16} wrap>
              <Form.Item label="场景类别" name="category">
                <Select style={{ width: 180 }} options={CATEGORY_OPTIONS} />
              </Form.Item>
              <Form.Item label="难度" name="difficulty">
                <Select style={{ width: 120 }} options={DIFFICULTY_OPTIONS} />
              </Form.Item>
              <Form.Item label=" " colon={false}>
                <Button
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  loading={submitting}
                  onClick={handleSubmit}
                >
                  发起诊断
                </Button>
              </Form.Item>
            </Space>
          </Form>
        </Spin>
      </Card>

      {err && (
        <Alert
          type={err.kind}
          showIcon
          style={{ marginBottom: 16 }}
          message={err.text}
          description={err.ambiguous ? '系统不区分"不存在"与"无权访问"，这是有意为之。' : undefined}
        />
      )}

      {report && (
        <Card size="small" title="诊断报告" style={{ marginBottom: 16 }}>
          {/* 规则 1：合法终止用 warning，不渲染成报错 */}
          <Alert
            type={state.kind}
            showIcon
            style={{ marginBottom: 16 }}
            message={state.text}
            description={state.detail}
          />

          <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="轨迹 ID">
              {trajId ? (
                <a onClick={() => navigate(`/oam/trajectories/${trajId}`)}>
                  <LinkOutlined /> {trajId}
                </a>
              ) : <Text type="secondary">未回传</Text>}
            </Descriptions.Item>
            <Descriptions.Item label="是否转人工">
              <Tag color={state.escalated ? 'orange' : 'default'}>
                {state.escalated ? '已转人工复核' : '否'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="验证器判定">
              {/* 规则 2：三态。null 渲染成"未判定"，不是"未通过" */}
              <Tag color={verdict.color}>{verdict.text}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="证据审计">
              <Tag color={audit.color}>{audit.text}</Tag>
            </Descriptions.Item>
          </Descriptions>

          {state.legalStop ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="未给出根因"
              description={
                <>
                  <Paragraph style={{ marginBottom: 8 }}>
                    原因：{report.evidence_insufficient_reason || report.reason || '后端未回传原因'}
                  </Paragraph>
                  {unsupported.length > 0 && (
                    <Table
                      size="small"
                      rowKey={(r, i) => `${r.kind}-${i}`}
                      dataSource={unsupported}
                      columns={claimColumns}
                      pagination={false}
                    />
                  )}
                </>
              }
            />
          ) : (
            <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="根因">
                {report.root_cause ? <Text strong>{report.root_cause}</Text> : <Text type="secondary">未给出</Text>}
              </Descriptions.Item>
            </Descriptions>
          )}

          {/* 规则 5：只给建议 */}
          <Alert
            type={fix.kind}
            showIcon
            message={
              <Space>
                <span>{fix.text}</span>
                {fix.badge && <Tag color="orange">{fix.badge}</Tag>}
                {fix.riskLevel && <Tag>风险等级：{fix.riskLevel}</Tag>}
              </Space>
            }
            description={
              fix.present ? (
                <Space direction="vertical" size={4}>
                  <Text code>{fix.spec}</Text>
                  <Text type="secondary">
                    执行入口：<Text code>{fix.executeVia}</Text>
                    {fix.note ? `　${fix.note}` : ''}
                  </Text>
                </Space>
              ) : undefined
            }
          />
        </Card>
      )}

      {/* 工具目录（只读）。后端权限码是 metrics.view，与本页的 tickets.execute 不同，
          取不到就如实说取不到，不静默吞掉 */}
      {(toolCatalog || toolsErr) && (
        <Collapse style={{ marginBottom: 16 }}>
          <Panel key="tools" header="工具目录（只读）">
            {toolsErr ? (
              <Alert type={toolsErr.kind} showIcon message={toolsErr.text} />
            ) : (
              <>
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={toolCatalog?.note || '变更类工具不在策略动作空间内'}
                />
                <Table
                  size="small"
                  rowKey="name"
                  dataSource={toolCatalog?.tools || []}
                  columns={toolColumns}
                  pagination={false}
                />
              </>
            )}
          </Panel>
        </Collapse>
      )}
    </div>
  )
}

export default OamDiagnose

import React, { useState, useCallback } from 'react'
import {
  Card, Form, Select, Button, Space, Tag, Alert, Descriptions, Table,
  Statistic, Row, Col, Input, message, Spin, Progress, Empty, Typography
} from 'antd'
import {
  TrophyOutlined, PlayCircleOutlined, ReloadOutlined, WarningOutlined
} from '@ant-design/icons'
import { oamAPI } from '../services/api'
import { coverageState, coverageWarningText, describeError } from '../utils/oamSemantics'

const { Text, Paragraph } = Typography

// 取值与后端 OamCase.SPLIT / OamCase.CATEGORY / OamCase.DIFFICULTY 对齐
const SPLIT_OPTIONS = [
  { value: 'test', label: '测试集（默认）' },
  { value: 'train', label: '训练集' },
  { value: 'ood', label: '异分布留出集' },
]
const BACKEND_OPTIONS = [
  { value: 'prompted', label: 'prompted（默认）' },
  { value: 'scripted', label: 'scripted' },
]
const CATEGORY_OPTIONS = [
  { value: '', label: '全部类别' },
  { value: 'inspection', label: '日常巡检' },
  { value: 'tuning', label: '查询性能调优' },
  { value: 'overload', label: '系统过载诊断' },
  { value: 'consultation', label: '数据库咨询' },
]
const DIFFICULTY_OPTIONS = [
  { value: '', label: '全部难度' },
  { value: 'easy', label: '易' },
  { value: 'hard', label: '难' },
]

const pct = (v) => (typeof v === 'number' ? v * 100 : null)

/**
 * 评测成绩单（V3.0 设计 §6 / §8）
 *
 * 两条硬约束在这一页的体现：
 *   1. **成功率必须与覆盖度同屏**（设计 §8："评测报告里没有 coverage
 *      就不算完成评测"）。所以成绩卡片和覆盖度卡片是并排的一个 Row，
 *      缺口通过 `coverageState().warning` 显著挂在成绩旁边；
 *   2. 缺 coverage 时后端返回 HTTP 500 `COVERAGE_MISSING` —— 这是业务语义
 *      （按设计视为未完成评测），文案按 `describeError` 呈现，
 *      **不能**显示成"服务器错误 / 网络异常"。
 */
const OamBench = () => {
  const [form] = Form.useForm()
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState(null)
  const [runId, setRunId] = useState('')
  const [err, setErr] = useState(null)

  const loadReport = useCallback(async (id) => {
    if (!id) return
    setRunId(id)
    setLoading(true)
    setErr(null)
    try {
      const res = await oamAPI.benchReport(id)
      setReport(res?.data || null)
    } catch (e) {
      setReport(null)
      setErr(describeError(e?.code, e?.status))
    } finally {
      setLoading(false)
    }
  }, [])

  const handleRun = async () => {
    const values = form.getFieldsValue()
    setRunning(true)
    setErr(null)
    try {
      const res = await oamAPI.benchRun({
        split: values.split || 'test',
        policy_backend: values.policy_backend || 'prompted',
        category: values.category || undefined,
        difficulty: values.difficulty || undefined,
      })
      const body = res?.data || {}
      message.success('评测完成')
      // 直接用 run 接口同屏返回的成绩 + 覆盖度，不让用户干等一次回查
      setReport(body)
      setRunId(body.run_id || '')
    } catch (e) {
      setErr(describeError(e?.code, e?.status))
    } finally {
      setRunning(false)
    }
  }

  // 覆盖度语义：全部交给纯函数
  const cov = coverageState(report?.coverage)
  const warning = coverageWarningText(report?.coverage)
  const perCategory = report?.per_category && typeof report.per_category === 'object'
    ? report.per_category
    : null
  const detail = report?.detail || {}

  const categoryColumns = [
    { title: '类别', dataIndex: 'category', key: 'category', width: 160 },
    {
      title: '成功率', dataIndex: 'success_rate', key: 'success_rate', width: 200,
      render: (v) => {
        const p = pct(v)
        return p == null ? <Text type="secondary">-</Text> : <Progress percent={p} size="small" />
      },
    },
    { title: '用例数', dataIndex: 'total', key: 'total', width: 100 },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}><TrophyOutlined /> 评测成绩单</h2>
        <Space>
          <Input.Search
            placeholder="回查 run_id"
            style={{ width: 320 }}
            enterButton="查询"
            onSearch={(v) => loadReport(v.trim())}
          />
          <Button icon={<ReloadOutlined />} disabled={!runId} onClick={() => loadReport(runId)}>刷新</Button>
        </Space>
      </div>

      <Card size="small" title="发起评测" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" initialValues={{ split: 'test', policy_backend: 'prompted' }}>
          <Form.Item label="数据切分" name="split">
            <Select style={{ width: 170 }} options={SPLIT_OPTIONS} />
          </Form.Item>
          <Form.Item label="策略后端" name="policy_backend">
            <Select style={{ width: 170 }} options={BACKEND_OPTIONS} />
          </Form.Item>
          <Form.Item label="类别" name="category">
            <Select style={{ width: 160 }} options={CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item label="难度" name="difficulty">
            <Select style={{ width: 120 }} options={DIFFICULTY_OPTIONS} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={running} onClick={handleRun}>
              跑评测
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {err && (
        <Alert
          type={err.kind}
          showIcon
          style={{ marginBottom: 16 }}
          message={err.text}
          description={err.ambiguous ? '系统不区分「不存在」与「无权访问」，这是有意为之。' : undefined}
        />
      )}

      <Spin spinning={loading || running}>
        {!report && !err && <Empty description="先跑一次评测，或用 run_id 回查历史成绩" />}

        {report && (
          <>
            {/* 规则 6：成绩与覆盖度同屏，一个 Row 里并排 */}
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={10}>
                <Card size="small" title="成绩">
                  <Statistic title="成功率（按类别宏平均）" value={pct(report.success_rate) ?? 0}
                    suffix="%" precision={2} />
                  <Row gutter={8} style={{ marginTop: 12 }}>
                    <Col span={8}><Statistic title="用例数" value={report.total || 0} /></Col>
                    <Col span={8}>
                      <Statistic title="幻觉率" value={pct(report.hallucination_rate) ?? 0} suffix="%" precision={2} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="转人工" value={report.escalated_count || 0} />
                    </Col>
                  </Row>
                  <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}>
                    加权成功率 {pct(report.success_rate_weighted) == null ? '-' : `${pct(report.success_rate_weighted).toFixed(2)}%`}
                    　·　平均轮次 {report.avg_turns ?? '-'}　·　平均工具调用 {report.avg_tool_calls ?? '-'}
                  </Paragraph>
                </Card>
              </Col>

              <Col span={14}>
                <Card size="small" title="覆盖度（与成绩同屏，缺了就不算完成评测）">
                  <Alert
                    type={cov.kind}
                    showIcon
                    message={cov.text}
                    style={{ marginBottom: 12 }}
                  />
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item label="标签空间">{cov.labelSpaceSize || 0}</Descriptions.Item>
                    <Descriptions.Item label="已覆盖">{cov.covered}</Descriptions.Item>
                    <Descriptions.Item label="未覆盖">
                      <Text type={cov.missing ? 'danger' : undefined}>{cov.missing}</Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="覆盖比率">
                      {cov.ratio == null ? '-' : `${(cov.ratio * 100).toFixed(1)}%`}
                    </Descriptions.Item>
                  </Descriptions>
                  {report.coverage?.db_types && (
                    <Space size={4} wrap style={{ marginTop: 8 }}>
                      {report.coverage.db_types.map((t) => <Tag key={t}>{t}</Tag>)}
                    </Space>
                  )}
                  {warning && (
                    <Paragraph style={{ marginTop: 12, marginBottom: 0 }}>
                      <WarningOutlined style={{ color: '#faad14', marginRight: 6 }} />
                      <Text strong>{warning}</Text>
                    </Paragraph>
                  )}
                  {report.coverage?.capability_boundary && (
                    <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                      能力边界：{report.coverage.capability_boundary}
                    </Paragraph>
                  )}
                </Card>
              </Col>
            </Row>

            <Descriptions size="small" column={3} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="run_id">
                <Text copyable>{report.run_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={report.status === 'done' ? 'green' : 'blue'}>{report.status || '-'}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="切分/策略">
                {report.split || '-'} / {report.policy_backend || '-'}{report.policy_version ? ` ${report.policy_version}` : ''}
              </Descriptions.Item>
              <Descriptions.Item label="开始">{report.started_at || '-'}</Descriptions.Item>
              <Descriptions.Item label="结束">{report.finished_at || '-'}</Descriptions.Item>
              <Descriptions.Item label="类别/难度">
                {report.category || '全部'} / {report.difficulty || '全部'}
              </Descriptions.Item>
            </Descriptions>

            {perCategory && Object.keys(perCategory).length > 0 && (
              <Card size="small" title="分类别成绩" style={{ marginBottom: 16 }}>
                <Table
                  size="small"
                  rowKey="category"
                  dataSource={Object.entries(perCategory).map(([category, v]) => ({
                    category,
                    ...(typeof v === 'object' ? v : { success_rate: v }),
                  }))}
                  columns={categoryColumns}
                  pagination={false}
                />
              </Card>
            )}

            {detail.note && (
              <Alert
                type="info"
                showIcon
                message={detail.note}
                description={
                  detail.escalated_is_legal_stop
                    ? '证据不足为合法终止，不计成功也不计幻觉 —— 因此"转人工"不是扣分项。'
                    : undefined
                }
              />
            )}
          </>
        )}
      </Spin>
    </div>
  )
}

export default OamBench

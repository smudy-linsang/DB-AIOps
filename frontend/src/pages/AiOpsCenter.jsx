import React, { useState, useEffect, useCallback } from 'react'
import {
  Card, Row, Col, Statistic, Table, Tag, Button, Space, message, Select, Alert, Tooltip,
} from 'antd'
import {
  RobotOutlined, ReloadOutlined, ApiOutlined, LikeOutlined,
  DatabaseOutlined, ExperimentOutlined,
} from '@ant-design/icons'
import { aiOpsAPI } from '../services/api'
import { hasPermission, Perm } from '../utils/permission'

const SCENE_TAG = {
  diagnosis: { color: 'blue', text: '事故诊断' }, plan_draft: { color: 'cyan', text: '方案草拟' },
  distill: { color: 'green', text: '案例蒸馏' }, agent: { color: 'purple', text: '主动排查' },
  embed: { color: 'default', text: '向量化' }, test: { color: 'default', text: '连通测试' },
}
const STATUS_TAG = {
  ok: 'green', timeout: 'orange', unavailable: 'red', bad_json: 'volcano', error: 'red',
}

export default function AiOpsCenter() {
  const [stats, setStats] = useState(null)
  const [days, setDays] = useState(7)
  const [calls, setCalls] = useState({ items: [], total: 0 })
  const [callsPage, setCallsPage] = useState(1)
  const [callsScene, setCallsScene] = useState('')
  const [ruleStats, setRuleStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const canViewCalls = hasPermission(Perm.AUDIT_LOGS_VIEW)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await aiOpsAPI.stats({ days })
      setStats(s)
      const rs = await aiOpsAPI.ruleStats()
      setRuleStats(rs.items || [])
      if (canViewCalls) {
        const c = await aiOpsAPI.llmCalls({ page: callsPage, page_size: 20, scene: callsScene || undefined })
        setCalls({ items: c.items || [], total: c.total || 0 })
      }
    } catch (e) {
      message.error('加载 AI 运营数据失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [days, callsPage, callsScene, canViewCalls])

  useEffect(() => { load() }, [load])

  const doTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const r = await aiOpsAPI.testLlm()
      setTestResult(r)
    } catch (e) {
      message.error('测试失败: ' + e.message)
    } finally {
      setTesting(false)
    }
  }

  const llm = stats?.llm || {}
  const rca = stats?.rca || {}
  const plans = stats?.plans || {}
  const cases = stats?.cases || {}
  const agent = stats?.agent || {}

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <h2 style={{ margin: 0 }}><RobotOutlined /> AI 运营中心</h2>
          <Select value={days} onChange={setDays} style={{ width: 110 }}
            options={[{ value: 7, label: '近 7 天' }, { value: 14, label: '近 14 天' }, { value: 30, label: '近 30 天' }]} />
        </Space>
        <Space>
          <Button icon={<ApiOutlined />} loading={testing} onClick={doTest}>LLM 连通测试</Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      </div>

      {testResult && (
        <Alert style={{ marginBottom: 16 }} showIcon closable onClose={() => setTestResult(null)}
          type={testResult.ok ? 'success' : 'error'}
          message={testResult.ok
            ? `LLM 连通正常: ${testResult.model} · ${testResult.latency_ms}ms` +
              (testResult.embed_ok ? ` · 向量模型正常(dim=${testResult.embed_dim})` : ' · 向量模型未启用/异常')
            : `LLM 连通失败: ${testResult.error || '未知错误'}`} />
      )}

      {/* 五段核心指标 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={5}>
          <Card size="small" loading={loading}>
            <Statistic title="LLM 调用" value={llm.calls || 0} prefix={<RobotOutlined />} />
            <div style={{ fontSize: 12, color: '#888' }}>
              错误率 {((llm.error_rate || 0) * 100).toFixed(1)}% · 平均 {llm.avg_latency_ms || 0}ms
            </div>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small" loading={loading}>
            <Statistic title="根因反馈准确率" prefix={<LikeOutlined />}
              value={((rca.accuracy || 0) * 100).toFixed(1)} suffix="%" />
            <div style={{ fontSize: 12, color: '#888' }}>反馈 {rca.feedback_total || 0} 条</div>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small" loading={loading}>
            <Statistic title="方案采纳率"
              value={((plans.adopt_rate || 0) * 100).toFixed(1)} suffix="%" />
            <div style={{ fontSize: 12, color: '#888' }}>反馈 {plans.feedback_total || 0} 条</div>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small" loading={loading}>
            <Statistic title="案例库" prefix={<DatabaseOutlined />}
              value={(cases.auto_distilled || 0) + (cases.manual || 0)} />
            <div style={{ fontSize: 12, color: '#888' }}>
              蒸馏 {cases.auto_distilled || 0} · 人工 {cases.manual || 0} · 已向量化 {cases.vector_indexed || 0}
            </div>
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small" loading={loading}>
            <Statistic title="Agent 排查" prefix={<ExperimentOutlined />} value={agent.runs || 0} />
            <div style={{ fontSize: 12, color: '#888' }}>
              完成 {agent.done || 0} · 平均 {agent.avg_steps || 0} 步
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={canViewCalls ? 14 : 24}>
          <Card title="规则准确率排行 (反馈校准)" size="small" style={{ marginBottom: 16 }}>
            <Table size="small" rowKey="rule_id" pagination={{ pageSize: 10 }} loading={loading}
              dataSource={ruleStats}
              columns={[
                { title: '规则', dataIndex: 'rule_id', width: 80, render: (v) => <Tag color="blue">{v}</Tag> },
                { title: '名称', dataIndex: 'rule_name', ellipsis: true },
                { title: '反馈样本', dataIndex: 'sample_count', width: 90 },
                {
                  title: '准确率', dataIndex: 'accuracy', width: 100,
                  render: (v) => <span style={{ color: v >= 0.7 ? '#3f8600' : v >= 0.4 ? '#d48806' : '#cf1322' }}>
                    {(v * 100).toFixed(1)}%</span>,
                },
                {
                  title: '校准置信度', dataIndex: 'calibrated_confidence', width: 110,
                  render: (v) => <Tooltip title="RCA 置信度按此值动态校准">{v}</Tooltip>,
                },
              ]} />
          </Card>
        </Col>
        {canViewCalls && (
          <Col span={10}>
            <Card size="small" style={{ marginBottom: 16 }}
              title={<Space>LLM 调用日志
                <Select allowClear placeholder="场景" size="small" style={{ width: 110 }}
                  value={callsScene || undefined}
                  onChange={(v) => { setCallsScene(v || ''); setCallsPage(1) }}
                  options={Object.entries(SCENE_TAG).map(([k, v]) => ({ value: k, label: v.text }))} />
              </Space>}>
              <Table size="small" rowKey="id" loading={loading}
                dataSource={calls.items}
                pagination={{
                  current: callsPage, pageSize: 20, total: calls.total,
                  onChange: setCallsPage, showSizeChanger: false,
                }}
                columns={[
                  {
                    title: '场景', dataIndex: 'scene', width: 90,
                    render: (v) => <Tag color={(SCENE_TAG[v] || {}).color}>{(SCENE_TAG[v] || {}).text || v}</Tag>,
                  },
                  {
                    title: '状态', dataIndex: 'status', width: 80,
                    render: (v) => <Tag color={STATUS_TAG[v] || 'default'}>{v}</Tag>,
                  },
                  { title: '耗时', dataIndex: 'latency_ms', width: 80, render: (v) => `${v}ms` },
                  {
                    title: 'Token', width: 100,
                    render: (_, r) => <span style={{ fontSize: 12 }}>{r.prompt_tokens}/{r.completion_tokens}</span>,
                  },
                  {
                    title: '时间', dataIndex: 'created_at', width: 150,
                    render: (v) => v ? new Date(v).toLocaleString('zh-CN') : '-',
                  },
                ]} />
            </Card>
          </Col>
        )}
      </Row>
    </div>
  )
}

import React, { useState, useEffect, useCallback } from 'react'
import {
  Card, Descriptions, Tag, Button, Space, Statistic, Row, Col, Timeline, Alert, message, Empty, Progress,
  Collapse, Tooltip,
} from 'antd'
import {
  ArrowLeftOutlined, ReloadOutlined, CheckOutlined, LikeOutlined, DislikeOutlined,
  RobotOutlined, ExperimentOutlined,
} from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import { incidentAPI } from '../services/api'

const PRIORITY_COLOR = { P1: 'red', P2: 'orange', P3: 'blue', P4: 'default' }
const STATUS_COLOR = {
  open: 'red', diagnosing: 'processing', plan_ready: 'gold',
  executing: 'purple', verifying: 'cyan', resolved: 'green', closed: 'default',
}
const SOURCE_TAG = {
  rule: { color: 'blue', text: '规则' },
  llm: { color: 'purple', text: 'AI' },
  both: { color: 'geekblue', text: '规则+AI' },
}
const TRACE_STATUS = {
  running: { color: 'processing', text: '排查中' }, done: { color: 'green', text: '完成' },
  failed: { color: 'red', text: '失败' }, budget_exceeded: { color: 'orange', text: '预算耗尽' },
}

function Stopwatch({ label, value, ok, budget }) {
  const color = value == null ? '#999' : (ok ? '#3f8600' : '#cf1322')
  return (
    <Card size="small">
      <Statistic title={`${label} (预算${budget}s)`} value={value == null ? '—' : value}
        suffix={value == null ? '' : 's'} valueStyle={{ color }} />
      {value != null && <Tag color={ok ? 'green' : 'red'} style={{ marginTop: 6 }}>{ok ? '达标' : '超标'}</Tag>}
    </Card>
  )
}

export default function IncidentDetail() {
  const { incidentId } = useParams()
  const navigate = useNavigate()
  const [inc, setInc] = useState(null)
  const [timeline, setTimeline] = useState([])
  const [loading, setLoading] = useState(false)
  const [traces, setTraces] = useState([])
  const [investigating, setInvestigating] = useState(false)

  const loadTraces = useCallback(async () => {
    try {
      const r = await incidentAPI.agentTrace(incidentId)
      setTraces(r.traces || [])
      return r.traces || []
    } catch { return [] }
  }, [incidentId])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await incidentAPI.detail(incidentId)
      setInc(d.incident || d)
      const t = await incidentAPI.timeline(incidentId)
      setTimeline(Array.isArray(t) ? t : (t.items || []))
      loadTraces()
    } catch (e) {
      message.error('加载事故详情失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [incidentId, loadTraces])

  useEffect(() => { load() }, [load])

  const doAck = async () => {
    try { await incidentAPI.ack(incidentId); message.success('已确认'); load() }
    catch (e) { message.error(e.message) }
  }
  const doClose = async () => {
    try { await incidentAPI.close(incidentId, '手动关闭'); message.success('已关闭'); load() }
    catch (e) { message.error(e.message) }
  }
  const doRediagnose = async () => {
    try { await incidentAPI.rediagnose(incidentId); message.success('已触发重新诊断'); setTimeout(load, 2000) }
    catch (e) { message.error(e.message) }
  }
  const doExecute = async (scenario) => {
    try {
      const r = await incidentAPI.execute(incidentId, { scenario })
      message.success(r.executing ? `执行中: ${r.reason}` : `已创建执行(待审批): ${r.reason}`)
      setTimeout(load, 3000)
    } catch (e) { message.error(e.message) }
  }

  // Phase 8B: 根因/方案反馈
  const doRcaFeedback = async (ruleId, verdict) => {
    try {
      await incidentAPI.rcaFeedback(incidentId, { rule_id: ruleId, verdict })
      message.success(verdict === 'correct' ? '已确认根因准确' : '已标记根因不准')
      load()
    } catch (e) { message.error(e.message) }
  }
  const doPlanFeedback = async (scenario, verdict) => {
    try {
      await incidentAPI.planFeedback(incidentId, { scenario, verdict })
      message.success('反馈已记录')
      load()
    } catch (e) { message.error(e.message) }
  }

  // Phase 8C: 深度排查 (启动后轮询轨迹直到结束)
  const doInvestigate = async () => {
    setInvestigating(true)
    try {
      await incidentAPI.investigate(incidentId)
      message.success('深度排查已启动')
      const poll = setInterval(async () => {
        const ts = await loadTraces()
        if (!ts.some((t) => t.status === 'running')) {
          clearInterval(poll)
          setInvestigating(false)
          load()
        }
      }, 5000)
      setTimeout(() => { clearInterval(poll); setInvestigating(false) }, 180000)
    } catch (e) {
      setInvestigating(false)
      message.error(e.message)
    }
  }

  if (!inc) return <div style={{ padding: 24 }}><Card loading={loading}>加载中...</Card></div>

  const rca = inc.rca_result || {}
  const impact = inc.impact || {}
  const plans = inc.plans || []
  const myFb = inc.my_feedback || { rca: {}, plan: {} }
  const agent = rca.agent || null
  const hasRunning = traces.some((t) => t.status === 'running')

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/incidents')}>返回</Button>
          <h2 style={{ margin: 0 }}>{inc.title}</h2>
          <Tag color={PRIORITY_COLOR[inc.priority]}>{inc.priority}</Tag>
          <Tag color={STATUS_COLOR[inc.status]}>{inc.status}</Tag>
          {inc.is_storm && <Tag color="volcano">事件风暴</Tag>}
        </Space>
        <Space>
          {!inc.acked_by && <Button icon={<CheckOutlined />} onClick={doAck}>确认</Button>}
          {['open', 'diagnosing', 'plan_ready'].includes(inc.status) &&
            <Button onClick={doRediagnose}>重新诊断</Button>}
          <Button icon={<ExperimentOutlined />} loading={investigating || hasRunning}
            onClick={doInvestigate} disabled={inc.status === 'closed'}>深度排查</Button>
          {inc.status !== 'closed' && <Button danger onClick={doClose}>关闭</Button>}
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      </div>

      {/* 1-5-10 秒表 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Stopwatch label="发现" value={inc.t_detect_sec} ok={inc.sla_detect_ok} budget={60} /></Col>
        <Col span={8}><Stopwatch label="方案" value={inc.t_plan_sec} ok={inc.sla_plan_ok} budget={300} /></Col>
        <Col span={8}><Stopwatch label="解决" value={inc.t_resolve_sec} ok={inc.sla_resolve_ok} budget={600} /></Col>
      </Row>

      {/* 元信息 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={3} size="small" bordered>
          <Descriptions.Item label="事故ID">{inc.incident_id}</Descriptions.Item>
          <Descriptions.Item label="数据库">{inc.db_name}</Descriptions.Item>
          <Descriptions.Item label="类别">{inc.category}</Descriptions.Item>
          <Descriptions.Item label="事件数">{inc.event_count}</Descriptions.Item>
          <Descriptions.Item label="确认人">{inc.acked_by || '-'}</Descriptions.Item>
          <Descriptions.Item label="健康快照">{inc.health_snapshot}</Descriptions.Item>
          <Descriptions.Item label="发生时间">{inc.occurred_at ? new Date(inc.occurred_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          <Descriptions.Item label="发现时间">{inc.detected_at ? new Date(inc.detected_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          <Descriptions.Item label="解决时间">{inc.resolved_at ? new Date(inc.resolved_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="事故时间线" size="small" style={{ marginBottom: 16 }}>
            <Timeline items={timeline.map((it) => ({
              color: it.kind === 'event' ? (it.severity === 'critical' ? 'red' : 'orange') : 'blue',
              children: (
                <div>
                  <b>{it.kind === 'event' ? `事件: ${it.signal} (${it.severity})` : '状态'}</b>
                  <div style={{ color: '#888', fontSize: 12 }}>{it.at ? new Date(it.at).toLocaleString('zh-CN') : ''}</div>
                  {it.text && <div>{it.text}</div>}
                  {it.detail && it.detail.max_wait_sec != null &&
                    <div style={{ fontSize: 12 }}>最长等待 {it.detail.max_wait_sec}s, 阻塞 {it.detail.waiters} 会话</div>}
                </div>
              ),
            }))} />
            {timeline.length === 0 && <Empty description="暂无时间线" />}
          </Card>
        </Col>
        <Col span={12}>
          <Card title="根因诊断" size="small" style={{ marginBottom: 16 }}>
            {rca.llm_summary && (
              <Alert type="info" showIcon icon={<RobotOutlined />} style={{ marginBottom: 12 }}
                message="AI 综合分析" description={rca.llm_summary} />
            )}
            {(rca.root_causes && rca.root_causes.length > 0)
              ? rca.root_causes.map((rc, i) => (
                <div key={i} style={{ marginBottom: 12, paddingBottom: 8, borderBottom: '1px dashed #eee' }}>
                  <Space wrap>
                    <b>{rc.name || rc.rule_id}</b>
                    <Tag color="blue">{rc.rule_id}</Tag>
                    {rc.source && SOURCE_TAG[rc.source] &&
                      <Tag color={SOURCE_TAG[rc.source].color}>{SOURCE_TAG[rc.source].text}</Tag>}
                    {rc.agent_confirmed && <Tag color="cyan">深挖确认</Tag>}
                    <span style={{ fontSize: 12 }}>置信度</span>
                    <Progress percent={Math.round((rc.confidence || 0) * 100)} size="small" style={{ width: 120 }} />
                    <Tooltip title="根因准确">
                      <Button size="small" icon={<LikeOutlined />}
                        type={myFb.rca[rc.rule_id] === 'correct' ? 'primary' : 'default'}
                        onClick={() => doRcaFeedback(rc.rule_id, 'correct')} />
                    </Tooltip>
                    <Tooltip title="根因不准">
                      <Button size="small" danger={myFb.rca[rc.rule_id] === 'wrong'} icon={<DislikeOutlined />}
                        onClick={() => doRcaFeedback(rc.rule_id, 'wrong')} />
                    </Tooltip>
                  </Space>
                  <div style={{ color: '#555', margin: '4px 0' }}>{rc.summary}</div>
                  {rc.reasoning && (
                    <div style={{ fontSize: 12, color: '#722ed1', margin: '4px 0' }}>
                      <RobotOutlined /> AI 推理: {rc.reasoning}
                    </div>)}
                  {rc.llm_dissent && (
                    <Alert type="warning" showIcon style={{ margin: '4px 0' }} banner
                      message={`AI 异议: ${rc.llm_dissent}`} />)}
                  {(rc.evidence || []).length > 0 && (
                    <div style={{ background: '#fafafa', padding: 8, borderRadius: 4 }}>
                      <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>证据链:</div>
                      {rc.evidence.map((ev, j) => (
                        <div key={j} style={{ fontSize: 12, marginBottom: 2 }}>
                          <Tag>{ev.type}</Tag>{ev.label}
                        </div>
                      ))}
                    </div>
                  )}
                  {(rc.suggestions || []).length > 0 &&
                    <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12 }}>
                      {rc.suggestions.map((s, k) => <li key={k}>{s}</li>)}
                    </ul>}
                </div>))
              : <Alert type="warning" showIcon message="诊断中" description="诊断管道处理后此处展示根因/证据链。" />}
            {(rca.similar_cases || []).length > 0 &&
              <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
                相似历史案例 {rca.similar_cases.length} 条</div>}
          </Card>
          {(agent || traces.length > 0) && (
            <Card size="small" style={{ marginBottom: 16 }}
              title={<Space><ExperimentOutlined />Agent 深度排查</Space>}>
              {agent && (
                <Alert type="success" showIcon style={{ marginBottom: 8 }}
                  message={<Space>排查结论 (置信度 {Math.round((agent.confidence || 0) * 100)}%)</Space>}
                  description={<div>
                    <div>{agent.root_cause}</div>
                    {agent.evidence_summary &&
                      <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>{agent.evidence_summary}</div>}
                    {(agent.suggestions || []).length > 0 &&
                      <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12 }}>
                        {agent.suggestions.map((s, i) => <li key={i}>{s}</li>)}
                      </ul>}
                  </div>} />
              )}
              <Collapse size="small" items={traces.map((t) => ({
                key: t.trace_id,
                label: <Space>
                  <span>{t.trace_id}</span>
                  <Tag color={(TRACE_STATUS[t.status] || {}).color}>{(TRACE_STATUS[t.status] || {}).text || t.status}</Tag>
                  <span style={{ fontSize: 12, color: '#888' }}>
                    {(t.steps || []).length} 步 · {t.started_at ? new Date(t.started_at).toLocaleString('zh-CN') : ''}
                  </span>
                </Space>,
                children: <Timeline items={(t.steps || []).map((s) => ({
                  color: s.action === 'final' ? 'green' : 'blue',
                  children: (
                    <div style={{ fontSize: 12 }}>
                      <div><b>思考:</b> {s.thought}</div>
                      <div><Tag>{s.action}</Tag>{s.elapsed_ms != null && <span style={{ color: '#888' }}>{s.elapsed_ms}ms</span>}</div>
                      {s.observation && (
                        <pre style={{ background: '#fafafa', padding: 6, borderRadius: 4, maxHeight: 120, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                          {String(s.observation).slice(0, 600)}
                        </pre>)}
                    </div>
                  ),
                }))} />,
              }))} />
            </Card>
          )}
          <Card title="影响评估" size="small" style={{ marginBottom: 16 }}>
            {impact.summary
              ? <div>
                <Space wrap>
                  <Tag color={impact.impact_level === 'high' ? 'red' : impact.impact_level === 'medium' ? 'orange' : 'green'}>
                    影响等级: {impact.impact_level}
                  </Tag>
                  <span>受影响会话: {impact.affected_sessions}</span>
                  <span>健康度: {impact.health_before} → {impact.health_now}</span>
                </Space>
                <div style={{ marginTop: 6 }}>{impact.summary}</div>
                {(impact.business_systems || []).map((b, i) =>
                  <Tag key={i} color="purple" style={{ marginTop: 4 }}>{b.name}({b.importance})</Tag>)}
              </div>
              : <Alert type="warning" showIcon message="诊断中" />}
          </Card>
          <Card title="处置方案" size="small">
            {plans.length > 0
              ? plans.map((p, i) => {
                const isAdvisory = p.scenario === 'llm_advisory' || p.plan_type === 'llm_advisory'
                return (
                <Card key={i} type="inner" size="small" style={{ marginBottom: 8 }}
                  title={<Space>
                    <Tag color={p.risk_level === 'high' ? 'red' : p.risk_level === 'mid' ? 'orange' : 'green'}>{p.risk_level}</Tag>
                    {p.name}
                    {isAdvisory && <Tag color="purple" icon={<RobotOutlined />}>AI 建议 · 不可执行</Tag>}
                  </Space>}
                  extra={<Space>
                    <Tooltip title="采纳且有效">
                      <Button size="small" icon={<LikeOutlined />}
                        type={myFb.plan[p.scenario] === 'adopted' ? 'primary' : 'default'}
                        onClick={() => doPlanFeedback(p.scenario, 'adopted')} />
                    </Tooltip>
                    <Tooltip title="没有帮助">
                      <Button size="small" danger={myFb.plan[p.scenario] === 'adopted_failed'} icon={<DislikeOutlined />}
                        onClick={() => doPlanFeedback(p.scenario, 'useless')} />
                    </Tooltip>
                    {!isAdvisory && <Button size="small" type="primary"
                      disabled={!['plan_ready', 'executing'].includes(inc.status) || !p.playbook_ref}
                      onClick={() => doExecute(p.scenario)}>执行</Button>}
                  </Space>}>
                  <div style={{ fontSize: 12, color: '#888' }}>
                    {isAdvisory
                      ? 'AI 生成的参考建议, 需人工确认后手动操作'
                      : <>预计 {p.est_minutes}min · {p.requires_approval ? '需审批' : '免审批'} · Playbook: {p.playbook_ref || '-'}</>}
                  </div>
                  <ol style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12 }}>
                    {(p.steps || []).map((s, j) => (
                      <li key={j}>{typeof s === 'string' ? s : s.desc}{s.sql ? <code style={{ display: 'block', color: '#c41d7f', fontSize: 11 }}>{s.sql}</code> : null}</li>
                    ))}
                  </ol>
                  {isAdvisory && (p.preventive || []).length > 0 && (
                    <div style={{ fontSize: 12, marginTop: 6 }}>
                      <span style={{ color: '#888' }}>预防建议: </span>
                      {p.preventive.map((s, j) => <div key={j} style={{ color: '#555' }}>· {typeof s === 'string' ? s : s.desc}</div>)}
                    </div>)}
                  {p.verify && p.verify.metric &&
                    <div style={{ fontSize: 12, color: '#389e0d', marginTop: 4 }}>
                      验证判据: {p.verify.metric} {p.verify.recover_expr} (观察{p.verify.window_sec}s)</div>}
                </Card>)
              })
              : <Alert type="warning" showIcon message="方案生成中" />}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

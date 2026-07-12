import React, { useState, useEffect, useCallback } from 'react'
import {
  Card, Descriptions, Tag, Button, Space, Statistic, Row, Col, Timeline, Alert, message, Empty,
} from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, CheckOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import { incidentAPI } from '../services/api'

const PRIORITY_COLOR = { P1: 'red', P2: 'orange', P3: 'blue', P4: 'default' }
const STATUS_COLOR = {
  open: 'red', diagnosing: 'processing', plan_ready: 'gold',
  executing: 'purple', verifying: 'cyan', resolved: 'green', closed: 'default',
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

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await incidentAPI.detail(incidentId)
      setInc(d.incident || d)
      const t = await incidentAPI.timeline(incidentId)
      setTimeline(Array.isArray(t) ? t : (t.items || []))
    } catch (e) {
      message.error('加载事故详情失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [incidentId])

  useEffect(() => { load() }, [load])

  const doAck = async () => {
    try { await incidentAPI.ack(incidentId); message.success('已确认'); load() }
    catch (e) { message.error(e.message) }
  }
  const doClose = async () => {
    try { await incidentAPI.close(incidentId, '手动关闭'); message.success('已关闭'); load() }
    catch (e) { message.error(e.message) }
  }

  if (!inc) return <div style={{ padding: 24 }}><Card loading={loading}>加载中...</Card></div>

  const rca = inc.rca_result || {}
  const impact = inc.impact || {}
  const plans = inc.plans || []

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
            {(rca.root_causes && rca.root_causes.length > 0)
              ? rca.root_causes.map((rc, i) => (
                <Alert key={i} type="info" showIcon style={{ marginBottom: 8 }}
                  message={`${rc.name || rc.rule_id} (置信 ${Math.round((rc.confidence || 0) * 100)}%)`}
                  description={rc.summary} />))
              : <Alert type="warning" showIcon message="诊断中" description="6B 诊断管道就位后此处展示根因/证据链。" />}
          </Card>
          <Card title="影响评估" size="small" style={{ marginBottom: 16 }}>
            {impact.summary
              ? <div>{impact.summary}</div>
              : <Alert type="warning" showIcon message="诊断中" description="6B 就位后展示受影响会话/健康衰减/业务系统。" />}
          </Card>
          <Card title="处置方案" size="small">
            {plans.length > 0
              ? plans.map((p, i) => <Tag key={i} color="blue">{p.name || p.scenario}</Tag>)
              : <Alert type="warning" showIcon message="方案生成中" description="6B/6C 就位后展示 2-3 套方案并可一键执行。" />}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

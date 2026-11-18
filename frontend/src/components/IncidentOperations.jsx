import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { incidentAPI } from '../services/api'
import { errorText } from '../utils/experience'
import { operationTime } from '../utils/operations'
import { hasPermission, Perm } from '../utils/permission'

const { Text, Paragraph } = Typography
const verificationStates = {
  observing: '正在验证', verified: '已验证恢复', insufficient_evidence: '验证超时，证据不足',
  unstable: '验证超时，恢复不稳定', not_recovered: '验证超时，采样未恢复', manual_required: '执行结束，需人工验证',
}
const runStates = {
  pending_approval: '待审批', prechecking: '前置检查', executing: '执行中', verifying: '验证中',
  succeeded: '执行结束', failed: '失败', rolled_back: '已回滚', timeout: '验证超时',
}
const ticketStates = {
  pending: '待审批', pending_approval_2: '待二级审批', approved: '已批准', executing: '执行中',
  success: '已执行', failed: '执行失败', rejected: '已拒绝', cancelled: '已取消',
}
const metricLabels = {
  active_connections: '活跃连接数', conn_usage_pct: '连接数使用率 (%)', qps: 'QPS', blocked_sessions: '阻塞会话数',
}

function VerificationRun({ run }) {
  const evidence = run.verification || {}
  const legacy = evidence.recovered === true ? '历史报告记录已恢复'
    : evidence.recovered === false ? '历史报告记录未恢复' : '暂无恢复验证记录'
  const status = verificationStates[evidence.state] || legacy
  return <Card size="small" title={run.playbook} extra={<Tag>{runStates[run.status] || run.status}</Tag>}>
    <Space direction="vertical">
      <Text strong>{status}</Text>
      <Text type="secondary">{run.id} · {operationTime(run.started_at)} 至 {operationTime(run.finished_at)}</Text>
      {run.failed_steps > 0 && <Text type="warning">{run.failed_steps} 个步骤失败，请检查处理记录。</Text>}
      {evidence.metric && <Text>{metricLabels[evidence.metric] || evidence.metric} {evidence.predicate || '判据未记录'} · 来源 {evidence.source || '未记录'}</Text>}
      {evidence.window_seconds != null && <Text>观察 {evidence.observed_seconds} / {evidence.window_seconds} 秒 · 有效采样 {evidence.sample_count} 次 · 连续达标 {evidence.stable_samples} / {evidence.required_samples} 次</Text>}
      {evidence.missing_checks != null && <Text>无有效数据 {evidence.missing_checks} 次轮询 · 观察窗内再次偏离 {evidence.recurrences} 次</Text>}
      {evidence.value != null && <Text>最近采样 {String(evidence.value)} · {operationTime(evidence.last_sample_at || evidence.at)}</Text>}
      {evidence.recovered === true && <Text type={run.followup?.passes === false ? 'danger' : 'secondary'}>
        {run.followup ? `验证后最近采样${run.followup.passes ? '达标' : '再次偏离'} · ${run.followup.value} · ${operationTime(run.followup.at)}`
          : '暂无可判断复发的后续采样'}
      </Text>}
      {evidence.note && <Text type="secondary">{evidence.note}</Text>}
    </Space>
  </Card>
}

function MetricTrace({ source }) {
  const [selected, setSelected] = useState(null)
  const keys = [...new Set(source.items.flatMap(row => Object.keys(row.values)))]
  const metric = keys.includes(selected) ? selected : keys[0]
  const rows = source.items.map(item => ({ ...item, value: item.values[metric] ?? null }))
  return <section id="incident-metrics">
    <Typography.Title level={5}>事故窗口采集曲线</Typography.Title>
    {!metric ? <Empty description="此窗口没有可绘制的核心指标" /> : <>
      <Select aria-label="事故曲线指标" value={metric} onChange={setSelected} options={keys.map(key => ({ value: key, label: metricLabels[key] || key }))} />
      <div style={{ height: 220, marginTop: 12 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows}>
            <XAxis dataKey="at" tickFormatter={v => new Date(v).toLocaleTimeString('zh-CN')} minTickGap={24} />
            <YAxis />
            <Tooltip labelFormatter={operationTime} />
            <Line dataKey="value" name={metricLabels[metric] || metric} stroke="#1677ff" connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <details>
        <summary>查看曲线采样值</summary>
        <Table size="small" rowKey="id" dataSource={[...rows].reverse()} pagination={{ pageSize: 8 }}
          columns={[
            { title: '采样时刻', dataIndex: 'at', render: operationTime },
            { title: metricLabels[metric] || metric, dataIndex: 'value', render: value => value ?? '缺失' },
          ]} />
      </details>
    </>}
    <Text type="secondary">来源为平台采集记录。{source.truncated && '仅展示窗口内最近 120 条采样。'}</Text>
  </section>
}

export default function IncidentOperations({ incident, canTriage }) {
  const id = incident.incident_id
  const canLinkTicket = canTriage && hasPermission(Perm.TICKETS_VIEW)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [revision, setRevision] = useState(0)
  const [editing, setEditing] = useState(null)
  const [ticketId, setTicketId] = useState('')
  const [action, setAction] = useState({})
  const refresh = () => setRevision(value => value + 1)

  const running = data?.runs.some(run => ['pending_approval', 'prechecking', 'executing', 'verifying'].includes(run.status))
  useEffect(() => {
    const controller = new AbortController()
    incidentAPI.context(id, controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); setError(null) }
    }).catch(err => {
      if (!controller.signal.aborted) {
        setError(errorText(err))
        if (err.status === 403 || err.status === 404) setData(null)
      }
    })
    return () => controller.abort()
  }, [id, incident.status, revision])

  useEffect(() => {
    if (!running) return undefined
    const timer = setInterval(() => {
      if (!document.hidden) setRevision(value => value + 1)
    }, 10000)
    return () => clearInterval(timer)
  }, [running])

  async function update(kind) {
    setAction({ pending: kind })
    try {
      if (kind === 'handoff') {
        const result = await incidentAPI.saveHandoff(id, editing)
        setData(previous => ({ ...previous, handoff: result }))
        setEditing(null)
      } else {
        await incidentAPI.linkTicket(id, Number(ticketId))
        setTicketId('')
      }
      setAction({ success: kind === 'handoff' ? '交接已保存' : '工单已关联' })
      refresh()
    } catch (err) {
      setAction({ error: errorText(err) })
    }
  }

  const windowParams = data && new URLSearchParams({ ...data.window, incident: id })
  return <div className="operations-feedback" id="incident-evidence">
    <Card title="交接与证据" size="small" extra={<Button onClick={refresh}>刷新证据</Button>}>
      {error && <Alert showIcon type="error" message={data ? '证据未刷新，显示上次结果' : '无法读取事故证据'} description={error} />}
      {!data && !error && <Text>正在读取事故证据…</Text>}
      {data && <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions column={{ xs: 1, sm: 2 }} items={[
          { key: 'owner', label: '负责人', children: data.handoff.owner || '待认领' },
          { key: 'next', label: '下一步', children: data.handoff.next_step || '待补充' },
          { key: 'by', label: '更新记录', children: data.handoff.updated_at
            ? `${data.handoff.updated_by} · ${operationTime(data.handoff.updated_at)}` : '尚未交接' },
          { key: 'window', label: '证据时间窗', children: `${operationTime(data.window.from)} 至 ${operationTime(data.window.to)}` },
        ]} />
        {canTriage && !editing && <Button disabled={!!error} onClick={() => setEditing({
          owner: data.handoff.owner, next_step: data.handoff.next_step, version: data.handoff.version,
        })}>编辑交接</Button>}
        {editing && <Space direction="vertical" style={{ width: '100%' }}>
          <label htmlFor="handoff-owner">负责人账号</label>
          <Input id="handoff-owner" placeholder="填写有权查看本事故的账号" value={editing.owner} maxLength={150}
            onChange={evt => setEditing(current => ({ ...current, owner: evt.target.value }))} />
          <label htmlFor="handoff-step">下一步行动</label>
          <Input.TextArea id="handoff-step" value={editing.next_step} maxLength={500} showCount
            onChange={evt => setEditing(current => ({ ...current, next_step: evt.target.value }))} />
          <Space>
            <Button type="primary" loading={action.pending === 'handoff'} disabled={!!error || !!action.pending}
              onClick={() => update('handoff')}>保存交接</Button>
            <Button disabled={!!action.pending} onClick={() => { setEditing(null); setAction({}) }}>取消编辑</Button>
          </Space>
        </Space>}
        {action.error && <Alert type="error" showIcon message={action.error} />}
        {action.success && <Text type="success" role="status">{action.success}</Text>}
        {data.metrics && <>
          <Link to={`/databases/${data.config_id}/performance?${windowParams}`}>打开同时间窗性能中心</Link>
          <MetricTrace source={data.metrics} />
        </>}
        <section id="incident-events">
          <Typography.Title level={5}>触发信号与事件</Typography.Title>
          {data.events.length === 0 ? <Text type="secondary">暂无已关联事件。</Text> : <ul className="operations-items">
            {data.events.map(event => <li id={`event-${event.event_uid}`} key={event.event_uid}>
              <Tag>{event.source}</Tag>{event.signal} · {event.metric_key || '指标未记录'} · 观测 {event.value}
              <div><Text type="secondary">{operationTime(event.occurred_at)}</Text></div>
            </li>)}
          </ul>}
        </section>
        <section>
          <Typography.Title level={5}>同实例的告警线索</Typography.Title>
          <Paragraph type="secondary">告警与该窗口重叠，因果关系待复核。</Paragraph>
          {data.alerts.length === 0 ? <Text type="secondary">该窗口没有告警线索。</Text> : <ul className="operations-items">
            {data.alerts.map(alert => <li key={alert.id}>
              <Link to={`/work-queue?kind=alerts&stage=all&q=${alert.id}`}>#{alert.id} {alert.title}</Link>
              <Text type="secondary"> · {operationTime(alert.occurred_at)}</Text>
            </li>)}
          </ul>}
        </section>
        <section id="incident-changes">
          <Typography.Title level={5}>同时间窗的变更线索</Typography.Title>
          {data.changes.length === 0 ? <Text type="secondary">该窗口没有变更记录。</Text> : <ul className="operations-items">
            {data.changes.map(change => <li key={change.id}><Tag>{change.source}</Tag> {change.title}
              <Text type="secondary"> · {operationTime(change.occurred_at)}</Text>
            </li>)}
          </ul>}
        </section>
        {data.tickets && <section>
          <Typography.Title level={5}>已关联工单</Typography.Title>
          {data.tickets.length ? <ul className="operations-items">
            {data.tickets.map(ticket => <li key={ticket.id}><Tag>{ticketStates[ticket.status] || ticket.status}</Tag>
              #{ticket.id} {ticket.description} · {operationTime(ticket.execute_time)}</li>)}
          </ul> : <Text type="secondary">暂无关联工单。</Text>}
          <Link style={{ display: 'block' }} to={`/tickets?incident_id=${encodeURIComponent(id)}`}>打开该事故工单</Link>
          {canLinkTicket && <Space wrap style={{ marginTop: 8 }}>
            <Input aria-label="要关联的工单编号" inputMode="numeric" placeholder="同实例工单编号" value={ticketId}
              onChange={evt => setTicketId(evt.target.value)} />
            <Button disabled={!/^[1-9]\d{0,14}$/.test(ticketId) || !!action.pending || !!error}
              loading={action.pending === 'ticket'} onClick={() => update('ticket')}>关联现有工单</Button>
          </Space>}
        </section>}
      </Space>}
    </Card>
    {data && <Card title="执行与恢复验证" size="small" className="operations-feedback">
      {!data.runs.length ? <Empty description="暂无该事故的执行记录" />
        : <div className="operations-items">{data.runs.map(run => <VerificationRun key={run.id} run={run} />)}</div>}
    </Card>}
  </div>
}

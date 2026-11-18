import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Form, Input, Pagination, Radio, Space, Spin, Tag, Typography } from 'antd'
import { Link, useSearchParams } from 'react-router-dom'
import { operationsAPI } from '../services/api'
import { errorText } from '../utils/experience'
import { incidentStatus, operationTime } from '../utils/operations'

const { Title, Text } = Typography
const LEVELS = {
  P1: ['error', 'P1'], P2: ['warning', 'P2'], P3: ['processing', 'P3'], P4: ['default', 'P4'],
  critical: ['error', '严重'], error: ['volcano', '错误'], warning: ['warning', '警告'], info: ['processing', '信息'],
}
const ALERT_STATES = { active: '待确认', acknowledged: '已确认', resolved: '已恢复' }
const STAGES = { pending: '待确认', acknowledged: '已确认', finished: '已结束', all: '全部' }

export default function WorkQueue() {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') || 'incidents'
  const stage = params.get('stage') || 'pending'
  const q = params.get('q') || ''
  const page = params.get('page') || '1'
  const key = JSON.stringify([kind, stage, q, page])
  const [keyword, setKeyword] = useState(q)
  const [openAlert, setOpenAlert] = useState(null)
  const [fetchState, setFetchState] = useState({})
  const [attempt, setAttempt] = useState(0)

  useEffect(() => { setKeyword(q) }, [q])
  useEffect(() => {
    const controller = new AbortController()
    setFetchState(old => ({ key, data: old.key === key ? old.data : null, loading: true }))
    operationsAPI.queue({ kind, stage, q, page }, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) setFetchState({ key, data })
      })
      .catch(error => {
        if (!controller.signal.aborted) {
          setFetchState(old => ({
            key,
            data: error.status === 403 ? null : old.data,
            error: errorText(error),
          }))
        }
      })
    return () => controller.abort()
  }, [key, kind, stage, q, page, attempt])

  const data = fetchState.key === key ? fetchState.data : null
  const error = fetchState.key === key ? fetchState.error : null
  const loading = fetchState.key !== key || fetchState.loading
  const isAlerts = kind === 'alerts'
  const queueUrl = '/work-queue' + (params.size ? '?' + params.toString() : '')
  const setQuery = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([name, value]) => {
      if (value) next.set(name, String(value))
      else next.delete(name)
    })
    setOpenAlert(null)
    setParams(next)
  }
  const alert = openAlert?.key === key ? data?.items.find(row => row.id === openAlert.id) : null

  return <section className="operations-page">
    <header className="experience-heading">
      <div>
        <Title level={2}>值班队列</Title>
        <Text>按优先级查看当前可见的事故和告警，复制地址即可交接这一筛选视图。</Text>
      </div>
      <Button loading={loading} onClick={() => setAttempt(value => value + 1)}>刷新队列</Button>
    </header>
    <Card size="small" className="operations-controls">
      <Space wrap role="group" aria-label="事项类别">
        <Button type={!isAlerts ? 'primary' : 'default'} aria-pressed={!isAlerts}
          onClick={() => setQuery({ kind: '', stage: '', page: '' })}>事故</Button>
        <Button type={isAlerts ? 'primary' : 'default'} aria-pressed={isAlerts}
          onClick={() => setQuery({ kind: 'alerts', stage: '', page: '' })}>告警</Button>
        <Link to={isAlerts ? '/alerts' : '/incidents'}>{isAlerts ? '告警中心' : '事故中心'}</Link>
      </Space>
      <Form layout="inline" onFinish={() => setQuery({ q: keyword.trim(), page: '' })} className="operations-search">
        <Form.Item>
          <Input value={keyword} onChange={event => setKeyword(event.target.value)} maxLength={100}
            aria-label="检索队列" placeholder="标题、实例名或编号" />
        </Form.Item>
        <Form.Item><Button htmlType="submit" type="primary">检索</Button></Form.Item>
        <Form.Item><Button onClick={() => { setKeyword(''); setQuery({ stage: '', q: '', page: '' }) }}>清除筛选</Button></Form.Item>
      </Form>
      <Radio.Group value={stage} aria-label="处理阶段" onChange={event => setQuery({ stage: event.target.value, page: '' })}>
        {Object.entries(STAGES).map(([value, label]) => (
          <Radio.Button key={value} value={value}>{label}{data ? ` (${data.counts[value]})` : ''}</Radio.Button>
        ))}
      </Radio.Group>
    </Card>

    {error && <Alert showIcon type={data ? 'warning' : 'error'} className="operations-feedback"
      message={data ? '刷新失败 · 正在显示上次结果' : '队列暂不可用'} description={error}
      action={<Button onClick={() => setAttempt(value => value + 1)}>重试</Button>} />}
    {!data && loading && <div className="operations-feedback" role="status"><Spin size="small" /> 正在获取队列…</div>}
    {data && <>
      <p className="operations-feedback" role="status">
        共 {data.total} 条 · 第 {data.page} 页 · 平台业务库 · 获取于 {operationTime(data.checked_at)}
        {loading ? ' · 刷新中' : ''}
      </p>
      {!data.items.length && <Card><Empty description="当前条件没有匹配事项" /></Card>}
      <ul className="operations-items" aria-label="队列事项">
        {data.items.map(row => {
          const [color, label] = LEVELS[row.level] || ['default', row.level || '未分级']
          return <li key={row.id}>
            <Card size="small">
              <div className="operations-row">
                <div>
                  <Space wrap>
                    <Tag color={color}>{label}</Tag>
                    <Tag>{isAlerts ? ALERT_STATES[row.status] || row.status : incidentStatus(row.status)}</Tag>
                  </Space>
                  <h3>{row.title}</h3>
                  <Text type="secondary">{row.database} · {row.id} · {operationTime(row.occurred_at)}</Text>
                  {!isAlerts && (row.acked_by || row.acked_at) && <div>已确认 · {row.acked_by || '确认人未记录'}</div>}
                </div>
                {isAlerts
                  ? <Button onClick={() => setOpenAlert({ key, id: row.id })}>查看告警摘要</Button>
                  : <Link to={`/incidents/${encodeURIComponent(row.id)}?returnTo=${encodeURIComponent(queueUrl)}`}>查看事故</Link>}
              </div>
            </Card>
          </li>
        })}
      </ul>
      {data.total > data.page_size && <Pagination current={data.page} total={data.total}
        pageSize={data.page_size} showSizeChanger={false} onChange={value => setQuery({ page: value })} />}
    </>}
    <Drawer title="告警摘要" open={!!alert} onClose={() => setOpenAlert(null)} width={500}>
      {alert && <>
        <Title level={4}>{alert.title}</Title>
        <Descriptions column={1} items={[
          { key: 'db', label: '实例', children: alert.database },
          { key: 'state', label: '记录状态', children: ALERT_STATES[alert.status] || alert.status },
          { key: 'time', label: '首次告警', children: operationTime(alert.occurred_at) },
          { key: 'resolution', label: '恢复时间', children: alert.resolved_at ? operationTime(alert.resolved_at) : '未记录' },
        ]} />
        <Typography.Paragraph className="operations-description">{alert.description || '本记录没有补充描述。'}</Typography.Paragraph>
        {alert.status === 'acknowledged' && !alert.resolved_at && <Alert type="info" showIcon message="已确认表示人员已知晓。恢复时间目前未记录。" />}
      </>}
    </Drawer>
  </section>
}

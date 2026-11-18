import { useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Empty, Row, Space, Spin, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { systemAPI } from '../services/api'
import { errorText } from '../utils/experience'
import { operationTime } from '../utils/operations'

const { Title, Text } = Typography
const STATES = {
  ok: ['success', '正常'], up: ['success', '在线'], degraded: ['warning', '降级'],
  down: ['error', '不可用'], unknown: ['default', '尚未上报'],
  disabled: ['default', '未启用'], error: ['error', '探测失败'],
}
const DEPENDENCIES = { database: '平台业务库', timescaledb: '时序存储', redis: '缓存', elasticsearch: '搜索存储' }
const REASONS = {
  business_database: '平台业务库探测失败',
  dependency: '有依赖探测失败',
  unreported_component: '有组件尚未上报心跳',
  offline_component: '有组件实例失联',
  degradation_records: '本进程有降级记录',
}

function Status({ value }) {
  const [color, label] = STATES[value] || ['default', value || '未获取']
  return <Tag color={color}>{label}</Tag>
}

function SourceState({ source, label, onRetry }) {
  if (source?.error) {
    return <Alert showIcon className="operations-feedback" type={source.data ? 'warning' : 'error'}
      message={label + (source.data ? '未更新 · 保留上次结果' : '暂不可用')}
      description={source.error} action={<Button onClick={onRetry}>重试</Button>} />
  }
  if (!source?.data) return <div className="operations-feedback" role="status"><Spin size="small" /> 正在获取{label}…</div>
  return null
}

export default function SystemHealth() {
  const [sources, setSources] = useState({})
  const [revision, setRevision] = useState(0)
  const refresh = () => setRevision(value => value + 1)

  useEffect(() => {
    const controller = new AbortController()
    const read = async (key, request) => {
      setSources(old => ({ ...old, [key]: { ...old[key], loading: true, error: null } }))
      try {
        const response = await request(controller.signal)
        if (!controller.signal.aborted) {
          setSources(old => ({ ...old, [key]: { data: response?.data || response, readAt: Date.now() } }))
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setSources(old => ({
            ...old,
            [key]: {
              ...(error.status === 403 ? {} : old[key]),
              loading: false,
              error: errorText(error),
            },
          }))
        }
      }
    }
    read('summary', systemAPI.health)
    read('detail', signal => systemAPI.components({}, signal))
    return () => controller.abort()
  }, [revision])

  const health = sources.summary?.data
  const heartbeats = sources.detail?.data
  const degradations = Object.entries(health?.degradations || {})
  return <section className="operations-page">
    <div className="experience-heading">
      <div><Title level={2}>系统健康</Title><Text>查看平台依赖和后台组件的最新检查结果。</Text></div>
      <Button icon={<ReloadOutlined />} loading={sources.summary?.loading || sources.detail?.loading} onClick={refresh}>刷新检查</Button>
    </div>
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="健康概要" size="small">
        <SourceState source={sources.summary} label="健康概要" onRetry={refresh} />
        {health && <>
          <p><Status value={health.status} /> <Text type="secondary">检查于 {operationTime(health.checked_at ?? sources.summary.readAt)}</Text></p>
          {!!health.reasons?.length && <ul>{health.reasons.map(reason => <li key={reason}>{REASONS[reason] || reason}</li>)}</ul>}
          <Row gutter={[12, 12]}>
            {Object.entries(health.dependencies || {}).map(([name, value]) => (
              <Col xs={24} sm={12} xl={6} key={name}>
                <Card size="small" title={DEPENDENCIES[name] || name}><Status value={value.status} /></Card>
              </Col>
            ))}
          </Row>
        </>}
      </Card>
      {health && <Card title="组件汇总" size="small">
        {health.components?.length
          ? <ul className="operations-items">{health.components.map(component => <li key={component.component}>
              <strong>{component.display}</strong> <Status value={component.status} />
              <Text type="secondary">{component.hint || `${component.instances} 个实例 · 最近心跳 ${operationTime(component.last_beat_at)}`}</Text>
            </li>)}</ul>
          : <Empty description="概要没有返回组件信息" />}
      </Card>}
      <Card title="组件心跳明细" size="small">
        <SourceState source={sources.detail} label="心跳明细" onRetry={refresh} />
        {heartbeats && <>
          <Text type="secondary">获取于 {operationTime(sources.detail.readAt)}</Text>
          <Table rowKey={row => JSON.stringify([row.component, row.instance])} dataSource={heartbeats.items || []}
            columns={[
              { title: '组件', dataIndex: 'display' },
              { title: '实例', dataIndex: 'instance' },
              { title: '状态', dataIndex: 'status', render: status => <Status value={status} /> },
              { title: '最后心跳', dataIndex: 'last_beat_at', render: operationTime },
              { title: '静默时间', render: (_, row) => row.silent_sec == null ? '未记录' : `${row.silent_sec} 秒 / 阈值 ${row.stale_after_sec} 秒` },
            ]} scroll={{ x: 650 }} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }}
            locale={{ emptyText: '尚无实例心跳记录' }} />
        </>}
      </Card>
      {health && <Card title="降级记录（当前 Web 进程）" size="small">
        {degradations.length ? degradations.map(([scope, count]) => <Tag key={scope}>{scope} · {count} 次</Tag>)
          : <Text type="secondary">当前进程未记录降级事件。</Text>}
      </Card>}
    </Space>
  </section>
}

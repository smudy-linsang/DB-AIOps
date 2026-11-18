import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from 'antd'
import { operationTime } from '../utils/operations'

export const collectionStates = {
  fresh: ['success', '采集正常'],
  stale: ['warning', '数据过期'],
  failed: ['error', '采集失败'],
  partial: ['warning', '采集结果不完整'],
  never: ['default', '尚未采集'],
  disabled: ['default', '监控已停用'],
}
const failureReasons = {
  collection_failed: '最近一次采集失败，请检查目标库连通性、授权或采集器日志。',
  missing_driver: '采集器缺少该数据库所需的驱动。',
}
const metrics = {
  active_connections: '活跃连接数',
  conn_usage_pct: '连接数使用率',
  qps: 'QPS',
}

export function CollectionStatusTag({ collection }) {
  const [color, label] = collectionStates[collection?.state] || ['default', '采集状态未获取']
  return <Tag color={color}>{label}</Tag>
}

export default function CollectionHealth({ result, error, onRetry }) {
  return <Card title="采集新鲜度" className="operations-feedback">
    {error && <Alert type="error" showIcon message="无法读取采集状态" description={error}
      action={<Button onClick={onRetry}>重试</Button>} />}
    {!result && !error && <Typography.Text>正在读取采集状态…</Typography.Text>}
    {result && <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <CollectionStatusTag collection={result} />
      {result.state === 'stale' && <Alert type="warning" showIcon
        message="最后成功的样本已过期，无法判断实例当前状态。" />}
      {failureReasons[result.failure_reason] && <Typography.Text>{failureReasons[result.failure_reason]}</Typography.Text>}
      <Descriptions column={{ xs: 1, sm: 2 }} items={[
        { key: 'attempt', label: '最后尝试', children: operationTime(result.last_attempt_at) },
        { key: 'success', label: '最后成功', children: operationTime(result.last_success_at) },
        { key: 'period', label: '配置采集周期', children: `${result.expected_interval_seconds} 秒` },
        { key: 'cutoff', label: '判定过期阈值', children: `${result.stale_after_seconds} 秒` },
        { key: 'metrics', label: '核心指标缺项', children: result.missing_metrics == null ? '等待成功样本'
          : result.missing_metrics.map(metric => metrics[metric] || metric).join('、') || '最近成功样本齐全' },
        { key: 'checked', label: '数据来源', children: '平台采集记录 · ' + operationTime(result.checked_at) },
      ]} />
    </Space>}
  </Card>
}

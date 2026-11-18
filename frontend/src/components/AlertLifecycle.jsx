import { Descriptions, Space, Tag, Typography } from 'antd'
import { operationTime } from '../utils/operations'

export function AlertMonitoringTag({ alert }) {
  const state = alert.monitoring_state || ({
    active: 'ongoing', acknowledged: 'ongoing', resolved: 'recovered', superseded: 'superseded',
  })[alert.status]
  const tag = {
    ongoing: ['warning', '异常持续'],
    recovered: ['success', '已恢复'],
    superseded: ['default', '历史重复记录'],
  }[state] || ['default', '监控状态未知']
  return <Tag color={tag[0]}>{tag[1]}</Tag>
}

export default function AlertLifecycle({ alert }) {
  const evidence = alert.recovery_evidence
  const acknowledged = alert.is_acknowledged || alert.status === 'acknowledged'
  const receipt = alert.acknowledged_by || alert.acknowledged_at
    ? `${alert.acknowledged_by || '确认人未记录'} · ${operationTime(alert.acknowledged_at)}`
    : acknowledged ? '已知晓，历史回执未记录' : '未记录确认'
  return <Space direction="vertical">
    <Descriptions size="small" column={1} items={[
      { key: 'monitor', label: '监控观测', children: <AlertMonitoringTag alert={alert} /> },
      { key: 'ack', label: '确认回执', children: receipt },
      { key: 'recovery', label: '恢复时间', children: operationTime(alert.resolved_at) },
    ]} />
    {evidence?.source === 'history_merge'
      ? <Typography.Text type="secondary">合并时保留告警 #{evidence.retained_alert_id} 的当前监测状态。</Typography.Text>
      : evidence?.source ? <Descriptions title="恢复依据" size="small" column={1} items={[
          { key: 'source', label: '来源', children: ({ collector: '采集器实测', baseline: '基线对比', alert_rule: '告警规则判定' })[evidence.source] || evidence.source },
          { key: 'time', label: '依据时间', children: operationTime(evidence.observed_at || evidence.at) },
          { key: 'metric', label: '指标', children: evidence.metric || '未记录' },
          { key: 'value', label: '观测值', children: evidence.value != null ? String(evidence.value) : '未记录' },
          ...(evidence.threshold != null ? [{ key: 'threshold', label: '告警阈值', children: evidence.threshold }] : []),
        ]} /> : null}
  </Space>
}

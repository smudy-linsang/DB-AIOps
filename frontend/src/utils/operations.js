const INCIDENT_STATES = {
  open: '待处理',
  diagnosing: '诊断中',
  plan_ready: '方案就绪',
  executing: '执行中',
  verifying: '验证中',
  resolved: '已解决',
  closed: '已关闭',
}

export function incidentStatus(value) {
  return INCIDENT_STATES[value] || value || '未记录'
}

export function operationTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录'
}

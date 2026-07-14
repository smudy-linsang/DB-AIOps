// Phase 7 契约: 统一等待类展示名与颜色 (phase7/README §3)。全前端唯一取色处。
export const WAIT_CLASS_META = {
  on_cpu:        { label: 'CPU',      color: '#04B04B' },
  user_io:       { label: '用户 I/O', color: '#1868C8' },
  system_io:     { label: '系统 I/O', color: '#6FB7F5' },
  concurrency:   { label: '并发',     color: '#8B1A1A' },
  application:   { label: '应用',     color: '#D9312B' },
  commit:        { label: '提交',     color: '#E8842A' },
  network:       { label: '网络',     color: '#8C9BAB' },
  configuration: { label: '配置',     color: '#9A6B2F' },
  other:         { label: '其他',     color: '#D96BA8' },
};

export const WAIT_CLASS_ORDER = [
  'on_cpu', 'user_io', 'system_io', 'concurrency', 'application',
  'commit', 'network', 'configuration', 'other',
];

export const waitClassLabel = (k) => WAIT_CLASS_META[k]?.label || k || '-';
export const waitClassColor = (k) => WAIT_CLASS_META[k]?.color || '#bbb';

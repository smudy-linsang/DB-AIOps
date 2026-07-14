import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Progress, Space, Table, Typography, message } from 'antd';
import { ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { perfAPI } from '../../../services/api';
import WaitClassTag from '../WaitClassTag';
import SqlDetailPanel from '../SqlDetailPanel';
import KillModal from '../KillModal';

const { Text } = Typography;

/** Tab4 SQL 监控 (phase7/30 §5): 运行中语句(进度) + SQL 详情 */
export default function SqlTab({ configId, selectedDigest, onOpenSql, refreshKey }) {
  const [rows, setRows] = useState([]);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [killTarget, setKillTarget] = useState(null);

  const load = () => {
    if (!configId) return;
    setLoading(true);
    perfAPI.runningSql(configId)
      .then((r) => { setRows(r.data.rows || []); setDegraded(!!r.data.degraded); })
      .catch((e) => message.error(`运行中 SQL 加载失败: ${e.message}`))
      .finally(() => setLoading(false));
  };
  useEffect(load, [configId, refreshKey]);

  return (
    <div>
      <Card size="small"
        title="运行中语句"
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>}>
        {degraded && <Alert type="warning" showIcon message="目标库直连失败, 列表可能为空" style={{ marginBottom: 8 }} />}
        <Table size="small" rowKey="session_id" loading={loading} dataSource={rows}
          pagination={false}
          columns={[
            { title: '会话', dataIndex: 'session_id', width: 90, render: (v) => <Text code>{v}</Text> },
            { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
              render: (v, r) => (
                <a onClick={() => r.sql_id && onOpenSql?.(r.sql_id)}>
                  <Text code style={{ fontSize: 11 }}>{v || r.sql_id || '-'}</Text>
                </a>) },
            { title: '用户', dataIndex: 'user_name', width: 90, ellipsis: true },
            { title: '已运行', dataIndex: 'elapsed_sec', width: 80, render: (v) => `${v}s` },
            { title: '等待类', dataIndex: 'wait_class', width: 90, render: (v) => <WaitClassTag value={v} /> },
            { title: '阶段', dataIndex: 'phase', width: 140, ellipsis: true },
            { title: '进度', width: 140,
              render: (_, r) => (r.progress_pct != null
                ? <Progress percent={r.progress_pct} size="small" />
                : <Text type="secondary" style={{ fontSize: 11 }}>计时中 {r.elapsed_sec}s</Text>) },
            { title: '预计剩余', dataIndex: 'est_remain_sec', width: 90,
              render: (v) => (v != null ? `${v}s` : '-') },
            { title: '操作', width: 100,
              render: (_, r) => (
                <Button size="small" danger icon={<StopOutlined />}
                        onClick={() => setKillTarget(r)}>终止</Button>) },
          ]} />
      </Card>
      <Card size="small" style={{ marginTop: 8 }}
            title={selectedDigest ? `SQL 详情: ${selectedDigest.slice(0, 16)}…` : 'SQL 详情'}>
        {selectedDigest
          ? <SqlDetailPanel configId={configId} digest={selectedDigest} />
          : <Alert type="info" showIcon message="从任意页面点击 SQL 进入详情 (趋势/计划/建议/关联事故)" />}
      </Card>
      <KillModal configId={configId} target={killTarget}
                 onClose={() => setKillTarget(null)} onDone={load} />
    </div>
  );
}

import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Empty, Space, Table, Tag, Typography,
} from 'antd';
import { StopOutlined } from '@ant-design/icons';
import { perfAPI } from '../../services/api';
import BreakdownBar from './BreakdownBar';
import WaitClassTag from './WaitClassTag';
import KillModal from './KillModal';
import { waitClassLabel } from './waitClassMeta';
import { withAlive } from './useSafeAsync';

const { Text, Paragraph } = Typography;

/**
 * BUG-125: 性能主页的「Top 会话」表格调用 onOpenSession，但 PerformanceCenter
 * 组装 common 时从未传入该回调，可选链把点击静默吞掉 —— EMCC 性能主页最核心的
 * 「Top 会话 → 会话详情/终止」下钻在本系统里完全没有反应，用户以为页面卡了。
 *
 * 本面板补齐这条链路：会话属性 + 等待类分解 + 关联 SQL（可跳 SQL 详情）
 * + 走审批链的终止会话。
 */
export default function SessionDetailPanel({ configId, session, range, onOpenSql }) {
  const [live, setLive] = useState(null);
  const [loading, setLoading] = useState(false);
  const [killTarget, setKillTarget] = useState(null);

  const sessionId = session?.key || session?.session_id;

  // 拉一次实时会话列表，找出这条会话的当前状态（历史 Top 只有聚合值）
  useEffect(() => {
    if (!configId || !sessionId) return undefined;
    setLoading(true);
    return withAlive((alive) => {
      perfAPI.sessions(configId)
        .then((r) => {
          if (!alive()) return;
          const rows = r.data?.sessions || [];
          setLive(rows.find((s) => String(s.session_id) === String(sessionId)) || null);
        })
        .catch(() => { if (alive()) setLive(null); })
        .finally(() => { if (alive()) setLoading(false); });
    });
  }, [configId, sessionId]);

  const breakdownRows = useMemo(() => {
    const bd = session?.breakdown || {};
    const total = Object.values(bd).reduce((a, v) => a + (v || 0), 0) || 1;
    return Object.entries(bd)
      .map(([k, v]) => ({ key: k, active_sec: v, pct: Math.round((v * 1000) / total) / 10 }))
      .sort((a, b) => b.active_sec - a.active_sec);
  }, [session]);

  if (!session) return <Empty description="未选择会话" />;

  const cur = live || {};
  const sqlText = cur.sql_text || session.sql_text;
  const digest = cur.sql_digest || session.sql_digest;

  return (
    <div>
      {!loading && !live && (
        <Alert type="info" showIcon style={{ marginBottom: 8 }}
               message="该会话在目标库上已不存在（可能已结束），以下为所选时间窗内的历史活动汇总" />
      )}

      <Card size="small" title="会话属性" loading={loading}
            extra={live && cur.killable !== false && (
              <Button size="small" danger icon={<StopOutlined />}
                      onClick={() => setKillTarget({ session_id: sessionId })}>
                终止会话
              </Button>
            )}>
        <Descriptions size="small" column={2} bordered
                      styles={{ label: { width: 96 } }}>
          <Descriptions.Item label="会话 ID"><Text code>{sessionId}</Text></Descriptions.Item>
          <Descriptions.Item label="用户">{cur.user_name || session.user_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="程序">{cur.program || session.program || '-'}</Descriptions.Item>
          <Descriptions.Item label="模块">{cur.module || '-'}</Descriptions.Item>
          <Descriptions.Item label="客户端">{cur.client_host || '-'}</Descriptions.Item>
          <Descriptions.Item label="数据库">{cur.db_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">{cur.state || cur.command || '-'}</Descriptions.Item>
          <Descriptions.Item label="当前等待">
            {cur.wait_class ? <WaitClassTag value={cur.wait_class} /> : '-'}
            {cur.wait_event ? <Text type="secondary" style={{ marginLeft: 6, fontSize: 11 }}>
              {cur.wait_event}</Text> : null}
          </Descriptions.Item>
          <Descriptions.Item label="活动秒">{cur.active_secs ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="锁等待秒">{cur.wait_secs ?? '-'}</Descriptions.Item>
          {cur.is_blocked && (
            <Descriptions.Item label="阻塞来源" span={2}>
              <Tag color="volcano">被 {cur.blocker_id} 阻塞</Tag>
              {cur.lock_object && <Text code style={{ fontSize: 11 }}>{cur.lock_object}</Text>}
            </Descriptions.Item>
          )}
          <Descriptions.Item label="窗口内活动" span={2}>
            {session.active_sec != null
              ? <span>{session.active_sec}s（占实例 {session.pct}%）</span> : '-'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title="等待类分解（所选时间窗）" style={{ marginTop: 8 }}>
        {breakdownRows.length ? (
          <>
            <BreakdownBar breakdown={session.breakdown} pct={session.pct} />
            <Table size="small" pagination={false} rowKey="key" style={{ marginTop: 8 }}
              dataSource={breakdownRows}
              columns={[
                { title: '等待类', dataIndex: 'key', width: 120,
                  render: (v) => <WaitClassTag value={v} /> },
                { title: '名称', dataIndex: 'key', render: (v) => waitClassLabel(v) },
                { title: '活动秒', dataIndex: 'active_sec', width: 90 },
                { title: '占比', dataIndex: 'pct', width: 80, render: (v) => `${v}%` },
              ]} />
          </>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无等待类明细" />}
      </Card>

      <Card size="small" title="当前 / 最后执行的 SQL" style={{ marginTop: 8 }}
            extra={digest && (
              <a onClick={() => onOpenSql?.(digest)}>查看 SQL 详情 →</a>
            )}>
        {sqlText ? (
          <Paragraph copyable={{ text: sqlText }}
                     style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
            <Text code style={{ fontSize: 12 }}>{sqlText}</Text>
          </Paragraph>
        ) : (
          <Text type="secondary">
            {digest ? <>无 SQL 原文样例，指纹 <Text code>{digest}</Text></> : '无 SQL'}
          </Text>
        )}
      </Card>

      <KillModal configId={configId} target={killTarget}
                 onClose={() => setKillTarget(null)}
                 onDone={() => setKillTarget(null)} />
    </div>
  );
}

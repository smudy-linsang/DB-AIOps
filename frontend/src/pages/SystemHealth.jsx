/**
 * SystemHealth - W4 系统自监控页
 *
 * 展示：组件心跳状态 / 依赖探测结果 / 降级留痕计数
 * 数据源：/api/v1/system/health、/api/v1/system/components
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Card, Table, Tag, Row, Col, Typography, Button, Space, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { systemAPI } from '../services/api';

const { Title, Text } = Typography;

const STATUS_COLOR = { ok: 'green', up: 'green', degraded: 'orange', down: 'red', unknown: 'default', disabled: 'default', error: 'red' };

const SystemHealth = () => {
  const [health, setHealth] = useState(null);
  const [components, setComponents] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [h, c] = await Promise.all([systemAPI.health(), systemAPI.components()]);
      setHealth(h?.data || h || null);
      const compRes = c?.data || c || {};
      setComponents(compRes.items || []);
    } catch (_) {
      // 接口不可用时保持空态展示
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const compColumns = [
    { title: '组件', dataIndex: 'display', key: 'display' },
    { title: '实例', dataIndex: 'instance', key: 'instance', render: (v) => <Text code>{v}</Text> },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (v) => <Tag color={STATUS_COLOR[v] || 'default'}>{v}</Tag>,
    },
    {
      title: '最后心跳', dataIndex: 'last_beat_at', key: 'last_beat_at',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '静默时长', dataIndex: 'silent_sec', key: 'silent_sec',
      render: (v, r) => (v == null ? '-' : `${v}s / 阈值 ${r.stale_after_sec}s`),
    },
  ];

  const deps = health?.dependencies || {};
  const degradations = Object.entries(health?.degradations || {});

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Row justify="space-between" align="middle">
          <Title level={4} style={{ margin: 0 }}>
            系统健康
            {health && (
              <Tag color={STATUS_COLOR[health.status] || 'default'} style={{ marginLeft: 12 }}>
                {health.status}
              </Tag>
            )}
          </Title>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Row>

        <Row gutter={16}>
          {Object.entries(deps).map(([name, info]) => (
            <Col span={6} key={name}>
              <Card size="small" title={name}>
                <Tag color={STATUS_COLOR[info?.status] || 'default'}>{info?.status || 'unknown'}</Tag>
                <div style={{ marginTop: 8 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>{info?.message || '-'}</Text>
                </div>
              </Card>
            </Col>
          ))}
        </Row>

        <Card size="small" title="组件心跳">
          <Table
            rowKey={(r) => `${r.component}-${r.instance}`}
            columns={compColumns}
            dataSource={components}
            pagination={false}
            size="small"
          />
        </Card>

        <Card size="small" title="降级留痕（进程启动以来）">
          {degradations.length === 0
            ? <Text type="secondary">当前无降级记录</Text>
            : degradations.map(([scope, count]) => (
              <Tag key={scope} color="orange" style={{ marginBottom: 8 }}>{scope}: {count}</Tag>
            ))}
        </Card>
      </Space>
    </Spin>
  );
};

export default SystemHealth;

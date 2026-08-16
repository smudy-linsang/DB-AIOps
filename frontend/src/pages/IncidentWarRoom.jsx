/**
 * IncidentWarRoom - DB-AIOps v2.0 一站式排障作战室
 * 汇聚：故障全景 (1-Min) + Causal Flow 因果图 (5-Min) + Playbook 自愈执行 (15-Min)
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Row, Col, Typography, Tag, Space, Button, Steps, Timeline,
  Progress, Badge, Table, Alert, Modal, Spin, message, Tooltip, Divider
} from 'antd';
import {
  ThunderboltOutlined, CheckCircleOutlined, WarningOutlined,
  CloseCircleOutlined, ClockCircleOutlined, ArrowRightOutlined,
  SafetyCertificateOutlined, PlayCircleOutlined, ReloadOutlined,
  FileSearchOutlined, NodeIndexOutlined, FireOutlined
} from '@ant-design/icons';
import { apiV2 } from '../services/api';

const { Title, Text, Paragraph } = Typography;

export default function IncidentWarRoom() {
  const [incidents, setIncidents] = useState([]);
  const [selectedIncidentId, setSelectedIncidentId] = useState(null);
  const [warRoomData, setWarRoomData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [dryRunResult, setDryRunResult] = useState(null);

  // 加载活跃故障清单
  const fetchActiveIncidents = useCallback(async () => {
    try {
      const res = await apiV2.getActiveIncidents();
      const list = res?.data?.items || [];
      setIncidents(list);
      if (list.length > 0 && !selectedIncidentId) {
        setSelectedIncidentId(list[0].incident_id);
      }
    } catch (e) {
      // 容错兜底
    }
  }, [selectedIncidentId]);

  // 加载指定故障的 WarRoom 全景
  const loadWarRoomDetail = useCallback(async (incId) => {
    if (!incId) return;
    setLoading(true);
    setDryRunResult(null);
    try {
      const res = await apiV2.getWarRoomContext(incId);
      setWarRoomData(res?.data || null);
    } catch (e) {
      message.error('加载排障作战室全景失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchActiveIncidents();
    const interval = setInterval(fetchActiveIncidents, 15000);
    return () => clearInterval(interval);
  }, [fetchActiveIncidents]);

  useEffect(() => {
    if (selectedIncidentId) {
      loadWarRoomDetail(selectedIncidentId);
    }
  }, [selectedIncidentId, loadWarRoomDetail]);

  // 预演 Playbook
  const handleDryRun = async (playbookCode) => {
    if (!warRoomData) return;
    setActionLoading(true);
    try {
      const res = await apiV2.dryRunPlaybook({
        incident_id: warRoomData.incident_id,
        playbook_code: playbookCode,
        config_id: warRoomData.config_id,
        params: {}
      });
      setDryRunResult(res?.data);
      if (res?.data?.status !== 'PASSED') {
        throw new Error(res?.data?.reason || '安全预演未通过');
      }
      message.success('Dry-Run 安全评估通过');
    } catch (e) {
      message.error('预演失败: ' + e.message);
    } finally {
      setActionLoading(false);
    }
  };

  // 正式自愈执行
  const handleExecutePlaybook = async (playbookCode) => {
    if (!warRoomData) return;
    setActionLoading(true);
    try {
      const res = await apiV2.executePlaybook({
        incident_id: warRoomData.incident_id,
        playbook_code: playbookCode,
        config_id: warRoomData.config_id,
        params: {}
      });
      const status = res?.data?.status;
      if (status === 'failed' || status === 'REJECTED') {
        throw new Error(res?.data?.error || res?.data?.reason || '剧本未执行');
      }
      message.info(res?.data?.message || `剧本已受理，当前状态：${status || 'unknown'}`);
      loadWarRoomDetail(warRoomData.incident_id);
    } catch (e) {
      message.error('执行失败: ' + e.message);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div style={{ padding: 20 }}>
      {/* 顶部 Header 状态条 */}
      <div style={{
        background: 'linear-gradient(135deg, #111827 0%, #1e293b 100%)',
        padding: '16px 20px',
        borderRadius: 8,
        marginBottom: 16,
        border: '1px solid #374151',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <Space size={16}>
          <div style={{
            background: 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
            padding: '8px 12px',
            borderRadius: 6,
            color: '#fff',
            fontWeight: 'bold',
            display: 'flex',
            alignItems: 'center',
            gap: 6
          }}>
            <FireOutlined /> 1-5-15 智能排障作战室 (WarRoom)
          </div>
          <div>
            <Title level={4} style={{ color: '#fff', margin: 0 }}>
              {warRoomData ? `${warRoomData.title} (${warRoomData.db_name})` : '正在监控全局纳管实例状态...'}
            </Title>
            <Text style={{ color: '#9ca3af', fontSize: 12 }}>
              目标时效：1分钟感知 ➔ 5分钟因果定位 ➔ 15分钟安全自愈闭环
            </Text>
          </div>
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => loadWarRoomDetail(selectedIncidentId)}>
            刷新全景
          </Button>
        </Space>
      </div>

      <Row gutter={16}>
        {/* 左侧：活跃事故流 (1-Min 感知) */}
        <Col span={6}>
          <Card
            title={<Space><ClockCircleOutlined style={{ color: '#ef4444' }} /><span>1-Min 活跃故障流</span></Space>}
            size="small"
            style={{ minHeight: 650, borderRadius: 8 }}
          >
            {incidents.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '40px 0', color: '#10b981' }}>
                <CheckCircleOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                <div>当前所有数据库实例运行正常</div>
              </div>
            ) : (
              incidents.map(inc => (
                <div
                  key={inc.incident_id}
                  onClick={() => setSelectedIncidentId(inc.incident_id)}
                  style={{
                    padding: 12,
                    borderRadius: 6,
                    marginBottom: 10,
                    cursor: 'pointer',
                    background: selectedIncidentId === inc.incident_id ? '#eff6ff' : '#f9fafb',
                    border: selectedIncidentId === inc.incident_id ? '1px solid #3b82f6' : '1px solid #e5e7eb'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Tag color={inc.severity === 'critical' ? 'red' : 'orange'}>
                      {inc.severity.toUpperCase()}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      已持续 {Math.floor(inc.duration_seconds / 60)}分{inc.duration_seconds % 60}秒
                    </Text>
                  </div>
                  <Text strong style={{ fontSize: 13 }}>{inc.title}</Text>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>
                    实例：{inc.db_name} · SLA 剩余：<span style={{ color: '#ef4444', fontWeight: 'bold' }}>{Math.floor(inc.sla_remaining_seconds / 60)}分</span>
                  </div>
                </div>
              ))
            )}
          </Card>
        </Col>

        {/* 中间：Causal Flow 因果推理链 (5-Min 定位) */}
        <Col span={10}>
          <Card
            title={<Space><NodeIndexOutlined style={{ color: '#8b5cf6' }} /><span>5-Min RCA 3.0 因果图谱推导</span></Space>}
            size="small"
            loading={loading}
            style={{ minHeight: 650, borderRadius: 8 }}
          >
            {warRoomData?.causal_chain?.length > 0 ? (
              <Timeline
                style={{ marginTop: 16 }}
                items={warRoomData.causal_chain.map((c, i) => ({
                  color: c.node_type === 'CHANGE' ? 'blue' : (c.node_type === 'SQL' ? 'orange' : (c.node_type === 'LOCK' ? 'red' : 'purple')),
                  children: (
                    <div style={{ background: '#f8fafc', padding: 10, borderRadius: 6, border: '1px solid #e2e8f0' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <Text strong style={{ fontSize: 13 }}>
                          Step {c.step}: [{c.node_type}] {c.name}
                        </Text>
                        <Tag color="purple">置信度: {(c.confidence * 100).toFixed(0)}%</Tag>
                      </div>
                      <Paragraph style={{ fontSize: 12, color: '#475569', margin: '4px 0 0' }}>
                        {c.desc}
                      </Paragraph>
                      {c.evidence?.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          {c.evidence.map(e => <Tag key={e} bordered={false} color="default" style={{ fontSize: 10 }}>证据 {e}</Tag>)}
                        </div>
                      )}
                    </div>
                  )
                }))}
              />
            ) : (
              <div style={{ textAlign: 'center', padding: '60px 0', color: '#9ca3af' }}>
                <FileSearchOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                <div>正在实时推导因果节点...</div>
              </div>
            )}
          </Card>
        </Col>

        {/* 右侧：自愈 Playbook 决策与安全沙箱 (15-Min 解决) */}
        <Col span={8}>
          <Card
            title={<Space><ThunderboltOutlined style={{ color: '#f59e0b' }} /><span>15-Min 自愈预案与安全执行</span></Space>}
            size="small"
            style={{ minHeight: 650, borderRadius: 8 }}
          >
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ fontSize: 13 }}>推荐应急 Playbook 方案：</Text>
            </div>

            {warRoomData?.recommended_actions?.map(action => (
              <Card
                key={action.playbook_code}
                size="small"
                style={{ marginBottom: 12, border: '1px solid #e5e7eb', borderRadius: 6 }}
                title={<Space><SafetyCertificateOutlined style={{ color: '#10b981' }} /><Text strong>{action.title}</Text></Space>}
              >
                <Paragraph style={{ fontSize: 12, color: '#6b7280', marginBottom: 8 }}>
                  {action.description}
                </Paragraph>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Tag color="green">风险等级: {action.risk_level.toUpperCase()}</Tag>
                  <Space size="small">
                    <Button
                      size="small"
                      loading={actionLoading}
                      onClick={() => handleDryRun(action.playbook_code)}
                    >
                      Dry-Run 预演
                    </Button>
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={actionLoading}
                      onClick={() => handleExecutePlaybook(action.playbook_code)}
                    >
                      立即执行
                    </Button>
                  </Space>
                </div>
              </Card>
            ))}

            {/* Dry-Run 预演结果面板 */}
            {dryRunResult && (
              <Alert
                style={{ marginTop: 16 }}
                type={dryRunResult.status === 'PASSED' ? 'success' : 'error'}
                showIcon
                message={<Text strong>Dry-Run 预演评估报告</Text>}
                description={
                  <div style={{ fontSize: 12, marginTop: 4 }}>
                    <div>• <b>结论</b>: {dryRunResult.impact_summary || dryRunResult.reason}</div>
                    {dryRunResult.status === 'PASSED' && (
                      <>
                        <div>• <b>预计释放锁数量</b>: {dryRunResult.released_locks_estimate} 个</div>
                        <div>• <b>回滚支持</b>: {dryRunResult.rollback_available ? '已就绪 (可一键逆向回滚)' : '无需回滚'}</div>
                      </>
                    )}
                  </div>
                }
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}

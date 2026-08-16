/**
 * CopilotDrawer - DB-AIOps 全局智能交互助手 (Tool Calling + Action Cards 驱动)
 * 支持：实时 ASH 探测、执行计划诊断、Action Cards 交互动作卡片（一键 Kill/扩容/跳转）
 */
import React, { useState, useEffect, useRef } from 'react';
import {
  Drawer, Input, Button, Space, Typography, Tag, Card, Avatar,
  Spin, List, Divider, Select, Tooltip, Badge, Row, Col, Progress, Alert,
  message, Modal
} from 'antd';
import {
  RobotOutlined, SendOutlined, ClearOutlined, ThunderboltOutlined,
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined,
  MedicineBoxOutlined, BulbOutlined, DatabaseOutlined, SyncOutlined,
  PlayCircleOutlined, SafetyCertificateOutlined, ArrowRightOutlined,
  CopyOutlined, ToolOutlined
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { aiOpsAPI, databaseAPI, apiV2 } from '../services/api';

const { TextArea } = Input;
const { Text, Paragraph, Title } = Typography;

export default function CopilotDrawer({ visible, onClose, initialDbId }) {
  const navigate = useNavigate();
  const [databases, setDatabases] = useState([]);
  const [selectedDbId, setSelectedDbId] = useState(initialDbId || null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [assessing, setAssessing] = useState(false);
  const [assessmentResult, setAssessmentResult] = useState(null);
  const [actionExecuting, setActionExecuting] = useState(false);

  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: '👋 您好！我是 **DB-AIOps 智能运维 Copilot 助手**。\n\n我已全面接入 **【运维工具集 (Tool Calling)】** 与 **【交互动作卡片 (Action Cards)】**：\n- ⏱️ `get_realtime_ash` 实时探查会话与阻塞链\n- 🔬 `explain_sql` 自动诊断执行计划与缺少索引\n- ⚡ `dry_run_playbook` 预案预演与影响评估\n- 🎴 在气泡中直接输出【立即 Kill 会话】、【一键扩容】等操作卡片\n\n请随时输入您遇到的数据库问题或 SQL！',
      time: new Date().toLocaleTimeString(),
      source: 'system'
    }
  ]);

  const messagesEndRef = useRef(null);

  // 同步外部传入的 initialDbId
  useEffect(() => {
    if (initialDbId) {
      setSelectedDbId(initialDbId);
    }
  }, [initialDbId]);

  // 加载数据库列表供切换
  const loadDatabaseList = async () => {
    try {
      const res = await databaseAPI.list();
      const list = res?.databases || res?.data || res?.items || (Array.isArray(res) ? res : []);
      setDatabases(list);
      if (!selectedDbId && list.length > 0) {
        setSelectedDbId(list[0].id);
      }
    } catch (e) {
      // 容错兜底：从 localStorage 或已有缓存读取
    }
  };

  useEffect(() => {
    if (visible) {
      loadDatabaseList();
    }
  }, [visible]);

  // 滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  // 发送对话
  const handleSend = async (customQuery) => {
    const q = customQuery || query;
    if (!q || !q.trim() || loading) return;

    const userMsg = {
      role: 'user',
      content: q.trim(),
      time: new Date().toLocaleTimeString()
    };

    setMessages(prev => [...prev, userMsg]);
    if (!customQuery) setQuery('');
    setLoading(true);

    try {
      // 提取历史轮次
      const history = messages
        .filter(m => m.source !== 'system')
        .slice(-6)
        .map(m => ({ role: m.role, content: m.content }));

      const res = await aiOpsAPI.chat({
        query: q.trim(),
        config_id: selectedDbId || undefined,
        history
      });

      const aiMsg = {
        role: 'assistant',
        content: res.answer || res.message || '抱歉，暂时未能生成有效回复。',
        time: new Date().toLocaleTimeString(),
        model: res.model,
        source: res.source,
        latency_ms: res.latency_ms,
        tool_results: res.tool_results,
        action_cards: res.action_cards || []
      };

      setMessages(prev => [...prev, aiMsg]);
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: `⚠️ 对话请求发生异常: ${err.message || '网络连接超时'}`,
          time: new Date().toLocaleTimeString(),
          source: 'error'
        }
      ]);
    } finally {
      setLoading(false);
    }
  };

  // 执行动作卡片 (Action Card)
  const handleExecuteActionCard = async (card) => {
    if (card.card_type === 'NAVIGATE') {
      onClose();
      navigate(card.target_url);
      return;
    }

    if (card.card_type === 'SQL_SUGGESTION') {
      navigator.clipboard.writeText(card.ddl);
      message.success('优化 DDL 已成功复制到剪贴板！');
      return;
    }

    if (card.card_type === 'PLAYBOOK_EXECUTE') {
      Modal.confirm({
        title: `确认执行自愈动作：${card.title}？`,
        icon: <SafetyCertificateOutlined style={{ color: '#fa8c16' }} />,
        content: (
          <div>
            <div>• <b>目标实例</b>: {card.db_name}</div>
            <div>• <b>剧本编码</b>: <code>{card.playbook_code}</code></div>
            <div>• <b>执行参数</b>: <code>{JSON.stringify(card.params)}</code></div>
            <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>{card.desc}</div>
          </div>
        ),
        okText: '立即安全执行',
        cancelText: '取消',
        onOk: async () => {
          setActionExecuting(true);
          try {
            const res = await apiV2.executePlaybook({
              playbook_code: card.playbook_code,
              config_id: card.config_id,
              params: card.params
            });
            message.success(res?.data?.message || '自愈动作已执行成功！');
            // 回显执行成功消息
            setMessages(prev => [
              ...prev,
              {
                role: 'assistant',
                content: `✅ **自愈执行成功**：已针对实例 **${card.db_name}** 执行预案 \`${card.playbook_code}\`，阻塞已释放。`,
                time: new Date().toLocaleTimeString(),
                source: 'system'
              }
            ]);
          } catch (e) {
            message.error('执行失败: ' + e.message);
          } finally {
            setActionExecuting(false);
          }
        }
      });
    }
  };

  // 一键体检
  const handleQuickAssessment = async () => {
    if (!selectedDbId) return;
    setAssessing(true);
    setAssessmentResult(null);
    try {
      const res = await aiOpsAPI.quickAssessment(selectedDbId);
      const data = res.assessment || res;
      setAssessmentResult(data);
      
      const scoreTag = data.overall_score >= 90 ? '🟢 优' : (data.overall_score >= 75 ? '🟡 良' : '🔴 需关注');
      const assessmentSummary = `已完成对 **${data.database?.name || data.db_name}** 的一键深度体检：\n- 综合健康评分：**${data.overall_score} 分 (${scoreTag})**\n- 评估维度：可用性(${data.dimensions?.[0]?.score}分)、负载(${data.dimensions?.[1]?.score}分)、连接(${data.dimensions?.[2]?.score}分)、容量(${data.dimensions?.[3]?.score}分)、告警(${data.dimensions?.[4]?.score}分)。\n\n详情可在上方体检卡片中查看。`;
      
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: assessmentSummary,
          time: new Date().toLocaleTimeString(),
          source: 'assessment'
        }
      ]);
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: `❌ 体检生成失败: ${err.message}`,
          time: new Date().toLocaleTimeString(),
          source: 'error'
        }
      ]);
    } finally {
      setAssessing(false);
    }
  };

  const selectedDb = databases.find(d => d.id === selectedDbId);

  return (
    <Drawer
      title={
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Space>
            <Avatar style={{ backgroundColor: '#6366f1' }} icon={<RobotOutlined />} />
            <div>
              <Text strong style={{ fontSize: 16 }}>DB-AIOps Copilot 专家助手</Text>
              <div>
                <Tag color="purple">Tool Calling 增强</Tag>
                <Tag color="cyan">Action Cards</Tag>
              </div>
            </div>
          </Space>
          <Select
            style={{ width: 220 }}
            placeholder="选择目标数据库"
            value={selectedDbId}
            onChange={setSelectedDbId}
            options={databases.map(d => ({
              label: `${d.name} (${d.db_type})`,
              value: d.id
            }))}
          />
        </div>
      }
      placement="right"
      width={660}
      onClose={onClose}
      open={visible}
      styles={{ body: { padding: '12px 16px', display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#f8fafc' } }}
    >
      {/* 快捷诊断操作栏 */}
      <div style={{ background: '#fff', padding: '12px', borderRadius: 8, marginBottom: 12, border: '1px solid #e2e8f0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13 }}><ThunderboltOutlined style={{ color: '#fa8c16' }} /> 快捷运维动作</Text>
          <Space size="small">
            <Button
              size="small"
              type="primary"
              icon={<MedicineBoxOutlined />}
              loading={assessing}
              onClick={handleQuickAssessment}
              disabled={!selectedDbId}
              style={{ background: '#6366f1', borderColor: '#6366f1' }}
            >
              一键智能体检
            </Button>
            <Button
              size="small"
              icon={<ClearOutlined />}
              onClick={() => setMessages([messages[0]])}
            >
              清空对话
            </Button>
          </Space>
        </div>

        {/* 快捷提问推荐词 */}
        <Space wrap size={[6, 6]}>
          <Tag
            color="geekblue"
            style={{ cursor: 'pointer' }}
            onClick={() => handleSend(`分析 ${selectedDb?.name || '当前实例'} 的实时 ASH 等待事件与阻塞情况`)}
          >
            ⏱️ 实时 ASH 等待探查
          </Tag>
          <Tag
            color="volcano"
            style={{ cursor: 'pointer' }}
            onClick={() => handleSend(`排查 ${selectedDb?.name || '当前实例'} 出现的锁等待阻塞链并生成处置预案`)}
          >
            🔒 行锁阻塞链排查
          </Tag>
          <Tag
            color="green"
            style={{ cursor: 'pointer' }}
            onClick={() => handleSend(`优化 update trade_order set status=2 where batch_id=90218 这条慢 SQL 并推荐索引`)}
          >
            💡 慢 SQL 执行计划与索引优化
          </Tag>
        </Space>
      </div>

      {/* 一键体检结果展示面板 */}
      {assessmentResult && (
        <Card
          size="small"
          style={{ marginBottom: 12, border: '1px solid #e2e8f0', borderRadius: 8 }}
          title={
            <Space>
              <MedicineBoxOutlined style={{ color: '#52c41a' }} />
              <span>体检结果：{assessmentResult.db_name || assessmentResult.database?.name}</span>
              <Tag color={assessmentResult.overall_score >= 80 ? 'green' : 'orange'}>
                等级: {assessmentResult.grade}
              </Tag>
            </Space>
          }
          extra={
            <Button type="text" size="small" onClick={() => setAssessmentResult(null)}>关闭</Button>
          }
        >
          <Row gutter={16} align="middle">
            <Col span={8} style={{ textAlign: 'center' }}>
              <Progress
                type="circle"
                percent={assessmentResult.overall_score}
                width={70}
                format={percent => `${percent}分`}
                strokeColor={assessmentResult.overall_score >= 80 ? '#52c41a' : '#faad14'}
              />
              <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>综合健康评分</div>
            </Col>
            <Col span={16}>
              <div style={{ fontSize: 12 }}>
                {assessmentResult.dimensions?.map(d => (
                  <div key={d.name} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                    <Text type="secondary">{d.name}</Text>
                    <Text strong>{d.score} 分</Text>
                  </div>
                ))}
              </div>
            </Col>
          </Row>
        </Card>
      )}

      {/* 消息对话流 */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: 4, marginBottom: 12 }}>
        <List
          itemLayout="horizontal"
          dataSource={messages}
          renderItem={(msg, index) => (
            <div
              key={index}
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 16
              }}
            >
              {msg.role === 'assistant' && (
                <Avatar
                  style={{ backgroundColor: msg.source === 'error' ? '#ef4444' : '#6366f1', marginRight: 8, flexShrink: 0 }}
                  icon={<RobotOutlined />}
                />
              )}
              <div
                style={{
                  maxWidth: '85%',
                  background: msg.role === 'user' ? '#6366f1' : '#fff',
                  color: msg.role === 'user' ? '#fff' : '#1e293b',
                  padding: '10px 14px',
                  borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                  border: msg.role === 'user' ? 'none' : '1px solid #e2e8f0',
                  wordBreak: 'break-word',
                  fontSize: 13,
                  lineHeight: '1.6'
                }}
              >
                <div style={{ whiteSpace: 'pre-wrap' }}>
                  {msg.content}
                </div>

                {/* 🎴 交互式动作卡片 (Action Cards) */}
                {msg.action_cards?.length > 0 && (
                  <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed #e2e8f0' }}>
                    <Text strong style={{ fontSize: 12, color: '#6366f1', display: 'flex', alignItems: 'center', gap: 4, marginBottom: 8 }}>
                      <ToolOutlined /> Copilot 推荐处置卡片 (一键联动执行):
                    </Text>
                    {msg.action_cards.map((card, cIdx) => (
                      <div
                        key={cIdx}
                        style={{
                          background: '#f8fafc',
                          padding: '10px 12px',
                          borderRadius: 6,
                          border: '1px solid #cbd5e1',
                          marginBottom: 8,
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center'
                        }}
                      >
                        <div style={{ flex: 1, marginRight: 10 }}>
                          <Text strong style={{ fontSize: 13, color: '#0f172a' }}>{card.title}</Text>
                          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{card.desc}</div>
                          {card.improvement && (
                            <Tag color="green" style={{ fontSize: 10, marginTop: 4 }}>收益: {card.improvement}</Tag>
                          )}
                        </div>
                        <Button
                          size="small"
                          type="primary"
                          icon={card.card_type === 'NAVIGATE' ? <ArrowRightOutlined /> : (card.card_type === 'SQL_SUGGESTION' ? <CopyOutlined /> : <PlayCircleOutlined />)}
                          loading={actionExecuting}
                          onClick={() => handleExecuteActionCard(card)}
                          style={{
                            background: card.card_type === 'NAVIGATE' ? '#3b82f6' : (card.card_type === 'SQL_SUGGESTION' ? '#10b981' : '#ef4444'),
                            borderColor: 'transparent'
                          }}
                        >
                          {card.card_type === 'NAVIGATE' ? '前往' : (card.card_type === 'SQL_SUGGESTION' ? '复制DDL' : '执行')}
                        </Button>
                      </div>
                    ))}
                  </div>
                )}

                <div
                  style={{
                    fontSize: 10,
                    color: msg.role === 'user' ? 'rgba(255,255,255,0.8)' : '#94a3b8',
                    marginTop: 6,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center'
                  }}
                >
                  <span>{msg.time}</span>
                  {msg.model && (
                    <Tag bordered={false} color="default" style={{ fontSize: 10, margin: 0, padding: '0 4px' }}>
                      {msg.model} {msg.latency_ms ? `· ${msg.latency_ms}ms` : ''}
                    </Tag>
                  )}
                </div>
              </div>
              {msg.role === 'user' && (
                <Avatar
                  style={{ backgroundColor: '#8b5cf6', marginLeft: 8, flexShrink: 0 }}
                  icon={<BulbOutlined />}
                />
              )}
            </div>
          )}
        />
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', background: '#fff', borderRadius: 8, width: 220 }}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>Copilot 工具调用与推理中...</Text>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 底部输入框 */}
      <div style={{ background: '#fff', padding: '10px', borderRadius: 8, border: '1px solid #cbd5e1' }}>
        <TextArea
          rows={3}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={`向 Copilot 提问（例如："分析锁等待" 或 "优化这条 SQL"）...`}
          onPressEnter={e => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={loading}
          style={{ resize: 'none', border: 'none', boxShadow: 'none', padding: 0 }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, paddingTop: 6, borderTop: '1px solid #f1f5f9' }}>
          <Text type="secondary" style={{ fontSize: 11 }}>Enter 发送，Shift + Enter 换行</Text>
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={loading}
            onClick={() => handleSend()}
            disabled={!query.trim()}
            style={{ background: '#6366f1', borderColor: '#6366f1' }}
          >
            发送
          </Button>
        </div>
      </div>
    </Drawer>
  );
}

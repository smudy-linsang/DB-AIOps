/**
 * CopilotDrawer - DB-AIOps 全局智能交互助手与一键体检工作台
 */
import React, { useState, useEffect, useRef } from 'react';
import {
  Drawer, Input, Button, Space, Typography, Tag, Card, Avatar,
  Spin, List, Divider, Select, Tooltip, Badge, Row, Col, Progress, Alert
} from 'antd';
import {
  RobotOutlined, SendOutlined, ClearOutlined, ThunderboltOutlined,
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined,
  MedicineBoxOutlined, BulbOutlined, DatabaseOutlined, SyncOutlined
} from '@ant-design/icons';
import { aiOpsAPI, databaseAPI } from '../services/api';

const { TextArea } = Input;
const { Text, Paragraph, Title } = Typography;

export default function CopilotDrawer({ visible, onClose, initialDbId }) {
  const [databases, setDatabases] = useState([]);
  const [selectedDbId, setSelectedDbId] = useState(initialDbId || null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [assessing, setAssessing] = useState(false);
  const [assessmentResult, setAssessmentResult] = useState(null);
  
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: '👋 您好！我是 **DB-AIOps 智能运维 Copilot 助手**。\n\n无论您是需要：\n- 🔍 诊断数据库当前性能瓶颈与等待事件\n- 🚨 分析活动告警并推导根因\n- ⚡ 优化慢 SQL 与索引推荐\n- 🩺 进行一键实例健康体检\n\n我都可以为您提供专业深度的辅助分析。请随时向我提问！',
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
  useEffect(() => {
    databaseAPI.list().then(res => {
      const list = res?.data || res?.items || (Array.isArray(res) ? res : []);
      setDatabases(list);
      if (!selectedDbId && list.length > 0) {
        setSelectedDbId(list[0].id);
      }
    }).catch(() => {});
  }, []);

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
        latency_ms: res.latency_ms
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

  // 一键体检
  const handleQuickAssessment = async () => {
    if (!selectedDbId) return;
    setAssessing(true);
    setAssessmentResult(null);
    try {
      const res = await aiOpsAPI.quickAssessment(selectedDbId);
      const data = res.assessment || res;
      setAssessmentResult(data);
      
      // 同时在聊天流中补充一条摘要
      const scoreTag = data.overall_score >= 90 ? '🟢 优' : (data.overall_score >= 75 ? '🟡 良' : '🔴 需关注');
      const assessmentSummary = `已完成对 **${data.database?.name}** 的一键深度体检：\n- 综合健康评分：**${data.overall_score} 分 (${scoreTag})**\n- 发现风险项：**${data.risk_items?.length || 0} 个**\n- 评估维度：可用性(${data.dimensions?.[0]?.score}分)、负载(${data.dimensions?.[1]?.score}分)、告警(${data.dimensions?.[2]?.score}分)、容量(${data.dimensions?.[3]?.score}分)。\n\n详情可在右侧面板上方【体检卡片】中查看。`;
      
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
            <Avatar style={{ backgroundColor: '#1890ff' }} icon={<RobotOutlined />} />
            <div>
              <Text strong style={{ fontSize: 16 }}>DB-AIOps Copilot 助手</Text>
              <div>
                <Tag color="blue">智能化 2.2</Tag>
                <Tag color="purple">双引擎模式</Tag>
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
      width={620}
      onClose={onClose}
      open={visible}
      styles={{ body: { padding: '12px 16px', display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#fafafa' } }}
    >
      {/* 快捷诊断操作栏 */}
      <div style={{ background: '#fff', padding: '12px', borderRadius: 8, marginBottom: 12, border: '1px solid #f0f0f0' }}>
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
            onClick={() => handleSend(`分析 ${selectedDb?.name || '当前实例'} 的最新性能指标与连接状态`)}
          >
            📊 当前性能指标快照
          </Tag>
          <Tag
            color="volcano"
            style={{ cursor: 'pointer' }}
            onClick={() => handleSend(`排查 ${selectedDb?.name || '当前实例'} 出现的锁等待或活动告警根因`)}
          >
            🚨 锁与告警根因排查
          </Tag>
          <Tag
            color="green"
            style={{ cursor: 'pointer' }}
            onClick={() => handleSend(`针对 ${selectedDb?.name || '数据库'} 提供慢 SQL 与索引优化建议指南`)}
          >
            💡 慢 SQL 与索引建议
          </Tag>
        </Space>
      </div>

      {/* 一键体检结果展示面板 */}
      {assessmentResult && (
        <Card
          size="small"
          style={{ marginBottom: 12, border: '1px solid #d9d9d9', borderRadius: 8 }}
          title={
            <Space>
              <MedicineBoxOutlined style={{ color: '#52c41a' }} />
              <span>体检结果：{assessmentResult.database?.name}</span>
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
          {assessmentResult.risk_items?.length > 0 && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #f0f0f0' }}>
              <Text strong style={{ fontSize: 12, color: '#cf1322' }}>⚠️ 识别出的风险项：</Text>
              {assessmentResult.risk_items.map((r, i) => (
                <div key={i} style={{ fontSize: 12, color: '#555', marginTop: 2 }}>
                  • <b>{r.title}</b>: {r.desc}
                </div>
              ))}
            </div>
          )}
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
                  style={{ backgroundColor: msg.source === 'error' ? '#ff4d4f' : '#1890ff', marginRight: 8, flexShrink: 0 }}
                  icon={<RobotOutlined />}
                />
              )}
              <div
                style={{
                  maxWidth: '82%',
                  background: msg.role === 'user' ? '#1890ff' : '#fff',
                  color: msg.role === 'user' ? '#fff' : '#262626',
                  padding: '10px 14px',
                  borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                  border: msg.role === 'user' ? 'none' : '1px solid #e8e8e8',
                  wordBreak: 'break-word',
                  fontSize: 13,
                  lineHeight: '1.6'
                }}
              >
                <div style={{ whiteSpace: 'pre-wrap' }}>
                  {msg.content}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: msg.role === 'user' ? 'rgba(255,255,255,0.8)' : '#999',
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
                  style={{ backgroundColor: '#87d068', marginLeft: 8, flexShrink: 0 }}
                  icon={<BulbOutlined />}
                />
              )}
            </div>
          )}
        />
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', background: '#fff', borderRadius: 8, width: 180 }}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>Copilot 思考分析中...</Text>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 底部输入框 */}
      <div style={{ background: '#fff', padding: '10px', borderRadius: 8, border: '1px solid #d9d9d9' }}>
        <TextArea
          rows={3}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={`向 Copilot 提问（例如："分析 ${selectedDb?.name || '当前实例'} 出现的锁等待或指标异常"）...`}
          onPressEnter={e => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={loading}
          style={{ resize: 'none', border: 'none', boxShadow: 'none', padding: 0 }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, paddingTop: 6, borderTop: '1px solid #f0f0f0' }}>
          <Text type="secondary" style={{ fontSize: 11 }}>Enter 发送，Shift + Enter 换行</Text>
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={loading}
            onClick={() => handleSend()}
            disabled={!query.trim()}
          >
            发送
          </Button>
        </div>
      </div>
    </Drawer>
  );
}

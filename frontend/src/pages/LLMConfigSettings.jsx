/**
 * LLMConfigSettings - 大模型与 API Key 在线配置管理页面
 * 支持：实时配置查询、动态保存持久化到 .env、在线连通性即时测试与大模型预设快捷填入
 */
import React, { useState, useEffect } from 'react';
import {
  Card, Form, Input, Switch, Button, Slider, InputNumber, Row, Col,
  Alert, Space, Typography, Tag, Divider, message, Select, Modal, Spin
} from 'antd';
import {
  RobotOutlined, KeyOutlined, ApiOutlined, CheckCircleOutlined,
  ThunderboltOutlined, ExperimentOutlined, SaveOutlined, ReloadOutlined,
  SafetyCertificateOutlined, InfoCircleOutlined
} from '@ant-design/icons';
import { aiOpsAPI } from '../services/api';

const { Title, Text, Paragraph } = Typography;
const { Option } = Select;

// 常用主流大模型服务预设
const MODEL_PRESETS = [
  {
    label: 'MiniMax 名之梦 (TokenPlanPlus / abab6.5s / MiniMax-Text-01)',
    value: 'minimax',
    baseUrl: 'https://api.minimax.chat/v1',
    model: 'MiniMax-Text-01',
  },
  {
    label: 'Google Gemini (Gemini 1.5 Pro / Flash 官方兼容端点)',
    value: 'gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    model: 'gemini-1.5-pro',
  },
  {
    label: 'DeepSeek 官方 API (推荐/高性价比)',
    value: 'deepseek',
    baseUrl: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
  },
  {
    label: 'OpenAI 官方 (GPT-4o / GPT-4o-mini)',
    value: 'openai',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
  },
  {
    label: '阿里云通义千问 (Qwen-Plus / Qwen-Turbo)',
    value: 'qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
  },
  {
    label: 'Moonshot 月之暗面 (Kimi)',
    value: 'moonshot',
    baseUrl: 'https://api.moonshot.cn/v1',
    model: 'moonshot-v1-8k',
  },
  {
    label: '本地私有化 Ollama (无需外部 Key)',
    value: 'ollama',
    baseUrl: 'http://localhost:11434/v1',
    model: 'qwen2.5:14b-instruct',
  },
];

export default function LLMConfigSettings() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [currentConfig, setCurrentConfig] = useState(null);

  // 加载当前大模型配置
  const loadConfig = async () => {
    setLoading(true);
    try {
      const res = await aiOpsAPI.getLlmConfig();
      const data = res?.data || res;
      setCurrentConfig(data);
      form.setFieldsValue({
        llm_enabled: data.llm_enabled,
        llm_provider: data.llm_provider || 'openai_compat',
        llm_base_url: data.llm_base_url || 'https://api.deepseek.com/v1',
        llm_model: data.llm_model || 'deepseek-chat',
        llm_api_key: '', // 密钥安全起见，初始为空
        llm_temperature: data.llm_temperature ?? 0.1,
        llm_max_tokens: data.llm_max_tokens ?? 2048,
        llm_timeout_sec: data.llm_timeout_sec ?? 25,
        agent_enabled: data.agent_enabled ?? true,
      });
    } catch (e) {
      message.error('加载大模型配置失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  // 快速选择预设配置
  const handlePresetSelect = (presetKey) => {
    const preset = MODEL_PRESETS.find(p => p.value === presetKey);
    if (preset) {
      form.setFieldsValue({
        llm_base_url: preset.baseUrl,
        llm_model: preset.model,
      });
      message.info(`已应用【${preset.label}】默认参数`);
    }
  };

  // 测试连通性
  const handleTestConnection = async () => {
    try {
      const values = await form.validateFields();
      setTesting(true);
      setTestResult(null);

      const res = await aiOpsAPI.testLlm({
        llm_base_url: values.llm_base_url,
        llm_api_key: values.llm_api_key,
        llm_model: values.llm_model,
      });

      const data = res?.data || res;
      setTestResult(data);
      if (data.ok) {
        message.success(`大模型连通测试成功！响应耗时: ${data.latency_ms}ms`);
      } else {
        message.error('大模型连接失败: ' + (data.error || '未知错误'));
      }
    } catch (e) {
      message.error('表单校验或连接异常: ' + e.message);
    } finally {
      setTesting(false);
    }
  };

  // 保存配置
  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const res = await aiOpsAPI.updateLlmConfig(values);
      message.success(res?.data?.message || '大模型与 API Key 配置已成功保存生效！');
      loadConfig();
      setTestResult(null);
    } catch (e) {
      message.error('保存配置失败: ' + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: '0 auto' }}>
      {/* 顶部标题横幅 */}
      <Card
        style={{
          background: 'linear-gradient(135deg, #111827 0%, #1e1b4b 100%)',
          borderRadius: 8,
          marginBottom: 20,
          border: '1px solid #374151'
        }}
      >
        <Row align="middle" justify="space-between">
          <Col>
            <Space size={16} align="center">
              <div style={{
                background: 'linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%)',
                padding: 12,
                borderRadius: 8,
                color: '#fff',
                fontSize: 24,
                display: 'flex',
                alignItems: 'center'
              }}>
                <RobotOutlined />
              </div>
              <div>
                <Title level={4} style={{ color: '#fff', margin: 0 }}>
                  LLM 大模型与 AI Copilot 智能服务配置
                </Title>
                <Text style={{ color: '#9ca3af', fontSize: 13 }}>
                  配置 OpenAI 兼容标准的大模型 API Key，驱动 RCA 3.0 深度归因、AI Copilot 专家对话与自主排查 Agent。
                </Text>
              </div>
            </Space>
          </Col>
          <Col>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={loadConfig}>刷新</Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Spin spinning={loading}>
        <Form form={form} layout="vertical">
          <Row gutter={24}>
            {/* 左侧：核心参数配置 */}
            <Col span={15}>
              <Card
                title={<Space><KeyOutlined style={{ color: '#8b5cf6' }} /><span>大模型接入设置</span></Space>}
                style={{ borderRadius: 8, marginBottom: 20 }}
              >
                {/* 常用厂商快捷预设 */}
                <Form.Item label="快捷选用主流服务商配置模板">
                  <Select placeholder="选择服务商快速填入..." onChange={handlePresetSelect} allowClear>
                    {MODEL_PRESETS.map(p => (
                      <Option key={p.value} value={p.value}>{p.label}</Option>
                    ))}
                  </Select>
                </Form.Item>

                <Divider style={{ margin: '16px 0' }} />

                <Form.Item
                  label="API 接入端点 (Base URL)"
                  name="llm_base_url"
                  rules={[{ required: true, message: '请输入 Base URL' }]}
                  tooltip="支持任意 OpenAI 兼容的 API 代理地址，如 DeepSeek、OpenAI、Ollama 或中转服务"
                >
                  <Input placeholder="例如: https://api.deepseek.com/v1 或 http://localhost:11434/v1" />
                </Form.Item>

                <Form.Item
                  label="大模型 API Key"
                  name="llm_api_key"
                  tooltip="您的 API 密钥已通过 AES-256 加密保存。如果不需要修改已配置密钥，保持留空即可。"
                >
                  <Input.Password
                    placeholder={currentConfig?.has_api_key ? `已配置: ${currentConfig?.llm_api_key_masked} (输入新Key将覆盖)` : "请输入 sk-... 格式的 API Key"}
                    prefix={<KeyOutlined style={{ color: '#9ca3af' }} />}
                  />
                </Form.Item>

                <Form.Item
                  label="模型名称 (Model ID)"
                  name="llm_model"
                  rules={[{ required: true, message: '请输入模型名称' }]}
                  tooltip="例如 deepseek-chat, gpt-4o-mini, qwen-plus 等"
                >
                  <Input placeholder="例如: deepseek-chat 或 qwen-plus" />
                </Form.Item>

                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item
                      label="生成发散度 (Temperature)"
                      name="llm_temperature"
                      tooltip="越低越精准严谨（推荐运维场景设置为 0.0 ~ 0.2）"
                    >
                      <Slider min={0} max={1} step={0.05} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item
                      label="最大响应 Tokens"
                      name="llm_max_tokens"
                    >
                      <InputNumber min={512} max={8192} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                </Row>

                <Divider style={{ margin: '16px 0' }} />

                <Space size="middle">
                  <Button
                    icon={<ApiOutlined />}
                    loading={testing}
                    onClick={handleTestConnection}
                  >
                    在线测试连通性
                  </Button>
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={saving}
                    onClick={handleSave}
                    style={{ background: '#6366f1', borderColor: '#6366f1' }}
                  >
                    保存并立即生效
                  </Button>
                </Space>

                {/* 连通性测试报告面板 */}
                {testResult && (
                  <Alert
                    style={{ marginTop: 16 }}
                    type={testResult.ok ? 'success' : 'error'}
                    showIcon
                    message={testResult.ok ? '大模型接口响应正常' : '连接失败'}
                    description={
                      <div style={{ fontSize: 12 }}>
                        {testResult.ok ? (
                          <>
                            <div>• <b>模型响应</b>: {testResult.reply || 'pong'}</div>
                            <div>• <b>网络延迟</b>: {testResult.latency_ms} 毫秒</div>
                            <div>• <b>模型标识</b>: {testResult.model}</div>
                          </>
                        ) : (
                          <div>• <b>错误详情</b>: {testResult.error}</div>
                        )}
                      </div>
                    }
                  />
                )}
              </Card>
            </Col>

            {/* 右侧：开关与状态提示 */}
            <Col span={9}>
              <Card
                title={<Space><SafetyCertificateOutlined style={{ color: '#10b981' }} /><span>特性与开关</span></Space>}
                style={{ borderRadius: 8, marginBottom: 20 }}
              >
                <Form.Item
                  label="启用 AI 智能诊断与 Copilot"
                  name="llm_enabled"
                  valuePropName="checked"
                  tooltip="开启后，Copilot 和 RCA 归因将优先使用大模型进行分析"
                >
                  <Switch checkedChildren="已开启" unCheckedChildren="已停用" />
                </Form.Item>

                <Form.Item
                  label="启用 Agentic 深度排查"
                  name="agent_enabled"
                  valuePropName="checked"
                  tooltip="允许大模型调用底层工具链进行多步深度排查"
                >
                  <Switch checkedChildren="已开启" unCheckedChildren="已停用" />
                </Form.Item>

                <Alert
                  type="info"
                  showIcon
                  message="企业安全提示"
                  description={
                    <div style={{ fontSize: 12, color: '#4b5563', lineHeight: 1.6 }}>
                      1. 平台遵循 <b>“只分析、不写库”</b> 原则，大模型仅处理聚合脱敏指标与告警日志，绝不泄露业务私密行数据。<br/>
                      2. 保存后，密钥将自动更新至服务端安全环境变量 <code>.env</code> 并对所有工作进程热生效。
                    </div>
                  }
                />
              </Card>
            </Col>
          </Row>
        </Form>
      </Spin>
    </div>
  );
}

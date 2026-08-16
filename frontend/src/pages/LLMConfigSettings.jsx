/**
 * LLMConfigSettings - DB-AIOps v2.0 多大模型智能路由与高可用网关控制台
 * 支持：
 * 1. 凭据连接池 (Credentials Pool)：多模型/多Key轮询、单节点Ping探活、429状态冷却监控
 * 2. 场景智能路由 (Scene Routing Matrix)：按场景配置主备容灾链与超参数
 * 3. 基础全局配置与预设快捷填入
 */
import React, { useState, useEffect } from 'react';
import {
  Card, Form, Input, Switch, Button, Slider, InputNumber, Row, Col,
  Alert, Space, Typography, Tag, Divider, message, Select, Modal, Spin,
  Tabs, Table, Tooltip, Popconfirm, Badge, Progress
} from 'antd';
import {
  RobotOutlined, KeyOutlined, ApiOutlined, CheckCircleOutlined,
  ThunderboltOutlined, ExperimentOutlined, SaveOutlined, ReloadOutlined,
  SafetyCertificateOutlined, InfoCircleOutlined, PlusOutlined, DeleteOutlined,
  EditOutlined, SyncOutlined, BranchesOutlined, AppstoreOutlined
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
  const [activeTab, setActiveTab] = useState('pool');
  const [loading, setLoading] = useState(false);

  // Tab 1: 凭据连接池状态
  const [credentials, setCredentials] = useState([]);
  const [credModalVisible, setCredModalVisible] = useState(false);
  const [editingCred, setEditingCred] = useState(null);
  const [credForm] = Form.useForm();
  const [pingingId, setPingingId] = useState(null);

  // Tab 2: 场景智能路由
  const [routes, setRoutes] = useState([]);
  const [routeModalVisible, setRouteModalVisible] = useState(false);
  const [editingRoute, setEditingRoute] = useState(null);
  const [routeForm] = Form.useForm();

  // Tab 3: 全局/兼容设置
  const [globalForm] = Form.useForm();
  const [savingGlobal, setSavingGlobal] = useState(false);
  const [testingGlobal, setTestingGlobal] = useState(false);
  const [globalTestResult, setGlobalTestResult] = useState(null);
  const [currentConfig, setCurrentConfig] = useState(null);

  // 加载凭据池与路由
  const loadData = async () => {
    setLoading(true);
    try {
      // 1. 加载全局配置
      const cfgRes = await aiOpsAPI.getLlmConfig();
      const cfgData = cfgRes?.data || cfgRes;
      setCurrentConfig(cfgData);
      globalForm.setFieldsValue({
        llm_enabled: cfgData.llm_enabled,
        llm_provider: cfgData.llm_provider || 'openai_compat',
        llm_base_url: cfgData.llm_base_url || 'https://api.deepseek.com/v1',
        llm_model: cfgData.llm_model || 'deepseek-chat',
        llm_api_key: '',
        llm_temperature: cfgData.llm_temperature ?? 0.1,
        llm_max_tokens: cfgData.llm_max_tokens ?? 2048,
        llm_timeout_sec: cfgData.llm_timeout_sec ?? 25,
        agent_enabled: cfgData.agent_enabled ?? true,
      });

      // 2. 加载凭据池
      const credRes = await aiOpsAPI.getCredentials();
      setCredentials(credRes?.credentials || credRes?.data?.credentials || []);

      // 3. 加载场景路由
      const routeRes = await aiOpsAPI.getRoutes();
      setRoutes(routeRes?.routes || routeRes?.data?.routes || []);
    } catch (e) {
      message.error('加载大模型配置失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // -------------------------------------------------------------
  // 凭据池操作
  // -------------------------------------------------------------
  const handleOpenAddCred = () => {
    setEditingCred(null);
    credForm.resetFields();
    credForm.setFieldsValue({
      provider_type: 'minimax',
      base_url: 'https://api.minimax.chat/v1',
      model_name: 'MiniMax-Text-01',
      priority: 10,
      weight: 1,
      is_active: true,
    });
    setCredModalVisible(true);
  };

  const handleOpenEditCred = (record) => {
    setEditingCred(record);
    credForm.setFieldsValue({
      name: record.name,
      provider_type: record.provider_type,
      base_url: record.base_url,
      model_name: record.model_name,
      api_key: '', // 保持为空，输入新值才覆盖
      priority: record.priority,
      weight: record.weight,
      is_active: record.is_active,
    });
    setCredModalVisible(true);
  };

  const handleSaveCred = async () => {
    try {
      const values = await credForm.validateFields();
      if (editingCred) {
        await aiOpsAPI.updateCredential(editingCred.id, values);
        message.success(`凭据【${values.name}】已更新！`);
      } else {
        await aiOpsAPI.createCredential(values);
        message.success(`凭据【${values.name}】已成功加入连接池！`);
      }
      setCredModalVisible(false);
      loadData();
    } catch (e) {
      message.error('保存凭据失败: ' + e.message);
    }
  };

  const handleDeleteCred = async (id) => {
    try {
      await aiOpsAPI.deleteCredential(id);
      message.success('凭据已安全删除');
      loadData();
    } catch (e) {
      message.error('删除失败: ' + e.message);
    }
  };

  const handlePingCred = async (record) => {
    setPingingId(record.id);
    try {
      const res = await aiOpsAPI.pingCredential(record.id);
      const data = res?.data || res;
      if (data.ok) {
        message.success(`【${record.name}】连通正常！耗时: ${data.latency_ms}ms 回复: ${data.reply}`);
      } else {
        message.error(`【${record.name}】连通异常: ${data.error || '未知错误'}`);
      }
      loadData();
    } catch (e) {
      message.error('探活异常: ' + e.message);
    } finally {
      setPingingId(null);
    }
  };

  // -------------------------------------------------------------
  // 场景路由操作
  // -------------------------------------------------------------
  const handleOpenEditRoute = (record) => {
    setEditingRoute(record);
    routeForm.setFieldsValue({
      scene_name: record.scene_name,
      primary_credential_id: record.primary_credential_id,
      fallback_credential_ids: record.fallback_credential_ids || [],
      temperature: record.temperature,
      timeout_sec: record.timeout_sec,
      max_tokens: record.max_tokens,
    });
    setRouteModalVisible(true);
  };

  const handleSaveRoute = async () => {
    try {
      const values = await routeForm.validateFields();
      await aiOpsAPI.updateRoute({
        scene_code: editingRoute.scene_code,
        ...values,
      });
      message.success(`场景【${editingRoute.scene_name}】路由策略已生效！`);
      setRouteModalVisible(false);
      loadData();
    } catch (e) {
      message.error('更新路由策略失败: ' + e.message);
    }
  };

  // -------------------------------------------------------------
  // 全局配置操作
  // -------------------------------------------------------------
  const handleSaveGlobal = async () => {
    try {
      const values = await globalForm.validateFields();
      setSavingGlobal(true);
      await aiOpsAPI.updateLlmConfig(values);
      message.success('全局设置已成功持久化并生效！');
      loadData();
    } catch (e) {
      message.error('保存失败: ' + e.message);
    } finally {
      setSavingGlobal(false);
    }
  };

  const handleTestGlobal = async () => {
    try {
      const values = await globalForm.validateFields();
      setTestingGlobal(true);
      setGlobalTestResult(null);
      const res = await aiOpsAPI.testLlm({
        llm_base_url: values.llm_base_url,
        llm_api_key: values.llm_api_key,
        llm_model: values.llm_model,
      });
      const data = res?.data || res;
      setGlobalTestResult(data);
      if (data.ok) {
        message.success(`测试连通成功！耗时: ${data.latency_ms}ms`);
      } else {
        message.error('连接失败: ' + (data.error || '未知错误'));
      }
    } catch (e) {
      message.error('测试异常: ' + e.message);
    } finally {
      setTestingGlobal(false);
    }
  };

  const credColumns = [
    {
      title: '配置名称',
      dataIndex: 'name',
      render: (text, r) => (
        <div>
          <Text strong>{text}</Text>
          <div><Tag color="geekblue">{r.provider_type}</Tag><Tag color="purple">{r.model_name}</Tag></div>
        </div>
      ),
    },
    {
      title: '接入端点 (Base URL)',
      dataIndex: 'base_url',
      ellipsis: true,
    },
    {
      title: '健康状态',
      dataIndex: 'is_healthy',
      width: 120,
      render: (healthy, r) => {
        if (!r.is_active) return <Tag color="default">已禁用</Tag>;
        if (r.cooldown_until) return <Tag color="warning"><SyncOutlined spin /> 429冷却</Tag>;
        return healthy ? <Tag color="success">🟢 健康</Tag> : <Tag color="error">🔴 异常</Tag>;
      },
    },
    {
      title: '时延 / 优先级',
      width: 140,
      render: (_, r) => (
        <div style={{ fontSize: 12 }}>
          <div>响应: <b>{r.last_latency_ms ? `${r.last_latency_ms}ms` : '-'}</b></div>
          <div style={{ color: '#888' }}>优先级: P{r.priority} · 权重 {r.weight}</div>
        </div>
      ),
    },
    {
      title: '操作',
      width: 180,
      render: (_, r) => (
        <Space size="small">
          <Button
            size="small"
            icon={<ApiOutlined />}
            loading={pingingId === r.id}
            onClick={() => handlePingCred(r)}
          >
            探活
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleOpenEditCred(r)}
          />
          <Popconfirm title="确定要移除该凭据？" onConfirm={() => handleDeleteCred(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const routeColumns = [
    {
      title: '业务场景',
      dataIndex: 'scene_name',
      render: (text, r) => (
        <div>
          <Text strong>{text}</Text>
          <div style={{ fontSize: 11, color: '#888' }}>{r.description}</div>
        </div>
      ),
    },
    {
      title: '首选模型 (Primary)',
      dataIndex: 'primary_credential_name',
      render: (text, r) => (
        <Tag color="cyan" style={{ fontSize: 12, padding: '2px 8px' }}>
          ⭐ {text}
        </Tag>
      ),
    },
    {
      title: '容灾降级链 (Failover Fallbacks)',
      dataIndex: 'fallback_credential_ids',
      render: (ids) => {
        if (!ids || ids.length === 0) return <Text type="secondary">自动继承全局连接池</Text>;
        const names = ids.map(id => credentials.find(c => c.id === id)?.name || `#${id}`);
        return (
          <Space wrap size={[4, 4]}>
            {names.map((n, i) => (
              <Tag key={i} color="blue">↳ {n}</Tag>
            ))}
          </Space>
        );
      },
    },
    {
      title: '超参数 (Temp / Timeout)',
      width: 150,
      render: (_, r) => (
        <div style={{ fontSize: 12 }}>
          <div>Temp: <b>{r.temperature}</b></div>
          <div style={{ color: '#888' }}>Timeout: {r.timeout_sec}s · Max: {r.max_tokens}</div>
        </div>
      ),
    },
    {
      title: '操作',
      width: 100,
      render: (_, r) => (
        <Button size="small" type="link" icon={<EditOutlined />} onClick={() => handleOpenEditRoute(r)}>
          配置策略
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      {/* 顶部标题横幅 */}
      <Card
        style={{
          background: 'linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%)',
          borderRadius: 8,
          marginBottom: 20,
          border: '1px solid #334155',
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
                alignItems: 'center',
              }}>
                <BranchesOutlined />
              </div>
              <div>
                <Title level={4} style={{ color: '#fff', margin: 0 }}>
                  多大模型智能路由与高可用网关控制台 (LLM Smart Router v2.0)
                </Title>
                <Text style={{ color: '#94a3b8', fontSize: 13 }}>
                  借鉴 CLIProxyAPI 架构设计：支持 MiniMax、Gemini 1.5 Pro、DeepSeek 等多模型池化、场景分流与 429 毫秒级链式容灾降级。
                </Text>
              </div>
            </Space>
          </Col>
          <Col>
            <Button icon={<ReloadOutlined />} onClick={loadData}>刷新数据</Button>
          </Col>
        </Row>
      </Card>

      <Spin spinning={loading}>
        <Tabs
          type="card"
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'pool',
              label: <span><AppstoreOutlined /> 凭据连接池 (Credentials Pool)</span>,
              children: (
                <Card
                  title={
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Space>
                        <KeyOutlined style={{ color: '#8b5cf6' }} />
                        <span>已纳管模型服务商凭据</span>
                        <Badge count={credentials.length} style={{ backgroundColor: '#6366f1' }} />
                      </Space>
                      <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenAddCred} style={{ background: '#6366f1', borderColor: '#6366f1' }}>
                        添加模型凭据
                      </Button>
                    </div>
                  }
                  style={{ borderRadius: 8 }}
                >
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="凭据池负载均衡与容灾机制"
                    description="系统在执行诊断和 Copilot 交互时，将优先选择健康且优先级最高（P1 > P10）的凭据；当某凭据触发 429 限流或超时，路由器将自动切换至备选凭据，保证零感知连续服务。"
                  />
                  <Table
                    rowKey="id"
                    columns={credColumns}
                    dataSource={credentials}
                    pagination={false}
                    size="middle"
                  />
                </Card>
              ),
            },
            {
              key: 'routes',
              label: <span><BranchesOutlined /> 场景智能路由 (Scene Routing)</span>,
              children: (
                <Card
                  title={<Space><BranchesOutlined style={{ color: '#10b981' }} /><span>运维业务场景分流策略矩阵</span></Space>}
                  style={{ borderRadius: 8 }}
                >
                  <Alert
                    type="success"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="场景最优化匹配建议"
                    description="建议将【Copilot 专家日常对话】绑定至高性价比、低时延的 MiniMax TokenPlanPlus / DeepSeek；将【RCA 3.0 深度归因】绑定至超强逻辑推理的 Google Gemini 1.5 Pro。"
                  />
                  <Table
                    rowKey="id"
                    columns={routeColumns}
                    dataSource={routes}
                    pagination={false}
                    size="middle"
                  />
                </Card>
              ),
            },
            {
              key: 'global',
              label: <span><SafetyCertificateOutlined /> 全局开关与快速预设</span>,
              children: (
                <Row gutter={24}>
                  <Col span={15}>
                    <Card title={<Space><KeyOutlined style={{ color: '#8b5cf6' }} /><span>单点快速设置 (兼容模式)</span></Space>} style={{ borderRadius: 8 }}>
                      <Form form={globalForm} layout="vertical">
                        <Form.Item label="快捷选用主流服务商配置模板">
                          <Select
                            placeholder="选择服务商快速填入..."
                            onChange={(val) => {
                              const p = MODEL_PRESETS.find(x => x.value === val);
                              if (p) globalForm.setFieldsValue({ llm_base_url: p.baseUrl, llm_model: p.model });
                            }}
                            allowClear
                          >
                            {MODEL_PRESETS.map(p => <Option key={p.value} value={p.value}>{p.label}</Option>)}
                          </Select>
                        </Form.Item>

                        <Form.Item label="API 接入端点 (Base URL)" name="llm_base_url" rules={[{ required: true }]}>
                          <Input />
                        </Form.Item>

                        <Form.Item label="大模型 API Key" name="llm_api_key">
                          <Input.Password placeholder={currentConfig?.has_api_key ? `已配置: ${currentConfig?.llm_api_key_masked} (输入新Key覆盖)` : "sk-..."} />
                        </Form.Item>

                        <Form.Item label="模型名称 (Model ID)" name="llm_model" rules={[{ required: true }]}>
                          <Input />
                        </Form.Item>

                        <Space size="middle">
                          <Button icon={<ApiOutlined />} loading={testingGlobal} onClick={handleTestGlobal}>
                            在线测试连通性
                          </Button>
                          <Button type="primary" icon={<SaveOutlined />} loading={savingGlobal} onClick={handleSaveGlobal} style={{ background: '#6366f1', borderColor: '#6366f1' }}>
                            保存并立即生效
                          </Button>
                        </Space>

                        {globalTestResult && (
                          <Alert
                            style={{ marginTop: 16 }}
                            type={globalTestResult.ok ? 'success' : 'error'}
                            showIcon
                            message={globalTestResult.ok ? '大模型连接正常' : '连接失败'}
                            description={<div>• 响应: {globalTestResult.reply || 'pong'} · 时延: {globalTestResult.latency_ms}ms</div>}
                          />
                        )}
                      </Form>
                    </Card>
                  </Col>

                  <Col span={9}>
                    <Card title={<Space><SafetyCertificateOutlined style={{ color: '#10b981' }} /><span>平台特性开关</span></Space>} style={{ borderRadius: 8 }}>
                      <Form form={globalForm} layout="vertical">
                        <Form.Item label="启用 AI 智能诊断与 Copilot" name="llm_enabled" valuePropName="checked">
                          <Switch checkedChildren="已开启" unCheckedChildren="已停用" />
                        </Form.Item>
                        <Form.Item label="启用 Agentic 深度排查" name="agent_enabled" valuePropName="checked">
                          <Switch checkedChildren="已开启" unCheckedChildren="已停用" />
                        </Form.Item>
                      </Form>
                    </Card>
                  </Col>
                </Row>
              ),
            },
          ]}
        />
      </Spin>

      {/* 凭据添加/编辑弹窗 */}
      <Modal
        title={editingCred ? `编辑凭据：${editingCred.name}` : '添加大模型服务商凭据'}
        open={credModalVisible}
        onOk={handleSaveCred}
        onCancel={() => setCredModalVisible(false)}
        okText="确认保存"
        cancelText="取消"
        width={560}
      >
        <Form form={credForm} layout="vertical">
          <Form.Item label="快速套用服务商模板">
            <Select
              placeholder="选择服务商快速填入..."
              onChange={(val) => {
                const p = MODEL_PRESETS.find(x => x.value === val);
                if (p) credForm.setFieldsValue({ provider_type: val, base_url: p.baseUrl, model_name: p.model, name: p.label.split(' ')[0] });
              }}
              allowClear
            >
              {MODEL_PRESETS.map(p => <Option key={p.value} value={p.value}>{p.label}</Option>)}
            </Select>
          </Form.Item>

          <Form.Item label="配置名称" name="name" rules={[{ required: true, message: '请输入配置名称' }]}>
            <Input placeholder="例如: MiniMax-主力账号01 / Gemini-1.5Pro" />
          </Form.Item>

          <Form.Item label="服务商类型" name="provider_type" rules={[{ required: true }]}>
            <Select>
              <Option value="minimax">MiniMax 名之梦</Option>
              <Option value="gemini">Google Gemini</Option>
              <Option value="deepseek">DeepSeek 深度求索</Option>
              <Option value="openai">OpenAI 官方</Option>
              <Option value="qwen">阿里通义千问</Option>
              <Option value="moonshot">月之暗面 Kimi</Option>
              <Option value="ollama">本地私有 Ollama</Option>
              <Option value="custom">自定义兼容端点</Option>
            </Select>
          </Form.Item>

          <Form.Item label="接入端点 (Base URL)" name="base_url" rules={[{ required: true, message: '请输入 Base URL' }]}>
            <Input placeholder="例如: https://api.minimax.chat/v1" />
          </Form.Item>

          <Form.Item label="大模型 API Key" name="api_key">
            <Input.Password placeholder="请输入 sk-... 格式密钥 (编辑时留空表示不修改)" />
          </Form.Item>

          <Form.Item label="模型名称 (Model ID)" name="model_name" rules={[{ required: true, message: '请输入模型名称' }]}>
            <Input placeholder="例如: MiniMax-Text-01 / gemini-1.5-pro" />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="优先级 (数字越小越优先)" name="priority">
                <InputNumber min={1} max={100} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="负载权重" name="weight">
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item label="启用此凭据" name="is_active" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* 场景路由编辑弹窗 */}
      <Modal
        title={`配置场景路由策略：${editingRoute?.scene_name}`}
        open={routeModalVisible}
        onOk={handleSaveRoute}
        onCancel={() => setRouteModalVisible(false)}
        okText="保存策略"
        cancelText="取消"
        width={560}
      >
        <Form form={routeForm} layout="vertical">
          <Form.Item label="首选主力模型凭据 (Primary)" name="primary_credential_id">
            <Select placeholder="选择主力凭据..." allowClear>
              {credentials.map(c => (
                <Option key={c.id} value={c.id}>{c.name} ({c.model_name})</Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item label="备选容灾降级模型链 (Failover Fallbacks)" name="fallback_credential_ids">
            <Select mode="multiple" placeholder="选择备用凭据（按顺序尝试）...">
              {credentials.map(c => (
                <Option key={c.id} value={c.id}>{c.name} ({c.model_name})</Option>
              ))}
            </Select>
          </Form.Item>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="发散度 (Temp)" name="temperature">
                <InputNumber min={0} max={1.5} step={0.05} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="超时 (秒)" name="timeout_sec">
                <InputNumber min={5} max={120} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="最大 Tokens" name="max_tokens">
                <InputNumber min={512} max={8192} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </div>
  );
}

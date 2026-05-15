/**
 * NotificationSettings - 通知规则配置页面 (Phase 4)
 *
 * 功能：
 * - 通知规则 CRUD（创建/编辑/删除/启停）
 * - 静默窗口管理
 * - 通知日志查看
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Space, Modal, Form, Input, Select, Switch,
  InputNumber, Tag, message, Tabs, Popconfirm, Row, Col, TimePicker,
  Checkbox, Tooltip, Badge, Empty,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, BellOutlined,
  ClockCircleOutlined, HistoryOutlined, SettingOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  notificationRuleAPI, silenceWindowAPI, alertNotificationAPI, databaseAPI,
} from '../services/api';

const { TabPane } = Tabs;
const { Option } = Select;

// ─── 通知规则标签页 ─────────────────────────────────
function NotificationRulesTab() {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [databases, setDatabases] = useState([]);
  const [form] = Form.useForm();

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const res = await notificationRuleAPI.list();
      setRules(res?.data || res || []);
    } catch (e) {
      message.error('加载通知规则失败');
    }
    setLoading(false);
  }, []);

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.data || res || []);
    } catch (_) {}
  }, []);

  useEffect(() => { loadRules(); loadDatabases(); }, [loadRules, loadDatabases]);

  const handleCreate = () => {
    setEditingRule(null);
    form.resetFields();
    form.setFieldsValue({
      alert_types: [], severities: ['critical', 'error'],
      channels: ['email'], priority: 0, escalation_minutes: 0,
      is_enabled: true,
    });
    setModalVisible(true);
  };

  const handleEdit = (record) => {
    setEditingRule(record);
    form.setFieldsValue({
      name: record.name,
      alert_types: record.alert_types || [],
      severities: record.severities || [],
      channels: record.channels || [],
      db_config: record.db_config,
      schedule: record.schedule ? {
        work_hours: record.schedule.work_hours || false,
        start: record.schedule.start || '09:00',
        end: record.schedule.end || '18:00',
        weekdays: record.schedule.weekdays || '1,2,3,4,5',
      } : undefined,
      escalation_minutes: record.escalation_minutes || 0,
      priority: record.priority || 0,
      is_enabled: record.is_enabled,
    });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload = { ...values };
      if (!payload.db_config) delete payload.db_config;
      if (!payload.schedule?.work_hours) delete payload.schedule;

      if (editingRule) {
        await notificationRuleAPI.update(editingRule.id, payload);
        message.success('通知规则已更新');
      } else {
        await notificationRuleAPI.create(payload);
        message.success('通知规则已创建');
      }
      setModalVisible(false);
      loadRules();
    } catch (e) {
      if (e.errorFields) return;
      message.error('保存失败: ' + (e.message || '未知错误'));
    }
  };

  const handleDelete = async (id) => {
    try {
      await notificationRuleAPI.delete(id);
      message.success('已删除');
      loadRules();
    } catch (e) {
      message.error('删除失败');
    }
  };

  const handleToggle = async (record) => {
    try {
      await notificationRuleAPI.update(record.id, { is_enabled: !record.is_enabled });
      loadRules();
    } catch (e) {
      message.error('操作失败');
    }
  };

  const columns = [
    { title: '规则名称', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '范围', key: 'scope', width: 120,
      render: (_, r) => r.db_config_name
        ? <Tag color="blue">{r.db_config_name}</Tag>
        : <Tag color="green">全局</Tag>,
    },
    {
      title: '告警类型', dataIndex: 'alert_types', key: 'alert_types', width: 180,
      render: (v) => v?.length ? v.map(t => <Tag key={t} size="small">{t}</Tag>) : <Tag>全部</Tag>,
    },
    {
      title: '严重程度', dataIndex: 'severities', key: 'severities', width: 150,
      render: (v) => v?.length ? v.map(s => {
        const colorMap = { critical: 'red', error: 'orange', warning: 'gold' };
        return <Tag key={s} color={colorMap[s]} size="small">{s}</Tag>;
      }) : <Tag>全部</Tag>,
    },
    {
      title: '通知渠道', dataIndex: 'channels', key: 'channels', width: 150,
      render: (v) => v?.map(c => <Tag key={c}>{c}</Tag>),
    },
    {
      title: '升级(分)', dataIndex: 'escalation_minutes', key: 'escalation_minutes', width: 80,
      render: (v) => v > 0 ? <Tag color="orange">{v}分</Tag> : '-',
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 70,
      sorter: (a, b) => a.priority - b.priority,
    },
    {
      title: '状态', dataIndex: 'is_enabled', key: 'is_enabled', width: 80,
      render: (v, r) => <Switch size="small" checked={v} onChange={() => handleToggle(r)} />,
    },
    {
      title: '操作', key: 'actions', width: 100,
      render: (_, r) => (
        <Space>
          <Tooltip title="编辑"><Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} /></Tooltip>
          <Popconfirm title="确认删除此规则?" onConfirm={() => handleDelete(r.id)}>
            <Tooltip title="删除"><Button type="link" size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Table
        dataSource={rules} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: t => `共 ${t} 条` }}
        title={() => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><BellOutlined /> 通知规则列表</span>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>新建规则</Button>
          </div>
        )}
      />

      <Modal
        title={editingRule ? '编辑通知规则' : '新建通知规则'}
        open={modalVisible}
        onOk={handleSave}
        onCancel={() => setModalVisible(false)}
        width={680}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="规则名称" rules={[{ required: true, message: '请输入规则名称' }]}>
                <Input placeholder="如：核心库告警通知" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="db_config" label="关联数据库">
                <Select allowClear placeholder="全局规则（不选）">
                  {databases.map(db => (
                    <Option key={db.id} value={db.id}>{db.name}</Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="alert_types" label="告警类型">
                <Select mode="multiple" allowClear placeholder="全部类型">
                  {['down', 'tablespace', 'connection', 'lock', 'baseline', 'password_expiry'].map(t => (
                    <Option key={t} value={t}>{t}</Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="severities" label="严重程度">
                <Select mode="multiple" allowClear placeholder="全部程度">
                  {['critical', 'error', 'warning'].map(s => (
                    <Option key={s} value={s}>{s}</Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="channels" label="通知渠道" rules={[{ required: true, message: '请选择至少一个渠道' }]}>
            <Checkbox.Group>
              <Space>
                <Checkbox value="email">邮件</Checkbox>
                <Checkbox value="dingtalk">钉钉</Checkbox>
                <Checkbox value="wecom">企业微信</Checkbox>
                <Checkbox value="sms">短信</Checkbox>
              </Space>
            </Checkbox.Group>
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="priority" label="优先级">
                <InputNumber min={0} max={100} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="escalation_minutes" label="升级等待(分钟)">
                <InputNumber min={0} max={1440} style={{ width: '100%' }} placeholder="0=不升级" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="is_enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </>
  );
}

// ─── 静默窗口标签页 ─────────────────────────────────
function SilenceWindowsTab() {
  const [windows, setWindows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingWin, setEditingWin] = useState(null);
  const [databases, setDatabases] = useState([]);
  const [form] = Form.useForm();

  const loadWindows = useCallback(async () => {
    setLoading(true);
    try {
      const res = await silenceWindowAPI.list();
      setWindows(res?.data || res || []);
    } catch (e) {
      message.error('加载静默窗口失败');
    }
    setLoading(false);
  }, []);

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.data || res || []);
    } catch (_) {}
  }, []);

  useEffect(() => { loadWindows(); loadDatabases(); }, [loadWindows, loadDatabases]);

  const handleCreate = () => {
    setEditingWin(null);
    form.resetFields();
    form.setFieldsValue({
      is_active: true, weekdays: ['1','2','3','4','5'],
    });
    setModalVisible(true);
  };

  const handleEdit = (record) => {
    setEditingWin(record);
    form.setFieldsValue({
      ...record,
      start_time: record.start_time ? dayjs(record.start_time, 'HH:mm') : undefined,
      end_time: record.end_time ? dayjs(record.end_time, 'HH:mm') : undefined,
      weekdays: record.weekdays ? record.weekdays.split(',') : [],
    });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload = {
        ...values,
        start_time: values.start_time?.format('HH:mm'),
        end_time: values.end_time?.format('HH:mm'),
        weekdays: values.weekdays?.join(',') || '1,2,3,4,5',
      };
      if (!payload.config) delete payload.config;

      if (editingWin) {
        await silenceWindowAPI.update(editingWin.id, payload);
        message.success('静默窗口已更新');
      } else {
        await silenceWindowAPI.create(payload);
        message.success('静默窗口已创建');
      }
      setModalVisible(false);
      loadWindows();
    } catch (e) {
      if (e.errorFields) return;
      message.error('保存失败');
    }
  };

  const handleDelete = async (id) => {
    try {
      await silenceWindowAPI.delete(id);
      message.success('已删除');
      loadWindows();
    } catch (e) {
      message.error('删除失败');
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 150 },
    {
      title: '数据库', key: 'db', width: 120,
      render: (_, r) => r.config_name || <Tag>全局</Tag>,
    },
    { title: '告警类型', dataIndex: 'alert_type', key: 'alert_type', width: 100, render: v => v || '全部' },
    {
      title: '时间', key: 'time', width: 150,
      render: (_, r) => r.start_datetime
        ? `${dayjs(r.start_datetime).format('MM-DD HH:mm')} ~ ${dayjs(r.end_datetime).format('MM-DD HH:mm')}`
        : `${r.start_time} ~ ${r.end_time}`,
    },
    { title: '星期', dataIndex: 'weekdays', key: 'weekdays', width: 100,
      render: v => {
        const dayMap = { '1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'日' };
        return v?.split(',').map(d => dayMap[d] || d).join('、');
      },
    },
    { title: '启用', dataIndex: 'is_active', key: 'is_active', width: 70,
      render: v => v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
    },
    { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
    {
      title: '操作', key: 'actions', width: 100,
      render: (_, r) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
          <Popconfirm title="确认删除?" onConfirm={() => handleDelete(r.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Table
        dataSource={windows} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20 }}
        title={() => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><ClockCircleOutlined /> 静默窗口列表</span>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>新建窗口</Button>
          </div>
        )}
      />
      <Modal
        title={editingWin ? '编辑静默窗口' : '新建静默窗口'}
        open={modalVisible} onOk={handleSave}
        onCancel={() => setModalVisible(false)} width={600} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：月度维护窗口" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="config" label="关联数据库">
                <Select allowClear placeholder="全局（不选）">
                  {databases.map(db => <Option key={db.id} value={db.id}>{db.name}</Option>)}
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="alert_type" label="告警类型">
                <Select allowClear placeholder="全部类型">
                  {['down', 'tablespace', 'connection', 'lock', 'baseline'].map(t => (
                    <Option key={t} value={t}>{t}</Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="start_time" label="开始时间" rules={[{ required: true }]}>
                <TimePicker format="HH:mm" style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="end_time" label="结束时间" rules={[{ required: true }]}>
                <TimePicker format="HH:mm" style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="weekdays" label="生效星期">
            <Checkbox.Group>
              <Space>
                {[
                  { value: '1', label: '周一' }, { value: '2', label: '周二' },
                  { value: '3', label: '周三' }, { value: '4', label: '周四' },
                  { value: '5', label: '周五' }, { value: '6', label: '周六' },
                  { value: '7', label: '周日' },
                ].map(d => <Checkbox key={d.value} value={d.value}>{d.label}</Checkbox>)}
              </Space>
            </Checkbox.Group>
          </Form.Item>
          <Form.Item name="reason" label="静默原因">
            <Input.TextArea rows={2} placeholder="如：月度维护" />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ─── 通知日志标签页 ─────────────────────────────────
function NotificationLogTab() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });

  const loadLogs = useCallback(async (page = 1) => {
    setLoading(true);
    try {
      const res = await alertNotificationAPI.list({ page, page_size: pagination.pageSize });
      const data = res?.data || res || {};
      setLogs(data.results || data || []);
      setPagination(prev => ({ ...prev, current: page, total: data.count || 0 }));
    } catch (e) {
      message.error('加载通知日志失败');
    }
    setLoading(false);
  }, [pagination.pageSize]);

  useEffect(() => { loadLogs(); }, [loadLogs]);

  const columns = [
    { title: '时间', dataIndex: 'send_time', key: 'send_time', width: 160 },
    { title: '告警标题', dataIndex: 'alert_title', key: 'alert_title', ellipsis: true },
    { title: '渠道', dataIndex: 'channel', key: 'channel', width: 80,
      render: v => {
        const map = { email: '邮件', dingtalk: '钉钉', wecom: '企微', sms: '短信' };
        return map[v] || v;
      },
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: v => {
        const map = { success: 'green', failed: 'red', skipped: 'default' };
        return <Tag color={map[v]}>{v}</Tag>;
      },
    },
    { title: '错误信息', dataIndex: 'error_message', key: 'error_message', ellipsis: true },
  ];

  return (
    <Table
      dataSource={logs} columns={columns} rowKey="id"
      loading={loading} size="small"
      pagination={{ ...pagination, showTotal: t => `共 ${t} 条` }}
      onChange={(pag) => loadLogs(pag.current)}
      title={() => <span><HistoryOutlined /> 通知发送日志</span>}
    />
  );
}

// ─── 主页面 ─────────────────────────────────────────
export default function NotificationSettings() {
  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <SettingOutlined /> 通知设置管理 — 配置告警路由规则、维护静默窗口和查看通知日志
      </Card>
      <Tabs defaultActiveKey="rules">
        <TabPane tab={<span><BellOutlined /> 通知规则</span>} key="rules">
          <NotificationRulesTab />
        </TabPane>
        <TabPane tab={<span><ClockCircleOutlined /> 静默窗口</span>} key="silence">
          <SilenceWindowsTab />
        </TabPane>
        <TabPane tab={<span><HistoryOutlined /> 通知日志</span>} key="logs">
          <NotificationLogTab />
        </TabPane>
      </Tabs>
    </div>
  );
}

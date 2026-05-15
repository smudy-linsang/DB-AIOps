/**
 * BusinessSystems - 业务系统管理页面 (Phase 4)
 *
 * 功能：
 * - 业务系统 CRUD（名称/重要程度/负责人/关联数据库）
 * - 数据库拓扑关系管理
 * - 影响分析（当某库故障时影响哪些业务系统）
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Space, Modal, Form, Input, Select, Tag, message,
  Popconfirm, Row, Col, Descriptions, Tabs, Spin, Alert, Empty, Tooltip,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ApartmentOutlined,
  LinkOutlined, WarningOutlined, AppstoreOutlined,
} from '@ant-design/icons';
import {
  businessSystemAPI, topologyAPI, databaseAPI,
} from '../services/api';

const { TabPane } = Tabs;
const { Option } = Select;
const { TextArea } = Input;

// ─── 业务系统标签页 ─────────────────────────────────
function BusinessSystemsTab() {
  const [systems, setSystems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingSys, setEditingSys] = useState(null);
  const [databases, setDatabases] = useState([]);
  const [form] = Form.useForm();

  const loadSystems = useCallback(async () => {
    setLoading(true);
    try {
      const res = await businessSystemAPI.list();
      setSystems(res?.data || res || []);
    } catch (e) {
      message.error('加载业务系统失败');
    }
    setLoading(false);
  }, []);

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.data || res || []);
    } catch (_) {}
  }, []);

  useEffect(() => { loadSystems(); loadDatabases(); }, [loadSystems, loadDatabases]);

  const handleCreate = () => {
    setEditingSys(null);
    form.resetFields();
    form.setFieldsValue({ importance: 'normal' });
    setModalVisible(true);
  };

  const handleEdit = (record) => {
    setEditingSys(record);
    form.setFieldsValue({
      name: record.name,
      importance: record.importance,
      owner: record.owner,
      contact: record.contact,
      description: record.description,
      databases: record.databases?.map(d => typeof d === 'object' ? d.id : d) || [],
    });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      if (editingSys) {
        await businessSystemAPI.update(editingSys.id, values);
        message.success('业务系统已更新');
      } else {
        await businessSystemAPI.create(values);
        message.success('业务系统已创建');
      }
      setModalVisible(false);
      loadSystems();
    } catch (e) {
      if (e.errorFields) return;
      message.error('保存失败: ' + (e.message || ''));
    }
  };

  const handleDelete = async (id) => {
    try {
      await businessSystemAPI.delete(id);
      message.success('已删除');
      loadSystems();
    } catch (e) {
      message.error('删除失败');
    }
  };

  const importanceColors = { critical: 'red', important: 'orange', normal: 'blue' };
  const importanceLabels = { critical: '核心', important: '重要', normal: '一般' };

  const columns = [
    { title: '业务系统', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '重要程度', dataIndex: 'importance', key: 'importance', width: 90,
      render: v => <Tag color={importanceColors[v]}>{importanceLabels[v] || v}</Tag>,
    },
    { title: '负责人', dataIndex: 'owner', key: 'owner', width: 100 },
    { title: '联系方式', dataIndex: 'contact', key: 'contact', width: 140, ellipsis: true },
    {
      title: '关联数据库', dataIndex: 'databases', key: 'databases',
      render: (v) => v?.length
        ? <Space wrap size={2}>{v.map(d => <Tag key={typeof d === 'object' ? d.id : d} color="blue">{typeof d === 'object' ? d.name : d}</Tag>)}</Space>
        : <Tag>未关联</Tag>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '操作', key: 'actions', width: 100,
      render: (_, r) => (
        <Space>
          <Tooltip title="编辑"><Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} /></Tooltip>
          <Popconfirm title="确认删除此业务系统?" onConfirm={() => handleDelete(r.id)}>
            <Tooltip title="删除"><Button type="link" size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Table
        dataSource={systems} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }}
        title={() => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><AppstoreOutlined /> 业务系统列表</span>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>新建业务系统</Button>
          </div>
        )}
      />
      <Modal
        title={editingSys ? '编辑业务系统' : '新建业务系统'}
        open={modalVisible} onOk={handleSave}
        onCancel={() => setModalVisible(false)} width={600} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="业务系统名称" rules={[{ required: true, message: '请输入名称' }]}>
                <Input placeholder="如：核心交易系统" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="importance" label="重要程度" rules={[{ required: true }]}>
                <Select>
                  <Option value="critical">核心</Option>
                  <Option value="important">重要</Option>
                  <Option value="normal">一般</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="owner" label="负责人">
                <Input placeholder="如：张三" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="contact" label="联系方式">
                <Input placeholder="如：13800138000" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="databases" label="关联数据库">
            <Select mode="multiple" allowClear placeholder="选择关联的数据库">
              {databases.map(db => (
                <Option key={db.id} value={db.id}>{db.name} ({db.db_type})</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} placeholder="业务系统描述" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ─── 拓扑管理标签页 ─────────────────────────────────
function TopologyTab() {
  const [topologies, setTopologies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [databases, setDatabases] = useState([]);
  const [form] = Form.useForm();

  const loadTopologies = useCallback(async () => {
    setLoading(true);
    try {
      // 加载所有数据库的拓扑信息
      const results = [];
      for (const db of databases) {
        try {
          const res = await topologyAPI.getTopology(db.id);
          const data = res?.data || res || {};
          if (data.topologies) {
            results.push(...data.topologies);
          } else if (Array.isArray(data)) {
            results.push(...data);
          } else if (data.id) {
            results.push(data);
          }
        } catch (_) {}
      }
      setTopologies(results);
    } catch (e) {
      message.error('加载拓扑信息失败');
    }
    setLoading(false);
  }, [databases]);

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.data || res || []);
    } catch (_) {}
  }, []);

  useEffect(() => { loadTopologies(); loadDatabases(); }, [loadTopologies, loadDatabases]);

  const handleCreate = () => {
    form.resetFields();
    form.setFieldsValue({ role: 'single', topology_type: 'single', sync_mode: '' });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const dbId = values.db_config;
      if (!dbId) {
        message.error('请选择数据库');
        return;
      }
      await topologyAPI.saveTopology(dbId, values);
      message.success('拓扑关系已保存');
      setModalVisible(false);
      loadTopologies();
    } catch (e) {
      if (e.errorFields) return;
      message.error('保存失败');
    }
  };

  const roleColors = {
    primary: 'green', standby: 'blue', rac_node: 'purple',
    dsc_node: 'purple', single: 'default',
  };
  const roleLabels = {
    primary: '主库', standby: '备库', rac_node: 'RAC节点',
    dsc_node: 'DSC节点', single: '单机',
  };

  const columns = [
    {
      title: '数据库', key: 'db', width: 150,
      render: (_, r) => r.db_config_name || r.db_config || '-',
    },
    {
      title: '角色', dataIndex: 'role', key: 'role', width: 100,
      render: v => <Tag color={roleColors[v]}>{roleLabels[v] || v}</Tag>,
    },
    {
      title: '拓扑类型', dataIndex: 'topology_type', key: 'topology_type', width: 130,
      render: v => {
        const map = { primary_standby: '主从', rac: 'RAC', adg: 'ADG', mha: 'MHA', dsc: 'DSC集群', dts: 'DTS复制', single: '单机' };
        return map[v] || v;
      },
    },
    { title: '集群名称', dataIndex: 'cluster_name', key: 'cluster_name', width: 130 },
    {
      title: '关联节点', dataIndex: 'peer_databases', key: 'peer_databases',
      render: (v) => v?.length
        ? <Space wrap size={2}>{v.map(d => <Tag key={typeof d === 'object' ? d.id : d}>{typeof d === 'object' ? d.name : d}</Tag>)}</Space>
        : '-',
    },
    {
      title: '同步模式', dataIndex: 'sync_mode', key: 'sync_mode', width: 90,
      render: v => {
        const map = { sync: '同步', async: '异步', semi_sync: '半同步' };
        return v ? <Tag>{map[v] || v}</Tag> : '-';
      },
    },
    {
      title: '延迟(秒)', dataIndex: 'lag_seconds', key: 'lag_seconds', width: 90,
      render: v => v != null ? <Tag color={v > 30 ? 'red' : v > 5 ? 'orange' : 'green'}>{v.toFixed(1)}s</Tag> : '-',
    },
  ];

  return (
    <>
      <Table
        dataSource={topologies} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20 }}
        title={() => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><ApartmentOutlined /> 数据库拓扑关系</span>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>添加拓扑</Button>
          </div>
        )}
      />
      <Modal
        title="添加/编辑拓扑关系"
        open={modalVisible} onOk={handleSave}
        onCancel={() => setModalVisible(false)} width={600} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="db_config" label="数据库" rules={[{ required: true }]}>
                <Select placeholder="选择数据库">
                  {databases.map(db => <Option key={db.id} value={db.id}>{db.name}</Option>)}
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="role" label="角色" rules={[{ required: true }]}>
                <Select>
                  <Option value="primary">主库</Option>
                  <Option value="standby">备库</Option>
                  <Option value="rac_node">RAC节点</Option>
                  <Option value="dsc_node">DSC节点</Option>
                  <Option value="single">单机</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="topology_type" label="拓扑类型" rules={[{ required: true }]}>
                <Select>
                  <Option value="primary_standby">主从</Option>
                  <Option value="rac">RAC</Option>
                  <Option value="adg">Active Data Guard</Option>
                  <Option value="mha">MHA</Option>
                  <Option value="dsc">DSC集群</Option>
                  <Option value="dts">DTS复制</Option>
                  <Option value="single">单机</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="cluster_name" label="集群名称">
                <Input placeholder="如：order-cluster" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="sync_mode" label="同步模式">
                <Select allowClear placeholder="选择同步模式">
                  <Option value="sync">同步</Option>
                  <Option value="async">异步</Option>
                  <Option value="semi_sync">半同步</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="lag_seconds" label="延迟秒数">
                <Input type="number" placeholder="如：0.5" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="peer_databases" label="关联节点">
            <Select mode="multiple" allowClear placeholder="选择关联的其他数据库节点">
              {databases.map(db => <Option key={db.id} value={db.id}>{db.name}</Option>)}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ─── 影响分析标签页 ─────────────────────────────────
function ImpactAnalysisTab() {
  const [databases, setDatabases] = useState([]);
  const [selectedDb, setSelectedDb] = useState(null);
  const [impactData, setImpactData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    databaseAPI.list().then(res => setDatabases(res?.data || res || [])).catch(() => {});
  }, []);

  const handleAnalyze = async (dbId) => {
    setSelectedDb(dbId);
    setLoading(true);
    try {
      const res = await topologyAPI.getImpact(dbId);
      setImpactData(res?.data || res || {});
    } catch (e) {
      message.error('影响分析失败');
      setImpactData(null);
    }
    setLoading(false);
  };

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space>
          <WarningOutlined />
          <span>选择一个数据库，分析其故障时对业务系统的影响范围</span>
          <Select
            style={{ width: 240 }} placeholder="选择数据库"
            onChange={handleAnalyze}
          >
            {databases.map(db => <Option key={db.id} value={db.id}>{db.name}</Option>)}
          </Select>
        </Space>
      </Card>

      {loading && <Spin tip="分析中..." style={{ display: 'block', margin: '40px auto' }} />}

      {!loading && impactData && (
        <Card size="small" title={`影响分析结果 - ${impactData.database_name || selectedDb}`}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="数据库">{impactData.database_name}</Descriptions.Item>
            <Descriptions.Item label="拓扑角色">{impactData.topology_role || '未知'}</Descriptions.Item>
          </Descriptions>

          {impactData.affected_systems?.length > 0 ? (
            <Table
              style={{ marginTop: 12 }}
              dataSource={impactData.affected_systems}
              columns={[
                { title: '业务系统', dataIndex: 'name', key: 'name' },
                {
                  title: '重要程度', dataIndex: 'importance', key: 'importance',
                  render: v => {
                    const map = { critical: 'red', important: 'orange', normal: 'blue' };
                    const labels = { critical: '核心', important: '重要', normal: '一般' };
                    return <Tag color={map[v]}>{labels[v] || v}</Tag>;
                  },
                },
                { title: '负责人', dataIndex: 'owner', key: 'owner' },
                { title: '联系方式', dataIndex: 'contact', key: 'contact' },
              ]}
              rowKey="id" size="small" pagination={false}
            />
          ) : (
            <Empty style={{ marginTop: 20 }} description="无关联业务系统" />
          )}

          {impactData.peer_databases?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>关联数据库节点：</strong>
              <Space style={{ marginLeft: 8 }}>
                {impactData.peer_databases.map(d => <Tag key={typeof d === 'object' ? d.id : d} color="blue">{typeof d === 'object' ? d.name : d}</Tag>)}
              </Space>
            </div>
          )}
        </Card>
      )}

      {!loading && !impactData && selectedDb === null && (
        <Empty description="请选择一个数据库进行影响分析" />
      )}
    </div>
  );
}

// ─── 主页面 ─────────────────────────────────────────
export default function BusinessSystems() {
  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <ApartmentOutlined /> 业务拓扑管理 — 管理业务系统、数据库拓扑关系和影响分析
      </Card>
      <Tabs defaultActiveKey="systems">
        <TabPane tab={<span><AppstoreOutlined /> 业务系统</span>} key="systems">
          <BusinessSystemsTab />
        </TabPane>
        <TabPane tab={<span><ApartmentOutlined /> 拓扑关系</span>} key="topology">
          <TopologyTab />
        </TabPane>
        <TabPane tab={<span><WarningOutlined /> 影响分析</span>} key="impact">
          <ImpactAnalysisTab />
        </TabPane>
      </Tabs>
    </div>
  );
}

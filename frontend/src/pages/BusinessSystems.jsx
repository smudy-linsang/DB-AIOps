import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card, Table, Button, Space, Modal, Form, Input, Select, Tag, message,
  Popconfirm, Row, Col, Descriptions, Tabs, Spin, Alert, Empty, Tooltip,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ApartmentOutlined,
  LinkOutlined, WarningOutlined, AppstoreOutlined, ReloadOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
} from '@ant-design/icons';
import {
  businessSystemAPI, topologyAPI, databaseAPI,
} from '../services/api';
import { PermissionGuard } from '../components/AuthGuard';
import { Perm } from '../utils/permission';

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
      setSystems(res?.business_systems || res?.data || []);
    } catch (e) {
      message.error('加载业务系统失败');
    }
    setLoading(false);
  }, []);

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.databases || res?.data || []);
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
      name: record.name, importance: record.importance,
      owner: record.owner, contact: record.contact,
      description: record.description,
      databases: record.databases?.map(d => typeof d === 'object' ? d.id : d) || [],
    });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload = { ...values, database_ids: values.databases };
      delete payload.databases;
      if (editingSys) {
        await businessSystemAPI.update(editingSys.id, payload);
        message.success('业务系统已更新');
      } else {
        await businessSystemAPI.create(payload);
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
    { title: '重要程度', dataIndex: 'importance', key: 'importance', width: 90,
      render: v => <Tag color={importanceColors[v]}>{importanceLabels[v] || v}</Tag>,
    },
    { title: '负责人', dataIndex: 'owner', key: 'owner', width: 100 },
    { title: '联系方式', dataIndex: 'contact', key: 'contact', width: 140, ellipsis: true },
    { title: '关联数据库', dataIndex: 'databases', key: 'databases',
      render: (v) => v?.length
        ? <Space wrap size={2}>{v.map(d => <Tag key={typeof d === 'object' ? d.id : d} color="blue">{typeof d === 'object' ? d.name : d}</Tag>)}</Space>
        : <Tag>未关联</Tag>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '操作', key: 'actions', width: 100,
      render: (_, r) => (
        <Space>
          <PermissionGuard code={Perm.BUSINESS_TOPOLOGY_MANAGE}>
            <Tooltip title="编辑"><Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} /></Tooltip>
          </PermissionGuard>
          <PermissionGuard code={Perm.BUSINESS_TOPOLOGY_MANAGE}>
            <Popconfirm title="确认删除此业务系统?" onConfirm={() => handleDelete(r.id)}>
              <Tooltip title="删除"><Button type="link" size="small" danger icon={<DeleteOutlined />} /></Tooltip>
            </Popconfirm>
          </PermissionGuard>
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
            <PermissionGuard code={Perm.BUSINESS_TOPOLOGY_MANAGE}><Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>新建业务系统</Button></PermissionGuard>
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
            <Col span={12}><Form.Item name="owner" label="负责人"><Input placeholder="如：张三" /></Form.Item></Col>
            <Col span={12}><Form.Item name="contact" label="联系方式"><Input placeholder="如：13800138000" /></Form.Item></Col>
          </Row>
          <Form.Item name="databases" label="关联数据库">
            <Select mode="multiple" allowClear placeholder="选择关联的数据库">
              {databases.map(db => <Option key={db.id} value={db.id}>{db.name} ({db.db_type})</Option>)}
            </Select>
          </Form.Item>
          <Form.Item name="description" label="描述"><TextArea rows={2} placeholder="业务系统描述" /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ─── 拓扑可视化标签页 ─────────────────────────────────
function TopologyVisualTab() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState(null);
  const [impactData, setImpactData] = useState(null);
  const [impactLoading, setImpactLoading] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await topologyAPI.getOverview();
      setNodes(res?.nodes || []);
      setEdges(res?.edges || []);
    } catch (e) {
      message.error('加载拓扑数据失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleNodeClick = async (node) => {
    setSelectedNode(node);
    setImpactLoading(true);
    try {
      const res = await topologyAPI.getImpact(node.id);
      setImpactData(res || {});
    } catch (e) {
      setImpactData(null);
    }
    setImpactLoading(false);
  };

  // 自动布局算法（简单的力导向模拟）
  const layoutNodes = () => {
    if (nodes.length === 0) return [];
    const width = 800;
    const height = 500;
    const centerX = width / 2;
    const centerY = height / 2;

    // 按集群分组
    const clusters = {};
    const standalone = [];
    nodes.forEach(n => {
      if (n.topology_type === 'single' || !n.cluster_name) {
        standalone.push(n);
      } else {
        const key = n.cluster_name || n.topology_type;
        if (!clusters[key]) clusters[key] = [];
        clusters[key].push(n);
      }
    });

    const positioned = [];
    const clusterKeys = Object.keys(clusters);

    // 集群节点环形布局
    clusterKeys.forEach((key, ci) => {
      const cluster = clusters[key];
      const angle = (2 * Math.PI * ci) / Math.max(clusterKeys.length, 1) - Math.PI / 2;
      const radius = Math.min(centerX, centerY) * 0.55;
      const cx = centerX + radius * Math.cos(angle);
      const cy = centerY + radius * Math.sin(angle);

      cluster.forEach((n, ni) => {
        const subAngle = (2 * Math.PI * ni) / cluster.length - Math.PI / 2;
        const subRadius = Math.min(80, 40 * cluster.length);
        positioned.push({
          ...n,
          x: cx + subRadius * Math.cos(subAngle),
          y: cy + subRadius * Math.sin(subAngle),
        });
      });
    });

    // 独立节点线性排列
    standalone.forEach((n, i) => {
      positioned.push({
        ...n,
        x: 60 + (i % 8) * 100,
        y: height - 60 - Math.floor(i / 8) * 70,
      });
    });

    return positioned;
  };

  const positionedNodes = layoutNodes();

  const getStatusColor = (status) => {
    switch (status) {
      case 'UP': return '#52c41a';
      case 'DOWN': return '#ff4d4f';
      default: return '#faad14';
    }
  };

  const getRoleColor = (role) => {
    switch (role) {
      case 'primary': return '#1890ff';
      case 'standby': return '#52c41a';
      case 'rac_node': return '#722ed1';
      case 'dsc_node': return '#722ed1';
      default: return '#8c8c8c';
    }
  };

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space>
          <ApartmentOutlined />
          <span>数据库拓扑总览 - 点击节点查看影响分析</span>
          <Button size="small" icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
        </Space>
      </Card>

      {loading ? <Spin style={{ display: 'block', margin: '40px auto' }} /> : (
        <Row gutter={16}>
          <Col span={16}>
            <Card bodyStyle={{ padding: 0 }}>
              {positionedNodes.length > 0 ? (
                <svg width="100%" viewBox="0 0 800 500" style={{ background: '#fafafa', minHeight: 400 }}>
                  {/* 连线 */}
                  {edges.map((edge, i) => {
                    const source = positionedNodes.find(n => n.id === edge.source);
                    const target = positionedNodes.find(n => n.id === edge.target);
                    if (!source || !target) return null;
                    return (
                      <g key={`edge-${i}`}>
                        <line
                          x1={source.x} y1={source.y}
                          x2={target.x} y2={target.y}
                          stroke={edge.sync_mode === 'sync' ? '#1890ff' : '#faad14'}
                          strokeWidth={2}
                          strokeDasharray={edge.sync_mode === 'async' ? '5,5' : 'none'}
                        />
                        {edge.lag_seconds != null && (
                          <text
                            x={(source.x + target.x) / 2}
                            y={(source.y + target.y) / 2 - 8}
                            textAnchor="middle"
                            fill="#666"
                            fontSize={10}
                          >
                            {edge.lag_seconds.toFixed(1)}s
                          </text>
                        )}
                        <text
                          x={(source.x + target.x) / 2}
                          y={(source.y + target.y) / 2 + 10}
                          textAnchor="middle"
                          fill="#999"
                          fontSize={9}
                        >
                          {edge.topology_type}
                        </text>
                      </g>
                    );
                  })}
                  {/* 节点 */}
                  {positionedNodes.map(node => (
                    <g
                      key={`node-${node.id}`}
                      onClick={() => handleNodeClick(node)}
                      style={{ cursor: 'pointer' }}
                    >
                      <circle
                        cx={node.x} cy={node.y}
                        r={24}
                        fill={selectedNode?.id === node.id ? '#e6f7ff' : '#fff'}
                        stroke={getStatusColor(node.status)}
                        strokeWidth={3}
                      />
                      <circle
                        cx={node.x} cy={node.y}
                        r={6}
                        fill={getStatusColor(node.status)}
                      />
                      <text
                        x={node.x} y={node.y + 38}
                        textAnchor="middle"
                        fill="#333"
                        fontSize={11}
                        fontWeight="bold"
                      >
                        {node.name}
                      </text>
                      <text
                        x={node.x} y={node.y + 52}
                        textAnchor="middle"
                        fill="#999"
                        fontSize={9}
                      >
                        {node.role} | {node.db_type}
                      </text>
                    </g>
                  ))}
                </svg>
              ) : (
                <Empty description="暂无拓扑数据，请先在「拓扑关系」中配置" style={{ padding: 60 }} />
              )}
            </Card>
          </Col>
          <Col span={8}>
            <Card title={selectedNode ? `节点详情: ${selectedNode.name}` : '节点详情'} size="small">
              {selectedNode ? (
                <div>
                  <Descriptions bordered column={1} size="small">
                    <Descriptions.Item label="名称">{selectedNode.name}</Descriptions.Item>
                    <Descriptions.Item label="类型"><Tag color="blue">{selectedNode.db_type?.toUpperCase()}</Tag></Descriptions.Item>
                    <Descriptions.Item label="地址">{selectedNode.host}:{selectedNode.port}</Descriptions.Item>
                    <Descriptions.Item label="状态">
                      <Tag color={selectedNode.status === 'UP' ? 'green' : selectedNode.status === 'DOWN' ? 'red' : 'orange'}>
                        {selectedNode.status}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="角色"><Tag color={getRoleColor(selectedNode.role)}>{selectedNode.role}</Tag></Descriptions.Item>
                    <Descriptions.Item label="拓扑类型">{selectedNode.topology_type}</Descriptions.Item>
                  </Descriptions>

                  {impactLoading ? <Spin style={{ display: 'block', margin: '12px auto' }} /> : impactData && (
                    <div style={{ marginTop: 12 }}>
                      <h4>影响分析</h4>
                      {impactData.failover_possible && (
                        <Tag color="green" style={{ marginBottom: 8 }}><CheckCircleOutlined /> 可故障切换</Tag>
                      )}
                      {impactData.affected_business_systems?.length > 0 ? (
                        <div>
                          <Text type="secondary">受影响业务系统：</Text>
                          <div style={{ marginTop: 4 }}>
                            {impactData.affected_business_systems.map((s, i) => (
                              <Tag key={i} color={s.importance === 'critical' ? 'red' : s.importance === 'important' ? 'orange' : 'blue'}>
                                {s.name} ({s.owner || '-'})
                              </Tag>
                            ))}
                          </div>
                        </div>
                      ) : <Text type="secondary">无关联业务系统</Text>}

                      {impactData.active_alerts?.length > 0 && (
                        <div style={{ marginTop: 8 }}>
                          <Text type="secondary">活跃告警：</Text>
                          <div style={{ marginTop: 4 }}>
                            {impactData.active_alerts.map(a => (
                              <Tag key={a.id} color={a.severity === 'critical' ? 'red' : 'orange'}>{a.title}</Tag>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <Empty description="点击左侧节点查看详情" />
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
}

// ─── 拓扑管理标签页 ─────────────────────────────────
function TopologyTab() {
  const [databases, setDatabases] = useState([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [form] = Form.useForm();

  const loadDatabases = useCallback(async () => {
    try {
      const res = await databaseAPI.list();
      setDatabases(res?.databases || res?.data || []);
    } catch (_) {}
  }, []);

  useEffect(() => { loadDatabases(); }, [loadDatabases]);

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
      const payload = { ...values, peer_database_ids: values.peer_databases };
      delete payload.peer_databases;
      await topologyAPI.saveTopology(dbId, payload);
      message.success('拓扑关系已保存');
      setModalVisible(false);
    } catch (e) {
      if (e.errorFields) return;
      message.error('保存失败');
    }
  };

  return (
    <>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space>
          <ApartmentOutlined />
          <span>管理数据库拓扑关系（主从/RAC/ADG等）</span>
          <PermissionGuard code={Perm.BUSINESS_TOPOLOGY_MANAGE}><Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleCreate}>添加拓扑</Button></PermissionGuard>
        </Space>
      </Card>
      <Modal
        title="添加/编辑拓扑关系"
        open={modalVisible} onOk={handleSave}
        onCancel={() => setModalVisible(false)} width={600} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="db_config" label="数据库" rules={[{ required: true }]}>
                <Select placeholder="选择数据库" showSearch optionFilterProp="children">
                  {databases.map(db => <Option key={db.id} value={db.id}>{db.name} ({db.db_type})</Option>)}
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
              <Form.Item name="cluster_name" label="集群名称"><Input placeholder="如：order-cluster" /></Form.Item>
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
              <Form.Item name="lag_seconds" label="延迟秒数"><Input type="number" placeholder="如：0.5" /></Form.Item>
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
    databaseAPI.list().then(res => setDatabases(res?.databases || res?.data || [])).catch(() => {});
  }, []);

  const handleAnalyze = async (dbId) => {
    setSelectedDb(dbId);
    setLoading(true);
    try {
      const res = await topologyAPI.getImpact(dbId);
      setImpactData(res || {});
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
          <Select style={{ width: 240 }} placeholder="选择数据库" onChange={handleAnalyze}>
            {databases.map(db => <Option key={db.id} value={db.id}>{db.name}</Option>)}
          </Select>
        </Space>
      </Card>

      {loading && <Spin tip="分析中..." style={{ display: 'block', margin: '40px auto' }} />}

      {!loading && impactData && (
        <Card size="small" title={`影响分析结果 - ${impactData.database?.name || selectedDb}`}>
          <Row gutter={16}>
            <Col span={8}>
              <Card size="small">
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="数据库">{impactData.database?.name}</Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <Tag color={impactData.database?.status === 'UP' ? 'green' : 'red'}>{impactData.database?.status}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="拓扑角色">{impactData.topology?.role || '未知'}</Descriptions.Item>
                  <Descriptions.Item label="故障切换">
                    {impactData.failover_possible
                      ? <Tag color="green"><CheckCircleOutlined /> 可切换</Tag>
                      : <Tag color="default"><CloseCircleOutlined /> 不可切换</Tag>
                    }
                  </Descriptions.Item>
                  {impactData.topology?.lag_seconds != null && (
                    <Descriptions.Item label="同步延迟">
                      <Tag color={impactData.topology.lag_seconds > 30 ? 'red' : 'green'}>
                        {impactData.topology.lag_seconds.toFixed(1)}s
                      </Tag>
                    </Descriptions.Item>
                  )}
                </Descriptions>
              </Card>
            </Col>
            <Col span={16}>
              {impactData.affected_business_systems?.length > 0 ? (
                <Table
                  dataSource={impactData.affected_business_systems}
                  columns={[
                    { title: '业务系统', dataIndex: 'name', key: 'name' },
                    { title: '重要程度', dataIndex: 'importance', key: 'importance',
                      render: v => {
                        const map = { critical: 'red', important: 'orange', normal: 'blue' };
                        const labels = { critical: '核心', important: '重要', normal: '一般' };
                        return <Tag color={map[v]}>{labels[v] || v}</Tag>;
                      },
                    },
                    { title: '负责人', dataIndex: 'owner', key: 'owner' },
                    { title: '联系方式', dataIndex: 'contact', key: 'contact' },
                  ]}
                  rowKey="name" size="small" pagination={false}
                  title={() => '受影响业务系统'}
                />
              ) : <Empty description="无关联业务系统" />}
            </Col>
          </Row>

          {impactData.active_alerts?.length > 0 && (
            <Card size="small" title="活跃告警" style={{ marginTop: 12 }}>
              <Space wrap>
                {impactData.active_alerts.map(a => (
                  <Tag key={a.id} color={a.severity === 'critical' ? 'red' : 'orange'}>
                    {a.title} ({a.alert_type})
                  </Tag>
                ))}
              </Space>
            </Card>
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
      <Tabs defaultActiveKey="visual">
        <TabPane tab={<span><ApartmentOutlined /> 拓扑视图</span>} key="visual">
          <TopologyVisualTab />
        </TabPane>
        <TabPane tab={<span><AppstoreOutlined /> 业务系统</span>} key="systems">
          <BusinessSystemsTab />
        </TabPane>
        <TabPane tab={<span><LinkOutlined /> 拓扑配置</span>} key="topology">
          <TopologyTab />
        </TabPane>
        <TabPane tab={<span><WarningOutlined /> 影响分析</span>} key="impact">
          <ImpactAnalysisTab />
        </TabPane>
      </Tabs>
    </div>
  );
}

/**
 * UserManagement - 用户与角色管理页面
 *
 * 功能：
 * - 用户管理 Tab：创建/编辑/禁用用户，分配角色
 * - 角色管理 Tab：查看/创建/编辑角色，权限矩阵配置
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, Modal, Form, Input, Select,
  message, Tabs, Descriptions, Checkbox, Row, Col, Popconfirm,
  Typography, Switch, Tooltip, Statistic, Badge,
} from 'antd';
import {
  UserOutlined, TeamOutlined, PlusOutlined, ReloadOutlined,
  EditOutlined, DeleteOutlined, LockOutlined, SafetyOutlined,
  CheckCircleOutlined, StopOutlined,
} from '@ant-design/icons';
import { userAPI, roleAPI } from '../services/api';
import { PermissionGuard } from '../components/AuthGuard';
import { Perm, MENU_PERMISSION_MAP } from '../utils/permission';

const { Title, Text } = Typography;
const { Option } = Select;

// ==========================================
// 用户管理 Tab
// ==========================================
function UsersTab() {
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [createModal, setCreateModal] = useState(false);
  const [editModal, setEditModal] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [createLoading, setCreateLoading] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [usersRes, rolesRes] = await Promise.all([
        userAPI.list(),
        roleAPI.list(),
      ]);
      setUsers(usersRes.users || []);
      setRoles(rolesRes.roles || []);
    } catch (e) {
      message.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setCreateLoading(true);
      await userAPI.create(values);
      message.success('用户创建成功');
      setCreateModal(false);
      form.resetFields();
      loadData();
    } catch (e) {
      if (e.errorFields) return;
      message.error(e.message || '创建失败');
    } finally {
      setCreateLoading(false);
    }
  };

  const handleEdit = async () => {
    try {
      const values = await editForm.validateFields();
      setEditLoading(true);
      await userAPI.update(currentUser.id, values);
      message.success('用户更新成功');
      setEditModal(false);
      editForm.resetFields();
      setCurrentUser(null);
      loadData();
    } catch (e) {
      if (e.errorFields) return;
      message.error(e.message || '更新失败');
    } finally {
      setEditLoading(false);
    }
  };

  const handleToggleActive = async (userId, isActive) => {
    try {
      await userAPI.update(userId, { is_active: !isActive });
      message.success(isActive ? '已禁用用户' : '已启用用户');
      loadData();
    } catch (e) {
      message.error('操作失败');
    }
  };

  const roleMap = {};
  roles.forEach(r => { roleMap[r.code] = r.name; });

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username', width: 120 },
    { title: '邮箱', dataIndex: 'email', width: 200 },
    {
      title: '角色', dataIndex: 'role', width: 140,
      render: (role) => {
        const colorMap = { super_admin: 'red', dba: 'blue', auditor: 'green', config_operator: 'orange', readonly: 'default' };
        return <Tag color={colorMap[role] || 'default'}>{roleMap[role] || role || '无角色'}</Tag>;
      },
    },
    {
      title: '状态', dataIndex: 'is_active', width: 80,
      render: (v) => v ? <Badge status="success" text="启用" /> : <Badge status="default" text="禁用" />,
    },
    {
      title: '最后登录', dataIndex: 'last_login', width: 170,
      render: (v) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_, record) => (
        <Space size="small">
          <PermissionGuard code={Perm.USERS_MANAGE}>
            <Button size="small" icon={<EditOutlined />}
              onClick={() => { setCurrentUser(record); editForm.setFieldsValue({ role: record.role, email: record.email }); setEditModal(true); }}>
              编辑
            </Button>
          </PermissionGuard>
          <PermissionGuard code={Perm.USERS_MANAGE}>
            <Popconfirm title={record.is_active ? '确认禁用此用户？' : '确认启用此用户？'} onConfirm={() => handleToggleActive(record.id, record.is_active)}>
              <Button size="small" danger={record.is_active} icon={record.is_active ? <StopOutlined /> : <CheckCircleOutlined />}>
                {record.is_active ? '禁用' : '启用'}
              </Button>
            </Popconfirm>
          </PermissionGuard>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Statistic title="总用户数" value={users.length} />
          <Statistic title="活跃用户" value={users.filter(u => u.is_active).length} valueStyle={{ color: '#3f8600' }} />
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
          <PermissionGuard code={Perm.USERS_MANAGE}>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModal(true)}>创建用户</Button>
          </PermissionGuard>
        </Space>
      </div>

      <Table rowKey="id" dataSource={users} columns={columns} loading={loading}
        pagination={{ pageSize: 15, showSizeChanger: false }} size="small" />

      {/* 创建用户 Modal */}
      <Modal title="创建用户" open={createModal} onCancel={() => setCreateModal(false)}
        onOk={handleCreate} confirmLoading={createLoading} width={480}>
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 8, message: '密码至少8位' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码（至少8位）" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="请输入邮箱" />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="readonly" rules={[{ required: true }]}>
            <Select>
              {roles.map(r => (
                <Option key={r.code} value={r.code}>
                  {r.is_builtin ? '[内置] ' : ''}{r.name}
                  <Text type="secondary" style={{ fontSize: 11 }}> - {r.description}</Text>
                </Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑用户 Modal */}
      <Modal title="编辑用户" open={editModal} onCancel={() => { setEditModal(false); setCurrentUser(null); }}
        onOk={handleEdit} confirmLoading={editLoading} width={480}>
        <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="用户名">{currentUser?.username}</Descriptions.Item>
        </Descriptions>
        <Form form={editForm} layout="vertical">
          <Form.Item name="email" label="邮箱">
            <Input placeholder="请输入邮箱" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select>
              {roles.map(r => (
                <Option key={r.code} value={r.code}>
                  {r.is_builtin ? '[内置] ' : ''}{r.name}
                </Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ==========================================
// 角色管理 Tab
// ==========================================
function RolesTab() {
  const [roles, setRoles] = useState([]);
  const [permMeta, setPermMeta] = useState({});
  const [permGroups, setPermGroups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editModal, setEditModal] = useState(false);
  const [createModal, setCreateModal] = useState(false);
  const [currentRole, setCurrentRole] = useState(null);
  const [selectedPerms, setSelectedPerms] = useState([]);
  const [form] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await roleAPI.list();
      setRoles(res.roles || []);
      setPermMeta(res.permission_meta || {});
      setPermGroups(res.permission_groups || []);
    } catch (e) {
      message.error('加载角色数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleEditPerms = (role) => {
    setCurrentRole(role);
    setSelectedPerms(role.permissions || []);
    setEditModal(true);
  };

  const handleSavePerms = async () => {
    try {
      await roleAPI.update(currentRole.id, {
        name: currentRole.name,
        description: currentRole.description,
        permissions: selectedPerms,
      });
      message.success('权限更新成功');
      setEditModal(false);
      setCurrentRole(null);
      loadData();
    } catch (e) {
      message.error(e.message || '更新失败');
    }
  };

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      await roleAPI.create(values);
      message.success('角色创建成功');
      setCreateModal(false);
      form.resetFields();
      loadData();
    } catch (e) {
      if (e.errorFields) return;
      message.error(e.message || '创建失败');
    }
  };

  const handleDelete = async (roleId) => {
    try {
      await roleAPI.delete(roleId);
      message.success('角色已删除');
      loadData();
    } catch (e) {
      message.error(e.message || '删除失败');
    }
  };

  const togglePerm = (code) => {
    setSelectedPerms(prev =>
      prev.includes(code) ? prev.filter(p => p !== code) : [...prev, code]
    );
  };

  const toggleGroup = (groupPerms) => {
    const allSelected = groupPerms.every(p => selectedPerms.includes(p));
    if (allSelected) {
      setSelectedPerms(prev => prev.filter(p => !groupPerms.includes(p)));
    } else {
      setSelectedPerms(prev => [...new Set([...prev, ...groupPerms])]);
    }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '角色', dataIndex: 'name', width: 150,
      render: (name, record) => (
        <Space>
          {name}
          {record.is_builtin && <Tag color="blue">内置</Tag>}
        </Space>
      ),
    },
    { title: '编码', dataIndex: 'code', width: 140 },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    {
      title: '权限数', dataIndex: 'permissions', width: 80,
      render: (perms) => <Tag color="blue">{perms?.length || 0}</Tag>,
    },
    {
      title: '用户数', dataIndex: 'user_count', width: 80,
      render: (v) => v || 0,
    },
    {
      title: '操作', key: 'actions', width: 180,
      render: (_, record) => (
        <Space size="small">
          <Button size="small" icon={<SafetyOutlined />} onClick={() => handleEditPerms(record)}>
            权限配置
          </Button>
          <PermissionGuard code={Perm.ROLES_MANAGE}>
            {!record.is_builtin && (
              <Popconfirm title="确认删除此角色？" onConfirm={() => handleDelete(record.id)}>
                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
            )}
          </PermissionGuard>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Statistic title="总角色数" value={roles.length} />
          <Statistic title="内置角色" value={roles.filter(r => r.is_builtin).length} />
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
          <PermissionGuard code={Perm.ROLES_MANAGE}>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModal(true)}>创建角色</Button>
          </PermissionGuard>
        </Space>
      </div>

      <Table rowKey="id" dataSource={roles} columns={columns} loading={loading}
        pagination={false} size="small" />

      {/* 权限配置 Modal */}
      <Modal
        title={`权限配置 - ${currentRole?.name || ''}`}
        open={editModal}
        onCancel={() => { setEditModal(false); setCurrentRole(null); }}
        onOk={handleSavePerms}
        width={700}
        okText="保存"
      >
        <div style={{ marginBottom: 12 }}>
          <Text type="secondary">
            已选择 <Text strong>{selectedPerms.length}</Text> 个权限
            {currentRole?.is_builtin && <Tag color="orange" style={{ marginLeft: 8 }}>内置角色</Tag>}
          </Text>
        </div>
        {permGroups.map(group => {
          const allSelected = group.permissions.every(p => selectedPerms.includes(p));
          const someSelected = group.permissions.some(p => selectedPerms.includes(p));
          return (
            <Card key={group.group} size="small" title={
              <Checkbox
                indeterminate={someSelected && !allSelected}
                checked={allSelected}
                onChange={() => toggleGroup(group.permissions)}
              >
                {group.group}
              </Checkbox>
            } style={{ marginBottom: 8 }}>
              <Row gutter={[8, 8]}>
                {group.permissions.map(perm => (
                  <Col span={8} key={perm}>
                    <Checkbox checked={selectedPerms.includes(perm)} onChange={() => togglePerm(perm)}>
                      {permMeta[perm] || perm}
                    </Checkbox>
                  </Col>
                ))}
              </Row>
            </Card>
          );
        })}
      </Modal>

      {/* 创建角色 Modal */}
      <Modal title="创建自定义角色" open={createModal} onCancel={() => setCreateModal(false)}
        onOk={handleCreate} width={480}>
        <Form form={form} layout="vertical">
          <Form.Item name="code" label="角色编码" rules={[{ required: true, message: '请输入角色编码' }]}
            extra="英文小写+下划线，如 ops_viewer">
            <Input placeholder="如 ops_viewer" />
          </Form.Item>
          <Form.Item name="name" label="角色名称" rules={[{ required: true, message: '请输入角色名称' }]}>
            <Input placeholder="如 运维观察员" />
          </Form.Item>
          <Form.Item name="description" label="角色描述">
            <Input.TextArea rows={2} placeholder="请输入角色描述" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ==========================================
// 主页面
// ==========================================
export default function UserManagement() {
  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>
        <TeamOutlined style={{ marginRight: 8 }} />
        用户与角色管理
      </Title>
      <Tabs defaultActiveKey="users" items={[
        {
          key: 'users',
          label: <span><UserOutlined /> 用户管理</span>,
          children: <UsersTab />,
        },
        {
          key: 'roles',
          label: <span><SafetyOutlined /> 角色管理</span>,
          children: <RolesTab />,
        },
      ]} />
    </div>
  );
}
